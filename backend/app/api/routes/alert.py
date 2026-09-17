from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import require_write_token
from app.repositories.alert_repo import AlertRepository
from app.schemas.alert import (
    AlertChannelsOut,
    AlertEventOut,
    AlertRuleCreate,
    AlertRuleOut,
    AlertRuleUpdate,
)
from app.schemas.envelope import Envelope

router = APIRouter(tags=["alerts"])


def _serialize_rule(rule) -> AlertRuleOut:
    return AlertRuleOut.model_validate(rule)


def _serialize_event(event) -> AlertEventOut:
    return AlertEventOut.model_validate(event)


def get_alert_repo(request: Request) -> AlertRepository:
    return request.app.state.alert_repo


@router.get("/alerts/channels")
async def list_channels() -> Envelope[AlertChannelsOut]:
    from app.notifiers import get_notifier_registry

    registry = get_notifier_registry()
    configured = {
        name: bool(getattr(n, "is_available", lambda: True)())
        for name, n in ((ch, registry.get(ch)) for ch in registry.names())
        if n is not None
    }
    return Envelope(
        data=AlertChannelsOut(
            available=registry.names(),
            default=["in_app", "log"],
            configured=configured,
        )
    )


@router.get("/alerts/rules")
async def list_rules(repo: AlertRepository = Depends(get_alert_repo)) -> Envelope[list[AlertRuleOut]]:
    return Envelope(data=[_serialize_rule(r) for r in repo.list_rules()])


@router.post("/alerts/rules", status_code=201, dependencies=[Depends(require_write_token)])
async def create_rule(body: AlertRuleCreate, repo: AlertRepository = Depends(get_alert_repo)) -> Envelope[AlertRuleOut]:
    rule = repo.create_rule(**body.model_dump())
    return Envelope(data=_serialize_rule(rule))


@router.get("/alerts/rules/{rule_id}")
async def get_rule(rule_id: int, repo: AlertRepository = Depends(get_alert_repo)) -> Envelope[AlertRuleOut]:
    rule = repo.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    return Envelope(data=_serialize_rule(rule))


@router.put("/alerts/rules/{rule_id}", dependencies=[Depends(require_write_token)])
async def update_rule(
    rule_id: int,
    body: AlertRuleUpdate,
    repo: AlertRepository = Depends(get_alert_repo),
) -> Envelope[AlertRuleOut]:
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    rule = repo.update_rule(rule_id, **update)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    return Envelope(data=_serialize_rule(rule))


@router.delete("/alerts/rules/{rule_id}", status_code=200, dependencies=[Depends(require_write_token)])
async def delete_rule(rule_id: int, repo: AlertRepository = Depends(get_alert_repo)) -> Envelope[dict]:
    if not repo.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail="规则不存在")
    return Envelope(data={"deleted": True})


@router.get("/alerts/events")
async def list_events(
    limit: int = Query(default=50, ge=1, le=200),
    rule_id: int | None = None,
    repo: AlertRepository = Depends(get_alert_repo),
) -> Envelope[list[dict]]:
    """事件列表 + AI 判读合并（2026-09-09 告警面板重设计：verdict 徽标数据源）。"""
    # 2026-09-12 实测判定**不搬线程**：`AlertRepository.list_events(limit=50)` 中位 **0.36ms**、
    # `list_rules()` 0.13ms（对照 `EventStore.list_events` 7.2~85ms）⇒ 毫秒级，与
    # `watcher.ensure_system_rule` / `record_sighting` 同族（见 `test_event_loop_no_block.py`
    # 「已审计、刻意不搬」段）。搬线程的调度开销与收益同量级，不加。
    events = repo.list_events(limit=limit, rule_id=rule_id)
    channel_states = repo.outbox.states_for_events([e.id for e in events])
    out = []
    triage_map: dict[int, tuple[str, str, str]] = {}
    try:
        from sqlalchemy import select as _sel

        from app.core.db import get_session_factory
        from app.models.agent import AgentTriage

        ids = [e.id for e in events]
        if ids:
            with get_session_factory()() as db:
                rows = db.execute(
                    _sel(AgentTriage).where(AgentTriage.event_id.in_(ids))
                ).scalars().all()
                triage_map = {t.event_id: (t.verdict, t.reason or "", t.model or "") for t in rows}
    except Exception:  # noqa: BLE001  判读缺失 → 事件仍可展示（三态）
        pass
    for e in events:
        d = _serialize_event(e).model_dump() if hasattr(_serialize_event(e), 'model_dump') else dict(_serialize_event(e))
        tri = triage_map.get(e.id)
        # model 一并返回：`llm_fallback` 表示「AI 判读不可用、按规则提醒」，
        # 界面必须能区分（P1-36「今日已挡事件」要展示是否降级）——不伪装成 AI 判断。
        d["triage"] = {"verdict": tri[0], "reason": tri[1], "model": tri[2]} if tri else None
        # Legacy delivered_channels is an acceptance projection, never a read receipt.
        d["channel_states"] = channel_states.get(e.id, [])
        out.append(d)
    return Envelope(data=out)


@router.post("/alerts/events/{event_id}/ack", dependencies=[Depends(require_write_token)])
async def ack_event(event_id: int, repo: AlertRepository = Depends(get_alert_repo)) -> Envelope[dict]:
    if not repo.acknowledge_event(event_id):
        raise HTTPException(status_code=404, detail="事件不存在")
    return Envelope(data={"acknowledged": True})
