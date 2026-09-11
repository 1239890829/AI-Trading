"""仓位决策引擎（选股→验证→持仓→离场→进化 闭环的「持仓」段，2026-09-09 用户指令）。

分工原则：盘中触发（买点卡 / watcher 确认）只回答「这只票到了可上车标准」；
**要不要开、开多大**由本引擎依据当日盘面统一裁定——

- 市场阶段 → 总仓位上限 + 最大同时持仓只数（KB-STOCK-13 强度四档的仓位映射）：
  发酵 65%/2 · 修复 55%/2 · 高潮 45%/2 · 分歧 35%/2 · 退潮 15%/1 · 冰点 10%/1
  （高潮给 45% 而非更高：不追顶；未知阶段保守 30%/1）
- 空仓闸门（gate，与每日精选同一 evaluate_stand_aside 口径）：strong → 0 仓、
  mild → 上限减半、none → 全额
- 角色定个股权重：龙头/空间板 55% · 中军 40% · 其余 30%（KB-STOCK-10）
- 稳定核纪律：最多 2 只（KB-STOCK-19）；确定性非「高」的 confirm 触发不开仓
- **只落模拟盘**（PaperTradingEngine.scope=main）；真实持仓只提醒不下单（L3 红线）

决策全程落 data/position_plans/YYYY-MM-DD.json（当日决策/离场/峰值轨迹），台账化可复盘。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.market.trading_status import beijing_now

log = logging.getLogger(__name__)

#: 市场阶段 → (总仓位上限, 最大同时持仓只数)
PHASE_CAPS: dict[str, tuple[float, int]] = {
    "发酵": (0.65, 2),
    "修复": (0.55, 2),
    "高潮": (0.45, 2),
    "分歧": (0.35, 2),
    "退潮": (0.15, 1),
    "冰点": (0.10, 1),
}
DEFAULT_CAP: tuple[float, int] = (0.30, 1)

#: 角色 → 个股权重（占总资金比例）
ROLE_WEIGHT: dict[str, float] = {"龙头": 0.55, "空间板": 0.55, "中军": 0.40}
DEFAULT_WEIGHT = 0.30

#: 允许自动开仓的触发源（嗅到≠买入：pre_limit 预警本身不开仓——KB-STOCK-07）
OPEN_TRIGGERS = ("buy_point", "confirm")

_PLAN_DIR = Path(__file__).resolve().parents[2] / "data" / "position_plans"


def _plan_path(d: str) -> Path:
    return _PLAN_DIR / f"{d}.json"


def load_plan(d: str | None = None) -> dict:
    d = d or beijing_now().date().isoformat()
    p = _plan_path(d)
    if not p.exists():
        return {"date": d, "decisions": [], "exits": [], "peaks": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  损坏则重建（决策台账允许丢，交易在数据库）
        return {"date": d, "decisions": [], "exits": [], "peaks": {}}


def save_plan(plan: dict) -> None:
    _PLAN_DIR.mkdir(parents=True, exist_ok=True)
    _plan_path(plan["date"]).write_text(
        json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def phase_caps(market_phase: str | None, gate: dict | None) -> tuple[float, int, str]:
    """市场阶段 × 空仓闸门 → (总仓位上限, 最大只数, 说明)。"""
    cap, max_pos = PHASE_CAPS.get(market_phase or "", DEFAULT_CAP)
    note = f"阶段 {market_phase or '未知'}"
    gate = gate or {}
    if gate.get("stand_aside"):
        if gate.get("level") == "strong":
            return 0.0, 0, f"空仓闸门 strong（{note}）——0 仓"
        cap, max_pos = cap / 2, max(1, max_pos - 1)
        note += " · 闸门 mild 减半"
    return cap, max_pos, note


def role_weight(role: str | None) -> float:
    return ROLE_WEIGHT.get(role or "", DEFAULT_WEIGHT)


def _open_positions(engine) -> list[dict]:
    try:
        return [p for p in engine.positions_with_pnl({}) if (p.get("quantity") or 0) > 0]
    except Exception:  # noqa: BLE001
        return []


def _today_gate_and_phase() -> tuple[dict, str | None]:
    """读今日 DailyPickSet.meta 的 gate/market_phase（每日精选管线已算好，直接复用不重算）。"""
    try:
        from sqlalchemy import select

        from app.core.db import get_session_factory
        from app.models.daily_pick import DailyPickSet

        today = beijing_now().date().isoformat()
        with get_session_factory()() as db:
            row = db.execute(
                select(DailyPickSet.meta).where(DailyPickSet.date == today)
            ).scalar_one_or_none()
        if row:
            meta = json.loads(row) if isinstance(row, str) else (row or {})
            return meta.get("gate") or {}, meta.get("market_phase")
    except Exception:  # noqa: BLE001
        pass
    return {}, None


async def maybe_open(
    app,
    *,
    symbol: str,
    name: str,
    trigger: str,
    price: float | None,
    role: str | None = None,
    certainty_level: str | None = None,
) -> dict:
    """触发后裁定是否自动开模拟仓。返回 {opened, reason, qty?, weight?}。全程只动模拟盘。"""
    state = app.state if hasattr(app, "state") else app
    engine = getattr(state, "paper", None)
    if engine is None:
        return {"opened": False, "reason": "paper engine 缺失"}
    if trigger not in OPEN_TRIGGERS:
        return {"opened": False, "reason": f"触发 {trigger} 不在开仓白名单（嗅到≠买入）"}
    if price is None or price <= 0:
        return {"opened": False, "reason": "无有效价格"}

    held = _open_positions(engine)
    if any(p.get("symbol") == symbol for p in held):
        return {"opened": False, "reason": "已持仓"}

    gate, phase = _today_gate_and_phase()
    total_cap, max_pos, cap_note = phase_caps(phase, gate)
    if total_cap <= 0 or max_pos <= 0:
        return {"opened": False, "reason": f"仓位封零：{cap_note}"}
    if len(held) >= max_pos:
        return {"opened": False, "reason": f"持仓只数已满（{len(held)}/{max_pos}，{cap_note}）"}

    # confirm 触发的确定性要求：非买点路径必须 cert=高（宁缺毋滥，KB-TRADE-11）
    if trigger == "confirm" and certainty_level != "高":
        return {"opened": False, "reason": f"confirm 触发但确定性 {certainty_level or '未知'} ≠ 高"}

    # 总敞口约束：现持仓市值 / 初始资金 < 总上限
    try:
        acc = engine.ensure_account()
        initial = float(acc.initial_cash or 1_000_000.0)
        exposure = sum((p.get("last_price") or p.get("cost_price") or 0) * p.get("quantity", 0) for p in held)
        if exposure / initial >= total_cap:
            return {"opened": False, "reason": f"总敞口 {exposure / initial:.0%} 已达上限 {total_cap:.0%}"}
        weight = role_weight(role)
        target_amount = initial * total_cap * weight
        qty = int(target_amount / price / 100) * 100
    except Exception as exc:  # noqa: BLE001
        return {"opened": False, "reason": f"账户读取失败: {exc}"}
    if qty < 100:
        return {"opened": False, "reason": f"目标金额不足一手（{target_amount:.0f} 元 @ {price}）"}

    try:
        order = await engine.place_order(symbol, "buy", price, qty)
    except Exception as exc:  # noqa: BLE001
        return {"opened": False, "reason": f"下单异常: {exc}"}
    if getattr(order, "status", "") == "rejected":
        reason = getattr(order, "reason", "rejected")
        return {"opened": False, "reason": f"撮合拒绝：{reason}"}

    filled = getattr(order, "filled_price", None) or price
    plan = load_plan()
    plan["decisions"].append(
        {
            "ts": beijing_now().strftime("%H:%M:%S"),
            "symbol": symbol, "name": name, "trigger": trigger,
            "action": "open", "qty": qty, "price": filled,
            "weight": weight, "total_cap": total_cap, "role": role,
            "reason": f"{cap_note}；角色 {role or '默认'} 权重 {weight:.0%}；触发 {trigger}",
        }
    )
    save_plan(plan)
    log.info("[仓位引擎] 开模拟仓 %s %s %d 股 @ %s（%s）", symbol, name, qty, filled, cap_note)

    # 通知中心留痕（in-app；飞书矩阵不动——开仓不是 CRITICAL，收盘清算见分晓）
    try:
        from app.picks.morning_brief import append_alert, brief_for_today

        target, _ = brief_for_today()
        append_alert(
            target,
            {
                "kind": "position_open", "symbol": symbol, "name": name,
                "key": f"position-open-{symbol}",
                "direction": "模拟持仓",
                "text": f"自动开模拟仓 {qty} 股 @ {filled}（总仓位上限 {total_cap:.0%}·个股权重 {weight:.0%}·触发 {trigger}）",
                "meta": {"trigger_value": filled},
            },
        )
    except Exception:  # noqa: BLE001
        log.exception("position_open 通知失败")
    return {"opened": True, "qty": qty, "weight": weight, "price": filled, "reason": cap_note}
