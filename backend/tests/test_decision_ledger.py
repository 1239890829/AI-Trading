"""决策台账（`app/services/decision_ledger.py`）单测（S2-11 第 5 步）。

**为什么需要它**：决策做完就"蒸发"——决策本身在 `review_action_items.status`、依据在 `evidence`/核验产物、事后在参数运行时值，三者互不关联，也没有统一入口。本模块用**只读聚合**把它们并陈。

**本文件锁住的是三条性质**（比"能查出数据"更重要）：
1. **未处置的不算决策**：`pending` 是待办，进台账会把它当成"已决定"，比不记更糟；
2. **采纳 ≠ 落地**：`applied` 只是状态位，参数是否真的改了由**运行时值**判定，不一致的必须单列成「决策未兑现」；
3. **不遮掩失败**：某个数据源读不到要写进 `problems`，不得静默返回"暂无决策"。
"""
from __future__ import annotations

from app.services import decision_ledger as dl


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    """最简 session：按预设返回固定行。"""

    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, _stmt):
        return _FakeResult(self._rows)


def _sf(rows):
    return lambda: _FakeSession(rows)


def _item(id=1, title="收紧熔断阈值 PENALTY_Y1 6.0 → 8.0", status="applied", note="", evidence=""):
    class R:
        pass

    r = R()
    r.id = id
    r.title = title
    r.category = "parameter"
    r.status = status
    r.resolution_note = note
    r.evidence = evidence
    r.resolved_at = None
    r.trade_date = "20260911"
    return r


# ---------------------------------------------------------------- 性质一：未处置不算决策


def test_pending_items_are_excluded(monkeypatch):
    """pending 是待办不是决策——进台账会让人以为已经决定了。

    隔离掉核验与参数变更两路：它们分别读**磁盘产物**与另一张表，
    不受这里的假 session 控制，不隔离就会混进条目。
    """
    monkeypatch.setattr(dl, "_from_verification", lambda: [])
    monkeypatch.setattr(dl, "_from_param_changes", lambda _sf, limit=20: [])
    out = dl.collect_decision_ledger(_sf([_item(status="pending")]))
    assert out["entries"] == []
    assert out["available"] is False
    assert out["note"]


def test_disposed_items_are_included():
    out = dl.collect_decision_ledger(_sf([_item(status="applied"), _item(status="rejected")]))
    kinds = {e["kind"] for e in out["entries"]}
    assert "改进项处置" in kinds
    assert out["counts"].get("改进项处置") == 2


def test_status_labels_are_chinese_and_unknown_passes_through():
    """未登记的状态**原样透出**，不猜中文（猜错比显示英文更有误导性）。"""
    out = dl.collect_decision_ledger(_sf([_item(status="confirmed"), _item(status="weird_new")]))
    decisions = {e["decision"] for e in out["entries"]}
    assert "已确认" in decisions
    assert "weird_new" in decisions


# ---------------------------------------------------------------- 性质二：采纳 ≠ 落地


def test_applied_with_matching_runtime_value_is_landed():
    """运行时值已等于建议值 ⇒ 已落地（不进「决策未兑现」）。

    这里用 monkeypatch 造一个"已改过"的运行时值（模块常量 6.0 → 8.0）。
    """
    import app.picks.halt_risk as hr

    old = hr.PENALTY_Y1
    hr.PENALTY_Y1 = 8.0
    try:
        out = dl.collect_decision_ledger(_sf([_item()]))
    finally:
        hr.PENALTY_Y1 = old
    kinds = [e["kind"] for e in out["entries"]]
    assert "决策未兑现" not in kinds
    assert any("已落地" in e["outcome"] for e in out["entries"])


def test_applied_without_actual_change_is_flagged(monkeypatch):
    """标了 applied 但运行时值没变 ⇒ 必须单列「决策未兑现」。"""
    monkeypatch.setattr(dl, "_landed_note", lambda r: "⚠️ 未落地：PENALTY_Y1")
    out = dl.collect_decision_ledger(_sf([_item()]))
    flagged = [e for e in out["entries"] if e["kind"] == "决策未兑现"]
    assert len(flagged) == 1
    assert "未落地" in flagged[0]["outcome"]


