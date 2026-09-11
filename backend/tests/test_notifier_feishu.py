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


def test_format_alert_text_includes_beijing_trigger_time():
    """triggered_at 已是**北京 naive**（2026-09-09 口径统一），文案**原样展示**。

    本用例原先写的是 `datetime(2026, 9, 3, 2, 30, 5)  # UTC → 北京 10:30:05`
    并断言输出 `10:30:05` —— 那断言的是**双重偏移**：`models/alert.py` 的
    `triggered_at` 列默认值就是 `beijing_now_naive`，库内本就是北京时间，
    渲染层再 +8h 会让飞书文案晚 8 小时（2026-09-11 实测：库内 14:59 显示成 22:59）。
    口径统一时只改了写入侧、漏了这里，且旧断言把它固化了下来。
    """
    ev = _event()
    ev.triggered_at = datetime(2026, 9, 3, 10, 30, 5)  # 北京 naive，直接展示
    text = format_alert_text(ev, _rule())
    assert "触发时间：2026-09-03 10:30:05（北京时间）" in text
    # 防回潮：不得出现 +8h 之后的墙钟
    assert "18:30:05" not in text


def test_format_alert_text_without_triggered_at_has_no_time_line():
    """triggered_at 为 None 时不出现时间行，也不炸。"""
    ev = _event()
    ev.triggered_at = None
    text = format_alert_text(ev, _rule())
    assert "触发时间" not in text


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
    # app 凭据也显式置空：本地 .env 可能已配，settings 默认值不可依赖（测试确定性）
    n = FeishuNotifier(webhook="", secret="", app_id="", app_secret="", open_id="", client=_client(calls, {"code": 0}))
    ok = asyncio.run(n.send(_event(), _rule()))
    assert ok is False
    assert calls == []  # 显式跳过：连请求都不发


# ---------------------------------------------------------------- app 凭据 P2P 通道

