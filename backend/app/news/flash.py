"""东财 7x24 快讯流（docs/summary/architecture-design.md §3，G1/P0①，2026-09-07）。

系统此前只有「按标的拉取」的个股资讯，没有全市场快讯源——热点消息无人
手工录入就永远进不了系统（设计文档 G1 缺口）。本模块补齐采集层：

- 接口：np-weblist ``comm/web/getFastNewsList``，频道由 `settings.flash_news_columns`
  控制（**默认 100 = 全部**，2026-09-10 实测：含公司/资金/政策/市场，已覆盖 101 要闻）；
  原用 101（仅宏观）导致「公司消息覆盖窄 → 盘后个股消息无法关联」（retro P0-2）；
- 必带 UA + Referer + req_trace（缺任一被东财拦截）；主域间歇性拦截 →
  双域 failover（np-weblist → np-listapi，两者盘中实测均通）；
- 每条自带 `stockList` 关联标的（`0.301468` 深 / `1.688496` 沪 / `90.BKxxxx` 板块 /
  `150.xxxxxx` 基金）→ **提取首个 A 股作 `source_symbol`**，补「公司快讯无标的归属」
  的缺口（retro P0-3 前半；板块代码映射待后续）；
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

from app.core.ttl_cache import TTLCache
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

#: board_code → 题材映射缓存（retro P0-3 后半）：300s 比板块列表缓存（30s）长，
#: 避免 15s 快讯轮询频繁触发板块列表重拉（性能第一）。
_BOARD_THEME_CACHE = TTLCache("flash-board-theme", ttl=300.0, maxsize=1)

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


def _a_share_symbols(stock_list) -> list[str]:
    """从东财 ``stockList`` 提取 A 股代码（保留顺序）。

    stockList 元素形如「市场.代码」（2026-09-10 实测）：

    - ``0.301468`` / ``1.688496`` → A 股（0=深市 / 1=沪市）✅ **取**
    - ``90.BK0800``               → 东财板块代码（非个股）✗
    - ``150.512480``              → 基金 / ETF / 可转债 ✗

    只认「0/1 + 6 位纯数字」，其余**显式跳过**（不臆造归属）。
    """
    out: list[str] = []
    for raw in stock_list or []:
        s = str(raw or "").strip()
        market, sep, code = s.partition(".")
        if sep and market in ("0", "1") and len(code) == 6 and code.isdigit():
            if code not in out:
                out.append(code)
    return out


def _board_codes(stock_list) -> list[str]:
    """从东财 ``stockList`` 提取板块代码（``90.BKxxxx`` → ``BKxxxx``），保序去重。

    retro P0-3 后半：实测约 35% 快讯只有板块代码、无个股代码——这类快讯若只
    提取 A 股会得到空 source_symbol，变成关联不到任何标的的僵尸事件。板块代码
    后续经 board_flow 映射成 ths 题材名，挂到事件方向行。
    """
    out: list[str] = []
    for raw in stock_list or []:
        s = str(raw or "").strip()
        if s.startswith("90.") and len(s) > 3:
            code = s[3:]
            if code not in out:
                out.append(code)
    return out


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
    code = (item.get("code") or "").strip() or None
    # 2026-09-09：东财快讯列表接口**不返回 url**，只返回 code（快讯唯一 ID）。
    # 原文链接 = https://finance.eastmoney.com/a/{code}.html（实测 8/8 全 200；
    # kuaixun.eastmoney.com 同路径 404）。此前 _to_event 未拼 url → 快讯事件
    # url 恒为 None → 前端「点不开 / 无原文链接」。此处用 code 拼出原文 url。
    url = f"https://finance.eastmoney.com/a/{code}.html" if (code and code.isdigit()) else None
    return {
        "title": title,
        "summary": (item.get("summary") or "").strip() or None,
        "code": code,
        "url": url,
        "show_time": show_time,
        # 关联标的（2026-09-10 新增）：stockList → A 股代码列表，供 source_symbol 挂载
        "symbols": _a_share_symbols(item.get("stockList")),
        # 关联板块（retro P0-3 后半）：stockList → 东财板块代码，供 board→题材映射
        "board_codes": _board_codes(item.get("stockList")),
    }


async def fetch_fast_news(*, limit: int = _PAGE_SIZE, column: int = 100, pages: int = 1) -> list[dict] | None:
    """拉取快讯（双域 failover + sortEnd 游标翻页）。全败返回 None（显式失败，绝不静默当空列表）。

    - 逐域尝试：任一域翻页拿到非空结果即返回；
    - 翻页：用上一页 `data.sortEnd` 作下一页 `sortEnd` 游标，最多 `pages` 页；
      页返回空 / sortEnd 空 / 无新增 → 提前停（不再空转）；
    - 跨页按 code 去重（游标边界可能重复）；
    - 2026-09-10 实测：pageSize=50 翻页边界无缝衔接（次页首条 = 上页末条前一分钟）。
    """
    last_err: Exception | None = None
    for host in _HOSTS:
        try:
            out: list[dict] = []
            seen: set[str] = set()
            sort_end = ""
            for _ in range(max(1, pages)):
                try:
                    resp = await _http().get(host + _PATH, params={
                        "client": "web",
                        "biz": "web_724",
                        "fastColumn": str(column),
                        "sortEnd": sort_end,
                        "pageSize": str(limit),
                        "req_trace": _req_trace(),
                    })
                    resp.raise_for_status()
                    data = resp.json().get("data") or {}
                except Exception:
                    # 翻页中途失败：已有数据返回部分结果（部分成功即可用）；否则换域
                    if out:
                        return out
                    raise
                items = data.get("fastNewsList") or []
                added = 0
                for p in (_parse_item(i) for i in items):
                    if p is None:
                        continue
                    key = p.get("code") or p.get("title") or ""
                    if key and key not in seen:
                        seen.add(key)
                        out.append(p)
                        added += 1
                nxt = (data.get("sortEnd") or "").strip()
                if not items or not nxt or added == 0:
                    break
                sort_end = nxt
            if out:
                return out
            last_err = ValueError(f"{host} 返回 200 但 fastNewsList 为空")
        except Exception as exc:  # noqa: BLE001 —— 逐域尝试，最后一个错误显式带出
            last_err = exc
    log.warning("flash news: all hosts failed: %s", last_err)
    return None


def configured_columns() -> list[int]:
    """`settings.flash_news_columns`（逗号分隔）→ 频道号列表；非法项显式丢弃。

    全非法/为空 → 回退 ``[100]``（默认全部）；绝不因配置写错而静默不拉。
    """
    from app.core.config import settings

    out: list[int] = []
    for part in (settings.flash_news_columns or "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) not in out:
            out.append(int(part))
    return out or [100]


async def fetch_fast_news_multi(columns: list[int] | None = None, pages: int | None = None) -> list[dict] | None:
    """多频道并发拉取 + 跨频道按 ``code`` 去重。

    单频道失败不影响其他（部分成功即可用）；**全败才返回 None**——与单频道
    的「显式失败」语义一致，游标据此记 ``last_ok=False``（不静默当空列表）。
    单频道时直达 `fetch_fast_news`（零 gather 开销，保持性能红线）。
    ``pages`` 每频道翻页数（None → settings.flash_news_pages）。
    """
    if pages is None:
        from app.core.config import settings

        pages = settings.flash_news_pages
    cols = columns or configured_columns()
    if len(cols) == 1:
        return await fetch_fast_news(column=cols[0], pages=pages)
    results = await asyncio.gather(
        *(fetch_fast_news(column=c, pages=pages) for c in cols), return_exceptions=True
    )
    merged: list[dict] = []
    seen: set[str] = set()
    ok_any = False
    for r in results:
        if isinstance(r, BaseException) or r is None:
            continue
        ok_any = True
        for p in r:
            key = p.get("code") or p.get("title") or ""
            if key in seen:
                continue
            seen.add(key)
            merged.append(p)
    if not ok_any:
        log.warning("flash news: all columns failed (cols=%s)", cols)
        return None
    return merged


def _theme_names(state) -> list[str]:
    """官方目录题材名（快讯实体匹配用）；目录未同步 → 空（缺方向行但不臆造）。

    与 app/api/routes/events.py 的 _theme_names 同口径（同一 theme_catalog 服务），
    此处独立实现只为避免 news → api 的循环导入。
    """
    svc = getattr(state, "theme_catalog", None)
    if svc is None:
        return []
    try:
        if svc.catalog_size() == 0:
            return []
        return [t.name for t in svc.get_catalog(limit=1000)]
    except Exception:  # noqa: BLE001 —— 目录取不到按空处理，快讯入库不因此中断
        return []


def _to_event(p: dict, theme_names: list[str] | None = None,
              board_themes: list[dict] | None = None) -> dict:
    """快讯行 → EventCard dict。

    2026-09-09 修复：此前**未传 theme_names**，导致官方题材目录匹配整条链路失效，
    只剩 ENTITY_ALIASES 手工别名表（~15 个热门题材）能命中——实测
    「北京：加快发展商业航天产业」抓到了却匹配不到任何板块（directions 为空），
    商业航天个股（600118）关联事件恒为 0。快讯是全市场源，必须按全市场目录匹配。

    2026-09-10 新增：快讯自带 `stockList` → 首个 A 股作 `source_symbol`，
    让「博盈特焊：海外订单充裕…」这类**公司快讯**也能挂到个股（retro P0-3 前半）；
    板块代码映射出的 `board_themes` 挂到事件方向行（retro P0-3 后半）。
    """
    from app.events.extract import build_event

    published = p.get("show_time")
    if published is not None:
        # 2026-09-09 时区修复：此前 astimezone(utc) 入库 → 展示被当北京时间
        # → 全部时间"早了 8 小时"（用户看到的事件全是早上）。事件时间口径
        # 统一为北京 naive（app.core.db.beijing_now_naive docstring）。
        published = published.astimezone(_TZ_BJ).replace(tzinfo=None)
    symbols = p.get("symbols") or []
    return build_event(
        p["title"],
        source="东财快讯",
        url=p.get("url"),
        summary=p.get("summary"),
        published_at=published,
        source_symbol=symbols[0] if symbols else None,
        theme_names=theme_names or [],
        board_themes=board_themes or [],
    )


async def _board_theme_map(state, theme_names: list[str] | None = None) -> dict[str, dict]:
    """board_code → ``{"theme_name", "board_name"}``（东财板块名与 ths 目录名/词干精确对齐才映射）。

    retro P0-3 后半。仅**精确对齐**（同名，或去「概念/板块/产业/指数」后缀后同名），
    不做模糊匹配——东财板块 vs ths 题材是两套体系（2026-09-07 已核对差异），跨源
    口径不可臆造。目录未同步 / 板块列表拉不到 → 返回 ``{}``（缺 board 方向行，不臆造）。
    """
    from app.events.extract import _name_stem

    theme_names = theme_names if theme_names is not None else _theme_names(state)
    if not theme_names:
        return {}
    theme_by_name = set(theme_names)
    theme_by_stem: dict[str, str] = {}
    for t in theme_names:
        stem = _name_stem(t)
        if stem and stem not in theme_by_stem:
            theme_by_stem[stem] = t

    from app.market.board_flow import get_board_list

    mapping: dict[str, dict] = {}
    for kind in ("concept", "industry"):
        try:
            rows, _ = await get_board_list(kind)
        except Exception:  # noqa: BLE001 —— 板块列表拿不到就跳过，不拖死快讯入库
            log.exception("flash board theme map: %s failed", kind)
            continue
        for r in rows or []:
            code, bname = r.get("board_code"), r.get("name")
            if not code or not bname or code in mapping:
                continue
            if bname in theme_by_name:
                theme = bname
            else:
                stem = _name_stem(bname)
                theme = theme_by_stem.get(stem)
            if theme:
                mapping[code] = {"theme_name": theme, "board_name": bname, "board_code": code}
    return mapping


async def poll_once(app) -> int:
    """拉一轮 → 逐条 build_event → EventStore 指纹去重入库。返回新增条数。

    app 可以是 FastAPI 实例（走 app.state）或任何带 event_store 属性的对象
    （测试直接传 SimpleNamespace）。
    """
    items = await fetch_fast_news_multi()
    _FLASH_CURSOR.record_fetch(items is not None, len(items or []))
    if not items:
        return 0
    state = getattr(app, "state", app)
    store = getattr(state, "event_store", None)
    if store is None:
        from app.events.store import EventStore

        store = EventStore()
    # 题材目录每轮取一次（不是每条取一次）：目录是千级名称，逐条重取没必要。
    # 目录未同步时为空 → 方向行缺失但不臆造（与 events 路由同口径）。
    theme_names = _theme_names(state)
    # board_code → 题材映射（300s 缓存）：让只有板块代码的快讯也能挂题材（P0-3 后半）
    hit, board_map = _BOARD_THEME_CACHE.get("map")
    if not hit:
        board_map = await _board_theme_map(state, theme_names)
        _BOARD_THEME_CACHE.set("map", board_map)
    created = 0
    for p in items:
        try:
            board_themes = [board_map[c] for c in (p.get("board_codes") or []) if c in board_map]
            _, is_new = store.add_event(_to_event(p, theme_names, board_themes))
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
