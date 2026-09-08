"""AI 控制台参数配置（方案 docs/ai-agent-console-plan.md P1-B）。

为什么需要这一层：复盘给出改进建议后，`suggest_methodology_changes()` 目前
**没有任何消费方**——建议看得到、改不了，闭环断在最后一公里。本模块把"建议
→ 变更单 → 生效 → 回滚"补上，但**开自动改的口子要非常克制**：

- 白名单：只有登记过的参数可改（首个：相位→风格权重偏移）
- 值域钳制：复用参数自身的校验（style_router.parse_overrides，越界抛错）
- 证据门槛：变更单必须带 evidence（样本天数/IC/胜率）；证据不足的只允许
  生成 draft，**不允许自动生效**（P2 量化分析接入后才评估自动转正）
- 运行时覆盖层：生效写覆盖表并注入 style_router，**改参数免重启**
  （重启才能生效的参数变更风险远大于收益）
- 回滚：每次生效都记 before，一键回滚并留审计

纪律：本模块**不自动改任何参数**；所有生效动作来自人工点确认或显式 API 调用。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.agent import AgentParam, AgentParamChange

log = __import__("logging").getLogger(__name__)

#: 参数白名单：key → {label, desc, validate(值→规范化值，非法抛 ValueError)}
PARAM_REGISTRY: dict[str, dict[str, Any]] = {
    "picks_style_offsets_json": {
        "label": "相位→风格权重偏移",
        "desc": "六相位对六维权重的偏移（JSON {相位: {维度: delta}}，|delta| ≤ 0.06）",
        "risk": "L1",
        "validate": None,  # 延迟绑定（避免 import 循环），见 _validate
    },
}


def _validate(key: str, value: Any) -> str:
    """校验并规范化参数值（非法抛 ValueError）。返回落库的字符串形式。"""
    if key not in PARAM_REGISTRY:
        raise ValueError(f"参数 {key} 不在白名单（可改：{'、'.join(PARAM_REGISTRY)}）")
    if key == "picks_style_offsets_json":
        from app.picks.style_router import parse_overrides

        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        parse_overrides(raw)  # 非法 → ValueError（维度未知/幅度越界/非对象）
        return raw
    raise ValueError(f"参数 {key} 缺少校验实现")


def _setting_default(key: str) -> str:
    from app.core.config import settings

    return str(getattr(settings, key, "") or "")


def current_value(key: str, session_factory=None) -> str:
    """当前生效值：覆盖层优先，否则静态配置默认。"""
    sf = session_factory or get_session_factory()
    with sf() as db:
        row = db.get(AgentParam, key)
        if row is not None and row.value is not None:
            return row.value
    return _setting_default(key)


def refresh_runtime_overrides(session_factory=None) -> None:
    """把覆盖层注入消费方（style_router），实现改参数免重启。"""
    from app.picks.style_router import set_override_provider

    def provider() -> str:
        return current_value("picks_style_offsets_json", session_factory)

    set_override_provider(provider)


def list_params(session_factory=None) -> list[dict]:
    out = []
    for key, meta in PARAM_REGISTRY.items():
        out.append({
            "key": key,
            "label": meta["label"],
            "desc": meta["desc"],
            "risk": meta["risk"],
            "current": current_value(key, session_factory),
            "default": _setting_default(key),
        })
    return out


def _dump(row: AgentParamChange) -> dict:
    def _j(raw: str | None) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return raw

    return {
        "id": row.id, "key": row.key,
        "before": _j(row.before), "after": _j(row.after),
        "source_type": row.source_type, "source_id": row.source_id,
        "evidence": _j(row.evidence),
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        "rolled_back_at": row.rolled_back_at.isoformat() if row.rolled_back_at else None,
        "task_id": row.task_id,
    }


def propose(key: str, after: Any, *, source_type: str = "manual", source_id: str = "",
            evidence: dict | None = None, session_factory=None) -> dict:
    """生成变更单（draft，不生效）。值域非法直接抛错——不静默收敛。"""
    sf = session_factory or get_session_factory()
    normalized = _validate(key, after)
    before = current_value(key, sf)
    if normalized == before:
        raise ValueError("新值与当前生效值相同，无需变更")
    with sf() as db:
        row = AgentParamChange(
            key=key, before=before, after=normalized,
            source_type=source_type, source_id=source_id,
            evidence=json.dumps(evidence or {}, ensure_ascii=False) if evidence else None,
            status="draft",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        out = _dump(row)
    record_audit("ai", "param.propose", key, before=before, after=normalized)
    return out


def apply_change(change_id: int, session_factory=None, *, mutation_source: str | None = None) -> dict:
    """生效：写覆盖层 + 注入运行时 + 审计。draft/applied 之外不可重复生效。

    mutation_source（2026-09-08 用户指令「改动前必须先建任务」）：人工路径传
    "user" 自动建留痕任务；A 类自动路径在外层 _execute_a 已建，传 None 跳过。
    """
    sf = session_factory or get_session_factory()
    mutation_id = None
    if mutation_source is not None:
        from app.services.agent_tasks import record_mutation

        with sf() as db:
            _row = db.get(AgentParamChange, change_id)
            _key = _row.key if _row else f"change:{change_id}"
            _after = _row.after if _row else None
        mutation_id = record_mutation(
            source=mutation_source, kind="param_change",
            summary=f"参数变更 {_key}：{json.dumps(_after, ensure_ascii=False)}",
            detail={"change_id": change_id},
        )
    with sf() as db:
        row = db.get(AgentParamChange, change_id)
        if row is None:
            if mutation_id:
                from app.services.agent_tasks import update_mutation_result
                update_mutation_result(mutation_id, "failed", "变更单不存在")
            raise ValueError("变更单不存在")
        if row.status == "applied":
            if mutation_id:
                from app.services.agent_tasks import update_mutation_result
                update_mutation_result(mutation_id, "failed", "变更单已生效（重复 apply）")
            return _dump(row)
        if row.status == "rolled_back":
            if mutation_id:
                from app.services.agent_tasks import update_mutation_result
                update_mutation_result(mutation_id, "failed", "已回滚的变更单不能再次生效（请新建变更单）")
            raise ValueError("已回滚的变更单不能再次生效（请新建变更单）")
        db.merge(AgentParam(key=row.key, value=row.after,
                            updated_at=datetime.utcnow()))
        row.status = "applied"
        row.applied_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        out = _dump(row)
    refresh_runtime_overrides(sf)
    record_audit("user", "param.apply", row.key, before=row.before, after=row.after,
                 rollback_ref=f"change:{change_id}")
    if mutation_id:
        from app.services.agent_tasks import update_mutation_result
        update_mutation_result(mutation_id, "succeeded", f"变更单 #{change_id} 已生效")
    return out


def shadow_change(change_id: int, session_factory=None) -> dict:
    """变更单进入**影子阶段**（2026-09-08 用户指令：新策略需经数据验证有效后方可启用）。

    影子状态 = 不写运行时覆盖层（provider 仍用旧值）、不生效；影子评估由
    experiments.evaluate_and_promote_shadow 在议程前执行（权重剧变检测），
    达标自动 promote（此时才真正 apply + 挂 30 日实验）。
    影子开始时间记进 evidence JSON（不改表结构）。
    """
    sf = session_factory or get_session_factory()
    with sf() as db:
        row = db.get(AgentParamChange, change_id)
        if row is None:
            raise ValueError("变更单不存在")
        if row.status not in ("draft", "shadow"):
            raise ValueError(f"仅 draft 可进入影子阶段（当前 {row.status}）")
        row.status = "shadow"
        try:
            ev = json.loads(row.evidence) if row.evidence else {}
        except Exception:  # noqa: BLE001
            ev = {}
        if "shadow_started_at" not in ev:
            ev["shadow_started_at"] = datetime.utcnow().isoformat(timespec="seconds")
        row.evidence = json.dumps(ev, ensure_ascii=False)
        db.commit()
        db.refresh(row)
        return _dump(row)


def promote_shadow(change_id: int, session_factory=None) -> dict:
    """影子达标转正：真正生效（apply_change）——由 experiments 评估器调用。"""
    out = apply_change(change_id, session_factory)
    return out


def list_shadow_changes(session_factory=None) -> list[dict]:
    """影子队列：status == shadow 的变更单（含影子开始时间）。"""
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(AgentParamChange).where(AgentParamChange.status == "shadow")
        ).scalars().all()
        out = []
        for r in rows:
            d = _dump(r)
            try:
                d["shadow_started_at"] = (json.loads(r.evidence) or {}).get("shadow_started_at")
            except Exception:  # noqa: BLE001
                d["shadow_started_at"] = None
            out.append(d)
        return out


def rollback_change(change_id: int, session_factory=None) -> dict:
    """回滚：恢复到变更前的 before（并留一条回滚记录）。"""
    sf = session_factory or get_session_factory()
    with sf() as db:
        row = db.get(AgentParamChange, change_id)
        if row is None:
            raise ValueError("变更单不存在")
        if row.status == "rolled_back":
            return _dump(row)
        restored = row.before if row.before is not None else ""
        if row.before is None:
            cur = db.get(AgentParam, row.key)
            if cur is not None:
                db.delete(cur)
        else:
            db.merge(AgentParam(key=row.key, value=restored, updated_at=datetime.utcnow()))
        row.status = "rolled_back"
        row.rolled_back_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        out = _dump(row)
    refresh_runtime_overrides(sf)
    record_audit("user", "param.rollback", row.key, before=row.after, after=row.before,
                 rollback_ref=f"change:{change_id}")
    return out


def list_changes(limit: int = 30, key: str | None = None, session_factory=None) -> list[dict]:
    sf = session_factory or get_session_factory()
    with sf() as db:
        q = select(AgentParamChange).order_by(AgentParamChange.id.desc())
        if key:
            q = q.where(AgentParamChange.key == key)
        return [_dump(r) for r in db.execute(q.limit(limit)).scalars().all()]


def record_audit(actor: str, action: str, target: str, *, before: Any = None,
                 after: Any = None, task_id: str | None = None,
                 rollback_ref: str | None = None) -> None:
    from app.services.agent_tasks import record_audit as _ra

    _ra(actor=actor, action=action, target=target, before=before, after=after,
        task_id=task_id, rollback_ref=rollback_ref)
