"""每日精选影子模拟持仓（picks-intraday-fusion-assessment.md §4，P0-B）。

解决「策略停滞」：空仓闸门连续触发时 picks 照常产出却无人消费——没有对照组，
永远无法回答"空仓是对是错"。影子持仓把"产出→决策→验证"链条重新接上：

- **每日**（无论 gate 是否空仓）：picks 组合收盘定稿 → 次日 09:26 竞价结束后，
  影子账户（scope=shadow，独立于用户 main 账户）在**执行闸门允许的桶内**开盘买入；
- **卖出**：持有 1 个交易日后（T+1 制度下 T+2 开盘可卖）次日晨窗全部卖出——
  机械轮动，与回测口径（D+1 开盘买入持有）对应；
- **受全部硬约束**：复用 PaperTradingEngine 撮合（T+1/涨跌停/整手/费用/停牌拒）；
  执行闸门 blocked/observe/anomaly/unknown 的票跳过并记录——闸门价值由此逐日量化。

幂等：当日晨窗已执行（影子订单表当日存在 buy 单）则跳过；执行摘要落
data/picks/shadow/YYYY-MM-DD.json 供复盘 collector 消费（缺失 = gap 降级）。

纯逻辑与 IO 分离：`plan_buys` 是纯函数（闸门结果 + 现金 → 计划），可测；
`run_morning` 是 IO 编排，失败逐票折进 log，不中断其他票。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path

from app.core.db import get_session_factory
from app.core.bjtime import BJ_TZ, beijing_now

log = logging.getLogger(__name__)

#: 资金使用上限比例（留 1% 缓冲费用）
CASH_USE_RATIO = 0.99


def plan_buys(gate_items: list[dict], cash: float) -> dict:
    """纯函数：执行闸门结果 + 可用现金 → 买入计划。

    只有 state=normal 进买入；blocked/observe/anomaly/unknown 跳过并留档
    （跳过原因就是闸门价值的证据链）。等权分配、整手（100 股）。
    """
    buyable = [i for i in gate_items if i.get("state") == "normal" and not i.get("observation_only")]
    skipped = [
        {"symbol": i.get("symbol"), "name": i.get("name"),
         "state": i.get("state"), "gap_pct": i.get("gap_pct"),
         "reason": "原精选仅观察，不生成影子买入计划"
         if i.get("state") == "normal" and i.get("observation_only") else i.get("reason"),
         **({"observation_only": True, "gate_reason": i.get("reason")}
            if i.get("observation_only") else {})}
        for i in gate_items
        if i.get("state") != "normal" or i.get("observation_only")
    ]
    plans: list[dict] = []
    if buyable and cash > 0:
        budget = cash * CASH_USE_RATIO / len(buyable)
        for it in buyable:
            price = it.get("open_price")
            if not price or price <= 0:
                skipped.append({"symbol": it.get("symbol"), "name": it.get("name"),
                                "state": "unknown", "gap_pct": it.get("gap_pct"),
                                "reason": "无开盘价，无法定价"})
                continue
            qty = int(budget / price / 100) * 100
            if qty <= 0:
                skipped.append({"symbol": it.get("symbol"), "name": it.get("name"),
                                "state": "no_lots", "gap_pct": it.get("gap_pct"),
                                "reason": f"预算 {budget:.0f} 不足一手（价 {price}）"})
                continue
            plans.append({"symbol": it["symbol"], "name": it.get("name"),
                          "price": price, "qty": qty})
    return {"plans": plans, "skipped": skipped}


def _shadow_dir() -> Path:
    from app.core.config import settings

    return Path(settings.parquet_dir).parent / "picks" / "shadow"


def _today_key() -> str:

    return beijing_now().date().isoformat()


def load_execution_log(day: str) -> dict | None:
    """读某日影子执行摘要（复盘 collector 用）。缺失返回 None（≠空执行）。"""
    p = _shadow_dir() / f"{day}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


#: 跳过原因（N4）：把「今天为什么没跑」写进台账，否则事后无法区分
#: **没执行** 与 **执行了但 0 成交** —— 两者在"文件缺失"时长得一模一样。
SKIP_NOT_TRADING_DAY = "not_trading_day"
SKIP_WINDOW_CLOSED = "window_not_open"
SKIP_GATE_NOT_JUDGEABLE = "gate_not_judgeable"
SKIP_NO_RUNNER = "runner_not_ready"
SKIP_T1_SETTLEMENT_UNAVAILABLE = "t1_settlement_unavailable"


def has_execution_evidence(record: dict) -> bool:
    """Execution fields, including empty order lists, must survive later skip beats."""
    return any(key in record for key in ("bought", "sold", "executed_at")) or isinstance(record.get("skipped"), list)


def execution_log_issues(record: dict | None, account: dict) -> dict[str, str]:
    """Read-only shape checks shared by review collection and presentation."""
    if not isinstance(record, dict) or not record:
        return {"execution": "当日无影子执行日志，不能判断未执行还是记录缺失"}
    skipped = record.get("skipped")
    raw_reason = record.get("skipped_reason")
    reason = raw_reason if isinstance(raw_reason, str) and raw_reason else (skipped if isinstance(skipped, str) else None)
    issues = {}
    if raw_reason is not None and not isinstance(raw_reason, str):
        issues["skipped_reason"] = "影子跳过原因字段非法，保留原标记，不推断执行状态"
    if record.get("caveat") is not None and not isinstance(record["caveat"], str):
        issues["caveat"] = "影子跳过依据字段非法，不能当作可读原因"
    if not has_execution_evidence(record) and isinstance(skipped, str) and skipped:
        if reason in {SKIP_WINDOW_CLOSED, SKIP_NOT_TRADING_DAY}:
            return issues
        issues["execution"] = f"影子本次跳过：{reason}；当日执行明细未确认"
        return issues
    for field in ("bought", "sold", "skipped"):
        rows = record.get(field)
        if not isinstance(rows, list):
            issues[field] = (f"已有执行记录，但逐股跳过明细未知；原跳过标记={skipped}"
                             if field == "skipped" and isinstance(skipped, str)
                             else f"影子{field}字段不是明细列表，数量与完整性未知")
        elif any(not isinstance(row, dict) for row in rows):
            issues[field] = f"影子{field}列表含非法明细，仅保留可识别记录，数量不完整"
        elif field in {"bought", "sold"} and any(
            not isinstance(row.get("symbol"), str) or not row["symbol"]
            or row.get("status") not in ("filled", "pending", "rejected", "cancelled", "canceled", "error")
            for row in rows
        ):
            issues[field] = f"影子{field}明细缺有效标的或委托状态，数量不完整"
        elif field == "skipped" and any(
            not isinstance(row.get("symbol"), str) or not row["symbol"]
            or not isinstance(row.get("state"), str) or not row["state"] for row in rows
        ):
            issues[field] = "影子跳过明细缺标的或状态，逐股跳过数量未知"
    try:
        stamp = datetime.fromisoformat(record["executed_at"])
        if stamp.tzinfo is None:
            raise ValueError("missing timezone")
    except (KeyError, TypeError, ValueError):
        issues["executed_at"] = "影子执行时点缺失或非法，不能补成当前时刻"
    gate = record.get("gate_summary")
    if not isinstance(gate, dict) or any(
        key in gate and (type(gate[key]) is not int or gate[key] < 0)
        for key in ("blocked", "observe", "anomaly", "unknown")
    ):
        issues["gate_summary"] = "影子闸门汇总缺失或非法，不推算闸门分桶数量"
    ret = account.get("total_return_pct") if isinstance(account, dict) else None
    if not isinstance(account, dict) or (ret is not None and (
        type(ret) not in (int, float) or not math.isfinite(ret)
    )):
        issues["account"] = "影子账户收益字段非法，未按零收益补齐"
    return issues


def persist_shadow_skip(day: str, reason: str, **extra) -> Path | None:
    """**跳过分支留痕**（N4，2026-09-11）。

    此前 `shadow_loop` 只有「执行完成」会由 `_persist` 落盘，其余分支（非交易日、
    窗口未到、闸门不可判、runner 未就绪）**只打日志** ⇒ 台账上这些日子是空白，
    排查时只能翻日志。现在每个跳过分支都写 `data/picks/shadow/<day>.json`。

    两条必须守住的性质：
    1. **不覆盖已执行的摘要**——若当天已经跑过（文件里有 `bought`/`sold`），
       绝不能把它覆盖成 "skipped"，那等于把真实执行抹掉；
    2. **同原因幂等**——循环每 60s 一拍，窗口未到会反复触发；原因没变就不重写，
       避免每分钟一次无谓 IO。

    :returns: 写入的路径；未写入（应保留原状）时返回 None。
    """
    p = _shadow_dir() / f"{day}.json"
    try:
        existing = load_execution_log(day)
    except OSError:
        return None
    if existing is None:
        try:
            p.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            return None
        else:
            return None  # Existing unreadable JSON or a dangling link is not a missing log.
    # 已有真实执行记录 ⇒ 保留，绝不覆盖
    if existing is not None and not isinstance(existing, dict):
        return None  # A malformed historical record is not permission to overwrite it.
    if existing and (has_execution_evidence(existing) or not existing.get("skipped")):
        return None
    # 同一原因已记录 ⇒ 幂等跳过
    if existing and existing.get("skipped_reason") == reason:
        return None

    payload = {**(existing or {}), "day": day, "skipped": reason,
               "skipped_reason": reason, "updated_at": beijing_now().isoformat()}
    payload.update(extra)
    d = p.parent
    d.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("[SHADOW] skip recorded: %s (%s) → %s", day, reason, p)
    return p


class ShadowRunner:
    """影子账户的晨窗执行器。持有 scope=shadow 的独立 engine。"""

    def __init__(self, engine, session_factory):
        self.engine = engine
        self._sf = session_factory

    # ---- 幂等 ----

    def executed_today(self) -> bool:
        from app.models.paper import PaperOrder

        today_cst = beijing_now().date().isoformat()
        with self._sf() as db:
            rows = db.query(PaperOrder).filter(
                PaperOrder.scope == self.engine.scope, PaperOrder.side == "buy"
            ).all()
        for o in rows:
            if not o.created_at:
                continue
            # created_at 存的是 naive UTC（default=utcnow）。必须先显式打 UTC 标再转
            # 北京——转「进程本地」在 +8 机器上碰巧正确，在 UTC 环境（CI）与
            # today_cst（北京日历）错位一天，幂等检测失效（2026-09-07 同类事故）。
            created_local = o.created_at.replace(tzinfo=timezone.utc).astimezone(BJ_TZ)
            if created_local.date().isoformat() == today_cst:
                return True
        return False

    # ---- 晨窗执行 ----

    async def run_morning(self, hub, gate: dict) -> dict:
        """09:26 竞价窗口：先卖昨日持仓（T+1 已解冻），再按闸门买今日计划。

        :param gate: collect_execution_gate 的返回（闸门判定）。
        :return: 执行摘要 dict（同时落 data/picks/shadow/<day>.json）。
        """
        day = _today_key()
        if self.executed_today():
            return {"day": day, "skipped": "already_executed"}

        # available is persisted quantity minus frozen_today. Settle this engine's
        # scope before reading it; otherwise the zero-available branch below can
        # prevent reaching place_order's own settlement forever.
        try:
            await self.engine.settle_t1()
        except Exception as exc:
            log.exception("shadow T+1 settlement failed; morning execution skipped")
            caveat = f"影子 T+1 结算失败（{type(exc).__name__}），本拍未执行委托，等待重试"
            persist_shadow_skip(day, SKIP_T1_SETTLEMENT_UNAVAILABLE, caveat=caveat,
                                pick_date=gate.get("pick_date"), gate_summary=gate.get("summary"))
            return {"day": day, "skipped": SKIP_T1_SETTLEMENT_UNAVAILABLE, "caveat": caveat}

        # 1) 卖出：可卖持仓全部市价卖出（机械轮动，持有一个交易日）
        sold: list[dict] = []
        sell_failed: list[dict] = []
        with self._sf() as db:
            from app.models.paper import PaperPosition

            rows = db.query(PaperPosition).filter(
                PaperPosition.scope == self.engine.scope, PaperPosition.quantity > 0
            ).all()
            pos_snapshot = [
                {"symbol": p.symbol, "qty": p.quantity, "available": p.available,
                 "cost": p.cost_price, "buy_date": p.buy_date}
                for p in rows
            ]
        for p in pos_snapshot:
            if p["available"] <= 0:
                sell_failed.append({"symbol": p["symbol"], "reason": "T+1 未解冻（昨买今卖不可）"})
                continue
            try:
                q = await hub.provider.get_quote(p["symbol"])
                if q is None or not q.price or q.price <= 0:
                    sell_failed.append({"symbol": p["symbol"], "reason": "停牌或无行情"})
                    continue
                o = await self.engine.place_order(p["symbol"], "sell", q.price, p["available"])
                sold.append({"symbol": p["symbol"], "qty": p["available"],
                             "order_id": o.id,
                             "status": o.status, "filled_price": o.filled_price,
                             "reason": o.reason})
            except Exception as exc:  # noqa: BLE001
                sell_failed.append({"symbol": p["symbol"], "reason": str(exc)[:120]})

        # 2) 买入：闸门 normal 桶，按开盘快照定价
        acc = self.engine.ensure_account()
        open_map = await self._open_prices(hub, gate)
        gate_items = [dict(i, open_price=open_map.get(i["symbol"])) for i in gate.get("items") or []]
        plan = plan_buys(gate_items, acc.cash)
        bought: list[dict] = []
        for pl in plan["plans"]:
            try:
                o = await self.engine.place_order(pl["symbol"], "buy", pl["price"], pl["qty"])
                bought.append({"symbol": pl["symbol"], "name": pl.get("name"),
                               "order_id": o.id,
                               "qty": pl["qty"], "price": pl["price"],
                               "status": o.status, "filled_price": o.filled_price,
                               "reason": o.reason})
            except Exception as exc:  # noqa: BLE001
                bought.append({"symbol": pl["symbol"], "name": pl.get("name"),
                               "qty": pl["qty"], "price": pl["price"],
                               "status": "error", "reason": str(exc)[:120]})

        summary = {
            "day": day,
            "pick_date": gate.get("pick_date"),
            "gate_summary": gate.get("summary"),
            "sold": sold,
            "sell_failed": sell_failed,
            "bought": bought,
            "skipped": plan["skipped"],
            "cash_after": round(self.engine.ensure_account().cash, 2),
            "executed_at": beijing_now().isoformat(),
        }
        self._persist(summary)
        return summary

    async def _open_prices(self, hub, gate: dict) -> dict[str, float]:
        """闸门成员的开盘价（买入定价）。逐票 get_quote 失败 = 缺失，纯函数侧拒单。"""
        out: dict[str, float] = {}
        for it in gate.get("items") or []:
            sym = it.get("symbol")
            if not sym or it.get("state") != "normal" or it.get("observation_only"):
                continue
            try:
                q = await hub.provider.get_quote(sym)
                if q is not None and q.open and q.open > 0:
                    out[sym] = q.open
            except Exception as exc:  # noqa: BLE001
                log.warning("shadow open price %s failed: %s", sym, exc)
        return out

    def _persist(self, summary: dict) -> None:
        d = _shadow_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{summary['day']}.json"
        existing = load_execution_log(summary["day"])
        if p.exists() and not isinstance(existing, dict):
            raise ValueError("existing shadow execution log is unreadable; preserving original")
        if isinstance(existing, dict) and existing.get("day") != summary["day"]:
            raise ValueError("existing shadow execution day is mismatched; preserving original")
        # A sell-only beat does not complete the buy evaluation for the day.
        # Keep its real order/error receipts when a later beat is empty, without
        # changing the original retry or executed_today rule.
        preserve = isinstance(existing, dict) and has_execution_evidence(existing)
        for field in ("sold", "sell_failed", "bought"):
            rows = summary[field]
            for row in rows:
                row.setdefault("recorded_at", summary["executed_at"])
            if preserve:
                previous = existing.get(field, [])
                if not isinstance(previous, list):
                    raise ValueError(f"existing shadow {field} is invalid; preserving original")
                known = {(row.get("order_id"), row.get("status")) for row in previous
                         if isinstance(row, dict) and type(row.get("order_id")) is int}
                rows = [row for row in rows if type(row.get("order_id")) is not int
                        or (row["order_id"], row.get("status")) not in known]
                summary[field] = [*previous, *rows]
        if preserve:
            summary["first_executed_at"] = existing.get("first_executed_at") or existing.get("executed_at")
        temporary = p.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(p)
        log.info("[SHADOW] executed: bought=%d skipped=%d sold=%d → %s",
                 len(summary["bought"]), len(summary["skipped"]),
                 len(summary["sold"]), p)

    # ---- 状态（端点用） ----

    def state(self) -> dict:
        return read_shadow_state(self._sf, self.engine.scope)


def read_shadow_state(session_factory, scope="shadow") -> dict:
    """Read an existing shadow account; opening its view never initializes funds."""
    from app.models.paper import PaperAccount, PaperOrder, PaperPosition

    with session_factory() as db:
        acc = db.query(PaperAccount).filter(PaperAccount.scope == scope).first()
        positions = db.query(PaperPosition).filter(PaperPosition.scope == scope, PaperPosition.quantity > 0).all()
        orders = db.query(PaperOrder).filter(PaperOrder.scope == scope).order_by(PaperOrder.id.desc()).limit(20).all()
        day_start = beijing_now().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
        from datetime import timedelta
        executed = db.query(PaperOrder.id).filter(PaperOrder.scope == scope, PaperOrder.side == "buy",
            PaperOrder.created_at >= day_start, PaperOrder.created_at < day_start + timedelta(days=1)).first() is not None
    return {"scope": scope, "account_created": acc is not None,
            "cash": round(acc.cash, 2) if acc else None, "initial_cash": round(acc.initial_cash, 2) if acc else None,
            "positions": [{"symbol": p.symbol, "quantity": p.quantity, "available": p.available,
                           "cost_price": p.cost_price, "buy_date": p.buy_date} for p in positions],
            "recent_orders": [{"id": o.id, "symbol": o.symbol, "side": o.side, "price": o.price,
                              "quantity": o.quantity, "status": o.status, "reason": o.reason,
                              "created_at": o.created_at.isoformat() if o.created_at else None} for o in orders],
            "executed_today": executed}


def activation_status(app, kind: str) -> dict:
    """Separate configuration, loaded runner, scheduler state, and completed ticks."""
    from app.core.config import settings

    daily = kind == "daily"
    configured = settings.picks_shadow_enabled if daily else settings.hunting_shadow_enabled
    runner = getattr(app.state, "paper_shadow" if daily else "hunting_shadow", None)
    registry = getattr(app.state, "schedulers", None)
    task_name = "picks-shadow" if daily else "hunting-shadow"
    scheduler = next((row for row in registry.snapshot() if row.get("name") == task_name), None) if registry else None
    health = getattr(runner, "health", {}) or {}
    state = "not_loaded" if runner is None else "paused" if not configured else "unknown"
    if configured and runner and scheduler:
        state = "running" if scheduler.get("state") == "running" else "paused"
        if state == "running" and health.get("state") in {"ready", "degraded"}:
            state = health["state"]
        if scheduler.get("consecutive_tick_failures", 0) or scheduler.get("state") in {"dead", "restarting"}:
            state = "degraded"
    def clock(minutes):
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    return {"enabled": bool(configured),
            "activation": {"configured": bool(configured), "runner_loaded": runner is not None,
                "timezone": "Asia/Shanghai", "mode": "daily_morning" if daily else "qualified_buy_point",
                "start_time": clock(settings.picks_shadow_start_minute) if daily else None,
                "end_time": clock(settings.picks_shadow_end_minute) if daily else None,
                "quote_recheck_seconds": None if daily else 60, "poll_seconds": 60 if daily else 30},
            "runtime": {"state": state, "as_of": health.get("as_of") or (scheduler or {}).get("last_ok_tick"),
                "scheduler_state": (scheduler or {}).get("state"),
                "reason": health.get("reason") or (scheduler or {}).get("last_tick_error") or
                          (scheduler or {}).get("not_started_reason") or ("后台尚未装配" if runner is None else None)}}


def collect_shadow_for_review(session_factory, trade_date: date) -> dict | None:
    """复盘消费：当日影子执行摘要 + 影子账户累计绩效。缺失返回 None（gap 降级）。"""
    from app.models.paper import SCOPE_SHADOW, PaperAccount

    log_row = load_execution_log(trade_date.isoformat())
    with session_factory() as db:
        acc = db.query(PaperAccount).filter(PaperAccount.scope == SCOPE_SHADOW).first()
    if log_row is None and acc is None:
        return None
    total = (acc.cash if acc else 0.0)
    return {
        "execution": log_row,
        "account": {
            "cash": round(acc.cash, 2) if acc else None,
            "initial_cash": round(acc.initial_cash, 2) if acc else None,
            # 未平仓的浮动盈亏不含（收盘复盘时影子已机械清仓——晨卖晚买节奏）；
            # 有持仓说明卖出失败，如实呈现而非漏记
            "total_return_pct": (
                round((total / acc.initial_cash - 1) * 100, 2)
                if acc and acc.initial_cash else None
            ),
        },
    }


# ---------------------------------------------------------------- 调度（lifespan 任务）


async def shadow_loop(app, stop: asyncio.Event) -> None:
    """晨窗调度：交易日 09:26~09:45 窗口内每 60s 检查，当日未执行则执行。

    无组合/闸门全 unknown 时跳过且**不落幂等标记**（幂等以"当日存在 buy 订单"
    为准），下一拍重试；窗口错过顺延次日，不追价（评估报告 §3.5 纪律）。
    """
    from app.core.config import settings
    from app.market import trade_calendar as tc

    interval = 60.0
    log.info("shadow loop started: window %02d:%02d-%02d:%02d",
             settings.picks_shadow_start_minute // 60, settings.picks_shadow_start_minute % 60,
             settings.picks_shadow_end_minute // 60, settings.picks_shadow_end_minute % 60)
    while not stop.is_set():
        try:
            now = beijing_now()
            minutes = now.hour * 60 + now.minute
            if settings.picks_shadow_start_minute <= minutes < settings.picks_shadow_end_minute:
                days = await tc.trading_days(app.state.hub.provider)
                td = tc.last_trade_date(days, asof=now.date()) if days else None
                if td == now.date():
                    runner = getattr(app.state, "paper_shadow", None)
                    # N4 留痕：runner 未就绪也要记一笔，否则那天在台账上就是空白
                    if runner is None:
                        persist_shadow_skip(now.date().isoformat(), SKIP_NO_RUNNER)
                    if runner is not None and not runner.executed_today():
                        from app.picks.execution_gate import collect_execution_gate

                        gate = await collect_execution_gate(
                            app.state.hub, get_session_factory(),
                            block_ge=settings.picks_gate_block_gap,
                            observe_ge=settings.picks_gate_observe_gap,
                            anomaly_le=settings.picks_gate_anomaly_gap,
                        )
                        executable = (gate.get("summary") or {}).get("executable", 0)
                        if gate.get("pick_date") and (executable or gate.get("summary")):
                            summary = await runner.run_morning(app.state.hub, gate)
                            if summary.get("skipped") == "already_executed":
                                log.info("[SHADOW] already executed today")
                            elif summary.get("skipped") == SKIP_T1_SETTLEMENT_UNAVAILABLE:
                                log.warning("[SHADOW] %s", summary["caveat"])
                            else:
                                log.warning("[SHADOW] morning run: bought=%d skipped=%d sold=%d",
                                            len(summary.get("bought") or []),
                                            len(summary.get("skipped") or []),
                                            len(summary.get("sold") or []))
                        else:
                            caveat = (gate.get("caveats") or ["no data"])[0]
                            log.info("[SHADOW] gate not judgeable yet (%s), retry next beat", caveat)
                            # N4 留痕：闸门不可判是"有原因的没跑"，必须与"跑了但 0 成交"区分
                            persist_shadow_skip(now.date().isoformat(),
                                                SKIP_GATE_NOT_JUDGEABLE,
                                                caveat=caveat,
                                                gate_summary=gate.get("summary"))
                else:
                    # N4 留痕：非交易日（日历不含今日）
                    persist_shadow_skip(now.date().isoformat(), SKIP_NOT_TRADING_DAY,
                                        calendar_last_trade_date=td.isoformat() if td else None)
            else:
                # N4 留痕：窗口未到/已过。幂等写（同原因只写一次），不产生每分钟的 IO
                persist_shadow_skip(now.date().isoformat(), SKIP_WINDOW_CLOSED,
                                    minute_of_day=minutes)
        except Exception:
            log.exception("shadow loop beat failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
