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
from datetime import date, datetime, timezone
from pathlib import Path

from app.core.db import get_session_factory
from app.market.trading_status import beijing_now

log = logging.getLogger(__name__)

#: 影子账户初始资金（与 main 默认一致，收益可直接对比）
SHADOW_INITIAL_CASH = 1_000_000.0
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
         "state": i.get("state"), "gap_pct": i.get("gap_pct"), "reason": i.get("reason")}
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
    from app.market.trading_status import beijing_now

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


class ShadowRunner:
    """影子账户的晨窗执行器。持有 scope=shadow 的独立 engine。"""

    def __init__(self, engine, session_factory):
        self.engine = engine
        self._sf = session_factory

    # ---- 幂等 ----

    def executed_today(self) -> bool:
        from app.market.trading_status import beijing_now
        from app.models.paper import PaperOrder

        today_cst = beijing_now().date().isoformat()
        with self._sf() as db:
            rows = db.query(PaperOrder).filter(
                PaperOrder.scope == self.engine.scope, PaperOrder.side == "buy"
            ).all()
        for o in rows:
            if not o.created_at:
                continue
            # created_at 存的是 naive UTC（default=utcnow）。必须先显式打 UTC 标再转本地——
            # 直接 .astimezone() 会把 naive 当本地时区解释，00:00-08:00 CST 期间
            # 刚写入的订单会被判成"昨天"，幂等检测失效（2026-09-07 深夜全量测试抓现行）。
            created_local = o.created_at.replace(tzinfo=timezone.utc).astimezone()
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
            q = await hub.provider.get_quote(p["symbol"])
            if q is None or not q.price or q.price <= 0:
                sell_failed.append({"symbol": p["symbol"], "reason": "停牌或无行情"})
                continue
            try:
                o = await self.engine.place_order(p["symbol"], "sell", q.price, p["available"])
                sold.append({"symbol": p["symbol"], "qty": p["available"],
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
            if not sym or it.get("state") != "normal":
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
        p.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("[SHADOW] executed: bought=%d skipped=%d sold=%d → %s",
                 len(summary["bought"]), len(summary["skipped"]),
                 len(summary["sold"]), p)

    # ---- 状态（端点用） ----

    def state(self) -> dict:
        from app.models.paper import PaperOrder, PaperPosition

        acc = self.engine.ensure_account()
        with self._sf() as db:
            positions = db.query(PaperPosition).filter(
                PaperPosition.scope == self.engine.scope, PaperPosition.quantity > 0
            ).all()
            orders = db.query(PaperOrder).filter(
                PaperOrder.scope == self.engine.scope
            ).order_by(PaperOrder.id.desc()).limit(20).all()
        return {
            "scope": self.engine.scope,
            "cash": round(acc.cash, 2),
            "initial_cash": round(acc.initial_cash, 2),
            "positions": [
                {"symbol": p.symbol, "quantity": p.quantity, "available": p.available,
                 "cost_price": p.cost_price, "buy_date": p.buy_date}
                for p in positions
            ],
            "recent_orders": [
                {"id": o.id, "symbol": o.symbol, "side": o.side, "price": o.price,
                 "quantity": o.quantity, "status": o.status, "reason": o.reason,
                 "created_at": o.created_at.isoformat() if o.created_at else None}
                for o in orders
            ],
            "executed_today": self.executed_today(),
        }


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
    from app.market.trading_status import beijing_now

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
                            else:
                                log.warning("[SHADOW] morning run: bought=%d skipped=%d sold=%d",
                                            len(summary.get("bought") or []),
                                            len(summary.get("skipped") or []),
                                            len(summary.get("sold") or []))
                        else:
                            log.info("[SHADOW] gate not judgeable yet (%s), retry next beat",
                                     (gate.get("caveats") or ["no data"])[0])
        except Exception:
            log.exception("shadow loop beat failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
