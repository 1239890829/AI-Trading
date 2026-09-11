from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings

_BJ = timezone(timedelta(hours=8))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def beijing_now_naive() -> datetime:
    """北京时间 naive（2026-09-09 定为事件时间口径）。

    事故背景：event_card.published_at 曾存 UTC（flash 入库时 astimezone(utc)），
    而展示/排序按北京时间理解 → 全部时间"早了 8 小时"（用户看到的事件全是早上）。
    事件面向人看，统一存北京 naive；ranking/judge 等内部计算同步对齐此口径。
    """
    return datetime.now(_BJ).replace(tzinfo=None)


_engine = None
_session_factory = None


def _migrate(engine):
    """轻量迁移：SQLite 已有表补列（幂等）。"""
    from sqlalchemy import text

    with engine.begin() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(watchlist)"))]
        if cols and "group_name" not in cols:
            conn.execute(text("ALTER TABLE watchlist ADD COLUMN group_name VARCHAR(32) DEFAULT '默认'"))
            log = __import__("logging").getLogger(__name__)
            log.info("migrated: watchlist.group_name added")


def get_engine():
    global _engine
    if _engine is None:
        url = settings.database_url
        kwargs: dict = {}
        if url.endswith(":memory:"):
            # 内存库需要在所有 session 间共享同一连接，否则表结构不可见
            kwargs = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
        else:
            db_path = url.removeprefix("sqlite:///")
            if db_path:
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            kwargs = {"connect_args": {"check_same_thread": False}}
        _engine = create_engine(url, **kwargs)
        try:
            _migrate(_engine)
        except Exception:
            pass  # 表尚不存在时由 create_all 建
    return _engine


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _session_factory
