"""Add scenario KB citation columns to opportunity decision snapshots.

revision: c5d2f8a3b7e1
down: b4f1a7c2e9d3

`RSH-027` 切片 1（2026-09-16）：落地蓝图 §5「每次决策快照记录 `scenario`、`kb_ids`、
引用状态、支持/冲突依据、特征版本和 `as_of`」中**尚缺的两项**。

`scenario` / `feature_version` / `as_of` 三列本表**已有**，故本次只增：

- `kb_ids`：被采纳的 KB 条目 ID（JSON 数组，保序去重）
- `kb_refs`：引用状态 + 支持/冲突依据（JSON 对象，含显式 `state` 三态）

两列**只增不改**、且**不参与任何决策**（KB 入模须先过有/无 KB 消融，蓝图 §5）；
既有行经 `server_default` 取「未引用 KB」语义 ⇒ 历史数据零改写、可回溯。

⚠️ 本迁移的 `down_revision` = `b4f1a7c2e9d3`（上一迁移），且**全程只用 `op.*` 接口**，
不由本文件自建 engine —— `BUG-014` 的成因正是迁移里用 `get_engine()`
（指向 settings 默认库）而非迁移目标连接。

⚠️ 两列**刻意不建索引**：KB 引用是解释性附注，不承担查询路径；多一个索引就多一处
「模型 ⇄ 迁移」形状分叉面（`test_db_migrations.py::_schema_drift` 逐索引比对）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5d2f8a3b7e1"
down_revision = "b4f1a7c2e9d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOT NULL ⇒ 必须给 `server_default`：SQLite 的 `ALTER TABLE ADD COLUMN`
    # 要给既有行一个可填值。存量行语义 = 「本列引入前的决策，未记录 KB 引用」
    # ⇒ 取与模型默认一致的 `[]` / `{}`（读取侧由 `kb_refs.state` 缺省体现为历史行）。
    op.add_column(
        "opportunity_decision_snapshot",
        sa.Column("kb_ids", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "opportunity_decision_snapshot",
        sa.Column("kb_refs", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("opportunity_decision_snapshot", "kb_refs")
    op.drop_column("opportunity_decision_snapshot", "kb_ids")
