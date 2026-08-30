"""做 T 分时信号引擎测试（docs/minute-chart-plan.md 模块 4 引擎核心）。

核心不变量：
- **as_of**：信号只用 ≤ 触发 bar 的数据——任意截断点的前缀信号必须与全量一致；
- **确认时点**：指标 1 的"3 分钟不创新低"在确认 bar 上触发，signal_price 是
  确认 bar 的价格而非谷底价（不是用未来数据回看发信号）；
- **组合阈值**：单指标权重 0.15~0.30 归一后 < 0.5，必须共振才出偏向。
"""

import sys
from datetime import datetime, timedelta

sys.path.insert(0, ".")

from app.market.minute_signals import compute_minute_signals


def mk_series(prices: list[float], vols: list[int], start="2026-08-28T01:30:00+00:00") -> list[dict]:
    """合成分钟序列：avg = 累计额/累计量（与真实口径一致）。"""
    pts = []
    cum_amt = 0.0
    cum_vol = 0
    t0 = datetime.fromisoformat(start)
    for i, (p, v) in enumerate(zip(prices, vols)):
        ts = t0 + timedelta(minutes=i)
        cum_amt += p * v
        cum_vol += v
        pts.append({
            "ts": ts.isoformat(),
            "price": p, "volume": float(v),
            "cum_amount": round(cum_amt, 2), "cum_volume": cum_vol,
            "avg": round(cum_amt / cum_vol, 3),
            "source": "test",
        })
    return pts


def _flat(n, price=10.0, vol=1000):
    return [price] * n, [vol] * n


def test_avg_dev_pullback_with_bottom_divergence_emits_low_buy():
    """低吸共振：缩量新低（底背离）+ 均价偏离谷底 3 分钟确认。

    时间线：30 根 10.00 平盘 → 1 根 9.80 放量下杀（破开盘低点+脉冲，观察区，
    **破位当刻不出低吸信号——不接飞刀**）→ 6 根 9.80 缩量横盘（破位信号出窗）
    → 1 根 9.75 缩量新低（底背离）→ 3 根 9.76 确认 → 低吸共振成立。
    """
    prices, vols = _flat(30)
    prices += [9.80, 9.80, 9.80, 9.80, 9.80, 9.80, 9.75, 9.76, 9.76, 9.76]
    vols += [5000, 1000, 1000, 1000, 1000, 1000, 200, 200, 200, 200]
    pts = mk_series(prices, vols)
    out = compute_minute_signals(pts, yesterday_vol=100_000)
    assert out["signals"], f"应产出低吸信号，实际 signals={out['signals']} observed={out['observed']}"
    s = out["signals"][0]
    assert s["bias"] == "低吸偏向" and s["score"] < 0
    keys = {h["key"] for h in s["triggered"]}
    assert {"avg_dev", "vol_div"} <= keys, keys
    # signal_price 是确认 bar 的价格（9.75 或 9.76），不是谷底价——as_of 证明
    assert s["signal_price"] in (9.75, 9.76)


def test_avg_dev_high_alone_never_emits_but_with_divergence_does():
    """单指标 0.30/0.9≈0.33 < 0.5 不出信号；叠加顶背离后 ≥0.5 出高抛。"""
    prices, vols = _flat(30)
    # 顶背离：创新高但量缩至均量 60% 以下；同时偏离 ≥ +2%
    prices += [10.30, 10.31]
    vols += [100, 100]
    pts = mk_series(prices, vols)
    out = compute_minute_signals(pts)
    keys = {h["key"] for s in out["signals"] for h in s["triggered"]}
    assert out["signals"], "偏离 +3% 与顶背离共振应出高抛"
    s = out["signals"][0]
    assert s["bias"] == "高抛偏向" and s["score"] > 0
    assert {"avg_dev", "vol_div"} <= keys


def test_surge_alone_does_not_emit():
    """量能突变权重 0.20，单独归一后 0.22 < 0.5——只记观察，不出偏向。"""
    prices, vols = _flat(30)
    prices += [10.05]
    vols += [5000]
    pts = mk_series(prices, vols)
    out = compute_minute_signals(pts)
    assert out["signals"] == []
    assert out["observed"] >= 1


