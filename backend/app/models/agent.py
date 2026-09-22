"""AI 控制台持久化层：任务 + 审计（2026-09-08 方案 docs/summary/ai-evolution.md P0）。

两张表的定位：
- `AgentTask`：一次 AI/人触发的任务（状态机 + 步骤轨迹）。**步骤轨迹是可追溯
  三件套的第一件**——每个任务必须能回答"它做过什么、依据什么、花了多久"。
- `AgentAudit`：写操作审计（before/after + 回滚点）。执行层任何改动状态的行为
  都要留痕，否则"AI 改了参数却无人知晓"会让全系统结论失去可信度。

设计纪律（与项目其余持久化一致）：
- JSON 字段一律 String 列存文本，读写侧 json 编解码，不引新依赖；
- 状态/类型是小写枚举字符串，非法值在 service 层拦，不在 DB 层猜；
- 事件时间统一北京 naive（beijing_now_naive，2026-09-12 方案 A 收敛，
  与全系统事件时间口径一致；原 UTC naive 存量已一次性迁移）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.bjtime import beijing_now_naive
from app.models.watchlist import Base

#: 任务状态机（方案 §3.2）：
#: queued → running → succeeded / failed / canceled
#: needs_confirm = L1/L2 任务停在预览态等人工确认（P1 接入写工具后启用）
TERMINAL_STATUSES = ("succeeded", "failed", "canceled")


class AgentTask(Base):
    """AI 控制台任务。

    steps 是步骤轨迹 JSON 数组：[{index, name, input_summary, output_summary,
    llm:{model,prompt_hash,enhanced}, duration_ms, ok}]——任务详情面板直接渲染。
    """

    __tablename__ = "agent_task"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4
    type: Mapped[str] = mapped_column(String(32), index=True)       # review / data_check / …
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    params: Mapped[str] = mapped_column(Text, default="{}")         # JSON：任务入参
    steps: Mapped[str] = mapped_column(Text, default="[]")          # JSON：步骤轨迹
    result_ref: Mapped[str | None] = mapped_column(Text, default=None)  # JSON：{kind,id}
    error: Mapped[str | None] = mapped_column(Text, default=None)   # JSON：{code,message,retryable}
    risk_level: Mapped[str] = mapped_column(String(2), default="L0")
    created_by: Mapped[str] = mapped_column(String(16), default="user")  # user/ai/scheduler
    created_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now_naive, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime, default=None, index=True)


class AgentResourceUsage(Base):
    """Durable Agent resource reservation + model usage receipt (IMP-052).

    Budgeted scopes use a small integer ``slot`` with a DB uniqueness constraint, so two
    processes cannot both consume the last daily slot. Unmetered telemetry leaves slot NULL.
    Started rows are never silently released: if a process dies after external I/O began,
    startup reconciliation marks the row ``unknown`` and token usage remains explicitly unknown.
    """

    __tablename__ = "agent_resource_usage"
    __table_args__ = (
        UniqueConstraint("budget_date", "scope", "slot", name="uq_agent_resource_usage_budget_slot"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    budget_date: Mapped[str] = mapped_column(String(10), index=True)
    scope: Mapped[str] = mapped_column(String(32), index=True)
    slot: Mapped[int | None] = mapped_column(Integer, default=None)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    purpose: Mapped[str] = mapped_column(String(80), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    state: Mapped[str] = mapped_column(String(16), default="reserved", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=0)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_chars: Mapped[int] = mapped_column(Integer, default=0)
    output_chars: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    output_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    usage_known: Mapped[int] = mapped_column(Integer, default=0)
    error_kind: Mapped[str | None] = mapped_column(String(32), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now_naive, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None, index=True)


class AgentTriage(Base):
    """告警 AI 判读（方案 P1）：规则触发 → AI 判断"值不值得提醒"。

    verdict：`notify`=值得提醒（进悬浮球）/ `ignore`=噪音，不上界面 /
    `escalate`=升级，进任务中心待办。
    model：`llm`=DeepSeek 大模型判读 / `jev`=经校准阈值放行的 Jev 结构化判读 /
    `rules`=确定性规则（冷却去重等）/ `llm_fallback`=LLM 不可用，按规则提醒
    （界面必须显式标注来源，不伪装成其它模型判断）。

    一条事件只判读一次（event_id 唯一）。
    """

    __tablename__ = "agent_triage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True, unique=True)
    verdict: Mapped[str] = mapped_column(String(16), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(16), default="rules")
    acked: Mapped[int] = mapped_column(Integer, default=0)   # 悬浮球确认（1=已读）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now_naive, index=True)


class AgentParam(Base):
    """参数运行时覆盖层（AI 控制台参数配置）。

    一行 = 一个被改过的参数。消费方（目前 style_router）通过
    `set_override_provider` 读取——**改参数免重启**；删除该行即回到静态配置。
    """

    __tablename__ = "agent_param"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, default=None)   # JSON 字符串
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=beijing_now_naive)


class AgentParamChange(Base):
    """参数变更单（可追溯 + 可回滚的最小单元）。

    before/after 存原值，回滚就是把 before 写回覆盖层；source_* 记录这条变更
    来自哪份复盘的哪条改进项；evidence 记录采纳依据（样本/IC/胜率）——证据
    不足的变更单不允许自动生效。
    """

    __tablename__ = "agent_param_change"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), index=True)
    before: Mapped[str | None] = mapped_column(Text, default=None)
    after: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(32), default="manual")
    source_id: Mapped[str] = mapped_column(String(64), default="")
    evidence: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now_naive)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    #: 回滚归因（2026-09-10 P1-15）：JSON `{"code": ..., "note": ...}`。
    #: 只记"回滚了"而不记"为什么回滚"，存活率就只是个数字——无法回答
    #: "这批变更为什么活不下来"（是实验测到劣化，还是当初判断就错了）。
    rollback_reason: Mapped[str | None] = mapped_column(Text, default=None)


class AgentParamPromotionApproval(Base):
    """Shadow parameter promotion approval, separate from model-produced evidence.

    The approval binds one reviewed candidate identity, the exact live baseline and
    the shadow-evidence snapshot to an independently reviewed effect artifact.
    ``consumed_at`` is written in the same transaction as activation so an approval
    cannot authorize two different parameter writes.
    """

    __tablename__ = "agent_param_promotion_approval"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_id: Mapped[int] = mapped_column(Integer, index=True)
    candidate_digest: Mapped[str] = mapped_column(String(64), index=True)
    baseline_value: Mapped[str | None] = mapped_column(Text, default=None)
    baseline_digest: Mapped[str] = mapped_column(String(64))
    shadow_evidence_digest: Mapped[str] = mapped_column(String(64))
    effect_evidence_ref: Mapped[str] = mapped_column(Text)
    effect_evidence_sha256: Mapped[str] = mapped_column(String(64))
    reviewer: Mapped[str] = mapped_column(String(32), default="operator")
    approval_source: Mapped[str] = mapped_column(String(32), default="promotion_token")
    note: Mapped[str] = mapped_column(Text, default="")
    approval_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now_naive, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, default=None, index=True)
    revocation_note: Mapped[str | None] = mapped_column(Text, default=None)


class AgentAgenda(Base):
    """每日进化议程（AI 大脑 v2，docs/summary/ai-evolution.md）。

    一天一份：LLM 汇总五路证据 → 议程项数组 → 按类别自动执行。
    items 元素：{class: A|B, finding, evidence, action, priority,
    expected_effect, verification, status, result}——status 由执行器写：
    executed / deferred(附原因) / rejected(附原因) / failed(附原因)，
    **没有"待确认"态**（后置守护模型）。
    """

    __tablename__ = "agent_agenda"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(10), unique=True, index=True)  # YYYY-MM-DD
    status: Mapped[str] = mapped_column(String(16), default="generating", index=True)
    inputs: Mapped[str] = mapped_column(Text, default="{}")   # JSON：五路证据摘要
    items: Mapped[str] = mapped_column(Text, default="[]")    # JSON：议程项
    budget: Mapped[str] = mapped_column(Text, default="{}")   # JSON：{llm_calls, tasks}
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now_naive)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class AgentExperiment(Base):
    """实验记录本（AI 大脑 v2 P1）：A 类参数变更的后置验证载体。

    安全模型=前置放行、后置纠错：变更生效时自动建实验（基线=当时 signal_health
    快照），验证窗口（默认 30 日）到期后自动对比——胜率劣化超阈值 → **自动回滚**
    变更单并记录结论；样本不足 → 延长窗口（最多 max_extensions 次），绝不误杀。
    每条实验回答：假设是什么、基线如何、结果如何、结论是什么。
    """

    __tablename__ = "agent_experiment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_id: Mapped[int] = mapped_column(Integer, index=True)   # 关联 agent_param_change
    param_key: Mapped[str] = mapped_column(String(64), index=True)
    hypothesis: Mapped[str] = mapped_column(Text, default="")     # 预期效果（来自议程项）
    baseline: Mapped[str] = mapped_column(Text, default="{}")     # JSON：生效时 signal_health 快照
    verification_date: Mapped[datetime | None] = mapped_column(DateTime, default=None, index=True)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    # running / concluded(改善或持平) / rolled_back(劣化自动回滚) / concluded_insufficient
    result: Mapped[str | None] = mapped_column(Text, default=None)  # JSON：对比结果与结论
    extensions: Mapped[int] = mapped_column(Integer, default=0)
    max_extensions: Mapped[int] = mapped_column(Integer, default=2)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now_naive)
    concluded_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class AgentAudit(Base):
    """执行层审计：谁在什么时候把什么从什么改成了什么（+ 怎么回滚）。"""

    __tablename__ = "agent_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(16), default="user")   # user/ai/scheduler
    action: Mapped[str] = mapped_column(String(48), index=True)      # task.create / param.apply …
    target: Mapped[str] = mapped_column(String(128), default="", index=True)
    before: Mapped[str | None] = mapped_column(Text, default=None)   # JSON
    after: Mapped[str | None] = mapped_column(Text, default=None)    # JSON
    task_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    rollback_ref: Mapped[str | None] = mapped_column(String(64), default=None)
    at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now_naive, index=True)
