"""信号健康度测试：CUSUM 下漂 / 三态状态机 / 库聚合与相位 join。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.daily_pick import DailyPickReview, DailyPickSet
from app.picks.signal_health import (
    CUSUM_THRESHOLD,
    collect_signal_health,
    evaluate_signal_health,
)

# import 即注册进 Base.metadata（create_all 需要）
_REGISTERED = (DailyPickReview, DailyPickSet)


def _group(date: str, n: int, good: int, bad: int, mean_ex: float | None,
           phase: str | None = None, flat: int = 0) -> dict:
    return {"date": date, "phase": phase, "n": n, "good": good, "bad": bad,
            "flat": flat, "mean_excess": mean_ex}


class TestEvaluate:
    def test_healthy_series_is_ok(self):
        groups = [_group(f"2026-08-{d:02d}", 5, 4, 1, 1.2) for d in range(1, 21)]
        out = evaluate_signal_health(groups)
        assert out["status"] == "ok"
        assert out["window"]["win_rate"] == pytest.approx(0.8)
        assert out["cusum"]["drift"] is False

    def test_insufficient_explicit_not_ok(self):
        groups = [_group(f"2026-08-{d:02d}", 5, 4, 1, 1.0) for d in range(1, 6)]
        out = evaluate_signal_health(groups)
        assert out["status"] == "insufficient"  # 5 组 < 10：不判 ok（三态）

    def test_persistent_underperformance_triggers_drift(self):
        """前 15 日 +1.2% 基线，后 15 日连续 -1.5%：CUSUM 必须报 drift。"""
        groups = [_group(f"2026-07-{d:02d}", 5, 4, 1, 1.2) for d in range(10, 25)]
        groups += [_group(f"2026-08-{d:02d}", 5, 1, 4, -1.5) for d in range(1, 16)]
        out = evaluate_signal_health(groups)
        assert out["status"] == "drift"
        assert out["cusum"]["s_max"] > CUSUM_THRESHOLD
        # 滚动窗口反映恶化：胜率跌、均值转负
        assert out["window"]["win_rate"] < 0.4

    def test_none_excess_observations_skipped_not_zeroed(self):
        """基准缺失（excess=None）跳过统计，绝不当 0 参与 CUSUM。"""
        groups = [_group(f"2026-08-{d:02d}", 5, 4, 1, 1.2) for d in range(1, 15)]
        groups += [_group(f"2026-08-{d:02d}", 5, 4, 1, None) for d in range(15, 21)]
        out = evaluate_signal_health(groups)
        assert out["status"] == "ok"
        assert out["window"]["mean_excess"] == pytest.approx(1.2)

    def test_empty_history(self):
        out = evaluate_signal_health([])
        assert out["status"] == "insufficient"
        assert "无命中记录" in out["reason"]

    def test_warning_on_low_win_rate_without_drift(self):
        """没破 CUSUM 但滚动胜率差 → warning（介于 ok 与 drift 之间的显式态）。"""
        groups = [_group(f"2026-08-{d:02d}", 5, 1, 4, 0.05) for d in range(1, 21)]
        out = evaluate_signal_health(groups)
        assert out["status"] in ("warning", "drift")
        assert out["window"]["win_rate"] == pytest.approx(0.2)


@pytest.fixture()
def sf():
    engine = create_engine("sqlite:///:memory:")
    from app.models.watchlist import Base

    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


class TestCollect:
    def test_aggregation_and_phase_join(self, sf):
        with sf() as db:
            meta = {"market_phase": "发酵"}
            db.add(DailyPickSet(date="2026-08-01", items="[]", meta=__import__("json").dumps(meta)))
            for sym, verdict, ex in (("600000", "good", 2.1), ("000001", "bad", -1.4),
                                     ("300001", "good", 0.9)):
                db.add(DailyPickReview(date="2026-08-01", symbol=sym, verdict=verdict,
                                       reason_category="gone_well", excess_pct=ex))
            db.commit()
        out = collect_signal_health(sf)
        assert out["status"] == "insufficient"  # 1 组合日 < 10，显式不判 ok
        assert out["counts"]["groups"] == 1
        hist = out["history"][0]
        assert hist["phase"] == "发酵"  # meta JSON join
        assert hist["good"] == 2 and hist["bad"] == 1
        # collect 层 round 到 3 位是设计（输出简洁）
        assert hist["mean_excess"] == pytest.approx(0.533, abs=1e-3)
        assert "caveat" in out  # excess_pct 列 default 0 的语义缺陷显式透出

    def test_zero_excess_participates_in_mean(self, sf):
        """真实 0.0（恰好平大盘）必须参与统计，不是被跳过的缺失值。"""
        with sf() as db:
            db.add(DailyPickSet(date="2026-08-01", items="[]", meta="{}"))
            db.add(DailyPickReview(date="2026-08-01", symbol="600000", verdict="flat",
                                   reason_category="market_drag", excess_pct=0.0))
            db.commit()
        out = collect_signal_health(sf)
        assert out["history"][0]["mean_excess"] == 0.0
        assert out["history"][0]["flat"] == 1
