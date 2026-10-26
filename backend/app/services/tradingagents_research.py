"""Explicit, evidence-gated full TradingAgents research in the existing task runtime.

This is a consumer boundary, not another graph, task system or model router. The
native driver is supplied by the application; there is no fake fallback.
Ordinary reads never collect evidence, reserve budget or call that driver.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid
import asyncio
import logging

log = logging.getLogger(__name__)

from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.bjtime import beijing_now
from app.core.config import TRADINGAGENTS_SELECTED_MODEL, settings
from app.core.db import get_session_factory
from app.models.agent import AgentResourceUsage, AgentTask
from app.services import agent_budget
from app.events.company_state import atomic_json

ROOT = Path(__file__).resolve().parents[2] / "data" / "tradingagents_research"
REQUIRED_MODEL_SLOTS = 22
UPSTREAM_SHA = "e8f5503e9b86d5c2710b0c76b1f686703968c9df"
VERIFICATION = "unverified_full_graph_research"
MAX_FILE_BYTES = 2_500_000
ROLES = (
    "Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst",
    "Bull Researcher", "Bear Researcher", "Research Manager", "Trader",
    "Aggressive Analyst", "Conservative Analyst", "Neutral Analyst", "Portfolio Manager",
)
REPORTS = (
    "market_report", "sentiment_report", "news_report", "fundamentals_report",
    "investment_plan", "trader_investment_plan", "final_trade_decision",
)


class ResearchBlocked(RuntimeError):
    def __init__(self, blockers: list[dict]):
        self.blockers = blockers
        self.code = blockers[0]["code"] if blockers else "research_blocked"
        super().__init__("；".join(row["message"] for row in blockers))


def _now() -> datetime:
    return beijing_now()


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _clock(value) -> datetime:
    if not isinstance(value, str):
        raise ValueError("research timestamp required")
    clock = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("research timestamp must include timezone")
    return clock


def validate_params(params: dict) -> str:
    if (not isinstance(params, dict) or set(params) != {"symbol"}
            or not isinstance(params["symbol"], str) or not re.fullmatch(r"[0-9]{6}", params["symbol"])):
        raise ValueError("完整研究只接受单个六位证券代码symbol；不接受模型、提示、路径或准入参数")
    return params["symbol"]


def _read_bound(path: Path, *, limit: int = MAX_FILE_BYTES) -> tuple[dict, str]:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("invalid research artifact")
    with path.open("rb") as handle:
        body = handle.read(limit + 1)
    if len(body) > limit:
        raise ValueError("oversized research artifact")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("invalid research object")
    return value, hashlib.sha256(body).hexdigest()


def _read(path: Path) -> dict:
    return _read_bound(path)[0]


def _folder(symbol: str) -> Path:
    validate_params({"symbol": symbol})
    if ROOT.is_symlink() or (ROOT / symbol).is_symlink():
        raise ValueError("invalid research store")
    return ROOT / symbol


def _driver(app):
    return getattr(getattr(app, "state", None), "tradingagents_research_driver", None)


def configure_driver(app) -> None:
    """Bind the optional fixed native runtime without affecting baseline startup.

    Runtime paths belong to this ignored server manifest, never to an HTTP
    request. Disabling the feature performs no optional imports or disk reads.
    Method availability is separate from the pinned quality admission below.
    """
    app.state.tradingagents_research_driver = None
    if not settings.tradingagents_research_enabled:
        return
    try:
        from app.services.tradingagents_driver import driver_from_server_manifest

        app.state.tradingagents_research_driver = driver_from_server_manifest(
            ROOT / "runtime.json", enabled=True,
        )
    except Exception:
        # The ordinary reader exposes driver_unavailable, never a private path,
        # credential location or upstream exception. Other services still start.
        app.state.tradingagents_research_driver = None


def _unknown_usage_exception(value, method_sha256, session_factory=None):
    """Validate a separately explicit continuation; never rewrite the named usage."""
    keys = {"version", "user_authorization", "reason", "authorized_at", "method_sha256", "allowed_unknown_usage"}
    if (not isinstance(value, dict) or set(value) != keys or type(value["version"]) is not int
            or value["version"] != 1 or value["user_authorization"] != "EXPLICIT"
            or not isinstance(value["reason"], str) or not 0 < len(value["reason"].strip()) <= 2000
            or value["method_sha256"] != method_sha256):
        raise ValueError("unknown usage continuation authorization mismatch")
    authorized_at = _clock(value["authorized_at"])
    if authorized_at > _now():
        raise ValueError("future unknown usage continuation")
    facts = agent_budget.validate_unknown_usage_exceptions(value["allowed_unknown_usage"])
    if not facts:
        raise ValueError("named original unknown usage required")
    sf, active = session_factory or get_session_factory(), []
    with sf() as db:
        for fact in facts:
            row = db.get(AgentResourceUsage, fact["usage_id"])
            if (row is None or row.kind != "model" or row.started_at is None
                    or row.scope != fact["scope"] or row.budget_date != fact["budget_date"]
                    or row.state not in agent_budget._STARTED_OR_TERMINAL):
                raise ValueError("named original usage identity drift")
            started = row.started_at.replace(tzinfo=_now().tzinfo) if row.started_at.tzinfo is None else row.started_at
            if started > authorized_at:
                raise ValueError("usage started after its continuation authorization")
            if not row.usage_known and fact["budget_date"] == _now().date().isoformat():
                active.append(dict(fact))
    return {**value, "allowed_unknown_usage": [dict(fact) for fact in facts],
            "active_allowed_unknown_usage": active}


def _quality(app, *, session_factory=None) -> tuple[dict, list[dict]]:
    """A server-side pinned admission is required; request params cannot grant it."""
    blockers = []
    if not settings.tradingagents_research_enabled:
        blockers.append({"code": "disabled", "message": "完整TradingAgents研究尚未启用；已有报告仍可读取。"})
    driver, identity = _driver(app), None
    if driver is not None:
        try:
            identity = driver.method_identity()
            if (not isinstance(identity, dict) or identity.get("upstream_sha") != UPSTREAM_SHA
                    or not callable(getattr(driver, "run_accounted", None))):
                identity = None
            if identity is not None:
                _digest(identity)  # Reject non-JSON identities before exposing a method hash.
        except Exception:
            identity = None
    if identity is None:
        blockers.append({"code": "driver_unavailable", "message": "完整原图执行器尚未安装或版本无法核实；不会用简化流程替代。"})
    admitted = {"state": "not_admitted", "method_sha256": _digest(identity) if identity else None}
    if settings.tradingagents_research_authorization_digest:
        try:
            artifact, actual = _read_bound(ROOT / "authorization.json")
            created, until = (_clock(artifact[key]) for key in ("created_at", "valid_until"))
            transport = (identity or {}).get("transport", {})
            if (actual != settings.tradingagents_research_authorization_digest
                    or not created <= _now() < until or artifact.get("version") != 1
                    or artifact.get("mode") != "RESEARCH_ONLY_AUTHORIZED"
                    or artifact.get("consumer") != "selected_stock_explanation"
                    or artifact.get("user_authorization") != "EXPLICIT"
                    or artifact.get("net_gain_verified") is not False
                    or artifact.get("production_eligible") is not False
                    or identity is None or artifact.get("method_identity") != identity
                    or transport.get("provider") != "codex_cli"
                    or transport.get("model") != TRADINGAGENTS_SELECTED_MODEL or transport.get("effort") != "medium"):
                raise ValueError("selected research authorization mismatch")
            admitted = {"state": "research_only_authorized", "method_sha256": _digest(identity),
                        "artifact_sha256": actual, "valid_until": artifact["valid_until"],
                        "net_gain_verified": False, "production_eligible": False}
            if "unknown_usage_exception" in artifact:
                admitted["unknown_usage_exception"] = _unknown_usage_exception(
                    artifact["unknown_usage_exception"], _digest(identity), session_factory)
        except (OSError, ValueError, TypeError, KeyError, SQLAlchemyError, RuntimeError):
            blockers.append({"code": "research_not_authorized", "message": "准确版本的选股后研究授权缺失、失效或漂移；没有冒充效果准入。"})
        return admitted, blockers
    try:
        expected = settings.tradingagents_research_admission_digest
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("admission not pinned")
        path = ROOT / "admission.json"
        artifact, actual = _read_bound(path)
        if actual != expected:
            raise ValueError("admission drift")
        created, until = (_clock(artifact[key]) for key in ("created_at", "valid_until"))
        if (not created <= _now() < until
                or artifact.get("version") != 1 or artifact.get("consumer") != "explicit_stock_research"
                or artifact.get("verdict") != "VALIDATED" or artifact.get("net_gain_verified") is not True
                or identity is None or artifact.get("method_identity") != identity
                or not re.fullmatch(r"[0-9a-f]{64}", artifact.get("evaluation_sha256", ""))):
            raise ValueError("quality admission mismatch")
        admitted = {"state": "validated_exact_version", "method_sha256": _digest(identity),
                    "artifact_sha256": expected, "valid_until": artifact["valid_until"]}
    except (OSError, ValueError, TypeError, KeyError):
        blockers.append({"code": "quality_not_admitted", "message": "该准确版本尚无有效净增益准入回执；工程测试和图完成不等于质量准入。"})
    return admitted, blockers


def _allowed_unknown_usage(admission):
    return tuple(admission.get("unknown_usage_exception", {}).get("active_allowed_unknown_usage", ()))


def authorized_unknown_usage(app, *, method_sha256, authorization_sha256):
    """Recheck server authorization at each physical reserve/start boundary."""
    admission, blockers = _quality(app)
    if (blockers or admission.get("state") != "research_only_authorized"
            or admission.get("method_sha256") != method_sha256
            or admission.get("artifact_sha256") != authorization_sha256):
        raise ResearchBlocked(blockers or [{"code": "research_not_authorized",
            "message": "本图的准确方法或服务端授权已变；保留原调用，未换授权继续付费。"}])
    return _allowed_unknown_usage(admission)


def selected_bindings(session_factory=None) -> dict[str, dict]:
    """Current daily/top membership; first recorded entry identifies the paid observation.

    Theme participants, reference ladders, audit-only rows and historical notices
    do not grant this narrower consumer identity. No source or model is touched.
    """
    from app.models.daily_pick import DailyPickSet
    from app.models.opportunity_learning import OpportunityDecisionRun as Run, OpportunityDecisionSnapshot as Snapshot
    from app.picks.selection_entry import intraday_entries

    sf, now = session_factory or get_session_factory(), _now()
    day = now.date().isoformat()
    current = now.replace(tzinfo=None)
    cutoff = now.astimezone(timezone.utc).replace(tzinfo=None)
    active, first = set(), {}
    def remember(symbol, entry):
        if not isinstance(entry, dict) or entry.get("state") not in {"recorded", "partial"}:
            return
        try:
            at = _clock(entry["selected_at"])
            if (at.astimezone(now.tzinfo).date().isoformat() != day or at > now
                    or not isinstance(entry.get("source_version"), str) or not entry["source_version"]
                    or entry.get("source") not in {"daily_generation", "intraday_rank_archive"}):
                return
        except (KeyError, ValueError, TypeError):
            return
        value = {"symbol": symbol, "trade_date": day, "selected_at": at.isoformat(),
                 "source": entry.get("source"), "source_version": entry["source_version"]}
        if symbol not in first or _clock(first[symbol]["selected_at"]) > at:
            first[symbol] = value
    with sf() as db:
        daily = db.scalar(select(DailyPickSet).where(DailyPickSet.date == day, DailyPickSet.created_at <= cutoff))
        if daily:
            try:
                items, meta = json.loads(daily.items or "[]"), json.loads(daily.meta or "{}")
                if isinstance(items, list) and isinstance(meta, dict):
                    generated = _clock(meta.get("generated_at"))
                    from app.services.selection_notifications import daily_selection_version
                    if (generated <= now and generated.astimezone(now.tzinfo).date().isoformat() == day
                            and meta.get("selection_version") == daily_selection_version(day, meta["generated_at"], items)):
                        active.update(item["symbol"] for item in items if isinstance(item, dict)
                                      and isinstance(item.get("symbol"), str) and re.fullmatch(r"[0-9]{6}", item["symbol"]))
                    entries = meta.get("selection_entries", {})
                    for symbol, entry in entries.items() if isinstance(entries, dict) else []:
                        remember(symbol, entry)
            except (ValueError, TypeError):
                pass
        run = db.scalar(select(Run).where(Run.trade_date == day, Run.scenario == "intraday_opportunity",
            Run.as_of <= current).order_by(Run.as_of.desc(), Run.run_id.desc()).limit(1))
        if run and run.data_state == "ready":
            safe = case((func.json_valid(Snapshot.evidence), Snapshot.evidence), else_="{}")
            active.update(db.scalars(select(Snapshot.symbol).where(Snapshot.run_id == run.run_id,
                Snapshot.trade_date == day, Snapshot.as_of == run.as_of, Snapshot.scenario == run.scenario,
                Snapshot.stage == "rank", Snapshot.decision == "ranked", Snapshot.data_state == "ready",
                Snapshot.created_at <= cutoff, Snapshot.rank >= 1,
                func.json_extract(safe, "$.selection_entry_contract") == "selection-entry-v1",
                func.json_type(safe, "$.within_display_capacity") == "true",
                func.json_type(safe, "$.effective_limit") == "integer",
                Snapshot.rank <= func.json_extract(safe, "$.effective_limit"))).all())
    active = {symbol for symbol in active if isinstance(symbol, str) and re.fullmatch(r"[0-9]{6}", symbol)}
    for symbol, entry in intraday_entries(day, active, session_factory=sf).items():
        remember(symbol, entry)
    return {symbol: {**first[symbol], "selection_key": _digest(first[symbol])}
            for symbol in sorted(active) if symbol in first}


def preflight(symbol: str, app, *, session_factory=None) -> dict:
    """Read-only snapshot. Original reserve/start still owns concurrency admission."""
    validate_params({"symbol": symbol})
    admission, blockers = _quality(app, session_factory=session_factory)
    budget, selection = None, None
    try:
        sf = session_factory or get_session_factory()
        summary = agent_budget.budget_status(sf)
        research_only = bool(settings.tradingagents_research_authorization_digest)
        selection = selected_bindings(sf).get(symbol) if research_only else None
        if research_only:
            budget = {"date": summary["date"], "limit": None, "remaining": None,
                      "used": summary["selected_research_used"], "scope": agent_budget.SCOPE_SELECTED_RESEARCH_LLM}
            if selection is None:
                blockers.append({"code": "not_selected", "message": "本股不在今日真实入选名单或首次入选证据未记录；不对候选/参考股自动研究。"})
        remaining = max(0, summary["llm_budget"] - summary["llm_used"])
        if not research_only:
            budget = {"date": summary["date"], "limit": summary["llm_budget"],
                  "used": summary["llm_used"], "remaining": remaining,
                  "scope": agent_budget.SCOPE_AUTONOMY_LLM}
        try:
            agent_budget.ensure_model_usage_known(sf, date=summary["date"],
                **({"consumer_scope": agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
                    "allowed_unknown_usage": _allowed_unknown_usage(admission)} if research_only else {}))
        except agent_budget.BudgetError:
            blockers.append({"code": "usage_unknown", "message": "今日共享模型用量尚未核实；不得重新调用或换通道。"})
        if not research_only and remaining < REQUIRED_MODEL_SLOTS:
            blockers.append({"code": "budget_insufficient", "message": f"完整图至少需{REQUIRED_MODEL_SLOTS}次可用模型余量，当前只有{remaining}次；未启动半图。"})
    except Exception:
        blockers.append({"code": "budget_unavailable", "message": "模型预算事实暂不可读取；未将不可用当作有余额。"})
    return {"symbol": symbol, "enabled": bool(settings.tradingagents_research_enabled),
            "executable": not blockers, "blockers": blockers,
            "required_model_slots": None if settings.tradingagents_research_authorization_digest else REQUIRED_MODEL_SLOTS, "budget": budget,
            "selection": selection,
            "preflight_reserves_budget": False,
            "admission": admission, "verification": VERIFICATION, "production_eligible": False,
            "flow": {"roles": list(ROLES), "reports": list(REPORTS), "upstream_sha": UPSTREAM_SHA}}


def require_start(params: dict, app, *, session_factory=None, task_id=None) -> dict:
    state = preflight(validate_params(params), app, session_factory=session_factory)
    if task_id and state["admission"].get("state") == "research_only_authorized":
        owned = _task_selection(params["symbol"], task_id, state["admission"]["method_sha256"],
                                session_factory or get_session_factory())
        if owned:
            state["selection"], state["research_intent"] = owned
            state["blockers"] = [row for row in state["blockers"] if row["code"] != "not_selected"]
    if state["blockers"]:
        raise ResearchBlocked(state["blockers"])
    return state


def _entry_digest(binding, sf):
    """Verify the original persisted first-entry receipt, not today's membership."""
    if binding["source"] == "daily_generation":
        from app.models.daily_pick import DailyPickSet
        with sf() as db:
            row = db.scalar(select(DailyPickSet).where(DailyPickSet.date == binding["trade_date"]))
            meta = json.loads(row.meta or "{}") if row else {}
            entries = meta.get("selection_entries", {}) if isinstance(meta, dict) else {}
            entry = entries.get(binding["symbol"]) if isinstance(entries, dict) else None
    else:
        from app.picks.selection_entry import intraday_entries
        entry = intraday_entries(binding["trade_date"], [binding["symbol"]], session_factory=sf).get(binding["symbol"])
    if (not isinstance(entry, dict) or entry.get("state") not in {"recorded", "partial"}
            or _clock(entry.get("selected_at")) != _clock(binding["selected_at"])
            or any(entry.get(key) != binding[key] for key in ("source", "source_version"))):
        raise ValueError("original selection receipt unavailable or changed")
    return _digest(entry)


