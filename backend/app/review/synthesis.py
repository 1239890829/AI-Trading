"""从各维度结论合成改进项（需求 4）。

改进项必须带 `target` 与 `proposed_change`——否则"建议优化情绪参数"这种话
没法落地也没法验证。宁可少提，不提无法执行的。

红线：改进项只能是**参数 / 策略 / 数据 / 流程**层面的，
绝不能是"明天买 X"这类确定性买卖结论。
"""
from __future__ import annotations

import logging
import uuid

from app.review.config import MethodologyConfig
from app.review.schemas import ActionItem, DimensionResult, ReviewData

log = logging.getLogger(__name__)


def _new_id(prefix: str = "AI") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def build_action_items(
    data: ReviewData, dimensions: list[DimensionResult], method: MethodologyConfig
) -> list[ActionItem]:
    """把维度结论转成可落地改进项。"""
    items: list[ActionItem] = []
    blocked_dims = {d.key for d in dimensions if d.status == "blocked"}

    # --- 数据缺失：最高优先级，没有数据一切结论都不可信 ---
    block_gaps = [g for g in data.all_gaps if g.severity == "block"]
    if block_gaps:
        fields = sorted({g.field for g in block_gaps})
        items.append(ActionItem(
            id=_new_id(), title=f"补齐阻断级数据缺失：{', '.join(fields)}",
            category="data", priority="P0",
            expected_impact="恢复被阻断维度的复盘结论；在此之前相关结论不可用",
            evidence="; ".join(f"{g.field}({g.source}): {g.reason}" for g in block_gaps[:3]),
            target=",".join(sorted({g.source for g in block_gaps})),
            proposed_change="检查 provider 健康状态、交易日历可用性与快照服务是否就绪",
        ))

    warn_gaps = [g for g in data.all_gaps if g.severity == "warn"]
    if len(warn_gaps) >= 3:
        items.append(ActionItem(
            id=_new_id(), title=f"处理 {len(warn_gaps)} 处降级级数据缺失",
            category="data", priority="P1",
            expected_impact="提升复盘结论的完整度与可信度",
            evidence="; ".join(f"{g.field}: {g.reason}" for g in warn_gaps[:3]),
            target=",".join(sorted({g.source for g in warn_gaps})[:3]),
            proposed_change="逐项核对数据源链，必要时补备源或调整采集时点",
        ))

    # --- 情绪不可信：优先查日期锚定，而不是调阈值 ---
    sent = data.market.sentiment or {}
    if sent.get("phase_unreliable"):
        items.append(ActionItem(
            id=_new_id(), title="情绪判定被哨兵标记为不可信，需排查日期锚定",
            category="strategy", priority="P0",
            expected_impact="恢复情绪阶段判定的可用性；当前判定不得作为下一交易日依据",
            evidence=f"phase_unreliable=True，self_check={sent.get('self_check')}",
            target="sentiment.engine.compute_sentiment / market.trade_calendar",
            proposed_change="核查 trade_date 与 prev_trade_date 是否严格相差一个交易日，"
                            "重点检查跨日 join 是否存在自指计算",
        ))

    # --- 操作纪律 ---
    t = data.trading
    th = method.thresholds
    if t.trade_count > th.max_daily_trades_for_discipline:
        items.append(ActionItem(
            id=_new_id(), title="当日交易笔数超过纪律上限",
            category="process", priority="P1",
            expected_impact="减少无效交易与手续费损耗",
            evidence=f"当日 {t.trade_count} 笔 > 上限 {th.max_daily_trades_for_discipline} 笔",
            target="review.config.Thresholds.max_daily_trades_for_discipline",
            proposed_change="复核是否为策略信号过于频繁，考虑提高信号触发门槛",
        ))

    rejected = [o for o in t.orders if o.status == "rejected" or
                (o.reason and o.status != "filled")]
    if rejected:
        items.append(ActionItem(
            id=_new_id(), title=f"复核 {len(rejected)} 笔被拒委托",
            category="process", priority="P1",
            expected_impact="区分「规则正确拦截」与「下单参数不合法」，避免误判执行质量",
            evidence="; ".join(f"{o.symbol}:{o.reason}" for o in rejected[:3] if o.reason),
            target="paper.engine.place_order",
            proposed_change="逐笔确认拒绝原因；若参数不合法则修正下单逻辑，"
                            "若规则正确拦截则无需改动",
        ))

    worst_bps = (next((d for d in dimensions if d.key == "trades"), None)
                 or DimensionResult(key="trades", title="")).evidence.get("max_slippage_bps")
    if worst_bps and worst_bps > th.slippage_alert_bps:
        items.append(ActionItem(
            id=_new_id(), title=f"成交滑点 {worst_bps}bp 超告警线",
            category="parameter", priority="P1",
            expected_impact="降低执行成本，提升模拟撮合的真实度",
            evidence=f"最大滑点 {worst_bps}bp > 阈值 {th.slippage_alert_bps}bp",
            target="paper.engine._fill / 撮合滑点参数",
            proposed_change="核对撮合滑点模型是否与实际盘口深度匹配",
        ))

    # --- 仓位与风险 ---
    if t.positions:
        worst = min(t.positions, key=lambda p: (p.pnl_pct if p.pnl_pct is not None else 0))
        if (worst.pnl_pct or 0) < -5:
            items.append(ActionItem(
                id=_new_id(), title=f"{worst.symbol} 浮亏 {worst.pnl_pct}%，复核买入依据",
                category="strategy", priority="P0",
                expected_impact="避免持有已被证伪的头寸",
                evidence=f"成本 {worst.cost_price}，现价 {worst.last_price}，浮亏 {worst.pnl_pct}%",
                target=f"position.{worst.symbol}",
                proposed_change="对照买入时的失效条件逐条检查；"
                                "若已触发失效条件则应执行离场而非继续持有",
            ))

    # --- 市场环境与梯队 ---
    broken = (data.market.theme_summary or {}).get("broken_ladder") or 0
    if broken:
        items.append(ActionItem(
            id=_new_id(), title=f"{broken} 个题材存在梯队断层，避免追其高位股",
            category="strategy", priority="P1",
            expected_impact="降低追高被套概率（断层题材接续风险高）",
            evidence=f"broken_ladder={broken}",
            target="theme_service.echelon_completeness",
            proposed_change="对断层题材的高位股在介入清单中加阻挡项",
        ))

    # --- 维度被阻断：说明方法论配置与实际数据能力不匹配 ---
    for key in sorted(blocked_dims):
        items.append(ActionItem(
            id=_new_id(), title=f"维度「{key}」连续不可用，需调整方法论配置或补数据",
            category="process", priority="P2",
            expected_impact="避免每次复盘都产出空洞维度",
            evidence=f"{key} 维度因数据缺失被阻断",
            target=f"review.methodology.dimensions.{key}.enabled",
            proposed_change="若该数据长期取不到，考虑关闭该维度而不是每次都标 blocked",
        ))

    # P0 白名单：防止噪音维度霸占注意力
    allowed_p0 = set(method.p0_allowed_dimensions)
    for it in items:
        if it.priority == "P0" and it.category not in allowed_p0 and it.category != "data":
            it.priority = "P1"

    order = {"P0": 0, "P1": 1, "P2": 2}
    items.sort(key=lambda x: order.get(x.priority, 9))
    return items