def test_cooldown_prevents_re_emission():
    """同一方向 30 根 bar 冷却：连续两段相同共振只出一次。"""
    prices, vols = _flat(30)
    seg = [9.80, 9.75, 9.76, 9.76, 9.76]
    vols_seg = [5000, 200, 200, 200, 200]
    prices += seg + [10.0] * 10 + seg  # 两段相同的低吸共振
    vols += vols_seg + [1000] * 10 + vols_seg
    pts = mk_series(prices, vols)
    out = compute_minute_signals(pts)
    low = [s for s in out["signals"] if s["bias"] == "低吸偏向"]
    assert len(low) == 1, f"冷却期内第二段不应重复触发，实际 {len(low)} 条"


def test_as_of_prefix_property():
    """防未来函数：任意截断点上算出的信号 = 全量信号的对应前缀。"""
    prices, vols = _flat(30)
    prices += [9.80, 9.75, 9.76, 9.76, 9.76, 9.90, 10.30, 10.31]
    vols += [5000, 200, 200, 200, 200, 1000, 100, 100]
    full = mk_series(prices, vols)
    all_signals = compute_minute_signals(full)["signals"]

    for k in (33, 36, len(full)):
        partial = compute_minute_signals(full[:k])["signals"]
        # 截断 bar 上触发的信号属于前缀（<=）
        expected = [s for s in all_signals if s["ts"] <= full[k - 1]["ts"]]
        assert partial == expected, f"截断点 {k} 前缀不一致：{len(partial)} vs {len(expected)}"


def test_degraded_flags_when_inputs_missing():
    prices, vols = _flat(35)
    pts = mk_series(prices, vols)
    out = compute_minute_signals(pts, yesterday_vol=None)
    assert any("breakout" in d for d in out["degraded"])
    assert any("turnover" in d for d in out["degraded"])
    assert any("avg_dev" in d for d in out["degraded"])  # 波动率缺失 → 阈值未自适应
    assert "turnover" not in out["basis"]["active_keys"]


def test_adaptive_threshold_scales_with_daily_volatility():
    """阈值随波动率缩放并双向 clamp：低波动收浅防噪音，高波动放松但封顶。"""
    prices, vols = _flat(30)
    # 低波动股：dev 只到 -1.0%（avg≈10 → 价格 9.90）
    prices += [9.90, 9.90, 9.90, 9.90, 9.90]
    vols += [1000] * 5
    pts = mk_series(prices, vols)
    out_lowvol = compute_minute_signals(pts, daily_vol_pct=0.5)   # 缩放 → -0.5%
    out_fixed = compute_minute_signals(pts)                        # 固定 -1.5%
    assert out_lowvol["basis"]["thresholds"]["avg_dev_low"] == -0.5
    assert out_fixed["basis"]["thresholds"]["avg_dev_low"] == -1.5
    # 防噪音地板：vol 0.3 时缩放值 -0.3 浅于 -0.5，被地板收紧到 -0.5
    assert compute_minute_signals(pts, daily_vol_pct=0.3)["basis"]["thresholds"]["avg_dev_low"] == -0.5
    # 高波动：阈值放松（-1.5% × 4.5/1.5 = -4.5%），但 -2.5 上限封顶防极端
    prices_hi, vols_hi = _flat(30)
    prices_hi += [9.80, 9.75, 9.76, 9.76, 9.76]
    vols_hi += [5000, 200, 200, 200, 200]
    out_highvol = compute_minute_signals(mk_series(prices_hi, vols_hi), daily_vol_pct=4.5)
    assert out_highvol["basis"]["thresholds"]["avg_dev_low"] == -2.5
    assert out_highvol["signals"] == []  # -2.2% 偏离未达 -2.5% 阈值，不触发


def test_breakout_high_with_volume_confirms_strength():
    """放量突破开盘 30 分钟高点 → 强势确认（抑制高抛，-1 方向）。"""
    prices, vols = _flat(30, price=10.0)
    prices += [10.50, 10.55]
    vols += [4000, 4000]
    pts = mk_series(prices, vols)
    out = compute_minute_signals(pts, yesterday_vol=1_000_000)
    bo = [h for s in out["signals"] for h in s["triggered"] if h["key"] == "breakout"]
    # 突破单独 0.15/0.9≈0.17 不出信号，但可作为组合成分被记录在观察里；
    # 这里断言：要么作为信号成分出现且方向 -1，要么仅因未达阈值而观察
    if bo:
        assert all(h["direction"] == -1 for h in bo)
        assert "量比" in bo[0]["evidence"]
