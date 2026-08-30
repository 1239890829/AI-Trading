"""数据库迁移入口（技术评审 B5）：stamp-or-upgrade 三态机。

三态判定（按库的现状，而非猜测）：
1. 已版本化（有 alembic_version 表）→ ``upgrade head``——常规演进路径；
2. 存量未版本化（有业务表、无 alembic_version）→ ``create_all`` 补缺失表 +
   ``stamp head``——存量库由 create_all+PRAGMA 演进而来，结构与基线一致，
   stamp 即认可现状，后续变更走新迁移；
3. 全新库（两者皆无）→ ``upgrade head``——由基线迁移建全量表。

create_all 从 lifespan 主路径移除：schema 从此唯一归 alembic 管，
create_all 只在存量兜底分支内出现（且只补缺失、不改现有表）。

迁移全程在传入 engine 的连接上执行（alembic 官方连接共享模式）——
:memory:/测试库与迁移同连接，不会把表建到别处。
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]  # app/core/migrations.py → backend/


def run_migrations(engine: Engine, url: str | None = None) -> str:
    """对指定 engine 执行三态迁移，返回动作描述（供日志与测试断言）。"""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    # url 默认从 engine 派生——迁移目标必须与传入 engine 严格一致，
    # 否则测试/多库场景会误操作默认库（env.py 仅在未设置时回填 settings）
    cfg.set_main_option("sqlalchemy.url", url or str(engine.url))

    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        insp = inspect(connection)
        has_version = insp.has_table("alembic_version")

        if has_version:
            command.upgrade(cfg, "head")
            log.info("database migrations: upgraded to head")
            return "upgraded"

        has_business = any(
            insp.has_table(t) for t in ("watchlist", "paper_account", "review_reports")
        )
        if has_business:
            # 存量库：create_all 只补缺失表（不动现有表），然后认可基线
            from app.models.watchlist import Base

            Base.metadata.create_all(connection)
            command.stamp(cfg, "head")
            log.info("database migrations: legacy schema stamped at baseline")
            return "stamped"

        command.upgrade(cfg, "head")
        log.info("database migrations: fresh database created at baseline")
        return "created"
