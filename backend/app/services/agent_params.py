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

**回滚的并发口径（R12，2026-09-14）**：`before` 采集于提案时，而生效可能发生在很久以后；
期间同 key 可能已被别的变更单改写。于是两处都要防：
- **生效时重定基线**：事务内若当前值 ≠ 提案时的 `before`，把 `before` 刷新为事务内当前值
  （`before` 的语义是"这条变更生效前的值"，不是"提案那一刻的值"）——否则回滚会抹掉中间那次变更；
- **回滚时 CAS**：仅当这条变更单**仍是当前值的拥有者**才写运行值；陈旧变更、从未生效的
  draft/shadow、以及被后来者取代的自动实验回滚，一律**只归档不改值**。

纪律：本模块**不自动改任何参数**；所有生效动作来自人工点确认或显式 API 调用。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.bjtime import beijing_now_naive, to_beijing_naive
from app.core.config import REPO_ROOT
from app.core.db import get_session_factory
from app.models.agent import AgentExperiment, AgentParam, AgentParamChange, AgentParamPromotionApproval

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

#: One-shot promotion authority must be short-lived; stale standing approvals are unsafe.
PROMOTION_APPROVAL_MAX_TTL = timedelta(hours=24)

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
    """校验并规范化参数值（非法抛 ValueError）。返回落库的字符串形式。

    R12（2026-09-14）：**非有限值先拒**。`nan` 的坑不在"数值离谱"，而在
    **Python 的有序比较对 NaN 恒为 False** —— `nan < lo` 与 `nan > hi` 都是 False
    ⇒ 上下界校验**整体失效**（不是"松一点"，是"完全没生效"）；落库成 `"nan"` 之后
    消费点的 `v < threshold` 同样恒 False ⇒ **门槛静默失效且不报错**。
    `inf` 目前碰巧被 `max` 挡住，但那依赖"每个标量参数都恰好有上界"，不能当防护。
    """
    if key not in PARAM_REGISTRY:
        raise ValueError(f"参数 {key} 不在白名单（可改：{'、'.join(PARAM_REGISTRY)}）")
    if key == "picks_style_offsets_json":
        from app.picks.style_router import parse_overrides

        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        parse_overrides(raw)  # 非法 → ValueError（维度未知/幅度越界/非对象/非有限）
        return raw

    meta = PARAM_REGISTRY[key]
    kind = meta.get("type")
    if kind in ("int", "float"):
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} 需要数值，收到 {value!r}") from None
        if not math.isfinite(num):
            raise ValueError(f"{key} 需要有限数值，收到 {value!r}（NaN/Infinity 不接受）")
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
    # R12 兜底：**已有**落库行可能来自校验加严之前（例如 "nan"）。读侧同样要挡住——
    # 一个 NaN 进覆盖层会让消费点的阈值比较恒 False，且全链路无一处报错。
    if not math.isfinite(num):
        log.warning("agent_params: %s 落库值非有限（%r），按未设置处理", key, raw)
        return None
    return int(num) if meta["type"] == "int" else num


def _setting_default(key: str) -> str:
    from app.core.config import settings

    return str(getattr(settings, key, "") or "")


def _effective_value(key: str, db) -> str:
    """事务内取当前生效值（覆盖层优先，否则静态配置默认）。**不另开 session**。

    回滚的 CAS 与生效时的基线重定都必须与写操作在**同一个事务**里读，
    否则读到的"当前值"在 commit 前就可能已经过期（这正是 R12 的原始形态）。
    """
    row = db.get(AgentParam, key)
    if row is not None and row.value is not None:
        return row.value
    return _setting_default(key)


def current_value(key: str, session_factory=None) -> str:
    """当前生效值：覆盖层优先，否则静态配置默认。"""
    sf = session_factory or get_session_factory()
    with sf() as db:
        return _effective_value(key, db)


