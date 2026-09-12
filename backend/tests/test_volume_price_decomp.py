"""「放量上涨」拆解实证脚本的核心数学自证（retro §6.19 实证 A）。

验证 `scripts/verify_volume_price_decomp.py` 的三个纯函数：
- `effects_from_cells`：2×2 计数加权单元均值 → 方向/量效应（含不等权）；
- `decile_shape`：十分位 → 峰值组与极端组落差（倒 U 的机器可读形状）；
- `ic_stats`：日频 IC 序列 → 均值/t/正占比/分年度（闭式核对 t 统计量）。

SQL 聚合本身由真实 marketdb 运行核验（§6.19 记录数值），此处不重复。
"""
from __future__ import annotations

import importlib.util
import math
from datetime import datetime, timezone
from pathlib import Path

# 层级：本测试在 backend/tests/，parents[1] = backend/
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_volume_price_decomp.py"


def _load():
    spec = importlib.util.spec_from_file_location("volprice_decomp", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_effects_from_cells_equal_weight():
    mod = _load()
    cells = [
        {"dir": 1, "vol_hi": 1, "n": 100, "mean": 0.002},
        {"dir": 1, "vol_hi": 0, "n": 100, "mean": 0.001},
        {"dir": -1, "vol_hi": 1, "n": 100, "mean": -0.001},
        {"dir": -1, "vol_hi": 0, "n": 100, "mean": -0.002},
    ]
    eff = mod.effects_from_cells(cells)
    # 方向效应 = 涨侧均值(0.0015) − 跌侧均值(−0.0015) = 0.003
    assert math.isclose(eff["direction_effect"], 0.003, abs_tol=1e-12)
    # 量效应 = 放量侧均值(0.0005) − 缩量侧均值(−0.0005) = 0.001
    assert math.isclose(eff["volume_effect"], 0.001, abs_tol=1e-12)


def test_effects_from_cells_count_weighted():
    """不等权时必须按计数加权，不是单元均值的简单平均。"""
    mod = _load()
    cells = [
        {"dir": 1, "vol_hi": 1, "n": 300, "mean": 0.001},   # 涨侧被 0.001 主导
        {"dir": 1, "vol_hi": 0, "n": 100, "mean": 0.005},
        {"dir": -1, "vol_hi": 1, "n": 100, "mean": -0.004},
        {"dir": -1, "vol_hi": 0, "n": 100, "mean": -0.004},
    ]
    eff = mod.effects_from_cells(cells)
    up = (300 * 0.001 + 100 * 0.005) / 400
    assert math.isclose(eff["up"], up, abs_tol=1e-12)
    assert math.isclose(eff["direction_effect"], up - (-0.004), abs_tol=1e-12)


def test_decile_shape_reports_tail_gap():
    mod = _load()
    # 倒 U：中部隆起、极端组塌陷
    dec = [(d, 1000, 0.0005 + 0.0001 * min(d, 6) - (0.002 if d == 10 else 0)) for d in range(1, 11)]
    shape = mod.decile_shape(dec)
    assert shape["peak_d"] == 6
    assert shape["d10_m"] < shape["peak_m"]
    assert shape["tail_gap"] == shape["d10_m"] - shape["peak_m"]
    # 单调上升（无倒 U）时峰值应是最极端组、落差 ≥ 0
    dec_mono = [(d, 1000, 0.0001 * d) for d in range(1, 11)]
    shape2 = mod.decile_shape(dec_mono)
    assert shape2["peak_d"] == 10 and shape2["tail_gap"] == 0


def test_ic_stats_closed_form():
    mod = _load()
    ics = [0.01, -0.03] * 120  # 240 个日频 IC，均值 -0.01
    daily = []
    for i, ic in enumerate(ics):
        ts = int(datetime(2023, 1, 2 + i % 20, tzinfo=timezone.utc).timestamp() * 1000) + i * 86_400_000
        daily.append((ts, ic, 500))
    st = mod.ic_stats(daily)
    mean = sum(ics) / len(ics)
    var = sum((x - mean) ** 2 for x in ics) / (len(ics) - 1)
    t = mean / math.sqrt(var) * math.sqrt(len(ics))
    assert math.isclose(st["mean"], mean, abs_tol=1e-15)
    assert math.isclose(st["t"], t, abs_tol=1e-12)
    assert st["pos_ratio"] == 0.5
    assert st["n_days"] == 240
    # 分年度键齐全且各年均值与全样本一致（构造数据逐年同分布）
    assert st["yearly"] and all(abs(v - mean) < 1e-9 for v in st["yearly"].values())


def test_ic_stats_zero_variance_no_crash():
    mod = _load()
    daily = [(1700000000000, 0.05, 500)] * 3
    st = mod.ic_stats(daily)
    assert st["t"] == 0.0 and math.isclose(st["mean"], 0.05, abs_tol=1e-12)
