"""市场环境计算的服务层：把原内联在路由里的逻辑抽出来，供 API 与复盘 Agent 共用。

为什么抽：**复盘 Agent 需要情绪判定，如果它自己去调 HTTP 或复制这段逻辑，
两边必然漂移**——路由改了口径而 Agent 没跟上，复盘结论就会基于过期口径。
抽成服务函数后，API 与 Agent 走同一个实现，口径只有一个。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from app.core.config import settings
from app.market import trade_calendar as tc
from app.sentiment.band_config import load_bands
from app.sentiment.engine import EARNING_BANDS, HEAT_BANDS, compute_sentiment

log = logging.getLogger(__name__)

# 阈值覆盖在模块加载时解析一次：非法配置直接让启动失败，
# 而不是每次请求才发现（band_config 的设计原则：配置错误不可静默回退）。
_HEAT_BANDS, _EARNING_BANDS, _BANDS_SOURCE = load_bands(
    settings.sentiment_heat_bands_json,
    settings.sentiment_earning_bands_json,
    HEAT_BANDS,
    EARNING_BANDS,
)
if _BANDS_SOURCE == "env_override":
    log.info("sentiment bands: env override active")


class CalendarUnavailable(Exception):
    """交易日历不可用。调用方必须拒绝输出结论，不能猜日期。"""


async def compute_market_sentiment(hub, snapshot_service) -> dict:
    """计算市场情绪。

    日期锚定一律走 `trade_calendar`——不用 `date.today()` 加减天数。
    东财涨停池对非交易日静默回退到最近交易日，靠猜日期会得到自指计算结果
    （2026-08-29 事故：把市场误判为「高潮」且置信度"高"）。

    :raises CalendarUnavailable: 交易日历或最近两个交易日定位失败
    """
    if snapshot_service.breadth is None:
        raise CalendarUnavailable("全市场快照尚未就绪")

    try:
        days = await tc.trading_days(hub.provider)
    except Exception as exc:
        log.warning("trading calendar unavailable: %s", exc)
        raise CalendarUnavailable(f"交易日历不可用：{exc}") from exc

    anchor = tc.last_trade_date(days)
    prev = tc.prev_trade_date(days, anchor)
    if not anchor or not prev:
        raise CalendarUnavailable("无法定位最近两个交易日")

    async def _pool(d: date) -> list:
        try:
            return await hub.provider.get_limit_up_pool(d)
        except Exception as exc:
            log.warning("sentiment pool %s failed: %s", d, exc)
            return []

    async def _breaks(d: date) -> list:
        try:
            return await hub.provider.get_limit_break_pool(d)
        except Exception as exc:
            log.warning("sentiment break pool %s failed: %s", d, exc)
            return []

    pool_today, pool_yesterday, breaks = await asyncio.gather(
        _pool(anchor), _pool(prev), _breaks(anchor)
    )

    max_board_prev = max((int(r.consecutive_boards or 1) for r in pool_yesterday), default=0)

    result = compute_sentiment(
        breadth=snapshot_service.breadth,
        pool_today=pool_today,
        pool_yesterday=pool_yesterday,
        snapshot=snapshot_service.snapshot,
        trade_date=anchor,
        prev_trade_date=prev,
        max_board_prev=max_board_prev,
        break_count=len(breaks) if breaks else None,
        bands={"heat": _HEAT_BANDS, "earning": _EARNING_BANDS},
    )
    return {
        **result,
        "pool_today_count": len(pool_today),
        "pool_yesterday_count": len(pool_yesterday),
        "is_last_trade_date_today": anchor == date.today(),
    }
