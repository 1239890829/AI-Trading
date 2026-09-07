"""东财 7x24 快讯流（hotspot-pipeline-design G1/P0①，2026-09-07）。

系统此前只有「按标的拉取」的个股资讯，没有全市场快讯源——热点消息无人
手工录入就永远进不了系统（设计文档 G1 缺口）。本模块补齐采集层：

- 接口：np-weblist ``comm/web/getFastNewsList``（fastColumn=101 宏观频道），
  盘中实测单次 50 条覆盖全天（2026-09-07 11:11 实测通过）；
- 必带 UA + Referer + req_trace（缺任一被东财拦截）；主域间歇性拦截 →
  双域 failover（np-weblist → np-listapi，两者盘中实测均通）；
- 抽取复用 events.extract.build_event（指纹去重）→ EventStore；
  快讯自身不落盘、不开新存储（设计 §4.3 纪律：不为热点消息开新存储）；
- 轮询：连续竞价 15s / 盘外 60s（性能第一，盘外降频）；失败不缓存——
  游标不推进，下一轮全量重拉，靠 EventStore 指纹天然去重。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from app.market.trade_calendar import in_trading_window
from app.news.flash_state import FlashCursor

log = logging.getLogger(__name__)

_TZ_BJ = timezone(timedelta(hours=8))

_HOSTS = (
    "https://np-weblist.eastmoney.com",
    "https://np-listapi.eastmoney.com",
)
_PATH = "/comm/web/getFastNewsList"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://kuaixun.eastmoney.com/",
}

_PAGE_SIZE = 50

_HTTP = None
_FLASH_CURSOR = FlashCursor()


def _http():
    global _HTTP
    if _HTTP is None:
        import httpx

        # trust_env=False：禁系统代理（MEMORY：本会话代理会吃本机/外呼请求）
        _HTTP = httpx.AsyncClient(trust_env=False, timeout=8.0, headers=_HEADERS)
    return _HTTP


async def aclose() -> None:
    global _HTTP
    if _HTTP is not None:
        await _HTTP.aclose()
        _HTTP = None


def _req_trace() -> str:
    return f"{int(time.time() * 1000)}"


def _parse_item(item: dict) -> dict | None:
    """fastNewsList 行 → 最小新闻 dict；缺标题的行丢弃（结构异常显式跳过，不臆造）。"""
    title = (item.get("title") or "").strip()
    if not title:
        return None
    show_time = None
    raw = (item.get("showTime") or "").strip()
    if raw:
        try:
            show_time = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_TZ_BJ)
        except ValueError:
            show_time = None  # 解析不出 → 交由 build_event 用当前时刻（指纹仍可去重）
    return {
        "title": title,
        "summary": (item.get("summary") or "").strip() or None,
        "code": (item.get("code") or "").strip() or None,
        "show_time": show_time,
    }


async def fetch_fast_news(*, limit: int = _PAGE_SIZE, column: int = 101) -> list[dict] | None:
    """拉取快讯列表（双域 failover）。全败返回 None（显式失败，绝不静默当成空列表）。"""
    query = {
        "client": "web",
        "biz": "web_724",
        "fastColumn": str(column),
        "sortEnd": "",
        "pageSize": str(limit),
        "req_trace": _req_trace(),
    }
    last_err: Exception | None = None
    for host in _HOSTS:
        try:
            resp = await _http().get(host + _PATH, params=query)
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            items = data.get("fastNewsList") or []
            out = [p for p in (_parse_item(i) for i in items) if p is not None]
            if out:
                return out
            last_err = ValueError(f"{host} 返回 200 但 fastNewsList 为空")
        except Exception as exc:  # noqa: BLE001 —— 逐域尝试，最后一个错误显式带出
            last_err = exc
    log.warning("flash news: all hosts failed: %s", last_err)
    return None


def _to_event(p: dict) -> dict:
    from app.events.extract import build_event

    published = p.get("show_time")
    if published is not None:
        published = published.astimezone(timezone.utc)
    return build_event(p["title"], source="东财快讯", published_at=published)


def poll_stats() -> dict:
    """轮询观测（心跳/验收用）：最近一轮结果。"""
    return _FLASH_CURSOR.snapshot()


async def poll_once(app) -> int:
    """拉一轮 → 逐条 build_event → EventStore 指纹去重入库。返回新增条数。

    app 可以是 FastAPI 实例（走 app.state）或任何带 event_store 属性的对象
    （测试直接传 SimpleNamespace）。
    """
    items = await fetch_fast_news()
    _FLASH_CURSOR.record_fetch(items is not None, len(items or []))
    if not items:
        return 0
    state = getattr(app, "state", app)
    store = getattr(state, "event_store", None)
    if store is None:
        from app.events.store import EventStore

        store = EventStore()
    created = 0
    for p in items:
        try:
            _, is_new = store.add_event(_to_event(p))
            created += 1 if is_new else 0
        except Exception:  # noqa: BLE001 —— 单条入库失败不拖死整轮
            log.exception("flash news: add_event failed: %s", p.get("title", "")[:50])
    _FLASH_CURSOR.record_ingest(created)
    return created


async def flash_news_loop(app, *, stop: asyncio.Event) -> None:
    """常驻轮询：连续竞价 15s / 盘外 60s；失败不缓存（游标不动，下轮重拉）。"""
    from app.core.config import settings

    interval_in = settings.flash_news_interval_seconds
    interval_out = settings.flash_news_eod_interval_seconds
    log.info("flash news loop started (in=%ss out=%ss)", interval_in, interval_out)
    while not stop.is_set():
        interval = interval_in if in_trading_window() else interval_out
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except TimeoutError:
            pass
        try:
            n = await poll_once(app)
            if n:
                log.info("flash news: +%d new events", n)
        except Exception:  # noqa: BLE001 —— 常驻 loop 永不自灭
            log.exception("flash news: poll failed")
    await aclose()
    log.info("flash news loop stopped")
