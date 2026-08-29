"""复盘方法论：可配置、可版本化。

为什么方法论要脱离代码：**如果复盘框架硬编码在服务里，就无法回答
"哪一种复盘方式更有效"**——因为你没法在同一批历史数据上跑两个版本做对比。
版本化之后，每次报告都带 `methodology_version`，才能统计出
"v2 比 v1 产生的改进项采纳率高多少"。

配置来源优先级：
1. `data/review/methodology/<version>.yaml`（存在则加载）
2. 代码内的 `DEFAULT_METHODOLOGY`（兜底）

新增版本 = 新增一个 yaml 文件，不需要改代码。
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
METHODOLOGY_DIR = REPO_ROOT / "data" / "review" / "methodology"


class DimensionConfig(BaseModel):
    """单个复盘维度的配置。"""

    enabled: bool = True
    weight: float = Field(1.0, ge=0.0, le=1.0)
    min_evidence: int = Field(1, description="证据不足此数则不产出判断，避免空谈")


class Thresholds(BaseModel):
    """规则分析器用到的阈值。全部可配置，便于后续校准。"""

    # 操作评估
    max_daily_trades_for_discipline: int = 5      # 超过则提示过度交易
    slippage_alert_bps: int = 50                  # 成交价偏离委托价超此值告警
    loss_trade_ratio_alert: float = 0.6           # 亏损单占比告警线

    # 系统表现
    signal_hit_rate_floor: float = 0.35           # 信号命中率低于此值判定参数失效
    min_signal_samples: int = 5                   # 样本不足不做失效判定


class MethodologyConfig(BaseModel):
    version: str = "v1"
    note: str = ""
    dimensions: dict[str, DimensionConfig] = Field(default_factory=dict)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    # 哪些维度允许产出 P0 改进项（防止噪音维度霸占注意力）
    p0_allowed_dimensions: list[str] = Field(default_factory=lambda: ["trades", "system"])


DEFAULT_METHODOLOGY = MethodologyConfig(
    version="v1",
    note="骨架版：三个维度等权重，规则分析器",
    dimensions={
        "trades": DimensionConfig(enabled=True, weight=1.0),
        "market": DimensionConfig(enabled=True, weight=1.0),
        "system": DimensionConfig(enabled=True, weight=1.0),
    },
    thresholds=Thresholds(),
)


def load_methodology(version: str = "v1") -> MethodologyConfig:
    """加载指定版本的方法论配置，失败时回退到代码内默认版本。"""
    path = METHODOLOGY_DIR / f"{version}.yaml"
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return MethodologyConfig(**raw)
        except Exception as exc:
            log.warning("methodology %s load failed, fallback to default: %s", version, exc)
    return DEFAULT_METHODOLOGY


def available_versions() -> list[str]:
    if not METHODOLOGY_DIR.exists():
        return ["v1"]
    return sorted(p.stem for p in METHODOLOGY_DIR.glob("*.yaml")) or ["v1"]


def ensure_default_methodology_file() -> None:
    """把默认方法论落盘成 v1.yaml，让用户能直接编辑而不是改代码。"""
    METHODOLOGY_DIR.mkdir(parents=True, exist_ok=True)
    path = METHODOLOGY_DIR / "v1.yaml"
    if path.exists():
        return
    data = DEFAULT_METHODOLOGY.model_dump()
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    log.info("methodology template written: %s", path)
