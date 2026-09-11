"""P1-34 隔夜海外输入 → 大盘方向偏向（规则层：走强 / 中性 / 承压）。

⚠️ **红线 3**：本模块产出的是**偏向 + 依据 + 失效条件**，不是预测，也不是买卖结论。
任何消费方（盘前简报 / 前端）必须原样带上 `disclaimer` 与 `invalidation`。

━━ 实证依据（2026-09-11 实测）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
核验脚本 `backend/scripts/verify_overnight_bias.py` —— **它 import 本模块常量**，
因此「文档里引用的数字」与「线上跑的规则」不可能漂移（改常量即改核验口径）。

样本：2014-11-11 ~ 2026-09-10，2879 个 A 股交易日。
对齐口径：A 股 T 日取「日期**严格小于** T」的最新一行
（美股 T−1 收盘于北京时间 T 日凌晨；离岸 CNH 为 24h 交易，按日线标签近似）。

① 单输入 vs 上证**开盘跳空**（pearson / spearman）
   纳指 +0.407/+0.491 · 费半 +0.363/+0.444 · 离岸 CNH −0.219/−0.256 · 美债10Y +0.060/+0.065
② 同为 vs **全天涨跌**：纳指 +0.169 · 费半 +0.156 · CNH −0.102 · 美债 +0.022
   ⇒ **隔夜信息主要在开盘一次性定价**，全天口径解释力大幅衰减（见 `HORIZON_NOTE`）
③ 组合（**本模块规则**，直接调 `evaluate` 回放）：
   走强 19.3% / 中性 64.2% / 承压 16.5%（未判定 0）；走强组全天 +0.302%（胜率 62.1%）、
   承压组 −0.415%（胜率 40.2%）⇒ **走强−承压 全天 +0.717pp（t=+6.03 / −5.66）**；
   开盘口径 +0.738pp（开盘胜率 70.5% vs 12.0%）。分年度 **12/12 年方向一致**。
④ **美债 10Y 权重置 0（实测否决）**：|corr| ≤ 0.07、同口径分组差仅 +0.10pp（t≈1.6，
   未达常规显著水平；纳指同口径 +0.54pp）⇒ **仅记录、不参与方向判定**。
   『美债利率↑ → A 股承压』在本样本上未获支持，不得因叙事顺畅而给它加权（[[KB-DEC-018]]）。

━━ 死区与权重为什么这样取（**不得随意调**；调整须重跑核验脚本并更新本节）━━━━━━
死区 = 各输入 2015-2026 实测**日变化绝对值中位数**，而非统一百分比：
纳指日 σ 1.353% 与 CNH 0.296% 相差 4.6 倍，统一死区会把 CNH 判成「恒平」。
实测敏感性（升−降 全天均值差，各按自身死区缩放）：
纳指 0.5×→+0.44pp / 1×→+0.54pp / 2×→+0.71pp；费半同理；**越大越清晰但样本越少**
⇒ 取中位分档，兼顾区分度与样本量。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime

#: 相邻两个有效点间隔超过该自然日数即判「数据滞后」，按缺失处理（覆盖周末与美股长假）
MAX_GAP_DAYS = 5

#: 三态判定所需的最小有效权重和（< 该值一律「未判定」，绝不用少数输入硬凑方向）
MIN_ACTIVE_WEIGHT = 2.0

#: 走强 / 承压的分数门槛
STANCE_UP = 2.0
STANCE_DOWN = -2.0


@dataclass(frozen=True)
class InputSpec:
    key: str
    label: str
    unit: str  # "%"（涨跌幅）| "bp"（绝对变化）
    dead_zone: float  # 与 unit 同尺度
    weight: float  # 0 = 仅展示、不参与方向判定
    rising_bullish: bool  # 数值上升是否对 A 股有利
    note: str


#: 展示顺序 = 有信息输入在前、仅记录项在后
SPECS: tuple[InputSpec, ...] = (
    InputSpec(
        key="nasdaq",
        label="纳斯达克指数",
        unit="%",
        dead_zone=0.65,  # 实测中位|Δ|=0.657%（日σ 1.353%）
        weight=1.0,
        rising_bullish=True,
        note="隔夜科技风险偏好；与 A 股开盘跳空相关度最高（r=+0.41）",
    ),
    InputSpec(
        key="sox",
        label="费城半导体指数",
        unit="%",
        dead_zone=1.10,  # 实测中位|Δ|=1.090%（日σ 2.116%）
        weight=1.0,
        rising_bullish=True,
        note="半导体链映射，对 A 股科技/算力相关度高于大盘（r=+0.36）",
    ),
    InputSpec(
        key="usdcnh",
        label="离岸人民币（USDCNH）",
        unit="%",
        dead_zone=0.145,  # 实测中位|Δ|=0.143%（日σ 0.296%）
        weight=1.0,
        rising_bullish=False,  # 数值上升 = 人民币贬值 = 不利
        note="人民币贬值（数值↑）对 A 股偏不利（r=−0.22）",
    ),
    InputSpec(
        key="us10y",
        label="美国10年期国债收益率",
        unit="bp",
        dead_zone=3.0,  # 实测中位|Δ|=3.00bp（日σ 5.29bp）
        weight=0.0,  # ← 实测否决，仅记录
        rising_bullish=False,
        note="**实测边际信息很弱**（r=+0.06、分组差 +0.10pp、t≈1.6 不显著）→ 仅记录、不参与方向判定",
    ),
)

#: 失效条件（随判定一起下发；消费方不得省略）
INVALIDATION: tuple[str, ...] = (
    "本偏向**主要解释开盘跳空**：实测开盘口径的方向区分度远大于全天口径，不得当作全天涨跌预测。",
    "当日出现国内重大政策、资金面或事件冲击时，外围输入的边际解释力让位于国内因素。",
    "『市场自身状态』判据（情绪相位 / 空仓闸门）优先于本偏向；两者冲突时以市场自身状态为准。",
    f"任一输入数据滞后超过 {MAX_GAP_DAYS} 个自然日（美股长假 / 源缺），即按缺失处理，不沿用旧值。",
)

HORIZON_NOTE = (
    "隔夜外围给出的是『开盘情绪偏向』：实测对开盘跳空方向区分度显著，"
    "对全天涨跌的均值差衰减到约 0.5pp——不要外推为全天方向。"
)

DISCLAIMER = "偏向 + 依据 + 失效条件，不构成买卖建议。"

#: 收益率源以**百分数**报价（4.83 表示 4.83%），换算成 bp 需 ×100。
#: 少了这一步会让美债 10Y 的日变化恒为 0.03「bp」量级 ⇒ 永远判「平」
#: （2026-09-11 由核验脚本的自检行抓出，属真实缺陷）。
_BP_PER_PCT = 100.0


def change_of(spec: InputSpec, prev_value: float, last_value: float) -> float:
    """按 spec 的 unit 算变化量：bp 用绝对差（×100 换算）、% 用涨跌幅。

    **单点收口**：模块内部与核验脚本都调本函数，避免两处口径漂移。
    """
    if spec.unit == "bp":
        return (last_value - prev_value) * _BP_PER_PCT
    return (last_value / prev_value - 1) * 100.0 if prev_value else 0.0


def _parse_date(v) -> date_cls | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date_cls):
        return v
    if not v:
        return None
    try:
        return date_cls.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _series_points(rows) -> list[tuple[date_cls, float]]:
    """把 [{date, close}] 归一为 [(date, value)]，丢弃无法解析的行（三态：不臆造）。"""
    pts: list[tuple[date_cls, float]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        d = _parse_date(r.get("date"))
        v = r.get("close")
        if d is None or v is None:
            continue
        try:
            pts.append((d, float(v)))
        except (TypeError, ValueError):
            continue
    pts.sort(key=lambda x: x[0])
    return pts


def evaluate(series: dict[str, list[dict]], *, brief_date: date_cls | None = None) -> dict:
    """纯函数：由四路原始日序列算出「走强 / 中性 / 承压」偏向。

    ``series`` 每项为 ``[{"date": "YYYY-MM-DD", "close": 数值}, ...]``（升序，可含多余历史）。
    **空列表 = 源不可得**；不足两点 = 数据不足。两者都进 ``missing``，绝不臆造 0。
    """
    evidence: list[dict] = []
    missing: list[str] = []
    active_weight = 0.0
    score = 0.0

    for spec in SPECS:
        pts = _series_points(series.get(spec.key))
        row: dict = {
            "key": spec.key,
            "label": spec.label,
            "unit": spec.unit,
            "weighted": spec.weight > 0,
            "note": spec.note,
            "date": None,
            "value": None,
            "prev_value": None,
            "change": None,
            "zone": None,
            "bullish": None,
        }
        if len(pts) < 2:
            reason = "源不可得" if not pts else "有效点不足（<2）"
            row["skip_reason"] = f"{reason}"
            missing.append(f"{spec.label}：{reason}")
            evidence.append(row)
            continue

        (prev_d, prev_v), (last_d, last_v) = pts[-2], pts[-1]
        row["date"], row["value"], row["prev_value"] = last_d.isoformat(), last_v, prev_v

        # 前视守卫：隔夜数据必须早于报告日（A 股当日开盘前只能看到更早的收盘）
        if brief_date is not None and last_d >= brief_date:
            row["skip_reason"] = "对齐异常（隔夜数据不早于报告日，疑似前视）"
            missing.append(f"{spec.label}：对齐异常（数据日期 {last_d} ≥ 报告日 {brief_date}）")
            evidence.append(row)
            continue

        gap = (last_d - prev_d).days
        if gap > MAX_GAP_DAYS:
            row["skip_reason"] = f"数据滞后（相邻有效点间隔 {gap} 天）"
            missing.append(f"{spec.label}：数据滞后（间隔 {gap} 天）")
            evidence.append(row)
            continue

        change = change_of(spec, prev_v, last_v)
        row["change"] = round(change, 4)

        if change > spec.dead_zone:
            direction, zone = 1, "升"
        elif change < -spec.dead_zone:
            direction, zone = -1, "降"
        else:
            direction, zone = 0, "平"
        row["zone"] = zone
        bullish = direction * (1 if spec.rising_bullish else -1)
        row["bullish"] = None if direction == 0 else bool(bullish > 0)

        if spec.weight > 0:
            active_weight += spec.weight
            score += spec.weight * bullish
        evidence.append(row)

    total_weight = sum(s.weight for s in SPECS if s.weight > 0)
    result: dict = {
        "score": None,
        "score_range": f"±{int(total_weight)}",
        "available_weight": active_weight,
        "stance": None,
        "unjudged_reason": None,
        "evidence": evidence,
        "missing": missing,
        "invalidation": list(INVALIDATION),
        "horizon_note": HORIZON_NOTE,
        "disclaimer": DISCLAIMER,
        "as_of": {e["key"]: e["date"] for e in evidence if e["date"]},
    }

    if active_weight < MIN_ACTIVE_WEIGHT:
        result["unjudged_reason"] = (
            f"有信息输入不足（有效权重 {active_weight:g} < {MIN_ACTIVE_WEIGHT:g}）→ 未判定"
        )
        return result

    result["score"] = score
    if score >= STANCE_UP:
        result["stance"] = "走强"
    elif score <= STANCE_DOWN:
        result["stance"] = "承压"
    else:
        result["stance"] = "中性"
    return result


async def collect(brief_date: date_cls | None = None) -> dict:
    """取数 + 判定（IO 段）。任一路失败只记 missing，不影响其余输入。"""
    import logging

    from app.services.akshare_ext import get_akshare_ext

    log = logging.getLogger(__name__)
    ext = get_akshare_ext()
    series: dict[str, list[dict]] = {}
    fetchers = {
        "nasdaq": lambda: ext.us_index_daily(".IXIC"),
        "sox": lambda: ext.us_index_daily(".SOX"),
        "usdcnh": ext.usdcnh_daily,
        "us10y": ext.us_treasury_10y_daily,
    }
    for key, fn in fetchers.items():
        try:
            series[key] = await fn()
        except Exception as exc:  # 三态：源不可得 → 空列表，由 evaluate 记 missing
            log.warning("overnight inputs: %s failed: %s", key, exc)
            series[key] = []
    return evaluate(series, brief_date=brief_date)


__all__ = ["SPECS", "InputSpec", "INVALIDATION", "HORIZON_NOTE", "DISCLAIMER",
           "MAX_GAP_DAYS", "MIN_ACTIVE_WEIGHT", "STANCE_UP", "STANCE_DOWN",
           "evaluate", "collect"]
