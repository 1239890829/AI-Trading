"""applied 半自动写回提示（`review/writeback`）单测。

S2-11 前本模块只产 `{param, file, current, suggested}` —— 一条没有风险、没有后果、
没有回滚方式、也无法验证落地与否的提示。于是 `applied` 是个空动作：点了采纳，
响应里飘过一串数字，事后无从追问。这里守的是补齐后的**载荷**与**落地核对**。
"""
from __future__ import annotations

from app.review.writeback import (
    MODULE_META,
    PARAM_META,
    PARAM_TARGETS,
    audit_applied_landed,
    build_applied_payload,
    build_param_diff,
    param_meta,
)


def test_registry_covers_registered_params_only():
    assert "PENALTY_Y1" in PARAM_TARGETS
    assert "FLOW_SURGE_YI" in PARAM_TARGETS
    # 每个登记项都指向真实文件（防手滑写错展示路径）
    for _, file_name in PARAM_TARGETS.values():
        assert file_name.startswith("app/")


def test_extract_suggestion_and_runtime_current():
    text = "建议把 PENALTY_Y1 6.0 → 8.0，观察一个月后再评估"
    diffs = build_param_diff(text)
    assert len(diffs) == 1
    d = diffs[0]
    assert d["param"] == "PENALTY_Y1"
    assert d["suggested"] == 8.0
    assert d["current_in_text"] == 6.0
    # 运行时值可读（halt_risk 模块常量=6.0）且与文本一致 → resolved 且不 stale
    assert d["resolved"] is True
    assert d["stale"] is False
    assert d["current"] == 6.0


def test_stale_flag_when_text_outdated():
    text = "RED_DEV_10D 50.0 → 60.0"  # 模块实际是 80.0 → 文本里的当前值过期
    diffs = build_param_diff(text)
    assert diffs[0]["stale"] is True


def test_unknown_param_skipped_and_no_match_empty():
    assert build_param_diff("SOME_UNKNOWN_PARAM 1.0 → 2.0") == []
    assert build_param_diff("没有参数调整意图的普通说明") == []
    assert build_param_diff("") == []


# ---------------------------------------------------------------- 载荷四件套


def test_diff_carries_risk_consumers_verify_rollback():
    """**定点回归**：每条 diff 必须带齐决策所需的四件事。

    收敛前只有 param/file/current/suggested —— 人拿到后不知道改了影响谁、
    怎么验证、怎么回滚，"采纳"就成了没有内容的动作。
    """
    d = build_param_diff("PENALTY_Y1 6.0 → 8.0")[0]
    for key in ("risk", "consumers", "verify", "rollback", "landed"):
        assert key in d, f"缺 {key}"
    assert d["risk"] == "high", "halt_risk 属风控闸门，必须高风险"
    assert d["consumers"], "下游消费者不得为空"
    assert "pytest" in d["verify"]
    assert "PENALTY_Y1=6.0" in d["rollback"], "回滚必须给出可直接执行的原值"


def test_landed_false_before_change_and_true_when_matches_runtime():
    """`landed` 由运行时值判定——建议值 ≠ 当前值 ⇒ False；相等 ⇒ True。"""
    d = build_param_diff("PENALTY_Y1 6.0 → 8.0")[0]
    assert d["landed"] is False
    d2 = build_param_diff("PENALTY_Y1 6.0 → 6.0")[0]
    assert d2["landed"] is True


def test_landed_is_none_safe_when_runtime_unreadable(monkeypatch):
    """运行时值读不到时 `landed` 必须是 False（判不出 ≠ 已落地），不是 True。"""
    import app.review.writeback as wb

    monkeypatch.setattr(wb, "_current_value", lambda _m, _p: None)
    d = build_param_diff("PENALTY_Y1 6.0 → 8.0")[0]
    assert d["resolved"] is False
    assert d["landed"] is False
    assert "人工确认原值" in d["rollback"]


def test_monitoring_params_are_low_risk():
    """监控类参数改错只影响告警灵敏度，不该与风控闸门同等级。"""
    assert param_meta("app.picks.signal_health", "CUSUM_THRESHOLD")["risk"] == "low"
    assert param_meta("app.picks.signal_health", "CUSUM_DELTA")["risk"] == "low"


def test_unregistered_module_falls_back_to_unknown_not_invented():
    """没有元信息就标 unknown，**不编造**验证方式。"""
    meta = param_meta("app.picks.nowhere", "X")
    assert meta["risk"] == "unknown"
    assert meta["consumers"] == []
    assert "未登记" in meta["verify"]


