"""策略级登记册测试：键一致性守卫 / 两套口径 / 三态纪律（P1-37 / P1-38）。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.daily_pick import DailyPickReview, DailyPickSet
from app.models.watch_ledger import WatchLedger
from app.picks.strategy_registry import (
    BASIS_ABSOLUTE,
    BASIS_MARKET_NEUTRAL,
    BY_KEY,
    SPECS,
    collect_all_strategy_health,
    collect_strategy_health,
    list_strategy_keys,
    strategy_verdicts,
)

# import 即注册进 Base.metadata（create_all 需要）
_REGISTERED = (DailyPickReview, DailyPickSet, WatchLedger)

REGISTRY_DOC = Path(__file__).resolve().parents[2] / "docs" / "strategy" / "strategy-registry.md"


@pytest.fixture()
def sf():
    engine = create_engine("sqlite:///:memory:")
    from app.models.watchlist import Base

    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _add_pick_review(sf, date: str, rows: list[tuple[str, str, float | None]]) -> None:
    with sf() as db:
        db.add(DailyPickSet(date=date, items="[]", meta="{}"))
        for sym, verdict, ex in rows:
            db.add(DailyPickReview(date=date, symbol=sym, verdict=verdict,
                                   reason_category="gone_well", excess_pct=ex))
        db.commit()


def _add_watch(sf, date: str, rows: list[tuple[str, str | None, float | None]]) -> None:
    """rows: (symbol, verdict, pnl_pct)；verdict=None 表示未清算。"""
    with sf() as db:
        for sym, verdict, pnl in rows:
            db.add(WatchLedger(
                trade_date=date, symbol=sym, name="", layer="l", source_theme="",
                reason="{}", is_leader=0, boards=0, entry_price=10.0,
                entry_time="", status="settled" if verdict else "tracking",
                close_price=None, pnl_pct=pnl, verdict=verdict,
            ))
        db.commit()


class TestRegistryConsistency:
    """登记册与文档必须同步（改一处必改另一处）。"""

    def test_keys_are_unique(self):
        keys = [s.key for s in SPECS]
        assert len(keys) == len(set(keys))

    def test_doc_lists_every_key(self):
        """docs/strategy/strategy-registry.md §1 总表必须含全部代码侧策略键（反漂移守卫）。"""
        text = REGISTRY_DOC.read_text(encoding="utf-8")
        missing = [s.key for s in SPECS if f"`{s.key}`" not in text]
        assert not missing, f"以下策略键未登记进 docs/strategy/strategy-registry.md：{missing}"

    def test_doc_status_symbol_matches_code(self):
        """文档状态符号与代码 status 必须一致（active→🟢 / observing→🟡 / rejected→⛔）。"""
        text = REGISTRY_DOC.read_text(encoding="utf-8")
        symbol = {"active": "🟢", "observing": "🟡", "rejected": "⛔"}
        for spec in SPECS:
            row = re.search(rf"^\|\s*`{re.escape(spec.key)}`\s*\|[^|]*\|\s*(.)", text, re.M)
            assert row, f"总表缺 `{spec.key}` 行"
            assert row.group(1) == symbol[spec.status], (
                f"{spec.key}: 文档标 {row.group(1)} 但代码 status={spec.status}"
            )

    def test_rejected_and_observing_not_evaluable(self):
        """非 active 策略不该被当作在跑的策略评估（无实时落库）。"""
        for s in SPECS:
            if s.status != "active":
                assert s.evaluable is False, f"{s.key} 非 active 却标 evaluable"


class TestBasisDiscipline:
    """口径必须显式：不同 basis 不可互相解释（KB-ENG-39）。"""

    def test_daily_picks_is_market_neutral(self):
        assert BY_KEY["daily_picks"].basis == BASIS_MARKET_NEUTRAL

    def test_intraday_watch_is_absolute(self):
        assert BY_KEY["intraday_watch"].basis == BASIS_ABSOLUTE

    def test_every_key_has_basis(self):
        for s in SPECS:
            assert s.basis in (BASIS_MARKET_NEUTRAL, BASIS_ABSOLUTE)

    def test_payload_carries_basis(self, sf):
        out = collect_strategy_health(sf, "intraday_watch")
        assert out["basis"] == BASIS_ABSOLUTE


class TestThreeStateDiscipline:
    """insufficient / thin / no_pipeline 都是「判不出」，绝不是 ok。"""

    def test_no_pipeline_for_rejected(self, sf):
        out = collect_strategy_health(sf, "triple_volume")
        assert out["status"] == "no_pipeline"
        assert out["status"] != "ok"

    def test_no_pipeline_for_observing(self, sf):
        assert collect_strategy_health(sf, "pullback_reversal")["status"] == "no_pipeline"

    def test_unknown_key(self, sf):
        out = collect_strategy_health(sf, "not_a_strategy")
        assert out["status"] == "unknown"

    def test_insufficient_when_no_groups(self, sf):
        out = collect_strategy_health(sf, "daily_picks")
        assert out["status"] == "insufficient"

    def test_thin_when_groups_enough_but_picks_few(self, sf):
        """10 个组日 × 每日 1 笔 = 10 笔 < 20 → thin（组日数达标但统计不可靠）。"""
        for d in range(1, 11):
            _add_pick_review(sf, f"2026-08-{d:02d}", [("600000", "good", 1.0)])
        out = collect_strategy_health(sf, "daily_picks")
        assert out["counts"]["groups"] == 10
        assert out["counts"]["picks"] == 10
        assert out["status"] == "thin"

    def test_picks_enough_passes_thin_gate(self, sf):
        """10 组日 × 每日 3 笔 = 30 笔 ≥ 20 且表现健康 → 不因 thin 被拦。"""
        for d in range(1, 11):
            _add_pick_review(sf, f"2026-08-{d:02d}", [
                ("600000", "good", 1.0), ("000001", "good", 1.0), ("300001", "good", 1.0),
            ])
        out = collect_strategy_health(sf, "daily_picks")
        assert out["counts"]["picks"] == 30
        assert out["status"] in ("ok", "warning")

    def test_unsettled_watch_rows_excluded(self, sf):
        """未清算行（verdict=None）不参与统计：'还没结算' ≠ '持平'。"""
        _add_watch(sf, "2026-09-08", [
            ("600000", "success", 1.2), ("000001", "fail", -3.0),
            ("300001", None, None),   # 未清算，必须被排除
        ])
        out = collect_strategy_health(sf, "intraday_watch")
        assert out["counts"]["groups"] == 1
        assert out["counts"]["picks"] == 2  # 3 行里只有 2 行已清算
        hist = out["history"][0]
        assert hist["good"] == 1 and hist["bad"] == 1 and hist["flat"] == 0


class TestAggregation:
    def test_watch_ledger_absolute_pnl_aggregated(self, sf):
        _add_watch(sf, "2026-09-08", [("600000", "success", 2.0), ("000001", "fail", -4.0)])
        out = collect_strategy_health(sf, "intraday_watch")
        # (2.0 + -4.0) / 2 = -1.0，绝对口径
        assert out["history"][0]["mean_excess"] == pytest.approx(-1.0)

    def test_all_health_covers_every_key(self, sf):
        out = collect_all_strategy_health(sf)
        assert len(out["strategies"]) == len(SPECS)
        assert out["counts"]["total"] == len(SPECS)
        assert out["counts"]["evaluable"] == 2  # 只有两条现役策略有落库
        assert "basis" in out["caveat"]

    def test_verdicts_only_attention(self, sf):
        assert strategy_verdicts(sf) == []  # 空库：无一条进入预警

    def test_list_keys_includes_non_evaluable(self):
        keys = [k["key"] for k in list_strategy_keys()]
        assert "triple_volume" in keys  # 已否决的策略也应可见（可追溯）
        assert len(keys) == len(SPECS)