def _check_intent(row, sf):
    intent, binding = row.get("research_intent"), row["selection"]
    if (not isinstance(intent, dict) or set(intent) != {"version", "captured_at", "entry_sha256"}
            or type(intent.get("version")) is not int or intent["version"] != 1
            or not isinstance(intent.get("entry_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", intent["entry_sha256"])
            or not _clock(binding["selected_at"]) <= _clock(intent.get("captured_at")) <= _clock(row["updated_at"]) <= _now()
            or _entry_digest(binding, sf) != intent["entry_sha256"]):
        raise ValueError("original selection intent drift")
    return intent


def _capture_pending(binding, method, sf):
    intent = {"version": 1, "captured_at": _now().isoformat(), "entry_sha256": _entry_digest(binding, sf)}
    return _write_selection_claim(_selection_claim(binding, method), binding, method, "pending", research_intent=intent)


def _pending_bindings(method, sf):
    """Read bounded server-owned intents. No new selection or AgentTask is made."""
    result = []
    for folder in sorted(ROOT.glob("[0-9][0-9][0-9][0-9][0-9][0-9]")):
        if not folder.is_dir():
            continue
        try:
            records = _selection_records({"symbol": folder.name, "selection_key": None}, all_records=True)
            for row in records:
                if row["state"] != "pending" or row["method_sha256"] != method:
                    continue
                _check_intent(row, sf)
                same = [value for value in records if value["selection"]["selection_key"] == row["selection"]["selection_key"]]
                if len(same) == 1:
                    result.append(row["selection"])
        except (ResearchBlocked, OSError, ValueError, TypeError, KeyError):
            continue  # A damaged symbol is not treated as an absent/new paid observation.
    return sorted(result, key=lambda value: (value["selected_at"], value["symbol"]))


def _task_selection(symbol, task_id, method, sf, *, running=False):
    """Only a scheduler may first consume an already-dispatched old intent.

    Public task params remain symbol-only. A caller cannot pass a date, path or
    selection key to grant itself historical admission. Once a current-day user
    task owns a running intent, it must keep the same receipt/cancel checks too.
    """
    with sf() as db:
        task = db.get(AgentTask, task_id)
        if task is None:
            return None
        if (task.type != "tradingagents_research" or task.status not in {"queued", "running"}
                or task.cancel_requested_at is not None or json.loads(task.params) != {"symbol": symbol}):
            raise ResearchBlocked([{"code": "selection_intent_owner_drift", "message": "原入选研究任务已取消或身份改变；未重新调用。"}])
        created = task.created_at.replace(tzinfo=_now().tzinfo) if task.created_at.tzinfo is None else task.created_at
        scheduler = task.created_by == "scheduler"
    try:
        records = _selection_records({"symbol": symbol, "selection_key": None}, all_records=True)
        matches = [row for row in records if row["method_sha256"] == method and "research_intent" in row
                   and (row["state"] in {"dispatching", "queued", "running"} and row.get("task_id") in {None, task_id}
                        if scheduler else row["state"] == "running" and row.get("task_id") == task_id)]
        if not matches:
            return None  # Legacy scheduler tasks still use the original current-day gate.
        if len(matches) != 1:
            raise ValueError("ambiguous selection owner")
        row = matches[0]
        intent = _check_intent(row, sf)
        if (scheduler and (not row.get("dispatch_owner") or not _clock(intent["captured_at"]) <= created <= _now())
                or not scheduler and (task.status != "running"
                    or created.astimezone(_now().tzinfo).date().isoformat() != row["selection"]["trade_date"]
                    or not created <= _clock(row["updated_at"]) <= _now())
                or running and (row["state"] != "running" or row.get("task_id") != task_id)
                or any(other["selection"]["selection_key"] == row["selection"]["selection_key"]
                       and other["method_sha256"] != method for other in records)):
            raise ValueError("selection task owner drift")
        return row["selection"], intent
    except (OSError, ValueError, TypeError, KeyError):
        raise ResearchBlocked([{"code": "selection_intent_owner_drift", "message": "原入选研究意图或来源无法核实；未重新调用。"}]) from None


def authorized_selected_usage(app, *, method_sha256, authorization_sha256, task_id, selection, intent):
    allowed = authorized_unknown_usage(app, method_sha256=method_sha256, authorization_sha256=authorization_sha256)
    owned = _task_selection(selection["symbol"], task_id, method_sha256, get_session_factory(), running=True)
    if owned != (selection, intent):
        raise ResearchBlocked([{"code": "selection_intent_owner_drift", "message": "原入选研究归属已变或已撤回；保留原结果。"}])
    return allowed


def _latest(symbol: str) -> dict | None:
    try:
        folder = _folder(symbol)
        pointer = _read(folder / "latest.json")
        run_id = pointer["run_id"]
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
            return None
        path = folder / run_id / "result.json"
        result, actual = _read_bound(path)
        review = result.get("review_summary")
        if (result.get("review_state") not in {"unreviewed", "finite_failed_requires_review"}
                or not isinstance(review, dict)
                or any(not isinstance(review.get(key), int) or isinstance(review.get(key), bool)
                       or review[key] < 0 for key in ("finite_failures", "peer_review_required"))
                or review.get("source_verified") is not False
                or review.get("independent_human_gold") is not False
                or (result["review_state"] == "unreviewed") != (review["finite_failures"] == 0)):
            return None
        generated, as_of = (_clock(result[key]) for key in ("generated_at", "as_of"))
        if (result.get("symbol") != symbol or result.get("run_id") != run_id
                or result.get("version") != 1 or result.get("state") != "completed"
                or not as_of <= generated <= _now()
                or result.get("upstream_sha") != UPSTREAM_SHA
                or not all(re.fullmatch(r"[0-9a-f]{64}", result.get(key, ""))
                           for key in ("input_sha256", "method_sha256"))
                or pointer.get("result_sha256") != actual
                or result.get("verification") != VERIFICATION or result.get("production_eligible") is not False
                or not isinstance(result.get("reports"), dict) or set(result["reports"]) != set(REPORTS)
                or any(not isinstance(v, str) or not v.strip() for v in result["reports"].values())):
            return None
        # Only the declared public research view is exposed. A driver must not
        # accidentally turn context, request logs or host paths into API fields.
        keys = ("version", "state", "symbol", "run_id", "generated_at", "as_of", "upstream_sha",
                "input_sha256", "method_sha256", "verification", "production_eligible", "reports", "review_state")
        selected = {}
        if "authorization_mode" in result:
            binding = result.get("selection", {})
            if (result["authorization_mode"] != "RESEARCH_ONLY_AUTHORIZED"
                    or result.get("net_gain_verified") is not False
                    or not isinstance(binding, dict) or binding.get("symbol") != symbol
                    or binding.get("source") not in {"daily_generation", "intraday_rank_archive"}
                    or not isinstance(binding.get("source_version"), str) or not binding["source_version"]
                    or _clock(binding.get("selected_at")) > as_of
                    or not re.fullmatch(r"[0-9a-f]{64}", binding.get("selection_key", ""))
                    or binding["selection_key"] != _digest({key: binding[key] for key in (
                        "symbol", "trade_date", "selected_at", "source", "source_version")})):
                return None
            selected = {"authorization_mode": "RESEARCH_ONLY_AUTHORIZED", "net_gain_verified": False,
                        "selection": {key: binding[key] for key in (
                            "symbol", "trade_date", "selected_at", "source", "source_version", "selection_key")}}
        lineage = {}
        if "recovery_lineage" in result:
            from app.services.tradingagents_recovery import public_lineage, completed_recovery
            try:
                value = public_lineage(result["recovery_lineage"])
                if not selected:
                    return None
                completed_recovery(_selection_records(result["selection"]), result["selection"], result["method_sha256"])
            except ResearchBlocked:
                return None
            if value["source_run_id"] != run_id or value["recovery_method_sha256"] != result["method_sha256"]:
                return None
            lineage = {"recovery_lineage": value}
        return {**{key: result[key] for key in keys}, **selected, **lineage,
                "review_summary": {key: review[key] for key in (
                    "finite_failures", "peer_review_required", "source_verified", "independent_human_gold")},
                "generated_today": generated.astimezone(_now().tzinfo).date() == _now().date()}
    except (OSError, ValueError, TypeError, KeyError):
        return None


ROLE_FIELDS = {
    "Sentiment Analyst": ("narrative", "overall_band", "overall_score", "confidence"),
    "Research Manager": ("recommendation", "rationale", "strategic_actions"),
    "Trader": ("action", "entry_price", "position_sizing", "reasoning", "stop_loss"),
    "Portfolio Manager": ("executive_summary", "investment_thesis", "price_target", "rating", "time_horizon"),
}


def _role_projection(folder: Path, result: dict) -> tuple[list[dict], str]:
    """Project stored typed originals without changing a completed result/hash.

    Missing legacy or damaged role evidence does not destroy the independent
    seven-report reader. It is explicitly unavailable, never reconstructed.
    """
    try:
        packet = _read(folder / "packet.json")
        if _digest(packet) != result["input_sha256"]:
            raise ValueError("role packet binding drift")
        path = folder / "role-outputs.jsonl"
        if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("invalid original role file")
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if len(rows) != len(ROLES):
            raise ValueError("complete original roles unavailable")
        roles, calls = {}, set()
        for row in rows:
            peer = row["peer"]
            role, call, output = peer["role"], peer["call_id"], peer["output"]
            if (role not in ROLES or role in roles or type(call) is not int or call <= 0 or call in calls
                    or peer.get("accepted") is not True or peer.get("state") != "accepted"
                    or peer.get("case_id") != packet["case_id"] or peer.get("arm") != "ta-luna"
                    or not isinstance(output, dict) or output.get("tool_calls")
                    or _digest(output) != peer.get("output_sha256")):
                raise ValueError("original role identity drift")
            captured = _read(folder / "model-outputs" / (str(call) + ".json"))
            if (captured.get("call_id") != call or captured.get("role") != role
                    or captured.get("output_sha256") != peer["output_sha256"]
                    or captured.get("structured_output") != output):
                raise ValueError("original role capture drift")
            keys = ROLE_FIELDS.get(role, ("content",))
            fields = {key: output[key] for key in keys if key in output}
            if not fields:
                raise ValueError("role has no declared original body")
            roles[role] = {"role": role, "call_id": call, "output_sha256": peer["output_sha256"], "fields": fields}
            calls.add(call)
        return [roles[role] for role in ROLES], "available"
    except (OSError, ValueError, TypeError, KeyError):
        return [], "unavailable"


def _public_task(task):
    error = None
    if task.error:
        try:
            code = json.loads(task.error).get("code", "TaskError")
        except (ValueError, TypeError, AttributeError):
            code = "TaskError"
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", code):
            code = "TaskError"
        error = {"code": code, "message": "本轮任务未完成；已保存报告保留，具体恢复条件见启动门禁和任务中心。"}
    result_run = None
    try:
        result, params = json.loads(task.result_ref or "{}"), json.loads(task.params or "{}")
        if (task.status == "succeeded" and isinstance(result, dict) and isinstance(params, dict)
                and result.get("symbol") == params.get("symbol") and isinstance(result.get("run_id"), str)
                and re.fullmatch(r"[0-9a-f]{32}", result["run_id"])):
            result_run = result["run_id"]
    except (ValueError, TypeError):
        pass
    return {"id": task.id, "status": task.status, "result_run_id": result_run, "cancel_requested": task.cancel_requested_at is not None,
            "created_at": task.created_at.isoformat(), "finished_at": task.finished_at.isoformat() if task.finished_at else None,
            "failed": bool(task.error), "error": error}


def _read_task_states(symbols, session_factory=None):
    """One latest-per-symbol query for an existing selected-list response."""
    states = {symbol: {"task": None} for symbol in symbols}
    if not symbols:
        return states
    try:
        sf = session_factory or get_session_factory()
        safe = case((func.json_valid(AgentTask.params), AgentTask.params), else_="{}")
        symbol = func.json_extract(safe, "$.symbol")
        latest = select(AgentTask.id, symbol.label("symbol"), func.row_number().over(
            partition_by=symbol, order_by=(AgentTask.created_at.desc(), AgentTask.id.desc())).label("position")).where(
                AgentTask.type == "tradingagents_research", symbol.in_(symbols)).subquery()
        with sf() as db:
            rows = db.execute(select(AgentTask, latest.c.symbol).join(latest, latest.c.id == AgentTask.id).where(latest.c.position == 1))
            for task, identity in rows:
                states[identity] = {"task": _public_task(task)}
    except Exception:
        states = {symbol: {"task": None, "task_read_unavailable": True} for symbol in symbols}
    return states


def _read_task_state(symbol, session_factory=None):
    return _read_task_states([symbol], session_factory)[symbol]


def read_view(symbol: str, app, *, session_factory=None, saved_only=False, check_recovery=False) -> dict:
    """Ordinary paired reading. Saved-only reads do not inspect launch machinery."""
    validate_params({"symbol": symbol})
    if saved_only:
        from app.services.selected_model_lease import authorization
        auth = authorization()
        state = {"symbol": symbol, "enabled": bool(settings.tradingagents_research_enabled),
                 "executable": False, "blockers": [], "startup_conditions_not_checked": True,
                 "admission": {"state": "research_only_authorized" if auth else "not_checked",
                               "method_sha256": auth["method_sha256"] if auth else None},
                 "verification": VERIFICATION, "production_eligible": False,
                 "flow": {"roles": list(ROLES), "reports": list(REPORTS), "upstream_sha": UPSTREAM_SHA}}
    else:
        state = preflight(symbol, app, session_factory=session_factory)
    state["latest"] = _latest(symbol)
    if state["latest"] is not None:
        result = state["latest"]
        roles, projection = _role_projection(_folder(symbol) / result["run_id"], result)
        result["roles"], result["role_projection_state"] = roles, projection
    state.update(_read_task_state(symbol, session_factory))
    from app.services._tradingagents_recovery_view import projection
    state["recovery"] = projection(symbol, state["task"], app,
        session_factory=session_factory, check=check_recovery)
    return state


def read_saved_summary(symbol: str, *, session_factory=None) -> dict:
    """Small saved-result metadata for the existing selected-list response owner."""
    validate_params({"symbol": symbol})
    return _saved_summary(symbol, _read_task_state(symbol, session_factory))


def read_saved_summaries(symbols, *, session_factory=None) -> dict:
    """Batch task state once; no launch preflight or model/quote/source call."""
    symbols = list(dict.fromkeys(validate_params({"symbol": symbol}) for symbol in symbols))
    states = _read_task_states(symbols, session_factory)
    return {symbol: _saved_summary(symbol, states[symbol]) for symbol in symbols}


def attach_saved_summaries(items: list[dict], *, session_factory=None) -> list[dict]:
    symbols = [row["symbol"] for row in items if isinstance(row, dict)
               and isinstance(row.get("symbol"), str) and re.fullmatch(r"[0-9]{6}", row["symbol"])]
    summaries = read_saved_summaries(symbols, session_factory=session_factory)
    return [{**row, **({"research_summary": summaries[row["symbol"]]} if row.get("symbol") in summaries else {})}
            for row in items if isinstance(row, dict)]


def _saved_summary(symbol, task_state):
    from app.services._tradingagents_summary import read

    saved = read(symbol)
    report = saved["summary"]
    task = task_state["task"]
    state = (task["status"] if task and task["status"] in {"queued", "running", "failed", "canceled"}
             else "completed" if report else "unknown" if task or saved["state"] == "unavailable" else "none")
    result = {"symbol": symbol, "state": "unknown" if task_state.get("task_read_unavailable") else state,
              "task_state": task["status"] if task else None, "has_saved": saved["has_record"],
              "summary_state": saved["state"], "summary_reason": saved["reason"],
              "run_id": saved["run_id"],
              "generated_at": report["generated_at"] if report else None,
              "as_of": report["as_of"] if report else None,
              "report_count": report["report_count"] if report else None if saved["has_record"] else 0,
              "roles_count": None, "final_rating": None,
              "review_state": report["review_state"] if report else None,
              "role_projection_state": "unavailable", "method_compatible": None}
    if report:
        for key in ("role_projection_state", "roles_count", "final_rating"):
            result[key] = report[key]
    return result


async def _materialize_completed_summary(symbol: str, run_id: str) -> None:
    """Optional derivation after durable result/selection completion; cancellation still drains."""
    from app.services._tradingagents_summary import materialize
    from app.services.tradingagents_driver import drain_thread

    try:
        await drain_thread(materialize, symbol, write=True, expected_run_id=run_id, expected_root=ROOT)
    except Exception:
        log.exception("saved research summary unavailable; completed originals retained")


async def run_research(rec, params: dict, app) -> dict:
    """The installed native driver is the only execution path; never a LAB stub."""
    from app.services import agent_tasks

    # The handler repeats admission after queueing. A previous GET/POST snapshot
    # grants neither a model reservation nor immunity to later changes.
    await rec.checkpoint_async()
    admitted = await agent_tasks._db_thread(require_start, params, app, task_id=rec.task_id)
    await rec.checkpoint_async()
    driver = _driver(app)
    try:
        same_method = _digest(await agent_tasks._db_thread(driver.method_identity)) == admitted["admission"]["method_sha256"]
    except Exception:
        same_method = False
    if not same_method or not callable(getattr(driver, "run_accounted", None)):
        raise ResearchBlocked([{"code": "driver_unavailable",
                                "message": "完整原图的逐调用执行接口未就绪或版本已变；未调用任何模型。"}])
    # This contract must be provided by the real full-graph adapter. No fake
    # fallback, direct CLI launch or model-budget bypass exists in this module.
    if admitted["admission"]["state"] != "research_only_authorized":
        result = await driver.run_accounted(rec=rec, params=dict(params), app=app,
                                            budget=agent_budget, store_root=ROOT,
                                            required_model_slots=REQUIRED_MODEL_SLOTS,
                                            verification=VERIFICATION)
        await _materialize_completed_summary(params["symbol"], result["run_id"])
        return result
    from app.services.company_evidence import worker_lease
    binding = admitted["selection"]
    claim = _selection_claim(binding, admitted["admission"]["method_sha256"])
    # One cross-process execution owner. A queued task is not authority to
    # regenerate a paid answer or replace a failed/unknown native checkpoint.
    with worker_lease(ROOT / "selected-execution" / binding["symbol"]):
        prior = await agent_tasks._db_thread(_execution_prior, binding, admitted["admission"]["method_sha256"])
        expected_intent = dict(admitted["research_intent"]) if admitted.get("research_intent") else None
        if expected_intent is not None and (not prior or prior.get("research_intent") != expected_intent):
            raise ResearchBlocked([{"code": "selection_intent_owner_drift", "message": "原入选研究意图或来源无法核实；未重新调用。"}])
        if prior and "research_intent" in prior:
            expected_intent = dict(await agent_tasks._db_thread(_check_intent, prior, get_session_factory()))
        if prior and prior.get("state") == "completed":
            return await agent_tasks._db_thread(_completed_selection, prior, binding)
        if prior and (prior.get("state") not in {"pending", "dispatching", "queued"}
                      or prior.get("task_id") not in {None, rec.task_id}):
            raise ResearchBlocked([{"code": "selection_needs_recovery", "message": "这次真实入选已有执行或失败/未知记录；保留原任务，不能自动重新付费。"}])
        await agent_tasks._db_thread(_write_selection_claim, claim, binding, admitted["admission"]["method_sha256"],
                                     "running", task_id=rec.task_id)
        try:
            result = await driver.run_accounted(rec=rec, params=dict(params), app=app,
                budget=agent_budget, store_root=ROOT, required_model_slots=None, verification=VERIFICATION,
                selection_binding=binding,
                **({"expected_selection_intent": expected_intent} if expected_intent is not None else {}))
            # Validate the real published artifact, not just a driver's return text.
            published = await agent_tasks._db_thread(_read_bound, _folder(binding["symbol"]) / result["run_id"] / "result.json")
            if (published[0].get("state") != "completed" or published[0].get("symbol") != binding["symbol"]
                    or published[0].get("method_sha256") != admitted["admission"]["method_sha256"]
                    or published[0].get("selection") != binding
                    or published[0].get("authorization_mode") != "RESEARCH_ONLY_AUTHORIZED"
                    or published[0].get("net_gain_verified") is not False
                    or published[0].get("production_eligible") is not False):
                raise ValueError("selected result identity mismatch")
            await agent_tasks._db_thread(_write_selection_claim, claim, binding, admitted["admission"]["method_sha256"],
                "completed", task_id=rec.task_id, run_id=result["run_id"], result_sha256=published[1])
        except BaseException:
            # Use the task runner's drain discipline even when cancellation has
            # arrived; the claim prevents a later quote/date refresh from retrying.
            from app.services.tradingagents_driver import drain_thread
            await drain_thread(_write_selection_claim, claim, binding, admitted["admission"]["method_sha256"],
                               "needs_recovery", task_id=rec.task_id)
            raise
        # A derived-view failure/cancellation must not roll the completed
        # graph or its selected-execution owner back into native recovery.
        await _materialize_completed_summary(binding["symbol"], result["run_id"])
        await notify_research_ready(binding, published[0])
        return result


async def notify_research_ready(binding, published):
    """Notification failure cannot roll a persisted research result backward."""
    from app.services import agent_tasks
    from app.services.selection_workflow import record_research_ready
    try:
        await agent_tasks._db_thread(record_research_ready, selection=binding, report=published,
                                     session_factory=get_session_factory())
    except Exception:
        log.exception("completed selected research notification unavailable; result retained")


def _selection_claim(binding, method_sha):
    key = _digest({"selection_key": binding["selection_key"], "method_sha256": method_sha})
    return _folder(binding["symbol"]) / ("selection-" + key + ".json")


def _selection_records(binding, *, all_records=False):
    """Read every method's claim for one selection under the existing claim lease.

    An unreadable claim may contain a paid observation. Reject it rather than
    treating corruption, another method, or a repaired driver as a new budget.
    Valid adjacent selections remain independent. No claim is rewritten here.
    """
    try:
        folder = _folder(binding["symbol"])
        records = []
        for count, path in enumerate(folder.glob("selection-*.json"), 1):
            if count > 4096:
                raise ValueError("selection history requires bounded review")
            value = _read_bound(path, limit=16_384)[0]
            selection, method = value.get("selection"), value.get("method_sha256")
            keys = ("symbol", "trade_date", "selected_at", "source", "source_version")
            if (type(value.get("version")) is not int or value["version"] != 1
                    or not isinstance(selection, dict) or set(selection) != {*keys, "selection_key"}
                    or selection["symbol"] != binding["symbol"]
                    or selection["source"] not in {"daily_generation", "intraday_rank_archive"}
                    or not isinstance(selection["source_version"], str) or not 0 < len(selection["source_version"]) <= 512
                    or selection["selection_key"] != _digest({key: selection[key] for key in keys})
                    or not isinstance(method, str) or not re.fullmatch(r"[0-9a-f]{64}", method)
                    or path != _selection_claim(selection, method)
                    or value.get("task_id") is not None and (not isinstance(value["task_id"], str) or not 0 < len(value["task_id"]) <= 160)
                    or value.get("dispatch_owner") is not None and (not isinstance(value["dispatch_owner"], str) or not 0 < len(value["dispatch_owner"]) <= 160)
                    or value.get("state") not in {"pending", "dispatching", "queued", "started", "running", "completed",
                                                   "needs_recovery", "dispatch_unknown", "unknown", "failed", "canceled",
                                                   "recovery_dispatching", "recovery_queued", "recovery_dispatch_unknown", "recovering"}):
                raise ValueError("selection claim identity drift")
            selected_at, updated_at = _clock(selection["selected_at"]), _clock(value.get("updated_at"))
            if (selected_at.astimezone(_now().tzinfo).date().isoformat() != selection["trade_date"]
                    or not selected_at <= updated_at <= _now()):
                raise ValueError("selection claim time drift")
            if selection["selection_key"] == binding["selection_key"]:
                if selection != binding:
                    raise ValueError("selection claim binding drift")
                records.append(value)
            elif all_records:
                records.append(value)
        return records
    except (OSError, ValueError, TypeError, KeyError):
        raise ResearchBlocked([{"code": "selection_needs_recovery", "message": "原入选执行记录无法核实，保留原记录；未重新调用模型。"}]) from None


def _execution_prior(binding, method):
    from app.services.company_evidence import worker_lease
    with worker_lease(ROOT / "selection-claims"):
        records = _selection_records(binding)
        if any(row["method_sha256"] != method for row in records):
            from app.services.tradingagents_recovery import completed_recovery
            return completed_recovery(records, binding, method)
        return next(iter(records), None)


def _write_selection_claim(path, binding, method_sha, state, *, task_id=None, dispatch_owner=None, **extra):
    from app.services.company_evidence import worker_lease
    with worker_lease(ROOT / "selection-claims"):
        records = _selection_records(binding)
        prior = next((row for row in records if row["method_sha256"] == method_sha), None)
        if state == "pending":
            if records:
                return False
            if task_id or dispatch_owner or "research_intent" not in extra:
                raise ValueError("unstarted research intent required")
        elif state == "dispatching":
            if records and (len(records) != 1 or not prior or prior["state"] != "pending" or prior.get("task_id")):
                return False  # Absent-CAS: a manual execution may have won after the scheduler's read.
            if not dispatch_owner:
                raise ValueError("dispatch owner required")
        elif state in {"queued", "dispatch_unknown"}:
            if (not prior or prior.get("state") != "dispatching" or not dispatch_owner
                    or prior.get("dispatch_owner") != dispatch_owner):
                return False  # Only this unstarted dispatch can advance; never downgrade real execution.
        elif state == "running":
            if (any(row["method_sha256"] != method_sha for row in records)
                    or prior and (prior.get("state") not in {"pending", "dispatching", "queued"}
                                  or prior.get("task_id") not in {None, task_id})):
                raise ResearchBlocked([{"code": "selection_needs_recovery", "message": "这次真实入选已有执行或失败/未知记录；保留原任务，不能自动重新付费。"}])
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, {"version": 1, "selection": binding, "method_sha256": method_sha,
                           "state": state, "task_id": task_id,
                           "dispatch_owner": dispatch_owner or (prior or {}).get("dispatch_owner"),
                           **({"research_intent": prior["research_intent"]} if prior and "research_intent" in prior else {}),
                           "updated_at": _now().isoformat(), **extra})
        return True


