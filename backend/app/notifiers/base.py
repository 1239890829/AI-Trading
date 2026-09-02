"""通知通道基础抽象（零内部依赖，供 __init__ 与各通道实现共同引用）。"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from app.models.alert import AlertEvent, AlertRule


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
        """返回是否发送成功。"""
