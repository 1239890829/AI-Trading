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
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import RLock
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
_PLAN_FILE_LOCK = RLock()  # Short file operations only; never held over DB work or an await.
_ABSENT = object()


class PlanConflict(RuntimeError):
    """A concurrent writer changed the same plan fact; do not overwrite it."""


class _PlanSnapshot(dict):
    def __init__(self, value):
        super().__init__(value)
        self.base = deepcopy(dict(value))  # An object attribute, never part of saved JSON.


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _row_identity(row):
    if isinstance(row, dict):
        for key in ("order_id", "trigger_event_id"):
            if type(row.get(key)) is int and row[key] > 0:
                return key, row[key]
    return None


def _row_source(row):
    return _canonical({k: v for k, v in row.items() if k not in {"event_id", "event_projected"}})


def _merge_rows(base, desired, current):
    """Append-only decision/exit rows; anonymous history keeps every occurrence."""
    historical = Counter(_canonical(row) for row in base if _row_identity(row) is None)
    orders, triggers = {}, {}
    for row in [*base, *desired, *current]:
        if not isinstance(row, dict):
            continue
        order, trigger = row.get("order_id"), row.get("trigger_event_id")
        if type(order) is int and order > 0 and type(trigger) is int and trigger > 0:
            if orders.get(order, trigger) != trigger or triggers.get(trigger, order) != order:
                raise PlanConflict("conflicting position-plan order/trigger identities")
            orders[order], triggers[trigger] = trigger, order

    def indexed(rows, side):
        result, seen = {}, Counter()
        for index, row in enumerate(rows):
            key = _row_identity(row)
            if key is not None and key[0] == "trigger_event_id" and key[1] in triggers:
                key = "order_id", triggers[key[1]]
            if key is None:
                body = _canonical(row)
                seen[body] += 1
                key = ("history", body, seen[body]) if seen[body] <= historical[body] else (side, index)
            if key in result:
                raise PlanConflict("duplicate durable position-plan identity")
            result[key] = row
        return result

    before, ours, latest = indexed(base, "base"), indexed(desired, "desired"), indexed(current, "current")
    if not before.keys() <= ours.keys() or not before.keys() <= latest.keys():
        raise PlanConflict("concurrent position-plan row removal or anonymous-row change")
    merged = deepcopy(latest)
    for key, row in ours.items():
        old, actual = before.get(key, _ABSENT), latest.get(key, _ABSENT)
        if row == old:
            continue
        if actual is _ABSENT or actual == old or actual == row:
            merged[key] = deepcopy(row)
        elif (old is not _ABSENT and isinstance(row, dict) and isinstance(actual, dict)
              and _row_source(old) == _row_source(row) == _row_source(actual)):
            receipt = deepcopy(actual)
            for field in ("event_id", "event_projected"):
                wanted, prior, now = row.get(field, _ABSENT), old.get(field, _ABSENT), actual.get(field, _ABSENT)
                if wanted == prior:
                    continue
                if now != prior and now != wanted:
                    raise PlanConflict("conflicting position-plan projection receipt")
                if wanted is not _ABSENT:
                    receipt[field] = deepcopy(wanted)
            merged[key] = receipt
        else:
            raise PlanConflict("conflicting position-plan source facts")
    return list(merged.values())


def _merge_plan(base: dict, desired: dict, current: dict) -> dict:
    if not (base.get("date") == desired.get("date") == current.get("date")):
        raise PlanConflict("position-plan date changed")
    merged = deepcopy(current)
    for key in base.keys() | desired.keys():
        old, wanted, actual = base.get(key, _ABSENT), desired.get(key, _ABSENT), current.get(key, _ABSENT)
        if wanted == old:
            continue
        if actual == old:
            if wanted is _ABSENT:
                merged.pop(key, None)
            else:
                merged[key] = deepcopy(wanted)
        elif key in {"decisions", "exits"} and all(isinstance(value, list) for value in (old, wanted, actual)):
            merged[key] = _merge_rows(old, wanted, actual)
        elif actual == wanted:
            merged[key] = deepcopy(wanted)
        elif key == "peaks" and all(isinstance(value, dict) for value in (old, wanted, actual)):
            peaks = deepcopy(actual)
            for symbol in old.keys() | wanted.keys():
                prior, value, now = old.get(symbol, _ABSENT), wanted.get(symbol, _ABSENT), actual.get(symbol, _ABSENT)
                if value == prior:
                    continue
                if now != prior and now != value:
                    raise PlanConflict("conflicting position-plan peak")
                if value is _ABSENT:
                    peaks.pop(symbol, None)
                else:
                    peaks[symbol] = deepcopy(value)
            merged[key] = peaks
        else:
            raise PlanConflict("conflicting position-plan field")
    return merged