def _completed_selection(prior, binding):
    try:
        if prior.get("selection", {}).get("selection_key") != binding["selection_key"]:
            raise ValueError("selection drift")
        run_id = prior["run_id"]
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError("invalid run")
        value, digest = _read_bound(_folder(binding["symbol"]) / run_id / "result.json")
        if (digest != prior.get("result_sha256") or value.get("symbol") != binding["symbol"]
                or value.get("state") != "completed" or value.get("method_sha256") != prior.get("method_sha256")
                or value.get("selection") != binding or value.get("net_gain_verified") is not False
                or value.get("authorization_mode") != "RESEARCH_ONLY_AUTHORIZED"
                or value.get("production_eligible") is not False
                or set(value.get("reports", {})) != set(REPORTS)):
            raise ValueError("completed artifact drift")
        if "recovery_lineage" in value:
            from app.services.tradingagents_recovery import completed_recovery
            completed_recovery(_selection_records(binding), binding, prior["method_sha256"])
        return {"kind": "tradingagents_research", "state": "completed", "symbol": binding["symbol"],
                "run_id": run_id, "reused_identical_selection": True, "production_eligible": False}
    except (OSError, ValueError, KeyError, TypeError):
        raise ResearchBlocked([{"code": "selection_needs_recovery", "message": "原入选报告无法核实，保留旧记录；未重新调用模型。"}]) from None


