"""Explicit, evidence-gated full TradingAgents research in the existing task runtime.

This is a consumer boundary, not another graph, scheduler or model router. The
reviewed native driver is supplied by the application; there is no fake fallback.
Ordinary reads never collect evidence, reserve budget or call that driver.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from sqlalchemy import case, func, select

from app.core.bjtime import beijing_now
from app.core.config import settings
from app.core.db import get_session_factory
from app.models.agent import AgentTask
from app.services import agent_budget

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


def preflight(symbol: str, app, *, session_factory=None) -> dict:
    """Read-only snapshot. Original reserve/start still owns concurrency admission."""
    validate_params({"symbol": symbol})
    admission, blockers = _quality(app)
    budget = None
    try:
        sf = session_factory or get_session_factory()
        summary = agent_budget.budget_status(sf)
        remaining = max(0, summary["llm_budget"] - summary["llm_used"])
        budget = {"date": summary["date"], "limit": summary["llm_budget"],
                  "used": summary["llm_used"], "remaining": remaining,
                  "scope": agent_budget.SCOPE_AUTONOMY_LLM}
        try:
            agent_budget.ensure_model_usage_known(sf, date=summary["date"])
        except agent_budget.BudgetError:
            blockers.append({"code": "usage_unknown", "message": "今日共享模型用量尚未核实；不得重新调用或换通道。"})
        if remaining < REQUIRED_MODEL_SLOTS:
            blockers.append({"code": "budget_insufficient", "message": f"完整图至少需{REQUIRED_MODEL_SLOTS}次可用模型余量，当前只有{remaining}次；未启动半图。"})
    except Exception:
        blockers.append({"code": "budget_unavailable", "message": "模型预算事实暂不可读取；未将不可用当作有余额。"})
    return {"symbol": symbol, "enabled": bool(settings.tradingagents_research_enabled),
            "executable": not blockers, "blockers": blockers,
            "required_model_slots": REQUIRED_MODEL_SLOTS, "budget": budget,
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
        return {**{key: result[key] for key in keys},
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
        same_method = _digest(driver.method_identity()) == admitted["admission"]["method_sha256"]
    except Exception:
        same_method = False
    if not same_method or not callable(getattr(driver, "run_accounted", None)):
        raise ResearchBlocked([{"code": "driver_unavailable",
                                "message": "完整原图的逐调用执行接口未就绪或版本已变；未调用任何模型。"}])
    # This contract must be provided by the real full-graph adapter. No fake
    # fallback, direct CLI launch or model-budget bypass exists in this module.
    return await driver.run_accounted(rec=rec, params=dict(params), app=app,
                                      budget=agent_budget, store_root=ROOT,
                                      required_model_slots=REQUIRED_MODEL_SLOTS,
                                      verification=VERIFICATION)
