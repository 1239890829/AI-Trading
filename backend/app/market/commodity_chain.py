"""大宗商品价格 → 板块传导链（一阶价格 + 实测标定）。

⚠️ 定位声明（README 级，先读再改）：
本模块是**规则层**——输入商品期货一阶价格，输出「板块偏向（偏多/偏空/无偏向）」。
链条的存在、方向与强度**全部来自历史实证**（`scripts/verify_commodity_chain.py`），
`weight > 0.0` 才代表「已过实证、可参与判定」。`intuition` 字段只记录**候选假设**
（不改行为、不参与计算），实测否决的链置 `weight=0.0` 并在 `basis` 写明否决依据。

纪律（与 [[KB-DEC-018]]「不得采信直觉链」一致）：
- **「油价↑→石化涨」这类说法是外推假说，不是结论**。本模块只收录过了
  「同期相关 + 分组差 + t 值 + 分年度 + 长滞后累积」检验的链；
- 未通过的链**保留在 SPECS 中并写明否决依据**（而非删除）——保留证据链，
  同时防止后人再次「凭直觉」把它们加回来（[[KB-ENG-40]] 处置三选一之①加标注）；
- 结论表述必须是「偏向 + 依据 + 失效条件」，**不得写成预测**（红线 3）。

## 🔴 实证结论（2026-09-11，n 见各条 basis；这是本模块的设计依据，改前必读）

`scripts/verify_commodity_chain.py` 对 9 条候选链做了三层检验，结论**反直觉但明确**：

| 口径 | 含义 | 实测 |
|---|---|---|
| **同期 h=0** | 商品与板块**同一交易日**的超额关系 | **极强**：corr 0.11~0.43，分组差 t = 4.8~18.8 |
| **隔夜 h=1** | 昨日商品收盘 → 今日板块（盘前可用口径） | **几乎全失效**：t = −0.23~2.55，原油甚至为**负**；分年一致率低至 2/5 |
| **中期 h=5/10/20** | 累积超额（持有 5~20 个交易日） | **仅螺纹钢→钢铁**全程显著（t = 3.39/2.54/2.17） |

**三条硬结论**：
1. **「昨天商品涨 → 今天板块涨」不成立**。因此本模块**不输出隔夜方向研判**，
   `weight` 一律为 0.0（这是实测结果，不是"还没标定"）。
2. 传导**不是不存在，而是时点不对**：同期相关性远高于隔夜 ⇒ 商品与 A 股板块
   在同一天共同反映同一宏观/产业信息（**同日共振**），商品**不具备领先性**。
3. 真正可用的形态是**中期**（周级），且当前只有 **螺纹钢→钢铁**一条通过；
   棉花→纺服是**最强否决**（同期 corr 就已经是 0）。

## 口径（三态，与 `overnight_bias.py` 同族）
- 判不出给 `None` / `skip_reason`，**绝不臆造 0**；
- `bias = 0`（变化落在死区内）是**真信息**（"确无偏向"），与 `skip_reason`（"算不了"）严格区分；
- 死区按各商品**自身尺度**取（实测日变化绝对值中位数），不用统一百分比
  ——原油 1.25% vs 沪铝 0.48% 差 2.6 倍，统一死区会让沪铝永远触发、原油永远不触发；
- 输出分两层：**单日异动事实**（真信息，永远给）+ **中期线索**（仅 `mid_verified=True` 的链给）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# 有效权重下限：低于此值视为「输入不足」，给 None（未判定）而非硬凑一个档位
MIN_ACTIVE_WEIGHT = 0.5
# 聚合分档阈值（有效权重内的加权偏向和）
STANCE_BULL = 0.5
STANCE_BEAR = -0.5
# 商品日线允许的最大滞后（自然日）：期货与 A 股交易日高度重合，超期即视为数据陈旧
MAX_STALE_DAYS = 7

EPISTEMIC_NOTE = (
    "传导链为历史统计关系（实测标定），非因果预测；"
    "商品价→业绩通常滞后 1~2 个季度，短期反映的是情绪而非盈利。"
)
# 本模块最重要的一句话：说清「测出来是什么」，防被读成领先信号（红线 3）
TIMING_NOTE = (
    "实测时点结构：商品与板块以**同日共振**为主（同期分组差 t 达 4.8~18.8），"
    "隔夜口径基本失效（t 多在 ±2 内，原油为负）⇒ 商品价格**不具备领先性**，"
    "本块只报告「已发生的异动」与「历史上该异动的中期表现」，不作隔夜方向预测。"
)
DISCLAIMER = "以上为规则层偏向研判，不构成买卖建议。"


@dataclass(frozen=True)
class CommoditySpec:
    """一条「商品 → 申万行业」候选/已验证链。

    - `code`：新浪连续合约代码（`futures_zh_daily_sina`）
    - `sw_index` / `sw_name`：对照用的申万一级行业指数（实证核验标的，生产判定不用）
    - `intuition`：**候选假设方向**（+1 = 直觉上商品涨利好该行业）；不参与计算
    - `dead_zone`：死区（%），= 该商品日变化绝对值**中位数**（实测）
    - `weight`：**隔夜单日**口径的实测权重；**0.0 = 未过实证，不参与判定**
    - `sign`：**实测生效方向**（+1/-1），与 `intuition` 可能相反
    - `mid_verified`：**中期（累积超额）口径是否通过验证**；通过才可能成为观察线索
    - `mid_edge` / `mid_t` / `mid_n`：中期实测超额（pp）/ t 值 / 样本量
    - `basis`：实测依据（样本量/相关/分组差/t/分年度），由核验脚本产出后回填
    """

    code: str
    label: str
    sw_index: str
    sw_name: str
    intuition: int
    dead_zone: float
    weight: float
    sign: int
    mid_verified: bool
    mid_edge: float | None
    mid_t: float | None
    mid_n: int
    basis: str


# ---------------------------------------------------------------------------
# 候选链（一个行业一条：同一行业多商品的，取实证最强的那一条进 SPECS；
# 其余候选的检验结果见 scripts/verify_commodity_chain.py 的 EXTRA 输出）
# ---------------------------------------------------------------------------
SPECS: tuple[CommoditySpec, ...] = (
    CommoditySpec(
        code="SC0", label="原油", sw_index="801960", sw_name="石油石化",
        intuition=1, dead_zone=1.2453, weight=0.0, sign=1, mid_verified=False,
        mid_edge=None, mid_t=None, mid_n=0,
        basis="实测否决（隔夜）：n=1144，隔夜分组差 −0.024pp / t=−0.23 / 分年 2/5；"
              "长滞后 h5/h10/h20 的 t 分别 0.15/0.92/−0.05，均不显著。"
              "同期 corr0=0.43、gap0=+1.14pp（t=11.3）——**是同日共振，不是领先信号**",
    ),
    CommoditySpec(
        code="CU0", label="沪铜", sw_index="801050", sw_name="有色金属",
        intuition=1, dead_zone=0.6858, weight=0.0, sign=1, mid_verified=False,
        mid_edge=None, mid_t=None, mid_n=0,
        basis="实测否决（隔夜）：n=5239，隔夜分组差 +0.145pp / t=2.55 / 分年 14/22；"
              "中期 h5=+0.256pp(t=1.93)、h10=+0.180pp(t=0.93)、h20=+0.403pp(t=1.44) —— **未过 t≥2**",
    ),
    CommoditySpec(
        code="RB0", label="螺纹钢", sw_index="801040", sw_name="钢铁",
        intuition=1, dead_zone=0.6803, weight=0.0, sign=1, mid_verified=True,
        mid_edge=0.3811, mid_t=3.389, mid_n=4214,
        basis="**唯一通过全部检验**：n=4219，同期 corr0=0.21 / gap0=+0.51pp(t=9.70)；"
              "隔夜 gap1=+0.124pp(t=2.46) 但经济意义不足（扣费即负）；"
              "**中期累积超额连续显著**：h5=+0.381pp(t=3.39)、h10=+0.403pp(t=2.54)、h20=+0.488pp(t=2.17)；"
              "分年 13/18 同向。⇒ 作为「中期观察线索」保留，**不作隔夜方向**",
    ),
    CommoditySpec(
        code="JM0", label="焦煤", sw_index="801950", sw_name="煤炭",
        intuition=1, dead_zone=1.1064, weight=0.0, sign=1, mid_verified=False,
        mid_edge=None, mid_t=None, mid_n=0,
        basis="实测否决：n=3037，隔夜 +0.215pp(t=2.46) 但中期转为不显著（h5 t=0.78、h10 t=−0.17、h20 t=−0.17），"
              "且分年仅 8/13 同向 ⇒ 方向不稳定",
    ),
    CommoditySpec(
        code="LC0", label="碳酸锂", sw_index="801730", sw_name="电力设备",
        intuition=-1, dead_zone=1.3652, weight=0.0, sign=-1, mid_verified=False,
        mid_edge=None, mid_t=None, mid_n=0,
        basis="实测否决：n=761（样本最短），隔夜 −0.086pp(t=−0.66)；中期 h5/h10/h20 的 t 分别 0.43/0.52/1.08。"
              "注意**直觉方向（锂价跌→电池成本降→利好）未被证实也未证伪**——样本不足以裁决",
    ),
    CommoditySpec(
        code="LH0", label="生猪", sw_index="801010", sw_name="农林牧渔",
        intuition=1, dead_zone=0.7806, weight=0.0, sign=1, mid_verified=False,
        mid_edge=None, mid_t=None, mid_n=0,
        basis="实测否决：n=1365，隔夜 +0.109pp(t=1.11)；中期全部趋零（h5 t=0.43、h10 t=0.19、h20 t=−0.25）",
    ),
    CommoditySpec(
        code="FG0", label="玻璃", sw_index="801710", sw_name="建筑材料",
        intuition=1, dead_zone=0.8407, weight=0.0, sign=1, mid_verified=False,
        mid_edge=None, mid_t=None, mid_n=0,
        basis="实测否决：n=3028，隔夜 +0.044pp(t=0.83)；中期 h5 t=0.09、h10 t=1.30、h20 t=0.25，均不显著",
    ),
    CommoditySpec(
        code="CF0", label="棉花", sw_index="801130", sw_name="纺织服饰",
        intuition=1, dead_zone=0.4917, weight=0.0, sign=1, mid_verified=False,
        mid_edge=None, mid_t=None, mid_n=0,
        basis="实测否决（最强否决）：n=5237，同期 corr0=−0.0009 就已经是 0；"
              "隔夜 +0.053pp(t=1.35)，中期全为负（h10 t=−1.02）⇒ **棉价与纺服板块无任何可辨识关系**",
    ),
    CommoditySpec(
        code="RU0", label="橡胶", sw_index="801890", sw_name="机械设备",
        intuition=1, dead_zone=0.9390, weight=0.0, sign=1, mid_verified=False,
        mid_edge=None, mid_t=None, mid_n=0,
        basis="实测否决：n=3034，同期 corr0 仅 0.039（gap0 t=3.08 但方向在隔夜即翻负 −0.021pp）；"
              "中期 h5 t=0.97、h10 t=1.12、h20 t=0.62 ⇒ 不纳入",
    ),
)

_BY_CODE: dict[str, CommoditySpec] = {s.code: s for s in SPECS}


def change_of(spec: CommoditySpec, prev_value: float, last_value: float) -> float:
    """口径单点收口：商品日变化（%）。

    核验脚本与生产判定**必须同调此函数**，否则「核验的规则」与「线上跑的规则」
    会悄悄分叉（[[KB-ENG-44]]）。商品价格是绝对价（元/吨），无需 bp 换算。
    """
    if not prev_value:
        return 0.0
    return (last_value / prev_value - 1.0) * 100.0


def _zone_sign(spec: CommoditySpec, change: float) -> int:
    """变化量 → 死区分档符号：+1 涨破死区 / -1 跌破死区 / 0 死区内（真信息）。"""
    if change > spec.dead_zone:
        return 1
    if change < -spec.dead_zone:
        return -1
    return 0


def _points(raw: object) -> list[tuple[str, float]]:
    """归一化取数结果 → [(date_str, close)]，丢弃不可解析行（源脏数据不参与）。

    兼容两种形态：``{"date":…,"close":…}``（akshare_ext 返回）与 ``(date, close)`` 序列
    （单测/核验脚本直给）——两种都吃，避免消费方各写一层转换。
    """
    out: list[tuple[str, float]] = []
    for row in raw or []:  # type: ignore[union-attr]
        if isinstance(row, dict):
            d, v = row.get("date"), row.get("close")
        else:
            try:
                d, v = row[0], row[1]
            except (TypeError, IndexError, KeyError):
                continue
        try:
            ds = str(d)[:10]
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv <= 0 or not ds:
            continue
        out.append((ds, fv))
    out.sort(key=lambda x: x[0])
    return out


def _parse_date(s: str):  # noqa: ANN202  —— 仅内部使用，返回 date | None
    from datetime import date as _date

    try:
        y, m, d = s[:10].split("-")
        return _date(int(y), int(m), int(d))
    except Exception:  # noqa: BLE001  脏日期一律当缺失
        return None


def evaluate(
    series: dict[str, object],
    *,
    asof=None,
    specs: tuple[CommoditySpec, ...] | None = None,
) -> dict:
    """商品日线序列 → 板块偏向研判（纯函数，零 IO）。

    `series`：`{code: [(date_str, close), ...]}`；缺某 code 即视为该路源不可得。
    `asof`：判定基准日（通常是「今天」）。**严格使用 date < asof 的最后两根已收盘日线**
    ——盘中拉到的当日 bar 是未完成 bar，纳入即构成前视（与 overnight_bias 同款守卫）。
    `specs`：可注入（默认为生产 `SPECS`），仅用于测试隔离。

    返回：`{rows, score, score_range, active_weight, stance, unjudged_reason, skipped}`。
    """
    from datetime import date as _date

    use_specs = SPECS if specs is None else specs
    by_code = {s.code: s for s in use_specs}

    if asof is None:
        asof = _date.today()
    elif not isinstance(asof, _date):
        asof = _parse_date(str(asof)) or _date.today()

    rows: list[dict] = []
    covered: set[str] = set()

    for spec in use_specs:
        row: dict = {
            "code": spec.code,
            "label": spec.label,
            "sw_name": spec.sw_name,
            "weighted": spec.weight > 0.0,
            "intuition": spec.intuition,
            "sign": spec.sign,
            "basis": spec.basis,
            "mid_verified": spec.mid_verified,
            "mid_edge": spec.mid_edge,
            "mid_t": spec.mid_t,
            "mid_n": spec.mid_n,
        }
        pts = [(d, v) for d, v in _points(series.get(spec.code)) if (_parse_date(d) or asof) < asof]
        if not pts:
            row.update(skip_reason="源不可得", change=None, zone="unknown", bias=None)
            rows.append(row)
            continue
        if len(pts) < 2:
            row.update(
                skip_reason="有效点不足（<2）",
                date=pts[-1][0], value=pts[-1][1], change=None, zone="unknown", bias=None,
            )
            rows.append(row)
            continue

        (pd_, pv), (ld_, lv) = pts[-2], pts[-1]
        change = round(change_of(spec, pv, lv), 4)
        covered.add(spec.code)

        stale_days = None
        pdate = _parse_date(ld_)
        if pdate is not None:
            stale_days = (asof - pdate).days
        if stale_days is not None and stale_days > MAX_STALE_DAYS:
            row.update(
                date=ld_, value=lv, prev_value=pv, change=change,
                zone="unknown", bias=None,
                skip_reason=f"数据滞后（最新有效点距今 {stale_days} 天）",
            )
            rows.append(row)
            continue

        z = _zone_sign(spec, change)
        # 偏向 = 死区分档符号 × 实测生效方向（不是直觉方向）
        bias = z * spec.sign if z else 0
        # 中期线索：仅当「实测通过中期验证」且「本次确有异动（不在死区内）」才成立
        mid_signal = bool(spec.mid_verified and z != 0)
        row.update(
            date=ld_,
            prev_date=pd_,
            value=lv,
            prev_value=pv,
            change=change,
            dead_zone=spec.dead_zone,
            zone={1: "涨破死区", -1: "跌破死区", 0: "死区内"}[z],
            bias=bias,
            mid_signal=mid_signal,
            mid_bias=(z * spec.sign) if mid_signal else None,
            note=(
                "变化在死区内，不给方向"
                if not z
                else (
                    f"中期线索：商品{'涨' if z > 0 else '跌'}破死区，"
                    f"该行业历史中期超额 {spec.mid_edge:+.3f}pp（t={spec.mid_t}，n={spec.mid_n}）"
                    if mid_signal
                    else "商品有异动，但该链**未通过实证**，不给板块含义"
                )
            ),
        )
        rows.append(row)

    active = [r for r in rows if r["weighted"] and r.get("bias") is not None]
    active_weight = 0.0
    score = 0.0
    for r in active:
        w = by_code[r["code"]].weight
        r["contribution"] = round(w * r["bias"], 4)
        active_weight += w
        score += r["contribution"]
    score = round(score, 4)

    if active_weight < MIN_ACTIVE_WEIGHT:
        stance = None
        unjudged = (
            f"隔夜口径实测不成立（有效权重 {active_weight:.2f} < {MIN_ACTIVE_WEIGHT}）"
            "——见模块 docstring 实证结论，本模块不输出隔夜方向"
        )
    else:
        unjudged = None
        stance = "偏多" if score >= STANCE_BULL else "偏空" if score <= STANCE_BEAR else "无偏向"

    mid_signals = [
        {
            "code": r["code"], "label": r["label"], "sw_name": r["sw_name"],
            "change": r["change"], "zone": r["zone"],
            "edge_pct": r["mid_edge"], "edge_t": r["mid_t"], "sample_n": r["mid_n"],
        }
        for r in rows if r.get("mid_signal")
    ]

    return {
        "rows": rows,
        "score": score,
        "score_range": f"±{round(active_weight, 2)}",
        "active_weight": round(active_weight, 2),
        "stance": stance,
        "unjudged_reason": unjudged,
        "mid_signals": mid_signals,
        "timing_note": TIMING_NOTE,
        "covered": sorted(covered),
        "as_of": asof.isoformat(),
        "epistemic_note": EPISTEMIC_NOTE,
        "disclaimer": DISCLAIMER,
    }


async def collect(asof=None) -> dict | None:
    """四路取数（并发）→ evaluate。单路失败只记 `skip_reason`，不影响其余路。

    返回 None 表示**一路都没拿到**（整块不渲染，不谎称"今日无偏向"）。
    """
    import asyncio

    from app.services.akshare_ext import get_akshare_ext

    ext = get_akshare_ext()

    async def _one(code: str):
        try:
            return code, await ext.commodity_daily(code)
        except Exception as exc:  # noqa: BLE001  单路失败降级，不拖垮整体
            log.warning("commodity_chain: fetch %s failed: %s", code, exc)
            return code, []

    results = await asyncio.gather(*[_one(s.code) for s in SPECS])
    series = dict(results)
    if not any(series.values()):
        return None
    return evaluate(series, asof=asof)
