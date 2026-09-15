"""P2-2 复盘强制改进抽查（evolution 证据增强）单测。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.review.models import ReviewActionItemRow
from app.models.watchlist import Base
from app.services.evolution import _collect_framework_backlog, _repeat_pending_items


@pytest.fixture
def sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p22.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def _fixed_beijing_now(monkeypatch):
    """Keep the seven-day fixtures independent of the wall-clock date."""
    from app.services import evolution

    fixed_now = datetime.fromisoformat("2026-09-10T12:00:00+08:00")
    monkeypatch.setattr(evolution, "beijing_now", lambda: fixed_now)


def _row(trade_date: str, title: str, category: str = "process", status: str = "pending"):
    return ReviewActionItemRow(
        review_id=f"r{trade_date}", trade_date=trade_date, title=title,
        category=category, priority="P1", status=status,
    )


def test_repeat_pending_detects_two_days(sf):
    with sf() as db:
        db.add(_row("20260908", "复盘需接低分维强制改进"))
        db.add(_row("20260909", "复盘需接低分维强制改进"))
        db.add(_row("20260909", "只出现一天的一次性改进"))
        db.commit()
    out = _repeat_pending_items(sf, days=7)
    assert len(out) == 1
    assert out[0]["title"].startswith("复盘需接低分维")
    assert out[0]["pending_days"] == 2


def test_repeat_pending_ignores_resolved_and_distinct(sf):
    with sf() as db:
        db.add(_row("20260908", "已处置项", status="applied"))
        db.add(_row("20260909", "已处置项", status="applied"))
        db.add(_row("20260909", "A 项"))
        db.add(_row("20260908", "B 项"))
        db.commit()
    assert _repeat_pending_items(sf, days=7) == []  # 均已处置/只一天 → 无强制复查


def test_framework_backlog_reads_recent(sf):
    out = _collect_framework_backlog()
    assert out["available"] is True
    assert out["n"] >= 1
    assert out["recent"], "§7 演化日志应有最近条目"


def test_framework_backlog_missing_file(sf, monkeypatch):
    from app.services import evolution

    monkeypatch.setattr(evolution, "PROJECT_ROOT", Path("/nonexistent-project-root"))
    out = _collect_framework_backlog()
    assert out["available"] is False
