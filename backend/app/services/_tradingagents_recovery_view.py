"""Small task/run identities; only an explicit check reads recovery evidence."""
from __future__ import annotations

import json
import re

from sqlalchemy.exc import SQLAlchemyError

from app.models.agent import AgentTask

HEX = re.compile(r"[0-9a-f]{32}")


def projection(symbol, task, app, *, session_factory, check=False):
    from app.services import tradingagents_research as research

    out = {"state": "none", "source_task_id": None, "run_id": None, "blockers": []}
    if task is None or task.get("status") not in {"failed", "canceled"}:
        return out
    try:
        session_factory = session_factory or research.get_session_factory()
        with session_factory() as db:
            stored = db.get(AgentTask, task["id"])
            params = json.loads(stored.params) if stored is not None else None
        if not isinstance(params, dict) or params.get("symbol") != symbol:
            raise ValueError("task identity changed")
        native = params.get("native_recovery")
        if isinstance(native, dict):
            source, run_id = native.get("source_task_id"), native.get("run_id")
            if not isinstance(source, str) or not HEX.fullmatch(source) or not isinstance(run_id, str) or not HEX.fullmatch(run_id):
                raise ValueError("invalid recovery identity")
        else:
            source, found = task["id"], []
            for count, folder in enumerate(research._folder(symbol).iterdir(), 1):
                if count > 4096:
                    raise ValueError("run range exceeded")
                if not HEX.fullmatch(folder.name):
                    continue
                try:
                    execution = research._read_bound(folder / "execution.json", limit=16_384)[0]
                except FileNotFoundError:
                    continue
                if (execution.get("task_id") == source and execution.get("run_id") == folder.name
                        and execution.get("symbol") == symbol):
                    found.append(folder.name)
            if len(found) != 1:
                raise ValueError("original run identity unavailable")
            run_id = found[0]
        out.update(state="not_checked", source_task_id=source, run_id=run_id)
        if check:
            from app.services.tradingagents_recovery import _context
            _context(source, run_id, app, session_factory)
            out["state"] = "available"
        return out
    except research.ResearchBlocked as error:
        out.update(state="blocked", blockers=error.blockers)
        return out
    except (OSError, ValueError, TypeError, KeyError, AttributeError, SQLAlchemyError):
        out.update(state="unknown", blockers=[{"code": "recovery_identity_unknown",
            "message": "原任务与检查点身份未能核实；保留旧答和用量，不按证券猜测或重新生成。"}])
        return out
