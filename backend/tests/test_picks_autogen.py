"""每日组合自动生成调度测试（2026-09-09，picks_autogen）。

覆盖：当日已有组合跳过（幂等，不覆盖手动生成）/ 周末与 14:00 截止 /
缺失+交易日 → 生成 / 非交易日跳过 / **未判定（`None`）跳过且可重试**（F7 三态）。
"""

import asyncio
import logging
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


def _app_stub(**state_attrs):
    """构造带 state 的 app 替身。S2-4 后管线依赖走 `PipelineDeps.from_state`，
    因此 state 必须提供 event_store（theme_catalog 可缺省）。"""
    defaults = {"hub": object(), "event_store": object()}
    state = type("S", (), {**defaults, **state_attrs})()
    return type("A", (), {"state": state})()


def _stub_generate(monkeypatch, counter):
    """打桩**服务层管线**（S2-4：调度不再反向 import 路由）。"""
    import app.services.picks_pipeline as pl

    async def _fake(*args, **kwargs):
        counter["n"] += 1
        counter["last"] = kwargs
        return {}

    monkeypatch.setattr(pl, "generate_picks_pipeline", _fake)


def test_tick_skips_when_row_exists(tmp_path, monkeypatch):
    """当日已有组合 → 跳过（不覆盖手动/既有结果）。"""
    sf = _factory(tmp_path)
    monkeypatch.setattr(pa, "get_session_factory", lambda: sf)
    counter = {"n": 0}
    _stub_generate(monkeypatch, counter)

    with sf() as db:
        db.add(DailyPickSet(date="2026-09-07", items="[]", meta="{}", replaced="[]", rejected="[]"))
        db.commit()

    out = asyncio.run(pa.picks_autogen_tick(_app_stub(), now=_dt(9, 40), run_hour=9, run_minute=26))
    assert out is False
    assert counter["n"] == 0


def test_tick_weekend_and_after_deadline():
    """周末 / 14:00 截止后 → 不生成。"""
    out_sat = asyncio.run(pa.picks_autogen_tick(_app_stub(), now=_dt(9, 40, 5), run_hour=9, run_minute=26))
    out_late = asyncio.run(pa.picks_autogen_tick(_app_stub(), now=_dt(15, 0, 0), run_hour=9, run_minute=26))
    out_early = asyncio.run(pa.picks_autogen_tick(_app_stub(), now=_dt(9, 0, 0), run_hour=9, run_minute=26))
    assert out_sat is False and out_late is False and out_early is False


def test_tick_generates_when_missing_and_trading_day(tmp_path, monkeypatch):
    """窗口内 + 交易日 + 当日缺失 → 生成一次，且**归属日取窗口判定的那个北京日**。

    KB-TRADE-02：此前窗口判定用 `now`（北京）而管线写入用 `date.today()`（本机），
    两处日期源不同源。本测试钉住 now 被显式传下去。
    """
    sf = _factory(tmp_path)
    monkeypatch.setattr(pa, "get_session_factory", lambda: sf)
    counter = {"n": 0}
    _stub_generate(monkeypatch, counter)

    async def _trading(hub, d):
        return True

    monkeypatch.setattr(pa, "_trade_day_state", _trading)
    out = asyncio.run(pa.picks_autogen_tick(_app_stub(), now=_dt(9, 40), run_hour=9, run_minute=26))
    assert out is True
    assert counter["n"] == 1
    assert counter["last"]["today"] == "2026-09-07"


def test_tick_skips_non_trading_day(tmp_path, monkeypatch):
    """非交易日 → 跳过（日历口径与盘前简报一致）。"""
    sf = _factory(tmp_path)
    monkeypatch.setattr(pa, "get_session_factory", lambda: sf)
    counter = {"n": 0}
    _stub_generate(monkeypatch, counter)

    async def _not_trading(hub, d):
        return False

    monkeypatch.setattr(pa, "_trade_day_state", _not_trading)
    out = asyncio.run(pa.picks_autogen_tick(_app_stub(), now=_dt(9, 40), run_hour=9, run_minute=26))
    assert out is False
    assert counter["n"] == 0