def _active_change_id(key: str, db) -> int | None:
    """当前值的**拥有者**变更单 id；无覆盖行 / 无匹配 → None（R12）。

    判据 = 「status == applied **且** `after` 等于当前覆盖值」中 id 最大的一条。
    **刻意不是**"最后一条 applied"：覆盖行被（手工清理 / 重置）删除后，
    后者仍会认领一个已经不在生效的值，回滚于是把它**复活**回来。
    要求 `after == 当前值` 就没有这个缺口——值不在生效，就没有拥有者。
    """
    row = db.get(AgentParam, key)
    if row is None or row.value is None:
        # 覆盖层没有行 ⇒ 当前值来自静态默认，**没有任何变更单拥有它**
        return None
    cur = row.value
    ids = [
        r.id for r in db.execute(
            select(AgentParamChange).where(
                AgentParamChange.key == key,
                AgentParamChange.status == "applied",
                AgentParamChange.after == cur,
            )
        ).scalars().all()
    ]
    return max(ids) if ids else None


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


def _manual_apply_reason(row: AgentParamChange) -> str | None:
    """Manual configuration is distinct from approving a researched candidate.

    Payload flags and mutation_source are attribution, not approval evidence.
    The existing authenticated manual-config route remains available; this is
    not principal isolation against a caller who already owns its write token.
    """
    if row.status != "draft":
        return f"当前状态 {row.status} 不允许直接生效；影子/拒绝项须独立审查"
    if row.source_type != "manual":
        return "非人工草稿缺少可独立核验的晋级批准与效果证据，保留待审"
    try:
        evidence = json.loads(row.evidence) if row.evidence else {}
    except (TypeError, ValueError):
        return "变更依据无法解析，须复核后重新提案"
    if not isinstance(evidence, dict):
        return "变更依据不是对象，须复核后重新提案"
    if "shadow_started_at" in evidence or "shadow_verdict" in evidence:
        return "曾进入影子评估的候选不能降回草稿绕过独立审查"
    return None


def _canonical_digest(payload: Any) -> str:
    """Stable SHA-256 identity for approval-bound JSON-like evidence."""
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False, default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _evidence_object(row: AgentParamChange) -> dict:
    if not row.evidence:
        return {}
    try:
        value = json.loads(row.evidence)
    except (TypeError, ValueError) as exc:
        raise ValueError("影子证据无法解析；须重新评估后再申请晋级") from exc
    if not isinstance(value, dict):
        raise ValueError("影子证据不是对象；须重新评估后再申请晋级")
    return value


def _candidate_digest(row: AgentParamChange) -> str:
    return _canonical_digest({
        "version": 1,
        "change_id": row.id,
        "key": row.key,
        "before": row.before,
        "after": row.after,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    })


def _baseline_digest(key: str, value: str) -> str:
    return _canonical_digest({"version": 1, "key": key, "value": value})


def _promotion_snapshot(row: AgentParamChange, db) -> dict:
    """Freeze the candidate/baseline/shadow-evidence identity reviewed by a human."""
    evidence = _evidence_object(row)
    current = _effective_value(row.key, db)
    shadow = evidence.get("shadow_verdict")
    blockers: list[str] = []
    if row.status != "shadow":
        blockers.append(f"candidate_status_{row.status}")
    try:
        _validate(row.key, row.after)
    except ValueError:
        blockers.append("candidate_value_invalid")
    if current != (row.before or ""):
        blockers.append("baseline_changed")
    if not isinstance(shadow, dict):
        blockers.append("shadow_assessment_missing")
    else:
        verdict = str(shadow.get("verdict") or "")
        # A human approval may bind independent full-effect evidence, but it may not silently
        # override already-negative/insufficient local evidence. Scalar exploratory evidence must
        # at least support the candidate; structured style offsets may reach human review after
        # their structure-only guard returns shadow_review_required.
        if verdict not in {"supports", "shadow_review_required"}:
            blockers.append(f"shadow_verdict_not_supportive:{verdict or 'missing'}")
        if shadow.get("review_required") is not True or shadow.get("runtime_changed") is not False:
            blockers.append("shadow_contract_invalid")
    return {
        "change_id": row.id,
        "key": row.key,
        "status": row.status,
        "before": row.before,
        "after": row.after,
        "current_baseline": current,
        "candidate_digest": _candidate_digest(row),
        "baseline_digest": _baseline_digest(row.key, current),
        "shadow_evidence_digest": _canonical_digest(evidence),
        "shadow_verdict": shadow if isinstance(shadow, dict) else None,
        "blockers": blockers,
        "approvable": not blockers,
    }


