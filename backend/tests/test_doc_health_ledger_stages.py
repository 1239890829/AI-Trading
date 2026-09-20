"""阶段账本行为守卫：保留旧 P/Q 的闭包、部分完成和漏销账防线。

旧 A/F/H 重复状态冲突改由重复定义判红；漏闭环记录改由完成证据判红。
历史划销不再是活动状态；缺载体/空面仍必红，另钉依赖环和旧号去向。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/doc-health.py"


def load():
    spec = importlib.util.spec_from_file_location("doc_health_ledger_probe", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def task(tid="BUG-014", status="待执行", deps="无", evidence="尚未实施", *, gate="G0", order=10, role="阻断", effect="无"):
    return f"""## {tid}

**修复实际缺口**

- **状态**：{status}
- **优先级**：P0
- **阶段门**：{gate}
- **门内序**：{order}
- **门禁角色**：{role}
- **依赖**：{deps}
- **效果前置**：{effect}
- **方案依据**：最终方案 W00
- **范围**：修复迁移连接
- **验收**：在隔离目标库断言表与字段
- **证据**：{evidence}
- **下一步**：条件为隔离环境可用后实施
- **恢复**：回退提交并从备份恢复隔离库
"""


@pytest.fixture
def probe(tmp_path, monkeypatch):
    mod = load()
    root = tmp_path
    docs = root / "docs"
    (docs / "stages").mkdir(parents=True)
    (docs / "archive").mkdir()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "DOCS", docs)
    monkeypatch.setattr(mod, "LEGACY_SOURCE_IDS", frozenset({"GOV-014"}), raising=False)
    index = ("### 5.9 阶段门、优先级与跨阶段治理\n"
             "G0 事实与安全底座 · G5 验收与发布 · GX 持续治理 · CROSS_GATE_EXCEPTION · 效果前置\n\n"
             "### 6.0 阶段索引\n\n| 阶段 | 内容 |\n|---|---|\n")
    for i in range(10):
        rel = f"stages/w{i:02}-phase.md"
        index += f"| W{i:02} | [阶段]({rel}) |\n"
        (docs / rel).write_text(f"# W{i:02} 阶段\n\n[总账](../retro-and-gaps.md#60-阶段索引)\n\n")
    (docs / "retro-and-gaps.md").write_text(index)
    (docs / "stages/w00-phase.md").write_text((docs / "stages/w00-phase.md").read_text() + task())
    (docs / mod.LEGACY_LEDGER).write_text("| GOV-014 | 已完成 | Git 原文 | 旧闭环留痕 |\n")
    (docs / "handoff.md").write_text(
        "# 当前现场\n"
        "- **当前主门**：G0\n"
        "- **主切片首选**：BUG-014\n"
        "- **门内候选**：BUG-014\n"
    )
    (root / "AGENTS.md").write_text("§5.9 CROSS_GATE_EXCEPTION\n")
    (docs / "collaboration-workflow.md").write_text("CROSS_GATE_EXCEPTION 效果前置\n")
    (docs / "plan-registry.md").write_text("文档自治治理契约 阶段门\n")
    (root / "skills" / "ashare-ledger-continue").mkdir(parents=True)
    (root / "skills" / "ashare-task-handoff").mkdir(parents=True)
    (root / "skills" / "ashare-ledger-continue" / "SKILL.md").write_text(
        "最低 CROSS_GATE_EXCEPTION 效果前置\n"
    )
    (root / "skills" / "ashare-task-handoff" / "SKILL.md").write_text(
        "CROSS_GATE_EXCEPTION 效果前置\n"
    )
    return mod


def edit(mod, rel, old, new):
    path = mod.DOCS / rel
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_real_ledger_has_no_stage_conflict():
    mod = load()
    assert mod.check_phase_index() == []
    assert mod.check_phase_tasks() == []
    assert mod.check_stage_gates() == []


def test_real_decision_propagation_is_closed():
    mod = load()
    assert mod.check_decision_propagation() == []


def test_decision_propagation_detects_stale_current_pointer(tmp_path, monkeypatch):
    mod = load()
    root = tmp_path
    docs = root / "docs"
    (docs / "summary").mkdir(parents=True)
    (root / "skills" / "ashare-ledger-continue").mkdir(parents=True)
    (root / "skills" / "ashare-task-handoff").mkdir(parents=True)

    (docs / "implementation-plan.md").write_text("# AShare AI Trader 实施校准方案 v9.4\nJev jev-integration.md\n")
    (docs / "INDEX.md").write_text("v9.4 jev-integration.md\n")
    (docs / "handoff.md").write_text("v9.4 jev-integration.md\n")
    (docs / "plan-registry.md").write_text("重大决策传播契约（防遗漏） 已更新 不适用\n")
    (docs / "collaboration-workflow.md").write_text("plan-registry.md\n")
    for name in ("system-final-blueprint.md", "ai-evolution.md", "architecture-design.md"):
        (docs / "summary" / name).write_text("v9.4\n")
    (root / "AGENTS.md").write_text("v9.3 jev-integration.md\n")
    (root / "skills" / "ashare-ledger-continue" / "SKILL.md").write_text(
        " ".join([
            "jev-integration.md", "docs/handoff.md", "docs/INDEX.md", "docs/plan-registry.md",
            "docs/implementation-plan.md", "docs/jev-integration.md", "docs/retro-and-gaps.md", "docs/stages/"
        ])
    )
    (root / "skills" / "ashare-task-handoff" / "SKILL.md").write_text("plan-registry.md\n")
    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "DOCS", docs)

    errors = mod.check_decision_propagation()
    assert any("AGENTS.md" in error and "v9.4" in error for error in errors)


def test_decision_propagation_auto_discovers_new_stale_current_summary(tmp_path, monkeypatch):
    mod = load()
    root = tmp_path
    docs = root / "docs"
    (docs / "summary").mkdir(parents=True)
    (root / "skills" / "ashare-ledger-continue").mkdir(parents=True)
    (root / "skills" / "ashare-task-handoff").mkdir(parents=True)

    (docs / "implementation-plan.md").write_text("# AShare AI Trader 实施校准方案 v9.4\nJev jev-integration.md\n")
    (docs / "INDEX.md").write_text("v9.4 jev-integration.md\n")
    (docs / "handoff.md").write_text("v9.4 jev-integration.md\n")
    (docs / "plan-registry.md").write_text("重大决策传播契约（防遗漏） 已更新 不适用\n")
    (docs / "collaboration-workflow.md").write_text("plan-registry.md\n")
    (docs / "summary" / "future-summary.md").write_text("> 当前方案为 ../implementation-plan.md v9.3\n")
    (root / "AGENTS.md").write_text("v9.4 jev-integration.md\n")
    (root / "skills" / "ashare-ledger-continue" / "SKILL.md").write_text(
        " ".join([
            "jev-integration.md", "docs/handoff.md", "docs/INDEX.md", "docs/plan-registry.md",
            "docs/implementation-plan.md", "docs/jev-integration.md", "docs/retro-and-gaps.md", "docs/stages/"
        ])
    )
    (root / "skills" / "ashare-task-handoff" / "SKILL.md").write_text("plan-registry.md\n")
    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "DOCS", docs)

    errors = mod.check_decision_propagation()
    assert any("future-summary.md" in error and "v9.3" in error for error in errors)


def test_open_evolution_guard_rejects_star_hard_gate(tmp_path, monkeypatch):
    mod = load()
    root = tmp_path
    docs = root / "docs"
    (docs / "stages").mkdir(parents=True)
    (docs / "kb").mkdir(parents=True)
    (root / "skills" / "ashare-ledger-continue").mkdir(parents=True)
    (root / "skills" / "ashare-task-handoff").mkdir(parents=True)
    (root / "skills" / "ashare-innovation-radar").mkdir(parents=True)

    (docs / "implementation-plan.md").write_text(
        "# AShare AI Trader 实施校准方案 v9.5\n"
        "Jev jev-integration.md continuous-evolution.md\n"
    )
    (docs / "INDEX.md").write_text("v9.5 jev-integration.md continuous-evolution.md\n")
    (docs / "handoff.md").write_text("v9.5 jev-integration.md continuous-evolution.md\n")
    (docs / "plan-registry.md").write_text(
        "重大决策传播契约（防遗漏） 已更新 不适用 continuous-evolution.md\n"
    )
    (docs / "collaboration-workflow.md").write_text("plan-registry.md\n")
    (docs / "continuous-evolution.md").write_text("# living plan\n")
    (docs / "stages" / "w08-governance.md").write_text(
        "## GOV-025\n方案：continuous-evolution.md\n"
    )
    (docs / "kb" / "05-repo-tracker.md").write_text(
        "Stars 只作成熟度/关注度弱信号\n5. **≥1000★ 硬门槛**保持\n"
    )
    (root / "AGENTS.md").write_text("v9.5 jev-integration.md continuous-evolution.md\n")
    (root / "skills" / "ashare-ledger-continue" / "SKILL.md").write_text(
        " ".join([
            "docs/handoff.md", "docs/INDEX.md", "docs/plan-registry.md",
            "docs/implementation-plan.md", "docs/continuous-evolution.md",
            "docs/jev-integration.md", "docs/retro-and-gaps.md", "docs/stages/"
        ])
    )
    (root / "skills" / "ashare-task-handoff" / "SKILL.md").write_text("plan-registry.md\n")
    (root / "skills" / "ashare-innovation-radar" / "SKILL.md").write_text(
        " ".join([
            "docs/continuous-evolution.md", "docs/plan-registry.md",
            "docs/retro-and-gaps.md", "docs/stages/", "docs/jev-integration.md"
        ])
    )
    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "DOCS", docs)

    errors = mod.check_decision_propagation()
    assert any("1000★ Star 硬门" in error for error in errors)


def test_open_evolution_guard_requires_governance_owner(tmp_path, monkeypatch):
    mod = load()
    root = tmp_path
    docs = root / "docs"
    (docs / "stages").mkdir(parents=True)
    (docs / "kb").mkdir(parents=True)
    (root / "skills" / "ashare-ledger-continue").mkdir(parents=True)
    (root / "skills" / "ashare-task-handoff").mkdir(parents=True)
    (root / "skills" / "ashare-innovation-radar").mkdir(parents=True)

    (docs / "implementation-plan.md").write_text(
        "# AShare AI Trader 实施校准方案 v9.5\n"
        "Jev jev-integration.md continuous-evolution.md\n"
    )
    for name in ("INDEX.md", "handoff.md"):
        (docs / name).write_text("v9.5 jev-integration.md continuous-evolution.md\n")
    (docs / "plan-registry.md").write_text(
        "重大决策传播契约（防遗漏） 已更新 不适用 continuous-evolution.md\n"
    )
    (docs / "collaboration-workflow.md").write_text("plan-registry.md\n")
    (docs / "continuous-evolution.md").write_text("# living plan\n")
    (docs / "stages" / "w08-governance.md").write_text("# W08\n")
    (docs / "kb" / "05-repo-tracker.md").write_text(
        "Stars 只作成熟度/关注度弱信号\n取消 ≥1000★ 硬门槛\n"
    )
    (root / "AGENTS.md").write_text("v9.5 jev-integration.md continuous-evolution.md\n")
    (root / "skills" / "ashare-ledger-continue" / "SKILL.md").write_text(
        " ".join([
            "docs/handoff.md", "docs/INDEX.md", "docs/plan-registry.md",
            "docs/implementation-plan.md", "docs/continuous-evolution.md",
            "docs/jev-integration.md", "docs/retro-and-gaps.md", "docs/stages/"
        ])
    )
    (root / "skills" / "ashare-task-handoff" / "SKILL.md").write_text("plan-registry.md\n")
    (root / "skills" / "ashare-innovation-radar" / "SKILL.md").write_text(
        " ".join([
            "docs/continuous-evolution.md", "docs/plan-registry.md",
            "docs/retro-and-gaps.md", "docs/stages/", "docs/jev-integration.md"
        ])
    )
    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "DOCS", docs)

    errors = mod.check_decision_propagation()
    assert any("GOV-025" in error for error in errors)



@pytest.mark.parametrize(
    "rel,needle,expected",
    [
        ("skills/ashare-innovation-radar/SKILL.md", "外部内容一律是不可信数据", "外部内容不可信边界"),
        ("skills/ashare-innovation-radar/SKILL.md", "问题驱动", "问题驱动扫描"),
        ("skills/ashare-innovation-radar/SKILL.md", "review_due", "候选复核到期"),
        ("skills/ashare-innovation-radar/SKILL.md", "stop_rule", "实验停止条件"),
        ("docs/continuous-evolution.md", "外部内容信任边界", "外部内容信任边界"),
        ("docs/continuous-evolution.md", "反证驱动（counter-evidence-driven）", "反证驱动发现"),
    ],
)
def test_open_evolution_guard_catches_removed_core_invariant(monkeypatch, rel, needle, expected):
    mod = load()
    target = mod.ROOT / rel
    original_read = mod._read

    def patched_read(path):
        text = original_read(path)
        if path == target:
            assert needle in text
            return text.replace(needle, "")
        return text

    monkeypatch.setattr(mod, "_read", patched_read)
    errors = mod.check_decision_propagation()
    assert any(expected in error for error in errors)



def test_decision_propagation_detects_stale_handoff_requirement_range(monkeypatch):
    mod = load()
    target = mod.DOCS / "handoff.md"
    original_read = mod._read

    def patched_read(path):
        text = original_read(path)
        if path == target:
            assert "U01–U46" in text
            return text.replace("U01–U46", "U01–U45", 1)
        return text

    monkeypatch.setattr(mod, "_read", patched_read)
    errors = mod.check_decision_propagation()
    assert any("累计要求范围" in error and "U46" in error for error in errors)


def test_decision_propagation_rejects_ready_handoff_with_pending_merge_text(monkeypatch):
    mod = load()
    target = mod.DOCS / "handoff.md"
    original_read = mod._read

    def patched_read(path):
        text = original_read(path)
        if path == target:
            head, sep, tail = text.partition("\n## 1.")
            assert "READY_FOR_NEXT_PLANNED_SLICE" in head
            head += "\n仅在 PR #999 通过后生效；未合并前不得执行。"
            return head + sep + tail
        return text

    monkeypatch.setattr(mod, "_read", patched_read)
    errors = mod.check_decision_propagation()
    assert any("READY 头部仍含候选态文字" in error for error in errors)


def test_active_collaboration_entry_does_not_pin_retired_feature_branch():
    root = Path(__file__).resolve().parents[2]
    active_surfaces = [
        root / "AGENTS.md",
        root / "docs" / "INDEX.md",
        root / "docs" / "handoff.md",
        root / "docs" / "collaboration-workflow.md",
        root / "skills" / "ashare-ledger-continue" / "SKILL.md",
        root / "skills" / "ashare-task-handoff" / "SKILL.md",
    ]
    retired_markers = (
        "codex/collaboration-runtime-state",
        "当前模式：DESIGN_ONLY",
        "当前固定入口为codex/",
        "固定入口分支codex/",
    )
    offenders = {
        path.relative_to(root).as_posix(): marker
        for path in active_surfaces
        for marker in retired_markers
        if marker in path.read_text()
    }
    assert offenders == {}


@pytest.mark.parametrize("state", ["待执行", "部分完成", "待条件", "待交付", "进行中"])
def test_open_states_do_not_require_closed_record(probe, state):
    edit(probe, "stages/w00-phase.md", "待执行", state)
    assert probe.check_phase_index() == []
    assert probe.check_phase_tasks() == []


def test_closed_item_left_in_another_phase_is_flagged(probe):
    path = probe.DOCS / "stages/w01-phase.md"
    path.write_text(path.read_text() + task(status="已完成", evidence="PR #14 隔离验证通过"))
    assert any("重复任务" in e for e in probe.check_phase_tasks())


def test_duplicate_same_state_also_fails(probe):
    path = probe.DOCS / "stages/w00-phase.md"
    path.write_text(path.read_text() + task())
    assert any("重复任务" in e for e in probe.check_phase_tasks())


def test_completed_without_receipt_fails(probe):
    edit(probe, "stages/w00-phase.md", "待执行", "已完成")
    assert any("完成缺" in e for e in probe.check_phase_tasks())


def test_half_finished_writeoff_hits_both_directions(probe):
    path = probe.DOCS / "stages/w00-phase.md"
    path.write_text(path.read_text() + task(status="已完成"))
    errors = probe.check_phase_tasks()
    assert any("重复任务" in e for e in errors)
    assert any("完成缺" in e for e in errors)


def test_completed_with_receipt_passes(probe):
    edit(probe, "stages/w00-phase.md", "待执行", "已完成")
    edit(probe, "stages/w00-phase.md", "尚未实施", "PR #14 全量验收通过")
    assert probe.check_phase_tasks() == []


@pytest.mark.parametrize("disposition", ["退出", "已完成"])
def test_historical_rows_are_not_active_states(probe, disposition):
    edit(probe, probe.LEGACY_LEDGER, "已完成", disposition)
    assert probe.check_phase_tasks() == []
    assert probe.ledger_task_ids() == {"BUG-014", "GOV-014"}


@pytest.mark.parametrize("target", ["retro-and-gaps.md", "stages/w00-phase.md"])
def test_missing_carrier_is_fail_loud(probe, target):
    (probe.DOCS / target).unlink()
    assert probe.check_phase_index()


@pytest.mark.parametrize("old,new", [
    ("### 6.0 阶段索引", "### 6.0-HHH 阶段索引"),
    ("| W00 |", "| W01 |"),
    ("stages/w00-phase.md", "../stages/w00-phase.md"),
    ("stages/w00-phase.md", "stages/w01-phase.md"),
])
def test_index_identity_and_path_cannot_drift(probe, old, new):
    edit(probe, "retro-and-gaps.md", old, new)
    assert probe.check_phase_index()


def test_orphan_phase_is_flagged(probe):
    (probe.DOCS / "stages/extra.md").write_text("# Unindexed\n")
    assert any("未索引" in e for e in probe.check_phase_index())


def test_untracked_target_is_not_checkout_evidence(probe, monkeypatch):
    tracked = {p.relative_to(probe.ROOT).as_posix() for p in probe.DOCS.rglob("*.md")}
    tracked.remove((probe.DOCS / "stages/w00-phase.md").relative_to(probe.ROOT).as_posix())
    monkeypatch.setattr(probe, "_tracked_paths", lambda: tracked)
    assert any("非跟踪" in e for e in probe.check_phase_index())


def test_backlink_suffix_does_not_count(probe):
    edit(probe, "stages/w00-phase.md", "#60-阶段索引)", "#60-阶段索引-wrong)")
    assert any("缺总账反链" in e for e in probe.check_phase_index())


def test_empty_task_surface_is_fail_loud(probe):
    edit(probe, "stages/w00-phase.md", task(), "")
    assert any("任务为空" in e for e in probe.check_phase_tasks())


@pytest.mark.parametrize("old,new", [
    ("## BUG-014", "## BUG-014-X"),
    ("**状态**：待执行", "**状态**：已上线"),
    ("**优先级**：P0", "**优先级**：P3"),
    ("**验收**：在隔离目标库断言表与字段", "**验收**："),
    ("**依赖**：无", "**依赖**：GOV-014"),
    ("**依赖**：无", "**依赖**：BUG-014"),
])
def test_invalid_task_contract_is_flagged(probe, old, new):
    edit(probe, "stages/w00-phase.md", old, new)
    assert probe.check_phase_tasks()


def test_duplicate_field_is_not_last_write_wins(probe):
    edit(probe, "stages/w00-phase.md", "- **状态**：待执行", "- **状态**：已完成\n- **状态**：待执行")
    assert any("重复字段" in e for e in probe.check_phase_tasks())


def test_multi_task_dependency_cycle_is_flagged(probe):
    path = probe.DOCS / "stages/w00-phase.md"
    path.write_text(path.read_text().replace("**依赖**：无", "**依赖**：IMP-001") + task("IMP-001", deps="BUG-014"))
    assert any("依赖成环" in e for e in probe.check_phase_tasks())


def test_pending_condition_must_explain_condition(probe):
    edit(probe, "stages/w00-phase.md", "待执行", "待条件")
    edit(probe, "stages/w00-phase.md", "条件为隔离环境可用后实施", "继续")
    assert any("未交代条件" in e for e in probe.check_phase_tasks())


@pytest.mark.parametrize("target", [
    "[BUG-999](../stages/w00-phase.md#bug-999)",
    "[BUG-014](../stages/w01-phase.md#bug-014)",
    "[BUG-014](../stages/w00-phase.md#bug-014-x)",
])
def test_historical_merge_destination_must_resolve(probe, target):
    edit(probe, probe.LEGACY_LEDGER, "已完成 | Git 原文", f"合并 | {target}")
    assert any("去向不可达" in e for e in probe.check_phase_tasks())


def test_valid_merge_keeps_old_reference_without_opening_task(probe):
    edit(probe, probe.LEGACY_LEDGER, "已完成 | Git 原文", "合并 | [BUG-014](../stages/w00-phase.md#bug-014)")
    assert probe.check_phase_tasks() == []
    assert "GOV-014" in probe.ledger_task_ids()


def test_retired_id_cannot_be_reused(probe):
    path = probe.DOCS / "stages/w00-phase.md"
    path.write_text(path.read_text() + task("GOV-014"))
    assert any("旧号被重复启用" in e for e in probe.check_phase_tasks())


def test_prose_id_does_not_define_task(probe):
    path = probe.DOCS / "stages/w00-phase.md"
    path.write_text(path.read_text() + "\n讨论 BUG-999\n")
    assert "BUG-999" not in probe.ledger_task_ids()


def test_handoff_cannot_restore_second_state_table(probe):
    (probe.DOCS / "handoff.md").write_text(task())
    assert any("handoff" in e for e in probe.check_phase_tasks())


def test_missing_history_is_fail_loud(probe):
    (probe.DOCS / probe.LEGACY_LEDGER).unlink()
    assert any("历史编号处置表缺失" in e for e in probe.check_phase_tasks())


@pytest.mark.parametrize("replacement", [
    "",
    "| GOV-999 | 已完成 | Git 原文 | 替换了旧号但行数不变 |\n",
    "| GOV-014-X | 已完成 | Git 原文 | 旧号被改残 |\n",
])
def test_legacy_migration_cannot_silently_lose_or_replace_source_id(probe, replacement):
    (probe.DOCS / probe.LEGACY_LEDGER).write_text(replacement)
    assert any("历史编号集合" in e for e in probe.check_phase_tasks())


def test_legacy_migration_cannot_invent_source_ids(probe):
    p = probe.DOCS / probe.LEGACY_LEDGER
    p.write_text(p.read_text() + "| GOV-999 | 退出 | — | 新号冒充旧来源 |\n")
    assert any("历史编号集合" in e for e in probe.check_phase_tasks())


@pytest.mark.parametrize("missing", [True, False])
def test_handoff_missing_or_empty_is_not_a_successful_migration(probe, missing):
    p = probe.DOCS / "handoff.md"
    if missing:
        p.unlink()
    else:
        p.write_text("  \n")
    assert any("交接现场缺失/为空" in e for e in probe.check_phase_tasks())


def test_real_legacy_source_manifest_is_fixed_and_fully_accounted_for():
    mod = load()
    # 从 84434a4 旧 A–F 首列独立提取后冻结，不能从被检验的新表生成分母。
    expected = {
        f"{prefix}-{n:03}"
        for prefix, stop in (("BUG", 24), ("GOV", 19), ("IMP", 44), ("OPS", 7), ("RSH", 29))
        for n in range(1, stop + 1)
    }
    assert len(expected) == 123
    assert mod.LEGACY_SOURCE_IDS == expected
    assert {row[0] for row in mod.legacy_rows()} == expected


@pytest.mark.parametrize("state", ["已退出", "已合并"])
def test_retirement_requires_reason_instead_of_faking_completion(probe, state):
    edit(probe, "stages/w00-phase.md", "待执行", state)
    assert any("缺处置依据" in e for e in probe.check_phase_tasks())


def test_retired_task_keeps_id_without_becoming_completed(probe):
    p = probe.DOCS / "stages/w00-phase.md"
    p.write_text(p.read_text().replace("待执行", "已退出") + "- **处置依据**：现有机制已覆盖，无增量收益。\n")
    assert probe.check_phase_tasks() == []
    assert "BUG-014" in probe.ledger_task_ids()


def test_merged_task_has_one_reachable_successor(probe):
    p = probe.DOCS / "stages/w00-phase.md"
    p.write_text(p.read_text().replace("待执行", "已合并") +
                 "- **处置依据**：同一消费者，避免重复实现。\n- **合并至**：IMP-001\n\n" + task("IMP-001"))
    assert probe.check_phase_tasks() == []


@pytest.mark.parametrize("target", ["", "BUG-014", "IMP-999"])
def test_merged_task_cannot_have_missing_self_or_unknown_target(probe, target):
    p = probe.DOCS / "stages/w00-phase.md"
    p.write_text(p.read_text().replace("待执行", "已合并") +
                 f"- **处置依据**：同一消费者。\n- **合并至**：{target}\n")
    assert any("合并去向无效" in e for e in probe.check_phase_tasks())


def test_open_task_cannot_keep_dependency_on_retired_task(probe):
    p = probe.DOCS / "stages/w00-phase.md"
    p.write_text(p.read_text().replace("待执行", "已退出") +
                 "- **处置依据**：已无独立用途。\n\n" + task("IMP-001", deps="BUG-014"))
    assert any("依赖已退出/合并" in e for e in probe.check_phase_tasks())


def test_merge_cannot_form_retirement_chain_or_cycle(probe):
    p = probe.DOCS / "stages/w00-phase.md"
    p.write_text(p.read_text().replace("待执行", "已合并") +
                 "- **处置依据**：同一消费者。\n- **合并至**：IMP-001\n\n" +
                 task("IMP-001", status="已合并") +
                 "- **处置依据**：错误回指。\n- **合并至**：BUG-014\n")
    assert any("合并去向无效" in e for e in probe.check_phase_tasks())


def test_stage_gate_rejects_higher_gate_hard_dependency(probe):
    p = probe.DOCS / "stages/w00-phase.md"
    p.write_text(p.read_text() + task("IMP-001", gate="G1", order=20, role="阻断"))
    edit(probe, "stages/w00-phase.md", "- **依赖**：无", "- **依赖**：IMP-001")
    errors = probe.check_stage_gates()
    assert any("更高阶段门" in error for error in errors)


def test_stage_gate_rejects_duplicate_gate_order(probe):
    p = probe.DOCS / "stages/w00-phase.md"
    p.write_text(p.read_text() + task("IMP-001", gate="G0", order=10, role="非阻断"))
    errors = probe.check_stage_gates()
    assert any("门内序 10" in error and "重复" in error for error in errors)


def test_stage_gate_rejects_hard_dependency_on_continuous_governance(probe):
    p = probe.DOCS / "stages/w00-phase.md"
    p.write_text(p.read_text() + task("GOV-001", gate="GX", order=20, role="持续治理"))
    edit(probe, "stages/w00-phase.md", "- **依赖**：无", "- **依赖**：GOV-001")
    errors = probe.check_stage_gates()
    assert any("硬依赖不得指向持续治理任务 GOV-001" in error for error in errors)


def test_stage_gate_rejects_missing_effect_prerequisite(probe):
    edit(probe, "stages/w00-phase.md", "- **效果前置**：无", "- **效果前置**：RSH-999")
    errors = probe.check_stage_gates()
    assert any("效果前置不存在" in error for error in errors)


def test_stage_gate_requires_g5_acceptance_role(probe):
    edit(probe, "stages/w00-phase.md", "- **阶段门**：G0", "- **阶段门**：G5")
    errors = probe.check_stage_gates()
    assert any("G5 只能使用验收角色" in error for error in errors)


def test_stage_gate_derives_lowest_actionable_blocker(probe):
    entries, _ = probe.phase_tasks()
    tasks = {tid: (rel, fields) for tid, rel, fields in entries}
    gate, selected, candidates = probe.derive_current_stage_selection(tasks)
    assert gate == "G0"
    assert selected == "BUG-014"
    assert candidates == ["BUG-014"]


def test_stage_gate_does_not_skip_lower_gate_for_higher_blocker(probe):
    p = probe.DOCS / "stages/w00-phase.md"
    p.write_text(p.read_text() + task("IMP-001", gate="G1", order=20, role="阻断"))
    entries, _ = probe.phase_tasks()
    tasks = {tid: (rel, fields) for tid, rel, fields in entries}
    gate, selected, _ = probe.derive_current_stage_selection(tasks)
    assert (gate, selected) == ("G0", "BUG-014")


def test_stage_gate_advances_after_lower_blocker_completed(probe):
    edit(probe, "stages/w00-phase.md", "- **状态**：待执行", "- **状态**：已完成")
    edit(probe, "stages/w00-phase.md", "- **证据**：尚未实施", "- **证据**：PR #1")
    p = probe.DOCS / "stages/w00-phase.md"
    p.write_text(p.read_text() + task("IMP-001", gate="G1", order=20, role="阻断"))
    entries, _ = probe.phase_tasks()
    tasks = {tid: (rel, fields) for tid, rel, fields in entries}
    gate, selected, _ = probe.derive_current_stage_selection(tasks)
    assert (gate, selected) == ("G1", "IMP-001")


def test_stage_gate_rejects_handoff_selection_mismatch(probe):
    handoff = probe.DOCS / "handoff.md"
    handoff.write_text(handoff.read_text().replace("BUG-014", "IMP-999"))
    errors = probe.check_stage_gates()
    assert any("主切片首选" in error and "账本推导" in error for error in errors)
