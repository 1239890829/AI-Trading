"""情绪阈值配置化（archive/plan-review.md P0-3）。

默认分档 = engine 里的业界经验值（HEAT_BANDS / EARNING_BANDS，来源与依据见
engine 模块注释）。支持通过 settings 的 JSON 字符串覆盖：

- ``ASHARE_SENTIMENT_HEAT_BANDS_JSON``
- ``ASHARE_SENTIMENT_EARNING_BANDS_JSON``

覆盖必须通过结构校验（键齐全、每档为 (上界|None, 得分, 标签) 三元组、上界
严格递增），非法配置直接抛错——**配置错误宁可启动失败，也不静默回退默认值**
（否则用户以为在调参，实际仍在跑默认值，结论失真且无感知）。
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)


def _validate_band_shape(bands: dict, template: dict, name: str) -> None:
    """结构校验：键齐全；每档 (upper, points, label)；upper 严格递增（None 只能在末位）。"""
    missing = set(template) - set(bands)
    extra = set(bands) - set(template)
    if missing or extra:
        raise ValueError(f"{name}: 指标键不匹配（缺 {sorted(missing)}，多 {sorted(extra)}）")
    for key, rows in bands.items():
        if not rows or not isinstance(rows, list):
            raise ValueError(f"{name}.{key}: 分档必须是非空列表")
        prev_upper = None
        seen_none = False
        for row in rows:
            if not (isinstance(row, (list, tuple)) and len(row) == 3):
                raise ValueError(f"{name}.{key}: 每档须为 [上界, 得分, 标签]，拿到 {row!r}")
            upper, points, label = row
            if upper is None:
                if seen_none:
                    raise ValueError(f"{name}.{key}: None 上界只能在末位出现一次")
                seen_none = True
            else:
                if not isinstance(upper, (int, float)):
                    raise ValueError(f"{name}.{key}: 上界须为数值，拿到 {upper!r}")
                if prev_upper is not None and upper <= prev_upper:
                    raise ValueError(f"{name}.{key}: 上界必须严格递增（{prev_upper} → {upper}）")
                prev_upper = upper
            if not isinstance(points, (int, float)):
                raise ValueError(f"{name}.{key}: 得分须为数值，拿到 {points!r}")
            if not isinstance(label, str):
                raise ValueError(f"{name}.{key}: 标签须为字符串")


def load_bands(heat_json: str, earning_json: str, template_heat: dict, template_earning: dict) -> tuple[dict, dict, str]:
    """解析并校验覆盖 JSON。返回 (heat, earning, source)，source ∈ defaults | env_override。

    任一 JSON 解析/校验失败直接抛 ValueError（调用方决定是否中止启动）。
    """
    heat, earning = template_heat, template_earning
    source = "defaults"
    if (heat_json or "").strip():
        heat = json.loads(heat_json)
        _validate_band_shape(heat, template_heat, "heat")
        source = "env_override"
    if (earning_json or "").strip():
        earning = json.loads(earning_json)
        _validate_band_shape(earning, template_earning, "earning")
        source = "env_override"
    if source == "env_override":
        log.info("sentiment bands loaded from env override")
    return heat, earning, source
