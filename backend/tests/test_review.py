"""盘后复盘 Agent 的纯逻辑测试（不触发网络）。

覆盖：规则分析器三维度、阻断维度不产出判断、改进项 P0 优先级、
模型路由降级、元结论分类、方法论版本加载、报告落库/检索/对比。
"""
import sys

sys.path.insert(0, ".")

from app.core.db import get_engine, get_session_factory
from app.models.watchlist import Base
from app.review.analyzers import LLMAnalyzer, RulesAnalyzer
from app.review.config import ensure_default_methodology_file, load_methodology
from app.review.model_router import ModelRouter
from app.review.methodology import build_meta_insights
from app.review.schemas import (
    DataGap,
    DimensionResult,
    IndexQuote,
    MarketSnapshot,
    ModelUsage,
    OrderRecord,
    PositionRecord,
    ReviewData,
    ReviewReport,
    TradingSnapshot,
)
from app.review.methodology import evaluate_historical_effectiveness
from app.review.storage import (
    REPORT_DIR,
    compare_reports,
    get_report,
    list_reports,
    save_report,
    update_action_item_status,
)
from app.review.synthesis import build_action_items

Base.metadata.create_all(get_engine())
ensure_default_methodology_file()


# ⚠️ 落库类测试必须用这个日期，**绝不能用真实交易日**。
# `save_report` 是「同交易日覆盖」，用真实日期会把生产报告覆盖成测试摘要：
# 2026-09-01 核查时发现 data/review/reports/20260828.json 的 summary 已是
# "summary-20260828"、generated_at 是测试运行时刻——真实的 20260828 报告已丢失。
_TEST_TD = "20990101"


def _ok_data(trade_date: str = "20260828") -> ReviewData:
    mkt = MarketSnapshot(
        trade_date=trade_date,
        indices=[IndexQuote(symbol="sh000001", name="上证", close=3000.0, change_pct=-0.5)],
        breadth={"up": 2000, "down": 2500, "limit_up": 50, "limit_down": 10},
        sentiment={"phase": "分歧", "temperature": 30, "confidence": 0.6, "phase_unreliable": False},
        themes=[],
        theme_summary={"theme_count": 3, "broken_ladder": 0, "summary": {}},
    )
    trad = TradingSnapshot(
        trade_date=trade_date,
        account={"cash": 900000, "initial_cash": 1000000},
        orders=[OrderRecord(
            id=1, symbol="600519", side="buy", price=100.0, quantity=100,
            status="filled", filled_price=100.0, fee=5.0,
            created_at="2026-08-28T09:35:00+08:00",
        )],
        positions=[PositionRecord(
            symbol="600519", quantity=100, cost_price=100.0,
            last_price=105.0, pnl=500.0, pnl_pct=5.0,
        )],
        realized_pnl=0.0, trade_count=1,
    )
    return ReviewData(trade_date=trade_date, market=mkt, trading=trad)


def _method():
    return load_methodology("v1")


def test_rules_analyzer_three_dimensions():
    dims = RulesAnalyzer().analyze(_ok_data(), _method())
    assert {d.key for d in dims} == {"trades", "market", "system"}
    market = next(d for d in dims if d.key == "market")
    assert market.status == "ok"
    assert any("分歧" in j for j in market.judgements)


def test_blocked_dimension_produces_no_judgements():
    data = _ok_data()
    data.market.gaps.append(DataGap(
        field="breadth", source="snapshot_service.breadth_payload",
        reason="快照未就绪", impact="market.宽度与情绪", severity="block",
    ))
    dims = RulesAnalyzer().analyze(data, _method())
    market = next(d for d in dims if d.key == "market")
    assert market.status == "blocked"
    assert market.judgements == []  # 阻断维度不臆测结论


def test_action_items_p0_for_block_gap():
    data = _ok_data()
    data.market.gaps.append(DataGap(
        field="sentiment", source="market_context",
        reason="日历不可用", impact="market.情绪阶段", severity="block",
    ))
    dims = RulesAnalyzer().analyze(data, _method())
    items = build_action_items(data, dims, _method())
    p0 = [i for i in items if i.priority == "P0"]
    assert any("数据缺失" in i.title for i in p0)


def test_model_router_degrades_without_llm():
    router = ModelRouter(requested="llm", llm=LLMAnalyzer())  # 无 base_url/api_key
    analyzer, usage = router.resolve()
    assert analyzer.name == "rules"
    assert usage.degraded is True
    assert usage.actual == "rules"
    assert usage.requested == "llm"


def test_meta_insights_classification():
    method = _method()
    data = _ok_data()
    # 三条分支：有判断→effective / 无判断→unknown / 阻断→ineffective
    d_eff = DimensionResult(key="market", title="", status="ok", findings=["x"], judgements=["有判断"])
    d_unk = DimensionResult(key="trades", title="", status="ok", findings=["x"], judgements=[])
    d_blk = DimensionResult(
        key="system", title="", status="blocked", findings=[],
        judgements=[], evidence={"blocked_reason": "缺数据"},
    )
    meta = build_meta_insights(data, [d_eff, d_unk, d_blk], method)
    by_dim = {m.dimension: m.effectiveness for m in meta}
    assert by_dim["market"] == "effective"
    assert by_dim["trades"] == "unknown"
    assert by_dim["system"] == "ineffective"


