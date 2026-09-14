"""回测引擎 golden test：确定性合成数据上的全指标回归基线。

目的：任何触碰引擎/指标口径的改动，若导致结果漂移，此处立即报警——
防止"指标悄悄变了"这类无报错的静默回归（比崩溃更危险）。

重新录制基线：BACKTEST_REGEN_GOLDEN=1 .venv/bin/python -m pytest tests/test_backtest_golden.py
（仅当确认口径变更合法时使用；录制后 diff 检查变更是否符合预期）。
数据为纯确定性合成（无随机源），本机与 CI 逐位一致。
"""
import json
import os
from pathlib import Path

from app.market.backtest import BacktestConfig, build_strategy, run_backtest

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "tests" / "golden" / "backtest_ma_cross.json"


def _golden_bars(n: int = 300) -> list[dict]:
    """确定性合成日K：慢趋势 × 正弦震荡（无随机数）。

    周期 9 的正弦会让 MA5/MA20 多次交叉 → 产生足量交易（golden 才有约束力）。
    """
    import math

    bars = []
    for i in range(n):
        close = 100.0 * (1 + 0.001 * i) * (1 + 0.03 * math.sin(i / 9))
        o = bars[-1]["close"] if bars else close * 0.998
        bars.append({
            "ts": f"d{i:04d}",
            "open": o,
            "high": max(o, close) * 1.004,
            "low": min(o, close) * 0.996,
            "close": close,
            "volume": 1_000_000.0 + i * 100.0,
        })
    return bars


def _snapshot() -> dict:
    report = run_backtest(
        _golden_bars(), build_strategy("ma_cross", {"fast": 5, "slow": 20}), BacktestConfig()
    )
    return {
        "bars_count": 300,
        "equity": [round(v, 6) for v in report.equity],
        "benchmark": [round(v, 6) for v in report.benchmark],
        "trades": [
            {
                "signal_ts": t.signal_ts, "fill_ts": t.fill_ts, "side": t.side,
                "price": round(t.price, 6), "qty": t.qty, "fee": round(t.fee, 6),
                "ok": t.ok, "reason": t.reason, "synthetic": t.synthetic,
            }
            for t in report.trades
        ],
        # R14：末段持仓改为估值展示（不再冒充退市强平）——golden 必须钉住它，
        # 否则「末段又多出一笔假成交」这类回归不会有任何报警。
        "open_position": (
            {k: (round(v, 6) if isinstance(v, float) else v)
             for k, v in vars(report.open_position).items()}
            if report.open_position is not None else None
        ),
        "metrics_extra": report.extra_metrics,
        "in_return": report.in_return,
        "out_return": report.out_return,
        "excess_return": report.excess_return,
    }


def test_golden_backtest_ma_cross():
    snap = _snapshot()
    if os.environ.get("BACKTEST_REGEN_GOLDEN") == "1":
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True))
        return
    assert GOLDEN_PATH.exists(), (
        "golden 基线缺失：先运行 BACKTEST_REGEN_GOLDEN=1 pytest tests/test_backtest_golden.py 录制"
    )
    golden = json.loads(GOLDEN_PATH.read_text())
    assert snap == golden, (
        "回测结果偏离 golden 基线——若口径变更是合法的，"
        "请 BACKTEST_REGEN_GOLDEN=1 重录并在 diff 中确认每处变更符合预期"
    )
