"""市场宽度指标：涨跌家数、涨跌停家数、两市成交额（full.md §5.2）。

涨跌停阈值单点在 app/market/price_rules.limit_pct（2026-09-07 R1 收口，
原「与 validator 保持同一口径」的注释约定已由单一实现取代）：
主板 ±10%（**含 ST**，2026-07-06 并轨）、创业板/科创板 ±20%、北交所 ±30%。
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.market import price_rules


def _limit_pct(symbol: str, name: str | None) -> float:
    # 2026-09-07 R1 收口：判定逻辑单点化到 price_rules.limit_pct，本文件
    # 只保留别名（breadth 内部多处调用 + 容差/豁免是本模块自有语义）。
    return price_rules.limit_pct(symbol, name)


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
        # 5% 限制，实际却跌了 9.574%——当时归因为「口径失配」；**2026-09-11
        # 复盘更正：真因是 ST 口径过期**（主板 ST 已于 2026-07-06 放宽至 10%，
        # 9.574% 在新规下完全合法，本应计入跌停）。双边判定仍要保留——除权/
        # 复牌/N 字头等场景的限价失配是真实存在的。
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
