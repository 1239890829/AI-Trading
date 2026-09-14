"""因子注册表（唯一口径锚）——docs/summary/factor-system.md §5。

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
    # ---------------------------------------------------------------- 2026-09-07 扩展批次
    # 来源：qlib Alpha158（microsoft/qlib contrib/data/loader.py，Apache-2.0）+ TA-Lib。
    # 挖掘过程见 docs/factor-lifecycle-governance.md §候选登记册。
    # kbar 族（qlib 原式，除权日单点污染由后续窗口稀释；一字板分母 0 → NULL 三态）：
    FactorDef(
        "kmid", "kbar", 1,
        "(close_price - open_price) / NULLIF(open_price, 0)",
        "qlib KMID：日内实体占比 (close-open)/open。阳线实体越长买方越强。",
    ),
    FactorDef(
        "klen", "kbar", 1,
        "(high_price - low_price) / NULLIF(open_price, 0)",
        "qlib KLEN：日振幅/开盘。日内博弈激烈度。",
    ),
    FactorDef(
        "kmid2", "kbar", 1,
        "(close_price - open_price) / NULLIF(high_price - low_price, 0)",
        "qlib KMID2：实体/全距，[-1,1]。方向确定性度量（一字板 → NULL）。",
    ),
    FactorDef(
        "kup", "kbar", 1,
        "(high_price - greatest(open_price, close_price)) / NULLIF(open_price, 0)",
        "qlib KUP：上影线长度。上方抛压。",
    ),
    FactorDef(
        "kup2", "kbar", 1,
        "(high_price - greatest(open_price, close_price)) / NULLIF(high_price - low_price, 0)",
        "qlib KUP2：上影线/全距。",
    ),
    FactorDef(
        "klow", "kbar", 1,
        "(least(open_price, close_price) - low_price) / NULLIF(open_price, 0)",
        "qlib KLOW：下影线长度。下方承接。",
    ),
    FactorDef(
        "klow2", "kbar", 1,
        "(least(open_price, close_price) - low_price) / NULLIF(high_price - low_price, 0)",
        "qlib KLOW2：下影线/全距。",
    ),
    FactorDef(
        "ksft", "kbar", 1,
        "(2 * close_price - high_price - low_price) / NULLIF(open_price, 0)",
        "qlib KSFT：收盘居中偏移量（2c-h-l）/open，正=收在高位。",
    ),
    FactorDef(
        "ksft2", "kbar", 1,
        "(2 * close_price - high_price - low_price) / NULLIF(high_price - low_price, 0)",
        "qlib KSFT2：收盘居中偏移量/全距，与 kmid2 高度相关（族内去重预判）。",
    ),
    # 趋势回归族（qlib BETA/RSQR/RESI 算子，regr_* 窗口聚合实现）：
    FactorDef(
        "beta20", "trend", 21,
        "slope20 / NULLIF(close_adj, 0)",
        "qlib BETA20：20 日对数序列线性回归斜率/现价（日化趋势强度）。>0 单边上行。",
    ),
    FactorDef(
        "rsqr20", "trend", 21,
        "rsqr20",
        "qlib RSQR20：20 日回归 R²。趋势线性度（0=震荡 1=单边），与 beta20 联用刻画趋势质量。",
    ),
    FactorDef(
        "resi20", "trend", 21,
        "(close_adj - (icept20 + slope20 * rn)) / NULLIF(close_adj, 0)",
        "qlib RESI20：20 日回归残差/现价。偏离趋势线的程度（残差回归均值逻辑）。",
    ),
    FactorDef(
        "bias20", "trend", 21,
        "(close_adj - ma20_adj) / NULLIF(ma20_adj, 0)",
        "TA-Lib/海通 BIAS20：20 日乖离率。与 mom20 同信源（去重预判高）。",
    ),
    # 波动族：
    FactorDef(
        "std20", "volatility", 21,
        "std20_w",
        "qlib STD20：20 日复权收盘价标准差/现价。与 vola20（收益波动）同族（去重预判高）。",
    ),
    FactorDef(
        "atr14", "volatility", 15,
        "atr14_w / NULLIF(close_price, 0)",
        "TA-Lib ATR14 归一：14 日真实波幅均值/收盘。TR 隔夜跳空 >±25% 判除权断层 → NULL（与 gap 同口径）；14 个 TR 全有效才给值。",
    ),
    # 位置/突破族（qlib RSV/IMAX/MAX/RANK）：
    FactorDef(
        "rsv20", "price_structure", 21,
        "(close_price - min20_l) / NULLIF(max20_h - min20_l, 0)",
        "qlib RSV20：收盘在 20 日高低区间位置（KDJ 原始值，未复权口径；除权日污染单点）。",
    ),
    FactorDef(
        "imax20", "price_structure", 21,
        "(rn - imax_rn20) / 20.0",
        "qlib IMAX20（Aroon 语义）：20 日最高价出现位置，0=今日新高 1=高点在 20 日前。强动量 → 低值。停牌安全（行数差非日历差）。",
    ),
    FactorDef(
        "max20", "price_structure", 21,
        "max20_h / NULLIF(close_price, 0)",
        "qlib MAX20：20 日最高价/现价。1=创新高（海龟突破近亲），>1=回撤深度。",
    ),
    FactorDef(
        "rank20", "price_structure", 21,
        "rank20_w",
        "qlib RANK20：现价在 20 日**滚动窗口内**的百分位排名——"
        "**不是截面排名**（qlib `Rank($close,d)` = `rolling(d).rank(pct=True)`）。"
        "并列取**平均名次**（pandas 默认 `average`，即 qlib 主路径；qlib 的 scipy 回退路径走 "
        "`percentileofscore(kind='rank')` 即 `<=` 口径，二者仅并列时不同，此处钉平均名次）。"
        "窗口有效样本 <20 或 close_adj 缺失 → NULL。",
    ),
    # 动量统计族：
    FactorDef(
        "cntp20", "momentum", 21,
        "cntp20",
        "qlib CNTP20：20 日上涨天数占比。频率动量（vs 幅度动量 mom20）。",
    ),
    FactorDef(
        "sump20", "momentum", 21,
        "sump20_num / NULLIF(sump20_den, 0)",
        "qlib SUMP20（RSI 同构）：20 日总涨幅/总波幅。上行能量占比；与 RSI 14 同族不同窗。",
    ),
    # 价量关系族（qlib CORR/CORD 算子，唯一新信息源类别）：
    FactorDef(
        "corr_pv20", "volume_price", 21,
        "corr_pv20",
        "qlib CORR20：corr(收盘价, ln(成交量))，20 日。价量同向（正）=量价齐升/齐跌，负=背离。",
    ),
    FactorDef(
        "cord20", "volume_price", 21,
        "cord20_raw",
        "qlib CORD20：corr(日收益, ln(量比))，20 日。放量上涨/缩量下跌的一致性——经典量价确认因子。",
    ),
    # 量能族：
    FactorDef(
        "vma20", "volume", 21,
        "vma20_raw / NULLIF(volume, 0)",
        "qlib VMA20：20 日均量/今日量（>1 今日缩量）。与 amt_ratio 同族（量 vs 额，去重预判高）。",
    ),
    FactorDef(
        "vstd20", "volume", 21,
        "vstd20_raw / NULLIF(volume, 0)",
        "qlib VSTD20：20 日量标准差/今日量。量能稳定性。",
    ),
    FactorDef(
        "wvma20", "volume", 21,
        "wvma20_num / NULLIF(wvma20_den, 0)",
        "qlib WVMA20：量加权价格波动的变异系数。波动×量的复合刻画。",
    ),
    FactorDef(
        "vsumd20", "volume", 21,
        "(vol_pos20 - vol_neg20) / NULLIF(vol_pos20 + vol_neg20, 0)",
        "qlib VSUMD20：量的 RSI（20 日增量/减量净占比）。量能方向。",
    ),
)

#: 注册表按名索引
FACTOR_BY_NAME: dict[str, FactorDef] = {f.name: f for f in FACTORS}
