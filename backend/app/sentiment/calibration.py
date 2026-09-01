"""情绪阈值的历史分位校准（plan-review P0-3b）。

## 为什么必须校准——实测证据（2026-09-02，回补 120 个交易日的命中率）

用**绝对阈值**判定情绪是本项目最隐蔽的一类失效：不报错、界面照常、结论系统性偏移。
把 `engine.HEAT_BANDS/EARNING_BANDS` 打到 2026-03-11 → 2026-09-01 的 120 天真实样本上：

| 指标 | 档位 | 命中率 | 判定 |
|---|---|---|---|
| `promo_1to2` | ≥40%（+2） | **0.0%** | **死档** |
| `promo_1to2` | 25–40%（+1） | 3.3% | 近乎死档 |
| `promo_1to2` | 两个负分档合计 | **96.7%** | 该指标≈恒定 −2 |
| `limit_up` | <25 家（−1） | **0.0%** | **死档**（"冰点"永不由此触发） |
| `max_board` | ≤2 板（−1） | 0.8% | 近乎死档 |
| `break_rate` | 三档 | 26.7 / 55.8 / 17.5% | 尚可 |

`promo_1to2` 被文档称为"业界公认最敏感的接力指标"，但在本项目的实际分布下
**96.7% 的日子都落在负分档**——它已退化成一个近乎恒定的 −2，把赚钱效应轴
系统性压低，这直接解释了为什么近期判定总是落在「偏弱 / 亏钱」。

（`break_rate` 的分布其实还行，说明问题不是"所有阈值都错"，而是
**业界的绝对经验值对不上本项目的实际分布**——这正是要按本地分位校准的理由。）

## 校准口径

分档上界取历史样本的**等分分位数**：k 档 → 在 p(100/k), p(200/k)… 处切 k−1 刀。
这样每一档各占约 1/k 的历史日子，阈值随市场中枢自动漂移，不再死锁在某几个经验值上。

### ⚠️ 已知代价：分位是相对的，长期熊市会掩盖风险

纯分位校准意味着**无论多差，总有 1/k 的日子落在最高档**。在持续下行市里，
阶段标签会一直显示"发酵/高潮"，掩盖真实风险。

这不是 bug 而是口径取舍，缓解手段（本模块均提供原始材料，由调用方决定怎么用）：
1. **同时暴露分位数值本身**——`percentile_of()` 给出"今天处在历史什么位置"，
   用户看分位比看标签可靠
2. basis 完整可审计（样本量 / 窗口 / 各分位切点），异常一眼可见
3. 保留 `band_config` 的 env override 通道，可强制回到经验值

## 覆盖范围

**只能校准能用历史涨停池/炸板池回算的指标**：`limit_up` / `max_board` / `break_rate` /
`promo_1to2` / `promo_2to3`。
`median_pct` / `red_rate` / `limit_down` 依赖**历史全市场行情快照**（今日涨跌幅），
本地没有、也无法从涨停池反推 → **不校准**，沿用经验值并在 basis 中显式标注
`calibrated=False`，绝不假装校准过。
"""
from __future__ import annotations

from typing import Iterable

# 可用历史涨停池/炸板池回算的指标——其余一律不碰
CALIBRATABLE = ("limit_up", "max_board", "break_rate", "promo_1to2", "promo_2to3")

# 样本下限：低于这个天数拒绝校准。分位数在小样本上极不稳定（31 天时 p80
# 只由第 25 个值决定，换一天就跳档），宁可明确报"样本不足"。
MIN_SAMPLES = 40

# 强制上界严格递增时的最小步长。_band 用 `value < upper` 判定，相邻上界相等
# 会让中间档永不命中；步长必须大于取整精度（6 位小数），否则被 round 抹平。
_MIN_STEP = 1e-5