def test_methodology_version_loads():
    method = load_methodology("v1")
    assert method.version == "v1"
    # 未知版本回退默认，而非报错
    assert load_methodology("does-not-exist").version == "v1"


def _report(trade_date: str) -> ReviewReport:
    data = _ok_data(trade_date)
    dims = RulesAnalyzer().analyze(data, _method())
    items = build_action_items(data, dims, _method())
    meta = build_meta_insights(data, dims, _method())
    return ReviewReport(
        trade_date=trade_date,
        model=ModelUsage(requested="rules", actual="rules", degraded=False),
        data=data, dimensions=dims, action_items=items,
        meta_insights=meta, summary=f"summary-{trade_date}",
    )


def test_storage_roundtrip():
    sf = get_session_factory()
    saved = save_report(sf, _report(_TEST_TD))
    got = get_report(sf, _TEST_TD)
    assert got is not None
    assert got.summary == f"summary-{_TEST_TD}"
    assert got.review_id == saved.review_id
    assert len(got.action_items) == len(saved.action_items)
    # 结构化列可检索
    rows = list_reports(sf, limit=5)
    assert any(r["trade_date"] == _TEST_TD for r in rows)


def test_compare_reports_logic():
    a = _report("20260827")
    b = _report("20260828")
    b.data.market.gaps.append(DataGap(
        field="indices", source="hub.get_indices", reason="r",
        impact="market.指数走势", severity="warn",
    ))
    cmp = compare_reports(a, b)
    assert cmp["from"] == "20260827"
    assert cmp["to"] == "20260828"
    assert "indices" in cmp["gaps_new"]


# ---------------------------------------------------------------- 改进项处置闭环

# 与 _TEST_TD 分开，避免与 test_storage_roundtrip 互相覆盖
_TEST_TD_AI = "20990102"


def _report_with_action_item() -> ReviewReport:
    """带至少一条改进项的报告。

    `_ok_data` 无 gap 时 `build_action_items` 产出为空（这正是它"健康"的表现），
    所以这里注入一个阻断级缺失，逼出一条 P0/data 改进项。
    """
    data = _ok_data(_TEST_TD_AI)
    data.market.gaps.append(DataGap(
        field="breadth", source="snapshot_service.breadth_payload",
        reason="测试注入", impact="market.宽度与情绪", severity="block",
    ))
    dims = RulesAnalyzer().analyze(data, _method())
    items = build_action_items(data, dims, _method())
    assert items, "测试前置失败：注入阻断缺失后应至少产出一条改进项"
    return ReviewReport(
        trade_date=_TEST_TD_AI,
        model=ModelUsage(requested="rules", actual="rules", degraded=False),
        data=data, dimensions=dims, action_items=items,
        meta_insights=[], summary=f"action-item-{_TEST_TD_AI}",
    )


def _cleanup(sf, trade_date: str) -> None:
    """删除测试报告（库 + 落盘文件）。

    不清理会污染两处：报告列表多出测试日期，以及有效性统计里混入测试数据
    ——而本组测试断言的正是采纳率变化。
    """
    from sqlalchemy import select as _select

    from app.review.models import (
        ReviewActionItemRow,
        ReviewMetaInsightRow,
        ReviewReportRow,
    )

    db = sf()
    try:
        rows = db.execute(
            _select(ReviewReportRow).where(ReviewReportRow.trade_date == trade_date)
        ).scalars().all()
        rids = [r.review_id for r in rows]
        for r in rows:
            db.delete(r)
        for rid in rids:
            db.query(ReviewActionItemRow).filter(
                ReviewActionItemRow.review_id == rid
            ).delete()
            db.query(ReviewMetaInsightRow).filter(
                ReviewMetaInsightRow.review_id == rid
            ).delete()
        db.commit()
    finally:
        db.close()
    (REPORT_DIR / f"{trade_date}.json").unlink(missing_ok=True)


def test_get_report_fills_action_item_pk():
    """改进项 id 必须回填为数据库主键——前端靠它寻址 PATCH。

    落库时并未持久化 `ActionItem.id`（payload 里的 `AI-xxxxxxxx` 只是报告内临时编号），
    不回填的话前端拿到的 id 在库里根本查不到，处置入口形同虚设。
    """
    sf = get_session_factory()
    save_report(sf, _report_with_action_item())
    try:
        got = get_report(sf, _TEST_TD_AI)
        assert got is not None and got.action_items
        ids = [i.id for i in got.action_items]
        assert all(i.isdigit() for i in ids), f"应回填整数主键，实际 {ids}"
        assert len(set(ids)) == len(ids), "id 必须唯一，否则会错配到别的改进项"
    finally:
        _cleanup(sf, _TEST_TD_AI)