def _plan_path(d: str) -> Path:
    return _PLAN_DIR / f"{d}.json"


def load_plan(d: str | None = None) -> dict:
    with _PLAN_FILE_LOCK:
        return _PlanSnapshot(_load_plan_contents(d))


def _load_plan_contents(d: str | None = None) -> dict:
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

    A plain dict retains explicit replacement semantics. Real load→save writers
    carry an object-only baseline so an async wait cannot erase another writer.
    """
    with _PLAN_FILE_LOCK:
        value = dict(plan)
        if isinstance(plan, _PlanSnapshot):
            value = _merge_plan(plan.base, value, _load_plan_contents(plan["date"]))
        _PLAN_DIR.mkdir(parents=True, exist_ok=True)
        target = _plan_path(plan["date"])
        tmp = target.with_name(f"{target.name}.tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, target)
        if isinstance(plan, _PlanSnapshot):
            # Keep the caller's live row references. Its next save describes only
            # changes since its own last view, not deletion of unseen additions.
            plan.base = deepcopy(dict(plan))


def record_confirm_result(alert, event_id, result, session_factory, *, reference_price=None):
    """Project this new source event's outcome; never execute or replay a confirm."""
    from app.models.alert import AlertEvent
    from app.picks.review_contract import object_json
    meta = alert.get("meta") or {}
    reference = meta.get("execution_ref") or {}
    certainty = meta.get("certainty_level") if meta.get("certainty_level") in {"高", "中", "低"} else None
    original_time = alert.get("at") if isinstance(alert.get("at"), str) else None
    try:
        trigger_clock = datetime.fromisoformat(original_time.replace("Z", "+00:00"))
        if trigger_clock.tzinfo is None:
            trigger_clock = None
    except (AttributeError, TypeError, ValueError):
        trigger_clock = None
    unknown = [key for key, value in {"certainty_level": certainty, "triggered_at": trigger_clock,
        "reference_price": reference_price,
        "decision_id": reference.get("decision_id"), "decision_version": reference.get("decision_version")}.items()
        if value is None]
    state = ("unknown" if result.get("execution_unknown") else "filled" if result.get("opened")
             else "pending" if result.get("accepted") else "rejected")
    receipt = {"state": state, "trace_state": "recorded", "trigger_event_id": event_id,
        "triggered_at": original_time, "recorded_at": beijing_now().isoformat(),
        "certainty_level": certainty, "unknown_fields": unknown, "order_id": result.get("order_id"),
        "reference_price": reference_price,
        "reason": str(result.get("reason") or "动作结果未提供原因")[:256]}
    try:
        with session_factory() as db:
            source = db.get(AlertEvent, event_id)
            if source is None or source.symbol != alert.get("symbol") or object_json(source.snapshot).get("kind") != "confirm":
                raise ValueError("confirm source event mismatch")
        try:
            plan = load_plan()
            decision = next((d for d in plan.get("decisions", [])
                if d.get("trigger_event_id") == event_id or (receipt["order_id"] is not None
                    and d.get("order_id") == receipt["order_id"] and d.get("trigger") == "confirm")), None)
            if decision is None:
                decision = {"ts": beijing_now().strftime("%H:%M:%S"), "symbol": alert.get("symbol"),
                    "name": alert.get("name"), "trigger": "confirm",
                    "action": {"rejected": "open_rejected", "unknown": "open_unknown",
                               "pending": "open_pending", "filled": "open"}[state]}
                plan.setdefault("decisions", []).append(decision)
            decision.update(receipt)
            save_plan(plan)
        except Exception:
            receipt["trace_state"] = "unknown"
            log.exception("confirm plan receipt unknown for source event %s", event_id)
        with session_factory() as db:
            source = db.get(AlertEvent, event_id)
            snapshot = object_json(source.snapshot)
            snapshot["paper_execution"] = receipt
            source.snapshot = json.dumps(snapshot, ensure_ascii=False)
            db.commit()
    except Exception:
        receipt["trace_state"] = "unknown"
        log.exception("confirm original-event receipt unknown for source event %s", event_id)
    return receipt


