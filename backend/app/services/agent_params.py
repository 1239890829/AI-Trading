"""AI 控制台参数配置（方案 docs/summary/ai-evolution.md P1-B）。

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
from typing import Any

from sqlalchemy import select

from app.core.bjtime import beijing_now_naive
from app.core.db import get_session_factory
from app.models.agent import AgentParam, AgentParamChange

log = __import__("logging").getLogger(__name__)

#: 回滚归因枚举（2026-09-10 P1-15）。**封闭集合**：不允许自由文本 code——
#: 兜底成 other 会让归因统计里出现一个什么都往里扔的垃圾桶，等于没归因。
ROLLBACK_REASONS = {
    "degraded": "实验测到劣化（30 日实验自动回滚）",
    "manual": "人工判断（未说明具体原因）",
    "superseded": "被更优变更取代",
    "data_issue": "数据问题导致当初判断有误",
    "other": "其他（见备注）",
}

#: 存活率样本下限：低于此值不给"率"的可信度（样本=1 的存活率是巧合不是指标）
MIN_SURVIVAL_SAMPLES = 3

#: 早于归因功能上线（2026-09-10）的回滚行没有 rollback_reason —— 归到 unspecified。
#: 它**不是**一个可提交的 code（不在 ROLLBACK_REASONS 里），只是统计时的历史分桶，
#: 但要有中文标签，否则界面直接显示英文 code（"unspecified×1" 没人看得懂）。
_UNSPECIFIED_LABEL = "未记录归因（早于归因功能上线）"

#: 参数白名单：key → 元数据 + 校验依据
#:
#: **纳入口径（2026-09-10 P1-15 扩充，边界要写死）**：只纳「**调错了不伤本金**」的
#: 参数——选股评分/组合节奏/展示容量。**风控与资金类永久排除**（空仓闸门阈值、
#: 仓位比例、止损档位）：`gate.py` 自己写着「阈值调整须走人工确认，不自动漂移」，
#: 把它放进可自动调整的白名单就是把那条纪律绕过去。
#:
#: `type` 为 "float"/"int" 的参数走**通用标量通道**（`runtime_params` 覆盖层 +
#: 值域校验）；其余（如结构化 JSON）保留各自模块的 provider 通道。
PARAM_REGISTRY: dict[str, dict[str, Any]] = {
    "picks_style_offsets_json": {
        "label": "相位→风格权重偏移",
        "desc": "六相位对六维权重的偏移（JSON {相位: {维度: delta}}，|delta| ≤ 0.06）",
        "risk": "L1",
        "validate": None,  # 延迟绑定（避免 import 循环），见 _validate
    },
    "picks_replace_threshold": {
        "label": "换股门槛（分差）",
        "desc": "新候选综合分需超出组合内最弱者达到该分差才替换（0–100，默认 15）。"
                "⚠️ 实测：分差型门槛在分层候选池下几乎不起作用，调组合稳定性请先动「每日换股上限」",
        "risk": "L1", "type": "float", "min": 0.0, "max": 100.0,
    },
    "picks_max_swaps_per_day": {
        "label": "每日换股上限（只）",
        "desc": "每日最多换入几只（0–5 整数，默认 2）。0 = 当日冻结组合不换股",
        "risk": "L1", "type": "int", "min": 0, "max": 5,
    },
    "picks_min_pick_score": {
        "label": "入选门槛（综合分）",
        "desc": "低于该分的候选不入选、留任成员跌破即出列（0–100，默认 50 = 六维中性线）",
        "risk": "L1", "type": "float", "min": 0.0, "max": 100.0,
    },
    "picks_intraday_top_limit": {
        "label": "盘中跟踪条数上限",
        "desc": "盘中跟踪名单最多展示几只（1–20 整数，默认 8）。纯展示容量，不改筛选逻辑",
        "risk": "L0", "type": "int", "min": 1, "max": 20,
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

    meta = PARAM_REGISTRY[key]
    kind = meta.get("type")
    if kind in ("int", "float"):
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} 需要数值，收到 {value!r}") from None
        if kind == "int" and num != int(num):
            raise ValueError(f"{key} 需要整数，收到 {value!r}")
        lo, hi = meta.get("min"), meta.get("max")
        if (lo is not None and num < lo) or (hi is not None and num > hi):
            raise ValueError(f"{key} 超出值域 [{lo}, {hi}]：{num}")
        return str(int(num)) if kind == "int" else str(num)
    raise ValueError(f"参数 {key} 缺少校验实现")


def typed_value(key: str, session_factory=None) -> float | int | None:
    """当前生效值 → 标量（按注册表 `type`）。非标量参数 / 无值 → None。

    只给 `type ∈ {int,float}` 的参数用：结构化参数（JSON）走各自的 provider 通道，
    不从这里过（否则"类型契约"变成随便什么都行）。
    """
    meta = PARAM_REGISTRY.get(key) or {}
    if meta.get("type") not in ("int", "float"):
        return None
    raw = current_value(key, session_factory)
    if raw in ("", None):
        return None
    try:
        num = float(raw)
    except (TypeError, ValueError):
        log.warning("agent_params: %s 落库值不是数值（%r），按未设置处理", key, raw)
        return None
    return int(num) if meta["type"] == "int" else num


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
    """把覆盖层注入消费方，实现改参数免重启。

    两条通道，各管一类参数（不混用）：
    - **结构化参数**（style_offsets JSON）→ `style_router.set_override_provider`；
    - **标量参数**（`type ∈ {int,float}`）→ `core.runtime_params` 覆盖层，
      业务侧在自己的默认值解析点读取（如 `picks.engine.apply_replacement_threshold`）。

    只注入**库里有值**的 key：未生效的参数不写入覆盖层 ⇒ 业务侧回落代码常量。
    于是「回滚 = 删掉覆盖行」天然生效，不需要在每个消费点写回退逻辑。
    """
    from app.core import runtime_params
    from app.picks.style_router import set_override_provider

    def provider() -> str:
        return current_value("picks_style_offsets_json", session_factory)

    set_override_provider(provider)

    scalars: dict[str, Any] = {}
    for key, meta in PARAM_REGISTRY.items():
        if meta.get("type") in ("int", "float"):
            v = typed_value(key, session_factory)
            if v is not None:
                scalars[key] = v
    runtime_params.set_overrides(scalars)


def list_params(session_factory=None) -> list[dict]:
    out = []
    for key, meta in PARAM_REGISTRY.items():
        out.append({
            "key": key,
            "label": meta["label"],
            "desc": meta["desc"],
            "risk": meta["risk"],
            "kind": meta.get("type") or "json",
            "range": ({"min": meta.get("min"), "max": meta.get("max")}
                      if meta.get("type") in ("int", "float") else None),
            "current": current_value(key, session_factory),
            # 标量参数没有 settings 默认字段：默认值在消费点（代码常量），
            # 这里如实回空串而不是编一个数出来
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

    # 回滚归因：前端按 {code,note} 读。历史行可能是裸字符串 → 归一，
    # 避免详情里渲染成 undefined（归因丢失 = 存活率失去下钻维度）
    reason: dict | None = None
    if row.rollback_reason:
        parsed_reason = _j(row.rollback_reason)
        reason = parsed_reason if isinstance(parsed_reason, dict) else {
            "code": "other", "note": str(parsed_reason),
        }

    return {
        "id": row.id, "key": row.key,
        "before": _j(row.before), "after": _j(row.after),
        "source_type": row.source_type, "source_id": row.source_id,
        "evidence": _j(row.evidence),
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        "rolled_back_at": row.rolled_back_at.isoformat() if row.rolled_back_at else None,
        "rollback_reason": reason,
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
                            updated_at=beijing_now_naive()))
        row.status = "applied"
        row.applied_at = beijing_now_naive()
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
            ev["shadow_started_at"] = beijing_now_naive().isoformat(timespec="seconds")
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


def rollback_change(
    change_id: int, session_factory=None, *, reason_code: str = "manual", note: str = ""
) -> dict:
    """回滚：恢复到变更前的 before（并留一条回滚记录 + **归因**）。

    归因（2026-09-10 P1-15）：只记"回滚了"不记"为什么回滚"，存活率就只是个数字。
    `code` 必须在 `ROLLBACK_REASONS` 内（非法直接抛错，不静默落到 other——
    兜底成 other 会让归因统计里出现一个什么都往里扔的垃圾桶）。
    """
    if reason_code not in ROLLBACK_REASONS:
        raise ValueError(
            f"未知回滚归因 code={reason_code!r}（可用：{'、'.join(ROLLBACK_REASONS)}）"
        )
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
            db.merge(AgentParam(key=row.key, value=restored, updated_at=beijing_now_naive()))
        row.status = "rolled_back"
        row.rolled_back_at = beijing_now_naive()
        row.rollback_reason = json.dumps(
            {"code": reason_code, "note": note[:500]}, ensure_ascii=False
        )
        db.commit()
        db.refresh(row)
        out = _dump(row)
    refresh_runtime_overrides(sf)
    record_audit("user", "param.rollback", row.key, before=row.after, after=row.before,
                 rollback_ref=f"change:{change_id}")
    # 归因同时进审计（任务中心能按时间看到"谁因为什么回滚了什么"）
    record_audit("user", "param.rollback.reason", row.key,
                 after={"code": reason_code, "note": note[:500]},
                 rollback_ref=f"change:{change_id}")
    return out


def survival_stats(session_factory=None) -> dict:
    """变更存活率与归因分布（P1-15，2026-09-10）。

    三个数各有分工，**不可互相替代**：
    - `survival_rate` = applied / (applied + rolled_back)：**已裁决**变更里活下来的比例；
    - `still_effective`：applied 且**当前值仍等于其 after**（未被后续变更覆盖）
      ——只看 status 会把"被后来者取代"的也当成存活，那是假存活；
    - `rollback_reasons`：归因分布（回答"为什么活不下来"）。

    诚实口径：`decided` 样本 < MIN_SURVIVAL_SAMPLES(3) 时 `insufficient=True` 并给出
    note——**样本=1 的存活率不是指标，是巧合**（当前库内就是这种情况）。
    """
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(select(AgentParamChange)).scalars().all()
        current = {p.key: p.value for p in db.execute(select(AgentParam)).scalars().all()}

    applied = [r for r in rows if r.status == "applied"]
    rolled = [r for r in rows if r.status == "rolled_back"]
    decided = len(applied) + len(rolled)

    still = 0
    for r in applied:
        # 仍生效 = 该 key 当前覆盖值仍等于这条变更的 after（字符串口径，与落库一致）
        if current.get(r.key) == r.after:
            still += 1

    reasons: dict[str, int] = {}
    for r in rolled:
        code = "unspecified"
        if r.rollback_reason:
            try:
                code = (json.loads(r.rollback_reason) or {}).get("code") or "unspecified"
            except Exception:  # noqa: BLE001
                code = "unspecified"
        reasons[code] = reasons.get(code, 0) + 1

    by_key: dict[str, dict] = {}
    for r in rows:
        d = by_key.setdefault(r.key, {"applied": 0, "rolled_back": 0, "draft": 0, "shadow": 0})
        if r.status in d:
            d[r.status] += 1

    return {
        "total_changes": len(rows),
        "applied": len(applied),
        "rolled_back": len(rolled),
        "draft": len([r for r in rows if r.status == "draft"]),
        "shadow": len([r for r in rows if r.status == "shadow"]),
        "decided": decided,
        "survival_rate": round(len(applied) / decided, 3) if decided else None,
        "still_effective": still,
        "superseded": len(applied) - still,
        "rollback_reasons": reasons,
        "reason_labels": {
            **({k: v for k, v in ROLLBACK_REASONS.items() if k in reasons}),
            **({"unspecified": _UNSPECIFIED_LABEL} if "unspecified" in reasons else {}),
        },
        "by_key": by_key,
        "insufficient": decided < MIN_SURVIVAL_SAMPLES,
        "note": (
            f"已裁决变更仅 {decided} 条，样本不足（< {MIN_SURVIVAL_SAMPLES}），"
            "存活率暂不可用于判断——先积累变更再评估"
            if decided < MIN_SURVIVAL_SAMPLES
            else f"基于近 {decided} 条已裁决变更"
        ),
    }


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
