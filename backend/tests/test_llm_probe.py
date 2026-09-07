"""LLM 网关健康探针（2026-09-06）。

变异验证纪律：这里的断言要能抓住两类回归——
① 分类被写死成 GATEWAY_ERROR（额度与网关故障又混为一谈）；
② 失败也走长缓存（故障被 TTL 掩盖，health 永远显示上一次的 ok）。
改分类逻辑或 TTL 语义时这些用例必须见红，否则等于没写。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import health as health_route
from app.core import llm_client as lc
from app.core.config import settings
from app.services import llm_probe as probe_mod
from app.services.llm_probe import LlmProbe, probe_loop


# ---------------------------------------------------------------- ① 失败分类


@pytest.mark.parametrize(
    "text",
    [
        "403 need quota 0.098258",
        "token quota is not enough",
        "insufficient balance",
        "余额不足",
        "额度不足，请充值",
    ],
)
def test_classify_text_quota(text):
    assert lc.classify_text_failure(text) == lc.LLMFailure.QUOTA


def test_classify_text_non_quota_falls_back_to_gateway():
    assert lc.classify_text_failure("internal server error") == lc.LLMFailure.GATEWAY_ERROR
    # 网关 WAF 的鉴权失败不是额度问题——不能因为文本里含 401/403 就判成"该充值"
    assert lc.classify_text_failure("401 unauthorized client detected") == lc.LLMFailure.GATEWAY_ERROR


def test_classify_cli_reads_stdout_not_only_stderr():
    """CLI 把网关错误塞进 stdout 的 JSON result 字段——只看 stderr 会漏判成网关故障。"""
    stdout = '{"is_error":true,"result":"token quota is not enough"}'
    assert lc.classify_cli_failure(1, stdout, "some warning") == lc.LLMFailure.QUOTA


def test_classify_http_status():
    assert lc.classify_http_failure(402) == lc.LLMFailure.QUOTA
    assert lc.classify_http_failure(429) == lc.LLMFailure.QUOTA
    assert lc.classify_http_failure(500) == lc.LLMFailure.GATEWAY_ERROR
    # body 里的额度文案优先于状态码归类
    assert lc.classify_http_failure(500, "need quota") == lc.LLMFailure.QUOTA


def test_llm_error_kind():
    assert lc.LLMError("boom").kind == lc.LLMFailure.GATEWAY_ERROR
    assert lc.LLMError("x", lc.LLMFailure.QUOTA).kind == lc.LLMFailure.QUOTA


# ---------------------------------------------------------------- ② 探针状态机


def _enable(monkeypatch, *, model="glm-5.3", provider="claude_cli"):
    monkeypatch.setattr(settings, "review_llm_model", model)
    monkeypatch.setattr(settings, "news_llm_model", "")
    monkeypatch.setattr(settings, "llm_provider", provider)


def test_probe_ok(monkeypatch):
    _enable(monkeypatch)
    p = LlmProbe()
    snap = asyncio.run(p.probe_once(runner=lambda m, c, t: "OK"))
    assert snap["state"] == "ok"
    assert snap["model"] == "glm-5.3"
    assert snap["last_ok_at"] is not None
    assert snap["consecutive_failures"] == 0
    assert snap["last_failure_kind"] is None
    assert snap["latency_ms"] >= 0


def test_probe_quota_failure(monkeypatch):
    """额度不足必须落到 kind=quota 且带人话提示——这是整个模块的存在的理由。"""

    def runner(m, c, t):
        raise lc.LLMError("claude_cli 返回错误：token quota is not enough", lc.LLMFailure.QUOTA)

    _enable(monkeypatch)
    p = LlmProbe()
    snap = asyncio.run(p.probe_once(runner=runner))
    assert snap["state"] == "failed"
    assert snap["last_failure_kind"] == "quota"
    assert "充值" in (snap["last_failure_hint"] or "")
    assert snap["consecutive_failures"] == 1
    assert snap["last_ok_at"] is None


def test_probe_gateway_failure_differs_from_quota(monkeypatch):
    """同为失败，gateway_error 的提示里不能出现"充值"——否则又误导用户去充钱。"""

    def runner(m, c, t):
        raise lc.LLMError("claude_cli 退出码 1：api_error", lc.LLMFailure.GATEWAY_ERROR)

    _enable(monkeypatch)
    p = LlmProbe()
    snap = asyncio.run(p.probe_once(runner=runner))
    assert snap["last_failure_kind"] == "gateway_error"
    assert "充值" not in (snap["last_failure_hint"] or "")


def test_probe_unknown_exception_becomes_gateway(monkeypatch):
    def runner(m, c, t):
        raise RuntimeError("kaboom")

    _enable(monkeypatch)
    p = LlmProbe()
    snap = asyncio.run(p.probe_once(runner=runner))
    assert snap["state"] == "failed"
    assert snap["last_failure_kind"] == "gateway_error"
    assert "RuntimeError" in (snap["last_error"] or "")


def test_probe_empty_reply_is_failure(monkeypatch):
    _enable(monkeypatch)
    p = LlmProbe()
    snap = asyncio.run(p.probe_once(runner=lambda m, c, t: "   "))
    assert snap["state"] == "failed"
    assert snap["last_failure_kind"] == "empty"


def test_probe_disabled_without_model(monkeypatch):
    """没配模型就绝不发请求——探针不能自己制造额度消耗。"""

    def runner(m, c, t):
        raise AssertionError("不应发起真实调用")

    _enable(monkeypatch, model="")
    p = LlmProbe()
    snap = asyncio.run(p.probe_once(runner=runner))
    assert snap["state"] == "disabled"
    assert snap["last_failure_kind"] == "not_configured"


def test_probe_disabled_for_non_cli_provider(monkeypatch):
    _enable(monkeypatch, provider="openai")
    p = LlmProbe()
    snap = asyncio.run(p.probe_once(runner=lambda m, c, t: "OK"))
    assert snap["state"] == "disabled"


# ---------------------------------------------------------------- ③ 缓存语义


def test_success_cached_failure_not_permanently(monkeypatch):
    """成功长缓存（省额度），失败短缓存（防抖但不掩盖故障）。"""
    _enable(monkeypatch)
    ok_calls = []

    def ok_runner(m, c, t):
        ok_calls.append(1)
        return "OK"

    p = LlmProbe(ok_ttl=3600.0, fail_ttl=0.0)
    asyncio.run(p.probe_once(runner=ok_runner))
    snap2 = asyncio.run(p.probe_once(runner=ok_runner))
    assert len(ok_calls) == 1
    assert snap2["cached"] is True

    fail_calls = []

    def fail_runner(m, c, t):
        fail_calls.append(1)
        raise lc.LLMError("need quota", lc.LLMFailure.QUOTA)

    p2 = LlmProbe(ok_ttl=3600.0, fail_ttl=0.0)
    asyncio.run(p2.probe_once(runner=fail_runner))
    asyncio.run(p2.probe_once(runner=fail_runner))
    assert len(fail_calls) == 2, "失败 TTL 到期必须重探，否则故障被永久掩盖"
    assert p2.consecutive_failures == 2


def test_force_bypasses_cache(monkeypatch):
    _enable(monkeypatch)
    calls = []

    def runner(m, c, t):
        calls.append(1)
        return "OK"

    p = LlmProbe(ok_ttl=3600.0)
    asyncio.run(p.probe_once(runner=runner))
    snap = asyncio.run(p.probe_once(runner=runner, force=True))
    assert len(calls) == 2 and snap["cached"] is False


# ---------------------------------------------------------------- ④ 端点暴露


def _app(probe=None) -> FastAPI:
    app = FastAPI()
    app.include_router(health_route.router, prefix="/api")
    app.dependency_overrides[health_route.get_hub] = lambda: SimpleNamespace(
        provider=SimpleNamespace(name="fake")
    )
    if probe is not None:
        app.state.llm_probe = probe
    return app


def test_endpoint_not_started():
    with TestClient(_app()) as client:
        body = client.get("/api/system/llm-probe").json()
    assert body == {"state": "not_started"}


def test_endpoint_force_runs_real_call(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(probe_mod, "run_probe_call", lambda m, c, t: "OK")
    with TestClient(_app(LlmProbe())) as client:
        body = client.get("/api/system/llm-probe?force=1").json()
    assert body["state"] == "ok"
    assert body["cached"] is False


def test_providers_includes_llm_gateway(monkeypatch):
    """/api/system/providers 一站式暴露；未启动态与 sentinel 保持一致。"""
    probe = LlmProbe()
    with TestClient(_app(probe)) as client:
        body = client.get("/api/system/providers").json()
    assert body["llm_gateway"]["state"] == "idle"
    assert "config" in body["llm_gateway"]

    with TestClient(_app()) as client:
        body2 = client.get("/api/system/providers").json()
    assert body2["llm_gateway"] == {"state": "not_started"}


def test_providers_endpoint_does_not_spend_quota(monkeypatch):
    """轮询 providers 绝不能触发真实调用——那等于反复烧额度。"""
    _enable(monkeypatch)

    def boom(m, c, t):
        raise AssertionError("providers 端点不应触发真实探针调用")

    monkeypatch.setattr(probe_mod, "run_probe_call", boom)
    with TestClient(_app(LlmProbe())) as client:
        client.get("/api/system/providers").json()


# ---------------------------------------------------------------- ⑤ loop 挂载


# ---------------------------------------------------------------- ⑥ 告警接线


def _fail_runner(kind=lc.LLMFailure.QUOTA, msg="need quota 0.09"):
    def runner(m, c, t):
        raise lc.LLMError(msg, kind)

    return runner


def _capture_alerts(monkeypatch):
    fired: list[dict] = []

    async def fake_fire(**kw):
        fired.append(kw)
        return True

    monkeypatch.setattr(probe_mod, "fire_llm_alert", fake_fire)
    return fired


def test_alert_fires_only_after_threshold(monkeypatch):
    _enable(monkeypatch)
    fired = _capture_alerts(monkeypatch)
    p = LlmProbe(ok_ttl=0.0, fail_ttl=0.0, alert_after=2, alert_cooldown=0.0)
    asyncio.run(p.probe_once(runner=_fail_runner()))
    assert fired == [], "未达阈值不得告警（单次抖动不该惊动人）"
    asyncio.run(p.probe_once(runner=_fail_runner()))
    assert len(fired) == 1
    assert fired[0]["kind"] == "quota"
    assert "充值" in (fired[0]["hint"] or "")
    assert fired[0]["consecutive"] == 2
    assert p.alerts_fired == 1 and p.last_alert_at is not None


def test_alert_cooldown_suppresses_spam(monkeypatch):
    """定时探针不冷却必然刷屏——2026-09-04 定案的"预警无冷却"缺陷。"""
    _enable(monkeypatch)
    fired = _capture_alerts(monkeypatch)
    p = LlmProbe(ok_ttl=0.0, fail_ttl=0.0, alert_after=1, alert_cooldown=3600.0)
    for _ in range(4):
        asyncio.run(p.probe_once(runner=_fail_runner()))
    assert len(fired) == 1
    assert p.consecutive_failures == 4


def test_alert_not_counted_when_dispatch_fails(monkeypatch):
    async def noop(**kw):
        return False

    _enable(monkeypatch)
    monkeypatch.setattr(probe_mod, "fire_llm_alert", noop)
    p = LlmProbe(ok_ttl=0.0, fail_ttl=0.0, alert_after=1, alert_cooldown=3600.0)
    asyncio.run(p.probe_once(runner=_fail_runner()))
    assert p.alerts_fired == 0 and p.last_alert_at is None


def test_alert_can_be_turned_off(monkeypatch):
    _enable(monkeypatch)
    fired = _capture_alerts(monkeypatch)
    p = LlmProbe(ok_ttl=0.0, fail_ttl=0.0, alert_after=1, alert_enabled=False)
    asyncio.run(p.probe_once(runner=_fail_runner()))
    assert fired == []


def test_disabled_probe_never_alerts(monkeypatch):
    """没配模型不是故障——不该为此发告警。"""
    _enable(monkeypatch, model="")
    fired = _capture_alerts(monkeypatch)
    p = LlmProbe(alert_after=1)
    asyncio.run(p.probe_once(runner=_fail_runner()))
    assert fired == []
    assert p.state == "disabled"


def test_probe_loop_registers_on_app_state(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(probe_mod, "run_probe_call", lambda m, c, t: "OK")
    app = SimpleNamespace(state=SimpleNamespace())

    async def _run():
        stop = asyncio.Event()
        task = asyncio.create_task(probe_loop(app, stop=stop, interval=0.01))
        await asyncio.sleep(0.05)
        stop.set()
        await task

    asyncio.run(_run())
    assert app.state.llm_probe is not None
    assert app.state.llm_probe.probes >= 1
    assert app.state.llm_probe.state == "ok"