def _publish_open_decision(plan: dict, decision: dict) -> tuple[int, bool] | None:
    """Project a filled simulated order to one event and the existing brief."""
    order_id = decision.get("order_id")
    if order_id is None:
        log.error("position_open %s lacks durable order ID; event remains unknown", decision.get("symbol"))
        return None
    from app.picks.source_events import record_source_event

    symbol = str(decision["symbol"])
    text = (
        f"自动开模拟仓 {decision['qty']} 股 @ {decision['price']}"
        f"（总仓位上限 {decision['total_cap']:.0%}·个股权重 {decision['weight']:.0%}"
        f"·触发 {decision['trigger']}）"
    )
    source_as_of = f"{plan['date']}T{decision['ts']}+08:00"
    alert = {
        "kind": "position_open", "symbol": symbol, "name": decision.get("name") or "",
        "key": f"position-open-{order_id}", "direction": "模拟持仓", "text": text,
        "meta": {"trigger_value": decision["price"],
                 "decision_id": decision.get("decision_id"),
                 "decision_version": decision.get("decision_version"),
                 "order_id": order_id},
    }
    event_id, _, projected = record_source_event(
        "position_open", f"paper_order:{order_id}", symbol=symbol,
        name=decision.get("name") or "", text=text,
        source_id=str(order_id), source_version=str(decision.get("decision_version") or "filled"),
        source_as_of=source_as_of, trade_date=plan["date"], direction="模拟持仓",
        brief_alert=alert,
    )
    decision["event_id"] = event_id
    decision["event_projected"] = projected
    return event_id, projected


def reconcile_position_open_events() -> int:
    """Retry missing same-day event/brief projections without repeating orders."""
    plan = load_plan()
    repaired = 0
    dirty = False
    for decision in plan.get("decisions", []):
        if decision.get("action") != "open" or (
            decision.get("event_id") and decision.get("event_projected")
        ):
            continue
        try:
            result = _publish_open_decision(plan, decision)
        except Exception:
            log.exception("position_open event reconciliation failed for order %s", decision.get("order_id"))
            continue
        if result is not None:
            repaired += 1
            dirty = True
    if dirty:
        save_plan(plan)
    return repaired


async def _project_saved_plan_row(plan, collection, row, publisher, *, read_plan=None, write_plan=None) -> bool:
    """Worker owns only copies; the loop merges receipts into the latest saved fact."""
    read_plan, write_plan = read_plan or load_plan, write_plan or save_plan
    original = deepcopy(row)
    if type(original.get("order_id")) is not int or original["order_id"] <= 0:
        return False
    copied_plan, copied_row = deepcopy(plan), deepcopy(row)
    task = asyncio.create_task(asyncio.to_thread(publisher, copied_plan, copied_row))
    canceled, error, result = False, None, None
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        canceled = True
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            result = task.result()
        except Exception as exc:
            error = exc
    except Exception as exc:
        error = exc
    merged = False
    if error is not None:
        log.error("saved %s projection failed for order %s; original fact retained", collection, original["order_id"],
                  exc_info=(type(error), error, error.__traceback__))
    elif (isinstance(result, tuple) and len(result) == 2 and type(result[0]) is int and result[0] > 0
          and type(result[1]) is bool and copied_row.get("event_id") == result[0]
          and copied_row.get("event_projected") is result[1] and _row_source(copied_row) == _row_source(original)):
        try:
            latest = read_plan()
            matches = [item for item in latest.get(collection, [])
                       if isinstance(item, dict) and type(item.get("order_id")) is int
                       and item["order_id"] == original["order_id"]]
            if latest.get("date") != plan.get("date") or len(matches) != 1 or _row_source(matches[0]) != _row_source(original):
                raise PlanConflict("saved projection source identity changed")
            matches[0].update(event_id=result[0], event_projected=result[1])
            write_plan(latest)
            merged = True
        except Exception:
            log.exception("saved %s projection receipt remains pending for order %s", collection, original["order_id"])
    if canceled:
        raise asyncio.CancelledError
    return merged


