"""飞书群自定义机器人通知通道。

使用飞书「群机器人 → 自定义机器人」webhook（https://open.feishu.cn/open-apis/bot/v2/hook/xxx），
配置 `ASHARE_ALERT_FEISHU_WEBHOOK` 后可用；机器人开启「签名校验」时再配
`ASHARE_ALERT_FEISHU_SECRET`。两者均留空 = 通道不可用：send() 显式 warning 并返回
False（调用方看到 feishu 不在 delivered_channels 即知未发出），绝不静默伪装成功。

消息形态用最稳的 text：告警文案本体来自 AlertEvent.snapshot.text（写入点
services/ths_sentinel.py / picks/watcher.py 都已生成人类可读的 text），这里不二次拼装行情。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

from app.models.alert import AlertEvent, AlertRule
from app.notifiers import Notifier, _symbol_snapshot

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(10.0)


def feishu_sign(timestamp: str, secret: str) -> str:
    """飞书自定义机器人签名校验算法（官方）：

    以 ``f"{timestamp}\\n{secret}"`` 为 HMAC-SHA256 的 **key**、空消息为内容，
    结果 base64。飞书服务端按同样方式重算比对。
    """
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def format_alert_text(event: AlertEvent, rule: AlertRule) -> str:
    """把 AlertEvent 渲染成飞书 text 消息文案（纯函数，便于测试）。"""
    snap = _symbol_snapshot(event.snapshot)
    lines = [f"🚨 {rule.name or f'rule#{rule.id}'} ({rule.condition_type})"]
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
    """飞书群机器人通道。webhook/secret 可注入（测试用），默认读全局配置。"""

    name = "feishu"

    def __init__(
        self,
        webhook: str | None = None,
        secret: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        # None = 未显式指定 → 读全局配置；显式传 "" = 有意禁用（测试无签名场景）
        if webhook is None or secret is None:
            from app.core.config import settings

            if webhook is None:
                webhook = settings.alert_feishu_webhook
            if secret is None:
                secret = settings.alert_feishu_secret
        self.webhook = webhook
        self.secret = secret
        self._client = client

    def is_available(self) -> bool:
        return bool(self.webhook)

    async def send(self, event: AlertEvent, rule: AlertRule) -> bool:
        if not self.is_available():
            log.warning(
                "feishu channel requested but ASHARE_ALERT_FEISHU_WEBHOOK is not "
                "configured; skip (event=%s symbol=%s)",
                event.id, event.symbol,
            )
            return False

        payload: dict[str, Any] = {
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
