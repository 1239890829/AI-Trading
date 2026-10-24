"""Complete-rule assessments of committed intraday selections, separate from first entry.

The existing buy-point worker owns refresh. Reads never collect data, score, notify or
trade. Assessments append to the existing opportunity evidence owner, not a new ledger.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta, timezone
import hashlib
import json
import logging
import uuid

from sqlalchemy import select

from app.core.bjtime import BJ_TZ, beijing_now, to_beijing, to_beijing_naive
from app.core.config import settings
from app.core.db import get_session_factory
from app.core.freshness import MAX_FUTURE_SKEW_SECONDS
from app.core.ttl_cache import cache_on
from app.models.opportunity_learning import OpportunityDecisionRun as Run
from app.models.opportunity_learning import OpportunityDecisionSnapshot as Snapshot
from app.paper.execution_guard import quote_issue, window_issue
from app.picks.engine import SCORING_REVISION
from app.picks.execution_qualification import _clock, _seed_issue, context_issue
from app.picks.selection_entry import CONTRACT, intraday_entries

log = logging.getLogger(__name__)
SCENARIO = "intraday_execution"
STAGE = "qualification"
PROFILE = "selected-complete-rules-v1"
FEATURE = "intraday-assessment-v1"
RETRY_SECONDS = 60


def _object(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _material(value):
    clocks = {"requested_at", "assessed_at", "judged_at", "source_as_of", "snapshot_as_of",
              "data_timestamp", "received_at", "quote_as_of", "checked_at", "age_seconds"}
    if isinstance(value, dict):
        return {k: _material(v) for k, v in value.items() if k not in clocks}
    if isinstance(value, list):
        return [_material(v) for v in value]
    return value


def _ranked(row):
    ev = _object(row.evidence)
    limit = ev.get("effective_limit")
    return (row.decision == "ranked" and row.data_state == "ready"
        and ev.get("selection_entry_contract") == CONTRACT
        and ev.get("within_display_capacity") == 1
        and type(limit) is int and limit > 0 and row.rank is not None and 1 <= row.rank <= limit)


def _sources(sf, now):
    """The newest committed canonical run wins, including unknown and empty runs."""
    day = now.date().isoformat()
    with sf() as db:
        run = db.scalar(select(Run).where(Run.trade_date == day,
            Run.scenario == "intraday_opportunity").order_by(Run.as_of.desc(), Run.created_at.desc(), Run.run_id.desc()).limit(1))
        if run is None:
            return {"state": "missing", "reason": "当日尚无已提交的盘中入选批次", "members": {}, "run_id": None}
        as_of = to_beijing(run.as_of.replace(tzinfo=BJ_TZ) if run.as_of.tzinfo is None else run.as_of)
        created = to_beijing(run.created_at.replace(tzinfo=timezone.utc) if run.created_at.tzinfo is None else run.created_at)
        rows = list(db.scalars(select(Snapshot).where(Snapshot.run_id == run.run_id,
            Snapshot.scenario == "intraday_opportunity", Snapshot.stage == "rank")
            .order_by(Snapshot.rank, Snapshot.source_theme, Snapshot.id)))
        good = (run.data_state == "ready" and run.snapshot_state == "ready"
            and as_of.date() == now.date() and created.date() == now.date()
            and (now - as_of).total_seconds() <= (settings.snapshot_save_interval_seconds
                + max(settings.snapshot_poll_interval_seconds * 3, 60.0))
            and (as_of - now).total_seconds() <= MAX_FUTURE_SKEW_SECONDS
            and (created - now).total_seconds() <= MAX_FUTURE_SKEW_SECONDS)
        membership = {"run_id": run.run_id, "as_of": as_of.isoformat(),
            "state": "ready" if good else "unknown"}
        selected = {}
        for row in rows:
            if _ranked(row):
                selected.setdefault(row.symbol, {"symbol": row.symbol, "name": row.name,
                    "theme": row.source_theme, "rank": row.rank})
        # Unknown latest input cannot resurrect older ready membership. Keeping
        # known first-entry identities exposes unknown instead of a false empty.
        if not good:
            known = list(db.scalars(select(Snapshot.symbol).where(Snapshot.trade_date == day,
                Snapshot.scenario == "intraday_opportunity", Snapshot.stage == "rank",
                Snapshot.decision == "ranked").distinct()))
            selected = {symbol: {"symbol": symbol} for symbol in known}
        entries = intraday_entries(day, selected, session_factory=sf)
        originals = list(db.scalars(select(Snapshot).where(Snapshot.trade_date == day,
            Snapshot.scenario == "intraday_opportunity", Snapshot.stage == "rank",
            Snapshot.symbol.in_(selected or [""])).order_by(Snapshot.created_at, Snapshot.id)))
        original_by = {}
        for row in originals:
            if _ranked(row):
                original_by.setdefault((row.symbol, row.run_id), row.snapshot_id)
        members = {}
        for symbol, row in selected.items():
            entry = entries.get(symbol) or {}
            version = entry.get("source_version")
            reason = None if good else "最新盘中名单来源未知；不沿用旧批次授权"
            if entry.get("state") != "recorded" or not version:
                reason = reason or "原首次入选身份或参考报价未完整归档"
            members[symbol] = {"state": "missing" if not reason else "unknown", "reason": reason,
                "selection_ref": f"intraday:{day}:{symbol}", "source_version": version,
                "reference_entry": {"price": entry.get("reference_price"), "as_of": entry.get("quote_as_of"),
                    "selected_at": entry.get("selected_at"), "source": "intraday_rank_archive",
                    "snapshot_id": original_by.get((symbol, version)), "semantics": "reference_only_not_fill"},
                "membership": deepcopy(membership), "candidate": row, "item": None, "assessment": None}
    return {**membership, "reason": None if good else "最新盘中名单来源未知", "members": members}


def _assessments(sf, now, symbols):
    if not symbols:
        return {}
    with sf() as db:
        rows = list(db.scalars(select(Snapshot).where(Snapshot.trade_date == now.date().isoformat(),
            Snapshot.scenario == SCENARIO, Snapshot.stage == STAGE, Snapshot.symbol.in_(symbols))
            .order_by(Snapshot.as_of.desc(), Snapshot.created_at.desc(), Snapshot.id.desc())))
        result = {}
        for row in rows:
            if row.symbol not in result:
                result[row.symbol] = {"payload": _object(row.evidence), "snapshot_id": row.snapshot_id,
                    "as_of": to_beijing(row.as_of.replace(tzinfo=BJ_TZ) if row.as_of.tzinfo is None else row.as_of).isoformat(),
                    "created_at": to_beijing(row.created_at.replace(tzinfo=timezone.utc)
                        if row.created_at.tzinfo is None else row.created_at)}
        return result


def load_intraday_execution_candidates(session_factory, now):
    """Read only complete assessments bound to the current committed membership."""
    now = to_beijing(now)
    source = _sources(session_factory, now)
    members = source["members"]
    latest = _assessments(session_factory, now, list(members))
    for symbol, envelope in members.items():
        saved = latest.get(symbol)
        if envelope["state"] == "unknown":
            continue
        if saved is None:
            envelope["reason"] = "尚未完成后续六维执行评估；原入选不是可执行买点"
            continue
        payload = saved["payload"]
        assessment = _object(payload.get("assessment"))
        stamp = _clock(assessment.get("assessed_at"))
        issue = None
        if (payload.get("selection_ref") != envelope["selection_ref"]
                or payload.get("source_version") != envelope["source_version"]
                or _object(payload.get("membership")).get("run_id") != source["run_id"]):
            issue = "完整评估与当前原入选/成员批次不匹配"
        elif (stamp is None or stamp.date() != now.date() or stamp > now + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS)
                or saved["created_at"] > now + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS)):
            issue = "后续评估时点无法核实"
        elif payload.get("state") != "ready":
            issue = payload.get("reason") or "后续完整评估尚未就绪"
        item = payload.get("item")
        if issue is None:
            issue = _seed_issue(item or {}, (item or {}).get("execution_seed"))
        if issue is None and (assessment.get("profile") != PROFILE
                or _object((item or {}).get("scoring_trace")).get("algorithm_version") != SCORING_REVISION
                or _object((item or {}).get("scoring_trace")).get("final_score") != (item or {}).get("score")):
            issue = "完整评分方法身份或同次计算证据无法核实"
        if issue is None and _object((item or {}).get("halt_risk")).get("available") is not True:
            issue = "异动风险缺少有效基准或历史，不能按零风险通过"
        envelope.update(state="unknown" if issue else "ready", reason=issue,
            item=deepcopy(item) if isinstance(item, dict) else None,
            assessment={**deepcopy(assessment), "snapshot_id": saved["snapshot_id"]})
    return members


def _write(sf, envelopes, now, *, attempt, state, reason=None, items=None, meta=None):
    from app.picks.opportunity_learning import archive_records

    records = []
    meta, items = meta or {}, items or {}
    for symbol, envelope in envelopes.items():
        item = items.get(symbol)
        item_reason = reason
        if state == "ready" and item is None:
            item_reason = "完整深评没有返回该入选股的评分，不能补成零分"
        item_state = "ready" if state == "ready" and item is not None else state if state != "ready" else "unknown"
        if item_reason and item_state == "ready":
            item_state = "unknown"
        material = {"selection_ref": envelope["selection_ref"], "source_version": envelope["source_version"],
            "item": _material(item), "meta": _material(meta), "state": item_state, "reason": item_reason}
        evidence = {"selection_ref": envelope["selection_ref"], "source_version": envelope["source_version"],
            "reference_entry": envelope["reference_entry"], "membership": envelope["membership"],
            "item": item, "meta": meta, "state": item_state, "reason": item_reason,
            "assessment": {"version": "IQV-" + _digest(material), "attempt": attempt,
                "requested_at": meta.get("requested_at") or now.isoformat(), "assessed_at": now.isoformat(),
                "profile": PROFILE}}
        records.append({"run_id": attempt, "trade_date": envelope["selection_ref"].split(":")[1],
            "as_of": to_beijing_naive(now), "scenario": SCENARIO, "stage": STAGE,
            "symbol": symbol, "name": str((item or envelope.get("candidate") or {}).get("name") or symbol),
            "source_theme": "", "decision": item_state, "rank": None, "strategy_version": PROFILE,
            "feature_version": FEATURE, "data_state": "ready" if item_state == "ready" else "unknown",
            "entry_price": None, "evidence": evidence,
            "created_at": now.astimezone(timezone.utc).replace(tzinfo=None)})
    if not records:
        return {"records": 0}
    return archive_records(attempt, records, session_factory=sf)


def _sf(state):
    return getattr(getattr(state, "alert_repo", None), "_session_factory", None) or get_session_factory()


async def refresh_intraday_execution(app, *, now=None):
    """One finite refresh in the existing worker, after its daily dispatch segment.

    The same membership reuses successful full scores as same-day reference inputs;
    current macro/quote/limits are still checked on every action. Failed acquisition
    can be retried after 60s, with a new observation clock and append-only evidence.
    """
    from app.services.picks_pipeline import PipelineDeps, _batch_quotes, score_selected_candidates

    state = app.state if hasattr(app, "state") else app
    now = to_beijing(now or beijing_now())
    if window_issue(now):
        return {"state": "skipped", "reason": "当前不是已确认的连续交易窗口"}
    sf = _sf(state)
    cache = cache_on(state, "picks.intraday-execution", RETRY_SECONDS, maxsize=1)
    hint = await asyncio.to_thread(_sources, sf, now)
    if hint["state"] != "ready":
        return {"state": "unknown", "reason": hint.get("reason"), "records": 0}
    lock = getattr(state, "intraday_execution_lock", None)
    if lock is None:
        lock = state.intraday_execution_lock = asyncio.Lock()

    async def refresh():
        source = await asyncio.to_thread(_sources, sf, beijing_now())
        members = {s: e for s, e in source["members"].items() if e["state"] != "unknown"}
        if source["state"] != "ready":
            return {"state": "unknown", "reason": source.get("reason"), "records": 0}
        if not members:
            return {"state": "unknown" if source["members"] else "empty", "records": 0,
                "reason": "原首次入选证据不完整" if source["members"] else None}
        old = await asyncio.to_thread(load_intraday_execution_candidates, sf, beijing_now())
        wanted = {s: e for s, e in members.items() if (old.get(s) or {}).get("state") != "ready"}
        if not wanted:
            return {"state": "reused", "records": len(members)}
        started = beijing_now()
        await asyncio.to_thread(_write, sf, wanted, started, attempt="iqp-" + uuid.uuid4().hex,
            state="evaluating", reason="正在获取完整六维与风险输入，尚无执行许可")
        try:
            quotes = await _batch_quotes(state.hub, list(wanted))
            candidates, usable, failures = [], {}, {}
            for symbol, envelope in wanted.items():
                issue = quote_issue(quotes.get(symbol), symbol, beijing_now())
                if issue:
                    failures[symbol] = issue[1]
                else:
                    candidates.append({**envelope["candidate"], "from": "intraday_selected"})
                    usable[symbol] = quotes[symbol]
            scored = await score_selected_candidates(PipelineDeps.from_state(state), state.hub,
                candidates, usable, assessed_at=started)
            completed = beijing_now()
            latest = await asyncio.to_thread(_sources, sf, completed)
            completed = beijing_now()
            meta = deepcopy(scored.get("meta") or {})
            meta.update(requested_at=started.isoformat(), assessed_at=completed.isoformat())
            issue = context_issue(meta.get("execution_context"), completed)
            if (completed.date() != started.date() or window_issue(completed)
                    or latest.get("state") != "ready" or latest.get("run_id") != source["run_id"]):
                issue = "评分期间跨日/闭窗或原成员批次已变化，未取得执行资格"
            items = {item["symbol"]: item for item in scored.get("items") or []}
            unscored = {row["symbol"]: row.get("reason") for row in scored.get("unscored") or []}
            for symbol, envelope in wanted.items():
                current = latest["members"].get(symbol)
                reason = issue or failures.get(symbol) or unscored.get(symbol)
                item = items.get(symbol)
                if item is not None and _object(item.get("halt_risk")).get("available") is not True:
                    reason = reason or "异动风险缺少有效基准或历史，不能把未测风险当作零风险通过"
                if not current or current.get("source_version") != envelope["source_version"]:
                    reason = "原盘中入选成员或首次来源已变化"
                quote_problem = quote_issue(quotes.get(symbol), symbol, completed)
                reason = reason or (quote_problem[1] if quote_problem else None)
                await asyncio.to_thread(_write, sf, {symbol: envelope}, completed,
                    attempt="iqr-" + uuid.uuid4().hex, state="unknown" if reason else "ready",
                    reason=reason, items=items, meta=meta)
            return {"state": "assessed", "records": len(wanted), "failures": len(failures)}
        except Exception as exc:
            log.exception("intraday full-rule assessment failed")
            await asyncio.to_thread(_write, sf, wanted, beijing_now(), attempt="iqf-" + uuid.uuid4().hex,
                state="unknown", reason=f"完整评分失败（{type(exc).__name__}），未借用每日资格")
            return {"state": "unknown", "reason": type(exc).__name__, "records": len(wanted)}

    async def single_writer():
        async with lock:
            return await refresh()

    # The cache coalesces identical runs; the holder lock also serializes changed
    # runs. Empty/unknown latest membership is read before any cached result.
    _, result = await cache.get_or_set((now.date().isoformat(), hint["run_id"]), single_writer)
    return deepcopy(result)


def attach_intraday_execution(data, *, session_factory=None, now=None):
    """Attach only saved subsequent evidence. No scoring or side effects in a GET."""
    now = to_beijing(now or beijing_now())
    sf = session_factory or get_session_factory()
    day = str(data.get("trade_date") or "")
    read_error = None
    try:
        candidates = load_intraday_execution_candidates(sf, now) if day == now.date().isoformat() else {}
    except Exception as exc:
        log.exception("saved intraday execution assessment read failed")
        candidates = {}
        read_error = f"执行复核证据暂不可读取（{type(exc).__name__}）"
    from app.picks.opportunity_learning import latest_notification_execution

    for theme in data.get("themes") or []:
        for stock in theme.get("participants") or []:
            symbol = str(stock.get("symbol") or "")
            fallback = read_error or ("历史原入选未做后续完整评估；不补算过去资格"
                if day != now.date().isoformat() else "尚无同版完整执行评估")
            envelope = deepcopy(candidates.get(symbol) or {
                "state": "unknown" if read_error else "missing", "reason": fallback})
            if envelope.get("state") == "ready":
                try:
                    envelope["decision"] = latest_notification_execution(day, sf,
                        selection_ref=envelope["selection_ref"], source_version=envelope["source_version"]).get(symbol)
                except Exception as exc:
                    log.exception("intraday execution decision read unavailable")
                    envelope.update(state="unknown", reason=f"最近执行判定暂不可读取（{type(exc).__name__}）")
            envelope.pop("candidate", None)
            stock["execution_qualification"] = envelope
