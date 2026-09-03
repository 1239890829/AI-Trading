"""盘后复盘 Agent 的纯逻辑测试（不触发网络）。

覆盖：规则分析器三维度、阻断维度不产出判断、改进项 P0 优先级、
模型路由降级、元结论分类、方法论版本加载、报告落库/检索/对比、
改进项处置闭环（含重跑不丢状态、不累积孤儿行）。
"""
import sys
from types import SimpleNamespace

import pytest

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
    ActionItemStaleError,
    compare_reports,
    get_report,
    list_reports,
    report_exists,
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
    # picks 维度 2026-09-04 加入：无快照时降级输出（不缺席，缺失可见）
    assert {d.key for d in dims} == {"trades", "market", "system", "picks"}
    picks = next(d for d in dims if d.key == "picks")
    assert picks.status == "degraded"
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

    **只接受 `2099*` 的测试日期。** 这个删除是按 `trade_date` 走的，而
    `review_reports.trade_date` 没有唯一约束（只有 `review_id` 唯一），
    同一天可以并存多行 → 传真实日期会把测试行和**真实复盘报告**一起删掉。
    2026-09-01 就写错过一次（把测试报告挪到"今天"再清理），差点删掉真实报告。
    """
    assert trade_date.startswith("2099"), (
        f"拒绝清理非测试日期 {trade_date}：_cleanup 按 trade_date 删除，"
        f"会连带删掉该日的真实复盘报告"
    )
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


def _guard(item) -> dict:
    """处置调用的守卫三元组——与路由 ActionItemPatch 的守卫字段同源。

    id 漂移守卫（2026-09-01）：id 是 SQLite rowid 别名且无 AUTOINCREMENT，
    重跑删除重建后会被复用甚至跨日串号；仅凭 id 寻址会把处置写到
    恰好复用该 id 的别的改进项上（静默错配）。
    """
    return dict(
        expect_trade_date=_TEST_TD_AI,
        expect_category=item.category,
        expect_title=item.title,
    )


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


def _count_action_item_rows(sf, trade_date: str) -> int:
    from sqlalchemy import select as _select

    from app.review.models import ReviewActionItemRow

    db = sf()
    try:
        return len(
            db.execute(
                _select(ReviewActionItemRow.id).where(
                    ReviewActionItemRow.trade_date == trade_date
                )
            ).all()
        )
    finally:
        db.close()


def test_regenerate_same_day_does_not_accumulate_rows():
    """重跑同一交易日不能让改进项行越堆越多。

    曾用 `review_id` 过滤清旧行——可 review_id 每次生成都带新的 HHMMSS 后缀，
    新 id 永远匹配不到旧行，孤儿行只增不减。实测 7 条真实改进项累积成 113 行，
    采纳率的分母被放大约 16 倍，统计与演进建议全部失真。
    """
    sf = get_session_factory()

    # review_id 必须显式给成不同的：不传的话 save_report 用当前 HHMMSS 生成，
    # 两次保存落在同一秒时 review_id 相同，旧的错误实现也能删掉旧行 → 测不出 bug。
    # 真实场景的"重跑"是隔一段时间（或重启后）再跑，review_id 必然不同。
    def _with_rid(seq: int) -> ReviewReport:
        r = _report_with_action_item()
        r.review_id = f"RV-{_TEST_TD_AI}-{seq:06d}"
        return r

    r1 = _with_rid(1)
    save_report(sf, r1)
    try:
        expected = _count_action_item_rows(sf, _TEST_TD_AI)
        assert expected == len(r1.action_items), "首次落库行数应等于改进项条数"

        save_report(sf, _with_rid(2))
        save_report(sf, _with_rid(3))

        got = _count_action_item_rows(sf, _TEST_TD_AI)
        assert got == expected, f"重跑两次后行数应仍是 {expected}，实际 {got}"
    finally:
        _cleanup(sf, _TEST_TD_AI)


def test_disposition_survives_regeneration():
    """重跑当日复盘不能把人的处置结论打回 pending。

    覆盖语义是「报告换新的」，但改进项是给人跟进的：已经确认/驳回过的结论
    被每天重写一次，PDCA 的 C 和 A 就等于不存在。
    """
    sf = get_session_factory()
    save_report(sf, _report_with_action_item())
    try:
        first = get_report(sf, _TEST_TD_AI)
        assert first is not None
        it0 = first.action_items[0]
        update_action_item_status(
            sf, it0.id, "rejected", note="测试：暂不处理", **_guard(it0)
        )

        # 重跑必然带新的 review_id（见 test_regenerate_same_day_does_not_accumulate_rows）
        again_report = _report_with_action_item()
        again_report.review_id = f"RV-{_TEST_TD_AI}-999999"
        save_report(sf, again_report)

        again = get_report(sf, _TEST_TD_AI)
        assert again is not None
        assert again.action_items[0].status == "rejected", (
            "重跑后应继承 rejected；若回到 pending 说明覆盖把处置结论抹掉了"
        )
    finally:
        _cleanup(sf, _TEST_TD_AI)


def test_report_exists_reflects_db():
    """`report_exists` 必须真实查库——它是调度器判重的唯一依据。

    单独测它，是因为调度器那个测试里它被换成桩了（见下）。
    """
    sf = get_session_factory()
    assert report_exists(sf, _TEST_TD_AI) is False, "测试日期不应有报告残留"
    save_report(sf, _report_with_action_item())
    try:
        assert report_exists(sf, _TEST_TD_AI) is True
    finally:
        _cleanup(sf, _TEST_TD_AI)
    assert report_exists(sf, _TEST_TD_AI) is False, "清理后应恢复为无报告"


def test_scheduler_skips_when_today_report_exists(monkeypatch):
    """'今天跑过了'必须查库判定，不能用内存变量。

    内存变量在进程重启后清空，而"当前时间已过触发点"这个条件重启后天然成立
    → 每次重启都重跑。这是孤儿改进项行的主要来源。

    这里**不能**往库里写真日期来造"今天已有报告"：`review_reports.trade_date`
    没有唯一约束，同一天可以并存多行；而 `_cleanup` 按 trade_date 删，
    会把测试行和真实报告一起删掉（2026-09-01 实测踩到，差点删掉真实复盘报告）。
    所以把 `report_exists` 换成可控桩，只验证"调度器确实去查了库、且听它的结论"。
    """
    import asyncio

    from app.market import trade_calendar as tc
    from app.review import storage
    from app.review.service import review_scheduler

    calls: list = []
    consulted: list = []
    exists = {"v": False}

    def _fake_exists(session_factory, ymd):
        # 用内存变量去重的实现根本不会走到这里 → consulted 恒为空即证明回归
        consulted.append(ymd)
        return exists["v"]

    class _FakeSvc:
        session_factory = get_session_factory()
        # 调度器会访问 service.hub.provider 取交易日历；这里用桩顶掉
        hub = SimpleNamespace(provider=None)

        async def run(self, when):
            calls.append(when)

    async def _fake_trading_days(provider):
        return []

    monkeypatch.setattr(storage, "report_exists", _fake_exists)
    # 让"今天是交易日"恒真，其余走真实调度逻辑
    monkeypatch.setattr(tc, "trading_days", _fake_trading_days)
    monkeypatch.setattr(tc, "is_trade_day", lambda days, day: True)

    async def go():
        """跑几个 tick 就停。无报告时每个 tick 都会重试，所以断言用增量而非绝对次数。"""
        stop = asyncio.Event()
        task = asyncio.create_task(
            review_scheduler(_FakeSvc(), run_hour=0, run_minute=0,
                             check_interval_seconds=0.01, stop=stop)
        )
        await asyncio.sleep(0.05)
        stop.set()
        await task

    # ① 库里查不到当日报告 → 应触发（可能连跑几个 tick，只要 >0 即可）
    asyncio.run(go())
    assert consulted, "调度器必须查库判重；若退回内存变量去重，这里会是空的"
    baseline = len(calls)
    assert baseline >= 1, "当日无报告时调度器应触发复盘"

    # ② 库里查得到 → 一次都不该触发（模拟进程重启后不再重跑）
    exists["v"] = True
    asyncio.run(go())
    assert len(calls) == baseline, (
        f"当日报告已存在时调度器应跳过，却仍触发了 {len(calls) - baseline} 次"
    )


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

        update_action_item_status(sf, pk, "confirmed", **_guard(first.action_items[0]))

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
        it = got.action_items[0]
        iid = it.id

        r1 = update_action_item_status(
            sf, iid, "confirmed", "认可，待实施", **_guard(it)
        )
        assert r1["status"] == "confirmed"
        assert r1["resolved_at"] is not None

        r2 = update_action_item_status(sf, iid, "applied", "已实施", **_guard(it))
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
        got = get_report(sf, _TEST_TD_AI)
        it = got.action_items[0]
        iid = it.id
        guard = _guard(it)

        for bad in ("done", "", "P0"):
            try:
                update_action_item_status(sf, iid, bad, **guard)
            except ValueError:
                pass
            else:
                raise AssertionError(f"非法状态 {bad!r} 应抛 ValueError")

        # 驳回与回退必须写理由——没有理由的处置后期无法归因
        for st in ("rejected", "reverted"):
            try:
                update_action_item_status(sf, iid, st, "   ", **guard)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{st} 缺 note 应抛 ValueError")

        try:
            update_action_item_status(sf, "999999999", "confirmed", **guard)
        except LookupError:
            pass
        else:
            raise AssertionError("不存在的改进项应抛 LookupError")

        try:
            update_action_item_status(sf, "not-a-number", "confirmed", **guard)
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
        it = get_report(sf, _TEST_TD_AI).action_items[0]
        iid = it.id
        assert update_action_item_status(
            sf, iid, "confirmed", "x", **_guard(it)
        )["resolved_at"]
        assert update_action_item_status(sf, iid, "pending", **_guard(it))["resolved_at"] is None
    finally:
        _cleanup(sf, _TEST_TD_AI)


def test_patch_stale_fingerprint_rejected():
    """id 漂移守卫：陈旧 id 必须显式失败，而不是静默写到别的改进项上。

    id 是 SQLite rowid 别名且无 AUTOINCREMENT——报告重跑删除重建后 id 被
    复用（实测 111→72），跨交易日还会串号。仅凭 id 寻址，用户拿重跑前的
    页面点处置，结论会挂到恰好复用该 id 的不相干改进项上，全程无报错。
    三元组守卫（trade_date/category/title）让这种错配显式失败。
    """
    sf = get_session_factory()
    save_report(sf, _report_with_action_item())
    try:
        it = get_report(sf, _TEST_TD_AI).action_items[0]

        # ① 标题对不上：该 id 在重跑后被别的标题的行复用
        with pytest.raises(ActionItemStaleError):
            update_action_item_status(
                sf, it.id, "confirmed",
                expect_trade_date=_TEST_TD_AI,
                expect_category=it.category,
                expect_title="重跑后才出现的另一个改进项标题",
            )

        # ② 交易日对不上：id 被另一天的行复用（跨日串号）
        with pytest.raises(ActionItemStaleError):
            update_action_item_status(
                sf, it.id, "confirmed",
                expect_trade_date="20981231",
                expect_category=it.category,
                expect_title=it.title,
            )

        # ③ 三元组全对 → 正常处置
        r = update_action_item_status(
            sf, it.id, "confirmed", "认可", **_guard(it)
        )
        assert r["status"] == "confirmed"
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
        it = got.action_items[0]
        iid, cat = it.id, it.category

        before = evaluate_historical_effectiveness(sf, None)["by_category"]
        b_cat = before.get(cat) or {"confirmed": 0, "adoption_rate": 0.0}

        update_action_item_status(
            sf, iid, "confirmed", "认可，待实施", **_guard(it)
        )

        after = evaluate_historical_effectiveness(sf, None)["by_category"]
        a_cat = after.get(cat)
        assert a_cat is not None, f"处置后 {cat} 类应出现在统计里"
        assert a_cat["confirmed"] == b_cat["confirmed"] + 1
        assert (a_cat["adoption_rate"] or 0) > (b_cat["adoption_rate"] or 0)
    finally:
        _cleanup(sf, _TEST_TD_AI)


# ---------------------------------------------------------------- 数据链健康（system 维度）
# 2026-09-02 起复盘采集 provider 健康与 ths 哨兵快照：熔断 open / 哨兵 alert
# 意味着当日部分数据建立在降级口径上，system 维度必须把这件事说出来——
# 不主动核对数据链，data_issue 归因永远是盲区（四类静默失败教训）。


def _ph(**over) -> dict:
    base = {
        "chain": "chain(ths→tencent→eastmoney→sina)",
        "breakers": {},
        "last_good": {"get_quotes": "tencent"},
        "switch_log": [],
        "ths_reason_sentinel": {"state": "ok", "records": 49, "coverage": 1.0},
    }
    base.update(over)
    return base


def _sys_dim(data: ReviewData) -> DimensionResult:
    return next(d for d in RulesAnalyzer().analyze(data, _method()) if d.key == "system")


def test_system_surfaces_open_breakers_and_sentinel_alert():
    data = _ok_data()
    data.market.provider_health = _ph(
        breakers={"get_kline@eastmoney": {"failures": 3, "cooldown_left": 41.0, "state": "open"}},
        switch_log=[{"at": "2026-09-02T10:00", "method": "get_kline", "to": "tencent"}],
        ths_reason_sentinel={"state": "alert", "records": 30, "coverage": 0.2},
    )
    dim = _sys_dim(data)
    joined = "\n".join(dim.judgements)
    assert "get_kline@eastmoney" in joined and "熔断" in joined
    assert "涨停原因哨兵" in joined and "降权" in joined
    assert dim.evidence["provider_health"]["open_breakers"] == ["get_kline@eastmoney"]
    assert dim.evidence["provider_health"]["switch_count"] == 1


def test_system_watch_state_is_finding_not_judgement():
    """watch（未达阈值）只是提示，不上judgement——避免噪音稀释真异常。"""
    data = _ok_data()
    data.market.provider_health = _ph(
        breakers={"get_kline@sina": {"failures": 1, "cooldown_left": 0.0, "state": "watch"}}
    )
    dim = _sys_dim(data)
    assert not any("熔断" in j for j in dim.judgements)
    assert any("watch" in f for f in dim.findings)


def test_system_no_provider_health_stays_silent():
    """未采集（None）= 不冒充健康也不产出噪音，单源部署不误报。"""
    dim = _sys_dim(_ok_data())
    assert "provider_health" not in dim.evidence
    assert not any("哨兵" in j for j in dim.judgements)


def test_collect_market_gathers_provider_health(monkeypatch):
    """collector 采集 provider 健康 + 哨兵快照；各失败路径单独容错。"""
    import asyncio
    from datetime import date as _date

    from app.review.collector import collect_market
    import app.services.market_context as mc
    import app.services.theme_service as ts

    async def fake_sentiment(hub, svc):
        return {}

    async def fake_board(provider, td):
        return {"themes": [], "broken_ladder": []}

    monkeypatch.setattr(mc, "compute_market_sentiment", fake_sentiment)
    monkeypatch.setattr(ts, "build_theme_board", fake_board)

    hub = SimpleNamespace(
        get_indices=lambda: [],
        provider=SimpleNamespace(
            provider_health=lambda: {"chain": "chain(a→b)", "breakers": {}, "last_good": {}, "switch_log": []},
        ),
    )
    snap_svc = SimpleNamespace(breadth_payload=lambda: {"breadth": {"up": 1}})
    sentinel = SimpleNamespace(snapshot=lambda: {"state": "ok", "records": 49})

    snap = asyncio.run(
        collect_market(hub, snap_svc, _date(2026, 9, 2), sentinel=sentinel)
    )
    assert snap.provider_health["chain"] == "chain(a→b)"
    assert snap.provider_health["ths_reason_sentinel"]["state"] == "ok"


def test_collect_market_provider_health_failure_records_gap(monkeypatch):
    """健康采集自身失败 → provider_health=None（未采集）+ warn gap，绝不给 {} 冒充健康。"""
    import asyncio
    from datetime import date as _date

    from app.review.collector import collect_market
    import app.services.market_context as mc
    import app.services.theme_service as ts

    async def fake_sentiment(hub, svc):
        return {}

    async def fake_board(provider, td):
        return {"themes": [], "broken_ladder": []}

    monkeypatch.setattr(mc, "compute_market_sentiment", fake_sentiment)
    monkeypatch.setattr(ts, "build_theme_board", fake_board)

    def boom():
        raise RuntimeError("breaker exploded")

    hub = SimpleNamespace(
        get_indices=lambda: [],
        provider=SimpleNamespace(provider_health=boom),
    )
    snap_svc = SimpleNamespace(breadth_payload=lambda: {"breadth": {"up": 1}})

    snap = asyncio.run(collect_market(hub, snap_svc, _date(2026, 9, 2)))
    assert snap.provider_health is None
    assert any(g.field == "provider_health" for g in snap.gaps)