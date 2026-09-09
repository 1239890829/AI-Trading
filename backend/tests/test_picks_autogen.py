"""每日组合自动生成调度测试（2026-09-09，picks_autogen）。

覆盖：当日已有组合跳过（幂等，不覆盖手动生成）/ 周末与 14:00 截止 /
缺失+交易日 → 生成 / 非交易日跳过。
"""

import asyncio
from datetime import datetime, timedelta

from app.picks import picks_autogen as pa


from app.models.daily_pick import DailyPickSet


def _factory(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.watchlist import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'pa.db'}")
    Base.metadata.create_all(engine)  # DailyPickSet 经顶部 import 注册
    return sessionmaker(bind=engine)


def _dt(h: int, m: int, weekday_offset: int = 0) -> datetime:
    """2026-09-07 是周一；weekday_offset=5 → 周六。"""
    return datetime(2026, 9, 7, h, m) + timedelta(days=weekday_offset)


def _stub_generate(monkeypatch, counter):
    import app.api.routes.picks as pr

    async def _fake(*args, **kwargs):
        counter["n"] += 1
        return {}

    monkeypatch.setattr(pr, "generate_picks", _fake)


def test_tick_skips_when_row_exists(tmp_path, monkeypatch):
    """当日已有组合 → 跳过（不覆盖手动/既有结果）。"""
    sf = _factory(tmp_path)
    monkeypatch.setattr(pa, "get_session_factory", lambda: sf)
    counter = {"n": 0}
    _stub_generate(monkeypatch, counter)

    with sf() as db:
        db.add(DailyPickSet(date="2026-09-07", items="[]", meta="{}", replaced="[]", rejected="[]"))
        db.commit()

    out = asyncio.run(pa.picks_autogen_tick(object(), now=_dt(9, 40), run_hour=9, run_minute=26))
    assert out is False
    assert counter["n"] == 0


def test_tick_weekend_and_after_deadline():
    """周末 / 14:00 截止后 → 不生成。"""
    out_sat = asyncio.run(pa.picks_autogen_tick(object(), now=_dt(9, 40, 5), run_hour=9, run_minute=26))
    out_late = asyncio.run(pa.picks_autogen_tick(object(), now=_dt(15, 0, 0), run_hour=9, run_minute=26))
    out_early = asyncio.run(pa.picks_autogen_tick(object(), now=_dt(9, 0, 0), run_hour=9, run_minute=26))
    assert out_sat is False and out_late is False and out_early is False


def test_tick_generates_when_missing_and_trading_day(tmp_path, monkeypatch):
    """窗口内 + 交易日 + 当日缺失 → 生成一次。"""
    sf = _factory(tmp_path)
    monkeypatch.setattr(pa, "get_session_factory", lambda: sf)
    counter = {"n": 0}
    _stub_generate(monkeypatch, counter)

    async def _trading(hub, d):
        return True

    monkeypatch.setattr(pa, "_is_trading_day", _trading)
    app_stub = type("A", (), {"state": type("S", (), {"hub": object()})()})()
    out = asyncio.run(pa.picks_autogen_tick(app_stub, now=_dt(9, 40), run_hour=9, run_minute=26))
    assert out is True
    assert counter["n"] == 1


def test_tick_skips_non_trading_day(tmp_path, monkeypatch):
    """非交易日 → 跳过（日历口径与盘前简报一致）。"""
    sf = _factory(tmp_path)
    monkeypatch.setattr(pa, "get_session_factory", lambda: sf)
    counter = {"n": 0}
    _stub_generate(monkeypatch, counter)

    async def _not_trading(hub, d):
        return False

    monkeypatch.setattr(pa, "_is_trading_day", _not_trading)
    app_stub = type("A", (), {"state": type("S", (), {"hub": object()})()})()
    out = asyncio.run(pa.picks_autogen_tick(app_stub, now=_dt(9, 40), run_hour=9, run_minute=26))
    assert out is False
    assert counter["n"] == 0
