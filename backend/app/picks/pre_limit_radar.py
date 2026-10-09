"""临板雷达：涨停前识别、提醒与准入（2026-09-09 用户指令，KB-DEC-011）。

**政策**：只有涨停前已提醒过的股票才准入盘中跟踪；封板后才发现的一律不入册。
旧门槛「boards≥1」= 制度上等涨停，是滞后根源之一——已从机会候选准入中移除。

临板区（按板性缩放，封板前保留可操作跑道）——涨幅上限**委托单点
`app/market/price_rules.limit_pct`**（2026-09-11 收口，此前本模块是第二份手写实现）：

| 板性 | 涨停幅 | 临板下沿 | 封板判定 |
|---|---|---|---|
| 主板 60/00 | 10% | 6.5% | pct ≥ 9.7 |
| 主板 ST/*ST | 10% | 6.5% | pct ≥ 9.7 |
| 创业/科创 30/68 | 20% | 13.0% | pct ≥ 19.7 |
| 北交所 43/83/87/92 | 30% | 19.5% | pct ≥ 29.7 |

📌 2026-07-06 并轨：主板 ST 由 5% 放宽至 10%（与主板普通股一致），创业板/
科创板 ST 维持 20%、北交所维持 30%——**ST 状态已不再改变涨跌幅**，故上表
不再单列 5% 档。旧实现「ST 名称优先于代码段」还会把双创/北交所 ST 误判为
5%，已随收口一并修正。

雷达活跃窗口：**09:20–11:30 / 13:00–15:00**（含集合竞价尾段 09:20-09:25——
竞价指示价临板即可提前预警，抢在 09:25 封板价确定之前）。每 6s 扫一遍内存
全市场快照；新进入临板区的股票：

1. `record_sighting(layer="pre_limit", gate=...)` —— 先登记（封板前一刻的入场价）
2. `dispatch_alert(kind="pre_limit")` —— 规则直发，**不经 LLM 判读（零延迟）**

落点：当日简报 `alerts[]`、`AlertEvent`；具名且带真实代码的 `pre_limit`
形状也进入消息通知中心。通知中心按事件形状筛选，不按 watcher 规则名全量接收；
板块级/无名称事件仍被排除。历史 `IMP-028/034` 的规则名收口注释已失效。

当日幂等：以台账首见唯一为准（重启安全——去重源是台账而非内存）。
新鲜度守卫：快照 `last_success` 超 120s（非交易时段/数据停更）雷达静默，防陈旧数据误报。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.market.price_rules import limit_pct as _rules_limit_pct
from app.market.price_rules import limit_up_distance, limit_up_distance_text
from app.core.bjtime import beijing_now, to_beijing
from app.core.ttl_cache import cache_on
from app.data_providers.tencent import to_tencent_symbol

log = logging.getLogger(__name__)

#: 快照新鲜度上限（秒）——超过视为陈旧（非交易时段/停更），雷达静默
SNAPSHOT_FRESH_SECONDS = 120
#: 雷达扫描间隔（秒）
SWEEP_INTERVAL = 6.0
#: 非活跃窗口的休眠间隔（秒）
IDLE_INTERVAL = 30.0
DISTANCE_FETCH_TIMEOUT = 2.0
DISTANCE_BATCH_SIZE = 50
#: 开板重评已落事件的本进程快取；持久去重以 AlertEvent.dedup_key 为准。
_REOPEN: set[tuple[str, str]] = set()

# 交易时段（含集合竞价尾段）："HH:MM" 区间
_ACTIVE_WINDOWS = (("09:20", "11:30"), ("13:00", "15:00"))


def board_limit_pct(symbol: str, name: str = "") -> float:
    """按现行交易规则推断涨停幅度（%）。

    2026-09-11 收口：原为本模块第二份硬编码实现，且判定顺序为「ST 名称 >
    代码段」——会把创业板/科创板 ST（应 20%）与北交所 ST（应 30%）误判为 5%，
    并把 B 股 900xxx 误判为 30%（`startswith("9")` 混入）。现全部委托单点
    `app/market/price_rules.limit_pct`，与 breadth / validator / sentiment 同源。
    （导入取别名 `_rules_limit_pct`：本模块多处形参名为 limit_pct 会遮蔽同名导入。）
    """
    return _rules_limit_pct(symbol, name or None)


def seal_threshold(limit_pct: float) -> float:
    """封板判定线：pct ≥ 此值视为已封板（留 0.3pct 容差吸收 9.96%~10.09% 的取整差异）。"""
    return limit_pct - 0.3


def pre_limit_floor(limit_pct: float) -> float:
    """临板区下沿：涨停幅 × 0.65——保留 ≥35% 涨停幅的可操作跑道。"""
    return round(limit_pct * 0.65, 1)


def is_sealed(pct: float, limit_pct: float) -> bool:
    return pct >= seal_threshold(limit_pct)


def in_pre_limit_zone(pct: float, limit_pct: float) -> bool:
    return pre_limit_floor(limit_pct) <= pct < seal_threshold(limit_pct)


def radar_active_now(now: datetime | None = None) -> bool:
    """雷达活跃窗口：交易日同时段的 09:20–11:30 / 13:00–15:00。"""
    now = now or beijing_now()
    if now.weekday() >= 5:  # 周末（节假日由快照新鲜度守卫兜底）
        return False
    hhmm = now.strftime("%H:%M")
    return any(lo <= hhmm < hi for lo, hi in _ACTIVE_WINDOWS)


def select_candidates(
    snapshot_rows: list[dict], registered_symbols: set[str], *,
    reopen_symbols: set[str] | None = None,
) -> list[dict]:
    """纯函数：从全市场快照选出「涨停前临板」候选（供雷达提醒+入册）。

    规则（KB-DEC-011）：未封板 + 进入临板区 + 当日未登记。
    已封板 = 涨停后才发现 = 一律不入册；炸板后重回临板区的票视为新的临板信号
    （此前从未在涨停前提醒过 → 允许预警，回封尝试可操作）。

    **板块权限**（2026-09-15 用户「创业板的不进，只有主板的权限现在」）：账户只开
    沪深主板 ⇒ 非主板板块**不进候选**（既不入册也不提醒）。判据单点 =
    `tradability.is_tradable`，与此处的涨限判定（`board_limit_pct`）同源同表。
    """
    from app.picks.tradability import is_tradable

    out: list[dict] = []
    reopen_symbols = reopen_symbols or set()
    for row in snapshot_rows or []:
        symbol = str(row.get("symbol") or "")
        pct = row.get("change_pct")
        if not symbol or pct is None or (symbol in registered_symbols and symbol not in reopen_symbols):
            continue
        name = str(row.get("name") or "")
        if not is_tradable(symbol, name):
            continue  # 无交易权限的板块：提醒了也执行不了，属噪音
        limit = board_limit_pct(symbol, name)
        if is_sealed(float(pct), limit):
            continue  # 已封板——涨停后才发现，一律不入册（用户指令）
        if not in_pre_limit_zone(float(pct), limit):
            continue
        out.append(
            {
                "symbol": symbol,
                "name": name,
                "market": row.get("market"),
                "pct": float(pct),
                "limit_pct": limit,
                "runway_pct": round(seal_threshold(limit) - float(pct), 2),
                # 兼容字段 runway_pct 仍是距封板判定线的百分点差，只用于原排序。
                **limit_up_distance(row),
                "price": row.get("price"),
                "turnover_rate": row.get("turnover_rate"),
            }
        )
    out.sort(key=lambda c: c["runway_pct"])  # 距封板最近的优先
    return out


def _quote_distance(quote, candidate: dict, now: datetime) -> dict | None:
    """源身份、北京时间当日及质量均通过，才使用同一Quote的现价和实际限价。"""
    if quote is None or quote.symbol != candidate["symbol"]:
        return None
    expected_market = candidate.get("market") or to_tencent_symbol(candidate["symbol"])[:2].upper()
    if quote.market != expected_market or quote.data_timestamp is None:
        return None
    source_time = to_beijing(quote.data_timestamp)
    if source_time.date() != now.date() or source_time > now:
        return None
    if quote.freshness(fresh_within=SNAPSHOT_FRESH_SECONDS).state != "ready":
        return None
    metrics = limit_up_distance({
        "symbol": quote.symbol, "market": quote.market, "name": quote.name,
        "price": quote.price, "limit_up_price": quote.limit_up_price,
        "quality": quote.quality.value, "source": quote.source,
        "data_timestamp": source_time.isoformat(),
    }, now=now)
    return metrics if metrics["limit_up_gap_state"] == "ready" else None


def _distance_alert_text(candidate: dict) -> str:
    """距离基准独立于入选快照；事实事件仅存text也能追溯这份报价。"""
    text = limit_up_distance_text(candidate)
    if candidate.get("limit_up_gap_state") != "ready":
        return text
    return (
        f"{text}（距离基准现价 {candidate['limit_up_gap_price']:.2f}，"
        f"实际涨停价 {candidate['limit_up_price']:.2f}，"
        f"来源 {candidate.get('limit_up_gap_source') or '待核对'}，"
        f"源时间 {candidate.get('limit_up_gap_as_of') or '待核对'}）"
    )


def _distance_alert_meta(candidate: dict, snapshot_as_of: str) -> dict:
    return {
        "trigger_value": candidate.get("price"),
        "snapshot_price": candidate.get("price"), "snapshot_pct": candidate["pct"],
        "snapshot_as_of": snapshot_as_of,
        **{key: candidate.get(key) for key in (
            "limit_up_price", "limit_up_gap_pct", "limit_up_gap_state",
            "limit_up_gap_price", "limit_up_gap_source", "limit_up_gap_as_of",
        )},
    }


async def _enrich_candidate_distances(state, candidates: list[dict]) -> None:
    """只补本轮提醒候选；每30秒最多50个miss，超时后的请求真实drain前不再排队。"""
    from app.services.quote_enrich import fetch_quotes_batched

    hub = getattr(state, "hub", None)
    if hub is None or not candidates:
        return
    now = beijing_now()
    day = now.date().isoformat()
    cache = cache_on(state, "pre_limit_distance", 30.0, maxsize=256)
    by_symbol = {c["symbol"]: c for c in candidates if c.get("limit_up_gap_state") != "ready"}
    missing = []
    cached_quotes = {q.symbol: q for q in hub.get_quotes(list(by_symbol))}
    for symbol, candidate in by_symbol.items():
        quote = cached_quotes.get(symbol)
        metrics = _quote_distance(quote, candidate, now)
        if metrics is not None:
            candidate.update(metrics)
            continue
        hit, quote = cache.get((day, symbol))
        if hit:
            metrics = _quote_distance(quote, candidate, now)
            if metrics is not None:
                candidate.update(metrics)
        else:
            missing.append(symbol)
    running = getattr(state, "_pre_limit_distance_inflight", None)
    gate_hit, _ = cache.get((day, "__batch__"))
    if not missing or running is not None or gate_hit:
        return
    symbols = missing[:DISTANCE_BATCH_SIZE]
    requested = {symbol: by_symbol[symbol] for symbol in symbols}
    cache.set((day, "__batch__"), True)
    task = asyncio.create_task(fetch_quotes_batched(hub, symbols, prefer_cache=False, batch_size=DISTANCE_BATCH_SIZE))
    state._pre_limit_distance_inflight = task

    def publish(done):
        if getattr(state, "_pre_limit_distance_inflight", None) is not done:
            return
        found = {} if done.cancelled() else done.result() if done.exception() is None else {}
        checked_at = beijing_now()
        for symbol in symbols:
            quote = found.get(symbol)
            valid = _quote_distance(quote, requested[symbol], checked_at)
            cache.set((day, symbol), quote.model_copy(deep=True) if valid is not None else None)
        state._pre_limit_distance_inflight = None

    task.add_done_callback(publish)
    done, _pending = await asyncio.wait({task}, timeout=DISTANCE_FETCH_TIMEOUT)
    if done:
        publish(task)
        for symbol, candidate in requested.items():
            _hit, quote = cache.get((day, symbol))
            metrics = _quote_distance(quote, candidate, beijing_now())
            if metrics is not None:
                candidate.update(metrics)


async def pre_limit_sweep(app) -> int:
    """单轮扫描：选候选 → 先登记（台账）→ 再提醒（当日简报 alerts[]；不进通知中心）。"""
    state = app.state if hasattr(app, "state") else app
    svc = getattr(state, "snapshot_service", None)
    rows = getattr(svc, "snapshot", None) or []

    # 新鲜度守卫：非交易时段/停更时快照 last_success 停止前进——静默防误报
    last = getattr(svc, "last_success", None)
    if last is None or (datetime.now(timezone.utc) - last).total_seconds() > SNAPSHOT_FRESH_SECONDS:
        return 0
    snapshot_as_of = last.isoformat() if hasattr(last, "isoformat") else str(last)

    from app.picks.watch_ledger import get_day, record_sighting

    tdate = beijing_now().date().isoformat()
    tstamp = beijing_now().strftime("%H:%M:%S")
    day_rows = {r.get("symbol"): r for r in get_day(tdate)}
    registered = set(day_rows)
    reopenable = {
        symbol for symbol, row in day_rows.items()
        if ((row.get("reason") or {}).get("gate")) == "sealed_no_entry"
    }
    candidates = select_candidates(rows, registered, reopen_symbols=reopenable)
    await _enrich_candidate_distances(state, [c for c in candidates
        if (tdate, c["symbol"]) not in _REOPEN])

    # 特殊情形（用户指令 4）：一字板/秒板**选对但无参与机会**——首见即封板 → 只登记观察
    # （watch_no_entry，不入持仓池）；某日开板重回临板区 → 通知重新纳入（见下方 board_reopen）
    # 板块权限同 `select_candidates`（非主板不入册，否则台账会被买不了的票填满）。
    from app.picks.tradability import is_tradable

    n_watch = 0
    for row in rows:
        symbol = str(row.get("symbol") or "")
        pct = row.get("change_pct")
        if not symbol or pct is None or symbol in registered:
            continue
        if not is_tradable(symbol, str(row.get("name") or "")):
            continue
        limit = board_limit_pct(symbol, str(row.get("name") or ""))
        if not is_sealed(float(pct), limit):
            continue
        record_sighting(
            trade_date=tdate, symbol=symbol, name=str(row.get("name") or ""),
            layer="watch_no_entry", source_theme="",
            reason={"kind": "technical",  # KB-TRADE-13：首见即封板的观察行，同临板口径
                    "gate": "sealed_no_entry", "pct": float(pct),
                    "seal_state": {"ever_sealed": True, "current_sealed": True,
                                   "snapshot_state": "ready", "version": snapshot_as_of},
                    "note": "首见时当前封板——保持观察；后续若开板按新快照重评（KB-STOCK-21）"},
            entry_price=None, entry_time=tstamp,
        )
        registered.add(symbol)
        n_watch += 1

    # 开板重评：登记为 no_entry 的票回落临板区 → 通知重新纳入（每票每日一次）
    for c in candidates:
        row0 = day_rows.get(c["symbol"]) or {}
        reopen_key = (tdate, c["symbol"])
        if ((row0.get("reason") or {}).get("gate")) == "sealed_no_entry" and reopen_key not in _REOPEN:
            alert = {
                    "kind": "board_reopen", "symbol": c["symbol"], "name": c["name"],
                    "key": f"board-reopen-{c['symbol']}",
                    "direction": "开板重评",
                    "text": f"今日曾封板后当前开板回落：入选快照涨幅 {c['pct']:.1f}%，"
                            f"入选快照价 {c.get('price') if c.get('price') is not None else '待核对'}，"
                            f"快照版本时点 {snapshot_as_of}；{_distance_alert_text(c)}——重新纳入评估候选，"
                            f"仍需题材/流动性/执行条件复核，不代表保证成交",
                    "seal_state": {"ever_sealed": True, "current_sealed": False,
                                   "snapshot_state": "ready", "version": snapshot_as_of},
                    "meta": _distance_alert_meta(c, snapshot_as_of),
            }
            try:
                from app.picks.source_events import record_source_event

                event_id, _, projected = record_source_event(
                    "board_reopen", f"{tdate}:{c['symbol']}",
                    symbol=c["symbol"], name=c["name"], text=alert["text"],
                    source_id=f"watch_no_entry:{tdate}:{c['symbol']}",
                    source_version=snapshot_as_of, source_as_of=snapshot_as_of,
                    trade_date=tdate, direction="开板重评", brief_alert=alert,
                )
            except Exception:
                # Do not mark the process cache on failure. The next sweep can
                # recover the durable event or its derived brief projection.
                log.exception("[临板雷达] 开板重评事件持久化失败 %s", c["symbol"])
            else:
                if projected:
                    _REOPEN.add(reopen_key)
                log.info("[临板雷达] 开板重评 %s %s event=%s", c["symbol"], c["name"], event_id)

    if not candidates and not n_watch:
        return 0

    from app.picks.watcher import dispatch_alert

    n_new = 0
    for c in candidates:
        # 1) 先登记（首见唯一；登记成功才提醒——保证「入册的票必然已触发临板预警」）
        row = record_sighting(
            trade_date=tdate,
            symbol=c["symbol"],
            name=c["name"],
            layer="pre_limit",
            source_theme="",
            reason={
                "kind": "technical",  # KB-TRADE-13：临板雷达=封板前技术形态（与 attribution 口径一致）
                "gate": "pre_limit",
                "pct": c["pct"],
                "runway_pct": c["runway_pct"],
                "limit_pct": c["limit_pct"],
                "limit_up_price": c["limit_up_price"],
                "limit_up_gap_pct": c["limit_up_gap_pct"],
                "limit_up_gap_state": c["limit_up_gap_state"],
                "limit_up_gap_price": c["limit_up_gap_price"],
                "limit_up_gap_source": c["limit_up_gap_source"],
                "limit_up_gap_as_of": c["limit_up_gap_as_of"],
                "turnover_rate": c.get("turnover_rate"),
                "note": "涨停前预警（临板雷达，KB-DEC-011）",
            },
            is_leader=False,
            boards=0,
            entry_price=c.get("price"),
            entry_time=tstamp,
        )
        if row is None:
            continue  # 并发下已被登记
        n_new += 1
        # 2) 提醒：规则直发（append_alert → 当日简报 alerts[] → AlertEvent），
        #    不经 LLM 判读；具名个股 pre_limit 由通知中心读取，板块级不进入。
        alert = {
            "kind": "pre_limit",
            # 去重键由调用方生成（append_alert 契约）——2026-09-09 曾漏此字段导致
            # key=None 落盘，前端 key={null} 触发 React key 警告且 None 互相撞去重。
            "key": f"pre-limit-{c['symbol']}",
            "symbol": c["symbol"],
            "name": c["name"],
            "direction": "临板预警",
            "text": (
                f"临板预警：入选快照涨幅 {c['pct']:.1f}%，"
                f"入选快照价 {c.get('price') if c.get('price') is not None else '待核对'}，"
                f"快照版本时点 {snapshot_as_of}；{_distance_alert_text(c)}；"
                f"涨停幅 {c['limit_pct']:.0f}%，换手 {c.get('turnover_rate') or '--'}%——涨停前预警"
            ),
            "meta": _distance_alert_meta(c, snapshot_as_of),
        }
        try:
            await dispatch_alert(app, alert)
        except Exception:  # noqa: BLE001  提醒失败不影响登记（登记是政策关键位）
            log.exception("pre-limit alert dispatch failed: %s", c["symbol"])
    if n_new:
        log.info("[临板雷达] %d 只新入册（涨停前预警）：%s", n_new,
                 ",".join(c["symbol"] for c in candidates[:n_new]))
    return n_new


async def pre_limit_loop(app, stop: asyncio.Event) -> None:
    """常驻循环：交易时段 6s 一轮，其余 30s。停机事件受控于 lifespan。

    S2-2 收尾（09-11）：睡眠用 `wait_or_stop` 而非裸 `asyncio.sleep`——裸 sleep 时
    `stop` 只在**下一轮开头**才被看到，实测停机日志「pre-limit-radar 超过 10s 未退出，
    强制 cancel」，每次停机白烧一个宽限窗口。
    """
    from app.core.scheduler import wait_or_stop

    log.info("pre-limit radar loop started (KB-DEC-011)")
    try:
        while True:
            try:
                if stop.is_set():
                    log.info("pre-limit radar loop stop requested")
                    return
                if radar_active_now():
                    await pre_limit_sweep(app)
                    if await wait_or_stop(stop, SWEEP_INTERVAL):
                        log.info("pre-limit radar loop stop requested")
                        return
                elif await wait_or_stop(stop, IDLE_INTERVAL):
                    log.info("pre-limit radar loop stop requested")
                    return
            except asyncio.CancelledError:
                log.info("pre-limit radar loop cancelled")
                return
            except Exception:  # noqa: BLE001  单轮失败不终止雷达
                log.exception("pre-limit radar sweep failed")
                if await wait_or_stop(stop, IDLE_INTERVAL):
                    return
    finally:
        state = app.state if hasattr(app, "state") else app
        task = getattr(state, "_pre_limit_distance_inflight", None)
        if task is not None:
            # 2s只结束本轮等待，不宣称杀掉底层线程/请求；停机同样等待真实drain。
            try:
                await asyncio.shield(task)
            except (Exception, asyncio.CancelledError):
                pass
