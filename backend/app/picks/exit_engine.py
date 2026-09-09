"""持仓监护与离场引擎（选股→验证→持仓→离场 闭环的「离场」段，2026-09-09 用户指令）。

对每只**开仓中**的标的按 tick（交易时段 15s）评估，规则全部确定性可解释：

- **连板持有**：当日已封板（板性阈值判定）→ 绝不早止盈（用户：避免过早止盈错失连板趋势）
- **硬止损**：现价 ≤ 成本×(1-止损幅)。角色分档：龙头/空间板 8% · 中军 6% · 其余 5.5%
  （与 risk.stop_loss_reference 同思想，此处按角色简化）
- **移动止盈**：峰值收益达标后从峰值回撤超容忍 → 离场。龙头/空间板（+15% 武装，回撤 8%）
  · 中军（+10%，6%）· 其余（+10%，5.5%）——趋势票给足呼吸空间（避免死扛与卖飞的双向错）
- **弱转强识别（二浪）**：持仓 ≥1 夜 + 昨收低于成本（走弱日）+ 今日拉升 ≥4% →
  「二浪启动」提醒**持有不动**（用户：识别走弱后第二三天突然转强，不停留在选股阶段）
- 峰值轨迹持久化在 position plan JSON（重启安全）

动作分流：**模拟持仓自动卖出**（撮合引擎 T+1/整手硬拦截兜底）；**真实持仓只发
CRITICAL 提醒**（推送矩阵修订：真实持仓离场提醒与买点卡同级——等不起日报），
用户确认卖出后真实持仓流水删除，标签随之自动消失（标签=持仓状态的派生）。
"""

from __future__ import annotations

import asyncio
import logging

from app.market.trading_status import beijing_now
from app.picks.position_engine import load_plan, save_plan
from app.picks.pre_limit_radar import board_limit_pct, is_sealed

log = logging.getLogger(__name__)

ROLE_STOP: dict[str, float] = {"龙头": 0.08, "空间板": 0.08, "中军": 0.06}
DEFAULT_STOP = 0.055
#: 角色 → (武装峰值收益, 峰值回撤容忍)
ROLE_TRAIL: dict[str, tuple[float, float]] = {
    "龙头": (0.15, 0.08), "空间板": (0.15, 0.08), "中军": (0.10, 0.06),
}
DEFAULT_TRAIL = (0.10, 0.055)
#: 弱转强（二浪）当日拉升阈值
WAVE_STRENGTH_PCT = 4.0

#: 已提醒去重（进程内；日内同股同信号只发一次）
_NOTIFIED: set[str] = set()


def _role_of(plan: dict, symbol: str) -> str | None:
    for d in reversed(plan.get("decisions", [])):
        if d.get("symbol") == symbol and d.get("action") == "open":
            return d.get("role")
    return None


def _stop_pct(role: str | None) -> float:
    return ROLE_STOP.get(role or "", DEFAULT_STOP)


def _trail(role: str | None) -> tuple[float, float]:
    return ROLE_TRAIL.get(role or "", DEFAULT_TRAIL)


def trailing_rule(cost: float, price: float, peak: float, role: str | None) -> dict | None:
    """移动止盈纯函数：峰值收益武装后从峰值回撤超容忍 → 离场。"""
    if not cost or cost <= 0 or not peak:
        return None
    armed, trail = _trail(role)
    if peak >= cost * (1 + armed) and price <= peak * (1 - trail):
        return {"action": "exit",
                "reason": f"移动止盈：峰值 {peak:.2f}（+{peak / cost - 1:.0%}）回落至 {price:.2f}（-{1 - price / peak:.1%}）"}
    return None


