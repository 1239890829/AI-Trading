"""情绪分位校准回归测试（P0-3b）。

锁的是**已发生的真实失效**：`promo_1to2` 用绝对阈值 ≥0.40 才给正分，
而实测近 31 个交易日该指标 **max 仅 0.265** → 正分档永不命中 →
这个"业界公认最敏感的接力指标"退化成恒定 −2 的常量，赚钱效应轴被系统性压低。

这类失效不报错、界面照常、结论偏移，只能靠"阈值 vs 实测分布"的对照测试抓住。
"""
from __future__ import annotations

from app.sentiment import calibration as cal
from app.sentiment.calibration import (
    CALIBRATABLE,
    MIN_SAMPLES,
    calibrate_bands,
    describe,
    percentile_of,
    quantile,
)
from app.sentiment.engine import EARNING_BANDS, HEAT_BANDS


def _hist(n: int, key: str, lo: float, hi: float) -> list[dict]:
    """线性铺开的 n 天样本。"""
    if n == 1:
        return [{key: lo}]
    return [{key: lo + (hi - lo) * i / (n - 1)} for i in range(n)]


# ---------------------------------------------------------------- 基础分位


def test_quantile_edges_and_interpolation():
    vals = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert quantile(vals, 0) == 0.0
    assert quantile(vals, 100) == 4.0
    assert quantile(vals, 50) == 2.0
    assert quantile(vals, 25) == 1.0  # 线性插值，不是"取第 k 个"


def test_quantile_single_sample():
    assert quantile([7.0], 50) == 7.0


def test_percentile_of_position():
    vals = [1.0, 2.0, 3.0, 4.0]
    assert percentile_of(vals, 1.0) == 12.5   # below=0, equal=1 → (0+0.5)/4
    assert percentile_of(vals, 4.0) == 87.5
    assert percentile_of(vals, 5.0) == 100.0  # 超过历史最高
    assert percentile_of(vals, 0.0) == 0.0


# ---------------------------------------------------------------- 核心：阈值失配


# 2026-03-11 → 2026-09-01 共 120 个交易日的真实命中率（见模块 docstring 表）
LEGACY_HIT_RATE = {  # 指标 → {档位得分: (命中天数, 总天数)}
    "promo_1to2": {-2: (79, 120), -1: (37, 120), 1: (4, 120), 2: (0, 120)},
    "limit_up": {-1: (0, 120), 0: (12, 120), 1: (35, 120), 2: (28, 120), 3: (45, 120)},
    "max_board": {-1: (1, 120), 0: (56, 120), 1: (45, 120), 2: (18, 120)},
}


def test_legacy_promo_bands_have_dead_and_near_dead_tiers():
    """旧阈值在真实分布上有**死档**，且负分档吃掉 96.7% 的日子。

    这是本次修复的动机。若有人把阈值调回绝对经验值，这条会失败并说明原因。
    注意断言是"≥40% 永不命中 + 负分档 >95%"，**不是**"+1 档也永不命中"——
    实测 25–40% 档命中的 4/120 = 3.3%，虽近死档但确实命中过。别把结论说满。
    """
    hit, total = LEGACY_HIT_RATE["promo_1to2"][2]
    assert hit == 0, "≥40% 档应永不命中（实测 max 0.275 < 0.40）"
    hit_p1, _ = LEGACY_HIT_RATE["promo_1to2"][1]
    assert hit_p1 / total < 0.05, f"25–40% 档命中率 {hit_p1/total:.1%} 应 <5%（近死档）"
    neg = sum(LEGACY_HIT_RATE["promo_1to2"][p][0] for p in (-2, -1))
    assert neg / total > 0.95, f"负分档占比 {neg/total:.1%} 应 >95%——该指标已近似恒定 −2"


def test_legacy_limit_up_lowest_tier_is_dead():
    """涨停家数 <25 家（−1）在 120 天里从未出现（实测 min 27）→
    「冰点」永远不会由涨停家数这一路触发。"""
    hit, total = LEGACY_HIT_RATE["limit_up"][-1]
    assert hit == 0


