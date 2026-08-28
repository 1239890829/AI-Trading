from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_engine = None
_session_factory = None


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
    return _engine


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _session_factory


def get_db():
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
