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
    up = down = flat = limit_up = limit_down = limit_anomaly = 0
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
        # 双边判定：涨跌停是「价格落在限价上」，不是「比限价更极端」。
        #
        # 单边写法（pct >= limit-tol / pct <= -(limit-tol)）会把「限价口径失配」
        # 静默吃成涨跌停。2026-08-28 实测：`*ST萃华` 被 `_limit_pct` 按名称判为
        # 5% 限制，实际却跌了 9.574%——5% 限制下这根本不可能发生，说明该股的
        # 限价口径与名称不符，但单边判定把它算成了跌停，且不报错、不留痕。
        # 超出限价的单独计 `limit_anomaly`，既不冒充涨跌停，也不悄悄丢掉。
        if (limit - _LIMIT_TOLERANCE) <= pct <= (limit + _LIMIT_TOLERANCE):
            limit_up += 1
        elif -(limit + _LIMIT_TOLERANCE) <= pct <= -(limit - _LIMIT_TOLERANCE):
            limit_down += 1
        elif pct > (limit + _LIMIT_TOLERANCE) or pct < -(limit + _LIMIT_TOLERANCE):
            limit_anomaly += 1
    return {
        "total": len(rows),
        "up": up,
        "down": down,
        "flat": flat,
        "suspended": suspended,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "limit_anomaly": limit_anomaly,
        "limit_up_ratio": round(limit_up / max(up, 1), 4),
        "total_amount": round(total_amount, 2),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
