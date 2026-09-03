"""复盘 picks 维度（每日精选准确率 + 失误归因）单测（2026-09-04 新增维度）。

覆盖：准确率口径（达成/(达成+失误)，踏空/数据缺失不可评）、闸门日仅观察剔除、
失误逐股归因 findings、归因缺失/组合缺失的诚实降级、collect_picks 的 DB 读回。
"""
from sqlalchemy import select

from app.core.db import get_engine, get_session_factory
from app.models.daily_pick import DailyPickReview, DailyPickSet
from app.models.watchlist import Base
from app.picks.intraday_opportunity import top_watch_stocks
from app.review.analyzers import RulesAnalyzer
from app.review.collector import collect_picks
from app.review.config import load_methodology
from app.review.schemas import DataGap, PicksSnapshot, PickEntry, PickReviewEntry

Base.metadata.create_all(get_engine())

# 与 test_review.py 同纪律：落库测试用远离真实的日期，绝不污染生产报告
_TEST_TD = "20990102"


def _method():
    return load_methodology("v1")


def _picks_snapshot(items=1, reviews=True, obs=False) -> PicksSnapshot:
    entries = [
        PickEntry(symbol=f"60000{i}", name=f"股{i}", score=80.0, themes=["芯片"])
        for i in range(items)
    ]
    if obs:
        entries.append(PickEntry(symbol="600099", name="观察股", score=70.0, observation_only=True))
    revs = []
    if reviews:
        revs = [
            PickReviewEntry(symbol="600000", name="股0", verdict="good",
                            reason_category="gone_well", excess_pct=3.0, note="超额为正"),
            PickReviewEntry(symbol="600001", name="股1", verdict="bad",
                            reason_category="entry_bad", excess_pct=-3.0, note="追高介入"),
            PickReviewEntry(symbol="600002", name="股2", verdict="flat",
                            reason_category="missed", excess_pct=None, note="全天未回区间"),
        ]
        if obs:
            revs.append(PickReviewEntry(symbol="600099", name="观察股", verdict="bad",
                                        reason_category="logic_failed", excess_pct=-5.0, note="闸门日观察标的"))
    return PicksSnapshot(trade_date=_TEST_TD, combo_date=_TEST_TD, items=entries, reviews=revs)


def _run_picks(picks: PicksSnapshot):
    from app.review.schemas import MarketSnapshot, ReviewData, TradingSnapshot

    data = ReviewData(
        trade_date=_TEST_TD,
        market=MarketSnapshot(trade_date=_TEST_TD),
        trading=TradingSnapshot(trade_date=_TEST_TD),
        picks=picks,
    )
    dim = next(d for d in RulesAnalyzer().analyze(data, _method()) if d.key == "picks")
    return dim


# ---------------------------------------------------------------- analyzer


def test_picks_accuracy_and_failure_rows():
    dim = _run_picks(_picks_snapshot(items=3))
    assert dim.status == "ok"
    # 2 可判定（good+bad）中 1 达成 → 50%；missed 为不可评
    assert dim.evidence["accuracy_pct"] == 50.0
    assert dim.evidence["by_category"] == {"entry_bad": 1}
    # 失误逐股归因必须出现在 findings（用户硬要求）
    assert any("600001" in f and "entry_bad" in f for f in dim.findings)
    assert any(f.startswith("✗") for f in dim.findings)
    assert any("当日准确率" in f for f in dim.findings)


def test_picks_observation_only_excluded():
    dim = _run_picks(_picks_snapshot(items=3, obs=True))
    assert dim.evidence["observation_only_excluded"] == 1
    # 观察标的的 bad 不进 by_category、不进准确率
    assert dim.evidence["by_category"] == {"entry_bad": 1}
    assert any("仅观察" in j for j in dim.judgements)


def test_picks_no_combo_degrades_without_failures():
    # 与真实链路对齐：collector 在无组合时会标 gap（warn）→ 维度 degraded
    snap = PicksSnapshot(trade_date=_TEST_TD, combo_date=None, items=[])
    snap.gaps.append(DataGap(
        field="picks.combo", source="daily_pick_set",
        reason="无组合", impact="picks.每日精选对照", severity="warn",
    ))
    dim = _run_picks(snap)
    assert dim.status == "degraded"
    assert not any(f.startswith("✗") for f in dim.findings)
    assert any("无精选组合" in f for f in dim.findings)


def test_picks_missing_reviews_degrades():
    snap = _picks_snapshot(items=3, reviews=False)
    snap.gaps.append(DataGap(
        field="picks.reviews", source="daily_pick_review",
        reason="归因行缺失", impact="picks.准确率与失误归因", severity="warn",
    ))
    dim = _run_picks(snap)
    assert dim.status == "degraded"
    assert not dim.evidence  # 没有归因就不给准确率，绝不冒充 100%


