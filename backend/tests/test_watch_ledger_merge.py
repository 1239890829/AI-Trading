"""merged_into_picks 接线自证（需求 7 收尾，§6.20 W1）。

此前「有字段无接线」：模型与读出（_dump）都在，唯一写入方缺失 ⇒ 恒 0。
本文件钉两件事：
1. `mark_merged_into_picks` 幂等 + 只按 (trade_date, symbol) 命中；
2. `_persist_picks` 定稿后自动回写（组合内 → 1；组合外/他日行不动）。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.core.db import get_engine, get_session_factory
from app.models.watch_ledger import WatchLedger
from app.picks.watch_ledger import get_day, mark_merged_into_picks, record_sighting
from app.services.picks_pipeline import _persist_picks


def _reset_tables():
    from app.models.daily_pick import DailyPickSet
    from app.models.watchlist import Base

    Base.metadata.create_all(get_engine())
    sf = get_session_factory()
    with sf() as db:
        db.query(WatchLedger).delete()
        db.query(DailyPickSet).delete()
        db.commit()


def _seed(trade_date: str, symbol: str):
    row = record_sighting(
        trade_date=trade_date, symbol=symbol, name=symbol,
        entry_price=10.0, entry_time="10:00:00",
        session_factory=get_session_factory(),
    )
    assert row is not None


def test_mark_merged_idempotent_and_scoped():
    _reset_tables()
    _seed("2026-09-11", "600519")
    _seed("2026-09-11", "000001")
    _seed("2026-09-10", "600519")  # 他日同股——不得被波及

    n = mark_merged_into_picks("2026-09-11", ["600519", "999999"], session_factory=get_session_factory())
    assert n == 1  # 999999 无台账行，不写入不报错
    rows = {r["symbol"]: r for r in get_day("2026-09-11", session_factory=get_session_factory())}
    assert rows["600519"]["merged_into_picks"] is True
    assert rows["000001"]["merged_into_picks"] is False
    prev = {r["symbol"]: r for r in get_day("2026-09-10", session_factory=get_session_factory())}
    assert prev["600519"]["merged_into_picks"] is False

    # 幂等：重复标记返回 0
    assert mark_merged_into_picks("2026-09-11", ["600519"], session_factory=get_session_factory()) == 0
    # 空 symbols 快速返回
    assert mark_merged_into_picks("2026-09-11", [], session_factory=get_session_factory()) == 0


def test_persist_picks_marks_ledger_rows():
    _reset_tables()
    _seed("2026-09-11", "600519")   # 进入组合 → 应被标记
    _seed("2026-09-11", "000001")   # 未进组合 → 保持 0

    _persist_picks(
        "2026-09-11",
        items=[{"symbol": "600519", "name": "贵州茅台"}],
        meta={}, replaced=[], rejected=[],
    )

    rows = {r["symbol"]: r for r in get_day("2026-09-11", session_factory=get_session_factory())}
    assert rows["600519"]["merged_into_picks"] is True
    assert rows["000001"]["merged_into_picks"] is False
    # 组合行确实落库（接线没有破坏既有持久化）
    sf = get_session_factory()
    with sf() as db:
        from sqlalchemy import select

        from app.models.daily_pick import DailyPickSet

        assert db.execute(select(DailyPickSet).where(DailyPickSet.date == "2026-09-11")).scalar_one_or_none() is not None
