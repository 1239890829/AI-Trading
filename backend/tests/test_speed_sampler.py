"""涨速采样器测试。

核心契约：**没有足够采样历史时涨速必须为 None**（宁可"采样中"也不臆造）；
有历史时涨速 = 最近采样 vs ~5 分钟前采样的涨跌幅（同花顺行情口径）。
"""
from __future__ import annotations

from app.services.speed_sampler import SpeedSampler


def test_speed_after_five_minutes_of_samples():
    s = SpeedSampler()
    s.record({"600519": 100.0}, now=0.0)
    s.record({"600519": 103.0}, now=300.0)  # 恰好 5 分钟后
    speed, span = s.speed("600519", now=300.0)
    assert speed == 3.0
    assert span == 300.0


def test_cold_start_returns_none_not_zero():
    """零历史返回 None（前端显示"采样中"），绝不拿 0% 或当日涨幅顶上。"""
    s = SpeedSampler()
    speed, span = s.speed("600519", now=100.0)
    assert speed is None and span == 0.0


def test_insufficient_history_returns_none_with_span():
    """只有 2 分钟历史：不足 5 分钟窗口 → None，但回报采样跨度供前端展示。"""
    s = SpeedSampler()
    s.record({"600519": 100.0}, now=0.0)
    s.record({"600519": 101.0}, now=120.0)
    speed, span = s.speed("600519", now=120.0)
    assert speed is None
    assert 0 < span <= 120.0 + 1e-9


def test_base_outside_tolerance_returns_none():
    """基准点超出 [窗口±90s] 容差（如采样中断 10 分钟）→ None。"""
    s = SpeedSampler()
    s.record({"600519": 100.0}, now=0.0)
    s.record({"600519": 103.0}, now=900.0)  # 15 分钟后，基准已远超容差
    speed, _ = s.speed("600519", now=900.0)
    assert speed is None


def test_base_uses_closest_sample_inside_window():
    """窗口内多个采样时取最近的那个作为基准（300s 前的比 375s 前的优先）。"""
    s = SpeedSampler()
    s.record({"600519": 100.0}, now=225.0)   # 距 now=600 有 375s，在窗口容差内
    s.record({"600519": 101.0}, now=300.0)   # 距 now=600 有 300s，更贴近 5 分钟窗
    s.record({"600519": 103.0}, now=600.0)
    speed, _ = s.speed("600519", now=600.0)
    # 基准=101 → (103-101)/101 = 1.98%
    assert speed == round((103.0 - 101.0) / 101.0 * 100, 2)


def test_record_dedupes_within_five_seconds():
    """5 秒内重复采样覆盖而非追加（防高频轮询灌爆队列）。"""
    s = SpeedSampler()
    s.record({"600519": 100.0}, now=0.0)
    s.record({"600519": 100.5}, now=3.0)
    s.record({"600519": 101.0}, now=10.0)
    dq = s._series["600519"]
    assert len(dq) == 2
    assert dq[0] == (3.0, 100.5)


def test_expired_symbols_are_collected():
    """20 分钟无新采样的标的被清除，避免缓存无限膨胀。"""
    s = SpeedSampler()
    s.record({"600519": 100.0}, now=0.0)
    s.record({"000001": 10.0}, now=1300.0)  # 触发 GC：600519 已 21 分钟无更新
    assert "600519" not in s._series
    assert "000001" in s._series


def test_max_symbols_evicts_least_recently_active():
    s = SpeedSampler()
    for i in range(700):
        s.record({f"{600000 + i:06d}": 10.0 + i}, now=float(i))
    assert len(s._series) <= 600


def test_invalid_prices_are_ignored():
    s = SpeedSampler()
    n = s.record({"600519": None, "000001": 0, "600105": -1, "603228": 10.0})
    assert n == 1
    assert "600519" not in s._series and "000001" not in s._series