def test_disposition_survives_report_reread():
    """处置结果必须能从报告详情回读到——否则闭环在"读"这一侧就断了。

    `get_report` 读的是生成时的 payload 快照，里面的 status 恒为 pending。
    2026-09-01 实测：PATCH 返回 confirmed/200，再拉报告详情仍是 pending，
    界面表现为"点了确认却没生效"。处置状态的权威来源只能是表行。
    """
    sf = get_session_factory()
    save_report(sf, _report_with_action_item())
    try:
        first = get_report(sf, _TEST_TD_AI)
        assert first is not None
        pk = first.action_items[0].id
        assert first.action_items[0].status == "pending"

        update_action_item_status(sf, pk, "confirmed")

        again = get_report(sf, _TEST_TD_AI)
        assert again is not None
        assert again.action_items[0].status == "confirmed", (
            "处置后回读报告详情必须是 confirmed；"
            "若仍是 pending 说明 get_report 返回的是 payload 快照而非表行状态"
        )
    finally:
        _cleanup(sf, _TEST_TD_AI)


def test_update_action_item_status_lifecycle():
    """pending → confirmed → applied，状态与 resolved_at 同步推进。"""
    sf = get_session_factory()
    save_report(sf, _report_with_action_item())
    try:
        got = get_report(sf, _TEST_TD_AI)
        iid = got.action_items[0].id

        r1 = update_action_item_status(sf, iid, "confirmed", "认可，待实施")
        assert r1["status"] == "confirmed"
        assert r1["resolved_at"] is not None

        r2 = update_action_item_status(sf, iid, "applied", "已实施")
        assert r2["status"] == "applied"
        # 再次读取应持久化，而不是只在内存里
        assert get_report(sf, _TEST_TD_AI).action_items[0].id == iid
    finally:
        _cleanup(sf, _TEST_TD_AI)


def test_update_action_item_status_guards():
    """非法状态 / 缺说明 / 不存在的 id 都必须显式报错，不静默吞掉。"""
    sf = get_session_factory()
    save_report(sf, _report_with_action_item())
    try:
        iid = get_report(sf, _TEST_TD_AI).action_items[0].id

        for bad in ("done", "", "P0"):
            try:
                update_action_item_status(sf, iid, bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"非法状态 {bad!r} 应抛 ValueError")

        # 驳回与回退必须写理由——没有理由的处置后期无法归因
        for st in ("rejected", "reverted"):
            try:
                update_action_item_status(sf, iid, st, "   ")
            except ValueError:
                pass
            else:
                raise AssertionError(f"{st} 缺 note 应抛 ValueError")

        try:
            update_action_item_status(sf, "999999999", "confirmed")
        except LookupError:
            pass
        else:
            raise AssertionError("不存在的改进项应抛 LookupError")

        try:
            update_action_item_status(sf, "not-a-number", "confirmed")
        except ValueError:
            pass
        else:
            raise AssertionError("非整数 item_id 应抛 ValueError")
    finally:
        _cleanup(sf, _TEST_TD_AI)


def test_revert_to_pending_clears_resolved_at():
    """撤销处置（回到 pending）要连处置时间一起清掉，否则时间线自相矛盾。"""
    sf = get_session_factory()
    save_report(sf, _report_with_action_item())
    try:
        iid = get_report(sf, _TEST_TD_AI).action_items[0].id
        assert update_action_item_status(sf, iid, "confirmed", "x")["resolved_at"]
        assert update_action_item_status(sf, iid, "pending")["resolved_at"] is None
    finally:
        _cleanup(sf, _TEST_TD_AI)


def test_adoption_rate_reflects_disposition():
    """闭环验证：改进项被处置后，采纳率统计必须真的变化。

    这是 2026-09-01 核查出的病根——107 条改进项全部 pending、采纳率恒为 0，
    连带让「样本≥5 且采纳率<20% → 该维度疑似产出噪音」的演进建议
    永远触发且毫无意义。改得动状态，这套统计才从空转变成可用。
    """
    sf = get_session_factory()
    save_report(sf, _report_with_action_item())
    try:
        got = get_report(sf, _TEST_TD_AI)
        iid, cat = got.action_items[0].id, got.action_items[0].category

        before = evaluate_historical_effectiveness(sf, None)["by_category"]
        b_cat = before.get(cat) or {"confirmed": 0, "adoption_rate": 0.0}

        update_action_item_status(sf, iid, "confirmed", "认可，待实施")

        after = evaluate_historical_effectiveness(sf, None)["by_category"]
        a_cat = after.get(cat)
        assert a_cat is not None, f"处置后 {cat} 类应出现在统计里"
        assert a_cat["confirmed"] == b_cat["confirmed"] + 1
        assert (a_cat["adoption_rate"] or 0) > (b_cat["adoption_rate"] or 0)
    finally:
        _cleanup(sf, _TEST_TD_AI)
