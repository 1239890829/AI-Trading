"""IMP-052: durable Agent budget leases, usage receipts and cancel request."""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "b5c9e7a2d4f1"
down_revision = "f4a2c8e1b6d3"
branch_labels = None
depends_on = None


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        sa.text("select count(*) from sqlite_master where type='table' and name=:n"), {"n": name}
    ).scalar())


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(sa.text(f"pragma table_info('{table}')")).mappings().all()
    return any(str(r["name"]) == column for r in rows)




def _insert_legacy_usage(
    conn, *, row_id: str, day: str, scope: str, slot: int, kind: str, purpose: str,
    task_id: str | None = None, at=None,
) -> None:
    stamp = at or conn.execute(sa.text("select datetime('now','+8 hours')")).scalar()
    conn.execute(
        sa.text(
            """
            insert into agent_resource_usage(
                id,budget_date,scope,slot,kind,purpose,task_id,provider,model,state,
                attempts,max_retries,timeout_ms,input_chars,output_chars,input_tokens,output_tokens,
                usage_known,error_kind,created_at,started_at,finished_at
            ) values (
                :id,:day,:scope,:slot,:kind,:purpose,:task_id,'','', 'unknown',
                1,0,0,0,0,null,null,0,'legacy_unknown',:at,:at,:at
            )
            """
        ),
        {
            "id": row_id[:36], "day": day, "scope": scope, "slot": slot,
            "kind": kind, "purpose": purpose[:80], "task_id": task_id, "at": stamp,
        },
    )


def _backfill_rollout_day(conn) -> None:
    """Carry today's pre-migration Agent consumption into the durable ledger.

    Historical rows do not contain reliable token usage, so migrated model calls are explicitly
    ``unknown`` rather than zero.  ``autonomy_llm`` then fails closed for the remainder of this
    Beijing day; the next day starts natively on the new ledger.
    """
    day = str(conn.execute(sa.text("select date(datetime('now','+8 hours'))")).scalar())

    audits = conn.execute(sa.text(
        """
        select id, action, task_id, at
        from agent_audit
        where date(at)=:day and action in ('agenda.generate','triage.llm','code.propose')
        order by id
        """
    ), {"day": day}).mappings().all()
    for slot, row in enumerate(audits, start=1):
        _insert_legacy_usage(
            conn, row_id=f"legacy-audit-{row['id']}", day=day, scope="autonomy_llm",
            slot=slot, kind="model", purpose=str(row["action"]),
            task_id=row.get("task_id"), at=row.get("at"),
        )

    # C proposals also had an independent daily cap before this migration. Preserve the first
    # historical attempt so a rollout cannot reset that cap mid-day.
    code_rows = [row for row in audits if row["action"] == "code.propose"]
    if code_rows:
        row = code_rows[0]
        _insert_legacy_usage(
            conn, row_id=f"legacy-code-{row['id']}", day=day, scope="code_proposal",
            slot=1, kind="task", purpose="code.propose",
            task_id=row.get("task_id"), at=row.get("at"),
        )

    agendas = conn.execute(sa.text(
        "select id, items, created_at from agent_agenda where date=:day order by id"
    ), {"day": day}).mappings().all()
    task_slot = 0
    for agenda in agendas:
        try:
            items = json.loads(agenda.get("items") or "[]")
        except Exception:
            items = []
        if not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict) or item.get("origin") == "data_health":
                continue
            if item.get("class") not in {"A", "B", "C"}:
                continue
            if item.get("status") not in {"executed", "proposed"}:
                continue
            task_slot += 1
            _insert_legacy_usage(
                conn, row_id=f"legacy-task-{agenda['id']}-{idx}", day=day,
                scope="autonomy_task", slot=task_slot, kind="task",
                purpose=f"agenda.{item.get('class')}", at=agenda.get("created_at"),
            )


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "agent_task", "cancel_requested_at"):
        with op.batch_alter_table("agent_task") as batch:
            batch.add_column(sa.Column("cancel_requested_at", sa.DateTime(), nullable=True))
            batch.create_index("ix_agent_task_cancel_requested_at", ["cancel_requested_at"], unique=False)
    if not _table_exists(conn, "agent_resource_usage"):
        op.create_table(
            "agent_resource_usage",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("budget_date", sa.String(length=10), nullable=False),
            sa.Column("scope", sa.String(length=32), nullable=False),
            sa.Column("slot", sa.Integer(), nullable=True),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("purpose", sa.String(length=80), nullable=False),
            sa.Column("task_id", sa.String(length=36), nullable=True),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("model", sa.String(length=80), nullable=False),
            sa.Column("state", sa.String(length=16), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("max_retries", sa.Integer(), nullable=False),
            sa.Column("timeout_ms", sa.Integer(), nullable=False),
            sa.Column("input_chars", sa.Integer(), nullable=False),
            sa.Column("output_chars", sa.Integer(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("usage_known", sa.Integer(), nullable=False),
            sa.Column("error_kind", sa.String(length=32), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("budget_date", "scope", "slot", name="uq_agent_resource_usage_budget_slot"),
        )
        for col in ("budget_date", "scope", "kind", "purpose", "task_id", "state", "created_at", "started_at", "finished_at"):
            op.create_index(f"ix_agent_resource_usage_{col}", "agent_resource_usage", [col], unique=False)
        _backfill_rollout_day(conn)


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "agent_resource_usage"):
        for col in ("finished_at", "started_at", "created_at", "state", "task_id", "purpose", "kind", "scope", "budget_date"):
            conn.execute(sa.text(f"drop index if exists ix_agent_resource_usage_{col}"))
        op.drop_table("agent_resource_usage")
    if _column_exists(conn, "agent_task", "cancel_requested_at"):
        with op.batch_alter_table("agent_task") as batch:
            batch.drop_index("ix_agent_task_cancel_requested_at")
            batch.drop_column("cancel_requested_at")
