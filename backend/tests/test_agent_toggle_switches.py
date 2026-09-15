"""自治开关的**配置解析语义**（审查 R10，2026-09-14）。

为什么值得一个测试文件：R10 的根因不是"少写了一行"，而是**文档变量名与配置字段对不上**——
`env_prefix="ASHARE_"` 使字段 `agent_autonomy_enabled` 对应
`ASHARE_AGENT_AUTONOMY_ENABLED`，而代码注释 / `docs/summary/ai-evolution.md` /
`docs/kb/04` 长期教人写少 `_ENABLED` 的短名。按该说明设置**完全不生效**：
operator 以为已停机，实际自治仍在跑（审查报告「配置复现」已复现）。

本文件钉三件事（对应 R10 验收标准的"禁读 dotenv 的配置解析测试"）：
1. 正式名生效；
2. 短名**仍生效**（不让既有部署静默失效）且必告警；
3. 两者冲突时**正式名优先**——这是 fail-closed 的关键：若短名能覆盖，
   一个遗留在 shell 里的陈旧 `…_AUTONOMY=1` 就能把已关闭的能力重新打开。

⚠️ 一律 `Settings(_env_file=None)`：只依赖进程环境（monkeypatch），
不复用仓库 `.env`——否则本机 `backend/.env` 的真实值会决定结论。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.core.config import Settings

OFFICIAL_AUTONOMY = "ASHARE_AGENT_AUTONOMY_ENABLED"
OFFICIAL_CODE = "ASHARE_AGENT_CODE_CHANGE_ENABLED"
LEGACY_AUTONOMY = "ASHARE_AGENT_AUTONOMY"
LEGACY_CODE = "ASHARE_AGENT_CODE_CHANGE"

_ALL = (OFFICIAL_AUTONOMY, OFFICIAL_CODE, LEGACY_AUTONOMY, LEGACY_CODE)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """先清掉四个变量，避免宿主环境把结论带偏（含 .env 之外的 shell 导出）。"""
    for name in _ALL:
        monkeypatch.delenv(name, raising=False)


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_restricted_autonomy_defaults_on_but_code_changes_default_off(monkeypatch):
    defaults = _settings()
    assert defaults.agent_autonomy_enabled is True
    assert defaults.agent_code_change_enabled is False

    monkeypatch.setenv(OFFICIAL_AUTONOMY, "0")
    monkeypatch.setenv(OFFICIAL_CODE, "1")
    s = _settings()
    assert s.agent_autonomy_enabled is False
    assert s.agent_code_change_enabled is True


def test_legacy_short_name_controls_both_toggles_and_warns(monkeypatch, caplog):
    """短名照旧生效（兼容），但必须告警——静默兼容等于把坑留在原地。"""
    monkeypatch.setenv(LEGACY_AUTONOMY, "1")
    monkeypatch.setenv(LEGACY_CODE, "1")
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        s = _settings()
    assert s.agent_autonomy_enabled is True
    assert s.agent_code_change_enabled is True
    text = caplog.text
    assert LEGACY_AUTONOMY in text and OFFICIAL_AUTONOMY in text
    assert LEGACY_CODE in text and OFFICIAL_CODE in text
    assert "废弃" in text


def test_official_name_wins_when_conflicting_with_legacy(monkeypatch, caplog):
    """fail-closed：正式名说停机，陈旧短名说开机 → 必须停机，且明确告警。

    若此断言反转（短名覆盖正式名），则"已停机"的系统会被一个环境变量残留重新唤醒。
    """
    monkeypatch.setenv(OFFICIAL_AUTONOMY, "0")
    monkeypatch.setenv(LEGACY_AUTONOMY, "1")
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        s = _settings()
    assert s.agent_autonomy_enabled is False, "正式名必须优先于已废弃短名"
    assert "冲突" in caplog.text
    assert OFFICIAL_AUTONOMY in caplog.text


def test_agreeing_values_still_get_deprecation_notice(monkeypatch, caplog):
    """取值一致不报"冲突"，但废弃提示照发——否则短名会被永久沿用。"""
    monkeypatch.setenv(OFFICIAL_AUTONOMY, "0")
    monkeypatch.setenv(LEGACY_AUTONOMY, "0")
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        s = _settings()
    assert s.agent_autonomy_enabled is False
    assert "冲突" not in caplog.text
    assert "已废弃" in caplog.text


def test_operator_manual_names_official_toggles_only():
    """面向操作者的手册只能出现正式变量名（短名只在兼容层与历史记录里出现）。

    R10 的复发形态就是"手册教错名字" ⇒ 把手册本身纳进守卫面。
    `docs/kb/` 属历史记录（勘误可提及旧名），不在本守卫范围内。
    """
    manual = (REPO_ROOT / "docs" / "summary" / "ai-evolution.md").read_text(encoding="utf-8")
    assert OFFICIAL_AUTONOMY in manual
    assert OFFICIAL_CODE in manual
    for legacy in (LEGACY_AUTONOMY, LEGACY_CODE):
        # 短名 = 正式名去掉 _ENABLED 后缀；用"正式名 + 紧邻非 _ENABLED 字符"来判定
        idx = 0
        while (idx := manual.find(legacy, idx)) != -1:
            tail = manual[idx + len(legacy):][:8]
            assert tail.startswith("_ENABLED"), (
                f"手册出现少 _ENABLED 的短名：…{manual[max(0, idx - 20):idx + 40]}…"
            )
            idx += len(legacy)
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert f"{OFFICIAL_AUTONOMY}=" in env_example
    assert f"{OFFICIAL_CODE}=" in env_example
