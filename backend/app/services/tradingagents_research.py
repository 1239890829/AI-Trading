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

from sqlalchemy import case, func, select

from app.core.bjtime import beijing_now
from app.core.config import TRADINGAGENTS_SELECTED_MODEL, settings
from app.core.db import get_session_factory
from app.models.agent import AgentTask
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


def _read_bound(path: Path) -> tuple[dict, str]:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("invalid research artifact")
    with path.open("rb") as handle:
        body = handle.read(MAX_FILE_BYTES + 1)
    if len(body) > MAX_FILE_BYTES:
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


def _quality(app) -> tuple[dict, list[dict]]:
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
        except (OSError, ValueError, TypeError, KeyError):
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
    admission, blockers = _quality(app)
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
            agent_budget.ensure_model_usage_known(sf, date=summary["date"])
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


def require_start(params: dict, app, *, session_factory=None) -> dict:
    state = preflight(validate_params(params), app, session_factory=session_factory)
    if state["blockers"]:
        raise ResearchBlocked(state["blockers"])
    return state


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
        return {**{key: result[key] for key in keys}, **selected,
                "review_summary": {key: review[key] for key in (
                    "finite_failures", "peer_review_required", "source_verified", "independent_human_gold")},
                "generated_today": generated.astimezone(_now().tzinfo).date() == _now().date()}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def read_view(symbol: str, app, *, session_factory=None) -> dict:
    """Ordinary paired reading; no provider, writes, task creation or model calls."""
    state = preflight(symbol, app, session_factory=session_factory)
    state["latest"] = _latest(symbol)
    state["task"] = None
    try:
        sf = session_factory or get_session_factory()
        safe = case((func.json_valid(AgentTask.params), AgentTask.params), else_="{}")
        with sf() as db:
            task = db.execute(select(AgentTask).where(
                AgentTask.type == "tradingagents_research",
                func.json_extract(safe, "$.symbol") == symbol,
            ).order_by(AgentTask.created_at.desc(), AgentTask.id.desc()).limit(1)).scalar_one_or_none()
            if task is not None:
                error = None
                if task.error:
                    try:
                        code = json.loads(task.error).get("code", "TaskError")
                    except (ValueError, TypeError, AttributeError):
                        code = "TaskError"
                    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", code):
                        code = "TaskError"
                    error = {"code": code, "message": "本轮任务未完成；已保存报告保留，具体恢复条件见启动门禁和任务中心。"}
                state["task"] = {"id": task.id, "status": task.status,
                                 "cancel_requested": task.cancel_requested_at is not None,
                                 "created_at": task.created_at.isoformat(),
                                 "finished_at": task.finished_at.isoformat() if task.finished_at else None,
                                 "failed": bool(task.error), "error": error}
    except Exception:
        state["task_read_unavailable"] = True
    return state


async def run_research(rec, params: dict, app) -> dict:
    """The installed native driver is the only execution path; never a LAB stub."""
    from app.services import agent_tasks

    # The handler repeats admission after queueing. A previous GET/POST snapshot
    # grants neither a model reservation nor immunity to later changes.
    await rec.checkpoint_async()
    admitted = await agent_tasks._db_thread(require_start, params, app)
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
        return await driver.run_accounted(rec=rec, params=dict(params), app=app,
                                          budget=agent_budget, store_root=ROOT,
                                          required_model_slots=REQUIRED_MODEL_SLOTS,
                                          verification=VERIFICATION)
    from app.services.company_evidence import worker_lease
    binding = admitted["selection"]
    claim = _selection_claim(binding, admitted["admission"]["method_sha256"])
    # One cross-process execution owner. A queued task is not authority to
    # regenerate a paid answer or replace a failed/unknown native checkpoint.
    with worker_lease(ROOT / "selected-execution"):
        prior = await agent_tasks._db_thread(lambda: _read(claim) if claim.exists() else None)
        if prior and prior.get("state") == "completed":
            return await agent_tasks._db_thread(_completed_selection, prior, binding)
        if prior and (prior.get("state") not in {"dispatching", "queued"}
                      or prior.get("task_id") not in {None, rec.task_id}):
            raise ResearchBlocked([{"code": "selection_needs_recovery", "message": "这次真实入选已有执行或失败/未知记录；保留原任务，不能自动重新付费。"}])
        await agent_tasks._db_thread(_write_selection_claim, claim, binding, admitted["admission"]["method_sha256"],
                                     "running", task_id=rec.task_id)
        try:
            result = await driver.run_accounted(rec=rec, params=dict(params), app=app,
                budget=agent_budget, store_root=ROOT, required_model_slots=None, verification=VERIFICATION,
                selection_binding=binding)
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
            return result
        except BaseException:
            # Use the task runner's drain discipline even when cancellation has
            # arrived; the claim prevents a later quote/date refresh from retrying.
            from app.services.tradingagents_driver import drain_thread
            await drain_thread(_write_selection_claim, claim, binding, admitted["admission"]["method_sha256"],
                               "needs_recovery", task_id=rec.task_id)
            raise


