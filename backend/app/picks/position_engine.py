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
import os
from pathlib import Path
from app.core.bjtime import beijing_now


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
        # 但**残件必须留档**：原实现直接覆盖重建，事后无从判断是"写坏了"还是"被改坏了"。
        try:
            stamp = beijing_now().strftime("%Y%m%d-%H%M%S")
            p.replace(p.with_name(f"{p.stem}.corrupt-{stamp}.json"))
        except Exception:  # noqa: BLE001  留档失败不该阻断重建
            log.warning("position plan 残件留档失败 %s", p, exc_info=True)
        log.warning("position plan %s 损坏，残件已留档并重建空台账", d, exc_info=True)
        return {"date": d, "decisions": [], "exits": [], "peaks": {}}


def save_plan(plan: dict) -> None:
    """原子写（tmp + os.replace，同目录 rename 在 POSIX 上是原子的）。

    直接 write_text 的窗口期里进程被杀 / 磁盘写满会留下**半截 JSON**，
    而 load_plan 对半截文件只能判为损坏 ⇒ 当日决策台账整体作废。
    与 morning_brief / heat_history / board_flow 同型。
    """
    _PLAN_DIR.mkdir(parents=True, exist_ok=True)
    target = _plan_path(plan["date"])
    tmp = target.with_name(f"{target.name}.tmp")
    tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, target)


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


def _open_positions(engine) -> list[dict] | None:
    """当前持仓（数量 > 0）。**读取失败返回 None，调用方必须拒绝开仓。**

    原实现 `except → []` 把「读不到」伪装成「没有持仓」，后果是两条风控同时失效：
    只数上限（len(held) >= max_pos）恒不触发、总敞口（exposure/initial）恒算成 0。
    风控方向的降级只能 fail-closed——读不到持仓就不许开新仓（撮合/风控是红线硬拦截）。
    """
    try:
        return [p for p in engine.positions_with_pnl({}) if (p.get("quantity") or 0) > 0]
    except Exception:  # noqa: BLE001
        log.warning("仓位引擎：持仓读取失败，拒绝开仓（风控不降级）", exc_info=True)
        return None


def _pending_orders(engine, side: str | None = None) -> list[dict] | None:
    """未成交挂单。**读取失败返回 None，调用方必须拒绝开仓**（风控不降级）。

    R09（2026-09-14）：`pending` 是**真实会发生**的状态——触发侧读的是快照价，
    而 `place_order` 内部会重新取一次实时行情，两个时刻之间只要价格动过，
    限价单就会被挂起（买：限价 < 现价；卖：限价 > 现价）。旧实现只排除
    `rejected`，于是「挂单已受理」被当成「已开仓」：写 decisions、记 log、
    发 position_open 通知；且名额判据只数持仓，未决挂单既不占名额也不计敞口
    ⇒ 同一触发可对同一只票反复挂单，只数上限与总敞口约束同时失效。
    """
    try:
        return list(engine.pending_orders(side))
    except Exception:  # noqa: BLE001
        log.warning("仓位引擎：挂单读取失败，拒绝开仓（风控不降级）", exc_info=True)
        return None


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
    if held is None:
        return {"opened": False, "reason": "持仓读取失败（风控不可信，拒绝开仓）"}
    if any(p.get("symbol") == symbol for p in held):
        return {"opened": False, "reason": "已持仓"}

    pending = _pending_orders(engine, "buy")
    if pending is None:
        return {"opened": False, "reason": "挂单读取失败（风控不可信，拒绝开仓）"}
    dup = next((o for o in pending if o.get("symbol") == symbol), None)
    if dup is not None:
        return {
            "opened": False,
            "reason": f"已挂单未成交（{dup.get('quantity')} 股 @ {dup.get('price')}，等待撮合）",
        }

    gate, phase = _today_gate_and_phase()
    total_cap, max_pos, cap_note = phase_caps(phase, gate)
    if total_cap <= 0 or max_pos <= 0:
        return {"opened": False, "reason": f"仓位封零：{cap_note}"}
    # 名额 = 持仓 + **未决挂单**：挂单已经占用名额与资金，若不计入，同一触发
    # 可在撮合前反复挂出，只数上限形同虚设（R09）。
    held_symbols = {p.get("symbol") for p in held}
    occupied = len(held) + len(
        {o.get("symbol") for o in pending if o.get("symbol") not in held_symbols}
    )
    if occupied >= max_pos:
        return {"opened": False, "reason": f"持仓/挂单只数已满（{occupied}/{max_pos}，{cap_note}）"}

    # confirm 触发的确定性要求：非买点路径必须 cert=高（宁缺毋滥，KB-TRADE-11）
    if trigger == "confirm" and certainty_level != "高":
        return {"opened": False, "reason": f"confirm 触发但确定性 {certainty_level or '未知'} ≠ 高"}

    # 总敞口约束： (现持仓市值 + 买入挂单冻结额) / 初始资金 < 总上限
    # 冻结额必须计入——挂单的钱已经从可用资金里划走、又不在持仓市值内（R01 同源口径），
    # 漏掉它会让「已占用多少仓位」系统性偏低，从而在资金已投出的情况下继续加仓。
    try:
        acc = engine.ensure_account()
        initial = float(acc.initial_cash or 1_000_000.0)
        exposure = sum((p.get("last_price") or p.get("cost_price") or 0) * p.get("quantity", 0) for p in held)
        exposure += float(engine.frozen_cash())
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

    if getattr(order, "status", "") != "filled":
        # 挂单**已受理但未成交**（限价未达现价）——R09：此处旧实现一路按成交处理，
        # 后果是「挂单」被记成「已开仓」并发 position_open 通知（用户以为已建仓）。
        # 台账仍要留痕（本模块契约：决策全程可复盘），但用**可区分的 action**，
        # 使按 `action == "open"` 统计已开仓的消费方不被误导。
        plan = load_plan()
        plan["decisions"].append(
            {
                "ts": beijing_now().strftime("%H:%M:%S"),
                "symbol": symbol, "name": name, "trigger": trigger,
                "action": "open_pending", "qty": qty, "price": price,
                "weight": weight, "total_cap": total_cap, "role": role,
                "order_id": getattr(order, "id", None),
                "reason": f"{cap_note}；限价 {price} 未达现价，挂单已受理、等待撮合",
            }
        )
        save_plan(plan)
        log.info(
            "[仓位引擎] 开仓挂单受理未成交 %s %s %d 股 @ 限价 %s（%s）",
            symbol, name, qty, price, cap_note,
        )
        return {
            "opened": False, "accepted": True, "pending": True,
            "qty": qty, "price": price, "order_id": getattr(order, "id", None),
            "reason": f"挂单受理未成交（限价 {price} 未达现价），等待撮合",
        }

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
