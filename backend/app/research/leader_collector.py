"""Bounded research capture over existing quote/event stores; no extra market feed."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time, timezone

from sqlalchemy import func, select

from app.core.bjtime import BJ_TZ, beijing_now
from app.core.db import get_session_factory
from app.core.scheduler import wait_or_stop
from app.market import trade_calendar as tc
from app.models.event import EventInterpretation, EventObservation
from app.models.theme_catalog import Theme, ThemeMember, ThemeOverride
from app.research.leader_followthrough import bj, capture, summary

log = logging.getLogger(__name__)


def load_evidence(as_of: datetime, session_factory=None) -> dict[str, list[dict]]:
    """Bind immutable event versions and current membership captured at decision time."""
    now = bj(as_of)
    sf = session_factory or get_session_factory()
    with sf() as db:
        latest = select(func.max(EventInterpretation.id)).where(
            EventInterpretation.effective_at <= now,
        ).group_by(EventInterpretation.event_id)
        pairs = db.execute(select(EventInterpretation, EventObservation).join(
            EventObservation, EventObservation.id == EventInterpretation.observation_id,
        ).where(EventInterpretation.id.in_(latest), EventInterpretation.state == "active",
                EventObservation.available_at <= now)).all()
        memberships = db.execute(select(Theme.name, ThemeMember.symbol, ThemeMember.synced_at).join(
            ThemeMember, ThemeMember.theme_code == Theme.code,
        )).all()
        overrides = db.execute(select(Theme.name, ThemeOverride).join(
            ThemeOverride, ThemeOverride.theme_code == Theme.code,
        )).all()
    by_theme = {}
    utc_now = as_of.astimezone(timezone.utc).replace(tzinfo=None) if as_of.tzinfo else now.replace(tzinfo=BJ_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    for name, symbol, synced in memberships:
        if synced is not None and synced.replace(tzinfo=None) <= utc_now:
            by_theme.setdefault(name, []).append((symbol, synced.isoformat()))
    for name, override in overrides:
        if (override.created_at > utc_now or
                (override.expires_at is not None and override.expires_at <= utc_now)):
            continue
        members = [(s, t) for s, t in by_theme.get(name, []) if s != override.symbol]
        if override.action == "include":
            members.append((override.symbol, override.created_at.isoformat()))
        by_theme[name] = members
    refs = {}
    for version, observation in pairs:
        payload = json.loads(version.payload_json)
        try:
            published = bj(datetime.fromisoformat(payload["published_at"]))
            if not 0 <= (now - published).total_seconds() < float(payload["half_life_hours"]) * 7200:
                continue
        except (ValueError, TypeError, KeyError):
            continue
        for direction in payload.get("directions", []):
            if direction.get("matched_by") == "llm_aux" or direction.get("direction") != 1:
                continue
            target = direction.get("target")
            symbols = [(target, None)] if direction.get("target_type") == "symbol" else by_theme.get(target, []) if direction.get("target_type") == "theme" else []
            for symbol, membership_time in symbols:
                refs.setdefault(symbol, []).append({
                    "event_id": version.event_id, "version_id": version.id,
                    "observation_id": observation.id, "state": version.state,
                    "published_at": published.isoformat(),
                    "source_published_at": observation.source_published_at.isoformat() if observation.source_published_at else None,
                    "available_at": max(version.effective_at, observation.available_at).isoformat(),
                    "received_at": observation.received_at.isoformat(), "direction": 1,
                    "route": "company_event" if payload.get("category") == "corporate" else "public_event",
                    "title": payload.get("title", ""), "url": payload.get("url"),
                    "fact_kind": payload.get("fact_kind"), "certainty": payload.get("certainty"),
                    "source": payload.get("source", ""), "basis": direction.get("basis", ""),
                    "target": target, "link_kind": direction.get("target_type"),
                    "membership_asof": membership_time,
                })
    return refs


async def collect(state, *, now=None, session_factory=None) -> dict:
    now = now or beijing_now()
    if (not time(9, 30) <= now.time().replace(tzinfo=None) <= time(15, 30)
            or time(11, 31) <= now.time().replace(tzinfo=None) < time(13)):
        return {"state": "outside_session", "inserted": 0}
    days = await tc.trading_days(state.hub.provider)
    if now.date() not in days:
        return {"state": "calendar_unavailable_or_closed", "inserted": 0}
    service = state.snapshot_service
    rows, source_as_of = service.versioned_snapshot()
    freshness = service.freshness()
    if freshness.state != "ready" or source_as_of is None:
        return {"state": freshness.state, "inserted": 0, "reason": freshness.reason}
    source = service.last_snapshot_source
    if not source or "mock" in source.lower():
        return {"state": "unavailable", "inserted": 0, "reason": "缺少真实来源身份"}
    # Event DB work is off the event loop. Model judgements are not called here.
    refs = await asyncio.to_thread(load_evidence, now, session_factory)
    return await asyncio.to_thread(
        capture, rows, refs, as_of=now, source_as_of=source_as_of,
        trading_days=days, source=source,
        session_factory=session_factory,
    )


async def research_loop(app, stop: asyncio.Event):
    while not stop.is_set():
        try:
            app.state.leader_research_health = {**await collect(app.state), "checked_at": beijing_now().isoformat()}
        except Exception as exc:
            app.state.leader_research_health = {"state": "error", "reason": type(exc).__name__, "checked_at": beijing_now().isoformat()}
            log.exception("leader research collection failed")
        if await wait_or_stop(stop, 60):
            return


def review_contribution(trade_date: str, session_factory=None):
    """Findings enter the existing action-item lifecycle, never auto-apply a rule."""
    from app.review.schemas import ActionItem, DataGap, DimensionResult
    result = summary(trade_date, session_factory)
    review = result["review"]
    missing = review["state"] != "observed"
    missed = review["missed"]
    dimension = DimensionResult(
        key="leader_research", title="强势候选持续研究",
        status="degraded" if missing else "ok",
        findings=[f"研究观察 {len(result['cards'])} 只；收盘对照覆盖 {review['universe_count']} 只",
                  f"收盘强势但研究未观察 {len(missed)} 只" if not missing else "缺少当日收盘普查，不能判断漏选"],
        judgements=["单日结果只产生待验证假设，不证明选股效果或因果。"],
        gaps=[DataGap(field="leader_research.closing_census", source="snapshot_service",
                      reason="该日未取得收盘快照", impact="不能判断漏选及该日后验变化")] if missing else [],
        evidence={"version": result["version"], "review": review, "outcome_basis": result["outcome_basis"]},
    )
    actions = []
    if missing or missed or review["cooled_symbols"]:
        actions.append(ActionItem(
            title="补查强势候选研究覆盖" if missing else "复核强势候选漏选入口",
            category="data" if missing else "strategy", priority="P1",
            target="research.leader_followthrough",
            evidence="收盘普查缺失" if missing else "漏选:" + ",".join(r["symbol"] for r in missed[:30]) + "; 转弱:" + ",".join(review["cooled_symbols"][:30]),
            proposed_change="检查来源/采集时点；逐只核当时已有线索并与失败者配对，冻结假设后比较下一批新样本",
            expected_impact="区分资料缺失与召回遗漏，形成可反驳的新候选条件",
        ))
    return dimension, actions
