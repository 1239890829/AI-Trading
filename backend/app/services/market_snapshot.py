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

from app.core.bjtime import beijing_now, beijing_today

log = logging.getLogger(__name__)


class PoolDateError(ValueError):
    """请求日不能被证实为跌停池交易日；status_code 供 HTTP 边界映射。"""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code


async def verify_limit_down_date(provider: Any, trade_date: date) -> None:
    """所有跌停池消费入口共用日期身份闸，避免上游静默回退到前一日。"""
    from app.market.trade_calendar import is_trade_day_on, trading_days

    try:
        days = await trading_days(provider)
    except Exception as exc:
        raise PoolDateError(503, f"交易日历不可用，无法核实跌停池日期：{exc}") from exc
    if not days or trade_date < days[0]:
        raise PoolDateError(503, "交易日历未覆盖所查跌停池日期")
    state = is_trade_day_on(trade_date, days)
    if state is None:
        raise PoolDateError(503, "交易日历尚未覆盖所查跌停池日期")
    if not state:
        raise PoolDateError(422, "所查日期不是交易日，跌停池不回退到其他日期")


async def default_trade_date(hub) -> date:
    """最近交易日 —— **唯一入口走 `trade_calendar`**（2026-09-14 收口）。

    ⚠️ **原实现在此处自建了一份 24 小时的日历缓存**（`cache_on(hub, "provider.trading_days",
    86400)`），且与 `api/routes/market.py::_prev_trade_date_async` **共用同一实例**
    （`cache_on` 以 `(holder, name)` 为键挂在 hub 上，**且首次调用传入的 TTL 生效** ——
    两处各写各的 TTL 是无效的）。事故形态：进程在盘前/前夜填充缓存，当天开盘后缓存仍在
    有效期内，而**源头日历是尾随窗口、不含未来日期**（09-13 22:28 抓 ⇒ 末日 09-11），
    于是 `past[-1]` 整个交易日都取到上一交易日 —— 2026-09-14 盘中实测「盘面板块数据全是
    09-11 的」，影响本函数的**全部 14 个调用点**（market.py×7 / theme_catalog.py×5 /
    picks_intraday.py×2）。

    现在统一走 `trade_calendar.trading_days()`：它已按「**覆盖今天**」判新鲜度
    （见 `trade_calendar._is_fresh`），并有 12h 上限 + 5 分钟重取下限。
    **日历缓存只此一处**（同族反模式见 implementation §5.4 / §5.5）。

    日历不可用（含 `RuntimeError`）时退到周末规则 —— 不 500、不静默。
    """
    from app.market.trade_calendar import last_trade_date, prev_trade_date, trading_days

    days: list[date] = []
    try:
        days = await trading_days(hub.provider if hasattr(hub, "provider") else hub)
    except Exception as exc:  # noqa: BLE001  日历故障不该让行情接口 500
        log.warning("default_trade_date: trading calendar unavailable: %s", exc)

    latest = last_trade_date(days, beijing_today()) if days else None
    if latest is not None:
        now = beijing_now()
        # 盘前（<09:15）当日涨停池/龙虎榜尚未形成，数据源返回的其实是最近收盘的池
        # ——日期必须一并回溯，否则"内容 8-31、日期标 9-1"
        # （2026-09-01 00:24 实测：86 只池内容为 8-31 收盘、trade_date 标 09-01）。
        if latest == beijing_today() and now.time().replace(tzinfo=None) < dt_time(9, 15):
            prev = prev_trade_date(days, latest)
            if prev is not None:
                return prev
        return latest
    return default_trade_date_weekend_fallback()


def default_trade_date_weekend_fallback() -> date:
    """周末回退规则（交易日历不可用时的兜底）。"""
    d = beijing_today()
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
