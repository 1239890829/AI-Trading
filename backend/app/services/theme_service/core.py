"""共享基础设施：日志句柄与 provider 定位（被 board_match / board_build 共用）。

`backend/app/services/theme_service.py` 的内部切片（IMP-005 批 4，2026-09-15 从 1383 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与前置注释与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app.services.theme_service` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

import logging
# 切片说明（IMP-005 批 4）：原文件此处为 `logging.getLogger(__name__)`；搬进子模块后
# `__name__` 会变成 "app.services.theme_service.core" ⇒ 改为**硬编码原日志名**，
# 保持日志通道与拆分前完全一致（全仓无按 logger 名过滤的配置，且无 caplog 按该名断言）。
log = logging.getLogger("app.services.theme_service")


def _pick_provider(provider, class_name: str):
    """从 composite provider 链里取出指定实现。

    题材归因只有同花顺提供 ``reason``，开板/换手/流通市值只有东财提供；
    走 composite 会在每个失败源上串行重试（每源 8s 超时 × 4 源），
    回溯 5 日就能把请求拖到 3 分钟以上并压垮事件循环。这里直接定位到具体 provider。
    """
    members = getattr(provider, "providers", None)
    if isinstance(members, (list, tuple)):
        return next((p for p in members if type(p).__name__ == class_name), None)
    return provider if type(provider).__name__ == class_name else None