def test_tick_defers_when_calendar_unknown(tmp_path, monkeypatch, caplog):
    """**未判定（`None`）→ 跳过，且下一次轮询必须重新判定**（F7 定点守卫）。

    本调度与盘前简报不同：它**没有持久化 last_run**，唯一持久闸门是
    `_today_row_exists`，因此「可重试」不是靠"不置位"，而是靠
    **未判定路径不得写下任何"今日已处置"的痕迹**。
    两条判据缺一不可（KB-ENG-65 ㈠：桩里只写 `return None` 而无计数，
    等于没判——被挡住的实现对这条断言同样全绿）：

    1. **调用计数**：第二拍仍要重新走日历判定（桩内计数 = 2）；
    2. **日志口径**：未判定必须留下**可见的 warning**，且**不得**被表述为
       「非交易日」——原实现两者共用一条 info，口径失真（`None` 与 `False`
       在日志里看起来一模一样，事故当天排查时正是被这一条误导）。

    *回退即红*：把 `if day_state is not True:` 改回 `if not day_state:`（二态塌缩）
    ⇒ 判据 2 立刻变红（日志变成「非交易日」、级别降为 info）。
    """
    sf = _factory(tmp_path)
    monkeypatch.setattr(pa, "get_session_factory", lambda: sf)
    counter = {"n": 0}
    _stub_generate(monkeypatch, counter)

    calls = {"n": 0}

    async def _unknown(hub, d):
        calls["n"] += 1
        return None

    monkeypatch.setattr(pa, "_trade_day_state", _unknown)
    with caplog.at_level(logging.WARNING):
        out1 = asyncio.run(
            pa.picks_autogen_tick(_app_stub(), now=_dt(9, 40), run_hour=9, run_minute=26)
        )
        out2 = asyncio.run(
            pa.picks_autogen_tick(_app_stub(), now=_dt(9, 40), run_hour=9, run_minute=26)
        )
    msgs = [r.getMessage() for r in caplog.records]
    assert out1 is False and out2 is False
    assert counter["n"] == 0, "未判定不得触发生成"
    assert calls["n"] == 2, "未判定路径必须可重试：第二拍仍要重新走日历判定"
    assert any("未判定" in m for m in msgs), "未判定必须留下可见告警，不得静默按非交易日处理"
    assert not any("非交易日" in m for m in msgs), "未判定不得被表述为「非交易日」（口径失真）"


def test_wait_snapshot_ready_polls_until_breadth():
    """重启补跑场景（2026-09-09 事故）：breadth 未就绪时轮询等待，就绪即返回。"""
    calls = {"n": 0}

    class Svc:
        @property
        def breadth(self):
            calls["n"] += 1
            return None if calls["n"] < 3 else {"up": 100, "down": 50}

    ok = asyncio.run(pa._wait_snapshot_ready(_app_stub(snapshot_service=Svc()), timeout=1.0, interval=0.01))
    assert ok is True
    assert calls["n"] >= 3  # 确实等了（不是看一眼就过）


def test_wait_snapshot_ready_times_out_without_blocking():
    """快照始终未就绪 → 超时返回 False，不无限阻塞调度。"""
    class Svc:
        breadth = None

    assert asyncio.run(
        pa._wait_snapshot_ready(_app_stub(snapshot_service=Svc()), timeout=0.05, interval=0.01)
    ) is False


def test_tick_proceeds_without_snapshot_service(tmp_path, monkeypatch):
    """无 snapshot_service（或旧 stub）→ 不等待、照常生成（回归保护）。"""
    sf = _factory(tmp_path)
    monkeypatch.setattr(pa, "get_session_factory", lambda: sf)
    counter = {"n": 0}
    _stub_generate(monkeypatch, counter)

    async def _trading(hub, d):
        return True

    monkeypatch.setattr(pa, "_trade_day_state", _trading)
    # 无 snapshot_service：_wait_snapshot_ready 直接返回 False，不阻塞
    out = asyncio.run(pa.picks_autogen_tick(_app_stub(), now=_dt(9, 40), run_hour=9, run_minute=26))
    assert out is True
    assert counter["n"] == 1