def _selection_claim(binding, method_sha):
    key = _digest({"selection_key": binding["selection_key"], "method_sha256": method_sha})
    return _folder(binding["symbol"]) / ("selection-" + key + ".json")


def _write_selection_claim(path, binding, method_sha, state, *, task_id=None, dispatch_owner=None, **extra):
    from app.services.company_evidence import worker_lease
    with worker_lease(ROOT / "selection-claims"):
        prior = _read(path) if path.exists() else None
        if state == "dispatching":
            if prior is not None:
                return False  # Absent-CAS: a manual execution may have won after the scheduler's read.
            if not dispatch_owner:
                raise ValueError("dispatch owner required")
        elif state in {"queued", "dispatch_unknown"}:
            if (not prior or prior.get("state") != "dispatching" or not dispatch_owner
                    or prior.get("dispatch_owner") != dispatch_owner):
                return False  # Only this unstarted dispatch can advance; never downgrade real execution.
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, {"version": 1, "selection": binding, "method_sha256": method_sha,
                           "state": state, "task_id": task_id,
                           "dispatch_owner": dispatch_owner or (prior or {}).get("dispatch_owner"),
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
        return {"kind": "tradingagents_research", "state": "completed", "symbol": binding["symbol"],
                "run_id": run_id, "reused_identical_selection": True, "production_eligible": False}
    except (OSError, ValueError, KeyError, TypeError):
        raise ResearchBlocked([{"code": "selection_needs_recovery", "message": "原入选报告无法核实，保留旧记录；未重新调用模型。"}]) from None


async def scheduler_tick(app):
    """Drain actual selected stocks through the existing single task runtime.

    No daily count quota and no model on reads. Durable claims retain dispatch
    uncertainty and failures; new snapshots are not new paid observations.
    """
    from app.services import agent_tasks
    from app.services.company_evidence import worker_lease
    admission, blockers = await agent_tasks._db_thread(_quality, app)
    if blockers or admission["state"] != "research_only_authorized":
        return
    sf = get_session_factory()
    try:
        await agent_tasks._db_thread(agent_budget.ensure_model_usage_known, sf)
    except agent_budget.BudgetError:
        return
    lease = worker_lease(ROOT / "selected-dispatch")
    try:
        lease.__enter__()
    except RuntimeError:
        return  # Another process already owns this short dispatch stage.
    try:
        if await agent_tasks._db_thread(_task_active, sf):
            return
        for binding in (await agent_tasks._db_thread(selected_bindings, sf)).values():
            claim = _selection_claim(binding, admission["method_sha256"])
            if await agent_tasks._db_thread(claim.exists):
                continue
            dispatch_owner = uuid.uuid4().hex
            owned = await agent_tasks._db_thread(_write_selection_claim, claim, binding,
                admission["method_sha256"], "dispatching", dispatch_owner=dispatch_owner)
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
            return
    finally:
        lease.__exit__(None, None, None)


def _task_active(sf):
    with sf() as db:
        return bool(db.scalar(select(AgentTask.id).where(AgentTask.type == "tradingagents_research",
                            AgentTask.status.in_(("queued", "running"))).limit(1)))
