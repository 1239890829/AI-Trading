"""飞书通知通道（两条路，都未配置时显式跳过不伪装成功）。

A. 群自定义机器人 webhook（https://open.feishu.cn/open-apis/bot/v2/hook/xxx），
   配置 `ASHARE_ALERT_FEISHU_WEBHOOK` 后可用；机器人开启「签名校验」时再配
   `ASHARE_ALERT_FEISHU_SECRET`。
B. 自建应用凭据直发 P2P 私信（2026-09-04 实测可用）：配置
   `ASHARE_FEISHU_APP_ID` / `ASHARE_FEISHU_APP_SECRET` /
   `ASHARE_FEISHU_NOTIFY_OPEN_ID` 三件后，用 tenant_access_token 调
   OpenAPI /im/v1/messages 发给接收人——无需建群、无需 webhook。
   两条路都配置时优先 webhook（群播 vs 私聊，语义不同）。

都未配置 = 通道不可用：send() 显式 warning 并返回 False（调用方看到
feishu 不在 delivered_channels 即知未发出），绝不静默伪装成功。

消息形态用最稳的 text：告警文案本体来自 AlertEvent.snapshot.text（写入点
services/ths_sentinel.py / picks/watcher.py 都已生成人类可读的 text），这里不二次拼装行情。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

from app.core.ttl_cache import cache_on
from app.models.alert import AlertEvent, AlertRule

# ⚠️ 必须从**定义处** `base` 导入，不能写 `from app.notifiers import ...`：
# 包 `__init__.py` 会 re-export `FeishuNotifier`（第 18 行），若此处再回引包，
# 就形成 `app.notifiers` ⇄ `app.notifiers.feishu` 的**加载期环**。实测（2026-09-15）：
# 仅交换 `__init__.py` 里两行 import 的顺序（零逻辑改动）即
# `ImportError: cannot import name 'Notifier' from partially initialized module 'app.notifiers'`
# ⇒ 环当前"能跑"完全依赖行序，属潜伏缺陷。改指向 `base` 后环消失且与行序无关。
# 注：下方 `get_notifier_registry` 确实定义在包内，只能从包导入——但那是**函数内**
# 延迟导入，不参与加载期依赖图。
from app.notifiers.base import DeliveryResult, Notifier, _symbol_snapshot

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(10.0)

_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
# 官方 expire=7200s；缓存 6600s 提前换新，避免临界点拿到将过期 token
_TOKEN_TTL_SECONDS = 6600


def feishu_sign(timestamp: str, secret: str) -> str:
    """飞书自定义机器人签名校验算法（官方）：

    以 ``f"{timestamp}\\n{secret}"`` 为 HMAC-SHA256 的 **key**、空消息为内容，
    结果 base64。飞书服务端按同样方式重算比对。
    """
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def format_alert_text(event: AlertEvent, rule: AlertRule) -> str:
    """把 AlertEvent 渲染成飞书 text 消息文案（纯函数，便于测试）。

    触发时间行取 event.triggered_at——它自 2026-09-09 起就是**北京 naive**
    （`models/alert.py` 的列默认值即 `beijing_now_naive`），**直接展示即可**。
    提醒发生在盘中，收到消息的人第一反应是「什么时候触发的」，文案必须自带时间。
    """
    snap = _symbol_snapshot(event.snapshot)
    lines = [f"🚨 {rule.name or f'rule#{rule.id}'} ({rule.condition_type})"]
    if event.triggered_at is not None:
        bj = event.triggered_at
        lines.append(f"触发时间：{bj.strftime('%Y-%m-%d %H:%M:%S')}（北京时间）")
    if event.symbol:
        lines.append(f"标的：{event.symbol}")
    body = (snap.get("text") or "").strip()
    if body:
        lines.append(body)
    lines.append(
        f"触发值 {event.trigger_value} / 阈值 {event.threshold}"
    )
    return "\n".join(lines)


def _is_success_body(body: object) -> bool:
    """只认明确整数零码（兼容两种键）；受理不等于送达或已读。"""
    if not isinstance(body, dict):
        return False
    codes = [body[key] for key in ("code", "StatusCode") if key in body]
    return bool(codes) and all(type(code) is int and code == 0 for code in codes)


def _response_result(resp: httpx.Response, via: str) -> DeliveryResult:
    """把 HTTP + 平台 body 映射为 accepted / explicit_rejected / unknown。"""
    status = int(resp.status_code)
    if 400 <= status < 500:
        return DeliveryResult("explicit_rejected", f"{via}_http_{status}")
    if status != 200:
        return DeliveryResult("unknown", f"{via}_http_{status}_unconfirmed")
    try:
        body = resp.json()
    except Exception:
        return DeliveryResult("unknown", f"{via}_non_json")
    if _is_success_body(body):
        return DeliveryResult("accepted", "platform_accepted")
    if isinstance(body, dict):
        codes = [body[key] for key in ("code", "StatusCode") if key in body]
        if codes and all(type(code) is int and code != 0 for code in codes):
            return DeliveryResult("explicit_rejected", f"{via}_platform_rejected")
    return DeliveryResult("unknown", f"{via}_acceptance_unconfirmed")


class _FeishuRejected(RuntimeError):
    pass


class _FeishuUnknown(RuntimeError):
    pass


class FeishuNotifier(Notifier):
    """飞书通道：webhook（群）优先，否则自建应用凭据 P2P 直发。

    各凭据参数 None = 未显式指定 → 读全局配置；显式传 "" = 有意禁用（测试无凭据场景）。
    """

    name = "feishu"

    def __init__(
        self,
        webhook: str | None = None,
        secret: str | None = None,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        open_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        if webhook is None or secret is None or app_id is None or app_secret is None or open_id is None:
            from app.core.config import settings

            if webhook is None:
                webhook = settings.alert_feishu_webhook
            if secret is None:
                secret = settings.alert_feishu_secret
            if app_id is None:
                app_id = settings.feishu_app_id
            if app_secret is None:
                app_secret = settings.feishu_app_secret
            if open_id is None:
                open_id = settings.feishu_notify_open_id
        self.webhook = webhook
        self.secret = secret
        self.app_id = app_id
        self.app_secret = app_secret
        self.open_id = open_id
        self._client = client

    @property
    def webhook_available(self) -> bool:
        return bool(self.webhook)

    @property
    def app_available(self) -> bool:
        return bool(self.app_id and self.app_secret and self.open_id)

    def is_available(self) -> bool:
        return self.webhook_available or self.app_available

    def delivery_target(self) -> str:
        """Opaque destination identity; pending work must not follow a new recipient."""
        if self.webhook_available:
            route = ["webhook", self.webhook]
        elif self.app_available:
            route = ["app", self.app_id, self.open_id]
        else:
            return ""
        return hashlib.sha256(json.dumps(route).encode()).hexdigest()

    async def _fetch_tenant_token(self) -> str:
        """换取 tenant_access_token；明确拒绝与未知错误分别上抛。"""
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}
        try:
            if self._client is not None:
                resp = await self._client.post(_TOKEN_URL, json=payload)
            else:
                async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as hc:
                    resp = await hc.post(_TOKEN_URL, json=payload)
        except Exception as exc:
            raise _FeishuUnknown("tenant_token_request_unconfirmed") from exc
        result = _response_result(resp, "tenant_token")
        if result.outcome == "explicit_rejected":
            raise _FeishuRejected(result.reason)
        if result.outcome != "accepted":
            raise _FeishuUnknown(result.reason)
        try:
            body = resp.json()
        except Exception as exc:
            raise _FeishuUnknown("tenant_token_non_json") from exc
        token = body.get("tenant_access_token") if isinstance(body, dict) else None
        if not token:
            raise _FeishuUnknown("tenant_token_missing")
        return str(token)

    async def _get_tenant_token(self) -> str:
        """tenant token 进程内缓存（TTLCache 统一层，key=app_id 支持多应用）。"""
        # 挂在默认 registry（进程级单例）上；函数内导入避免循环依赖
        from app.notifiers import get_notifier_registry

        cache = cache_on(get_notifier_registry(), "feishu_tenant_token", _TOKEN_TTL_SECONDS, maxsize=4)
        _, token = await cache.get_or_set(self.app_id, self._fetch_tenant_token)
        return token

    async def _send_via_webhook_result(
        self, event: AlertEvent, rule: AlertRule, card: dict | None = None,
    ) -> DeliveryResult:
        if card is not None:
            payload: dict[str, Any] = {"msg_type": "interactive", "card": card}
        else:
            payload = {"msg_type": "text", "content": {"text": format_alert_text(event, rule)}}
        if self.secret:
            ts = str(int(time.time()))
            payload["timestamp"] = ts
            payload["sign"] = feishu_sign(ts, self.secret)
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                    resp = await client.post(self.webhook, json=payload)
            else:
                resp = await self._client.post(self.webhook, json=payload)
        except Exception:
            log.exception("feishu webhook request failed (event=%s)", event.id)
            return DeliveryResult("unknown", "webhook_request_unconfirmed")
        return _response_result(resp, "webhook")

    async def _send_via_app_result(
        self, event: AlertEvent, rule: AlertRule, card: dict | None = None,
    ) -> DeliveryResult:
        try:
            token = await self._get_tenant_token()
        except _FeishuRejected as exc:
            return DeliveryResult("explicit_rejected", str(exc))
        except Exception:
            log.exception("feishu app token request unconfirmed (event=%s)", event.id)
            return DeliveryResult("unknown", "tenant_token_unconfirmed")

        payload = {
            "receive_id": self.open_id,
            "msg_type": "interactive" if card is not None else "text",
            "content": json.dumps(
                card if card is not None else {"text": format_alert_text(event, rule)},
                ensure_ascii=False,
            ),
        }
        headers = {"Authorization": f"Bearer {token}"}
        try:
            if self._client is not None:
                resp = await self._client.post(
                    _MESSAGE_URL, params={"receive_id_type": "open_id"}, json=payload, headers=headers,
                )
            else:
                async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as hc:
                    resp = await hc.post(
                        _MESSAGE_URL, params={"receive_id_type": "open_id"}, json=payload, headers=headers,
                    )
        except Exception:
            log.exception("feishu app message request failed (event=%s)", event.id)
            return DeliveryResult("unknown", "app_request_unconfirmed")

        result = _response_result(resp, "app")
        if result.outcome != "accepted":
            from app.notifiers import get_notifier_registry

            cache_on(get_notifier_registry(), "feishu_tenant_token", _TOKEN_TTL_SECONDS, maxsize=4).invalidate(
                self.app_id
            )
        return result

    async def send_result(self, event: AlertEvent, rule: AlertRule) -> DeliveryResult:
        snap = _symbol_snapshot(event.snapshot)
        card = snap.get("card") if isinstance(snap.get("card"), dict) else None
        if self.webhook_available:
            return await self._send_via_webhook_result(event, rule, card=card)
        if self.app_available:
            return await self._send_via_app_result(event, rule, card=card)
        log.warning("feishu channel requested but no configured target (event=%s)", event.id)
        return DeliveryResult("explicit_rejected", "channel_unconfigured")

    async def send(self, event: AlertEvent, rule: AlertRule) -> bool:
        """Compatibility API: True only for explicit platform acceptance."""
        return bool(await self.send_result(event, rule))

    async def send_interactive_result(self, card: dict) -> DeliveryResult:
        """类型化 direct-card 回执；仅保留兼容能力，买点主链不再旁路直发。"""
        if self.webhook_available:
            payload: dict[str, Any] = {"msg_type": "interactive", "card": card}
            if self.secret:
                ts = str(int(time.time()))
                payload["timestamp"] = ts
                payload["sign"] = feishu_sign(ts, self.secret)
            try:
                if self._client is None:
                    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                        resp = await client.post(self.webhook, json=payload)
                else:
                    resp = await self._client.post(self.webhook, json=payload)
            except Exception:
                return DeliveryResult("unknown", "webhook_card_request_unconfirmed")
            return _response_result(resp, "webhook_card")
        if self.app_available:
            try:
                token = await self._get_tenant_token()
            except _FeishuRejected as exc:
                return DeliveryResult("explicit_rejected", str(exc))
            except Exception:
                return DeliveryResult("unknown", "tenant_token_unconfirmed")
            payload = {
                "receive_id": self.open_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            }
            headers = {"Authorization": f"Bearer {token}"}
            try:
                if self._client is not None:
                    resp = await self._client.post(
                        _MESSAGE_URL, params={"receive_id_type": "open_id"}, json=payload, headers=headers,
                    )
                else:
                    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as hc:
                        resp = await hc.post(
                            _MESSAGE_URL, params={"receive_id_type": "open_id"}, json=payload, headers=headers,
                        )
            except Exception:
                return DeliveryResult("unknown", "app_card_request_unconfirmed")
            result = _response_result(resp, "app_card")
            if result.outcome != "accepted":
                from app.notifiers import get_notifier_registry

                cache_on(
                    get_notifier_registry(), "feishu_tenant_token",
                    _TOKEN_TTL_SECONDS, maxsize=4,
                ).invalidate(self.app_id)
            return result
        return DeliveryResult("explicit_rejected", "channel_unconfigured")

    async def send_interactive(self, card: dict) -> bool:
        """兼容旧调用方：仅明确 accepted 返回 True。"""
        return bool(await self.send_interactive_result(card))
