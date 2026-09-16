"""模型名单一来源 + 失败可诊断守卫（2026-09-16 事故固化）。

**事故链条（全部实测，非推理）**：
1. 用户已把本机 claude 默认模型切到 `deepseek-v4-flash`（`~/.claude/settings.json`
   的 `ANTHROPIC_MODEL` 等；实测该名字 3.3s 正常返回）；
2. 但后端 `backend/.env` 仍写 `ASHARE_REVIEW_LLM_MODEL=glm-5.3`，代码把配置值
   原样透传 `claude -p --model glm-5.3`（**覆盖** CLI 侧默认）；
3. `glm-5.3` 上游已下架 ⇒ 调用**挂起不返回**（实测 150s 无输出、直到被 SIGTERM）
   ⇒ 15:45 进化议程 failed、告警判读降级 `llm_fallback`；
4. 而报错文本只有裸「claude_cli 调用超时」——模型名藏在被截断的命令行里
   ⇒ 只能人肉翻日志定位。

**三条防复发判据**（本文件即是它们的唯一载体）：
- A 模型名**单一来源**：默认值非空，且 `app/**` 不得再出现任何模型名字面量
  （否则同一类"项目配的 ≠ 实际用的"会换个文件重演）；
- B 失败**可诊断**：claude_cli 的超时 / 退出码错误消息必须带 `model=`；
- C `unrecognized_model` 警告与成败**正交**：带该警告仍须正常返回（不得当失败），
  但必须留一条日志（不得当纯噪音静默吞掉）——历史文档曾把它单方面写成
  "无害警告"，而 2026-09-04 那次它伴随退出码 1、LLM 通道全天不可用。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from app.core import llm_client
from app.core.config import Settings
from app.core.llm_client import LLMError, LLMFailure, chat_completion

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# 唯一允许出现模型名的文件：默认值定义处（单一来源的"那一处"）。
# 新增合法例外必须在此具名登记理由，**不得放开成目录级豁免**。
_MODEL_NAME_ALLOWED = {
    "app/core/config.py": "settings.review_llm_model 默认值 —— 全仓唯一写死点",
}

# 模型名字面量：家族前缀 + 版本号（`glm-5.3` / `deepseek-v4-flash` / `claude-3-5-sonnet`）。
# ⚠️ 刻意要求"版本号"：裸 `"claude"` 是可执行文件名（`shutil.which("claude")`、
# `.nvm/versions/node/*/bin/claude`），**不是**模型名，误伤它会让守卫变成噪音。
_MODEL_LITERAL = re.compile(
    r"""["']((?:glm|deepseek|qwen|kimi|moonshot|doubao|gemini|gpt|claude)-[a-z]*\d[\w.\-]*)["']"""
)


def _iter_app_sources() -> list[tuple[str, str]]:
    repo_root = APP_DIR.parent
    out: list[tuple[str, str]] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        out.append((rel, path.read_text(encoding="utf-8")))
    return out


# ---------------- A. 单一来源 ----------------

def test_review_llm_model_default_is_not_empty():
    """默认值不得为空串。

    空串时 `--model ""` 会由 CLI 侧默认兜底，于是"项目配的模型"与"实际用的模型"
    不一致，出问题只能靠人肉分辨——这正是 2026-09-16 事故的成因。
    """
    default = Settings.model_fields["review_llm_model"].default
    assert isinstance(default, str) and default.strip(), (
        "review_llm_model 默认值不得为空：写死一个可用默认值 = 少一层'两边不一致'的失败面"
    )


def test_model_name_appears_in_exactly_one_place():
    """`app/**` 里模型名字面量只允许出现在登记过的那一个文件。"""
    offenders: list[str] = []
    for rel, text in _iter_app_sources():
        if rel in _MODEL_NAME_ALLOWED:
            continue
        for m in _MODEL_LITERAL.finditer(text):
            offenders.append(f"{rel}: {m.group(1)}")
    assert not offenders, (
        "模型名不得写进 app/ 代码（应读 settings.review_llm_model）：\n  "
        + "\n  ".join(offenders)
    )


