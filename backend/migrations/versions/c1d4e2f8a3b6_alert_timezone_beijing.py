"""告警/预警时间统一北京时间 naive（P0-2：用户三.1 时区缺陷）

alert_event.triggered_at / alert_rule.last_triggered_at.created_at.updated_at
此前 default=utcnow() 存 UTC naive → 展示被当北京时间 → 告警时间早 8 小时
（与 event_card 同款，2026-09-09 实锤：最新告警存 06:55 而北京已 20:55）。

幂等判定：UTC naive 值的"当前语义时刻"恒 ≤ 当前 UTC naive；而北京时间
naive 值 = UTC now + 8h，恒 > 当前 UTC naive。故「该列最新值 ≤ 当前 UTC
naive」即判定为 UTC 存储 → 整列 +8h；否则跳过（防重复 +8）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "c1d4e2f8a3b6"
down_revision = "b7e5c3d9a2f4"
branch_labels = None
depends_on = None

# 各表需北京时间化的时间列
_TARGETS = {
    "alert_event": ["triggered_at"],
    "alert_rule": ["last_triggered_at", "created_at", "updated_at"],
}
# 防误伤起点：这些表 2026-08 才上线，早于此的值视为脏数据不动
_EPOCH = "2026-08-01 00:00:00"


def _utc_now_naive() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def upgrade() -> None:
    conn = op.get_bind()
    now_utc = _utc_now_naive()
    for table, cols in _TARGETS.items():
        existing = [r[1] for r in conn.execute(sa.text(f"pragma table_info({table})")).fetchall()]
        for col in cols:
            if col not in existing:
                continue
            mx = conn.execute(sa.text(f"select max({col}) from {table}")).scalar()
            if not mx or str(mx) < _EPOCH:
                continue
            # UTC naive 存储 → 最新值 ≤ 当前 UTC；北京时间 naive → 恒 > 当前 UTC
            if str(mx) <= now_utc:
                conn.execute(sa.text(f"update {table} set {col} = datetime({col}, '+8 hours') where {col} is not null"))
                print(f"[migrate] {table}.{col}: UTC→北京 +8h (max {str(mx)[:19]})")


def downgrade() -> None:
    """回滚：北京 naive → 减 8h 回 UTC（幂等判定反过来：最新值 > 当前 UTC）。"""
    conn = op.get_bind()
    now_utc = _utc_now_naive()
    for table, cols in _TARGETS.items():
        existing = [r[1] for r in conn.execute(sa.text(f"pragma table_info({table})")).fetchall()]
        for col in cols:
            if col not in existing:
                continue
            mx = conn.execute(sa.text(f"select max({col}) from {table}")).scalar()
            if not mx or str(mx) < _EPOCH:
                continue
            if str(mx) > now_utc:
                conn.execute(sa.text(f"update {table} set {col} = datetime({col}, '-8 hours') where {col} is not null"))
                print(f"[migrate-rollback] {table}.{col}: 北京→UTC -8h")
