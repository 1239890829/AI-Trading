"""event_card add llm_judged_at

P2-3 层1：LLM 辅助判定「已做过」标记列（防重复烧钱）。
- 存北京时间 naive（与 published_at 同口径，2026-09-09 时区统一）。
- 幂等：列不存在才 ALTER；已存在直接跳过（历史库升级安全）。
"""
from alembic import op
import sqlalchemy as sa

revision: str = "d8e6a4b2c1f3"
down_revision = "c1d4e2f8a3b6"
branch_labels = None
depends_on = None


def _has_column(conn, table: str, col: str) -> bool:
    return col in {r[1] for r in conn.execute(sa.text(f"pragma table_info({table})")).fetchall()}


def upgrade() -> None:
    conn = op.get_bind()
    if _has_column(conn, "event_card", "llm_judged_at"):
        return
    op.add_column("event_card", sa.Column("llm_judged_at", sa.DateTime(), nullable=True))
    # 存量一律视为「未做过」（NULL），由扫描循环按批补齐——不臆造已判时间


def downgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "event_card", "llm_judged_at"):
        return
    op.drop_column("event_card", "llm_judged_at")