def promotion_review(change_id: int, session_factory=None) -> dict:
    """Read-only human review package; the returned digests must be echoed on approval."""
    sf = session_factory or get_session_factory()
    with sf() as db:
        row = db.get(AgentParamChange, change_id)
        if row is None:
            raise ValueError("变更单不存在")
        return _promotion_snapshot(row, db)


_EFFECT_EVIDENCE_PREFIXES = ("docs/review/", "docs/research/", "artifacts/")


def _is_relative_to(path, root) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_effect_evidence_identity(ref: str, digest: str) -> tuple[str, str]:
    """Bind approval to a real repository evidence file and its exact bytes.

    The reference format is ``repo://<relative-path>``. Only review/research/artifact
    directories are accepted; path traversal, symlink escape, missing files and digest
    drift fail closed. The same check is repeated when the approval is consumed.
    """
    clean_ref = str(ref or "").strip()
    clean_digest = str(digest or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", clean_digest):
        raise ValueError("效果证据必须提供 64 位 SHA-256 摘要")
    if not clean_ref.startswith("repo://") or len(clean_ref) > 500 or any(ord(ch) < 32 for ch in clean_ref):
        raise ValueError("效果证据引用必须使用 repo://<仓库相对路径>")
    rel = clean_ref.removeprefix("repo://").lstrip("/")
    if not rel or not rel.startswith(_EFFECT_EVIDENCE_PREFIXES):
        raise ValueError("效果证据只能来自 docs/review、docs/research 或 artifacts 目录")
    root = REPO_ROOT.resolve()
    target = (root / rel).resolve()
    allowed_roots = [
        (root / "docs" / "review").resolve(),
        (root / "docs" / "research").resolve(),
        (root / "artifacts").resolve(),
    ]
    if not any(_is_relative_to(target, allowed_root) for allowed_root in allowed_roots):
        raise ValueError("效果证据真实路径越出允许证据目录（含符号链接跳转）")
    if not target.is_file():
        raise ValueError("效果证据文件不存在；不得只提交一个引用字符串")
    if target.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("效果证据文件超过 10 MiB 上限；请使用可审阅的摘要 artifact")
    hasher = hashlib.sha256()
    with target.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual != clean_digest:
        raise ValueError("效果证据 SHA-256 与当前文件不一致；须重新审阅证据版本")
    return f"repo://{rel}", clean_digest


def _approval_digest_values(*, change_id: int, candidate_digest: str, baseline_value: str,
                            baseline_digest: str, shadow_evidence_digest: str,
                            effect_evidence_ref: str, effect_evidence_sha256: str,
                            reviewer: str, approval_source: str, note: str,
                            expires_at: datetime) -> str:
    return _canonical_digest({
        "version": 1,
        "change_id": change_id,
        "candidate_digest": candidate_digest,
        "baseline_value": baseline_value,
        "baseline_digest": baseline_digest,
        "shadow_evidence_digest": shadow_evidence_digest,
        "effect_evidence_ref": effect_evidence_ref,
        "effect_evidence_sha256": effect_evidence_sha256,
        "reviewer": reviewer,
        "approval_source": approval_source,
        "note": note,
        "expires_at": expires_at.isoformat(timespec="microseconds"),
    })


def _dump_promotion_approval(row: AgentParamPromotionApproval) -> dict:
    now = beijing_now_naive()
    if row.consumed_at is not None:
        state = "consumed"
    elif row.revoked_at is not None:
        state = "revoked"
    elif row.expires_at <= now:
        state = "expired"
    else:
        state = "approved"
    return {
        "id": row.id,
        "change_id": row.change_id,
        "candidate_digest": row.candidate_digest,
        "baseline_value": row.baseline_value,
        "baseline_digest": row.baseline_digest,
        "shadow_evidence_digest": row.shadow_evidence_digest,
        "effect_evidence_ref": row.effect_evidence_ref,
        "effect_evidence_sha256": row.effect_evidence_sha256,
        "reviewer": row.reviewer,
        "approval_source": row.approval_source,
        "note": row.note,
        "approval_digest": row.approval_digest,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "consumed_at": row.consumed_at.isoformat() if row.consumed_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "revocation_note": row.revocation_note,
        "state": state,
    }


def list_promotion_approvals(change_id: int, session_factory=None) -> list[dict]:
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(AgentParamPromotionApproval)
            .where(AgentParamPromotionApproval.change_id == change_id)
            .order_by(AgentParamPromotionApproval.id.desc())
        ).scalars().all()
        return [_dump_promotion_approval(row) for row in rows]