def test_non_parameter_items_say_so_not_pretend_landed():
    """流程/文档类改进没有参数意图 ⇒ 明确说"不适用"，不得伪装成已落地。"""
    out = dl.collect_decision_ledger(_sf([_item(title="把复盘流程改成每日自动跑")]))
    note = " ".join(e["outcome"] for e in out["entries"])
    assert "无参数意图" in note


# ---------------------------------------------------------------- 性质三：不遮掩失败


def test_source_failure_is_recorded_in_problems(monkeypatch):
    """某个数据源读不到要写进 problems，不得静默返回"暂无决策"。"""

    def boom(*_a, **_kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(dl, "_from_action_items", boom)
    out = dl.collect_decision_ledger(_sf([]))
    assert any("改进项" in p for p in out["problems"])


def test_caveat_declares_readonly_and_no_causality():
    """边界声明必须随载荷给出，消费方才知道它不能当权威数据源。"""
    out = dl.collect_decision_ledger(_sf([_item()]))
    assert "只读" in out["caveat"]
    assert "不做因果判定" in out["caveat"]


def test_entry_keys_are_stable():
    """字段集是消费契约，改动须同步更新 `ENTRY_KEYS` 与本测试。"""
    assert set(dl.ENTRY_KEYS) == {"kind", "subject", "decision", "basis", "outcome", "at"}
    out = dl.collect_decision_ledger(_sf([_item()]))
    for e in out["entries"]:
        assert set(e) == set(dl.ENTRY_KEYS)


def test_empty_everywhere_is_explicitly_empty(monkeypatch):
    """全部来源都为空时，必须显式说"暂无"，不能给个空列表让人以为查成功。"""
    monkeypatch.setattr(dl, "_from_verification", lambda: [])
    out = dl.collect_decision_ledger(_sf([]))
    assert out["available"] is False
    assert out["entries"] == []
    assert out["note"]



def test_bug027_verification_view_never_conflates_machine_pass_and_admission(monkeypatch):
    from app.research import strategy_verify as sv, strategy_trials as st, verify_registry as vr
    trial_evidence = st.trial_family_evidence(
        [{"label": "fixture", "n": 500, "excess": 0.5, "std": 2.0}],
        selected_label="fixture",
    )
    overlap_evidence = {
        "evidence_version": st.EVIDENCE_VERSION, "checked": True,
        "comparisons": [], "exact_duplicate": False,
    }
    protocol = sv.validation_protocol(
        horizon=5, cost_bps=sv.ADMISSION_COST_BPS,
        split={"purge_sessions": 5, "embargo_sessions": 5, "split_date_ms": 1_700_000_000_000},
        selection_scope="train_only", universe_point_in_time=True, feature_point_in_time=True,
        trials=1, multiple_testing_accounted=True, signal_overlap_checked=True,
        multiple_testing_evidence=trial_evidence,
        signal_overlap_evidence=overlap_evidence,
    )
    metrics = {"n": 500, "excess": 0.5, "horizon": 5, "cost_bps": sv.ADMISSION_COST_BPS}
    good = sv.gate_verdict(metrics, yearly_pos=9, yearly_tot=10,
                           limit_up_share=0.05, excess_median=0.2, excess_win_rate=0.57,
                           protocol=protocol)
    missing = sv.gate_verdict(metrics, yearly_pos=9, yearly_tot=10,
                              limit_up_share=0.05, excess_median=None, excess_win_rate=0.57,
                              protocol=protocol)
    records = [
        {"key": "legacy", "verdict": "pass"},
        {"key": "current", "verdict": "pass", "gate": good},
        {"key": "missing", "verdict": "observe", "gate": missing},
    ]
    monkeypatch.setattr(vr, "list_records", lambda: records)
    rows = {row["subject"]: row for row in dl._from_verification()}
    assert rows["legacy"]["decision"] == "历史通过（待复核）"
    assert rows["current"]["decision"] == "机器条款通过（待终审）"
    assert rows["missing"]["decision"] == "观察"
    assert "未验 1 项" in rows["missing"]["outcome"]
    assert all(row["decision"] != "准入" for row in rows.values())
