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
from app.core.freshness import Freshness
from app.market import trade_calendar as tc
from app.sentiment import metric_history
from app.sentiment.band_config import load_bands
from app.sentiment.calibration import MIN_SAMPLES, calibrate_bands, describe
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


def _stale_trade_days(window_end, days: list[date]) -> int | None:
    """历史指标库最新一日距最近交易日的**交易日**数。

    窗口尾超出日历覆盖范围时返回 None（判不出来），绝不猜——
    周末/节假日用自然日差会虚报，日历不够长时虚报为 0 同样是撒谎。
    """
    if window_end is None or not days:
        return None
    try:
        we = date.fromisoformat(str(window_end))
    except ValueError:
        return None
    if days[-1] <= we:
        return 0
    if we < days[0]:
        return None  # 日历覆盖不到库尾，无法数交易日
    return sum(1 for d in days if we < d <= days[-1])


def resolve_bands(trade_days: list[date] | None = None) -> tuple[dict, dict, str, dict]:
    """决定本次判定用哪套分档，并给出可审计的依据。

    优先级：**env 显式覆盖 > 历史分位校准 > 业界经验值**。
    显式配置必须压过自动校准，否则用户改了配置却没生效，又是一次静默失效。

    Args:
        trade_days: 交易日历（调用方通常已拉过，传入复用避免重复请求）。
                    用于把"历史库多少天没更新"换算成交易日。

    Returns:
        `(heat, earning, source, meta)`。source ∈ env_override | calibrated | defaults
        | defaults(样本不足)；meta 含校准 basis 与当前分位，未校准时 basis 里
        逐指标写明原因（沿用"缺失显式标注"纪律，不假装校准过）。
    """
    rows = metric_history.history()
    stale = _stale_trade_days(rows[-1].get("date") if rows else None, trade_days or [])
    meta: dict = {
        "samples": len(rows),
        "basis": None,
        "percentile": None,
        # 窗口必须随结论一起返回：只写"按近 N 个交易日校准"而不给首尾日期，
        # 库一旦停止更新（后台回补任务被关掉 / 数据源挂了），这句话就成了
        # 一句无人能证伪的漂亮话。stale_days 让"窗口漂移"自己浮出来。
        "window": {
            "start": rows[0].get("date") if rows else None,
            "end": rows[-1].get("date") if rows else None,
            "stale_days": stale,
        },
    }
    if stale and stale >= 3:
        meta["stale_reason"] = f"历史库已 {stale} 个交易日未更新，校准窗口变旧"

    if _BANDS_SOURCE == "env_override":
        meta["reason"] = "已启用 env 显式覆盖，跳过历史分位校准"
        return _HEAT_BANDS, _EARNING_BANDS, _BANDS_SOURCE, meta

    if not settings.sentiment_calibrate:
        meta["reason"] = "已由 ASHARE_SENTIMENT_CALIBRATE=0 关闭历史分位校准"
        return _HEAT_BANDS, _EARNING_BANDS, "defaults", meta

    if len(rows) < MIN_SAMPLES:
        # 历史库还没回补够——明说"用的仍是经验值"，不要静默假装校准过
        meta["reason"] = f"历史样本不足（{len(rows)} < {MIN_SAMPLES} 个交易日），沿用业界经验值"
        return _HEAT_BANDS, _EARNING_BANDS, "defaults", meta

    heat, h_basis = calibrate_bands(rows, HEAT_BANDS)
    earn, e_basis = calibrate_bands(rows, EARNING_BANDS)
    meta["basis"] = {"heat": h_basis, "earning": e_basis}
    meta["percentile"] = describe(rows)
    meta["reason"] = f"按近 {len(rows)} 个交易日的历史分位校准"
    return heat, earn, "calibrated", meta


class CalendarUnavailable(Exception):
    """交易日历不可用。调用方必须拒绝输出结论，不能猜日期。"""


def _snapshot_freshness(svc) -> Freshness:
    """取全市场快照的新鲜度（S2-1 契约）。

    优先用服务自身的 `freshness()`（单点口径）；测试桩没有该方法时回退到
    按 `last_success` + `poll_interval` 派生同样的判据——**不回退到"当作新鲜的"**。
    """
    fn = getattr(svc, "freshness", None)
    if callable(fn):
        return fn()
    interval = getattr(svc, "poll_interval", None) or 60.0
    return Freshness.from_age(
        as_of=getattr(svc, "last_success", None),
        fresh_within=interval * 3,
        source="snapshot",
        missing_reason="快照服务未提供成功刷新时间，无法判定新鲜度",
    )


async def compute_market_sentiment(hub, snapshot_service) -> dict:
    """计算市场情绪。

    日期锚定一律走 `trade_calendar`——不用 `date.today()` 加减天数。
    东财涨停池对非交易日静默回退到最近交易日，靠猜日期会得到自指计算结果
    （2026-08-29 事故：把市场误判为「高潮」且置信度"高"）。

    :raises CalendarUnavailable: 交易日历或最近两个交易日定位失败
    """
    snap_fresh = _snapshot_freshness(snapshot_service)
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

    heat_bands, earning_bands, bands_source, calib_meta = resolve_bands(trade_days=days)
    result = compute_sentiment(
        breadth=snapshot_service.breadth,
        pool_today=pool_today,
        pool_yesterday=pool_yesterday,
        snapshot=snapshot_service.snapshot,
        trade_date=anchor,
        prev_trade_date=prev,
        max_board_prev=max_board_prev,
        break_count=len(breaks) if breaks else None,
        bands={"heat": heat_bands, "earning": earning_bands},
    )
    # 阈值来源与校准依据**必须随结论一起返回**：让用户看到"这次判定用的是哪套阈值"，
    # 而不是只看一个阶段标签。分位口径是相对的（见 calibration 模块 docstring
    # 的"已知代价"），暴露分位数值比暴露标签更能反映真实位置。
    result["bands_source"] = bands_source
    result["calibration"] = calib_meta
    # S2-1/S1-3：快照不新鲜时**结论必须降级可见**，而不是照算不误。
    # 过去的形态是"20 分钟前的宽度配当前涨停池"——数字全都合理、结论是错的，
    # 界面上看不出来。此处沿用引擎既有的 caveats/confidence 机制（不新增第二套），
    # 因为 confidence 已在卡片与前端展示链路上，能被真正看到。
    result["snapshot_freshness"] = snap_fresh.model_dump(mode="json")
    if not snap_fresh.is_fresh():
        caveats = list(result.get("caveats") or [])
        caveats.append(f"{snap_fresh.note('全市场快照')}——宽度口径与当前涨停池可能不同时点")
        result["caveats"] = caveats
        result["confidence"] = "低"
    return {
        **result,
        "pool_today_count": len(pool_today),
        "pool_yesterday_count": len(pool_yesterday),
        "is_last_trade_date_today": anchor == date.today(),
    }
