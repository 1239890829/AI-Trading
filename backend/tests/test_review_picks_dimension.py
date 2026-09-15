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
    """opportunities payload：`stocks`=涨停梯队（仅参考）、`participants`=可参与候选。

    2026-09-15 口径变更后 `top_watch_stocks` 的 `items` 取 participants、
    `reference_items` 取 stocks；本 fixture 两侧都造，才能同时钉住"谁入选"与
    "谁只作参考"——只造一侧会让另一半静默失去覆盖。
    """
    return {
        "trade_date": "20260904",
        "hot_available": True,
        "caveats": [],
        "themes": [
            {
                "theme": "算力", "stage": "发酵", "strength_tier": "T1",
                "participants": [
                    # linkage=高 → tier 1
                    {"symbol": "300011", "name": "P1", "change_pct": 7.2,
                     "linkage": {"level": "高", "basis": "题材发酵 · 已进临板区"},
                     "tradability": {"level": "可参与", "basis": "未封在涨停板，报价可成交"},
                     "basis": "题材内涨停 4 家形成集中，本股尚未涨停（7.2%）"},
                    # linkage=中 → tier 2
                    {"symbol": "300012", "name": "P2", "change_pct": 3.4,
                     "linkage": {"level": "中", "basis": "题材发酵 · 涨幅 3.4% 有跟进迹象"},
                     "tradability": {"level": "可参与", "basis": "未封在涨停板，报价可成交"},
                     "basis": "题材内涨停 4 家形成集中，本股尚未涨停（3.4%）"},
                    # linkage=低 → 一律不入选（三态纪律：绝不拿"低"凑数）
                    {"symbol": "300013", "name": "P3", "change_pct": 1.1,
                     "linkage": {"level": "低", "basis": "联动迹象弱"},
                     "tradability": {"level": "可参与", "basis": "未封在涨停板，报价可成交"},
                     "basis": "联动迹象弱"},
                ],
                "stocks": [
                    {"symbol": "300001", "name": "A", "role": "龙头", "boards": 3,
                     "change_pct": 10.0, "reason": "核心", "first_seal_time": "09:25:00",
                     "tradability": {"level": "不可参与",
                                     "basis": "开盘即涨停（竞价即封，首封 09:25）——全天无买入机会"},
                     "distinctiveness": {"level": "高", "basis": "人气第2"},
                     "certainty": {"level": "高", "basis": "题材发酵"}},
                    {"symbol": "300002", "name": "B", "role": "跟风", "boards": 1,
                     "change_pct": 5.0, "reason": None, "first_seal_time": "14:05:00",
                     "tradability": {"level": "不可参与", "basis": "已封在涨停板"},
                     "distinctiveness": {"level": "低", "basis": "无人气"},
                     "certainty": {"level": "中", "basis": "题材发酵"}},
                ],
            },
            {
                "theme": "机器人", "stage": "启动", "strength_tier": "T2",
                "participants": [
                    {"symbol": "600110", "name": "Q1", "change_pct": 5.0,
                     "linkage": {"level": "中", "basis": "题材启动 · 涨幅 5.0% 有跟进迹象"},
                     "tradability": {"level": "可参与", "basis": "未封在涨停板，报价可成交"},
                     "basis": "题材内涨停 3 家形成集中，本股尚未涨停（5.0%）"},
                    # unknown（题材阶段缺失）→ 不入选
                    {"symbol": "600111", "name": "Q2", "change_pct": 4.0,
                     "linkage": {"level": "unknown", "basis": "题材阶段缺失"},
                     "tradability": {"level": "可参与", "basis": "未封在涨停板，报价可成交"},
                     "basis": "题材阶段缺失"},
                ],
                "stocks": [
                    {"symbol": "600100", "name": "D", "role": "首板", "boards": 1,
                     "change_pct": 10.0, "reason": None, "first_seal_time": "10:31:00",
                     "tradability": {"level": "不可参与", "basis": "已封在涨停板（首封 10:31）"},
                     "distinctiveness": {"level": "高", "basis": "人气第5"},
                     "certainty": {"level": "中", "basis": "题材启动"}},
                ],
            },
        ],
    }


