"""市场宽度指标：涨跌家数、涨跌停家数、两市成交额（full.md §5.2）。

涨跌停阈值与 data_quality/validator.board_limit_pct 保持同一口径：
主板 ±10%、创业板/科创板 ±20%、北交所 ±30%、ST ±5%（新股上市特殊阶段后续由规则配置接管）。
"""
from __future__ import annotations

from datetime import datetime, timezone


def _limit_pct(symbol: str, name: str | None) -> float:
    if symbol.startswith(("300", "301", "688", "689")):
        return 20.0
    if symbol.startswith(("43", "83", "87", "92")):
        return 30.0
    if name and "ST" in name.upper():
        return 5.0
    return 10.0


# 涨跌停判定的浮点容差（百分点）
_LIMIT_TOLERANCE = 0.15


def compute_breadth(rows: list[dict]) -> dict:
    up = down = flat = limit_up = limit_down = 0
    suspended = 0
    total_amount = 0.0
    for r in rows:
        price = r.get("price")
        pct = r.get("change_pct")
        if price is None or price <= 0:
            suspended += 1
            continue
        amount = r.get("amount")
        if amount:
            total_amount += amount
        if pct is None:
            flat += 1
            continue
        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        else:
            flat += 1
        limit = _limit_pct(r["symbol"], r.get("name"))
        # N/C 字头 = 上市初期新股，无涨跌幅限制（首日/注册制前5日），排除涨跌停判定
        name_head = (r.get("name") or " ")[:1]
        if name_head in {"N", "C"}:
            continue
        if pct >= limit - _LIMIT_TOLERANCE:
            limit_up += 1
        elif pct <= -(limit - _LIMIT_TOLERANCE):
            limit_down += 1
    return {
        "total": len(rows),
        "up": up,
        "down": down,
        "flat": flat,
        "suspended": suspended,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "limit_up_ratio": round(limit_up / max(up, 1), 4),
        "total_amount": round(total_amount, 2),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