def _app_client(
    calls: list,
    token: str = "t-mock",
    token_ok: bool = True,
    msg_body: dict | None = None,
    msg_status: int = 200,
) -> httpx.AsyncClient:
    """Mock 同时应答 token 端点与消息端点；calls 记录 (url, request)。"""
    msg_body = msg_body or {"code": 0, "data": {"message_id": "om_mock"}}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), request))
        if "tenant_access_token" in str(request.url):
            if token_ok:
                return httpx.Response(200, json={"code": 0, "tenant_access_token": token, "expire": 7200})
            return httpx.Response(200, json={"code": 10003, "msg": "invalid app_id"})
        return httpx.Response(msg_status, json=msg_body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _app_notifier(calls: list, app_id: str = "cli_test_a", **kw) -> FeishuNotifier:
    """app 通道 notifier：webhook 显式置空，凭据齐备，client 为双端点 mock。

    app_id 默认各用例取不同值：token 缓存挂在进程级 registry 上，键隔离防跨用例污染。
    """
    return FeishuNotifier(
        webhook="", secret="", app_id=app_id, app_secret="s3cret", open_id="ou_recv",
        client=_app_client(calls, **kw),
    )


def test_app_channel_availability_matrix():
    assert FeishuNotifier(webhook="https://hook.test/abc", secret="", app_id="", app_secret="", open_id="").is_available()
    assert FeishuNotifier(webhook="", secret="", app_id="cli", app_secret="s", open_id="ou").is_available()
    # 三件缺一（如只有 id 没有 secret/open_id）= app 路不可用
    assert not FeishuNotifier(webhook="", secret="", app_id="cli", app_secret="", open_id="ou").is_available()
    assert not FeishuNotifier(webhook="", secret="", app_id="cli", app_secret="s", open_id="").is_available()
    assert not FeishuNotifier(webhook="", secret="", app_id="", app_secret="", open_id="").is_available()


def test_app_channel_send_success_p2p_text():
    calls: list = []
    n = _app_notifier(calls, app_id="cli_test_b1")
    ok = asyncio.run(n.send(_event(), _rule()))
    assert ok is True
    assert len(calls) == 2  # token + message
    token_url, token_req = calls[0]
    msg_url, msg_req = calls[1]
    assert "tenant_access_token" in token_url
    assert json.loads(token_req.content)["app_id"] == "cli_test_b1"
    assert "receive_id_type=open_id" in msg_url
    assert msg_req.headers["Authorization"] == "Bearer t-mock"
    body = json.loads(msg_req.content)
    assert body["receive_id"] == "ou_recv"
    assert body["msg_type"] == "text"
    # OpenAPI 的 content 是 JSON 字符串，不是对象
    assert isinstance(body["content"], str)
    assert "茅台突破" in json.loads(body["content"])["text"]


def test_app_channel_token_failure_returns_false_no_message():
    calls: list = []
    n = FeishuNotifier(
        webhook="", secret="", app_id="cli_test_c1", app_secret="s3cret", open_id="ou_recv",
        client=_app_client(calls, token_ok=False),
    )
    ok = asyncio.run(n.send(_event(), _rule()))
    assert ok is False
    assert len(calls) == 1  # token 失败后不再发消息


def test_app_channel_token_cached_and_invalidated_on_reject():
    calls: list = []
    app_id = "cli_test_d1"
    n = FeishuNotifier(
        webhook="", secret="", app_id=app_id, app_secret="s3cret", open_id="ou_recv",
        client=_app_client(calls, msg_body={"code": 99991663, "msg": "token invalid"}),
    )
    assert asyncio.run(n.send(_event(), _rule())) is False
    # token 失效被拒后清缓存：第二次 send 必须重换 token（共 2 次 token 请求）
    assert asyncio.run(n.send(_event(), _rule())) is False
    token_calls = [c for c in calls if "tenant_access_token" in c[0]]
    assert len(token_calls) == 2
    assert len(calls) == 4  # 2 token + 2 message


# ---------------------------------------------------------------- registry

def test_registry_dispatch_skips_unconfigured_feishu():
    reg = NotifierRegistry()
    assert "feishu" in reg.names()
    # 默认 registry 的 feishu 读全局 settings（本地 .env 可能已配 app 凭据），
    # 这里显式覆写为全空凭据，测试"未配置 → 显式缺席"这一确定性行为
    reg.register(FeishuNotifier(webhook="", secret="", app_id="", app_secret="", open_id=""))
    delivered = asyncio.run(reg.dispatch(_event(), _rule(channels='["feishu"]')))
    assert "feishu" not in delivered  # 未配置 → 显式缺席，不伪装成功


def test_registry_dispatch_delivers_configured_feishu():
    reg = NotifierRegistry()
    calls: list = []
    reg.register(FeishuNotifier(webhook="https://hook.test/abc", client=_client(calls, {"code": 0})))
    delivered = asyncio.run(reg.dispatch(_event(), _rule(channels='["in_app", "feishu"]')))
    assert "feishu" in delivered
    assert len(calls) == 1


# ---------------------------------------------------------------- interactive 卡片（2026-09-08 买点推送）

CARD = {"config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📈 盘中买点"}, "template": "orange"},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "**1｜测试股 600000**"}}]}


def test_send_interactive_via_webhook_posts_card_object():
    calls: list = []
    notifier = FeishuNotifier(webhook="https://example.invalid/hook", client=_client(calls, {"code": 0}))
    assert asyncio.run(notifier.send_interactive(CARD))
    assert calls[0]["msg_type"] == "interactive"
    assert calls[0]["card"] == CARD  # webhook 形态：card 为对象


def test_send_interactive_via_app_posts_content_string():
    calls: list = []
    client = _client(calls, {"code": 0, "tenant_access_token": "tok"})
    notifier = FeishuNotifier(app_id="aid", app_secret="sec", open_id="ou_x", client=client)
    assert asyncio.run(notifier.send_interactive(CARD))
    # OpenAPI 形态：content 为 JSON 字符串
    assert calls[1]["msg_type"] == "interactive"
    assert json.loads(calls[1]["content"]) == CARD


def test_send_interactive_unconfigured_returns_false():
    notifier = FeishuNotifier(webhook="", app_id="", app_secret="", open_id="")
    assert not asyncio.run(notifier.send_interactive(CARD))


def test_event_with_card_snapshot_renders_interactive():
    """snapshot 带 card → send() 走 interactive 分支（通用卡片事件能力）。"""
    calls: list = []
    ev = _event()
    ev.snapshot = json.dumps({"kind": "buy_point", "text": "t", "card": CARD})
    notifier = FeishuNotifier(webhook="https://example.invalid/hook", client=_client(calls, {"code": 0}))
    assert asyncio.run(notifier.send(ev, _rule()))
    assert calls[0]["msg_type"] == "interactive" and calls[0]["card"] == CARD
