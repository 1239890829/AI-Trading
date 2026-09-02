"""飞书通知通道测试：签名 / 文案渲染 / 成败判定 / 未配置显式跳过 / registry 集成。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import httpx

from app.models.alert import AlertEvent, AlertRule
from app.notifiers import NotifierRegistry
from app.notifiers.feishu import FeishuNotifier, feishu_sign, format_alert_text


def _event(symbol: str = "600519") -> AlertEvent:
    return AlertEvent(
        id=7,
        rule_id=1,
        symbol=symbol,
        trigger_value=12.5,
        threshold=12.0,
        triggered_at=datetime(2026, 9, 2, 10, 30, 0),
        snapshot=json.dumps({"kind": "price_above", "text": "股价 12.50 突破阈值 12.00"}),
    )


def _rule(channels: str = '["feishu"]') -> AlertRule:
    return AlertRule(
        id=1, name="茅台突破", condition_type="price_above", channels=channels,
    )


def _client(handler_calls: list, body: dict, status_code: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        handler_calls.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(status_code, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- 纯函数

def test_feishu_sign_matches_official_algorithm():
    # golden 值：以 f"{timestamp}\n{secret}" 为 key、空消息的 HMAC-SHA256 base64
    assert feishu_sign("1700000000", "test-secret") == "mbm4Y4oluIPQ00qlBIhX8vAZ0EKv3nw0LuTb91jPL84="


def test_format_alert_text_contains_fields():
    text = format_alert_text(_event(), _rule())
    assert "茅台突破" in text
    assert "price_above" in text
    assert "600519" in text
    assert "股价 12.50 突破阈值 12.00" in text
    assert "12.5" in text and "12.0" in text


def test_format_alert_text_survives_bad_snapshot():
    ev = _event()
    ev.snapshot = "not-json"
    text = format_alert_text(ev, _rule())
    assert "600519" in text  # 不炸，缺 text 只少一段


# ---------------------------------------------------------------- send

def test_send_success_posts_text_payload():
    calls: list = []
    n = FeishuNotifier(webhook="https://hook.test/abc", secret="", client=_client(calls, {"code": 0}))
    ok = asyncio.run(n.send(_event(), _rule()))
    assert ok is True
    assert len(calls) == 1
    body = calls[0]
    assert body["msg_type"] == "text"
    assert "茅台突破" in body["content"]["text"]
    assert "timestamp" not in body and "sign" not in body  # 未配 secret 不带签名


def test_send_with_secret_includes_signature():
    calls: list = []
    n = FeishuNotifier(webhook="https://hook.test/abc", secret="s3cret", client=_client(calls, {"code": 0}))
    ok = asyncio.run(n.send(_event(), _rule()))
    assert ok is True
    body = calls[0]
    assert body["sign"] and body["timestamp"]
    assert body["sign"] == feishu_sign(body["timestamp"], "s3cret")


def test_send_rejected_when_body_code_nonzero():
    calls: list = []
    n = FeishuNotifier(webhook="https://hook.test/abc", client=_client(calls, {"code": 19021, "msg": "sign match fail"}))
    ok = asyncio.run(n.send(_event(), _rule()))
    assert ok is False


def test_send_rejected_on_http_error():
    calls: list = []
    n = FeishuNotifier(webhook="https://hook.test/abc", client=_client(calls, {"code": 0}, status_code=500))
    assert asyncio.run(n.send(_event(), _rule())) is False

    # 网络异常同样返回 False 不上抛
    n2 = FeishuNotifier(
        webhook="https://hook.test/abc",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("boom")))),
    )
    assert asyncio.run(n2.send(_event(), _rule())) is False


def test_send_unconfigured_returns_false_without_request():
    calls: list = []
    n = FeishuNotifier(webhook="", secret="", client=_client(calls, {"code": 0}))
    ok = asyncio.run(n.send(_event(), _rule()))
    assert ok is False
    assert calls == []  # 显式跳过：连请求都不发


# ---------------------------------------------------------------- registry

def test_registry_dispatch_skips_unconfigured_feishu():
    reg = NotifierRegistry()
    assert "feishu" in reg.names()
    delivered = asyncio.run(reg.dispatch(_event(), _rule(channels='["feishu"]')))
    assert "feishu" not in delivered  # 未配置 → 显式缺席，不伪装成功


def test_registry_dispatch_delivers_configured_feishu():
    reg = NotifierRegistry()
    calls: list = []
    reg.register(FeishuNotifier(webhook="https://hook.test/abc", client=_client(calls, {"code": 0})))
    delivered = asyncio.run(reg.dispatch(_event(), _rule(channels='["in_app", "feishu"]')))
    assert "feishu" in delivered
    assert len(calls) == 1
