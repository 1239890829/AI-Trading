"""`RSH-027` 切片 1：场景化 KB 路由、引用校验、索引覆盖度。

本文件的每条守卫都对应一个**具体的失效方式**，不是"多测点更保险"：

1. `test_scenario_table_matches_blueprint_verbatim` —— 路由表与蓝图 §5 表格
   **逐行逐字**比对。防「改了代码忘了改文档」与「改了文档忘了改代码」两个方向。
2. `test_kb_index_parse_covers_every_table_row` —— 覆盖度**恒等式**自证。
   这是本轮修掉那条真实偏差的回归：议程第八路的私有正则曾静默漏 16/178 条。
3. `test_status_column_with_note_is_parsed` —— 逐条回归被漏掉的那 16 条。
4. `test_intraday_allows_trade_discipline_but_not_stale_or_example` —— 蓝图对盘中
   的限定是**状态**（已落地/试验中），不是册；含四条拒绝路径。
5. `test_example_is_readable_but_never_a_hard_rule` —— 「可以读」与「可以当判据」
   是两件事：盘前允许引用 `📎`，但 `assert_hard_rule_allowed` 必须拒绝。
6. `test_intraday_status_filter_is_load_bearing` / `test_scoring_book_identity_is_load_bearing`
   —— **注入自证**：改动被守卫保护的那份配置，守卫必须变红。防「断言与实现脱钩」
   （`IMP-034` 的教训）。
7. `test_scoring_admission_requires_evidence` —— 蓝图 §5「没有稳定增益时不强行入模」
   落成会报红的错误。
8. `test_aliases_resolve_real_scenario_literals` —— 别名守卫打在**真实字面量**上
   （从 `opportunity_learning.py` 源码抓），不是打在自造的样例上。
9. `test_agenda_knowledge_base_uses_the_single_parser` —— 证明「唯一解析实现」真的唯一
   （议程第八路与本模块口径必须一致，否则又是两份真相源）。
10. `test_snapshot_citations_*` —— 三态可区分（`BUG-016` 同族：空与坏不可同形）。
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.opportunity_learning import OpportunityDecisionSnapshot
from app.models.watchlist import Base
from app.picks import kb_routing as kr
from app.picks.opportunity_learning import (
    build_intraday_records,
    build_notification_records,
    archive_records,
    learning_summary,
    replay_run,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
BLUEPRINT = PROJECT_ROOT / "docs" / "summary" / "system-final-blueprint.md"
OPPORTUNITY_LEARNING_SRC = BACKEND_ROOT / "app" / "picks" / "opportunity_learning.py"

#: 状态列带备注、或状态为 `📎` 而**被旧口径静默漏掉**的 16 条。
#: 来源：2026-09-16 实测（旧正则匹配 162 / 唯一实现 178）。这是那条偏差的直接回归。
_REGRESSION_IDS = (
    "KB-STOCK-01", "KB-STOCK-02", "KB-STOCK-03", "KB-STOCK-04",  # 📎 示例
    "KB-STOCK-27", "KB-STOCK-29", "KB-STOCK-30", "KB-STOCK-31",
    "KB-STOCK-32", "KB-STOCK-33", "KB-STOCK-34", "KB-STOCK-35", "KB-STOCK-36",
    "KB-DEC-003", "KB-ENG-79", "KB-ENG-82",
)


def _blueprint_section5_rows() -> list[list[str]]:
    """从蓝图 §5 原文解析目标路由表（**判定面 = 文档原文**，不是测试里抄一份）。"""
    text = BLUEPRINT.read_text(encoding="utf-8")
    start = text.index("## 5. 知识库")
    end = text.index("## 6.", start)
    rows: list[list[str]] = []
    header_seen = False
    for line in text[start:end].splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if cells and cells[0] == "场景":
            header_seen = True
            continue
        if not header_seen or set(stripped) <= set("|- "):
            continue
        rows.append(cells)
    return rows


# ---------------------------------------------------------------- 1. 路由表 ⇄ 蓝图


def test_scenario_table_matches_blueprint_verbatim():
    rows = _blueprint_section5_rows()
    # 判据自证：解析面必须真的拿到 4 行，否则本用例会退化成恒真（[[KB-ENG-72]] 同族）。
    assert len(rows) == 4, f"蓝图 §5 表格解析面异常：拿到 {len(rows)} 行"
    assert len(kr.SCENARIOS) == len(rows), "路由表场景数与蓝图 §5 表格行数必须一致"

    by_label = {rt.blueprint_label: rt for rt in kr.SCENARIOS.values()}
    for scenario, knowledge, purpose, prohibition in rows:
        assert scenario in by_label, f"蓝图 §5 的场景 {scenario!r} 未在 SCENARIOS 登记"
        rt = by_label[scenario]
        assert rt.knowledge_label == knowledge, f"{scenario}「可调用知识」与蓝图不符"
        assert rt.purpose == purpose, f"{scenario}「用途」与蓝图不符"
        assert rt.prohibition == prohibition, f"{scenario}「禁止事项」与蓝图不符"
    # 反向闭包：每个登记场景都能在蓝图里找到（防"多登记一个没人认的场景"）
    assert set(by_label) == {r[0] for r in rows}


# ---------------------------------------------------------------- 2/3. 索引覆盖度


def test_kb_index_parse_covers_every_table_row():
    index = kr.load_kb_index()
    assert index.available, f"知识库索引不可用：{index.note}"
    # 判据自证：条目数必须是个像样的数，路径写错时本用例不能静默恒绿。
    assert index.total > 100, f"解析出的条目数异常偏少：{index.total}"
    assert index.unparsed_rows == (), (
        "索引表出现解析器不认的行 ⇒ 该行会被静默漏掉（正是 RSH-027 修的偏差）："
        f"{index.unparsed_rows[:3]}"
    )
    # 覆盖度恒等式：判据面（candidate_rows）= 条目 + 册级汇总行 + 未解析行
    assert index.coverage_identity_holds(), (
        f"candidate_rows={index.candidate_rows} total={index.total} "
        f"book_level={len(index.book_level_rows)} unparsed={len(index.unparsed_rows)}"
    )
    assert index.total == index.candidate_rows - len(index.book_level_rows)
    # 索引里出现的册必须都已登记，否则引用它会被误报成「非法 ID」（理由错）
    assert index.unknown_books == (), f"索引出现未登记的册：{index.unknown_books}"
    # 册级汇总行必须**显式**存在，不能静默跳过（KB-REPO-* 是第五个册族）
    assert index.book_level_rows, "册级汇总行（形如 KB-XXX-*）不应为空，否则该行被静默丢弃"


def test_status_column_with_note_is_parsed():
    """逐条回归：状态列带备注 / 状态为 `📎` 的条目，旧口径全部漏掉。"""
    index = kr.load_kb_index()
    missing = [kb_id for kb_id in _REGRESSION_IDS if kb_id not in index.entries]
    assert not missing, f"以下条目未被解析（旧口径静默漏检的 16 条）：{missing}"
    for kb_id in _REGRESSION_IDS:
        assert index.status_of(kb_id) in kr.KNOWN_STATUSES
    # 具体形态：状态与备注必须**分开**取到，而不是把 `✅ 已测否` 当成一个整体
    assert index.status_of("KB-STOCK-27") == kr.STATUS_LANDED
    assert index.note_of("KB-STOCK-27") == "已测否"
    assert index.status_of("KB-STOCK-34") == kr.STATUS_PENDING
    assert index.status_of("KB-STOCK-01") == kr.STATUS_EXAMPLE
    # `⏳` 候选池（议程第八路的 C 类改进项来源）必须包含带备注的那条
    assert "KB-STOCK-34" in index.ids_by_status(kr.STATUS_PENDING)


def test_index_overview_exposes_coverage():
    overview = kr.index_overview()
    assert overview["unparsed_rows"] == []
    assert overview["coverage_identity_holds"] is True
    assert overview["book_level_rows"] == ["KB-REPO"]
    assert overview["unknown_books"] == []
    assert overview["example_count"] == len(kr.load_kb_index().ids_by_status(kr.STATUS_EXAMPLE))
    assert overview["registered_books"] == list(kr.KB_BOOKS)


# ---------------------------------------------------------------- 4. 盘中：状态过滤


def test_intraday_allows_trade_discipline_but_not_stale_or_example():
    accepted, rejected = kr.validate_kb_citations(
        "intraday_pick",
        # ✅ 已落地交易纪律 / 📎 示例 / ⏳ 待落地 / 未授权册 / 不存在 / 非法 ID
        ["KB-STOCK-07", "KB-STOCK-01", "KB-STOCK-25", "KB-DEC-014", "KB-STOCK-999", "nope"],
    )
    assert accepted == ["KB-STOCK-07"], "只有「已落地/试验中的交易纪律」可通过"
    # 每条驳回都必须带**理由**——空理由 = 与「没传」不可区分（BUG-016 同族）
    assert set(rejected) == {
        "KB-STOCK-01", "KB-STOCK-25", "KB-DEC-014", "KB-STOCK-999", "nope",
    }
    assert all(reason.strip() for reason in rejected.values())
    assert "只允许" in rejected["KB-STOCK-01"], "示例被拒的理由必须说明是状态档问题"
    assert "未授权册" in rejected["KB-DEC-014"]


def test_pre_open_allows_examples_but_kb_trade_must_be_landed():
    """盘前：`KB-STOCK` 不限状态（示例可读），`KB-TRADE` 限「已验证」= ✅。"""
    accepted, rejected = kr.validate_kb_citations(
        "pre_open_event", ["KB-STOCK-01", "KB-TRADE-11", "KB-TRADE-13"],
    )
    assert accepted == ["KB-STOCK-01", "KB-TRADE-11"], "KB-STOCK-01（📎）在盘前可读"
    assert "KB-TRADE-13" in rejected, "KB-TRADE-13 是 ⏳，不属「已验证的 KB-TRADE」"


def test_example_is_readable_but_never_a_hard_rule():
    """两层执行：允许**引用**示例（盘前），但示例**不得当硬规则**。"""
    accepted, _ = kr.validate_kb_citations("pre_open_event", ["KB-STOCK-01"])
    assert accepted == ["KB-STOCK-01"], "示例身份的知识应可被引用为参考输入"
    assert kr.is_hard_rule_source(kr.STATUS_LANDED) is True
    assert kr.is_hard_rule_source(kr.STATUS_EXAMPLE) is False
    with pytest.raises(kr.KbRoutingError) as exc:
        kr.assert_hard_rule_allowed("KB-STOCK-01", kr.STATUS_EXAMPLE)
    assert "不得当硬规则" in str(exc.value)


# ---------------------------------------------------------------- 5/6. 注入自证


def test_intraday_status_filter_is_load_bearing(monkeypatch):
    """注入自证：把盘中场景的状态限放宽到「不限」⇒ 示例必须立刻被放进来。

    若放宽后结果不变，说明状态过滤没在起作用（断言与实现脱钩）。
    """
    before, _ = kr.validate_kb_citations("intraday_pick", ["KB-STOCK-01"])
    assert before == [], "默认配置下 📎 必须被盘中拒绝"

    relaxed = replace(
        kr.SCENARIOS["intraday_pick"],
        allow=tuple(
            kr.KbAllowRule(rule.book) for rule in kr.SCENARIOS["intraday_pick"].allow
        ),
    )
    monkeypatch.setitem(kr.SCENARIOS, "intraday_pick", relaxed)
    after, _ = kr.validate_kb_citations("intraday_pick", ["KB-STOCK-01"])
    assert after == ["KB-STOCK-01"], "放宽状态限后应被接受 ⇒ 证明状态过滤是承重的"


def test_scoring_book_identity_is_load_bearing(monkeypatch):
    """注入自证：把 `KB-DEC` 塞进打分册 ⇒ 恒等式守卫必须变红。"""
    kr.assert_scoring_excludes_governance_books()  # 默认必须通过
    monkeypatch.setattr(kr, "SCORING_BOOKS", kr.SCORING_BOOKS | {"KB-DEC"})
    with pytest.raises(kr.KbRoutingError) as exc:
        kr.assert_scoring_excludes_governance_books()
    assert "不进入个股收益打分" in str(exc.value)


def test_scoring_admission_requires_evidence(monkeypatch):
    """蓝图 §5：没有消融证据就不许声明 KB 进入个股收益打分。"""
    kr.assert_scoring_admission_is_evidence_gated()  # 默认（全部 False）必须通过
    monkeypatch.setitem(
        kr.SCENARIOS, "intraday_pick",
        replace(kr.SCENARIOS["intraday_pick"], enters_scoring=True),
    )
    with pytest.raises(kr.KbRoutingError) as exc:
        kr.assert_scoring_admission_is_evidence_gated()
    assert "没有消融证据" in str(exc.value)
    # 补上证据即通过 —— 说明拦的是「缺证据」，不是「禁止入模」
    monkeypatch.setitem(
        kr.SCENARIOS, "intraday_pick",
        replace(
            kr.SCENARIOS["intraday_pick"],
            enters_scoring=True,
            ablation_evidence="有/无 KB 影子对照：可成交样本 42，净期望 +0.31pp",
        ),
    )
    kr.assert_scoring_admission_is_evidence_gated()


def test_route_fails_loud_on_unknown_scenario():
    with pytest.raises(kr.KbRoutingError) as exc:
        kr.route("no_such_scenario")
    assert "未登记的场景" in str(exc.value)


# ---------------------------------------------------------------- 8. 别名打在真实字面量上


def test_aliases_resolve_real_scenario_literals():
    """从**源码**抓 `scenario` 字面量，断言全部能解析（不是拿自造样例自证）。"""
    source = OPPORTUNITY_LEARNING_SRC.read_text(encoding="utf-8")
    literals = set(re.findall(r'"scenario":\s*"([^"]+)"', source))
    # 判据自证：抓不到就说明源码写法变了，本用例不能静默恒绿
    assert literals, f"未从 {OPPORTUNITY_LEARNING_SRC.name} 抓到任何 scenario 字面量"
    kr.assert_aliases_resolve(sorted(literals))
    # 落库列口径与 run_id 口径都必须映射到同一个 canonical 场景
    assert {kr.route(lit).key for lit in literals} == {"intraday_pick"}


def test_aliases_guard_rejects_unregistered_literal():
    with pytest.raises(kr.KbRoutingError) as exc:
        kr.assert_aliases_resolve(["definitely_not_registered"])
    assert "未登记别名" in str(exc.value)


# ---------------------------------------------------------------- 9. 唯一解析实现


def test_agenda_knowledge_base_uses_the_single_parser():
    """议程第八路必须与本模块**同源**——两份解析口径必然漂移（这正是旧缺陷成因）。"""
    from app.services.evolution import _collect_knowledge_base

    collected = _collect_knowledge_base()
    index = kr.load_kb_index()
    assert collected["available"] is True
    assert collected["total"] == index.total, (
        "议程第八路与 kb_routing 的解析口径不一致（存在第二份真相源）"
    )
    assert collected["by_status"] == index.by_status()
    assert collected["coverage"]["consistent"] is True
    pending_ids = {p["id"] for p in collected["pending"]}
    assert "KB-STOCK-34" in pending_ids, "⏳（带备注）条目必须进入议程候选池"


# ---------------------------------------------------------------- 10. 快照三态


def test_snapshot_citations_distinguishes_not_consulted_from_rejected():
    # ① 未引用（现状）：state 必须显式为 not_consulted，而不是靠 kb_ids 为空去猜
    ids_json, refs_json = kr.snapshot_citations("intraday_pick", [])
    assert json.loads(ids_json) == []
    refs = json.loads(refs_json)
    assert refs["state"] == kr.REF_STATE_NOT_CONSULTED
    assert refs["conflict"] == {}

    # ② 引用成功：state=cited，并带上状态与支持依据
    ids_json, refs_json = kr.snapshot_citations("intraday_pick", ["KB-STOCK-07"])
    assert json.loads(ids_json) == ["KB-STOCK-07"]
    refs = json.loads(refs_json)
    assert refs["state"] == kr.REF_STATE_CITED
    assert refs["status"] == {"KB-STOCK-07": kr.STATUS_LANDED}
    assert refs["support"][0]["kb_id"] == "KB-STOCK-07"
    assert refs["support"][0]["title"], "支持依据应带索引里的一句话（可回溯到条目）"

    # ③ 引了但全被驳回：**必须**与 ① 区分开（前者是异常，后者是现状）
    ids_json, refs_json = kr.snapshot_citations("intraday_pick", ["KB-STOCK-01"])
    assert json.loads(ids_json) == []
    refs = json.loads(refs_json)
    assert refs["state"] == kr.REF_STATE_REJECTED
    assert set(refs["conflict"]) == {"KB-STOCK-01"}
    assert refs["conflict"]["KB-STOCK-01"].strip()


# ---------------------------------------------------------------- 写入/读取闭环


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'kb-routing.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _payload():
    return {
        "trade_date": "2026-09-16",
        "hot_available": True,
        "themes": [{
            "theme": "算力", "stage": "发酵", "strength_tier": "强势",
            "participants": [{
                "symbol": "600001", "name": "甲", "price": 10.0,
                "change_pct": 6.8, "amount": 2e8, "board": "沪市主板",
                "tradability": {"level": "可参与", "basis": "报价可成交"},
                "linkage": {"level": "高", "basis": "题材成建制且进入临板区"},
            }],
            "stocks": [],
        }],
    }


def test_snapshots_persist_kb_citations(tmp_path):
    """写入↔读取闭环：`kb_ids` / `kb_refs` 落库、可重放、并在汇总里可度量。"""
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5),
    )
    assert rows, "构造记录失败"
    # 全部记录都带上三态字段，且默认（未传引用）为 not_consulted
    for row in rows:
        assert json.loads(row["kb_ids"]) == []
        assert json.loads(row["kb_refs"])["state"] == kr.REF_STATE_NOT_CONSULTED
    archive_records(run_id, rows, sf)

    # 再归档一轮：显式引用一条 ✅ 交易纪律，另一条是 📎（必被驳回）
    run_id2, rows2 = build_intraday_records(
        _payload(), trade_date="2026-09-17", as_of=datetime(2026, 9, 17, 10, 5),
        kb_ids=["KB-STOCK-07", "KB-STOCK-01"],
    )
    for row in rows2:
        assert json.loads(row["kb_ids"]) == ["KB-STOCK-07"]
        refs = json.loads(row["kb_refs"])
        assert refs["state"] == kr.REF_STATE_CITED
        assert set(refs["conflict"]) == {"KB-STOCK-01"}
    archive_records(run_id2, rows2, sf)

    with sf() as db:
        stored = db.execute(
            select(OpportunityDecisionSnapshot.kb_ids).where(
                OpportunityDecisionSnapshot.run_id == run_id2
            )
        ).scalars().all()
    assert stored and all(json.loads(v) == ["KB-STOCK-07"] for v in stored)

    # 重放侧要能看到「当时引了什么」
    replay = replay_run(run_id2, sf)
    assert replay["records"] == len(rows2)
    assert replay["items"][0]["kb_ids"] == ["KB-STOCK-07"]

    # 汇总侧把「KB 未接入」变成可读出的数（不是靠读代码推断）
    summary = learning_summary("2026-09-16", sf)
    assert summary["kb_ref_states"] == {kr.REF_STATE_NOT_CONSULTED: len(rows)}
    summary2 = learning_summary("2026-09-17", sf)
    assert summary2["kb_ref_states"] == {kr.REF_STATE_CITED: len(rows2)}


def test_notification_pipeline_records_kb_state(tmp_path):
    """通知闸门（别名 `buy_point`）同样落三态字段。"""
    _, rows = build_notification_records(
        [{"symbol": "600001", "name": "甲", "confidence": {"tier": "高"}}],
        trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5),
        hits=[{"item": {"symbol": "600001"}, "price": 10.0, "chg": 3.0}],
        skips=[], dispatch_by_symbol={"600001": "notified"},
    )
    assert len(rows) == 1
    assert json.loads(rows[0]["kb_refs"])["state"] == kr.REF_STATE_NOT_CONSULTED
    _, rows2 = build_notification_records(
        [{"symbol": "600001", "name": "甲", "confidence": {"tier": "高"}}],
        trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5),
        hits=[{"item": {"symbol": "600001"}, "price": 10.0, "chg": 3.0}],
        skips=[], dispatch_by_symbol={"600001": "notified"},
        kb_ids=["KB-TRADE-11"],
    )
    assert json.loads(rows2[0]["kb_ids"]) == ["KB-TRADE-11"]


def test_kb_routing_route_is_registered():
    """端点必须真的挂在 router 上（防「文档写了、路由没挂」）。"""
    from app.api.routes.picks_intraday import router

    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/picks/kb-routing" in paths


# BUG-025: missing or corrupt evidence must not become a verified citation.
@pytest.mark.parametrize("scenario,kb_id", [
    ("pre_open_event", "KB-STOCK-999"),
    ("intraday_pick", "KB-STOCK-999"),
    ("post_close_review", "KB-TRADE-999"),
    ("system_evolution", "KB-ENG-999"),
])
@pytest.mark.parametrize("available", [False, True])
def test_bug025_empty_index_cannot_verify_citations(scenario, kb_id, available):
    index = kr.KbIndex(available=available, note="test empty index")
    accepted, rejected = kr.validate_kb_citations(scenario, [kb_id], index)
    assert accepted == []
    assert "索引" in rejected[kb_id]
    ids, raw = kr.snapshot_citations(scenario, [kb_id], index)
    refs = json.loads(raw)
    assert json.loads(ids) == []
    assert refs["state"] == kr.REF_STATE_REJECTED
    assert refs["support"] == [] and refs["status"] == {}
    assert refs["requested"] == [kb_id]
    assert refs["index_note"]


def _bug025_index(tmp_path):
    path = tmp_path / "citation-index.md"
    path.write_text("| KB-STOCK-07 | verified discipline | ✅ | 2026-09-16 |\n")
    return path, kr.load_kb_index(path)


@pytest.mark.parametrize("extra_row", [
    "| KB-STOCK-08 | broken status | ? | 2026-09-16 |\n",
    "| KB-STOCK-07 | conflicting duplicate | 📎 | 2026-09-16 |\n",
    "| KB-UNKNOWN-01 | unknown book | ✅ | 2026-09-16 |\n",
])
def test_bug025_partial_or_ambiguous_index_cannot_verify(tmp_path, extra_row):
    path, _ = _bug025_index(tmp_path)
    path.write_text(path.read_text() + extra_row)
    index = kr.load_kb_index(path)
    # Preserve parser diagnostics while refusing to certify a damaged index.
    assert index.available and index.entries
    ids, raw = kr.snapshot_citations("pre_open_event", ["KB-STOCK-07"], index)
    refs = json.loads(raw)
    assert json.loads(ids) == []
    assert refs["state"] == kr.REF_STATE_REJECTED
    assert "完整性" in refs["conflict"]["KB-STOCK-07"]
    assert refs["index_note"]


@pytest.mark.parametrize("changes", [
    {"id": "KB-STOCK-08"}, {"book": "KB-ENG"},
    {"title": "   "}, {"status": "unknown"},
])
def test_bug025_entry_identity_and_status_must_be_real(tmp_path, changes):
    _, index = _bug025_index(tmp_path)
    index.entries["KB-STOCK-07"] = replace(index.entries["KB-STOCK-07"], **changes)
    accepted, rejected = kr.validate_kb_citations("pre_open_event", ["KB-STOCK-07"], index)
    assert accepted == []
    assert rejected["KB-STOCK-07"]


def test_bug025_requests_are_separate_ordered_and_generator_safe(tmp_path):
    _, index = _bug025_index(tmp_path)
    requested = (x for x in [" KB-STOCK-07 ", "", "KB-STOCK-999", "KB-STOCK-07"])
    ids, raw = kr.snapshot_citations("intraday_pick", requested, index)
    refs = json.loads(raw)
    assert json.loads(ids) == ["KB-STOCK-07"]
    assert refs["requested"] == ["KB-STOCK-07", "KB-STOCK-999"]
    assert refs["state"] == kr.REF_STATE_CITED
    assert set(refs["conflict"]) == {"KB-STOCK-999"}
    assert refs["support"] == [{"kb_id": "KB-STOCK-07", "status": "✅", "title": "verified discipline"}]


def test_bug025_no_request_is_not_consulted_even_if_index_is_missing():
    ids, raw = kr.snapshot_citations("intraday_pick", ["", "  "], kr.KbIndex())
    refs = json.loads(raw)
    assert json.loads(ids) == [] and refs["requested"] == []
    assert refs["state"] == kr.REF_STATE_NOT_CONSULTED
    assert refs["conflict"] == {} and refs["support"] == []
    assert refs["index_note"]


@pytest.mark.parametrize("failure", ["missing", "empty", "invalid_utf8"])
def test_bug025_disk_failure_recovers_with_one_index_read(tmp_path, monkeypatch, failure):
    path = tmp_path / "restored-index.md"
    if failure == "empty":
        path.write_text("")
    elif failure == "invalid_utf8":
        path.write_bytes(b"\xff")
    monkeypatch.setattr(kr, "KB_INDEX_PATH", path)
    original_loader = kr.load_kb_index
    reads = []

    def counted_load():
        reads.append(True)
        return original_loader()

    monkeypatch.setattr(kr, "load_kb_index", counted_load)
    ids, refs = kr.snapshot_citations("intraday_pick", ["KB-STOCK-07"])
    assert len(reads) == 1
    assert json.loads(ids) == []
    assert json.loads(refs)["state"] == kr.REF_STATE_REJECTED
    path.write_text("| KB-STOCK-07 | restored discipline | ✅ | 2026-09-16 |\n")
    ids, refs = kr.snapshot_citations("intraday_pick", ["KB-STOCK-07"])
    assert len(reads) == 2
    assert json.loads(ids) == ["KB-STOCK-07"]
    assert json.loads(refs)["state"] == kr.REF_STATE_CITED


def test_bug025_unavailable_index_cannot_certify_retained_entries(tmp_path):
    _, index = _bug025_index(tmp_path)
    index.available = False
    accepted, rejected = kr.validate_kb_citations("intraday_pick", ["KB-STOCK-07"], index)
    assert accepted == [] and "不可用" in rejected["KB-STOCK-07"]


def test_bug025_missing_index_does_not_weaken_scenario_permission():
    accepted, rejected = kr.validate_kb_citations(
        "intraday_pick", ["KB-DEC-014", "not-an-id"], kr.KbIndex(),
    )
    assert accepted == []
    assert "未授权册" in rejected["KB-DEC-014"]
    assert "合法" in rejected["not-an-id"]


def test_bug025_archive_replay_and_summary_preserve_evidence(tmp_path, monkeypatch):
    path, _ = _bug025_index(tmp_path)
    monkeypatch.setattr(kr, "KB_INDEX_PATH", path)
    sf = _factory(tmp_path)
    old_run, old_rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5),
        kb_ids=["KB-STOCK-07"],
    )
    assert old_rows
    archive_records(old_run, old_rows, sf)
    old_refs = old_rows[0]["kb_refs"]
    # An index failure must affect new evidence only, not prior decisions.
    path.write_text("broken index")
    new_run, new_rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 10),
        kb_ids=["KB-STOCK-07"],
    )
    assert new_run != old_run and new_rows
    assert all(json.loads(row["kb_refs"])["state"] == "rejected" for row in new_rows)
    archive_records(new_run, new_rows, sf)
    # Repeat archive attempts cannot overwrite the existing accepted evidence.
    archive_records(old_run, [{**row, "kb_ids": "[]", "kb_refs": "{}"} for row in old_rows], sf)
    legacy_run, legacy_rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 15),
    )
    for row in legacy_rows:
        row["kb_ids"], row["kb_refs"] = "[]", "{}"
    archive_records(legacy_run, legacy_rows, sf)
    _, notifications = build_notification_records(
        [{"symbol": "600001", "name": "甲", "confidence": {"tier": "高"}}],
        trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 20),
        hits=[{"item": {"symbol": "600001"}, "price": 10.0, "chg": 3.0}],
        skips=[], dispatch_by_symbol={"600001": "suppressed"}, kb_ids=["KB-STOCK-07"],
    )
    assert notifications and json.loads(notifications[0]["kb_refs"])["state"] == "rejected"

    def forbidden_reload(*args, **kwargs):
        raise AssertionError("Replay must use archived references, not the live index")

    monkeypatch.setattr(kr, "load_kb_index", forbidden_reload)
    replay = replay_run(old_run, sf)
    assert replay["mismatches"] == 0
    assert replay["items"][0]["kb_refs"] == json.loads(old_refs)
    assert replay_run(new_run, sf)["items"][0]["kb_refs"]["state"] == "rejected"
    assert replay_run(legacy_run, sf)["items"][0]["kb_refs"] == {}
    assert learning_summary("2026-09-16", sf)["kb_ref_states"] == {
        "cited": len(old_rows), "rejected": len(new_rows), "legacy": len(legacy_rows),
    }
