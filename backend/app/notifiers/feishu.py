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
from app.notifiers import Notifier, _symbol_snapshot

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


def _is_success_body(body: dict[str, Any]) -> bool:
    """飞书新版返回 {"code":0,...}，老版返回 {"StatusCode":0,...}；两套都认。"""
    code = body.get("code")
    status = body.get("StatusCode")
    return (code in (None, 0)) and (status in (None, 0))


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

    async def _fetch_tenant_token(self) -> str:
        """换取 tenant_access_token；失败上抛（调用方记日志并返回 False）。"""
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}
        if self._client is not None:
            resp = await self._client.post(_TOKEN_URL, json=payload)
        else:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as hc:
                resp = await hc.post(_TOKEN_URL, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"tenant_token http {resp.status_code}")
        body = resp.json()
        token = body.get("tenant_access_token")
        if not _is_success_body(body) or not token:
            raise RuntimeError(f"tenant_token rejected: {body}")
        return str(token)

    async def _get_tenant_token(self) -> str:
        """tenant token 进程内缓存（TTLCache 统一层，key=app_id 支持多应用）。"""
        # 挂在默认 registry（进程级单例）上；函数内导入避免循环依赖
        from app.notifiers import get_notifier_registry

        cache = cache_on(get_notifier_registry(), "feishu_tenant_token", _TOKEN_TTL_SECONDS, maxsize=4)
        _, token = await cache.get_or_set(self.app_id, self._fetch_tenant_token)
        return token

    async def _send_via_app(self, event: AlertEvent, rule: AlertRule, card: dict | None = None) -> bool:
        try:
            token = await self._get_tenant_token()
        except Exception as exc:
            log.warning("feishu app channel token fetch failed: %s (event=%s)", exc, event.id)
            return False

        # OpenAPI 的 content 字段是 JSON 字符串（与 webhook 的对象形态不同）；
        # interactive 卡片同理（content=json.dumps(card)），text 保持原形态。
        if card is not None:
            payload = {
                "receive_id": self.open_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            }
        else:
            payload = {
                "receive_id": self.open_id,
                "msg_type": "text",
                "content": json.dumps({"text": format_alert_text(event, rule)}, ensure_ascii=False),
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
            return False

        if resp.status_code != 200:
            log.warning("feishu app message http %s (event=%s)", resp.status_code, event.id)
            return False
        try:
            body = resp.json()
        except Exception:
            log.warning("feishu app message non-json response (event=%s)", event.id)
            return False
        if not _is_success_body(body):
            # token 失效类失败一并清缓存：下一条告警重换 token，避免持续 401 空转
            from app.notifiers import get_notifier_registry

            cache_on(get_notifier_registry(), "feishu_tenant_token", _TOKEN_TTL_SECONDS, maxsize=4).invalidate(
                self.app_id
            )
            log.warning("feishu app message rejected: %s (event=%s)", body, event.id)
            return False
        return True

    async def send(self, event: AlertEvent, rule: AlertRule) -> bool:
        snap = _symbol_snapshot(event.snapshot)
        card_payload = snap.get("card")
        if isinstance(card_payload, dict):
            # 2026-09-08 用户指令：盘中买点推送用 interactive 卡片（与每日精选推送卡同版式，
            # 构建函数 app/picks/push_cards.py）。snapshot.card 存在 → 卡片形态优先。
            if self.webhook_available:
                return await self._send_via_webhook(event, rule, card=card_payload)
            if self.app_available:
                return await self._send_via_app(event, rule, card=card_payload)
            log.warning(
                "feishu card requested but neither webhook nor app credentials are "
                "configured; skip (event=%s symbol=%s)",
                event.id, event.symbol,
            )
            return False
        if self.webhook_available:
            return await self._send_via_webhook(event, rule)
        if self.app_available:
            return await self._send_via_app(event, rule)
        log.warning(
            "feishu channel requested but neither ASHARE_ALERT_FEISHU_WEBHOOK nor "
            "app credentials (ASHARE_FEISHU_APP_ID/_SECRET/_NOTIFY_OPEN_ID) are "
            "configured; skip (event=%s symbol=%s)",
            event.id, event.symbol,
        )
        return False

    async def send_interactive(self, card: dict) -> bool:
        """直接发送一张 interactive 卡片（不走 event/rule 模型）。

    盘中买点聚合卡专用（buy_point.check_and_dispatch 显式单发）：多票同拍
    命中合并一卡，避免逐票分发造成连发。webhook 优先，否则自建应用 P2P；
    都未配置显式 warning 返回 False。
    """
        if self.webhook_available:
            return await self._send_card_webhook(card)
        if self.app_available:
            return await self._send_card_app(card)
        log.warning("feishu interactive card requested but no channel configured; skip")
        return False

    async def _send_card_webhook(self, card: dict) -> bool:
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
            log.exception("feishu webhook card request failed")
            return False
        return self._card_resp_ok(resp, "webhook")

    async def _send_card_app(self, card: dict) -> bool:
        try:
            token = await self._get_tenant_token()
        except Exception as exc:
            log.warning("feishu app card token fetch failed: %s", exc)
            return False
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
            log.exception("feishu app card request failed")
            return False
        if not self._card_resp_ok(resp, "app"):
            # token 失效类失败清缓存：下一张卡重换 token
            from app.notifiers import get_notifier_registry

            cache_on(get_notifier_registry(), "feishu_tenant_token", _TOKEN_TTL_SECONDS, maxsize=4).invalidate(
                self.app_id
            )
            return False
        return True

    def _card_resp_ok(self, resp: httpx.Response, via: str) -> bool:
        if resp.status_code != 200:
            log.warning("feishu %s card http %s", via, resp.status_code)
            return False
        try:
            body = resp.json()
        except Exception:
            log.warning("feishu %s card non-json response", via)
            return False
        if not _is_success_body(body):
            log.warning("feishu %s card rejected: %s", via, body)
            return False
        return True

    async def _send_via_webhook(self, event: AlertEvent, rule: AlertRule, card: dict | None = None) -> bool:
        if card is not None:
            payload: dict[str, Any] = {"msg_type": "interactive", "card": card}
        else:
            payload = {
                "msg_type": "text",
                "content": {"text": format_alert_text(event, rule)},
            }
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
            return False

        if resp.status_code != 200:
            log.warning(
                "feishu webhook http %s (event=%s)", resp.status_code, event.id
            )
            return False
        try:
            body = resp.json()
        except Exception:
            log.warning("feishu webhook non-json response (event=%s)", event.id)
            return False
        if not _is_success_body(body):
            log.warning(
                "feishu webhook rejected: %s (event=%s)", body, event.id
            )
            return False
        return True
