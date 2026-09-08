from __future__ import annotations

from datetime import timedelta, timezone

from app.market import price_rules
from app.schemas.market import OrderBook, Quote, Quality, utcnow

# 质量判定优先级：invalid > low > high。
# medium 预留给延迟数据源；stale 由 QuoteHub 在刷新失败/超时时统一标记。

_INVALID_SYMBOL_DIGITS = 6
_FUTURE_TOLERANCE = timedelta(minutes=5)
_PCT_MISMATCH_TOLERANCE = 1.0  # 涨跌幅与昨收反推值允许的百分点误差


def board_limit_pct(quote: Quote) -> float:
    """涨跌停幅度（小数）：主板 ±10%，创业板/科创板 ±20%，北交所 ±30%，ST ±5%。

    2026-09-07 R1 收口：判定单点在 app/market/price_rules.limit_pct（百分数），
    本函数保留小数单位对外契约（quote 参数），仅做单位换算委托。
    新股上市初期等特殊阶段未在此展开，由后续交易规则模块接管。
    """
    return price_rules.limit_pct(quote.symbol, quote.name) / 100.0


def _add(reasons: list[str], invalid: list[str], reason: str, is_invalid: bool) -> None:
    reasons.append(reason)
    if is_invalid:
        invalid.append(reason)


def validate_quote(new: Quote, prev: Quote | None = None, *, live: bool | None = None) -> Quote:
    """按 §2.3 校验一条行情，直接在 new 上落 quality / quality_reasons 并返回。

    时段感知（2026-09-01 用户反馈）：非交易时段（盘前/收盘后/周末/节假日），
    数据源返回空字段或「昨收 + 0」快照是常态，不是数据质量问题——此前盘前
    全部标的被标 low("可疑")/invalid("非法") 纯属误判。因此完整性罚分
    （missing_price/price 越界/涨跌幅不匹配/与上一条对比）仅在交易时段生效；
    结构性错误（symbol 非法、负成交量、时间戳在未来）任何时段都判。
    live 缺省自动判定（trade_calendar.in_trading_window），测试可显式传。
    """
    if live is None:
        from app.market.trade_calendar import in_trading_window

        live = in_trading_window()

    reasons: list[str] = []
    invalid: list[str] = []

    if len(new.symbol) != _INVALID_SYMBOL_DIGITS or not new.symbol.isdigit():
        _add(reasons, invalid, "invalid_symbol", True)

    for field in ("volume", "amount"):
        v = getattr(new, field)
        if v is not None and v < 0:
            _add(reasons, invalid, f"negative_{field}", True)

    if new.data_timestamp is not None:
        now = utcnow()
        if new.data_timestamp.tzinfo is None:
            new.data_timestamp = new.data_timestamp.replace(tzinfo=timezone.utc)
        if new.data_timestamp > now + _FUTURE_TOLERANCE:
            _add(reasons, invalid, "timestamp_in_future", True)

    if live:
        if new.price is None:
            _add(reasons, invalid, "missing_price", False)
        elif new.price <= 0:
            _add(reasons, invalid, "non_positive_price", True)

        # 0 值 = 「未建立」而非「越界」：开盘头几拍源仍返回盘前形态（价格=昨收、
        # high/low=0），而 9:30 窗口已开——把 0 当越界依据会把这几拍全部误判
        # invalid"非法"（2026-09-03 用户反馈刷新后短暂显示非法的根因）。
        # 降为 low 留痕（不上界面徽标），下一拍形态切换后自动恢复。
        high_set = new.high is not None and new.high > 0
        low_set = new.low is not None and new.low > 0
        if high_set and low_set and new.high < new.low:
            _add(reasons, invalid, "high_below_low", True)
        if new.price is not None and high_set and new.price > new.high:
            _add(reasons, invalid, "price_above_high", True)
        if new.price is not None and low_set and new.price < new.low:
            _add(reasons, invalid, "price_below_low", True)
        if new.price is not None and new.price > 0 and (new.high is not None or new.low is not None) and not (high_set and low_set):
            reasons.append("unset_high_low")

        if (
            new.price is not None
            and new.price > 0
            and new.prev_close is not None
            and new.prev_close > 0
            and new.change_pct is not None
        ):
            implied_pct = (new.price - new.prev_close) / new.prev_close * 100
            if abs(implied_pct - new.change_pct) > _PCT_MISMATCH_TOLERANCE:
                _add(reasons, invalid, "change_pct_mismatch", False)

        if prev is not None and not invalid:
            if prev.data_timestamp is not None and new.data_timestamp is not None:
                prev_ts = prev.data_timestamp if prev.data_timestamp.tzinfo else prev.data_timestamp.replace(tzinfo=timezone.utc)
                new_ts = new.data_timestamp if new.data_timestamp.tzinfo else new.data_timestamp.replace(tzinfo=timezone.utc)
                if new_ts < prev_ts:  # 严格早于才算倒退；同秒更新不判罚
                    _add(reasons, invalid, "time_regress", False)
            if prev.price is not None and prev.price > 0 and new.price is not None:
                drift = abs(new.price - prev.price) / prev.price
                limit = board_limit_pct(new)
                if drift > limit:
                    _add(reasons, invalid, f"tick_jump_gt_{int(limit * 100)}pct", False)

    if invalid:
        new.quality = Quality.invalid
    elif reasons:
        new.quality = Quality.low
    else:
        new.quality = Quality.high
    if not live and new.quality == Quality.high:
        new.quality_reasons = ["off_session"]  # 供展示层提示"休市"，不参与降级
    else:
        new.quality_reasons = reasons
    return new


def validate_order_book(ob: OrderBook, *, live: bool | None = None) -> OrderBook:
    """盘口校验。空盘口在非交易时段是常态（挂单已清空），不判罚分。"""
    if live is None:
        from app.market.trade_calendar import in_trading_window

        live = in_trading_window()

    reasons: list[str] = []
    invalid: list[str] = []
    bid_prices = [lv.price for lv in ob.bids if lv.price is not None]
    ask_prices = [lv.price for lv in ob.asks if lv.price is not None]
    if not bid_prices or not ask_prices:
        if live:
            _add(reasons, invalid, "empty_order_book", False)
    else:
        if any(p <= 0 for p in bid_prices + ask_prices):
            _add(reasons, invalid, "non_positive_price", True)
        if any(lv.volume is not None and lv.volume < 0 for lv in ob.bids + ob.asks):
            _add(reasons, invalid, "negative_volume", True)
        if bid_prices[0] > ask_prices[0]:
            _add(reasons, invalid, "bid1_above_ask1", True)
        # 买档必须严格降序，卖档必须严格升序
        for seq, descending in ((bid_prices, True), (ask_prices, False)):
            broken = any(
                (seq[i] <= seq[i + 1]) if descending else (seq[i] >= seq[i + 1])
                for i in range(len(seq) - 1)
            )
            if broken:
                _add(reasons, invalid, "level_order_broken", True)
                break
    if invalid:
        ob.quality = Quality.invalid
    elif reasons:
        ob.quality = Quality.low
    else:
        ob.quality = Quality.high
    if not live and ob.quality == Quality.high:
        ob.quality_reasons = ["off_session"]
    else:
        ob.quality_reasons = reasons
    return ob


def mark_stale(quote: Quote, reason: str) -> Quote:
    quote.quality = Quality.stale
    quote.quality_reasons = [reason]
    return quote
