"""B5 迁移三态机测试（临时 sqlite 文件库，不碰真实 ashare.db）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.core.migrations import run_migrations

BASE = Path(__file__).resolve().parent.parent


def _fresh_engine(tmp_name: str):
    path = BASE / "data" / f"tmp-mig-{tmp_name}.db"
    if path.exists():
        path.unlink()
    return create_engine(f"sqlite:///{path}"), path


def _tables(engine) -> set[str]:
    insp = inspect(engine)
    return {t for t in insp.get_table_names() if t != "sqlite_sequence"}


def test_fresh_database_created_at_baseline():
    engine, path = _fresh_engine("fresh")
    try:
        action = run_migrations(engine)
        assert action == "created"
        tables = _tables(engine)
        assert {"watchlist", "paper_account", "review_reports", "prediction_themes", "minute_decisions", "alembic_version"} <= tables
        with engine.connect() as conn:
            ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert ver, "alembic_version 必须已写入"
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def test_legacy_database_stamped_and_completed():
    """存量库（只有一张旧表、无 alembic_version）→ create_all 补齐 + stamp。"""
    engine, path = _fresh_engine("legacy")
    try:
        with engine.begin() as conn:
            # 模拟最老的存量库：只有 watchlist 一张表
            conn.execute(text("CREATE TABLE watchlist (id INTEGER PRIMARY KEY, symbol VARCHAR(12))"))
        action = run_migrations(engine)
        assert action.startswith("stamped")
        tables = _tables(engine)
        assert {"paper_account", "review_reports", "prediction_themes"} <= tables, "缺失表必须被 create_all 补齐"
        with engine.connect() as conn:
            ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert ver
        # 存量的旧表数据结构未被破坏（可查）
        with engine.connect() as conn:
            conn.execute(text("SELECT symbol FROM watchlist"))
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def test_versioned_database_upgrades_idempotently():
    """已版本化库重复 run → upgrade 幂等（无变更时 no-op）。"""
    engine, path = _fresh_engine("versioned")
    try:
        assert run_migrations(engine) == "created"
        # 幂等：再跑一次仍是 upgrade 路径且不报错
        action = run_migrations(engine)
        assert action == "upgraded"
        assert "alembic_version" in _tables(engine)
        # sqlite 直查校验 alembic_version 未被破坏
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT COUNT(*) FROM alembic_version").fetchone()[0] == 1
        conn.close()
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)
