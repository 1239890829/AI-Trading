"""盘中买点判定与飞书卡片推送（2026-09-08 用户定稿，唯一保留的盘中飞书推送）。

用户原话规则：「盘中通过猎群捕捉到的个股真正进入了盘中跟踪状态，并且已到达
可以上车的买点——经过你的筛选、结合多方面多因素综合判定可以买入时——才发
飞书消息。其余任何中间状态、预警或异动信息一律不要推送。」

系统语义映射：
- 猎群捕捉 → 当日每日精选名单（DailyPickSet，盘前选出=盘中跟踪输入）
- 多因素综合判定（全部满足才命中，缺一即 skip 且 skip 理由可观测）：
  1. 置信档 ∈ {executable, strong}（meta_confidence 六维合成：综合分+相位+筹码+红线）
  2. 无红线一票否决（vetoes 空）
  3. 闸门语义：闸门日须 follow_state == "followable"（P1-2 三态）；非闸门日
     不得处于仅观察
  4. 现价落买入区间 [low, high]（build_buy_range ±3% 收敛支撑/压力）——
     「可以上车」的核心定义；无区间不臆造、直接不判
  5. 未触涨停区（change_pct < 9.5%，10cm 保守口径；20cm 高弹性由区间上限约束）
- 推送形态：飞书 interactive 卡片，**每只命中票一张独立卡片**（逐票单卡，
  不汇总多票在一条消息里），版式与每日精选推送卡完全一致
  （app/picks/push_cards.py 同函数）；同拍多票按序发送并间隔 0.5s（频控）
- 去重：每票每日至多一推（简报 append_alert 按 key 去重）；逐票落 AlertEvent
  （in_app/log 通道）供复盘与 T+1 收益回填；卡片经 FeishuNotifier
  .send_interactive 显式发送（不进逐票 NotifierRegistry 分发，避免 text 形态）

结构：evaluate_buy_points 纯函数（可回测可单测）；check_and_dispatch 服务层
（取数+分发）；buy_point_loop lifespan 调度（与 watcher_loop 同模式）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

from app.core.config import settings
from app.core.db import get_session_factory
from app.market import trade_calendar as tc
from app.models.alert import AlertRule
from app.notifiers import get_notifier_registry
from app.picks.push_cards import build_buy_point_card
from app.core.bjtime import beijing_now

log = logging.getLogger(__name__)

#: 买点专用系统规则名（落库与 in_app/log 分发；飞书卡片显式单发不走 rule 通道）
BUY_POINT_RULE_NAME = "__picks_buy_point__"

#: 置信档白名单：meta_confidence 的 executable / strong（observe/stand_aside 不推）
EXEC_TIERS = {"executable", "strong"}

#: 触涨停区阈值（涨幅 ≥ 此值视为买不进/追高风险，不推）。10cm 保守口径；
#: 20cm 高弹性标的由买入区间上限（现价 ±3% 收敛压力位）自然约束。
LIMIT_ZONE_PCT = 9.5


# ---------------------------------------------------------------- 纯函数：判定


def evaluate_buy_points(
    items: list[dict], *, gate_stand: bool, quotes: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    """多因素综合判定「可以上车」。纯函数零 IO。

    :param items: 当日每日精选 items（DailyPickSet.items 反序列化）
    :param gate_stand: 当日空仓闸门是否触发（meta.gate.stand_aside）
    :param quotes: symbol(裸6位) → 快照行（price/change_pct/prev_close）
    :returns: (hits, skips)。hits 元素可直接喂 build_buy_point_card；
              skips 元素 {"symbol", "reason"} 留痕可观测（为什么没推）。
    """
    hits: list[dict] = []
    skips: list[dict] = []
    for it in items or []:
        sym = it.get("symbol") or ""
        q = quotes.get(sym) or {}
        price = q.get("price")
        if price is None:
            skips.append({"symbol": sym, "reason": "快照无现价（不臆造）"})
            continue
        conf = it.get("confidence") or {}
        tier = conf.get("tier")
        if tier not in EXEC_TIERS:
            skips.append({"symbol": sym, "reason": f"置信档 {tier or '未判定'} 不足（需 executable/strong）"})
            continue
        vetoes = it.get("vetoes") or []
        if vetoes:
            skips.append({"symbol": sym, "reason": f"红线一票否决 {len(vetoes)} 条"})
            continue
        if gate_stand:
            # 闸门日：只有满足可跟判据（P1-2 三态）的标的才进入买点判定；
            # 可跟 ≠ 可买——不推买入建议语义的文案，卡片注明须经影子持仓验证
            if it.get("follow_state") != "followable":
                skips.append({"symbol": sym, "reason": "闸门期且不满足可跟判据"})
                continue
        elif it.get("observation_only"):
            skips.append({"symbol": sym, "reason": "仅观察（observation_only）"})
            continue
        br = it.get("buy_range") or {}
        low, high = br.get("low"), br.get("high")
        if low is None or high is None:
            skips.append({"symbol": sym, "reason": "无买入区间（不臆造）"})
            continue
        if not (low <= price <= high):
            skips.append({"symbol": sym, "reason": f"现价 {price} 不在买入区间 {low}-{high}"})
            continue
        chg = q.get("change_pct")
        if chg is None and q.get("prev_close"):
            chg = (price / q["prev_close"] - 1) * 100  # 数据源纪律：close 能推就算
        if chg is not None and chg >= LIMIT_ZONE_PCT:
            skips.append({"symbol": sym, "reason": f"涨幅 {chg:+.1f}% 触涨停区（买不进/追高风险）"})
            continue
        hits.append({
            "item": it,
            "price": price,
            "chg": chg,
            "tier_label": conf.get("label") or str(tier),
            "low": low,
            "high": high,
        })
    return hits, skips


# ---------------------------------------------------------------- 系统规则


def _default_rule_channels() -> str:
    return json.dumps([c.strip() for c in settings.picks_buy_point_channels.split(",") if c.strip()])


def ensure_buy_point_rule(session_factory) -> AlertRule:
    """买点专用系统规则（get-or-create + channels 跟随配置默认，同 watcher 语义）。"""
    with session_factory() as db:
        row = db.query(AlertRule).filter(AlertRule.name == BUY_POINT_RULE_NAME).one_or_none()
        default_channels = _default_rule_channels()
        if row is None:
            row = AlertRule(
                name=BUY_POINT_RULE_NAME,
                enabled=1,
                condition_type="picks_buy_point",
                scope="all",
                threshold=0.0,
                channels=default_channels,
            )
            db.add(row)
        elif row.channels != default_channels:
            row.channels = default_channels
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


# ---------------------------------------------------------------- 服务层：取数与分发


def _today_picks_payload() -> dict | None:
    """当日每日精选 payload（与 GET /api/picks/today 同口径直读 DB）。"""
    from sqlalchemy import select

    from app.models.daily_pick import DailyPickSet

    today = beijing_now().date().isoformat()
    with get_session_factory()() as db:
        row = db.execute(select(DailyPickSet).where(DailyPickSet.date == today)).scalar_one_or_none()
        if row is None:
            return None
        return {
            "date": row.date,
            "items": json.loads(row.items) if row.items else [],
            "meta": json.loads(row.meta) if row.meta else None,
        }


async def _sent_with_cache(state, refresh_after: float) -> dict | None:
    """情绪指标（卡片栅格用）进程内缓存——compute_market_sentiment 较重
    （全市场宽度 + 两天涨停池），与 watcher env 同节奏 600s 刷新。
    失败沿用上次值（不猜新值）；首次失败返回 None（栅格显式 '--'）。"""
    cache = getattr(state, "buy_point_sent_cache", None)
    if cache is None:
        cache = {"at": 0.0, "sent": None}
        state.buy_point_sent_cache = cache
    if cache["sent"] is not None and time.monotonic() - float(cache["at"] or 0.0) < refresh_after:
        return cache["sent"]
    try:
        from app.services.market_context import compute_market_sentiment

        sent = await compute_market_sentiment(state.hub, state.snapshot_service) or None
        if sent is not None:
            cache["sent"] = sent
            cache["at"] = time.monotonic()
    except Exception as exc:
        log.warning("buy point sentiment refresh failed: %s（沿用上次值）", exc)
    return cache["sent"]


def _quotes_from_snapshot(state) -> dict[str, dict]:
    """全市场快照 → symbol 索引（纯内存读；裸 6 位，与 DailyPickItem.symbol 同键）。"""
    snap = getattr(state, "snapshot_service", None)
    rows = getattr(snap, "snapshot", None) or []
    return {r["symbol"]: r for r in rows if r.get("symbol")}


def build_buy_point_alert(hit: dict) -> dict:
    """单票买点事件（落库与 in_app/log 分发；飞书聚合卡在 check_and_dispatch 显式单发）。"""
    it = hit["item"]
    chg = hit.get("chg")
    chg_txt = f"{chg:+.2f}%" if chg is not None else "--"
    return {
        "key": f"buy-point-{it.get('symbol')}",
        "kind": "buy_point",
        "direction": "",
        "symbol": it.get("symbol") or "",
        "name": it.get("name") or "",
        "text": (
            f"🎯 盘中买点 {it.get('name') or ''}({it.get('symbol')})："
            f"现价 {hit['price']:.2f} ∈ 买入区间 {hit['low']:.2f}-{hit['high']:.2f} · "
            f"置信 {hit['tier_label']} · 涨幅 {chg_txt}（多因素判定可上车，非投资建议）"
        ),
        "at": beijing_now().isoformat(),
        "meta": {
            "kind": "buy_point",
            "price": hit["price"],
            "buy_range": [hit["low"], hit["high"]],
            "tier": (it.get("confidence") or {}).get("tier"),
            "change_pct": chg,
        },
    }


async def check_and_dispatch(app) -> list[dict]:
    """一拍检查：交易日+盘中 → 当日精选+快照 → 判定 → 聚合卡单发 + 逐票留痕。

    返回实际分发（去重后）的 hits。任何一环失败不抛（loop 兜底日志）。
    """
    state = app.state if hasattr(app, "state") else app
    now = beijing_now()
    # 交易日 + 盘中窗口（与 watcher 同口径；盘前/盘后/午休不推）
    try:
        days = await tc.trading_days(state.hub.provider)
    except Exception as exc:
        log.warning("buy point: calendar failed: %s", exc)
        return []
    td = tc.last_trade_date(days, asof=now.date()) if days else None
    if td != now.date() or not tc.in_trading_window(now):
        return []

    payload = _today_picks_payload()
    if not payload or not payload.get("items"):
        return []
    gate = ((payload.get("meta") or {}).get("gate")) or {}
    gate_stand = bool(gate.get("stand_aside"))

    quotes = _quotes_from_snapshot(state)
    hits, skips = evaluate_buy_points(payload["items"], gate_stand=gate_stand, quotes=quotes)
    for s in skips:
        log.info("buy point skip %s: %s", s["symbol"], s["reason"])
    if not hits:
        return []

    # 逐票去重落库（append_alert key 去重；已推过的票当日不再进卡）
    from app.picks.watcher import dispatch_alert

    dispatched: list[dict] = []
    for h in hits:
        ok = await dispatch_alert(app, build_buy_point_alert(h), rule_provider=ensure_buy_point_rule)
        if ok:
            dispatched.append(h)
        else:
            log.info("buy point deduped: %s（当日已推）", h["item"].get("symbol"))
    if not dispatched:
        return []

    # 逐票单卡（2026-09-08 用户要求：每只股票对应一张独立卡片，不汇总）：
    # 先全部落库去重，再按顺序逐票发卡；同拍多票间隔 0.5s 防飞书频控
    # （自定义机器人/应用消息 5 条/秒上限，真实场景命中票通常 1~3 只）。
    sent = await _sent_with_cache(state, settings.picks_watcher_env_refresh_seconds)
    snap = getattr(state, "snapshot_service", None)
    breadth = getattr(snap, "breadth", None) or {}
    notifier = get_notifier_registry().get("feishu")
    send_card = getattr(notifier, "send_interactive", None)
    if send_card is None:
        log.warning("feishu notifier 无 send_interactive（%d 票已落库留痕）", len(dispatched))
        return dispatched
    for n, h in enumerate(dispatched, 1):
        if n > 1:
            await asyncio.sleep(0.5)
        card = build_buy_point_card([h], sent, breadth, gate or None, show=now)
        sym = h["item"].get("symbol") or ""
        if await send_card(card):
            log.warning("[PICKS-BUY-POINT] 卡片已推送：%s", sym)
        else:
            log.warning("buy point feishu card NOT delivered: %s（已落库留痕）", sym)
    return dispatched


async def buy_point_loop(app, stop: asyncio.Event) -> None:
    """盘中调度（lifespan 任务）：交易时段内每拍判定，命中即推聚合卡。

    单拍失败不终止循环；间隔下限 30s（买点判定读全市场快照内存，轻）。"""
    interval = max(30.0, settings.picks_buy_point_interval_seconds)
    while not stop.is_set():
        try:
            if settings.picks_buy_point_enabled:
                await check_and_dispatch(app)
        except Exception:
            log.exception("buy point check failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
