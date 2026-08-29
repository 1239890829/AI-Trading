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
from app.review.storage import (
    compare_reports,
    get_report,
    list_reports,
    save_report,
)
from app.review.synthesis import build_action_items

Base.metadata.create_all(get_engine())
ensure_default_methodology_file()


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
    saved = save_report(sf, _report("20260828"))
    got = get_report(sf, "20260828")
    assert got is not None
    assert got.summary == "summary-20260828"
    assert got.review_id == saved.review_id
    assert len(got.action_items) == len(saved.action_items)
    # 结构化列可检索
    rows = list_reports(sf, limit=5)
    assert any(r["trade_date"] == "20260828" for r in rows)


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