async def scheduler_tick(app):
    """Drain actual selected stocks through the existing bounded task runtime.

    No daily count quota and no model on reads. Durable claims retain dispatch
    uncertainty and failures; new snapshots are not new paid observations.
    """
    if not settings.tradingagents_auto_research_enabled or not settings.tradingagents_research_enabled:
        return
    from app.services import agent_tasks
    from app.services.company_evidence import worker_lease
    admission, blockers = await agent_tasks._db_thread(_quality, app)
    if blockers or admission["state"] != "research_only_authorized":
        return
    sf = get_session_factory()
    # Capture first, even while another task/dispatch lease is active. Pending
    # is an original selection intent, not a queued model task or a new-day pick.
    for binding in (await agent_tasks._db_thread(selected_bindings, sf)).values():
        try:
            await agent_tasks._db_thread(_capture_pending, binding, admission["method_sha256"], sf)
        except (ResearchBlocked, OSError, ValueError, TypeError, KeyError):
            continue
    try:
        await agent_tasks._db_thread(agent_budget.ensure_model_usage_known, sf,
            consumer_scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
            allowed_unknown_usage=_allowed_unknown_usage(admission))
    except agent_budget.BudgetError:
        return
    lease = worker_lease(ROOT / "selected-dispatch")
    try:
        lease.__enter__()
    except RuntimeError:
        return  # Another process already owns this short dispatch stage.
    try:
        capacity = max(1, min(3, int(settings.tradingagents_research_concurrency)))
        active_symbols = await agent_tasks._db_thread(_active_symbols, sf)
        if len(active_symbols) >= capacity:
            return
        for binding in await agent_tasks._db_thread(_pending_bindings, admission["method_sha256"], sf):
            if binding["symbol"] in active_symbols:
                continue
            claim = _selection_claim(binding, admission["method_sha256"])
            dispatch_owner = uuid.uuid4().hex
            try:
                owned = await agent_tasks._db_thread(_write_selection_claim, claim, binding,
                    admission["method_sha256"], "dispatching", dispatch_owner=dispatch_owner)
            except ResearchBlocked:
                continue  # An uncertain symbol cannot authorize payment; another selection may still proceed.
            if not owned:
                continue
            try:
                task = await agent_tasks.create_task_async("tradingagents_research", {"symbol": binding["symbol"]}, created_by="scheduler")
                await agent_tasks._db_thread(_write_selection_claim, claim, binding, admission["method_sha256"],
                    "queued", task_id=task["id"], dispatch_owner=dispatch_owner)
            except BaseException:
                from app.services.tradingagents_driver import drain_thread
                await drain_thread(_write_selection_claim, claim, binding, admission["method_sha256"],
                                   "dispatch_unknown", dispatch_owner=dispatch_owner)
                raise
            active_symbols.add(binding["symbol"])
            if len(active_symbols) >= capacity:
                return
    finally:
        lease.__exit__(None, None, None)