def test_every_registered_param_has_meta_coverage():
    """注册表里的参数必须都有元信息（模块级默认也算覆盖）——防新增参数忘了登记。"""
    for param, (module, _file) in PARAM_TARGETS.items():
        meta = param_meta(module, param)
        assert meta["risk"] in ("high", "medium", "low", "unknown")
        assert meta["verify"]
    # 参数级覆盖不得指向不存在的参数
    assert set(PARAM_META) <= set(PARAM_TARGETS)
    assert set(MODULE_META) <= {m for m, _ in PARAM_TARGETS.values()}


# ---------------------------------------------------------------- 顶层载荷


def test_applied_payload_summary_and_counts():
    p = build_applied_payload("PENALTY_Y1 6.0 → 8.0 且 CUSUM_DELTA 0.5 → 0.8")
    assert p["available"] is True
    assert p["counts"]["total"] == 2
    assert p["counts"]["high"] == 1
    assert p["counts"]["low"] == 1
    assert "高风险" in p["summary"]
    assert "PENALTY_Y1" in p["summary"]


def test_applied_payload_exposes_unresolved_params():
    """文本里提到但不在注册表的疑似参数**必须列出**，不能静默丢弃。"""
    p = build_applied_payload("PENALTY_Y1 6.0 → 8.0，另建议调整 NEW_FANCY_PARAM 1 → 2")
    assert "NEW_FANCY_PARAM" in p["unresolved"]
    assert p["counts"]["total"] == 1


def test_applied_payload_empty_case_is_explicit():
    p = build_applied_payload("这次主要改流程，不涉及参数")
    assert p["available"] is False
    assert p["items"] == []
    assert "未识别到" in p["summary"]
    assert "不会自动修改代码" in p["caveat"]


def test_applied_payload_declares_no_auto_write():
    """边界声明：无论有没有识别到参数，都必须说明系统不会自动改代码。"""
    for text in ("PENALTY_Y1 6.0 → 8.0", "无参数"):
        assert "不会自动修改代码" in build_applied_payload(text)["caveat"]


# ---------------------------------------------------------------- 落地核对


def _row(id_, title, note="", status="applied"):
    return {"id": id_, "title": title, "resolution_note": note, "status": status}


def test_audit_splits_landed_not_landed_and_no_intent():
    """三类必须分开：`no_param_intent` 单列，不稀释落地率分母。"""
    rows = [
        _row(1, "PENALTY_Y1 6.0 → 6.0"),          # 建议值 == 运行时值 ⇒ 已落地
        _row(2, "PENALTY_Y1 6.0 → 8.0"),          # 未落地
        _row(3, "把复盘流程改成每日自动跑"),           # 无参数意图
        _row(4, "RED_DEV_10D 80.0 → 90.0", status="pending"),  # 非 applied，不计
    ]
    a = audit_applied_landed(rows)
    assert a["total_applied"] == 3
    assert a["with_param_intent"] == 2
    assert a["landed"] == 1
    assert a["not_landed"] == 1
    assert a["no_param_intent"] == 1
    assert not any(i["id"] == 4 for i in a["items"]["not_landed"])


def test_audit_not_landed_exposes_pending_params():
    a = audit_applied_landed([_row(7, "PENALTY_Y1 6.0 → 8.0")])
    entry = a["items"]["not_landed"][0]
    assert entry["pending"] == ["PENALTY_Y1"]
    assert entry["id"] == 7


def test_audit_empty_is_explicit():
    a = audit_applied_landed([])
    assert a["total_applied"] == 0
    assert a["note"] == "尚无 applied 改进项"


def test_audit_ignores_non_applied_statuses():
    """只有 `applied` 参与核对——pending/rejected 本就不该被追问落地与否。"""
    rows = [_row(1, "PENALTY_Y1 6.0 → 8.0", status="rejected"),
            _row(2, "PENALTY_Y1 6.0 → 8.0", status="pending")]
    a = audit_applied_landed(rows)
    assert a["total_applied"] == 0
    assert a["not_landed"] == 0


def test_audit_reads_resolution_note_too():
    """参数意图常写在处置说明里（而非标题），两处都要扫。"""
    a = audit_applied_landed([_row(1, "收紧熔断阈值", note="已按建议 PENALTY_Y1 6.0 → 6.0")])
    assert a["landed"] == 1