def test_picks_category_hint_judgement():
    snap = PicksSnapshot(
        trade_date=_TEST_TD, combo_date=_TEST_TD,
        items=[PickEntry(symbol=f"60000{i}", name=f"股{i}") for i in range(3)],
        reviews=[
            PickReviewEntry(symbol="600000", name="股0", verdict="bad",
                            reason_category="sentiment_misread", excess_pct=-4.0, note="退潮期逆势"),
            PickReviewEntry(symbol="600001", name="股1", verdict="bad",
                            reason_category="sentiment_misread", excess_pct=-3.0, note="退潮期逆势"),
            PickReviewEntry(symbol="600002", name="股2", verdict="good",
                            reason_category="gone_well", excess_pct=2.0, note="健康"),
        ],
    )
    dim = _run_picks(snap)
    assert any("情绪误判" in j for j in dim.judgements)


# ---------------------------------------------------------------- collector（DB 读回）


def _cleanup(sf, td: str) -> None:
    db = sf()
    try:
        for r in db.execute(select(DailyPickReview).where(DailyPickReview.date == td)).scalars():
            db.delete(r)
        for r in db.execute(select(DailyPickSet).where(DailyPickSet.date == td)).scalars():
            db.delete(r)
        db.commit()
    finally:
        db.close()


def test_collect_picks_reads_combo_and_reviews():
    sf = get_session_factory()
    _cleanup(sf, _TEST_TD)
    db = sf()
    try:
        db.add(DailyPickSet(
            date=_TEST_TD,
            items='[{"symbol":"600519","name":"茅台","score":82.0,"themes":["白酒"],"observation_only":false}]',
            meta="{}",
        ))
        db.add(DailyPickReview(
            date=_TEST_TD, symbol="600519", name="茅台", verdict="good",
            reason_category="gone_well", excess_pct=2.5, note="走势健康",
        ))
        db.commit()
    finally:
        db.close()
    try:
        from datetime import date as _date

        snap = collect_picks(sf, _date(2099, 1, 2))
        assert snap.combo_date == _TEST_TD
        assert [i.symbol for i in snap.items] == ["600519"]
        assert len(snap.reviews) == 1 and snap.reviews[0].verdict == "good"
        assert snap.gaps == []
    finally:
        _cleanup(sf, _TEST_TD)


def test_collect_picks_missing_reviews_marks_gap():
    sf = get_session_factory()
    _cleanup(sf, _TEST_TD)
    db = sf()
    try:
        db.add(DailyPickSet(date=_TEST_TD, items='[{"symbol":"600519","name":"茅台"}]', meta="{}"))
        db.commit()
    finally:
        db.close()
    try:
        from datetime import date as _date

        snap = collect_picks(sf, _date(2099, 1, 2))
        assert snap.reviews == []
        assert any(g.field == "picks.reviews" for g in snap.gaps)
    finally:
        _cleanup(sf, _TEST_TD)


# ---------------------------------------------------------------- top_watch_stocks


def _payload():
    return {
        "trade_date": "20260904",
        "hot_available": True,
        "caveats": [],
        "themes": [
            {
                "theme": "算力", "stage": "发酵", "strength_tier": "T1",
                "stocks": [
                    {"symbol": "300001", "name": "A", "role": "龙头", "boards": 3,
                     "change_pct": 10.0, "reason": "核心",
                     "distinctiveness": {"level": "高", "basis": "人气第2"},
                     "certainty": {"level": "高", "basis": "题材发酵"}},
                    {"symbol": "300002", "name": "B", "role": "跟风", "boards": 1,
                     "change_pct": 5.0, "reason": None,
                     "distinctiveness": {"level": "低", "basis": "无人气"},
                     "certainty": {"level": "中", "basis": "题材发酵"}},
                    {"symbol": "300003", "name": "C", "role": "中军", "boards": 2,
                     "change_pct": 7.0, "reason": None,
                     "distinctiveness": {"level": "中", "basis": "2板"},
                     "certainty": {"level": "unknown", "basis": "阶段缺失"}},
                ],
            },
            {
                "theme": "机器人", "stage": "启动", "strength_tier": "T2",
                "stocks": [
                    {"symbol": "600100", "name": "D", "role": "首板", "boards": 1,
                     "change_pct": 10.0, "reason": None,
                     "distinctiveness": {"level": "高", "basis": "人气第5"},
                     "certainty": {"level": "中", "basis": "题材启动"}},
                    {"symbol": "600101", "name": "E", "role": "首板", "boards": 1,
                     "change_pct": 3.0, "reason": None,
                     "distinctiveness": {"level": "高", "basis": "人气第8"},
                     "certainty": {"level": "低", "basis": "题材启动"}},
                ],
            },
        ],
    }


def test_top_watch_tier_order_and_exclusion():
    out = top_watch_stocks(_payload())
    symbols = [i["symbol"] for i in out["items"]]
    # T1: cert高+dist高；T2: cert高；T3: cert中+dist高（D）
    assert symbols == ["300001", "600100"]
    assert out["items"][0]["tier"] == 1
    # unknown/低一律不入选
    assert "300003" not in symbols and "300002" not in symbols and "600101" not in symbols
    assert out["criteria"] and out["trade_date"] == "20260904"


def test_top_watch_limit_keeps_strongest():
    out = top_watch_stocks(_payload(), limit=1)
    assert [i["symbol"] for i in out["items"]] == ["300001"]
