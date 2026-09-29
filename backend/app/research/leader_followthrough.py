"""RSH-031 forward observations. Research thresholds are not trading admission.

Source facts are frozen at capture, outcomes are derived only from later closing
censuses. This ledger owns research observations, never order or opportunity state.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import date, datetime, time, timedelta
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert

from app.core.bjtime import BJ_TZ
from app.core.db import get_session_factory
from app.models.leader_research import LeaderResearchObservation as Observation
from app.models.leader_research import LeaderResearchSession as Session
from app.market.price_rules import limit_pct

VERSION = "rsh031-followthrough-v1"
# Preregistered discovery baselines, not estimated optimal thresholds.
STRONG_PCT = 5.0
TREND_PCT = 3.0
RELATIVE_PERCENTILE = 0.98
FOLLOW_SESSIONS = 20
DISCLAIMER = "研究观察，未经选股效果准入；不构成买卖建议。"


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def bj(value: datetime) -> datetime:
    return value.astimezone(BJ_TZ).replace(tzinfo=None) if value.tzinfo else value


def number(value):
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def universe(rows: list[dict]) -> dict[str, dict]:
    """Main-board research scope. Invalid quotes are counted by the caller."""
    result = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        price, pct = number(row.get("price")), number(row.get("change_pct"))
        name = str(row.get("name") or "")
        if (len(symbol) != 6 or not symbol.isdigit()
                or not symbol.startswith(("000", "001", "002", "003", "600", "601", "603", "605"))
                or name.startswith(("N", "C")) or price is None or price <= 0 or pct is None):
            continue
        result[symbol] = {"symbol": symbol, "name": name, "price": price, "pct": pct}
    return result


def visible_refs(refs: list[dict], as_of: datetime) -> list[dict]:
    result = []
    for ref in refs:
        try:
            published = bj(datetime.fromisoformat(ref["published_at"]))
            available = bj(datetime.fromisoformat(ref["available_at"]))
        except (KeyError, ValueError, TypeError):
            continue
        if (published <= as_of and available <= as_of and ref.get("version_id")
                and ref.get("state") == "active" and ref.get("direction") == 1):
            result.append(ref)
    return sorted({dumps(r): r for r in result}.values(), key=lambda r: (r["event_id"], r["version_id"], r.get("target", "")))


def decide(row: dict, *, as_of: datetime, market_median: float,
           relative_cut: float, refs: list[dict], previous: dict | None, trend_change: float | None = None) -> dict:
    """Evaluate only the injected prefix. Price continuity is evidence, not causality."""
    pct = row["pct"]
    strong = pct >= STRONG_PCT
    relative = pct >= TREND_PCT and pct >= relative_cut and pct > market_median
    sustained = trend_change is not None and trend_change >= 10
    refs = visible_refs(refs, as_of)
    routes = sorted({r["route"] for r in refs})
    if relative or sustained:
        routes.append("trend")
    relative_since = (previous.get("relative_since") if previous and previous.get("relative") else None)
    relative_since = (relative_since or as_of.isoformat()) if relative else None
    continuity = bool(relative_since and
                      (as_of - bj(datetime.fromisoformat(relative_since))).total_seconds() >= 1800)
    # No fabricated first-board status, queue position, or fill from daily pct.
    no_entry = pct >= limit_pct(row["symbol"], row["name"], asof=as_of.date()) - 0.3
    confirmed = bool((refs and (strong or relative)) or continuity or sustained)
    state = "priority" if confirmed else "observing"
    if previous and not strong and not relative and not sustained and not refs:
        state = "weakened"
    reasons = []
    if refs:
        reasons.append("有当时已可见的正向事件关联；业务兑现仍需核实")
    if strong:
        reasons.append("当日涨幅达到研究观察线")
    if relative:
        reasons.append("相对强度进入主板截面前列")
    if continuity:
        reasons.append("不同观察时点继续相对强势（研究判据）")
    if sustained:
        reasons.append("5交易日快照形成累计强势；未复权，需核公司行动")
    if previous and previous.get("event_refs") and not refs:
        reasons.append("此前正向事件已不在当前可见有效集，需复核撤回、期限或关联变化")
    if state == "weakened":
        reasons.append("当前不再满足原观察条件，保留旧判断供复盘")
    unknowns = ["实际成交与排队状态未验", "事件直接业绩贡献未验"]
    if not refs:
        unknowns.append("未取得可见正向事件证据；不等于没有催化")
    return {
        **row, "as_of": as_of.isoformat(), "state": state, "routes": sorted(set(routes)),
        "relative": relative, "relative_since": relative_since, "strong": strong, "continuity": continuity,
        "sustained": sustained, "trend_change_pct": trend_change,
        "market_median": market_median, "relative_cut": relative_cut,
        "event_refs": refs, "no_entry": no_entry, "reasons": reasons,
        "unknowns": unknowns,
        "next_check": "观察后续相对强度、事件更正及首次可评估窗口",
        "invalidation": "事件撤回/失效、相对强度消失或跟踪期届满",
        "entry_state": "封板附近，无法假定参与" if no_entry else "需另验流动性与执行条件",
        "version": VERSION,
    }


def _latest(db, start: str, end: datetime) -> list[Observation]:
    ids = select(func.max(Observation.id)).where(
        Observation.trade_date >= start, Observation.as_of <= end,
        Observation.version == VERSION,
    ).group_by(Observation.symbol)
    return list(db.scalars(select(Observation).where(Observation.id.in_(ids))).all())


def capture(rows: list[dict], refs_by_symbol: dict[str, list[dict]], *,
            as_of: datetime, source_as_of: datetime, trading_days: list[date],
            source: str, session_factory=None) -> dict:
    """Atomic transition writes; same source replay/late inputs cannot rewrite history."""
    now, source_time = bj(as_of), bj(source_as_of)
    days = sorted(set(d for d in trading_days if d <= now.date()))
    if now.date() not in days or not time(9, 30) <= now.time() <= time(15, 30):
        return {"state": "outside_session", "inserted": 0}
    if time(11, 31) <= now.time() < time(13):
        return {"state": "outside_session", "inserted": 0}
    age = (now - source_time).total_seconds()
    if source_time.date() != now.date() or not 0 <= age <= 180:
        return {"state": "stale", "inserted": 0}
    prices = universe(rows)
    if not prices:
        return {"state": "unavailable", "inserted": 0}
    ordered = sorted(r["pct"] for r in prices.values())
    market_median = median(ordered)
    relative_cut = ordered[min(len(ordered) - 1, math.ceil(len(ordered) * RELATIVE_PERCENTILE) - 1)]
    start = days[max(0, len(days) - FOLLOW_SESSIONS - 1)].isoformat()
    sf = session_factory or get_session_factory()
    inserted = 0
    closing = now.time() >= time(15) and source_time.time() >= time(15)
    with sf.begin() as db:
        previous = {r.symbol: r for r in _latest(db, start, now)}
        # Closing census is a result-only observation; never retrospectively create intraday picks.
        if closing:
            payload = {"source": source, "source_as_of": source_time.isoformat(),
                       "universe": prices, "input_rows": len(rows), "valid_rows": len(prices),
                       "trading_days": [d.isoformat() for d in sorted(set(trading_days))],
                       "basis": "unadjusted_closing_snapshot_proxy"}
            db.execute(insert(Session).values(
                trade_date=now.date().isoformat(), as_of=now, version=VERSION,
                payload=dumps(payload),
            ).on_conflict_do_nothing(index_elements=["trade_date"]))
            return {"state": "closing_census", "inserted": 0, "universe_count": len(prices), "source_as_of": source_time.isoformat(),
                    "as_of": now.isoformat(), "observed_symbols": sorted(prices)}
        baseline_session = db.get(Session, days[-6].isoformat()) if len(days) >= 6 else None
        baseline = json.loads(baseline_session.payload)["universe"] if baseline_session else {}
        for symbol, row in prices.items():
            prior = previous.get(symbol)
            old = json.loads(prior.payload) if prior else None
            if prior and sum(d >= prior.first_seen.date() for d in days) > FOLLOW_SESSIONS + 1:
                prior, old = None, None
            refs = refs_by_symbol.get(symbol, [])
            card = decide(row, as_of=now, market_median=market_median,
                          relative_cut=relative_cut, refs=refs, previous=old,
                          trend_change=(row["price"] / baseline[symbol]["price"] - 1) * 100
                          if symbol in baseline else None)
            if not prior and not (card["strong"] or card["relative"] or card["sustained"] or card["event_refs"]):
                continue
            if prior and source_time <= datetime.fromisoformat(old["source_as_of"]):
                continue
            signature = digest({key: card[key] for key in (
                "state", "routes", "relative", "strong", "continuity", "sustained", "event_refs", "no_entry")})
            if prior and prior.trade_date == now.date().isoformat() and prior.signature == signature:
                continue
            first = prior.first_seen if prior else now
            card.update({"first_seen": first.isoformat(), "source": source,
                         "source_as_of": source_time.isoformat(),
                         "reference_price": old["reference_price"] if old else row["price"],
                         "previous_id": prior.observation_id if prior else None})
            identity = digest({"symbol": symbol, "source_as_of": source_time.isoformat(),
                               "version": VERSION})
            result = db.execute(insert(Observation).values(
                observation_id=identity, symbol=symbol, trade_date=now.date().isoformat(),
                as_of=now, first_seen=first, version=VERSION, signature=signature, payload=dumps(card),
            ).on_conflict_do_nothing(index_elements=["observation_id"]))
            inserted += result.rowcount
    return {"state": "ready", "inserted": inserted, "universe_count": len(prices), "source_as_of": source_time.isoformat(),
                    "as_of": now.isoformat(), "observed_symbols": sorted(prices)}


def summary(trade_date: str, session_factory=None) -> dict:
    """Read-only cards, transitions and missed signals; no model, source call or mutation."""
    target = date.fromisoformat(trade_date)
    sf = session_factory or get_session_factory()
    end = datetime.combine(target, time.max)
    with sf() as db:
        sessions = list(db.scalars(select(Session).where(
            Session.trade_date <= trade_date, Session.version == VERSION,
        ).order_by(Session.trade_date.desc()).limit(FOLLOW_SESSIONS + 1)).all())
        latest_session = json.loads(sessions[0].payload) if sessions else None
        days = [d for d in (latest_session or {}).get("trading_days", []) if d <= trade_date]

        start = days[-FOLLOW_SESSIONS] if len(days) >= FOLLOW_SESSIONS else (target - timedelta(days=40)).isoformat()
        observations = _latest(db, start, end)
        changes = list(db.scalars(select(Observation).where(
            Observation.trade_date == trade_date, Observation.version == VERSION,
        ).order_by(Observation.id.desc()).limit(100)).all())
    close_by_date = {s.trade_date: json.loads(s.payload)["universe"] for s in sessions}
    today_close = close_by_date.get(trade_date)
    cards = []
    for obs in observations:
        card = json.loads(obs.payload)
        first_date = obs.first_seen.date().isoformat()
        later = [d for d in days if d >= first_date]
        outcomes = {}
        for offset in (0, 1, 3, 5, 10, 20):
            due = first_date if offset == 0 else later[offset] if first_date in days and len(later) > offset else None
            quote = close_by_date.get(due, {}).get(obs.symbol) if due else None
            outcomes[f"d{offset}"] = {
                "target_date": due, "state": "observed" if quote else "missing" if due else "unknown" if first_date not in days else "pending",
                "reference_change_pct": round((quote["price"] / card["reference_price"] - 1) * 100, 3) if quote else None,
            }
        cards.append({**card, "observation_id": obs.observation_id, "outcomes": outcomes,
                      "stale": obs.trade_date != trade_date,
                      "expired": sum(d >= first_date for d in days) > FOLLOW_SESSIONS + 1})
    seen_intraday = {c["symbol"] for c in cards if c["first_seen"][:10] <= trade_date}
    leaders = [r for r in (today_close or {}).values() if r["pct"] >= STRONG_PCT]
    # Independent multi-session screen can expose slow trends below the intraday 3% gate.
    trend_leaders = []
    baseline = close_by_date.get(days[-6], {}) if len(days) >= 6 and days[-1] == trade_date else {}
    for symbol, quote in (today_close or {}).items():
        prior = baseline.get(symbol)
        if prior and (quote["price"] / prior["price"] - 1) >= 0.10:
            trend_leaders.append({**quote, "audit_reason": "5交易日未复权快照涨幅≥10%，需排除公司行动"})
    audit_pool = {r["symbol"]: r for r in leaders + trend_leaders}
    missed = [r for r in audit_pool.values() if r["symbol"] not in seen_intraday]
    cooled = [c["symbol"] for c in cards if c["symbol"] in (today_close or {})
              and today_close[c["symbol"]]["pct"] < 0]
    cards.sort(key=lambda c: (c["stale"], c["state"] != "priority", c["symbol"]))
    return {
        "trade_date": trade_date, "version": VERSION,
        "state": "ready" if cards else "collected_empty" if today_close is not None else "not_collected", "cards": cards,
        "changes": [json.loads(c.payload) for c in changes],
        "review": {"state": "observed" if today_close is not None else "missing_close_census",
                   "universe_count": len(today_close) if today_close is not None else None,
                   "strong_count": len(leaders) if today_close is not None else None,
                   "missed": missed, "cooled_symbols": cooled,
                   "trend_audit_count": len(trend_leaders) if baseline else None,
                   "priority_count": sum(c["state"] == "priority" for c in cards),
                   "unknowns": dict(Counter(g for c in cards for g in c["unknowns"]))},
        "disclaimer": DISCLAIMER,
        "outcome_basis": "未复权收盘快照相对首次观察价的变化；除权等公司行动未校正，不是成交收益或策略胜率",
    }