def _notify(app, symbol: str, name: str, kind: str, text: str, *, critical: bool = False) -> None:
    """通知中心 + （critical 时）飞书。失败只记日志。"""
    try:
        from app.picks.morning_brief import append_alert, brief_for_today

        target, _ = brief_for_today()
        append_alert(target, {"kind": kind, "symbol": symbol, "name": name,
                              "direction": "持仓监护", "text": text, "meta": {}})
    except Exception:  # noqa: BLE001
        log.exception("持仓通知 append 失败")
    if not critical:
        return
    try:
        import threading

        from app.notifiers import get_notifier_registry
        from app.services.push_policy import PolicyKind, feishu_allowed

        if not feishu_allowed(PolicyKind.CRITICAL):
            return
        notifier = get_notifier_registry().get("feishu")
        send = getattr(notifier, "send_interactive", None) or getattr(notifier, "send_text", None)
        if send is None:
            return
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"**{symbol} {name}**\n{text}"}}],
        }
        threading.Thread(
            target=lambda: asyncio.run(send(card)),
            name="real-exit-feishu", daemon=True,
        ).start()
    except Exception:  # noqa: BLE001
        log.exception("真实持仓离场飞书提醒失败")


def _real_positions() -> dict[str, dict]:
    """真实持仓（RealTrade 净额聚合）：symbol → {cost, quantity}（加权成本）。"""
    try:
        from sqlalchemy import select

        from app.core.db import get_session_factory
        from app.models.real_position import RealTrade

        with get_session_factory()() as db:
            trades = db.execute(select(RealTrade)).scalars().all()
        net: dict[str, dict] = {}
        for t in trades:
            cur = net.setdefault(t.symbol, {"quantity": 0, "cost_sum": 0.0, "name": t.name or ""})
            if t.side == "buy":
                cur["quantity"] += t.quantity
                cur["cost_sum"] += t.fill_price * t.quantity
            else:
                cur["quantity"] -= t.quantity
                cur["cost_sum"] -= t.fill_price * t.quantity
        return {
            s: {"quantity": v["quantity"], "cost": v["cost_sum"] / v["quantity"], "name": v["name"]}
            for s, v in net.items() if v["quantity"] > 0
        }
    except Exception:  # noqa: BLE001
        return {}


def _rule_for(pos: dict, price: float, pct: float | None, prev_close: float | None, role: str | None, today: str) -> dict | None:
    """单持仓单 tick 的规则裁定 → {action: hold|exit|wave, reason} 或 None（无信号）。

    pos: {symbol, quantity, available, cost_price, buy_date}
    """
    cost = pos["cost_price"]
    if not cost or cost <= 0:
        return None
    limit = board_limit_pct(pos["symbol"])
    # 1) 连板持有：封板状态不做任何离场判断（避免过早止盈）
    if pct is not None and is_sealed(pct, limit):
        return {"action": "hold", "reason": f"封板 {pct:.1f}%——连板趋势持有"}

    # 2) 硬止损
    if price <= cost * (1 - _stop_pct(role)):
        return {"action": "exit", "reason": f"止损：{price} ≤ 成本 {cost:.2f}×(1-{_stop_pct(role):.1%})"}

    # 4) 弱转强（二浪）：昨收在成本下（走弱日）+ 今日拉升达标 + 非买入当日
    if (
        pos.get("buy_date") and pos["buy_date"] < today
        and prev_close is not None and prev_close < cost
        and pct is not None and pct >= WAVE_STRENGTH_PCT
    ):
        return {"action": "wave", "reason": f"二浪启动：昨收 {prev_close:.2f} 低于成本 {cost:.2f}（走弱日），今日 +{pct:.1f}% 转强——持有观察"}

    return None


