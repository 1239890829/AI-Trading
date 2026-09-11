"""市场快照读取与「默认交易日」判定 —— 从 `api/routes/market.py` 上移到服务层。

**为什么上移（S2-4，2026-09-11 架构审查根因 C）**：这两个函数原本是
`api/routes/market.py` 的**私有**实现，却被 `picks_intraday.py` 与
`theme_catalog.py` 以
`from app.api.routes.market import _load_snapshot_map` 的形式**当公共 API 使用**
（跨模块依赖别人的私有名）。后果是 market.py 一重构，两个消费方**静默**失效——
不是编译错误，是运行期 ImportError 或行为漂移。

现在实现住在服务层，route 与消费方都从这里取；`request` 参数被换成显式的
`snapshot_service`，调用方自己决定从哪里拿（route 用 `request.app.state`）。
"""
from __future__ import annotations

import logging
from datetime import date, time as dt_time, timedelta
from pathlib import Path
from typing import Any

from app.core.ttl_cache import cache_on
from app.market.trading_status import beijing_now

log = logging.getLogger(__name__)


async def default_trade_date(hub) -> date:
    """最近交易日：优先官方交易日历（ths，缓存 24h），失败回退周末规则。"""
    cache = cache_on(hub, "provider.trading_days", 86400, maxsize=1)
    hit, days = cache.get("days")
    if not hit:
        for p in hub.providers if hasattr(hub, "providers") else [hub.provider]:
            if hasattr(p, "get_trading_days"):
                try:
                    got = await p.get_trading_days()
                    if got:  # 失败/空结果不缓存，下次请求换源重试
                        days = got
                        cache.set("days", days)
                        break
                except Exception:
                    continue
    if days:
        now = beijing_now()
        today_str = now.strftime("%Y%m%d")
        past = [d for d in days if d <= today_str]
        if past:
            latest = past[-1]
            # 盘前（<09:15）当日涨停池/龙虎榜尚未形成，数据源返回的其实是
            # 最近收盘的池——日期必须一并回溯，否则"内容 8-31、日期标 9-1"
            # （2026-09-01 00:24 实测：86 只池内容为 8-31 收盘、trade_date 标 09-01）。
            if latest == now.date().strftime("%Y%m%d") and now.time().replace(tzinfo=None) < dt_time(9, 15) and len(past) >= 2:
                latest = past[-2]
            return date(int(latest[:4]), int(latest[4:6]), int(latest[6:]))
    return default_trade_date_weekend_fallback()


def default_trade_date_weekend_fallback() -> date:
    """周末回退规则（交易日历不可用时的兜底）。"""
    d = date.today()
    return {5: d - timedelta(days=1), 6: d - timedelta(days=2)}.get(d.weekday(), d)


def load_snapshot_map(
    snapshot_service: Any,
    trade_date: date | None = None,
    columns: list[str] | None = None,
) -> dict[str, dict]:
    """读指定交易日（默认最新）的全市场快照 → ``symbol -> {列: 值}``。

    接力赚钱效应（昨日涨停股今日溢价）需要覆盖全市场的当日涨跌幅，
    逐只拉行情太慢，快照 Parquet 是现成的数据底座。读不到就返回空（溢价指标降级为 None）。

    **必须按 trade_date 取，不能永远取最新一份**：溢价问的是「该交易日的涨跌幅」，
    拿最新快照（例如周六回看上周五，快照目录却是周六）会在非交易日或回看历史日期时
    把错误的涨跌幅当成溢价——数字照样出得来，但结论是错的，属于「错了也看不出来」。
    找不到当天目录时，退到不晚于该日期的最近一份，并记 warning。

    columns（2026-09-08 概念详情用）：默认 ["symbol", "change_pct"]；可传更多列
    （如 turnover_rate/nmc——parquet 存的是完整快照行）。
    """
    cols = columns or ["symbol", "change_pct"]
    try:
        from app.services.parquet_store import read_latest_in_dir

        base = Path(getattr(snapshot_service, "parquet_dir", "") or "") / "snapshots" if snapshot_service else None
        if base is None or not base.exists():
            return {}
        day_dirs = sorted((p for p in base.iterdir() if p.is_dir()), reverse=True)

        chosen: Path | None = None
        if trade_date is not None:
            want = trade_date.strftime("%Y%m%d")
            exact = base / want
            if exact.is_dir() and sorted(exact.glob("*.parquet")):
                chosen = exact
            else:
                # 退到不晚于目标日期的最近一份
                for d in day_dirs:
                    if d.name <= want and sorted(d.glob("*.parquet")):
                        chosen = d
                        log.warning(
                            "snapshot for %s not found, falling back to %s "
                            "(溢价口径可能偏移)", want, d.name,
                        )
                        break
        if chosen is None:
            for d in day_dirs:
                if sorted(d.glob("*.parquet")):
                    chosen = d
                    break
        if chosen is None:
            return {}

        # 取该日目录里最新一份**可读**的快照：损坏文件会被跳过而不是让整个端点 502
        read = read_latest_in_dir(chosen, columns=cols)
        if not read.ok:
            log.warning("snapshot unreadable for %s: %s", chosen, read.error)
            return {}
        df = read.df
        out: dict[str, dict] = {}
        for rec in zip(*[df[c].to_list() for c in cols]):
            row = dict(zip(cols, rec))
            sym = row.get("symbol")
            if sym is None:
                continue
            out[str(sym).zfill(6)] = row
        return out
    except Exception as exc:  # 快照缺失不应让看板整体失败
        log.warning("snapshot map unavailable: %s", exc)
        return {}
