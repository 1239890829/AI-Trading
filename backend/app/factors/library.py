"""因子注册表（唯一口径锚）——docs/factor-library-design.md §5。

每个因子是作用在统一 base 视图上的 SQL 表达式（evaluate.py 生成 base）：
- base 提供：close_adj / ret1 / c5..c120（复权 LAG 锚点）/ fwd_*（前瞻收益）/
  窗口统计列（vola/amihud/range/amt）/ pc1_raw（昨日未复权收盘）/ cnt（每股累计行数）；
- expr 只引用 base 列；NULL 守卫（窗口样本不足、除权污染）在表达式内完成，
  缺失一律 NULL（三态），绝不凑 0；
- min_bars = 该因子可计算的每股最少历史行数（初筛守卫，防止次新噪声）；
- note 记录口径与预期方向——预期方向仅供解读，判定以实测 IC 为准。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FactorDef:
    name: str
    category: str
    min_bars: int
    expr: str
    note: str


#: 前瞻收益窗口（交易日）：exec = T+1 收盘进、T+h 收盘出（保守执行口径）；
#: close_1 = T 收盘进、T+1 收盘出（仅作隔夜跳空损耗对比，不作判定依据）。
HORIZONS_EXEC: tuple[int, ...] = (3, 5, 10, 20)
HORIZON_CLOSE: int = 1

#: 隔夜跳空除权污染过滤阈值：A 股合法日内跳空上限 ±20%（创业/科创），
#: |gap| 超过 25% 只能是除权缺口（如 10送10 ≈ -50%）→ 视为缺失而非信号。
GAP_FILTER_ABS = 0.25

FACTORS: tuple[FactorDef, ...] = (
    FactorDef(
        "mom5", "momentum", 6,
        "close_adj / c5 - 1",
        "5 日动量（复权收盘）。A 股短周期动量常见反转效应，预期 |IC| 或为负——负 IC 亦是信息。",
    ),
    FactorDef(
        "mom10", "momentum", 11,
        "close_adj / c10 - 1",
        "10 日动量。",
    ),
    FactorDef(
        "mom20", "momentum", 21,
        "close_adj / c20 - 1",
        "20 日动量。",
    ),
    FactorDef(
        "mom60", "momentum", 61,
        "close_adj / c60 - 1",
        "60 日动量（季线动量，欧奈尔 RPS 近亲）。",
    ),
    FactorDef(
        "mom120", "momentum", 121,
        "close_adj / c120 - 1",
        "120 日动量（半年）。",
    ),
    FactorDef(
        "vola20", "volatility", 21,
        "vola20_w",
        "20 日日收益标准差（复权）。低波动异象：预期负 IC。窗口样本 <20 根 → NULL。",
    ),
    FactorDef(
        "amt_ratio", "volume", 22,
        "turnover / NULLIF(amt20_prev, 0)",
        "成交额量比：当日成交额 / 前 20 日均额（基期不含当日）。放量确认类。",
    ),
    FactorDef(
        "liq20", "liquidity", 21,
        "amt20_cur",
        "20 日均成交额（含当日）。流动性溢价：预期负 IC（大额=拥挤）。"
        "RankIC 对单调变换不敏感，不取对数。",
    ),
    FactorDef(
        "amihud20", "liquidity", 21,
        "amihud20_w",
        "Amihud 非流动性：20 日均值(|日收益|/成交额)。非流动性溢价：预期正 IC。"
        "窗口内有效样本 <20 → NULL。",
    ),
    FactorDef(
        "gap", "price_structure", 2,
        "CASE WHEN abs(open_price / NULLIF(pc1_raw, 0) - 1) > "
        f"{GAP_FILTER_ABS} THEN NULL ELSE open_price / NULLIF(pc1_raw, 0) - 1 END",
        "隔夜跳空（今开/昨收-1，未复权口径）。|gap|>25% 判为除权缺口 → NULL（不判为信号）。"
        "已知局限：小额分红（<1%）污染保留，见设计文档 §1.4。",
    ),
    FactorDef(
        "range20", "price_structure", 21,
        "range20_w",
        "20 日平均日内振幅 (high-low)/close（未复权，除权日单点污染由 20 均稀释）。"
        "窗口有效样本 <20 → NULL。",
    ),
)

#: 注册表按名索引
FACTOR_BY_NAME: dict[str, FactorDef] = {f.name: f for f in FACTORS}
