"""通知通道抽象层。

默认实现：
- ConsoleNotifier：打印日志（永远可用，便于本地验证）
- InAppNotifier：把事件写入 AlertEvent 表，前端通过 REST/WS 拉取
- FeishuNotifier：飞书群自定义机器人 webhook（`notifiers/feishu.py`）
  —— 配置 ASHARE_ALERT_FEISHU_WEBHOOK 后，规则 channels 里选 "feishu" 即走此通道；
     未配置时显式 warning 跳过，不静默伪装成功。
"""
from __future__ import annotations

import json
import logging

from app.models.alert import AlertEvent, AlertRule
from app.notifiers.base import Notifier, _symbol_snapshot  # noqa: F401 (re-export)
from app.notifiers.feishu import FeishuNotifier

log = logging.getLogger(__name__)


class ConsoleNotifier(Notifier):
    name = "log"

    async def send(self, event: AlertEvent, rule: AlertRule) -> bool:
        snap = _symbol_snapshot(event.snapshot)
        log.warning(
            "[ALERT] %s %s %s=%s 触发 %s (阈值=%s)",
            rule.name or f"rule#{rule.id}",
            rule.condition_type,
            event.symbol,
            event.trigger_value,
            "触发",
            event.threshold,
            extra={"snapshot": snap},
        )
        return True


class InAppNotifier(Notifier):
    """通过数据库存储实现应用内通知；是否成功以能否持久化衡量。"""

    name = "in_app"

    async def send(self, event: AlertEvent, rule: AlertRule) -> bool:
        # event 已经由 AlertRepository.record_trigger 写入
        return True


class NotifierRegistry:
    def __init__(self):
        self._notifiers: dict[str, Notifier] = {}
        self.register(ConsoleNotifier())
        self.register(InAppNotifier())
        # 始终注册：未配置 webhook 时 send() 内显式 warning 并返回 False，
        # 让 "feishu 不在 delivered_channels" 与日志共同构成可见的跳过事实。
        self.register(FeishuNotifier())

    def register(self, notifier: Notifier) -> None:
        self._notifiers[notifier.name] = notifier

    def get(self, name: str) -> Notifier | None:
        return self._notifiers.get(name)

    def names(self) -> list[str]:
        return list(self._notifiers.keys())

    async def dispatch(self, event: AlertEvent, rule: AlertRule) -> list[str]:
        channels = []
        try:
            wanted = json.loads(rule.channels or "[]")
        except Exception:
            wanted = ["in_app", "log"]
        for ch in wanted:
            notifier = self._notifiers.get(ch)
            if not notifier:
                continue
            try:
                if await notifier.send(event, rule):
                    channels.append(ch)
            except Exception:
                log.exception("alert channel %s failed for event %s", ch, event.id)
        return channels


_default_registry = NotifierRegistry()


def get_notifier_registry() -> NotifierRegistry:
    return _default_registry
