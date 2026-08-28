from __future__ import annotations

from app.core.config import Settings
from app.data_providers.composite import CompositeProvider
from app.data_providers.eastmoney import EastmoneyProvider
from app.data_providers.mock import MockProvider
from app.data_providers.sina import SinaProvider
from app.data_providers.tencent import TencentProvider

_REGISTRY = {
    "tencent": TencentProvider,
    "sina": SinaProvider,
    "eastmoney": EastmoneyProvider,
    "mock": MockProvider,
}


def _build(name: str, settings: Settings):
    cls = _REGISTRY.get(name.strip())
    if cls is None:
        raise ValueError(f"unknown provider: {name}")
    if name.strip() == "mock":
        return MockProvider()
    return cls(timeout=settings.request_timeout_seconds)


def build_provider(settings: Settings):
    """主源 + 备源链。mock 只能单独使用（作为备源会把真实源失败掩盖成演示数据）。"""
    if settings.data_provider.strip() == "mock":
        return MockProvider()
    names = [settings.data_provider] + [
        n for n in settings.provider_fallbacks.split(",") if n.strip()
    ]
    seen: list[str] = []
    providers = []
    for n in names:
        n = n.strip()
        if not n or n in seen:
            continue
        seen.append(n)
        providers.append(_build(n, settings))
    if len(providers) == 1:
        return providers[0]
    return CompositeProvider(providers)
