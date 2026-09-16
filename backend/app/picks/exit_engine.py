"""持仓监护与离场引擎（选股→验证→持仓→离场 闭环的「离场」段，2026-09-09 用户指令）。

对每只**开仓中**的标的按 tick（交易时段 15s）评估，规则全部确定性可解释：

- **连板持有**：当日已封板（板性阈值判定）→ 绝不早止盈（用户：避免过早止盈错失连板趋势）
- **硬止损**：现价 ≤ 成本×(1-止损幅)。角色分档：龙头/空间板 8% · 中军 6% · 其余 5.5%
  （与 risk.stop_loss_reference 同思想，此处按角色简化）
- **移动止盈**：峰值收益达标后从峰值回撤超容忍 → 离场。龙头/空间板（+15% 武装，回撤 8%）
  · 中军（+10%，6%）· 其余（+10%，5.5%）——趋势票给足呼吸空间（避免死扛与卖飞的双向错）
- **弱转强识别（二浪）**：持仓 ≥1 夜 + 昨收低于成本（走弱日）+ 今日拉升 ≥4% →
  「二浪启动」提醒**持有不动**（用户：识别走弱后第二三天突然转强，不停留在选股阶段）
- **止盈档位（P0-1，2026-09-10 落地）**：日内冲高 ≥ 阈值（默认 3%，env 可调）→
  提醒减退。**封板不触发**（与「连板持有」一致，避免过早止盈错失连板趋势）；
  标的池 = 实际持仓 ∪ 当日每日精选组合（去重、上限 10）；按持仓状态分流文案
  （持仓 = 减半仓纪律建议；未持仓 = 仅记录，**不给卖出指令**——守「不输出确定性
  买卖结论」红线）。去重 key `take-profit:{trade_date}:{symbol}` = 当日一次。
- 峰值轨迹持久化在 position plan JSON（重启安全）

动作分流：**模拟持仓自动卖出**（撮合引擎 T+1/整手硬拦截兜底）；**真实持仓只发
CRITICAL 提醒**（推送矩阵修订：真实持仓离场提醒与买点卡同级——等不起日报），
用户确认卖出后真实持仓流水删除，标签随之自动消失（标签=持仓状态的派生）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from app.picks.position_engine import load_plan, save_plan
from app.picks.pre_limit_radar import board_limit_pct, is_sealed
from app.core.bjtime import beijing_now

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

#: 止盈档位默认阈值（%）——env `ASHARE_TAKE_PROFIT_PCT` 可调
TAKE_PROFIT_PCT_DEFAULT = 3.0
#: 止盈标的池上限（持仓 ∪ 当日精选组合，去重后截断；持仓优先）
TAKE_PROFIT_POOL_CAP = 10

#: 已提醒去重（进程内；日内同股同信号只发一次）
_NOTIFIED: set[str] = set()


def take_profit_pct() -> float:
    """止盈阈值（%）。env 非法/非正 → 回退默认并记日志（不静默改口径）。"""
    raw = os.environ.get("ASHARE_TAKE_PROFIT_PCT")
    if raw is None or str(raw).strip() == "":
        return TAKE_PROFIT_PCT_DEFAULT
    try:
        val = float(raw)
    except (TypeError, ValueError):
        log.warning("ASHARE_TAKE_PROFIT_PCT 非法（%r），回退默认 %.1f%%", raw, TAKE_PROFIT_PCT_DEFAULT)
        return TAKE_PROFIT_PCT_DEFAULT
    if val <= 0:
        log.warning("ASHARE_TAKE_PROFIT_PCT 非正（%r），回退默认 %.1f%%", raw, TAKE_PROFIT_PCT_DEFAULT)
        return TAKE_PROFIT_PCT_DEFAULT
    return val


def take_profit_rule(pct: float | None, *, sealed: bool, held: bool) -> dict | None:
    """止盈档位纯函数：日内冲高 ≥ 阈值 → 返回提醒（None = 不触发）。

    三条口径（与设计稿一致，勿擅改）：
    1. **封板不触发**——与 `_rule_for` 的「连板持有」同源：封板日绝不提示过早止盈。
    2. 触发口径 = 当日涨跌幅（相对昨收），**直取快照 `change_pct` 字段**，不自算避免误差。
    3. 文案按持仓状态分流：持仓给「减半仓」纪律建议；未持仓**只记录**、不给买卖指令。
    """
    if sealed or pct is None:
        return None
    thr = take_profit_pct()
    if pct < thr:
        return None
    hint = "已持仓 → 纪律建议：减半仓落袋" if held else "未持仓 → 仅记录，不作为买入依据"
    return {
        "action": "take_profit",
        "pct": pct,
        "threshold": thr,
        "held": held,
        "reason": f"日内冲高 +{pct:.1f}%（阈值 +{thr:.1f}%）：{hint}",
    }


def _picks_combos() -> dict[str, str]:
    """当日每日精选组合 symbol → name；当日无行时回退最近一日。

    取不到（DB 不可用 / 从未生成）**如实返回空**，调用方降级为「仅持仓」——
    绝不拿昨日组合冒充今日（与三态纪律一致）。
    """
    try:
        from sqlalchemy import select

        from app.core.db import get_session_factory
        from app.models.daily_pick import DailyPickSet

        today = beijing_now().date().isoformat()
        with get_session_factory()() as db:
            row = db.execute(
                select(DailyPickSet).where(DailyPickSet.date == today)
            ).scalar_one_or_none()
            if row is None:
                return {}
        items = json.loads(row.items or "[]")
        return {
            str(i.get("symbol")): str(i.get("name") or "")
            for i in items
            if i.get("symbol")
        }
    except Exception:  # noqa: BLE001
        log.exception("止盈档位：精选组合读取失败（本轮降级为仅持仓）")
        return {}


def _role_of(plan: dict, symbol: str) -> str | None:
    """该标的的开仓角色（决定止损/回撤档位）。

    `open_pending` 一并认：开仓挂单受理时的角色意图与成交后应走的档位是同一个，
    挂单成交后（match_pending）不会再补写 `action="open"` 记录——若此处不认，
    这类持仓会静默掉到默认档位（止损放宽），而它恰恰是最初被触发的那批标的。
    """
    for d in reversed(plan.get("decisions", [])):
        if d.get("symbol") == symbol and d.get("action") in ("open", "open_pending"):
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


def _notify(app, symbol: str, name: str, kind: str, text: str, *, critical: bool = False,
            key: str | None = None) -> None:
    """当日简报 alerts[] + （critical 时）飞书。失败只记日志。

    落点不是通知中心（`IMP-028` 后该中心只收 `__picks_buy_point__` 买点）：
    持仓监护提醒进猎场页「盘中提醒」，`AlertEvent` 可在控制台「提醒与告警」追查
    （2026-09-16 `IMP-034` 订正原「通知中心 +」措辞）。

    key 传入时作为落盘去重键（跨重启生效）；不传则由 append_alert 兜底生成。
    """
    try:
        from app.picks.morning_brief import append_alert, brief_for_today

        target, _ = brief_for_today()
        alert = {"kind": kind, "symbol": symbol, "name": name,
                 "direction": "持仓监护", "text": text, "meta": {}}
        if key:
            alert["key"] = key
        append_alert(target, alert)
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


#: 真实持仓读取状态（S1-2，2026-09-11）。**三态**，键形状对齐规划中的 Freshness 契约
#: （state/as_of/age_seconds/reason/source），便于后续阶段 1 统一时直接归并。
#: 历史缺陷：`except Exception: return {}` 把「读失败」与「确实无持仓」压成同一个返回值，
#: 且**连日志都没有** ⇒ 一次 SQLite 锁超时就让整轮真实持仓止损检查静默跳过，
#: 用户既收不到止损提醒、也无从知道监护停摆。
_REAL_READ: dict = {
    "state": "unknown",  # unknown | ok | empty | failed
    "as_of": None,
    "age_seconds": None,
    "reason": None,
    "source": "real_position",
    "failures": 0,
}


def real_position_read_state() -> dict:
    """真实持仓读取状态（只读快照）。供数据健康哨兵/健康端点观测，**不参与交易决策**。"""
    return dict(_REAL_READ)


#: 模拟持仓读取状态（S1-2 同族）：读失败 ⇒ 自动离场/硬止损整轮跳过。
_PAPER_READ: dict = {
    "state": "unknown",  # unknown | ok | empty | failed
    "as_of": None,
    "age_seconds": None,
    "reason": None,
    "source": "paper_position",
    "failures": 0,
}


def position_monitor_state() -> dict:
    """持仓监护两路读取状态的合并快照（数据健康哨兵单点消费）。"""
    return {"paper": dict(_PAPER_READ), "real": dict(_REAL_READ)}


def _real_positions() -> dict[str, dict]:
    """真实持仓（**与持仓页面同一事实视图**）：symbol → {quantity, cost, name, overridden}。

    `cost` 约定：正常情况下是正的加权摊薄成本；**成本不可用时为 `None`**
    （只可能来自人工覆盖把总成本记成 0/负）——消费方**必须先判空**再算止损，
    详见下方 R07 注释。

    返回值 `{}` 有两种含义，**必须靠 `_REAL_READ` 区分**（S1-2）：
    - `state=empty`：确实无持仓（正常，无需提醒）
    - `state=failed`：读取失败 ⇒ **本轮真实持仓止损检查已跳过**（降级，必须可见）

    R07（2026-09-14）：本函数**曾另写一份"净现金投入"成本算法**，与
    `real_position_service.aggregate_positions`（页面/API 用的摊薄成本）口径不一致，
    且**完全不读 `RealPositionOverride`**。合成账本「买 1000 股 @10、卖 400 股 @12」下
    页面剩余成本 10.00、监护算 8.67（净现金投入 5200/600）；登记人工覆盖
    （200 股 / 成本 11）后监护仍看 600 股 / 8.67 ⇒ **止损提醒线与实际持仓对不上**，
    且部分卖出后提醒线被自己"算低"，该提醒的时候不提醒。

    现改为直接复用 `load_positions()`：口径只有一个（费用/成本结转/人工覆盖
    全部跟随该实现演进，单一真相源），本函数只做**形状适配 + 三态记账**。
    刻意不在这里"就地修正"成本——第二份算法的存在本身就是缺陷。
    """
    try:
        from app.core.db import get_session_factory
        from app.services.real_position_service import load_positions

        held = load_positions(get_session_factory())
        out: dict[str, dict] = {}
        unfunded: list[str] = []
        for p in held:
            if p.quantity <= 0:
                continue  # 已清仓：页面仍展示其已实现盈亏，监护无事可做
            entry = {
                "quantity": p.quantity,
                "cost": p.avg_cost,
                "name": p.name or "",
                "overridden": p.overridden,
            }
            if p.avg_cost is None or p.avg_cost <= 0:
                # ⚠️ 后果是**静默漏报**而非误报：按 0 计算时判据是 `price <= 0`，
                # 正价格恒不成立 ⇒ 该持仓**永远不会触发止损**，且外面看不出来。
                # 故显式置 cost=None 并留痕，交由消费方跳过；**不臆造**一个成本。
                entry["cost"] = None
                unfunded.append(p.symbol)
            out[p.symbol] = entry
        if unfunded:
            log.warning(
                "真实持仓缺少有效成本，止损判定跳过（不会触发提醒）：%s"
                "——请在持仓页修正人工覆盖的成本",
                ", ".join(unfunded[:10]),
            )
        _REAL_READ.update(state="ok" if out else "empty", as_of=beijing_now(),
                          age_seconds=0.0, reason=None, failures=0)
        return out
    except Exception as exc:  # noqa: BLE001
        _REAL_READ.update(
            state="failed", as_of=beijing_now(), age_seconds=0.0,
            reason=f"{type(exc).__name__}: {exc}"[:200],
            failures=int(_REAL_READ.get("failures") or 0) + 1,
        )
        log.exception(
            "真实持仓读取失败——本轮真实持仓止损检查已跳过（连续 %d 次）：%s",
            _REAL_READ["failures"], _REAL_READ["reason"],
        )
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
    #: symbol → name（模拟 + 真实持仓），供止盈档位区分「持仓 / 未持仓」
    held_names: dict[str, str] = {}

    # ---------- 模拟持仓 ----------
    # S1-2 同族：读失败与「确实无持仓」必须可区分——否则一次 DB 抖动就让
    # **模拟仓自动离场（含硬止损）整轮跳过**，而 failed 与 empty 返回值完全相同。
    try:
        # R04（2026-09-14）：**先结算 T+1，再读持仓**。
        # `available = quantity - frozen_today` 是派生值，而解冻此前只在下单路径发生
        # ⇒ 「买入后不再下单」的持仓 frozen 永不清零，`available` 恒为 0，
        # 监护每轮都走第 419 行「T+1 当日买入不可卖」分支 —— **硬止损被无限期跳过**。
        # 结算失败即视为读取失败（进 degraded），不带着不可信的 available 去判断。
        await engine.settle_t1()
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
        _PAPER_READ.update(state="ok" if positions else "empty", as_of=beijing_now(),
                           age_seconds=0.0, reason=None, failures=0)
    except Exception as exc:  # noqa: BLE001
        positions = []
        _PAPER_READ.update(
            state="failed", as_of=beijing_now(), age_seconds=0.0,
            reason=f"{type(exc).__name__}: {exc}"[:200],
            failures=int(_PAPER_READ.get("failures") or 0) + 1,
        )
        log.exception("模拟持仓读取失败——本轮自动离场/止损检查已跳过（连续 %d 次）",
                      _PAPER_READ["failures"])
        key = f"position-monitor-degraded:paper:{today}"
        if key not in _NOTIFIED:
            _NOTIFIED.add(key)
            _notify(app, "-", "", "position_monitor_degraded",
                    f"模拟持仓读取失败（连续 {_PAPER_READ['failures']} 轮），"
                    f"**本轮自动离场与硬止损已跳过**：{_PAPER_READ['reason']}",
                    key=key)
        fired.append({
            "symbol": "-", "action": "degraded",
            "reason": f"模拟持仓读取失败，自动离场与硬止损已跳过：{_PAPER_READ['reason']}",
            "failures": _PAPER_READ["failures"],
        })

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
        held_names[sym] = name

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
        if getattr(order, "status", "") != "filled":
            # 卖单**已受理但未成交**（限价高于现价，即本轮的行情读值与撮合内部
            # 重新取价之间价格下滑——R09）。旧实现与仓位引擎同型：直接记 `exits`、
            # 清峰值、发「自动离场」通知 ⇒ **持仓还在，却被记为已离场**，事后复盘
            # 与峰值轨迹全部对不上。此处只留痕「挂单受理」，离场判定留给下一轮
            # （持仓仍在持仓表里，下一轮仍会被检查到）。
            if key not in _NOTIFIED:
                _NOTIFIED.add(key)
                _notify(app, sym, name, "position_exit_pending",
                        f"离场挂单受理未成交：{reason}（限价 {price} 未达现价，等待撮合）")
            fired.append({"symbol": sym, "action": "exit_pending", "reason": reason})
            log.warning("[离场引擎] 离场挂单受理未成交 %s %d 股 @ 限价 %s（%s）",
                        sym, pos["available"], price, reason)
            continue
        plan["exits"].append({"ts": beijing_now().strftime("%H:%M:%S"), "symbol": sym,
                              "reason": reason, "qty": pos["available"], "price": price})
        peaks.pop(sym, None)
        save_plan(plan)
        _notify(app, sym, name, "position_exit", f"自动离场：{reason}")
        fired.append({"symbol": sym, "action": "exit", "reason": reason})
        log.warning("[离场引擎] 模拟仓自动卖出 %s %d 股 @ %s（%s）", sym, pos["available"], price, reason)

    # ---------- 真实持仓（只提醒，CRITICAL 级） ----------
    real_pos = _real_positions()
    if _REAL_READ["state"] == "failed":
        # S1-2：读失败必须与「确实无持仓」可区分——当日一次在告警台账留痕（kind 独立，
        # 前端可按系统级渲染），并进 fired 供健康哨兵观测。**降级不推送飞书**（盘中飞书
        # 只保留买点卡），但绝不能再静默当作"没有真实持仓"。
        key = f"position-monitor-degraded:{today}"
        if key not in _NOTIFIED:
            _NOTIFIED.add(key)
            _notify(app, "-", "", "position_monitor_degraded",
                    f"真实持仓读取失败（连续 {_REAL_READ['failures']} 轮），"
                    f"**本轮真实持仓止损检查已跳过**：{_REAL_READ['reason']}",
                    key=key)
        fired.append({
            "symbol": "-", "action": "degraded",
            "reason": f"真实持仓读取失败，真实持仓止损检查已跳过：{_REAL_READ['reason']}",
            "failures": _REAL_READ["failures"],
        })
    for sym, rp in real_pos.items():
        held_names.setdefault(sym, rp.get("name") or "")
        q = snap.get(sym) or {}
        price = q.get("price")
        if price is None or price <= 0:
            continue
        pct = q.get("change_pct")
        cost = rp["cost"]
        if cost is None or cost <= 0:
            # 成本不可用（人工覆盖把总成本记成 0/负）：**无法判定止损**。
            # 不能拿 0 参与计算——判据会退化成 `price <= 0`，正价格恒不成立，
            # 该持仓会**静默永不触发**。`_real_positions` 已对该标的告警留痕。
            continue
        # 成本来源可见（R07）：同一条提醒，成本来自**人工覆盖**还是**流水摊薄**
        # 对用户意味着不同的核对动作——不写清就无从判断该去改覆盖还是补流水。
        cost_src = "人工覆盖" if rp.get("overridden") else "流水摊薄"
        stop = _stop_pct(None)
        if price <= cost * (1 - stop):
            key = f"real:{sym}:stop"
            if key not in _NOTIFIED:
                _NOTIFIED.add(key)
                text = (
                    f"真实持仓止损提醒：现价 {price} 已低于成本 {cost:.2f}（{cost_src}）"
                    f"的 -{stop:.0%} 线——请确认是否卖出；"
                    "若已实际卖出，**登记卖出流水**即可（不必删除历史流水）"
                )
                _notify(app, sym, rp.get("name") or "", "real_exit_alert", text, critical=True)
                fired.append({"symbol": sym, "action": "real_alert", "reason": text})
        elif pct is not None and is_sealed(pct, board_limit_pct(sym)):
            peaks[sym] = max(float(peaks.get(sym) or price), price)

    # ---------- 止盈档位（P0-1）：日内冲高提醒 ----------
    # 标的池 = 持仓 ∪ 当日精选组合（持仓优先、去重后截断；本 tick 已离场的不重复）。
    fired_syms = {f["symbol"] for f in fired}
    pool: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    for sym, nm in held_names.items():
        if sym not in seen:
            seen.add(sym)
            pool.append((sym, nm, True))
    for sym, nm in _picks_combos().items():
        if sym not in seen:
            seen.add(sym)
            pool.append((sym, nm, False))

    for sym, nm, held in pool[:TAKE_PROFIT_POOL_CAP]:
        if sym in fired_syms:
            continue
        q = snap.get(sym) or {}
        pct = q.get("change_pct")
        if pct is None:
            continue
        rule = take_profit_rule(pct, sealed=is_sealed(pct, board_limit_pct(sym)), held=held)
        if rule is None:
            continue
        # 当日一次：key 含 trade_date，跨日自动重置（去重同时落盘，重启也不重发）
        key = f"take-profit:{today}:{sym}"
        if key in _NOTIFIED:
            continue
        _NOTIFIED.add(key)
        _notify(app, sym, nm or str(q.get("name") or ""), "take_profit", rule["reason"], key=key)
        fired.append({"symbol": sym, "action": "take_profit",
                      "reason": rule["reason"], "pct": rule["pct"], "held": held})

    save_plan(plan)
    return fired


async def position_loop(app, stop: asyncio.Event) -> None:
    """持仓监护常驻循环：交易时段 15s 一轮（与临板雷达同窗口判定）。

    S2-2 收尾（09-11）：同 `pre_limit_loop` —— 睡眠改 `wait_or_stop`。裸 `asyncio.sleep`
    时 `stop` 只在下一轮开头可见，实测停机日志「position-monitor 超过 10s 未退出，强制 cancel」。
    """
    from app.core.scheduler import wait_or_stop
    from app.picks.pre_limit_radar import radar_active_now

    log.info("position monitor loop started")
    while not stop.is_set():
        try:
            if radar_active_now():
                await evaluate_once(app)
                if await wait_or_stop(stop, 15):
                    return
            elif await wait_or_stop(stop, 30):
                return
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            log.exception("position monitor loop failed")
            if await wait_or_stop(stop, 30):
                return
