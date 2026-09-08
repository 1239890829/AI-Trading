"""AI 控制台持久化层：任务 + 审计（2026-09-08 方案 docs/ai-agent-console-plan.md P0）。

两张表的定位：
- `AgentTask`：一次 AI/人触发的任务（状态机 + 步骤轨迹）。**步骤轨迹是可追溯
  三件套的第一件**——每个任务必须能回答"它做过什么、依据什么、花了多久"。
- `AgentAudit`：写操作审计（before/after + 回滚点）。执行层任何改动状态的行为
  都要留痕，否则"AI 改了参数却无人知晓"会让全系统结论失去可信度。

设计纪律（与项目其余持久化一致）：
- JSON 字段一律 String 列存文本，读写侧 json 编解码，不引新依赖；
- 状态/类型是小写枚举字符串，非法值在 service 层拦，不在 DB 层猜；
- 时间统一 UTC（utcnow），展示侧转北京时间。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import utcnow
from app.models.watchlist import Base

#: 任务状态机（方案 §3.2）：
#: queued → running → succeeded / failed / canceled
#: needs_confirm = L1/L2 任务停在预览态等人工确认（P1 接入写工具后启用）
TASK_STATUSES = ("queued", "running", "succeeded", "failed", "canceled", "needs_confirm")
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class AgentTriage(Base):
    """告警 AI 判读（方案 P1）：规则触发 → AI 判断"值不值得提醒"。

    verdict：`notify`=值得提醒（进悬浮球）/ `ignore`=噪音，不上界面 /
    `escalate`=升级，进任务中心待办。
    model：`llm`=大模型判读 / `rules`=确定性规则（冷却去重等）/
    `llm_fallback`=LLM 不可用，按规则提醒（界面必须显式标注，不伪装成 AI 判断）。

    一条事件只判读一次（event_id 唯一）。
    """

    __tablename__ = "agent_triage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True, unique=True)
    verdict: Mapped[str] = mapped_column(String(16), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(16), default="rules")
    acked: Mapped[int] = mapped_column(Integer, default=0)   # 悬浮球确认（1=已读）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


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
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