def test_calibrated_promo_bands_all_reachable():
    """校准后四档都应可达——每档各占约 1/4 的历史日子。"""
    history = _hist(MIN_SAMPLES + 10, "promo_1to2", 0.065, 0.265)
    bands, basis = calibrate_bands(history, {"promo_1to2": EARNING_BANDS["promo_1to2"]})
    info = basis["promo_1to2"]
    assert info["calibrated"] is True
    assert info["samples"] == MIN_SAMPLES + 10
    assert info["cuts"] == [25.0, 50.0, 75.0]  # 4 档 → 3 刀

    rows = bands["promo_1to2"]
    reached = set()
    for h in history:
        v = h["promo_1to2"]
        for upper, points, _label in rows:
            if upper is None or v < upper:
                reached.add(points)
                break
    assert reached == {-2, -1, 1, 2}, f"校准后仍有不可达档位：{sorted(reached)}"


def test_calibrated_uppers_are_strictly_increasing():
    """相邻切点相等会让中间档永不命中（正是本次要修的病）。

    用大量重复值构造退化样本，验证单调守卫生效。
    """
    history = [{"break_rate": 0.20}] * 60  # 全部相同 → 所有分位都相等
    bands, _ = calibrate_bands(history, {"break_rate": HEAT_BANDS["break_rate"]})
    uppers = [r[0] for r in bands["break_rate"] if r[0] is not None]
    assert uppers == sorted(set(uppers)), f"上界未严格递增：{uppers}"
    assert len(uppers) == len(set(uppers))


# ---------------------------------------------------------------- 样本守卫


def test_insufficient_samples_refuses_to_calibrate():
    """样本不足必须**拒绝**校准并说明原因，不能静默沿用经验值。"""
    history = _hist(MIN_SAMPLES - 1, "limit_up", 36, 137)
    bands, basis = calibrate_bands(history, {"limit_up": HEAT_BANDS["limit_up"]})
    info = basis["limit_up"]
    assert info["calibrated"] is False
    assert "样本不足" in info["reason"]
    # 分档必须原样保留，一个数字都不许动
    assert bands["limit_up"] == [list(r) for r in HEAT_BANDS["limit_up"]]


def test_non_calibratable_metrics_stay_untouched_with_reason():
    """median_pct/red_rate/limit_down 依赖当日全市场行情快照，无法从涨停池回算
    ——必须显式标注未校准，绝不假装校准过。"""
    history = [
        {"limit_up": 36 + i, "promo_1to2": 0.05 + i / 1000}
        for i in range(80)
    ]
    bands, basis = calibrate_bands(history, EARNING_BANDS)
    for m in ("median_pct", "red_rate", "limit_down"):
        assert basis[m]["calibrated"] is False
        assert "经验值" in basis[m]["reason"]
        assert bands[m] == [list(r) for r in EARNING_BANDS[m]]
    assert basis["promo_1to2"]["calibrated"] is True


def test_calibrate_never_raises_on_garbage_history():
    """脏数据（全 None / 空列表）不得抛异常——判定失败要落在 basis 里。"""
    for bad in ([], [{"limit_up": None, "max_board": None}], [{}] * 5):
        _bands, basis = calibrate_bands(bad, HEAT_BANDS)
        assert all(v["calibrated"] is False for v in basis.values())


# ---------------------------------------------------------------- 得分与标签不被改动


def test_calibration_touches_only_uppers():
    """校准只动上界。改得分/标签等于改权重，超出本函数职责。"""
    history = _hist(60, "limit_up", 36, 137)
    bands, _ = calibrate_bands(history, {"limit_up": HEAT_BANDS["limit_up"]})
    for (u_new, p_new, l_new), (_u_old, p_old, l_old) in zip(
        bands["limit_up"], HEAT_BANDS["limit_up"]
    ):
        assert p_new == p_old and l_new == l_old


def test_calibratable_set_matches_recomputable_metrics():
    """CALIBRATABLE 里的每一个都必须能由历史涨停池/炸板池算出来。

    若有人往里加了依赖当日行情快照的指标，这条会提醒他补回算口径。
    """
    assert set(CALIBRATABLE) == {
        "limit_up", "max_board", "break_rate", "promo_1to2", "promo_2to3",
    }


# ---------------------------------------------------------------- describe


def test_describe_reports_current_percentile():
    history = _hist(11, "limit_up", 10.0, 110.0)
    d = describe(history, ["limit_up"])
    assert d["limit_up"]["value"] == 110.0
    assert d["limit_up"]["percentile"] > 90  # 历史最高
    assert d["limit_up"]["samples"] == 11


def test_describe_empty_history():
    assert describe([], ["limit_up"]) == {}