def test_model_name_allowlist_entries_still_hold_a_literal():
    """反向核对：豁免条目本身若已不含模型名字面量，说明豁免过期了。

    没有这条，豁免会变成"永久免检名单"——文件早已改名/删掉而无人发现
    （同族：豁免只增不减 = 判据静默退化）。
    """
    stale: list[str] = []
    for rel, reason in _MODEL_NAME_ALLOWED.items():
        path = APP_DIR.parent / rel
        if not path.is_file() or not _MODEL_LITERAL.search(path.read_text(encoding="utf-8")):
            stale.append(f"{rel}（理由：{reason}）")
    assert not stale, "以下模型名豁免已失效，应删除或更新理由：\n  " + "\n  ".join(stale)


# ---------------- B. 失败可诊断 ----------------

class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _stub_cli(monkeypatch, proc) -> None:
    monkeypatch.setattr(llm_client.subprocess, "run", lambda cmd, **kw: proc)
    monkeypatch.setattr(llm_client, "resolve_cli_path", lambda p="": p or "/fake/claude")


def test_timeout_error_carries_model_name(monkeypatch):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))

    monkeypatch.setattr(llm_client.subprocess, "run", boom)
    monkeypatch.setattr(llm_client, "resolve_cli_path", lambda p="": p or "/fake/claude")

    with pytest.raises(LLMError) as ei:
        chat_completion(
            "", "", "some-model-v9",
            [{"role": "user", "content": "x"}], provider="claude_cli",
        )
    msg = str(ei.value)
    assert ei.value.kind == LLMFailure.TIMEOUT
    assert "some-model-v9" in msg, "超时信息必须自带 model —— 否则又只能人肉翻日志"
    assert "ASHARE_REVIEW_LLM_MODEL" in msg, "应把排查入口直接写进提示"


def test_nonzero_exit_error_carries_model_name(monkeypatch):
    _stub_cli(monkeypatch, _Proc(returncode=1, stderr="boom"))
    with pytest.raises(LLMError) as ei:
        chat_completion(
            "", "", "some-model-v9",
            [{"role": "user", "content": "x"}], provider="claude_cli",
        )
    assert "some-model-v9" in str(ei.value)


# ---------------- C. unrecognized_model 警告与成败正交 ----------------

_UNRECOGNIZED_STDERR = '[claude-code:unrecognized_model] {"model":"m-1","query_source":"sdk"}'


def test_unrecognized_model_warning_does_not_fail_the_call(monkeypatch, caplog):
    """实测：`deepseek-v4-flash` 带此警告仍 3.3s 正常返回 ⇒ 警告不是失败信号。"""
    stdout = json.dumps({"result": "ok", "is_error": False})
    _stub_cli(monkeypatch, _Proc(0, stdout, _UNRECOGNIZED_STDERR))

    with caplog.at_level("WARNING", logger="app.core.llm_client"):
        out = chat_completion(
            "", "", "m-1",
            [{"role": "user", "content": "x"}], provider="claude_cli",
        )
    assert out == "ok"
    assert any("未识别模型名" in r.getMessage() for r in caplog.records), (
        "警告必须留痕——它说明名字被原样透传给上游，可用性取决于上游是否仍提供该模型"
    )


def test_no_warning_logged_when_stderr_is_clean(monkeypatch, caplog):
    """反向对照：干净 stderr 不得凭空打警告（防"恒真"式判据）。"""
    stdout = json.dumps({"result": "ok", "is_error": False})
    _stub_cli(monkeypatch, _Proc(0, stdout, ""))

    with caplog.at_level("WARNING", logger="app.core.llm_client"):
        out = chat_completion(
            "", "", "m-1",
            [{"role": "user", "content": "x"}], provider="claude_cli",
        )
    assert out == "ok"
    assert not [r for r in caplog.records if "未识别模型名" in r.getMessage()]
