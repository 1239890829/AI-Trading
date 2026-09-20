"""RSH-026: point-in-time funnel evidence, replay, and outcome labels."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.opportunity_learning import OpportunityDecisionSnapshot, OpportunityOutcomeLabel
from app.models.watchlist import Base
from app.picks.opportunity_learning import (
    COST_MODEL_VERSION,
    FEATURE_VERSION,
    FILL_SEAL_GAP_PCT,
    OUTCOME_HORIZON,
    STRATEGY_VERSION,
    archive_records,
    assess_fill_state,
    build_intraday_records,
    build_notification_records,
    label_trade_date,
    learning_summary,
    opportunity_scorecard,
    pending_symbols,
    replay_run,
    round_trip_net_pct,
)


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'opportunity-learning.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _payload():
    return {
        "trade_date": "2026-09-16",
        "hot_available": True,
        "themes": [{
            "theme": "算力", "stage": "发酵", "strength_tier": "强势",
            "participants": [
                {
                    "symbol": "600001", "name": "甲", "price": 10.0,
                    "change_pct": 6.8, "amount": 2e8, "container": "算力概念",
                    "basis": "尚未涨停，已进临板区", "board": "沪市主板",
                    "tradability": {"level": "可参与", "basis": "报价可成交"},
                    "linkage": {"level": "高", "basis": "题材成建制且进入临板区"},
                },
                {
                    "symbol": "600002", "name": "乙", "price": 8.0,
                    "change_pct": 1.2, "amount": 1e8, "container": "算力概念",
                    "basis": "尚未涨停但联动弱", "board": "沪市主板",
                    "tradability": {"level": "可参与", "basis": "报价可成交"},
                    "linkage": {"level": "低", "basis": "跟进迹象弱"},
                },
            ],
            "stocks": [],
        }],
    }


def test_intraday_snapshot_covers_candidate_gate_and_rank_with_unknown_safe():
    payload = _payload()
    payload["themes"][0]["participants"][1]["tradability"] = {
        "level": "unknown", "basis": "盘口缺失"
    }
    run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )

    assert run_id
    assert len(rows) == 6
    by = {(r["symbol"], r["stage"]): r for r in rows}
    assert by[("600001", "candidate")]["decision"] == "included"
    assert by[("600001", "hard_gate")]["decision"] == "passed"
    assert by[("600001", "rank")]["decision"] == "ranked"
    assert by[("600001", "rank")]["rank"] == 1
    # unknown 不能塌缩成 passed/rejected，也不能靠排序补进名单。
    assert by[("600002", "hard_gate")]["decision"] == "unknown"
    assert by[("600002", "rank")]["decision"] == "unknown"


def test_filtered_symbol_is_archived_before_it_disappears_from_participants():
    payload = _payload()
    payload["themes"][0]["participants"] = [payload["themes"][0]["participants"][0]]
    payload["themes"][0]["_candidate_audit"] = [
        {
            "symbol": "300003", "name": "创业丙", "candidate_decision": "rejected",
            "hard_gate_decision": "rejected", "reason": "账户无创业板交易权限",
            "price": 12.0, "change_pct": 5.0, "amount": 2e8, "board": "创业板",
            "facts": {"quote_present": True, "board_tradable": False},
        },
        {
            "symbol": "600001", "name": "甲", "candidate_decision": "included",
            "hard_gate_decision": "passed", "reason": "候选与可参与硬门均通过",
            "price": 10.0, "change_pct": 6.8, "amount": 2e8, "board": "沪市主板",
            "facts": {
                "quote_present": True, "board_tradable": True, "change_pct": 6.8,
                "linkage_min_pct": 1.0, "amount": 2e8, "amount_min": 3e7,
                "seal_line": 9.7,
            },
        },
    ]
    _run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    filtered = [r for r in rows if r["symbol"] == "300003"]
    assert [(r["stage"], r["decision"]) for r in filtered] == [
        ("candidate", "rejected"), ("hard_gate", "rejected")
    ]
    assert all(r["symbol"] != "300003" or r["stage"] != "rank" for r in rows)


def test_archive_is_append_only_idempotent_and_offline_replay_matches(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    first = archive_records(run_id, rows, sf)
    second = archive_records(run_id, rows, sf)

    assert first["inserted"] == 6
    assert second["inserted"] == 0
    replay = replay_run(run_id, sf)
    assert replay["records"] == 6 and replay["mismatches"] == 0
    assert all(item["matches"] for item in replay["items"])
    with sf() as db:
        evidence_before = db.execute(
            select(OpportunityDecisionSnapshot.snapshot_id, OpportunityDecisionSnapshot.evidence)
            .order_by(OpportunityDecisionSnapshot.id)
        ).all()
    label_trade_date("2026-09-16", {"600001": 10.5}, sf)
    with sf() as db:
        evidence_after = db.execute(
            select(OpportunityDecisionSnapshot.snapshot_id, OpportunityDecisionSnapshot.evidence)
            .order_by(OpportunityDecisionSnapshot.id)
        ).all()
    assert evidence_after == evidence_before, "结果标签必须写独立表，不能改写当时证据"


def test_outcome_label_is_retryable_and_coverage_is_mechanical(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)

    assert pending_symbols("2026-09-16", sf) == {"600001"}
    missing = label_trade_date("2026-09-16", {}, sf)
    assert missing == {"trade_date": "2026-09-16", "labeled": 0, "unknown": 0, "pending": 1}
    done = label_trade_date("2026-09-16", {"600001": 10.5}, sf)
    assert done["labeled"] == 1 and done["pending"] == 0
    # 幂等：已标结果不重算，不允许未来一次价格覆盖原始 D0 标签。
    assert label_trade_date("2026-09-16", {"600001": 99.0}, sf)["labeled"] == 0
    with sf() as db:
        label = db.execute(select(OpportunityOutcomeLabel)).scalar_one()
    assert label.state == "labeled" and label.label == "positive"
    assert label.return_pct == 5.0 and label.outcome_price == 10.5
    summary = learning_summary("2026-09-16", sf)
    assert summary["label_coverage"] == 1.0
    assert summary["stages"] == {"candidate": 2, "hard_gate": 2, "rank": 2, "notification": 0}


def test_missing_entry_price_becomes_unknown_not_zero_return(tmp_path):
    sf = _factory(tmp_path)
    payload = _payload()
    payload["themes"][0]["participants"][0]["price"] = None
    run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    result = label_trade_date("2026-09-16", {"600001": 10.5}, sf)
    assert result["unknown"] == 1
    with sf() as db:
        label = db.execute(select(OpportunityOutcomeLabel)).scalar_one()
    assert label.state == "unknown" and label.return_pct is None
    assert "价格缺失" in label.reason


def test_notification_replay_keeps_rejections_and_dispatch_result(tmp_path):
    sf = _factory(tmp_path)
    items = [
        {"symbol": "600001", "name": "甲", "confidence": {"tier": "strong"}},
        {"symbol": "600002", "name": "乙", "confidence": {"tier": "observe"}},
    ]
    hits = [{"item": items[0], "price": 10.0, "chg": 2.0}]
    skips = [{"symbol": "600002", "reason": "置信档 observe 不足"}]
    run_id, rows = build_notification_records(
        items, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 30),
        hits=hits, skips=skips, dispatch_by_symbol={"600001": "notified"},
    )
    archive_records(run_id, rows, sf)

    assert {r["decision"] for r in rows} == {"notified", "rejected"}
    replay = replay_run(run_id, sf)
    assert replay["mismatches"] == 0
    rejected = next(i for i in replay["items"] if i["symbol"] == "600002")
    assert "observe" in rejected["evidence"]["gate_reason"]


def test_archived_evidence_is_valid_json_and_contains_versions(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    with sf() as db:
        stored = db.execute(select(OpportunityDecisionSnapshot)).scalars().all()
    assert all(json.loads(row.evidence) for row in stored)
    assert all(row.strategy_version and row.feature_version and row.as_of for row in stored)


# ── RSH-026 第二批（2026-09-16）：成本后净收益 + 可成交性 + 样本门禁 ──────────────


def test_net_return_is_sourced_from_calc_fee_not_a_second_rate_table():
    """净收益必须**真的消费** `paper.engine.calc_fee`，而不是自带一份费率常量。

    判据不是「值对得上」（抄一遍公式也能对得上），而是**改费率 ⇒ 净收益跟着变**
    —— 这才是「同源」的可证伪形式（同族：`paper/reconcile.py` 的费率漂移自检）。
    """
    from app.paper.engine import FEE, calc_fee

    net, cost, qty = round_trip_net_pct(10.0, 10.5)
    assert qty == 10_000, "10 万元名义本金 ÷ 10 元 = 1 万整股，整手口径"
    buy_fee = calc_fee("buy", 10.0, qty)
    sell_fee = calc_fee("sell", 10.5, qty)
    paid = 10.0 * qty + buy_fee
    assert cost == round((buy_fee + sell_fee) / paid * 100, 4)
    assert net == round((10.5 * qty - sell_fee - paid) / paid * 100, 2)
    # 同口径毛收益：净必须**严格小于**毛，差额即成本（本例约 0.11 个百分点）
    gross = round((10.5 * qty - 10.0 * qty) / paid * 100, 2)
    assert net < gross
    assert 0 < gross - net - cost < 0.02, f"差额 {gross - net} 应约等于成本率 {cost}"

    original = FEE["commission_rate"]
    try:
        FEE["commission_rate"] = original * 8
        higher_cost_net, higher_cost, _q = round_trip_net_pct(10.0, 10.5)
    finally:
        FEE["commission_rate"] = original
    assert higher_cost > cost and higher_cost_net < net, (
        "费率提高后成本率必须上升、净收益必须下降；"
        "若不变，说明本模块自带了一份费率常量（同源契约被破坏）"
    )


def test_flat_close_turns_positive_gross_into_negative_net():
    """毛收益 0% 时净收益必须为负——这是「扣成本」最直观的可证伪点。"""
    net, _cost, _q = round_trip_net_pct(10.0, 10.0)
    assert net < 0, "平盘卖出必亏双边成本；若净收益为 0 或正，说明成本没真的扣"


def test_fill_state_boundaries_follow_board_limit_and_tolerance():
    """可成交性判据：涨停幅度取 `price_rules.limit_pct`，容差取 `FILL_SEAL_GAP_PCT`。"""
    from app.market.price_rules import limit_pct

    limit = limit_pct("600001")
    assert limit == 10.0
    # 阈值恰为 limit − 容差（10 − 0.15 = 9.85）：两侧各钉一点
    assert assess_fill_state("600001", limit - FILL_SEAL_GAP_PCT)[0] == "sealed"
    assert assess_fill_state("600001", limit - FILL_SEAL_GAP_PCT - 0.01)[0] == "ok"
    # 板块差异必须生效：同样 12% 在主板可买、在创业板距 20% 仍有余量、在 19.9% 封板
    assert assess_fill_state("300750", 19.9)[0] == "sealed"
    assert assess_fill_state("300750", 12.0)[0] == "ok"
    assert assess_fill_state("832000", 29.9)[0] == "sealed"
    assert assess_fill_state("600001", None)[0] == "no_quote"


def test_label_trade_date_actually_wires_net_return_into_the_row(tmp_path):
    """**接线守卫**：`label_trade_date` 必须真的调用净收益口径并把结果落库。

    本用例是注入自证抓出的补丁：上面那几条只测**纯函数**（`round_trip_net_pct` /
    `assess_fill_state`），而「纯函数对」**不等于**「调用它」—— 把 `label_trade_date`
    里的净收益换成 `net = ret, cost = 0.0` 时，纯函数用例全绿、只有本用例变红。
    判据刻意取**关系式**（`net < gross`、`cost > 0`、与独立复算相等）而非硬编码数值。
    """
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    label_trade_date("2026-09-16", {"600001": 10.5}, sf)

    expected_net, expected_cost, _q = round_trip_net_pct(10.0, 10.5)
    with sf() as db:
        row = db.execute(
            select(
                OpportunityOutcomeLabel.return_pct,
                OpportunityOutcomeLabel.net_return_pct,
                OpportunityOutcomeLabel.cost_pct,
            ).join(
                OpportunityDecisionSnapshot,
                OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
            )
        ).one()
    gross, net, cost = float(row[0]), float(row[1]), float(row[2])
    assert net == expected_net and cost == expected_cost, "落库值必须等于口径函数的独立复算"
    assert net < gross, f"净 {net} 必须严格小于毛 {gross}；相等即成本没真的扣"
    assert cost > 0, "成本率必须为正"
    assert round(gross - net, 3) >= cost * 0.9, "毛净差额应与成本量级一致"


def test_sealed_board_keeps_net_return_none_and_never_zero(tmp_path):
    """封板买不到 ⇒ 净收益必须是 `None`，**严禁记 0**。

    记 0 会把「买不到」混进「零收益」样本，使净期望被系统性高估
    （与既有 `test_missing_entry_price_becomes_unknown_not_zero_return` 同族）。
    """
    sf = _factory(tmp_path)
    payload = _payload()
    participants = payload["themes"][0]["participants"]
    participants[0]["change_pct"] = 9.95      # 贴 10% 涨停 ⇒ sealed
    participants[1]["change_pct"] = 1.2       # 距涨停有余量 ⇒ ok
    # ⚠️ 必须让两只都进 `ranked`：结果标签只为 ranked/notified/suppressed 建档，
    # 联动为「低」者判 rejected ⇒ 根本没有 outcome 行（这是既有漏斗语义，非缺陷）。
    participants[1]["linkage"] = {"level": "高", "basis": "题材成建制且进入临板区"}
    run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    label_trade_date("2026-09-16", {"600001": 10.1, "600002": 8.2}, sf)

    with sf() as db:
        rows = db.execute(
            select(
                OpportunityDecisionSnapshot.symbol,
                OpportunityOutcomeLabel.fill_state,
                OpportunityOutcomeLabel.net_return_pct,
                OpportunityOutcomeLabel.cost_pct,
                OpportunityOutcomeLabel.return_pct,
                OpportunityOutcomeLabel.reason,
            ).join(
                OpportunityDecisionSnapshot,
                OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
            )
        ).all()
    by_symbol = {symbol: (fill, net, cost, ret, reason) for symbol, fill, net, cost, ret, reason in rows}
    sealed, ok = by_symbol["600001"], by_symbol["600002"]
    # 元组序：(fill_state, net_return_pct, cost_pct, return_pct, reason)
    assert sealed[:3] == ("sealed", None, None)
    assert ok[0] == "ok" and ok[1] is not None
    # 封板的**毛收益照常记录**（信号方向仍可评估），只是不进净期望、不给成本
    assert sealed[3] is not None and sealed[2] is None
    assert "封板买不到" in sealed[4]
    summary = learning_summary("2026-09-16", sf)
    assert summary["fill_states"] == {"ok": 1, "sealed": 1}
    assert summary["cost_model"] == COST_MODEL_VERSION


def test_scorecard_refuses_verdict_until_min_labels_reached(tmp_path):
    """样本不足时 `verdict` 恒为 `insufficient_sample`——把纪律落成**代码门禁**。"""
    from app.picks.opportunity_learning import MIN_LABELS_FOR_VERDICT

    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    label_trade_date("2026-09-16", {"600001": 10.5, "600002": 8.2}, sf)

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["min_labels_for_verdict"] == MIN_LABELS_FOR_VERDICT
    assert card["fillable"] < MIN_LABELS_FOR_VERDICT
    assert card["verdict"] == "insufficient_sample"
    assert "不构成买卖建议" in card["note"]
    assert "选择性偏差" in card["note"], "必须显式声明净期望只覆盖可成交样本"


def test_scorecard_expectancy_fields_never_mix_denominators(tmp_path):
    """毛/净期望**分母不同**，故必须另给同分母毛期望——否则差额会被读成负成本。

    本用例刻意构造「封板样本毛收益低于可成交样本」：若有人把全样本 `gross_pct`
    与 `net_pct` 相减当成本，会得到**负成本**（净 > 毛）的荒谬结论。
    """
    sf = _factory(tmp_path)
    payload = _payload()
    participants = payload["themes"][0]["participants"]
    participants[0]["change_pct"] = 9.95   # sealed，D0 收盘 +1%（买不到，毛收益仍记账）
    participants[1]["change_pct"] = 1.2    # ok，D0 收盘 +5%
    # 两只都必须进 `ranked` 才有 outcome 行（rejected 不建档，见既有漏斗语义）
    participants[1]["linkage"] = {"level": "高", "basis": "题材成建制且进入临板区"}
    run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    label_trade_date("2026-09-16", {"600001": 10.1, "600002": 8.4}, sf)

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    exp = card["expectancy"]
    assert exp["gross_labels"] == 2 and exp["fillable_labels"] == 1
    # 同分母差额 = 成本（正数、量级合理）
    assert exp["gross_on_fillable_pct"] is not None and exp["net_pct"] is not None
    gap = round(exp["gross_on_fillable_pct"] - exp["net_pct"], 4)
    assert 0 < gap < 0.5, f"同分母差额 {gap} 应为正的成本量级"
    assert exp["gross_on_fillable_pct"] != exp["gross_pct"], "两个毛期望的分母不同，不可同值"
    # 反例自证：全样本毛期望**低于**净期望 ⇒ 两者相减会得负成本。
    # 若本条变红，说明有人把 gross_pct 改成了同分母口径 ⇒ 应删除本条并同步
    # 更新 opportunity_scorecard 文档里的「分母纪律」段落（不是放宽断言）。
    assert exp["net_pct"] > exp["gross_pct"], (
        "本用例的构造前提是「封板样本拉低全样本毛期望」；该前提消失时应更新用例而非删除判据"
    )


def _seed_scorecard_label(
    sf, *, snapshot_id: str, run_id: str, symbol: str, as_of: datetime,
    stage: str = "rank", rank: int | None = 1, horizon: str = OUTCOME_HORIZON,
    strategy_version: str = STRATEGY_VERSION, feature_version: str = FEATURE_VERSION,
    gross: float = 1.0, proxy: float | None = 0.9, source_theme: str = "T",
    reason: str | None = None,
) -> None:
    decision = "ranked" if stage == "rank" else "notified"
    with sf() as db:
        db.add(OpportunityDecisionSnapshot(
            snapshot_id=snapshot_id, run_id=run_id, trade_date="2026-09-16", as_of=as_of,
            scenario="intraday_opportunity", stage=stage, symbol=symbol, name=symbol,
            source_theme=source_theme, decision=decision, rank=rank if stage == "rank" else None,
            strategy_version=strategy_version, feature_version=feature_version,
            data_state="ready", entry_price=10.0, evidence=json.dumps({"change_pct": 1.0}),
        ))
        db.add(OpportunityOutcomeLabel(
            snapshot_id=snapshot_id, horizon=horizon, target_date="2026-09-16",
            state="labeled", label="positive", reference_price=10.0, outcome_price=10.1,
            return_pct=gross, fill_state="ok", cost_pct=0.1 if proxy is not None else None,
            net_return_pct=proxy,
            reason=reason or f"D0 cost proxy ({COST_MODEL_VERSION})",
        ))
        db.commit()

def test_scorecard_36_audit_rows_are_one_symbol_day_sample(tmp_path):
    sf = _factory(tmp_path)
    stages = ("candidate", "hard_gate", "rank", "notification")
    for run_no in range(9):
        for stage_no, stage in enumerate(stages):
            _seed_scorecard_label(
                sf, snapshot_id=f"s-{run_no}-{stage}", run_id=f"run-{run_no}",
                symbol="600001", as_of=datetime(2026, 9, 16, 9, 30 + run_no),
                stage=stage, rank=1, source_theme=f"T{stage_no}",
            )

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["audit"]["labeled_rows"] == 36
    assert card["sample"]["unit"] == "symbol_trade_date"
    assert card["sample"]["count"] == 1
    assert card["denominators"] == {
        "audit_rows": 36, "runs": 9, "run_symbol_opportunities": 9,
        "symbols": 1, "symbol_trade_date_samples": 1,
    }
    assert card["fillable"] == 1
    assert card["verdict"] == "insufficient_sample"


def test_scorecard_top_k_is_single_run_and_unique_symbol(tmp_path):
    sf = _factory(tmp_path)
    when = datetime(2026, 9, 16, 10, 5)
    _seed_scorecard_label(
        sf, snapshot_id="a1", run_id="run-a", symbol="600001", as_of=when,
        rank=1, source_theme="T1", proxy=1.0,
    )
    _seed_scorecard_label(
        sf, snapshot_id="a2", run_id="run-a", symbol="600001", as_of=when,
        rank=2, source_theme="T2", proxy=-1.0,
    )
    _seed_scorecard_label(
        sf, snapshot_id="a3", run_id="run-a", symbol="600002", as_of=when,
        rank=3, source_theme="T1", proxy=1.0,
    )
    _seed_scorecard_label(
        sf, snapshot_id="b1", run_id="run-b", symbol="600003",
        as_of=datetime(2026, 9, 16, 10, 6), rank=1, source_theme="T3", proxy=-1.0,
    )

    card = opportunity_scorecard("2026-09-16", top_k=2, run_id="run-a", session_factory=sf)
    p = card["precision_at_k"]
    assert p["run_id"] == "run-a" and p["k_requested"] == 2
    assert p["symbols"] == ["600001", "600002"]
    assert p["selected"] == 2 and p["evaluable"] == 2
    assert p["observed"] == 1.0


def test_scorecard_filters_horizon_and_versions_explicitly(tmp_path):
    sf = _factory(tmp_path)
    when = datetime(2026, 9, 16, 10, 5)
    _seed_scorecard_label(
        sf, snapshot_id="current-d0", run_id="run-current", symbol="600001",
        as_of=when, horizon=OUTCOME_HORIZON,
    )
    _seed_scorecard_label(
        sf, snapshot_id="current-d1", run_id="run-current-d1", symbol="600002",
        as_of=when, horizon="d1_close",
    )
    _seed_scorecard_label(
        sf, snapshot_id="legacy-d0", run_id="run-legacy", symbol="600003",
        as_of=when, strategy_version="legacy-strategy", feature_version="legacy-features",
    )

    current = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert current["filters"] == {
        "horizon": OUTCOME_HORIZON,
        "strategy_version": STRATEGY_VERSION,
        "feature_version": FEATURE_VERSION,
    }
    assert current["audit"]["labeled_rows"] == 1
    d1 = opportunity_scorecard("2026-09-16", horizon="d1_close", session_factory=sf)
    assert d1["audit"]["labeled_rows"] == 1


def test_nan_close_never_becomes_a_labeled_sample(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)

    result = label_trade_date("2026-09-16", {"600001": float("nan")}, sf)
    assert result["labeled"] == 0
    with sf() as db:
        outcome = db.execute(select(OpportunityOutcomeLabel)).scalar_one()
    assert outcome.state == "pending"
    assert outcome.return_pct is None and outcome.net_return_pct is None

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["sample"]["count"] == 0
    assert card["fillable"] == 0


def test_d0_scorecard_names_cost_value_as_nonrealizable_proxy(tmp_path):
    sf = _factory(tmp_path)
    _seed_scorecard_label(
        sf, snapshot_id="d0", run_id="run-d0", symbol="600001",
        as_of=datetime(2026, 9, 16, 10, 5),
    )
    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    identity = card["metric_identity"]
    assert identity["horizon"] == OUTCOME_HORIZON
    assert identity["realizable_return"] is False
    assert "T+1" in identity["note"]
    assert "cost_adjusted_d0_proxy_pct" in card["expectancy"]


def test_scorecard_excludes_unproven_cost_model_rows_from_proxy(tmp_path):
    sf = _factory(tmp_path)
    _seed_scorecard_label(
        sf, snapshot_id="old-cost", run_id="run-old", symbol="600001",
        as_of=datetime(2026, 9, 16, 10, 5), reason="legacy cost proxy without version",
    )
    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["audit"]["cost_version_excluded_samples"] == 1
    assert card["sample"]["count"] == 1
    assert card["fillable"] == 0
    assert card["expectancy"]["cost_adjusted_d0_proxy_pct"] is None


def test_scorecard_stage_diagnostics_dedupe_within_stage_not_globally(tmp_path):
    sf = _factory(tmp_path)
    when = datetime(2026, 9, 16, 10, 5)
    for n, stage in enumerate(("rank", "notification")):
        for dup in range(2):
            _seed_scorecard_label(
                sf, snapshot_id=f"{stage}-{dup}", run_id=f"run-{stage}-{dup}",
                symbol="600001", as_of=when, stage=stage, proxy=1.0 + n,
            )
    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["sample"]["count"] == 1
    assert card["by_stage"]["rank"]["labels"] == 1
    assert card["by_stage"]["notification"]["labels"] == 1
    assert card["audit"]["repeated_labeled_rows"] == 3
