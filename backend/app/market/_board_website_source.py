"""Decode the official funds page response; board_flow owns all reads and caches.

The same-origin chart endpoint returns raw (fltt=1) fields.  f124 is a quote
clock only.  Neither receipt time nor quote time establishes the funds date.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
import re

from app.core.bjtime import BJ_TZ

FUNDS_URL = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
FIELDS = "f62,f3,f2,f124,f152"
BOARD_FS = {"concept": "m:90+t:3", "industry": "m:90+s:4"}
MAX_ROWS = 10000
MAX_BYTES = 2 * 1024 * 1024


def _number(value) -> Decimal | None:
    if value is None or value == "-":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("官网资金含非法数值")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("官网资金含非法数值") from exc
    if not result.is_finite():
        raise ValueError("官网资金含非有限数值")
    return result


def _scaled(value: Decimal | None, divisor: Decimal) -> float | None:
    if value is None:
        return None
    number = float(value / divisor)
    if not isfinite(number):
        raise ValueError("官网资金数值超出有效范围")
    return number


def _quote_fields(row: dict, received_at: datetime) -> dict:
    raw_price = _number(row.get("f2"))
    precision = _number(row.get("f152"))
    valid_precision = precision is not None and precision == precision.to_integral_value() and 0 <= precision <= 8
    price = (_scaled(raw_price, Decimal(10) ** int(precision))
             if valid_precision and raw_price is not None and raw_price > 0 else None)
    stamp = _number(row.get("f124"))
    quote_at = None
    if stamp is not None and stamp == stamp.to_integral_value() and 0 < stamp <= Decimal(str(received_at.timestamp())):
        try:
            quote_at = datetime.fromtimestamp(int(stamp), BJ_TZ).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    return {"price": price, "change_pct": _scaled(_number(row.get("f3")), Decimal(100)),
            "quote_source_as_of": quote_at}


def _member_identity(code: str, market: int) -> dict:
    # This product reports Beijing 4/8/920 stocks with source market 0. Do not
    # inherit another provider's "Beijing funds unavailable" limitation.
    is_b = code.startswith(("200", "900"))
    expected = (1 if code.startswith(("6", "900")) else
                0 if code.startswith(("0", "3", "4", "8", "920", "200")) else None)
    if expected is not None and market != expected:
        raise ValueError("官网成员证券代码与来源市场身份不符")
    # A read-only source observation does not grant stock-selection eligibility.
    # Unknown prefixes retain their original market ID instead of being guessed
    # into a supported market or silently removed from a complete response.
    return {"source_security_id": f"{market}.{code}", "share_class": "B" if is_b else "A" if expected is not None else None,
            "market_identity_state": "verified" if expected is not None else "unsupported"}


def decode_response(payload: dict, directory: dict, kind: str, received_at: datetime,
                    *, board_code: str | None = None) -> dict:
    """Require complete counts, unique identities and exact official directory IDs."""
    if kind not in BOARD_FS or not isinstance(directory.get(kind), dict) or not directory[kind]:
        raise ValueError("官网资金正式板块类别未核实")
    if board_code is not None and board_code not in directory[kind]:
        raise ValueError("板块不属于所请求的正式类别")
    if (not isinstance(payload, dict) or type(payload.get("rc")) is not int or payload["rc"] != 0
            or type(payload.get("full")) is not int or payload["full"] != 1):
        raise ValueError("官网资金来源未返回完整成功响应")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("官网资金数据缺失")
    total, raw_rows = data.get("total"), data.get("diff")
    if (type(total) is not int or not 0 <= total <= MAX_ROWS or not isinstance(raw_rows, list)
            or len(raw_rows) != total):
        raise ValueError("官网资金总量无效、超界或返回不完整")
    seen, rows = set(), []
    for item in raw_rows:
        if not isinstance(item, dict):
            raise ValueError("官网资金行无效")
        code, name, market = item.get("f12"), item.get("f14"), item.get("f13")
        pattern = r"\d{6}" if board_code is not None else r"BK\d{3,4}"
        if (not isinstance(code, str) or not re.fullmatch(pattern, code) or code in seen
                or not isinstance(name, str) or not name.strip() or type(market) is not int
                or market not in ((0, 1) if board_code is not None else (90,))):
            raise ValueError("官网资金身份缺失、类型错误或重复")
        seen.add(code)
        security_identity = _member_identity(code, market) if board_code is not None else {}
        if board_code is None and (code not in directory[kind] or name != directory[kind][code]):
            raise ValueError("官网资金板块身份、类别或名称与正式目录不符")
        row = {"name": name, "main_net_yi": _scaled(_number(item.get("f62")), Decimal(100000000)),
               "main_net_ratio": None, "super_net_yi": None, **_quote_fields(item, received_at)}
        if board_code is None:
            row.update(board_code=code, kind=kind, main_net_5d_yi=None, main_net_10d_yi=None,
                       main_net_20d_yi=None, leader_name=None, leader_symbol=None, streak=None, rank_delta=None)
        else:
            row.update(symbol=code, big_net_yi=None, **security_identity)
        rows.append(row)
    if board_code is None and seen != set(directory[kind]):
        raise ValueError("官网资金未覆盖本次正式板块目录")
    rows.sort(key=lambda row: (row["main_net_yi"] is None, -(row["main_net_yi"] or 0),
                               row.get("board_code") or row["symbol"]))
    if board_code is None:
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
    quote_clocks = [row["quote_source_as_of"] for row in rows if row["quote_source_as_of"]]
    result = {"available": True, "source": "eastmoney_official_funds", "source_product": "东财官网资金",
              "kind": kind, "range": "website", "source_as_of": None, "as_of": None,
              "received_at": received_at.isoformat(), "quality": "stale", "observation_state": "clock_unknown",
              "quote_source_as_of": max(quote_clocks) if quote_clocks else None,
              "coverage_state": "source_complete", "source_total": total, "source_returned": len(rows),
              "directory_total": len(directory[kind]), "coverage": {"total": total, "returned": len(rows), "complete": True},
              "rows": rows, "unit_basis": "f62原单位元；展示亿元（÷100000000），未舍入资金精度",
              "time_basis": "资金源日期及更新时间未提供；报价源钟仅对应证券报价，不能作为资金时点",
              "degraded": ["资金源日期及更新时间未知，不能确认本交易日或实时"]}
    if board_code is None:
        result["total_boards"] = len(rows)
    else:
        result.update(board_code=board_code, name=directory[kind][board_code], delayed=False)
    return result
