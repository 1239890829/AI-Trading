"""Bounded existing-model calls do not expose the service environment or cwd.

The executable fixture is a local Python program, not a model or network client.
It reports only synthetic arguments and environment names used by these tests.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.core import llm_client as lc


MESSAGES = [{"role": "system", "content": "Use only this synthetic public packet."},
            {"role": "user", "content": "synthetic company source"}]


def _bounded(**options):
    return lc.bounded_chat_completion("", "", "fixture-model", MESSAGES,
                                      provider="claude_cli", **options)


def _executable(tmp_path):
    path = tmp_path / "fixture-cli"
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "result = {'cwd': str(Path.cwd()), 'argv': sys.argv[1:],"
        " 'environment_names': sorted(os.environ),"
        " 'model_auth': os.environ.get('ANTHROPIC_AUTH_TOKEN'),"
        " 'home': os.environ.get('HOME')}\n"
        "print(json.dumps({'result': json.dumps(result), 'is_error': False,"
        " 'usage': {'input_tokens': 123, 'output_tokens': 45}}))\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def test_real_child_changes_only_its_cwd_and_excludes_service_credentials(tmp_path, monkeypatch):
    cli = _executable(tmp_path)
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fixture-model-auth")
    forbidden = {"ASHARE_API_TOKEN", "ASHARE_REVIEW_LLM_API_KEY", "PYTHONPATH",
                 "OPENAI_API_KEY", "HF_TOKEN", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY"}
    for name in forbidden:
        monkeypatch.setenv(name, "fixture-service-secret")
    original_cwd = Path.cwd()
    original_env = dict(os.environ)
    observations, usage = [], []
    monkeypatch.setattr(lc, "_record_cli_health", lambda *args, **kwargs: observations.append((args, kwargs)))

    def parent_must_not_chdir(*_args):
        pytest.fail("bounded transport changed the parent process cwd")

    monkeypatch.setattr(lc.os, "chdir", parent_must_not_chdir)
    result = json.loads(_bounded(cli_path=str(cli), usage_callback=usage.append))
    assert Path.cwd() == original_cwd and dict(os.environ) == original_env
    assert Path(result["cwd"]) != original_cwd
    assert not Path(result["cwd"]).exists()  # The adapter and its temporary cwd were removed.
    assert result["model_auth"] == "fixture-model-auth" and result["home"] == str(home)
    assert not forbidden.intersection(result["environment_names"])
    assert not any(name.startswith("ASHARE_") for name in result["environment_names"])
    args = result["argv"]
    assert args[args.index("-p") + 1] == MESSAGES[1]["content"]
    assert args[args.index("--system-prompt") + 1] == MESSAGES[0]["content"]
    assert args[args.index("--model") + 1] == "fixture-model"
    assert args[args.index("--tools") + 1] == ""
    assert "--no-session-persistence" in args
    assert args[args.index("--setting-sources") + 1] == "user"
    assert json.loads(args[args.index("--settings") + 1]) == {"disableAllHooks": True}
    assert "--strict-mcp-config" in args and "--disable-slash-commands" in args
    assert json.loads(args[args.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert args[args.index("--max-turns") + 1] == "1"
    assert usage == [{"input_tokens": 123, "output_tokens": 45}]
    assert observations == []


@pytest.mark.parametrize("raw_usage,expected", [
    ({"input_tokens": 7, "output_tokens": 9}, {"input_tokens": 7, "output_tokens": 9}),
    ({"prompt_tokens": 7, "completion_tokens": 9}, {"input_tokens": 7, "output_tokens": 9}),
    ({"input_tokens": 7}, None),
    (None, None),
])
def test_bounded_cli_preserves_usage_without_guessing_unknown_tokens(tmp_path, monkeypatch, raw_usage, expected):
    cli = _executable(tmp_path)
    calls, usage = [], []

    def run(cmd, **options):
        calls.append((cmd, options))
        assert Path(cmd[0]).is_file()
        assert options["timeout"] == 37
        assert "PYTHONPATH" not in options["env"]
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
            "result": "fixture reply", "usage": raw_usage,
        }))

    monkeypatch.setattr(lc.subprocess, "run", run)
    assert _bounded(cli_path=str(cli), timeout=37, usage_callback=usage.append) == "fixture reply"
    assert len(calls) == 1 and usage == [expected]
    assert not Path(calls[0][0][0]).exists()


@pytest.mark.parametrize("failure,kind", [
    ("exit", lc.LLMFailure.QUOTA),
    ("body_error", lc.LLMFailure.GATEWAY_ERROR),
    ("bad_json", lc.LLMFailure.BAD_RESPONSE),
    ("bad_shape", lc.LLMFailure.BAD_RESPONSE),
    ("empty", lc.LLMFailure.EMPTY),
    ("timeout", lc.LLMFailure.TIMEOUT),
    ("os_error", lc.LLMFailure.UNAVAILABLE),
])
def test_bounded_failure_keeps_classification_without_leaking_or_retrying(tmp_path, monkeypatch, failure, kind):
    cli = _executable(tmp_path)
    calls, usage = [], []
    sensitive = "fixture-sensitive-prompt-and-token"

    def run(cmd, **options):
        calls.append(cmd)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd + [sensitive], options["timeout"], output=sensitive)
        if failure == "os_error":
            raise OSError(sensitive)
        if failure == "exit":
            return SimpleNamespace(returncode=1, stdout=sensitive, stderr="need quota " + sensitive)
        body = {"result": sensitive, "is_error": True} if failure == "body_error" else {"result": " "}
        stdout = sensitive if failure == "bad_json" else "[]" if failure == "bad_shape" else json.dumps(body)
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(lc.subprocess, "run", run)
    with pytest.raises(lc.LLMError) as error:
        _bounded(cli_path=str(cli), usage_callback=usage.append)
    assert error.value.kind is kind
    assert sensitive not in str(error.value)
    assert MESSAGES[1]["content"] not in str(error.value)
    assert error.value.__suppress_context__ is True
    assert len(calls) == 1 and usage == []  # Failed/unmetered output remains unknown, never zero.
    assert not Path(calls[0][0]).exists()


def test_unavailable_cli_stops_without_an_external_attempt(monkeypatch):
    monkeypatch.setattr(lc, "resolve_cli_path", lambda _path="": None)

    def unexpected_call(*_args, **_kwargs):
        pytest.fail("missing configured CLI must not launch a process")

    monkeypatch.setattr(lc.subprocess, "run", unexpected_call)
    with pytest.raises(lc.LLMError) as error:
        _bounded()
    assert error.value.kind is lc.LLMFailure.UNAVAILABLE


def test_non_cli_reuses_existing_transport_and_exact_usage_callback(monkeypatch):
    calls, usage = [], []

    def existing(*args, **kwargs):
        calls.append((args, kwargs))
        kwargs["usage_callback"]({"input_tokens": 1, "output_tokens": 2})
        return "http fixture"

    monkeypatch.setattr(lc, "chat_completion", existing)
    callback = usage.append
    result = lc.bounded_chat_completion("https://fixture.invalid/v1", "fixture-key", "same-model", MESSAGES,
                                        provider="openai", timeout=19, usage_callback=callback)
    assert result == "http fixture" and usage == [{"input_tokens": 1, "output_tokens": 2}]
    assert calls == [(("https://fixture.invalid/v1", "fixture-key", "same-model", MESSAGES),
                      {"provider": "openai", "cli_path": "", "timeout": 19, "usage_callback": callback})]


def test_non_cli_upstream_error_is_redacted_with_original_failure_kind(monkeypatch):
    def failed(*_args, **_kwargs):
        raise lc.LLMError("upstream echoed fixture-token and synthetic company source", lc.LLMFailure.GATEWAY_ERROR)

    monkeypatch.setattr(lc, "chat_completion", failed)
    with pytest.raises(lc.LLMError) as error:
        lc.bounded_chat_completion("https://fixture.invalid", "fixture-token", "same-model", MESSAGES,
                                   provider="openai")
    assert error.value.kind is lc.LLMFailure.GATEWAY_ERROR
    assert "fixture-token" not in str(error.value) and "synthetic company source" not in str(error.value)


def test_ordinary_cli_calls_keep_their_original_environment_flags_and_health(monkeypatch):
    calls, health = [], []
    monkeypatch.setattr(lc, "resolve_cli_path", lambda path="": path or "/fixture/cli")
    monkeypatch.setattr(lc, "_record_cli_health", lambda *args, **kwargs: health.append((args, kwargs)))

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stderr="", stdout='{"result":"unchanged"}')

    monkeypatch.setattr(lc.subprocess, "run", run)
    assert lc.chat_completion("", "", "old-model", MESSAGES, provider="claude_cli") == "unchanged"
    assert len(calls) == 1 and "env" not in calls[0][1]
    assert "--setting-sources" not in calls[0][0] and "--strict-mcp-config" not in calls[0][0]
    assert "--no-session-persistence" in calls[0][0]
    assert health == [(("old-model", ""), {"ok": True})]