def approve_shadow_promotion(
    change_id: int, *,
    expected_candidate_digest: str,
    expected_baseline_digest: str,
    expected_shadow_evidence_digest: str,
    effect_evidence_ref: str,
    effect_evidence_sha256: str,
    expires_at: datetime,
    note: str = "",
    session_factory=None,
) -> dict:
    """Create a human approval bound to the exact reviewed candidate/baseline/evidence.

    The approval source is server-fixed to ``human_api``. Candidate model evidence cannot
    synthesize this row by setting ``approved=true``. The caller must first read
    :func:`promotion_review` and echo all three exact digests, closing the review→approve
    time-of-check/time-of-use gap.
    """
    sf = session_factory or get_session_factory()
    ref, effect_sha = _validate_effect_evidence_identity(effect_evidence_ref, effect_evidence_sha256)
    expiry = to_beijing_naive(expires_at)
    now = beijing_now_naive()
    if expiry <= now:
        raise ValueError("批准到期时间必须晚于当前北京时间")
    if expiry > now + PROMOTION_APPROVAL_MAX_TTL:
        raise ValueError("批准有效期不得超过 24 小时；长期 standing approval 不允许")
    note = str(note or "").strip()[:500]
    reviewer, approval_source = "operator", "promotion_token"

    with sf() as db:
        row = db.get(AgentParamChange, change_id)
        if row is None:
            raise ValueError("变更单不存在")
        snap = _promotion_snapshot(row, db)
        if snap["blockers"]:
            raise ValueError(f"当前候选不可批准：{','.join(snap['blockers'])}")
        expected = {
            "candidate_digest": str(expected_candidate_digest or "").lower(),
            "baseline_digest": str(expected_baseline_digest or "").lower(),
            "shadow_evidence_digest": str(expected_shadow_evidence_digest or "").lower(),
        }
        for key, value in expected.items():
            if value != snap[key]:
                raise ValueError(f"{key} 已变化；须重新读取 review package 后再批准")
        approval_digest = _approval_digest_values(
            change_id=change_id,
            candidate_digest=snap["candidate_digest"], baseline_value=snap["current_baseline"],
            baseline_digest=snap["baseline_digest"], shadow_evidence_digest=snap["shadow_evidence_digest"],
            effect_evidence_ref=ref, effect_evidence_sha256=effect_sha,
            reviewer=reviewer, approval_source=approval_source, note=note, expires_at=expiry,
        )
        existing = db.execute(
            select(AgentParamPromotionApproval).where(
                AgentParamPromotionApproval.approval_digest == approval_digest
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _dump_promotion_approval(existing)
        approval = AgentParamPromotionApproval(
            change_id=change_id,
            candidate_digest=snap["candidate_digest"],
            baseline_value=snap["current_baseline"],
            baseline_digest=snap["baseline_digest"],
            shadow_evidence_digest=snap["shadow_evidence_digest"],
            effect_evidence_ref=ref,
            effect_evidence_sha256=effect_sha,
            reviewer=reviewer,
            approval_source=approval_source,
            note=note,
            approval_digest=approval_digest,
            expires_at=expiry,
            created_at=now,
        )
        db.add(approval)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.execute(
                select(AgentParamPromotionApproval).where(
                    AgentParamPromotionApproval.approval_digest == approval_digest
                )
            ).scalar_one_or_none()
            if existing is None:
                raise ValueError("批准写入发生并发冲突；未创建批准，请重试") from None
            return _dump_promotion_approval(existing)
        db.refresh(approval)
        out = _dump_promotion_approval(approval)
        audit_key = row.key
    record_audit("user", "param.promotion.approve", audit_key, after={
        "approval_id": out["id"], "candidate_digest": out["candidate_digest"],
        "effect_evidence_sha256": out["effect_evidence_sha256"], "expires_at": out["expires_at"],
    })
    return out


def revoke_promotion_approval(approval_id: int, *, note: str = "", session_factory=None) -> dict:
    """Revoke an unconsumed approval. Consumed approval must be handled by parameter rollback."""
    sf = session_factory or get_session_factory()
    now = beijing_now_naive()
    note = str(note or "").strip()[:500]
    with sf() as db:
        approval = db.get(AgentParamPromotionApproval, approval_id)
        if approval is None:
            raise ValueError("晋级批准不存在")
        if approval.consumed_at is not None:
            raise ValueError("批准已消费；如需撤销已生效参数，请走参数回滚")
        if approval.revoked_at is not None:
            return _dump_promotion_approval(approval)
        result = db.execute(
            update(AgentParamPromotionApproval).where(
                AgentParamPromotionApproval.id == approval_id,
                AgentParamPromotionApproval.consumed_at.is_(None),
                AgentParamPromotionApproval.revoked_at.is_(None),
            ).values(revoked_at=now, revocation_note=note)
        )
        if result.rowcount != 1:
            db.rollback()
            raise ValueError("批准状态已变化；撤销未生效")
        db.commit()
        approval = db.get(AgentParamPromotionApproval, approval_id)
        out = _dump_promotion_approval(approval)
    record_audit("user", "param.promotion.revoke", f"change:{out['change_id']}", after={
        "approval_id": approval_id, "note": note,
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

    apply_reason = _manual_apply_reason(row) if row.status != "applied" else None
    return {
        "id": row.id, "key": row.key,
        "before": _j(row.before), "after": _j(row.after),
        "source_type": row.source_type, "source_id": row.source_id,
        "evidence": _j(row.evidence),
        "status": row.status,
        "manual_apply_allowed": row.status == "draft" and apply_reason is None,
        "apply_block_reason": apply_reason,
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
    "user" 自动建留痕任务；这仅是归因，不是候选晋级批准。只接受人工草稿，自动/影子候选不得借本入口生效。
    """
    sf = session_factory or get_session_factory()
    # Reject before recording a mutation task; repeat against the writing session.
    with sf() as check_db:
        candidate = check_db.get(AgentParamChange, change_id)
        if candidate is None:
            raise ValueError("变更单不存在")
        if candidate.status == "applied":
            return _dump(candidate)  # Legacy/idempotent read, never re-apply.
        reason = _manual_apply_reason(candidate)
        if reason:
            raise ValueError(reason)
        _validate(candidate.key, candidate.after)
        identity_fields = ("key", "before", "after", "source_type", "source_id", "evidence")
        checked_identity = tuple(getattr(candidate, name) for name in identity_fields)
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
        reason = _manual_apply_reason(row)
        try:
            if tuple(getattr(row, name) for name in identity_fields) != checked_identity:
                raise ValueError("候选内容在确认期间变化，须重新确认，未生效")
            if reason:
                raise ValueError(reason)
            _validate(row.key, row.after)
        except ValueError:
            if mutation_id:
                from app.services.agent_tasks import update_mutation_result
                update_mutation_result(mutation_id, "failed", "候选状态或内容已变化，未生效")
            raise
        # R12（2026-09-14）：`before` 的语义是「这条变更**生效前**的值」。
        # 提案到生效之间同 key 可能已被别的变更单改写，此时若仍以提案时的旧值当基线，
        # 回滚会把中间那次变更**整个抹掉**（合成复现：50→52→58，回滚早先那条 50→52
        # 恢复成 50 而不是 58，等于静默丢弃 B）。真前驱必须**在事务内**取，漂移留痕。
        current = _effective_value(row.key, db)
        rebased_from: str | None = None
        if current != row.before:
            rebased_from = row.before
            row.before = current
            log.warning(
                "参数 %s 变更单 #%d 基线重定：提案时 before=%r，生效时当前值=%r",
                row.key, change_id, rebased_from, current,
            )
        db.merge(AgentParam(key=row.key, value=row.after,
                            updated_at=beijing_now_naive()))
        row.status = "applied"
        row.applied_at = beijing_now_naive()
        db.commit()
        db.refresh(row)
        out = _dump(row)
        out["rebased_from"] = rebased_from
    refresh_runtime_overrides(sf)
    if rebased_from is not None:
        record_audit("user", "param.apply.rebased", row.key,
                     before=rebased_from, after=row.before,
                     rollback_ref=f"change:{change_id}")
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
    只保留结构/影响评估；温和漂移或 supports 都不是效果证明或晋级批准。
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


def _verify_promotion_approval(row: AgentParamChange, approval: AgentParamPromotionApproval, db) -> dict:
    snap = _promotion_snapshot(row, db)
    if "baseline_changed" in snap["blockers"]:
        raise ValueError("运行基线已变化；批准失效，须重新审阅当前候选")
    if snap["blockers"]:
        raise ValueError(f"候选不满足晋级前置：{','.join(snap['blockers'])}")
    now = beijing_now_naive()
    if approval.change_id != row.id:
        raise ValueError("批准不属于当前变更单")
    if approval.reviewer != "operator" or approval.approval_source != "promotion_token":
        raise ValueError("批准来源不是独立 promotion token 操作者，拒绝消费")
    if approval.consumed_at is not None:
        raise ValueError("批准已被消费，不能再次用于晋级")
    if approval.revoked_at is not None:
        raise ValueError("批准已撤销，不能用于晋级")
    if approval.expires_at <= now:
        raise ValueError("批准已过期；须重新审阅当前候选")
    if approval.candidate_digest != snap["candidate_digest"]:
        raise ValueError("候选身份在批准后发生变化；批准失效")
    if approval.baseline_value != snap["current_baseline"] or approval.baseline_digest != snap["baseline_digest"]:
        raise ValueError("运行基线在批准后发生变化；批准失效")
    if approval.shadow_evidence_digest != snap["shadow_evidence_digest"]:
        raise ValueError("影子证据在批准后发生变化；批准失效")
    _validate_effect_evidence_identity(approval.effect_evidence_ref, approval.effect_evidence_sha256)
    expected_digest = _approval_digest_values(
        change_id=approval.change_id,
        candidate_digest=approval.candidate_digest, baseline_value=approval.baseline_value or "",
        baseline_digest=approval.baseline_digest, shadow_evidence_digest=approval.shadow_evidence_digest,
        effect_evidence_ref=approval.effect_evidence_ref,
        effect_evidence_sha256=approval.effect_evidence_sha256,
        reviewer=approval.reviewer, approval_source=approval.approval_source,
        note=approval.note or "", expires_at=approval.expires_at,
    )
    if approval.approval_digest != expected_digest:
        raise ValueError("批准记录摘要不一致；拒绝消费")
    return snap


def promote_shadow(
    change_id: int, session_factory=None, *, approval_id: int | None = None,
    mutation_source: str | None = None,
) -> dict:
    """Atomically consume a human approval and activate one exact shadow candidate.

    Candidate status CAS, approval one-shot consumption and live-baseline CAS share one
    transaction. Any concurrent candidate/evidence/baseline change rolls the whole transaction
    back. Model-produced ``approved`` fields are never consulted.
    """
    if approval_id is None:
        raise ValueError("影子转正必须提供独立人工审查批准 approval_id")
    sf = session_factory or get_session_factory()

    # Read-only preflight before creating the mutation-trace task.
    with sf() as check_db:
        candidate = check_db.get(AgentParamChange, change_id)
        approval = check_db.get(AgentParamPromotionApproval, approval_id)
        if candidate is None:
            raise ValueError("变更单不存在")
        if approval is None:
            raise ValueError("晋级批准不存在")
        if candidate.status == "applied":
            if approval.change_id == change_id and approval.consumed_at is not None:
                out = _dump(candidate)
                out["promotion_approval"] = _dump_promotion_approval(approval)
                out["promotion_already_applied"] = True
                try:
                    refresh_runtime_overrides(sf)
                    out["runtime_refreshed"] = True
                    out["restart_required"] = False
                except Exception as exc:  # Durable state is already authoritative; expose repair need.
                    log.exception("promotion runtime refresh retry failed (change=%s)", change_id)
                    out["runtime_refreshed"] = False
                    out["restart_required"] = True
                    out["runtime_refresh_error"] = type(exc).__name__
                return out
            raise ValueError("变更单已经由其它路径生效；当前批准未消费")
        _verify_promotion_approval(candidate, approval, check_db)
        summary_key, summary_after = candidate.key, candidate.after

    mutation_id = None
    if mutation_source is not None:
        from app.services.agent_tasks import record_mutation
        mutation_id = record_mutation(
            source=mutation_source, kind="param_promotion",
            summary=f"参数影子晋级 {summary_key}：{json.dumps(summary_after, ensure_ascii=False)}",
            detail={"change_id": change_id, "approval_id": approval_id},
        )

    try:
        # Existing post-activation degradation guard is mandatory for newly enabled promotion.
        # Capture it before the write transaction; failure leaves approval and runtime untouched.
        from app.services import experiments as experiment_svc
        post_guard_baseline = experiment_svc.capture_experiment_baseline(sf)
        with sf() as db:
            row = db.get(AgentParamChange, change_id)
            approval = db.get(AgentParamPromotionApproval, approval_id)
            if row is None or approval is None:
                raise ValueError("候选或批准在消费前消失；未生效")
            snap = _verify_promotion_approval(row, approval, db)
            now = beijing_now_naive()
            identity = {
                "key": row.key, "before": row.before, "after": row.after,
                "source_type": row.source_type, "source_id": row.source_id,
                "evidence": row.evidence, "created_at": row.created_at,
            }

            # 1) Exact candidate CAS: only this still-shadow identity may become applied.
            stmt = update(AgentParamChange).where(
                AgentParamChange.id == change_id,
                AgentParamChange.status == "shadow",
            )
            for field, value in identity.items():
                stmt = stmt.where(getattr(AgentParamChange, field) == value)
            changed = db.execute(stmt.values(status="applied", applied_at=now))
            if changed.rowcount != 1:
                raise ValueError("候选状态或身份发生并发变化；批准未消费")

            # 2) One-shot approval CAS in the same transaction.
            consumed = db.execute(
                update(AgentParamPromotionApproval).where(
                    AgentParamPromotionApproval.id == approval_id,
                    AgentParamPromotionApproval.change_id == change_id,
                    AgentParamPromotionApproval.approval_digest == approval.approval_digest,
                    AgentParamPromotionApproval.consumed_at.is_(None),
                    AgentParamPromotionApproval.revoked_at.is_(None),
                    AgentParamPromotionApproval.expires_at > now,
                ).values(consumed_at=now)
            )
            if consumed.rowcount != 1:
                raise ValueError("批准已被消费/撤销/过期或发生并发变化；未生效")

            # 3) Live-baseline CAS. A concurrent manual/other promotion may not be overwritten.
            param = db.get(AgentParam, row.key)
            if param is None:
                db.add(AgentParam(key=row.key, value=row.after, updated_at=now))
                try:
                    db.flush()
                except IntegrityError as exc:
                    raise ValueError("运行基线发生并发变化；批准未消费") from exc
            else:
                expected_stored = param.value
                value_clause = (AgentParam.value.is_(None) if expected_stored is None
                                else AgentParam.value == expected_stored)
                written = db.execute(
                    update(AgentParam).where(
                        AgentParam.key == row.key, value_clause,
                    ).values(value=row.after, updated_at=now)
                )
                if written.rowcount != 1:
                    raise ValueError("运行基线发生并发变化；批准未消费")

            # 4) Existing degradation monitor is attached atomically with activation. The
            # approval/effect identity is copied into the immutable baseline envelope so a later
            # rollback can be traced back to the exact human-reviewed artifact.
            experiment_baseline = {
                **post_guard_baseline,
                "promotion_approval_id": approval_id,
                "promotion_approval_digest": approval.approval_digest,
                "effect_evidence_ref": approval.effect_evidence_ref,
                "effect_evidence_sha256": approval.effect_evidence_sha256,
            }
            experiment = AgentExperiment(
                change_id=change_id,
                param_key=row.key,
                hypothesis=(approval.note or f"approved evidence {approval.effect_evidence_ref}")[:300],
                baseline=json.dumps(experiment_baseline, ensure_ascii=False, allow_nan=False),
                verification_date=now + timedelta(days=experiment_svc.VERIFY_WINDOW_DAYS),
                status="running",
            )
            db.add(experiment)
            db.flush()
            experiment_id = experiment.id

            db.commit()
            final = db.get(AgentParamChange, change_id)
            final_approval = db.get(AgentParamPromotionApproval, approval_id)
            out = _dump(final)
            out["promotion_approval"] = _dump_promotion_approval(final_approval)
            out["baseline_digest"] = snap["baseline_digest"]
            out["post_guard_experiment_id"] = experiment_id
    except Exception:
        if mutation_id:
            from app.services.agent_tasks import update_mutation_result
            update_mutation_result(mutation_id, "failed", "影子晋级失败；批准与运行值未部分消费")
        raise

    try:
        refresh_runtime_overrides(sf)
        out["runtime_refreshed"] = True
        out["restart_required"] = False
        refresh_note = "运行时覆盖已刷新"
    except Exception as exc:  # DB/approval/experiment already committed; never pretend no side effect.
        log.exception("promotion committed but runtime refresh failed (change=%s)", change_id)
        out["runtime_refreshed"] = False
        out["restart_required"] = True
        out["runtime_refresh_error"] = type(exc).__name__
        refresh_note = f"DB 已生效但运行时刷新失败（{type(exc).__name__}），须重试刷新或重启"
    record_audit("user", "param.promote", out["key"], before=out["before"], after={
        "value": out["after"], "runtime_refreshed": out["runtime_refreshed"],
        "post_guard_experiment_id": out["post_guard_experiment_id"],
    }, rollback_ref=f"change:{change_id}")
    if mutation_id:
        from app.services.agent_tasks import update_mutation_result
        mutation_status = "succeeded" if out["runtime_refreshed"] else "failed"
        update_mutation_result(
            mutation_id, mutation_status,
            f"变更单 #{change_id} 已用批准 #{approval_id} 原子写入；{refresh_note}",
        )
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

    **CAS（R12，2026-09-14）**：只有「这条变更单真的生效过」**且**「它仍是当前值的
    拥有者」才写运行值；否则**只归档**（改状态+记归因，不碰覆盖层）。三类都要挡：
    ① 陈旧变更（后续有别的变更单改了同一个 key）；② draft/shadow（**从未生效**——
       旧实现无条件把提案时采集的 `before` 写回覆盖层，等于用一条没生效过的单子改参数）；
    ③ 自动实验回滚（30 日实验劣化回滚一条已被取代的变更）。
    返回值带 `runtime_value_restored` / `skipped_reason`，便于调用方与守卫**断言实际
    消费值**，而不是只看 `status`（状态是 `rolled_back` 但值没动的两种情况必须可区分）。
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
        owner = _active_change_id(row.key, db)
        if row.status != "applied":
            skipped: str | None = "never_applied"
        elif owner != change_id:
            skipped = "superseded" if owner is not None else "no_owner"
        else:
            skipped = None
        if skipped is None:
            restored = row.before if row.before is not None else ""
            if row.before is None:
                cur = db.get(AgentParam, row.key)
                if cur is not None:
                    db.delete(cur)
            else:
                db.merge(AgentParam(key=row.key, value=restored,
                                    updated_at=beijing_now_naive()))
        row.status = "rolled_back"
        row.rolled_back_at = beijing_now_naive()
        row.rollback_reason = json.dumps(
            {"code": reason_code, "note": note[:500]}, ensure_ascii=False
        )
        db.commit()
        db.refresh(row)
        out = _dump(row)
        out["runtime_value_restored"] = skipped is None
        out["skipped_reason"] = skipped
        out["active_change_id"] = owner
    refresh_runtime_overrides(sf)
    if skipped is None:
        record_audit("user", "param.rollback", row.key, before=row.after, after=row.before,
                     rollback_ref=f"change:{change_id}")
    else:
        # 归档型回滚**没有改动运行值**，就不该留下一条"before→after"的假审计——
        # 那会让审计看起来像真回滚过一次。
        record_audit("user", "param.rollback.skipped", row.key,
                     after={"skipped_reason": skipped, "current_owner": owner},
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
