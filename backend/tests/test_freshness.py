"""S2-1 统一 Freshness 契约 + S1-3 快照陈旧被识别。

测试重点沿用本项目的一贯选法：**"错了也看不出来"的地方**——
`market_context` 过去只判 `breadth is None`、不判年龄，于是 20 分钟前的宽度
配上当前涨停池照样出「阶段」结论：数字全都合理、结论是错的。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, ".")

from app.core.freshness import Freshness, age_seconds_of
from app.schemas.market import Quote
from app.services import market_context as mc
from app.services.snapshot_service import MarketSnapshotService


def _run(coro):
    return asyncio.run(coro)


# ---------- 契约本身 ----------


def test_from_age_three_states():
    now = datetime.now(timezone.utc)
    assert Freshness.from_age(as_of=None, fresh_within=60).state == "unknown"
    assert Freshness.from_age(as_of=now, fresh_within=60).state == "ready"
    assert Freshness.from_age(as_of=now - timedelta(seconds=120), fresh_within=60).state == "stale"


def test_future_timestamp_is_not_negative_age():
    """时钟回拨/上游给未来时间 ⇒ 年龄必须夹到 0。

    负数年龄会让所有 `age > 阈值` 比较**恒假**，静默变成"永远新鲜"——
    正是本契约要消灭的那类失效。
    """
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    assert age_seconds_of(future) == 0.0
    assert Freshness.from_age(as_of=future, fresh_within=60).state == "ready"


def test_usable_is_not_fresh():
    """`stale` 有数据但不足以支撑结论——两个判定刻意分开，历史事故正是"有数据就当新鲜用"。"""
    f = Freshness.stale(reason="x", as_of=datetime.now(timezone.utc), age_seconds=120)
    assert f.is_usable() is True
    assert f.is_fresh() is False


def test_note_text_is_single_age():
    f = Freshness.stale(reason="数据滞后 300 秒，超出新鲜窗口 60 秒",
                        as_of=datetime.now(timezone.utc), age_seconds=300)
    note = f.note("全市场快照")
    assert note.count("300") == 1, note


# ---------- Quote 样板 ----------


def test_quote_freshness_uses_data_timestamp_first():
    old = datetime.now(timezone.utc) - timedelta(seconds=300)
    q = Quote(symbol="600519", price=100.0, source="t", data_timestamp=old)
    assert q.freshness(fresh_within=60).state == "stale"


def test_quote_invalid_quality_is_unavailable_even_if_just_received():
    q = Quote(symbol="600519", price=100.0, source="t", quality="invalid")
    assert q.freshness().state == "unavailable"


def test_quote_stale_quality_downgrades_ready():
    q = Quote(symbol="600519", price=100.0, source="t", quality="stale")
    f = q.freshness(fresh_within=600)
    assert f.state == "stale"
    assert "自评质量 stale" in (f.reason or "")


# ---------- 快照样板 ----------


def _svc(**kw) -> MarketSnapshotService:
    svc = MarketSnapshotService(poll_interval=60.0, save_interval=300.0,
                                parquet_dir=kw.pop("parquet_dir", __import__("pathlib").Path("/tmp")))
    for k, v in kw.items():
        setattr(svc, k, v)
    return svc


def test_snapshot_unavailable_before_first_success():
    svc = _svc()
    assert svc.freshness().state == "unavailable"


def test_snapshot_ready_within_three_polls():
    svc = _svc(breadth={"total": 1}, last_success=datetime.now(timezone.utc))
    assert svc.freshness().state == "ready"


def test_snapshot_stale_beyond_three_polls():
    svc = _svc(breadth={"total": 1},
               last_success=datetime.now(timezone.utc) - timedelta(seconds=300))
    assert svc.freshness().state == "stale"


def test_snapshot_degraded_when_upstream_failing_but_data_fresh():
    svc = _svc(breadth={"total": 1}, last_success=datetime.now(timezone.utc),
               consecutive_failures=3, last_error="boom")
    f = svc.freshness()
    assert f.state == "degraded"
    assert "连续失败 3 次" in (f.reason or "")


def test_off_hours_window_scales_with_poll_interval():
    """休市时轮询降到 240s，窗口必须是 poll_interval×3——写死常量会恒定误报 stale。"""
    svc = _svc(poll_interval=240.0, breadth={"total": 1},
               last_success=datetime.now(timezone.utc) - timedelta(seconds=300))
    assert svc.freshness().state == "ready"


def test_breadth_payload_keeps_legacy_keys_and_adds_freshness():
    svc = _svc(breadth={"total": 1, "up": 1}, last_success=datetime.now(timezone.utc))
    p = svc.breadth_payload()
    assert p["freshness"]["state"] == "ready"
    assert "snapshot_age_seconds" in p and "consecutive_failures" in p


# ---------- QuoteHub 样板：is_stale 背后其实是三种成因 ----------


def _hub(**kw):
    from app.services.quote_hub import QuoteHub

    class _P:
        name = "stub"

    h = QuoteHub(provider=_P(), poll_interval=1.0, stale_after=10.0)
    for k, v in kw.items():
        setattr(h, k, v)
    return h


def test_hub_freshness_three_causes_are_distinguishable():
    """旧 `is_stale()` 把三种成因压成一个布尔；契约必须把它们分开。

    分不开的代价：前端不知道"该显示占位符"还是"该显示昨收并标注"。
    """
    h = _hub()
    assert h.freshness().state == "unavailable"
    assert h.is_stale() is True

    h.last_success_refresh = datetime.now(timezone.utc)
    assert h.freshness().state == "ready"
    assert h.is_stale() is False

    h._closed_marked = True  # 休市：数据是最近交易日的，不冒充实时（红线 2）
    f = h.freshness()
    assert f.state == "stale" and "休市" in (f.reason or "")
    assert h.is_stale() is True


def test_hub_freshness_stale_by_age():
    h = _hub(last_success_refresh=datetime.now(timezone.utc) - timedelta(seconds=60))
    assert h.freshness().state == "stale"
    assert h.is_stale() is True


# ---------- S1-3：陈旧快照必须让结论降级可⻅ ----------

class _Hub:
    class provider:  # noqa: N801 - 简化桩
        name = "stub"

        @staticmethod
        async def get_limit_up_pool(_d):
            return []

        @staticmethod
        async def get_limit_break_pool(_d):
            return []


def _patch_calendar(monkeypatch, day: date):
    async def _days(_provider):
        # `trade_calendar.trading_days` 的契约是**归一化后的 date 对象**
        # （原始 provider 日历是字符串，直取会让比较全部失配——见 KB 中的调度教训）。
        # 必须给两个交易日：情绪判定要 anchor + prev。
        return [day - timedelta(days=1), day]

    monkeypatch.setattr(mc.tc, "trading_days", _days)


def test_stale_snapshot_downgrades_sentiment_confidence(monkeypatch):
    day = date(2026, 9, 11)
    _patch_calendar(monkeypatch, day)
    svc = _svc(breadth={"total": 5550, "up": 3000, "down": 2400, "flat": 100,
                        "suspended": 4, "limit_up": 90, "limit_down": 3},
               snapshot=[], poll_interval=60.0,
               last_success=datetime.now(timezone.utc) - timedelta(seconds=1200))
    out = _run(mc.compute_market_sentiment(_Hub(), svc))
    assert out["snapshot_freshness"]["state"] == "stale"
    assert out["confidence"] == "低"
    assert any("全市场快照" in c for c in out["caveats"])


def test_fresh_snapshot_does_not_add_caveat(monkeypatch):
    day = date(2026, 9, 11)
    _patch_calendar(monkeypatch, day)
    svc = _svc(breadth={"total": 5550, "up": 3000, "down": 2400, "flat": 100,
                        "suspended": 4, "limit_up": 90, "limit_down": 3},
               snapshot=[], poll_interval=60.0,
               last_success=datetime.now(timezone.utc))
    out = _run(mc.compute_market_sentiment(_Hub(), svc))
    assert out["snapshot_freshness"]["state"] == "ready"
    assert not any("全市场快照" in c for c in (out.get("caveats") or []))