async def reconcile_position_open_events_async() -> int:
    """Read/write the plan on its loop owner; only source-event projection waits in a worker."""
    plan = load_plan()
    repaired = 0
    for decision in plan.get("decisions", []):
        if decision.get("action") == "open" and not (decision.get("event_id") and decision.get("event_projected")):
            repaired += bool(await _project_saved_plan_row(plan, "decisions", decision, _publish_open_decision))
    return repaired


def preserve_filled_exit(plan_date: str, row: dict) -> None:
    """A known filled order survives unrelated optimistic-plan conflicts."""
    if type(row.get("order_id")) is not int or row["order_id"] <= 0:
        raise PlanConflict("filled exit lacks durable order identity")
    with _PLAN_FILE_LOCK:
        latest = load_plan(plan_date)
        matches = [item for item in latest.get("exits", []) if item.get("order_id") == row["order_id"]]
        if matches:
            if len(matches) != 1 or _row_source(matches[0]) != _row_source(row):
                raise PlanConflict("filled exit source identity conflicts")
            return
        latest.setdefault("exits", []).append(deepcopy(row))
        save_plan(latest)


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
    decision_context: dict | None = None,
    selection_context: dict | None = None,
) -> dict:
    """触发后裁定是否自动开模拟仓。返回 {opened, reason, qty?, weight?}。全程只动模拟盘。"""
    state = app.state if hasattr(app, "state") else app
    engine = getattr(state, "paper", None)
    if engine is None:
        return {"opened": False, "reason": "paper engine 缺失"}
    if engine.scope != "main":
        return {"opened": False, "reason": "自动模拟动作只属于main账户，不混用研究影子账户"}
    if trigger not in OPEN_TRIGGERS:
        return {"opened": False, "reason": f"触发 {trigger} 不在开仓白名单（嗅到≠买入）"}
    if price is None or price <= 0:
        return {"opened": False, "reason": "无有效价格"}

    # IMP-006：模拟动作只引用上游已冻结的 decision/version；完整快照正文不复制到
    # position plan，避免形成第二事实库。价格身份仅保留必要摘要，真实成交仍只认 order fill。
    decision_trace = {}
    if isinstance(decision_context, dict):
        reference = decision_context.get("reference_entry") or {}
        executable = decision_context.get("executable_snapshot") or {}
        decision_trace = {
            "decision_id": decision_context.get("decision_id"),
            "decision_version": decision_context.get("decision_version"),
            "reference_price": decision_context.get("reference_price", reference.get("price")),
            "execution_snapshot_price": decision_context.get(
                "execution_snapshot_price", executable.get("price")
            ),
            "execution_snapshot_state": decision_context.get(
                "execution_snapshot_state", executable.get("state")
            ),
            "execution_snapshot_as_of": decision_context.get(
                "execution_snapshot_as_of", executable.get("as_of")
            ),
            "execution_snapshot_source": decision_context.get(
                "execution_snapshot_source", executable.get("source")
            ),
        }

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

    if trigger == "buy_point":
        from app.paper.selection_action import automatic_selection_binding
        try:
            selected_meta, issue = automatic_selection_binding(engine, symbol, selection_context)
        except Exception:
            log.exception("automatic selection binding unavailable for %s", symbol)
            return {"opened": False, "reason": "原自动入选资格暂不可核实，未执行"}
        if issue:
            return {"opened": False, "reason": issue[1]}
        gate, phase = selected_meta.get("gate") or {}, selected_meta.get("market_phase")
    else:
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
        # Planning is read-only. Only the qualified executor may initialize funds.
        account = engine.account_summary(0, initialize=False)
        from app.paper.engine import DEFAULT_INITIAL_CASH
        initial = float(account.get("initial_cash") or DEFAULT_INITIAL_CASH)
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
        order = await engine.place_order(symbol, "buy", price, qty,
            **({"selection_context": selection_context} if trigger == "buy_point" else {}))
    except Exception as exc:  # noqa: BLE001
        return {"opened": False, "execution_unknown": True, "reason": f"下单异常: {exc}"}
    if getattr(order, "status", "") == "rejected":
        reason = getattr(order, "reason", "rejected")
        return {"opened": False, "reason": f"撮合拒绝：{reason}"}

    if getattr(order, "status", "") != "filled":
        # 挂单**已受理但未成交**（限价未达现价）——R09：此处旧实现一路按成交处理，
        # 后果是「挂单」被记成「已开仓」并发 position_open 通知（用户以为已建仓）。
        # 台账仍要留痕（本模块契约：决策全程可复盘），但用**可区分的 action**，
        # 使按 `action == "open"` 统计已开仓的消费方不被误导。
        projection_unknown = False
        try:
            plan = load_plan()
            plan["decisions"].append({
                "ts": beijing_now().strftime("%H:%M:%S"),
                "symbol": symbol, "name": name, "trigger": trigger,
                "action": "open_pending", "qty": qty, "price": price,
                "weight": weight, "total_cap": total_cap, "role": role,
                "order_id": getattr(order, "id", None),
                **decision_trace,
                "reason": f"{cap_note}；限价 {price} 未达现价，挂单已受理、等待撮合",
            })
            save_plan(plan)
        except Exception:
            projection_unknown = True
            log.exception("position plan projection failed after accepted order %s", getattr(order, "id", None))
        log.info(
            "[仓位引擎] 开仓挂单受理未成交 %s %s %d 股 @ 限价 %s（%s）",
            symbol, name, qty, price, cap_note,
        )
        return {
            "opened": False, "accepted": True, "pending": True,
            "qty": qty, "price": price, "order_id": getattr(order, "id", None),
            "decision_id": decision_trace.get("decision_id"),
            "decision_version": decision_trace.get("decision_version"),
            "projection_unknown": projection_unknown,
            "reason": f"挂单受理未成交（限价 {price} 未达现价），等待撮合",
        }

    filled = getattr(order, "filled_price", None) or price
    decision = {
        "ts": beijing_now().strftime("%H:%M:%S"),
        "symbol": symbol, "name": name, "trigger": trigger,
        "action": "open", "qty": qty, "price": filled,
        "weight": weight, "total_cap": total_cap, "role": role,
        "order_id": getattr(order, "id", None),
        **decision_trace,
        "reason": f"{cap_note}；角色 {role or '默认'} 权重 {weight:.0%}；触发 {trigger}",
    }
    plan = {"date": beijing_now().date().isoformat(), "decisions": [decision]}
    plan_loaded = False
    projection_unknown = False
    try:
        plan = load_plan()
        plan["decisions"].append(decision)
        plan_loaded = True
        save_plan(plan)
    except Exception:
        projection_unknown = True
        # A filled order must still reach the durable event even when the
        # secondary JSON plan is temporarily unwritable.
        log.exception("position_open plan save failed after filled order %s", decision["order_id"])
    log.info("[仓位引擎] 开模拟仓 %s %s %d 股 @ %s（%s）", symbol, name, qty, filled, cap_note)

    # 成交单是权威事实；事件及晨报是可恢复投影。失败不得重复下单。
    event_id = None
    event_projected = False
    try:
        result = _publish_open_decision(plan, decision)
        if result is not None:
            event_id, event_projected = result
    except Exception:
        projection_unknown = True
        log.exception("position_open 事件投影失败；成交单保留，后续按 plan 补建")
    if plan_loaded:
        try:
            save_plan(plan)
        except Exception:
            projection_unknown = True
            log.exception("position_open plan save failed after event attempt %s", decision["order_id"])
    return {
        "opened": True, "qty": qty, "weight": weight, "price": filled,
        "order_id": getattr(order, "id", None),
        "event_id": event_id, "event_projected": event_projected,
        "decision_id": decision_trace.get("decision_id"),
        "decision_version": decision_trace.get("decision_version"),
        "projection_unknown": projection_unknown,
        "reason": cap_note,
    }
