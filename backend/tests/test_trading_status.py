"""停牌判定回归测试（UI 缺陷 #1）。

三条**不可回退**的红线，每条都由真实事故/实测反例驱动：

1. **不得靠"成交量 = 0"判停牌**——一字涨停同样无量（2026-09-02 全市场实测：
   `volume==0` 的 19 只里 12 只是数据源 502 而非停牌）。本模块的判据是"日K 缺 bar"。
2. **空 K 线必须判 `unknown`，不得判"未上市"**——301688 / 601091 的"零 bar"
   实测是接口 502，把数据源故障说成事实属于静默误判。
3. **盘中不得把正常股误判成停牌**——当日未收盘时数据源通常还没有当日 bar，
   "缺今天"必须从停牌天数里剔除，否则盘中全市场都会被标停牌 1 天。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.market.trading_status import BJ, bar_date, beijing_now, resolve_trading_status
from app.schemas.market import TradingStatus

# 2026-08-24(Mon) … 2026-09-02(Wed)，周末已剔除
DAYS = [
    date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27),
    date(2026, 8, 28), date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2),
]

CLOSED = datetime(2026, 9, 2, 15, 30, tzinfo=BJ)     # 收盘后：日K 应已包含当日
PREOPEN = datetime(2026, 9, 2, 2, 10, tzinfo=BJ)     # 盘中/盘前：当日 bar 通常还没有
INTRADAY = datetime(2026, 9, 2, 10, 30, tzinfo=BJ)   # 连续竞价中


def test_normal_stock_with_today_bar_is_trading():
    """收盘后，最后一根 bar == 最近交易日 → 正常交易。"""
    info = resolve_trading_status(DAYS[:-1] + [DAYS[-1]], DAYS, now=CLOSED)
    assert info.status == TradingStatus.trading
    assert info.last_bar_date == date(2026, 9, 2)
    assert info.suspended_days is None


def test_suspended_stock_counts_missing_trade_days():
    """002274 真实案例：最后 bar 8/25，未收盘口径下截至 9/2 停牌 5 个交易日。

    注意 9/2 当天**未计入**——它还没收盘、日K 未生成。这与线上实测一致
    （2026-09-02 02:10 查询返回 suspended_days=5），不是四舍五入。
    """
    bars = [d for d in DAYS if d <= date(2026, 8, 25)]
    info = resolve_trading_status(bars, DAYS, now=PREOPEN)
    assert info.status is TradingStatus.suspended
    assert info.suspended_days == 5
    assert info.suspended_since == date(2026, 8, 26)
    assert info.anchor_date == date(2026, 9, 2)
    assert "停牌 5 个交易日" in info.reason


def test_suspended_count_grows_after_close():
    """同一只票收盘后再判，天数含当日——盘中/盘后口径差异是设计如此，不是抖动。"""
    bars = [d for d in DAYS if d <= date(2026, 8, 25)]
    assert resolve_trading_status(bars, DAYS, now=CLOSED).suspended_days == 6


def test_intraday_normal_stock_is_not_misflagged_as_suspended():
    """红线 3：盘中当日 bar 还没落地，正常股**不能**被判停牌。"""
    bars = [d for d in DAYS if d <= date(2026, 9, 1)]
    for now in (PREOPEN, INTRADAY):
        info = resolve_trading_status(bars, DAYS, now=now)
        assert info.status is TradingStatus.trading, now
        assert info.reason  # 必须给出依据，不是空白结论


def test_intraday_suspended_excludes_today_from_count():
    """盘中停牌股：天数只数到上一个完整交易日，不含"还没生成的今天"。"""
    bars = [d for d in DAYS if d <= date(2026, 8, 31)]
    info = resolve_trading_status(bars, DAYS, now=INTRADAY)
    assert info.status == TradingStatus.suspended
    assert info.suspended_days == 1          # 只 9/1；9/2 未收盘不计
    assert info.suspended_since == date(2026, 9, 1)
    assert info.anchor_date == date(2026, 9, 2)


def test_same_stock_after_close_counts_today():
    """同一只票收盘后再判，天数含当日——盘中/盘后口径差异是**设计如此**。"""
    bars = [d for d in DAYS if d <= date(2026, 8, 31)]
    info = resolve_trading_status(bars, DAYS, now=CLOSED)
    assert info.suspended_days == 2


def test_empty_bars_is_unknown_not_pre_listing():
    """红线 2：零 bar 是数据源 502（实测 301688/601091），不得判成"未上市"。"""
    info = resolve_trading_status([], DAYS, now=CLOSED)
    assert info.status is TradingStatus.unknown
    assert "未上市" in info.reason  # 只作为"可能性之一"陈述
    assert info.suspended_days is None


def test_missing_calendar_is_unknown_not_trading():
    """日历不可用 → unknown + 原因，绝不静默退化成 trading。"""
    info = resolve_trading_status(DAYS, [], now=CLOSED)
    assert info.status is TradingStatus.unknown
    assert "交易日历" in info.reason


def test_future_bar_treated_as_trading():
    """数据源给了未来日期（异常但无害）→ 判 trading，不崩、不判停牌。"""
    info = resolve_trading_status(DAYS + [date(2026, 9, 3)], DAYS, now=CLOSED)
    assert info.status is TradingStatus.trading


def test_long_gap_hints_delisting():
    """缺失跨度 > 250 个交易日 → reason 额外提示可能退市/长期停牌。"""
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(0, 900, 3)]
    bars = days[:5]
    info = resolve_trading_status(bars, days, now=datetime(2026, 9, 2, 15, 30, tzinfo=BJ))
    assert info.status is TradingStatus.suspended
    assert info.suspended_days > 250
    assert "退市" in info.reason


def test_bar_date_handles_offsets():
    """统一按北京时间取日期，避免某个源改存 UTC 午夜导致整体偏一天。"""
    assert bar_date(datetime(2026, 9, 1, 0, 0, tzinfo=BJ)) == date(2026, 9, 1)
    # 同一时刻的 UTC 表示：2026-08-31T16:00Z == 北京时间 9/1 00:00
    assert bar_date(datetime(2026, 8, 31, 16, 0, tzinfo=timezone.utc)) == date(2026, 9, 1)
    # naive 按北京时间理解
    assert bar_date(datetime(2026, 9, 1, 0, 0)) == date(2026, 9, 1)


def test_beijing_now_is_offset_aware():
    assert beijing_now().tzinfo is not None
    assert beijing_now().utcoffset() == timedelta(hours=8)