async def evaluate_once(app) -> list[dict]:
    """监护一轮：模拟持仓逐只裁定 → 离场/提醒；真实持仓同规则只提醒。返回信号列表。"""
    state = app.state if hasattr(app, "state") else app
    engine = getattr(state, "paper", None)
    if engine is None:
        return []
    snap_rows = getattr(getattr(state, "snapshot_service", None), "snapshot", None) or []
    snap = {r.get("symbol"): r for r in snap_rows if r.get("symbol")}
    today = beijing_now().date().isoformat()
    plan = load_plan()
    peaks: dict = plan.setdefault("peaks", {})
    fired: list[dict] = []

    # ---------- 模拟持仓 ----------
    try:
        from app.models.paper import PaperPosition

        with engine._sf() as db:
            rows = db.query(PaperPosition).filter(
                PaperPosition.scope == engine.scope, PaperPosition.quantity > 0
            ).all()
        positions = [
            {"symbol": p.symbol, "quantity": p.quantity, "available": p.available,
             "cost_price": p.cost_price, "buy_date": p.buy_date}
            for p in rows
        ]
    except Exception:  # noqa: BLE001
        positions = []

    for pos in positions:
        sym = pos["symbol"]
        q = snap.get(sym) or {}
        price = q.get("price")
        if price is None or price <= 0:
            continue
        pct = q.get("change_pct")
        prev_close = round(price / (1 + pct / 100), 3) if (pct is not None and pct > -99) else None
        role = _role_of(plan, sym)
        name = str(q.get("name") or "")

        peak = max(float(peaks.get(sym) or price), price)
        peaks[sym] = peak

        rule = _rule_for(pos, price, pct, prev_close, role, today)
        # 移动止盈（需要峰值轨迹，峰值在 plan.peaks 持久化维护）
        if rule is None:
            rule = trailing_rule(pos["cost_price"], price, peak, role)

        if rule is None:
            continue
        action, reason = rule["action"], rule["reason"]
        key = f"{sym}:{action}:{reason[:12]}"
        if action == "wave":
            if key in _NOTIFIED:
                continue
            _NOTIFIED.add(key)
            _notify(app, sym, name, "position_wave", f"{reason}（持仓监护·持有）")
            fired.append({"symbol": sym, "action": "wave", "reason": reason})
            continue
        if action != "exit":
            continue

        if pos["available"] <= 0:
            reason += "（T+1 当日买入不可卖——下一交易日自动执行）"
            if key in _NOTIFIED:
                continue
            _NOTIFIED.add(key)
            _notify(app, sym, name, "position_exit", reason)
            fired.append({"symbol": sym, "action": "exit_deferred", "reason": reason})
            continue
        try:
            order = await engine.place_order(sym, "sell", price, pos["available"])
        except Exception as exc:  # noqa: BLE001
            log.exception("自动离场下单失败 %s: %s", sym, exc)
            continue
        if getattr(order, "status", "") == "rejected":
            log.warning("自动离场被撮合拒绝 %s: %s", sym, getattr(order, "reason", ""))
            continue
        plan["exits"].append({"ts": beijing_now().strftime("%H:%M:%S"), "symbol": sym,
                              "reason": reason, "qty": pos["available"], "price": price})
        peaks.pop(sym, None)
        save_plan(plan)
        _notify(app, sym, name, "position_exit", f"自动离场：{reason}")
        fired.append({"symbol": sym, "action": "exit", "reason": reason})
        log.warning("[离场引擎] 模拟仓自动卖出 %s %d 股 @ %s（%s）", sym, pos["available"], price, reason)

    # ---------- 真实持仓（只提醒，CRITICAL 级） ----------
    for sym, rp in _real_positions().items():
        q = snap.get(sym) or {}
        price = q.get("price")
        if price is None or price <= 0:
            continue
        pct = q.get("change_pct")
        cost = rp["cost"]
        stop = _stop_pct(None)
        if price <= cost * (1 - stop):
            key = f"real:{sym}:stop"
            if key not in _NOTIFIED:
                _NOTIFIED.add(key)
                text = f"真实持仓止损提醒：现价 {price} 已低于成本 {cost:.2f} 的 -{stop:.0%} 线——请确认是否卖出（确认后删除持仓流水，标签自动消失）"
                _notify(app, sym, rp.get("name") or "", "real_exit_alert", text, critical=True)
                fired.append({"symbol": sym, "action": "real_alert", "reason": text})
        elif pct is not None and is_sealed(pct, board_limit_pct(sym)):
            peaks[sym] = max(float(peaks.get(sym) or price), price)

    save_plan(plan)
    return fired


async def position_loop(app, stop: asyncio.Event) -> None:
    """持仓监护常驻循环：交易时段 15s 一轮（与临板雷达同窗口判定）。"""
    from app.picks.pre_limit_radar import radar_active_now

    log.info("position monitor loop started")
    while not stop.is_set():
        try:
            if radar_active_now():
                await evaluate_once(app)
                await asyncio.sleep(15)
            else:
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            log.exception("position monitor loop failed")
            await asyncio.sleep(30)
