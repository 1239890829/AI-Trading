"""每日精选执行闸门（picks-intraday-fusion-assessment.md §3.1，P0-A）。

数据证实的结论（近 60 交易日 4983 涨停股样本，docs/picks-intraday-fusion-assessment §2.2）：
- 开盘溢价 gap ≥ 9.5%（一字/近一字）：T+1 均值 -2.09%、胜率仅 12%——**禁买**；
- gap ∈ 5~9.5%：均值 -1.28%（均值被大亏样本拖垮）——**降级观察**；
- gap ≤ -5%：单票异常（除权/利空嫌疑）——**标注复核**，不自动买；
- 其余：可执行，按 picks 原定计划。

**板块映射（2026-09-13 条件化审计 A1，§6.25）**：上述阈值按**主板 10% 制度**标定。
8.5 年分层实测（昨日涨停股，开盘买→次日卖，marketdb）：
- 主板：gap 5~9.5% 追入 -1.54%/胜率 39.8%（观察语义成立）；≥9.5% 为一字买不进（block 语义成立）；
- 创业/科创 20%：gap 10~19%（可成交半程高开）追入 **+0.02%/胜率 44.5%（零期望，非负）**
  ——固定 9.5% 会把这一档错标成「严重负期望」；≥19% 才买不进。
⇒ 阈值按板块制度**比例映射**：block = 0.95×limit_pct、observe = 0.5×limit_pct
（主板 10% → 9.5/5.0 与原值完全一致，历史行为不变；20cm → 19/10；北交 30 → 28.5/15）。
单点口径走 ``app.market.price_rules.limit_pct``。

9:25 竞价结束（stage=final）即知，不需要等盘中——这是盘中数据对盘后决策
唯一合法的修改点（§1.2 纠偏：盘中不改选股，只管执行）。

三态纪律（沿用 auction_premium）：竞价数据缺失 = unknown，绝不冒充 0
或把缺失票当"可买"放行——执行闸门放行的必须是**显式判定**。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select

log = logging.getLogger(__name__)

STATE_BLOCKED = "blocked"      # gap ≥ 9.5%：一字/超高开，禁买
STATE_OBSERVE = "observe"      # gap ∈ 5~9.5%：降级观察
STATE_NORMAL = "normal"        # gap ∈ -5~5%：可执行
STATE_ANOMALY = "anomaly"      # gap ≤ -5%：异常低开（除权/利空嫌疑），复核
STATE_UNKNOWN = "unknown"      # 竞价数据缺失：判不出，不放行

#: 阈值默认值（与 config.settings 同名字段互为备份；settings 优先）
BLOCK_GAP_PCT = 9.5
OBSERVE_GAP_PCT = 5.0
ANOMALY_GAP_PCT = -5.0


def thresholds_for(symbol: str, name: str | None = None) -> tuple[float, float]:
    """按板块制度的 (block_ge, observe_ge)——主板 10% 制度下与旧固定值逐字一致。

    条件化审计 A1（§6.25）：统一 9.5% 对 20cm 股把「可成交半程高开（实测零期望）」
    错标成「严重负期望」，且 19% 以下并未一字（买得进）——阈值必须随制度映射。
    """
    from app.market.price_rules import limit_pct as _rules_limit_pct

    limit = _rules_limit_pct(symbol, name)
    return (round(limit * 0.95, 2), round(limit * 0.5, 2))


def classify_execution(
    gap_pct: float | None,
    *,
    block_ge: float = BLOCK_GAP_PCT,
    observe_ge: float = OBSERVE_GAP_PCT,
    anomaly_le: float = ANOMALY_GAP_PCT,
) -> dict:
    """单股执行三态判定。None → unknown（缺失 ≠ 平开）。"""
    if gap_pct is None:
        return {"state": STATE_UNKNOWN, "gap_pct": None, "reason": "竞价数据缺失，判不出"}
    if gap_pct >= block_ge:
        return {"state": STATE_BLOCKED, "gap_pct": gap_pct,
                "reason": f"开盘溢价 {gap_pct:+.2f}% ≥ {block_ge}%（一字/超高开，历史胜率 12%）"}
    if gap_pct >= observe_ge:
        return {"state": STATE_OBSERVE, "gap_pct": gap_pct,
                "reason": f"开盘溢价 {gap_pct:+.2f}% ∈ {observe_ge}~{block_ge}%（期望偏负）"}
    if gap_pct <= anomaly_le:
        return {"state": STATE_ANOMALY, "gap_pct": gap_pct,
                "reason": f"开盘 {gap_pct:+.2f}% ≤ {anomaly_le}%（异常低开，复核除权/利空）"}
    return {"state": STATE_NORMAL, "gap_pct": gap_pct, "reason": f"开盘溢价 {gap_pct:+.2f}%（正常区间）"}


async def collect_execution_gate(
    hub,
    session_factory,
    *,
    pick_date: str | None = None,
    block_ge: float = BLOCK_GAP_PCT,
    observe_ge: float = OBSERVE_GAP_PCT,
    anomaly_le: float = ANOMALY_GAP_PCT,
) -> dict:
    """最新组合（默认）成员的执行闸门判定。

    :param pick_date: 指定组合日期（YYYY-MM-DD）；None = 最新一份。
    :return: {pick_date, items, summary, caveats}——任何失败折进 caveats，
             绝不抛出（闸门缺一面 ≠ 端点不可用）。
    """
    from app.models.daily_pick import DailyPickSet

    caveats: list[str] = []
    with session_factory() as db:
        stmt = select(DailyPickSet)
        if pick_date:
            stmt = stmt.where(DailyPickSet.date == pick_date)
        row = db.execute(stmt.order_by(DailyPickSet.date.desc()).limit(1)).scalar_one_or_none()
    if row is None:
        return {"pick_date": None, "items": [], "summary": None,
                "caveats": ["无每日精选组合，闸门无可判对象"]}

    try:
        items_raw = json.loads(row.items or "[]")
    except Exception:
        items_raw = []
    if not items_raw:
        return {"pick_date": row.date, "items": [], "summary": None,
                "caveats": [f"组合 {row.date} 为空"]}

    symbols = [i.get("symbol") for i in items_raw if i.get("symbol")]
    auction_by_sym: dict[str, dict] = {}
    try:
        rows = await hub.provider.get_auction_snapshot(symbols, stage="final")
        auction_by_sym = {r["symbol"]: r for r in (rows or []) if r.get("symbol")}
    except Exception as exc:  # noqa: BLE001
        caveats.append(f"竞价快照拉取失败：{exc}")

    items: list[dict] = []
    for it in items_raw:
        sym = it.get("symbol")
        auc = auction_by_sym.get(sym) or {}
        # 缺失票绝不冒充 0：data_status 非 ready/final 或字段缺失都归 unknown
        pct = auc.get("auction_pct")
        if auc and auc.get("data_status") not in (None, "ready", "final"):
            pct = None
        # 条件化审计 A1（§6.25）：按板块制度映射阈值；显式入参（block_ge 等被
        # 调用方覆盖）时优先入参，未覆盖的维度按 symbol 制度派生
        sym_block, sym_observe = thresholds_for(sym, it.get("name"))
        if block_ge == BLOCK_GAP_PCT:
            block_ge_i = sym_block
        else:
            block_ge_i = block_ge
        if observe_ge == OBSERVE_GAP_PCT:
            observe_ge_i = sym_observe
        else:
            observe_ge_i = observe_ge
        verdict = classify_execution(pct, block_ge=block_ge_i, observe_ge=observe_ge_i, anomaly_le=anomaly_le)
        items.append({
            "symbol": sym,
            "name": it.get("name"),
            "score": it.get("score"),
            "observation_only": bool(it.get("observation_only")),
            # 空仓闸门三态（审查 §4.2）：blocked/observe/followable；非闸门日缺省
            "follow_state": it.get("follow_state"),
            "state": verdict["state"],
            "gap_pct": verdict["gap_pct"],
            "reason": verdict["reason"],
        })

    from collections import Counter
    dist = Counter(i["state"] for i in items)
    summary = {
        "total": len(items),
        "blocked": dist.get(STATE_BLOCKED, 0),
        "observe": dist.get(STATE_OBSERVE, 0),
        "normal": dist.get(STATE_NORMAL, 0),
        "anomaly": dist.get(STATE_ANOMALY, 0),
        "unknown": dist.get(STATE_UNKNOWN, 0),
        "executable": dist.get(STATE_NORMAL, 0),
    }
    return {"pick_date": row.date, "items": items, "summary": summary, "caveats": caveats}