def test_top_watch_items_are_participants_only():
    """`items` 只收可参与的联动候选；低/unknown 不入选；涨停梯队不在 items 里。"""
    out = top_watch_stocks(_payload())
    symbols = [i["symbol"] for i in out["items"]]
    # tier1（linkage 高）在前，tier2 按涨幅降序（600110 5.0% > 300012 3.4%）
    assert symbols == ["300011", "600110", "300012"]
    assert out["items"][0]["tier"] == 1
    # 三态纪律：低/unknown 一律不入选
    assert "300013" not in symbols and "600111" not in symbols
    # 涨停梯队**一只都不在** items（旧口径的病根：名单全是买不进的票）
    for sym in ("300001", "300002", "600100"):
        assert sym not in symbols
    # 未涨停 ⇒ 不臆造封板语义字段
    assert out["items"][0]["role"] is None and out["items"][0]["boards"] is None
    assert out["items"][0]["certainty"] is None
    assert out["criteria"] and out["trade_date"] == "20260904"


def test_top_watch_reference_items_are_ladder_with_tradability():
    """涨停梯队进 `reference_items`，且每只都带可参与性判定（开盘即涨停须被点名）。"""
    out = top_watch_stocks(_payload())
    ref = {r["symbol"]: r for r in out["reference_items"]}
    assert set(ref) == {"300001", "300002", "600100"}
    assert all(r["reference_only"] is True for r in out["reference_items"])
    assert all((r["tradability"] or {}).get("level") == "不可参与" for r in out["reference_items"])
    assert "开盘即涨停" in ref["300001"]["tradability"]["basis"]
    assert ref["300001"]["first_seal_time"] == "09:25:00"
    assert out["reference_total"] == 3
    # 摘要文案必须让"上面那批买不进"一眼可见，且**不得带 Markdown 记号**
    # （这些字符串直接进 innerText，`**…**` 会被原样显示出来——2026-09-15 实测）
    assert "仅作题材集中度的参考信息" in out["reference_criteria"]
    assert "**" not in out["reference_criteria"] and "**" not in out["criteria"]


def test_top_watch_limit_keeps_strongest():
    out = top_watch_stocks(_payload(), limit=1)
    assert [i["symbol"] for i in out["items"]] == ["300011"]
    assert [i["symbol"] for i in out["reference_items"]] == ["300001"]  # 梯队按连板降序


def test_top_watch_reference_drops_boards_without_permission():
    """参考区也不展示无交易权限的板块，且**计数留痕**（不是静默消失）。

    2026-09-15 用户「创业板的不进，只有主板的权限现在」。参考区虽是"仅参考"，
    但它是给人看的名单——留着买不了的票只会占位并误导注意力。
    """
    payload = {
        "trade_date": "20260915",
        "hot_available": True,
        "caveats": [],
        "board_excluded_reference": 1,
        "themes": [
            {
                "theme": "固态电池", "stage": "启动", "strength_tier": "T1",
                "participants": [],
                "stocks": [
                    {"symbol": "002882", "name": "金龙羽", "board": "深市主板", "tradable": True,
                     "boards": 1, "change_pct": 9.99, "first_seal_time": "09:30:06",
                     "tradability": {"level": "不可参与", "basis": "已封在涨停板"},
                     "distinctiveness": {"level": "低", "basis": ""},
                     "certainty": {"level": "高", "basis": ""}},
                    {"symbol": "301662", "name": "宏工科技", "board": "创业板", "tradable": False,
                     "boards": 1, "change_pct": 20.0, "first_seal_time": "09:31:00",
                     "tradability": {"level": "不可参与", "basis": "已封在涨停板"},
                     "distinctiveness": {"level": "低", "basis": ""},
                     "certainty": {"level": "高", "basis": ""}},
                ],
            }
        ],
    }
    out = top_watch_stocks(payload)
    assert [r["symbol"] for r in out["reference_items"]] == ["002882"]
    assert out["reference_items"][0]["board"] == "深市主板"
    assert out["board_excluded_reference"] == 1
    assert "非主板" in out["reference_criteria"]
    assert "沪市主板" in out["tradable_boards"]