def quantile(sorted_values: list[float], p: float) -> float:
    """线性插值分位数（p ∈ [0,100]）。输入必须已升序。

    用插值而非"取第 k 个"——小样本下后者会让分位数阶跃，档位频繁跳变。
    """
    if not sorted_values:
        raise ValueError("quantile: 空样本")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    p = max(0.0, min(100.0, p))
    pos = (len(sorted_values) - 1) * (p / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return float(sorted_values[lo]) + (float(sorted_values[hi]) - float(sorted_values[lo])) * frac


def percentile_of(sorted_values: list[float], v: float) -> float:
    """v 在历史样本中的分位（0–100）。用于前端展示"今天处在历史什么位置"。"""
    if not sorted_values:
        return float("nan")
    below = sum(1 for x in sorted_values if x < v)
    equal = sum(1 for x in sorted_values if x == v)
    return 100.0 * (below + 0.5 * equal) / len(sorted_values)


def _cuts_for(k: int) -> list[float]:
    """k 档 → k−1 个等分分位点。"""
    return [100.0 * i / k for i in range(1, k)]


def calibrate_bands(
    history: list[dict],
    template: dict,
    target: dict | None = None,
) -> tuple[dict, dict]:
    """按历史分位校准分档。**纯函数**，不碰 IO。

    Args:
        history: 每日原始指标列表（每项为 {指标名: 数值|None}）
        template: 分档模板 `{指标名: [(上界, 得分, 标签), ...]}`，只改上界、
                  沿用得分与标签（改得分等于改权重，超出本函数职责）
        target: 只校准这些指标；默认取 `CALIBRATABLE ∩ template`

    Returns:
        `(bands, basis)`。basis 每个指标一条，含样本量/切点/是否校准及原因。
        **校准失败不会抛异常**——对应指标原样返回并在 basis 标注原因
        （沿用"缺失显式标注"纪律：静默回退到经验值等于假装没这事）。
    """
    want = set(target or CALIBRATABLE) & set(template)
    basis: dict[str, dict] = {}
    bands: dict = {k: [list(row) for row in rows] for k, rows in template.items()}

    for metric in sorted(template):
        rows = bands[metric]
        k = len(rows)
        if metric not in want:
            basis[metric] = {
                "calibrated": False, "samples": 0, "cuts": [], "uppers": [],
                "reason": "该指标无历史回算口径（依赖当日全市场行情快照），沿用业界经验值",
            }
            continue

        values = sorted(
            float(h[metric]) for h in history
            if h.get(metric) is not None
        )
        if len(values) < MIN_SAMPLES:
            basis[metric] = {
                "calibrated": False, "samples": len(values), "cuts": [], "uppers": [],
                "reason": f"样本不足（{len(values)} < {MIN_SAMPLES} 个交易日），拒绝校准",
            }
            continue

        # 末档上界是 None（"及以上"），只需定前 k−1 个上界
        # 先取整再补单调——反过来会被 round 把步长抹平（0.2000002 → 0.2，
        # 守卫白做，中间档照旧永不命中）。这是单测 `test_calibrated_uppers_are_
        # strictly_increasing` 抓出来的，别调换顺序。
        uppers = [round(quantile(values, p), 6) for p in _cuts_for(k)]
        for i in range(1, len(uppers)):
            if uppers[i] <= uppers[i - 1]:
                uppers[i] = round(uppers[i - 1] + _MIN_STEP, 6)

        for i, u in enumerate(uppers):
            rows[i][0] = u
        basis[metric] = {
            "calibrated": True,
            "samples": len(values),
            "cuts": [round(p, 2) for p in _cuts_for(k)],
            "uppers": [round(u, 6) for u in uppers],
            "min": round(values[0], 6),
            "max": round(values[-1], 6),
            "reason": f"按近 {len(values)} 个交易日的等分分位切档",
        }
    return bands, basis


def describe(history: list[dict], metrics: Iterable[str] = CALIBRATABLE) -> dict:
    """当前（最新一日）各指标的历史分位，供前端展示"今天处在什么位置"。"""
    if not history:
        return {}
    latest = history[-1]
    out: dict[str, dict] = {}
    for m in metrics:
        values = sorted(float(h[m]) for h in history if h.get(m) is not None)
        v = latest.get(m)
        if not values or v is None:
            continue
        out[m] = {
            "value": float(v),
            "percentile": round(percentile_of(values, float(v)), 1),
            "samples": len(values),
        }
    return out