def _active_symbols(sf):
    with sf() as db:
        rows = db.scalars(select(AgentTask.params).where(AgentTask.type == "tradingagents_research",
                          AgentTask.status.in_(("queued", "running")))).all()
    symbols = set()
    for index, raw in enumerate(rows):
        try:
            symbol = json.loads(raw)["symbol"]
            if isinstance(symbol, str) and re.fullmatch(r"[0-9]{6}", symbol):
                if symbol in symbols:
                    symbols.add("duplicate-owner-" + str(index))
                symbols.add(symbol)
            else:
                return {"unknown-owner-" + str(i) for i in range(3)}
        except (ValueError, TypeError, KeyError):
            return {"unknown-owner-" + str(i) for i in range(3)}
    return symbols


def wake_selected_research(app=None) -> bool:
    """Coalesce committed selection events in the existing tracked scheduler."""
    if not settings.tradingagents_research_enabled or not settings.tradingagents_auto_research_enabled:
        return False
    if app is None:
        from app.services.agent_tasks import _APP
        app = _APP
    state = getattr(app, "state", None)
    event, loop = getattr(state, "selected_research_wake", None), getattr(state, "selected_research_loop", None)
    if event is None or loop is None or loop.is_closed():
        return False
    loop.call_soon_threadsafe(event.set)
    return True


async def selected_research_loop(app, stop, *, interval=60.0, first_delay=90.0, registry=None):
    """One registry-owned loop; no untracked dispatch tasks or shutdown list."""
    event = app.state.selected_research_wake
    app.state.selected_research_loop = asyncio.get_running_loop()
    delay = first_delay
    try:
        while not stop.is_set():
            waking = asyncio.create_task(event.wait())
            stopping = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait({waking, stopping}, timeout=delay, return_when=asyncio.FIRST_COMPLETED)
                if stopping in done or stop.is_set():
                    return
            finally:
                for task in (waking, stopping):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(waking, stopping, return_exceptions=True)
            event.clear()
            try:
                await scheduler_tick(app)
                if registry is not None:
                    registry.tick_ok("tradingagents-selected-research")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if registry is not None:
                    registry.tick_failed("tradingagents-selected-research", error)
                log.exception("selected research dispatch unavailable; original claims retained")
            delay = interval
    finally:
        app.state.selected_research_loop = None
