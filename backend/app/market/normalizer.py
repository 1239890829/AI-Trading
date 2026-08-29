"""Eastmoney 行情字段 → 统一 schema 的 Normalizer。

字段口径实测于 2026-08-28（详见 docs/data-sources.md）：
- ulist/clist 族：f2 价、f3 涨幅%、f4 涨跌、f5 成交量(手)、f6 成交额(元)、
  f8 换手率、f15/16/17/18 = 高/低/开/昨收、f12/13/14 = 代码/市场/名称、f124 时间戳
- 涨停池 getTopicZTPool：p=价格×100、fbt/lbt=HHMMSS、fund=封单额(元)、zbc=炸板次数
- 龙虎榜 RPT_DAILYBILLBOARD_DETAILSNEW：SECURITY_CODE/BILLBOARD_* 等
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

from app.schemas.market import (
    Kline,
    LimitUpRecord,
    LongHuRecord,
    OrderBook,
    OrderBookLevel,
    Quote,
    Trade,
)

EASTMONEY_SOURCE = "eastmoney"


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    s = str(v).strip()
    if s in {"", "-", "null", "None"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _epoch(v) -> datetime | None:
    n = _num(v)
    if n is None or n <= 0:
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc)


def _market(mkt) -> str | None:
    n = _num(mkt)
    if n is None:
        return None
    return {1: "SH", 0: "SZ"}.get(int(n), str(int(n)))


def _hhmmss(v) -> str | None:
    n = _num(v)
    if n is None:
        return None
    s = f"{int(n):06d}"
    return f"{s[:2]}:{s[2:4]}:{s[4:6]}"


def normalize_index(raw: dict) -> Quote:
    return Quote(
        symbol=str(raw.get("f12") or ""),
        name=raw.get("f14"),
        market=_market(raw.get("f13")),
        price=_num(raw.get("f2")),
        change=_num(raw.get("f4")),
        change_pct=_num(raw.get("f3")),
        amount=_num(raw.get("f6")),
        data_timestamp=_epoch(raw.get("f124")),
        source=EASTMONEY_SOURCE,
    )


def normalize_quote(raw: dict) -> Quote:
    volume_hands = _num(raw.get("f5"))
    return Quote(
        symbol=str(raw.get("f12") or ""),
        name=raw.get("f14"),
        market=_market(raw.get("f13")),
        price=_num(raw.get("f2")),
        change=_num(raw.get("f4")),
        change_pct=_num(raw.get("f3")),
        high=_num(raw.get("f15")),
        low=_num(raw.get("f16")),
        open=_num(raw.get("f17")),
        prev_close=_num(raw.get("f18")),
        volume=volume_hands * 100 if volume_hands is not None else None,  # 手 → 股
        amount=_num(raw.get("f6")),
        turnover_rate=_num(raw.get("f8")),
        data_timestamp=_epoch(raw.get("f124")),
        source=EASTMONEY_SOURCE,
    )


# stock/get 端点的五档盘口字段（与 ulist 族编号不同）。
ASK_FIELDS = [("f12", "f11"), ("f14", "f13"), ("f15", "f16"), ("f17", "f18"), ("f19", "f20")]
BID_FIELDS = [("f31", "f32"), ("f33", "f34"), ("f35", "f36"), ("f37", "f38"), ("f39", "f40")]


def normalize_order_book(symbol: str, raw: dict) -> OrderBook:
    asks = [OrderBookLevel(price=_num(raw.get(p)), volume=_num(raw.get(v))) for p, v in ASK_FIELDS]
    bids = [OrderBookLevel(price=_num(raw.get(p)), volume=_num(raw.get(v))) for p, v in BID_FIELDS]
    return OrderBook(
        symbol=symbol,
        bids=[lv for lv in bids if lv.price is not None],
        asks=[lv for lv in asks if lv.price is not None],
        data_timestamp=_epoch(raw.get("f86")),
        source=EASTMONEY_SOURCE,
    )


def normalize_kline_row(symbol: str, timeframe: str, row: str) -> Kline | None:
    parts = row.split(",")
    if len(parts) < 7:
        return None
    try:
        ts = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            ts = datetime.strptime(parts[0], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return Kline(
        symbol=symbol,
        timeframe=timeframe,
        ts=ts,
        open=_num(parts[1]),
        close=_num(parts[2]),
        high=_num(parts[3]),
        low=_num(parts[4]),
        volume=_num(parts[5]),  # 手
        amount=_num(parts[6]),
        change_pct=_num(parts[8]) if len(parts) > 8 else None,
        turnover_rate=_num(parts[10]) if len(parts) > 10 else None,
        source=EASTMONEY_SOURCE,
    )


def normalize_trade(symbol: str, row: str) -> Trade | None:
    parts = row.split(",")
    if len(parts) < 3:
        return None
    today = date.today().isoformat()
    try:
        ts = datetime.strptime(f"{today} {parts[0]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        ts = None
    # details 行格式：时间,价格,成交量(手),成交额,买卖方向(1买/2卖/4中性)
    side_raw = parts[4] if len(parts) > 4 else None
    side = {"1": "buy", "2": "sell"}.get(str(side_raw), "neutral")
    return Trade(
        symbol=symbol,
        ts=ts,
        price=_num(parts[1]),
        volume=_num(parts[2]),  # 手
        side=side,
        source=EASTMONEY_SOURCE,
    )


def normalize_limit_up(raw: dict, trade_date: date) -> LimitUpRecord | None:
    symbol = str(raw.get("c") or "")
    if not symbol:
        return None
    raw_price = _num(raw.get("p"))
    price = raw_price / 100 if raw_price is not None else None
    stat = raw.get("zttj") or {}
    days, count = stat.get("days"), stat.get("ct")
    boards_stat = f"{days}天{count}板" if days is not None and count is not None else None
    return LimitUpRecord(
        symbol=symbol,
        name=raw.get("n"),
        trade_date=trade_date,
        price=price,
        change_pct=_num(raw.get("zdp")),
        first_seal_time=_hhmmss(raw.get("fbt")),
        last_seal_time=_hhmmss(raw.get("lbt")),
        break_count=int(_num(raw.get("zbc")) or 0),
        seal_amount=_num(raw.get("fund")),
        turnover_rate=_num(raw.get("hs")),
        consecutive_boards=int(_num(raw.get("lbc")) or 0),
        boards_stat=boards_stat,
        source=EASTMONEY_SOURCE,
    )


def normalize_longhu(raw: dict) -> LongHuRecord | None:
    symbol = str(raw.get("SECURITY_CODE") or "")
    raw_date = str(raw.get("TRADE_DATE") or "")[:10]
    if not symbol or not raw_date:
        return None
    return LongHuRecord(
        symbol=symbol,
        name=raw.get("SECURITY_NAME_ABBR"),
        trade_date=date.fromisoformat(raw_date),
        close=_num(raw.get("CLOSE_PRICE")),
        change_pct=_num(raw.get("CHANGE_RATE")),
        turnover_rate=_num(raw.get("TURNOVERRATE")),
        amount=_num(raw.get("BILLBOARD_DEAL_AMT")),
        net_buy=_num(raw.get("BILLBOARD_NET_AMT")),
        buy_amount=_num(raw.get("BILLBOARD_BUY_AMT")),
        sell_amount=_num(raw.get("BILLBOARD_SELL_AMT")),
        reason=raw.get("EXPLAIN") or raw.get("EXPLANATION"),
        source=EASTMONEY_SOURCE,
    )


def normalize_search(raw: dict) -> tuple[str, str | None, str | None] | None:
    code = raw.get("Code")
    # 只收 A 股/指数：6 位数字代码（东财 suggest 会混入港股等 5 位代码）
    if not code or len(str(code)) != 6 or not str(code).isdigit():
        return None
    m = _num(raw.get("MktNum"))
    mkt = {1: "SH", 0: "SZ"}.get(int(m)) if m is not None else None  # 注意 0=深市，勿用 or 短路
    return str(code), raw.get("Name"), mkt


def _seat_type(name: str | None) -> str:
    n = name or ""
    if "机构专用" in n:
        return "机构专用"
    if "沪股通" in n or "深股通" in n:
        return "互联互通"
    if "量化" in n:
        return "量化席位"
    return "营业部"


def normalize_longhu_seat(raw: dict, side: str) -> dict | None:
    name = raw.get("OPERATEDEPT_NAME")
    code = str(raw.get("SECURITY_CODE") or "")
    if not code:
        return None
    num = lambda v: float(v) if v is not None else None  # noqa: E731
    return {
        "symbol": code,
        "side": side,  # buy / sell
        "seat": name,
        "seat_type": _seat_type(name),
        "buy": num(raw.get("BUY")),
        "sell": num(raw.get("SELL")),
        "net": num(raw.get("NET")),
        "reason": raw.get("EXPLANATION"),
        "rise_probability_3day": num(raw.get("RISE_PROBABILITY_3DAY")),
        "source": EASTMONEY_SOURCE,
    }


def normalize_longhu_history(raw: dict) -> dict | None:
    code = str(raw.get("SECURITY_CODE") or "")
    raw_date = str(raw.get("TRADE_DATE") or "")[:10]
    if not code or not raw_date:
        return None
    num = lambda v: float(v) if v is not None else None  # noqa: E731
    return {
        "symbol": code,
        "trade_date": raw_date,
        "close": num(raw.get("CLOSE_PRICE")),
        "change_pct": num(raw.get("CHANGE_RATE")),
        "net_buy": num(raw.get("BILLBOARD_NET_AMT")),
        "amount": num(raw.get("BILLBOARD_DEAL_AMT")),
        "reason": raw.get("EXPLAIN") or raw.get("EXPLANATION"),
        "after_1d": num(raw.get("D1_CLOSE_ADJCHRATE")),
        "after_3d": num(raw.get("D3_CLOSE_ADJCHRATE")),
        "after_5d": num(raw.get("D5_CLOSE_ADJCHRATE")),
        "after_10d": num(raw.get("D10_CLOSE_ADJCHRATE")),
        "source": EASTMONEY_SOURCE,
    }


def normalize_financial(raw: dict) -> dict | None:
    code = str(raw.get("SECURITY_CODE") or "")
    rd = str(raw.get("REPORTDATE") or "")[:10]
    if not code or not rd:
        return None
    num = lambda v: float(v) if v is not None else None  # noqa: E731
    return {
        "symbol": code,
        "report_date": rd,
        "revenue": num(raw.get("TOTAL_OPERATE_INCOME")),
        "revenue_yoy": num(raw.get("YSTZ")),
        "net_profit": num(raw.get("PARENT_NETPROFIT")),
        "profit_yoy": num(raw.get("SJLTZ")),
        "gross_margin": num(raw.get("XSMLL")),
        "roe": num(raw.get("WEIGHTAVG_ROE")),
        "eps": num(raw.get("BASIC_EPS")),
        "debt_ratio": num(raw.get("DEBT_ASSET_RATIO")),
        "netcash_operate_ps": num(raw.get("PER_NETCASH_OPERATE")),
        "source": EASTMONEY_SOURCE,
    }
