"""参数变更单增加回滚归因列（P1-15，2026-09-10）

- `agent_param_change.rollback_reason`：JSON `{"code": ..., "note": ...}`。

为什么需要：此前回滚只写 `status=rolled_back` + `rolled_back_at`，**不记原因**。
于是"变更存活率"只能是一个数字，回答不了"这批变更为什么活不下来"——
是实验测到劣化（degraded）、被更优变更取代（superseded）、还是当初判断就错了
（data_issue）。归因是存活率的下钻维度，不是可选的装饰字段。

幂等：SQLite 的 ADD COLUMN 无 IF NOT EXISTS，故先查 `PRAGMA table_info` 再改；
重复 upgrade 不会报错（与既有迁移同款守卫风格）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e9b3c7a1d5f2"
down_revision = "d8e6a4b2c1f3"
branch_labels = None
depends_on = None


def _columns(conn, table: str) -> set[str]:
    rows = conn.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def upgrade() -> None:
    conn = op.get_bind()
    if "rollback_reason" not in _columns(conn, "agent_param_change"):
        op.add_column("agent_param_change", sa.Column("rollback_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if "rollback_reason" in _columns(conn, "agent_param_change"):
        op.drop_column("agent_param_change", "rollback_reason")
