"""通知通道基础抽象（零内部依赖，供 __init__ 与各通道实现共同引用）。"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from app.models.alert import AlertEvent, AlertRule

DeliveryOutcome = Literal["accepted", "explicit_rejected", "unknown"]


@dataclass(frozen=True)
class DeliveryResult:
    """渠道受理结果；accepted 只表示平台明确受理，不表示最终送达/已读。"""

    outcome: DeliveryOutcome
    reason: str

    @property
    def accepted(self) -> bool:
        return self.outcome == "accepted"

    def __bool__(self) -> bool:
        return self.accepted


def _symbol_snapshot(snapshot_str: str | None) -> dict[str, Any]:
    if not snapshot_str:
        return {}
    try:
        return json.loads(snapshot_str)
    except Exception:
        return {}


class Notifier(ABC):
    name: str = ""

    @abstractmethod
    async def send(self, event: AlertEvent, rule: AlertRule) -> bool:
        """兼容层：返回是否获得明确受理。"""

    async def send_result(self, event: AlertEvent, rule: AlertRule) -> DeliveryResult:
        """类型化回执；旧 bool 通道的 False 保守解释为 unknown。"""
        try:
            accepted = await self.send(event, rule)
        except Exception:
            return DeliveryResult("unknown", "legacy_channel_exception")
        return (
            DeliveryResult("accepted", "platform_accepted")
            if accepted is True
            else DeliveryResult("unknown", "legacy_acceptance_unconfirmed")
        )
