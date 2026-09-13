"""chip_signal 阈值 × 波动率 分层探针（审计 C1，§6.25）。

问题：chip_signal 的固定阈值（获利盘≥85% 派发 / 30~70%+低位+密集=吸筹 / 集中度 0.40~0.45）
对不同**波动率**的股票是否同义？——高波股的获利盘日间摆幅大，固定阈值触发频率与信号质量
可能系统性偏移（KB-STOCK-36①：条件量必须声明条件域）。

方法（抽样设计，控制 O(n²) 模拟成本）：
- 宇宙 = marketdb 有 ≥256 根日 K 的股票，按 vol20（ret1 标准差）取**高波 20 + 低波 20**（seed 固定）；
- 每票逐日截断调用 `simulate_chip_distribution`（口径单点）+ `evaluate_chip_signal`，
  记录 launch_watch / distribution_warning 触发与 fwd5（收盘买→5 日收盘卖）；
- 按波动带聚合：触发日占比、信号后 fwd5 均值、获利盘日间摆幅。

⚠️ 快速探针口径：未复权 / 不含费用 / 模拟筹码为 ASSUMED_AVG_TURNOVER 代理模型（chip.py 同源）。
"""
from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.market.chip import simulate_chip_distribution  # noqa: E402
from app.picks.chip_signal import evaluate_chip_signal  # noqa: E402

DB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"
N_PER_BAND = 20
SIM_DAYS = 120          # 逐日评估的最近交易日数
WINDOW = 250            # 筹码窗口（与 chip.py DEFAULT_WINDOW 一致）
FWD = 5


def pick_universe(seed: int = 42) -> tuple[list[str], list[str], dict[str, float]]:
    con = duckdb.connect(str(DB), read_only=True)
    try:
        rows = con.execute("""
        WITH r AS (
          SELECT thscode, date_ms, close_price,
                 LAG(close_price) OVER (PARTITION BY thscode ORDER BY date_ms) AS pc,
                 row_number() OVER (PARTITION BY thscode ORDER BY date_ms DESC) AS rn_desc,
                 count(*) OVER (PARTITION BY thscode) AS n
          FROM daily_k WHERE date_ms >= 1514736000000
        ),
        v AS (
          SELECT thscode, stddev_samp(close_price/NULLIF(pc,0) - 1) AS vol20, max(n) AS n
          FROM r WHERE pc > 0 AND rn_desc <= 20 GROUP BY thscode
        )
        SELECT thscode, vol20, n FROM v
        WHERE vol20 IS NOT NULL AND n >= 256 AND thscode NOT LIKE '9%' AND thscode NOT LIKE '4%'
        """).fetchall()
    finally:
        con.close()
    ranked = sorted(rows, key=lambda x: x[1])
    low = [r[0] for r in ranked[:200]]
    high = [r[0] for r in ranked[-200:]]
    rng = random.Random(seed)
    rng.shuffle(low)
    rng.shuffle(high)
    volmap = {r[0]: r[1] for r in rows}
    return low[:N_PER_BAND], high[:N_PER_BAND], volmap


def load_rows(symbol: str) -> list[dict]:
    con = duckdb.connect(str(DB), read_only=True)
    try:
        rows = con.execute(
            "select date_ms, open_price, high_price, low_price, close_price, volume, turnover "
            "from daily_k where thscode = ? order by date_ms",
            [symbol if "." in symbol else
             (f"{symbol}.SH" if symbol.startswith("6") else
              (f"{symbol}.BJ" if symbol.startswith(("4", "8", "9")) else f"{symbol}.SZ"))],
        ).fetchall()
    finally:
        con.close()
    out = []
    for ms, o, h, low, c, v, tr in rows:
        out.append({"date_ms": ms, "open": o, "high": h, "low": low, "close": c,
                    "volume": v, "turnover": tr})
    return out


def main() -> int:
    low_syms, high_syms, volmap = pick_universe()
    print(f"抽样：高波 {len(high_syms)} + 低波 {len(low_syms)}（seed=42）· 每票近 {SIM_DAYS} 日逐日模拟")
    results: dict[str, dict] = {"高波": {"launch": [], "distr": [], "fwd_launch": [], "fwd_distr": [],
                                         "days": 0, "swing": []},
                                "低波": {"launch": [], "distr": [], "fwd_launch": [], "fwd_distr": [],
                                         "days": 0, "swing": []}}
    for band, syms in (("高波", high_syms), ("低波", low_syms)):
        for i, sym in enumerate(syms, 1):
            try:
                rows = load_rows(sym)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}] {sym} 跳过：{exc}")
                continue
            if len(rows) < WINDOW + FWD + SIM_DAYS:
                continue
            tail = rows[-(SIM_DAYS + FWD):]
            prev_profit = None
            for t in range(len(tail) - FWD):
                sub = tail[: t + 1]
                chip = simulate_chip_distribution(sub)
                if not chip:
                    continue
                sig = evaluate_chip_signal(chip, sub)
                closes = [float(r["close"]) for r in sub if r.get("close") is not None]
                fwd = (closes[-1 + FWD] / closes[-1] - 1) * 100 if len(closes) > FWD else None
                results[band]["days"] += 1
                p = chip.get("profit_ratio")
                if prev_profit is not None and p is not None:
                    results[band]["swing"].append(abs(p - prev_profit) * 100)
                prev_profit = p
                if sig.get("signal") == "launch_watch":
                    results[band]["launch"].append(1)
                    if fwd is not None:
                        results[band]["fwd_launch"].append(fwd)
                else:
                    results[band]["launch"].append(0)
                if sig.get("signal") == "distribution_warning":
                    results[band]["distr"].append(1)
                    if fwd is not None:
                        results[band]["fwd_distr"].append(fwd)
                else:
                    results[band]["distr"].append(0)
            print(f"  [{i}/{len(syms)}] {sym} vol={volmap.get(sym, 0):.3f} 完成", file=sys.stderr)

    print("\n=== chip_signal 阈值 × 波动带（抽样探针） ===")
    print(f"{'带':<5}{'评估日':>7}{'吸筹触发%':>9}{'派发触发%':>9}{'获利盘日摆幅':>12}"
          f"{'吸筹后fwd5':>10}{'派发后fwd5':>10}")
    for band in ("高波", "低波"):
        d = results[band]
        days = max(1, d["days"])
        launch_pct = sum(d["launch"]) / days * 100
        distr_pct = sum(d["distr"]) / days * 100
        swing = statistics.mean(d["swing"]) if d["swing"] else 0
        fl = statistics.mean(d["fwd_launch"]) if d["fwd_launch"] else float("nan")
        fd = statistics.mean(d["fwd_distr"]) if d["fwd_distr"] else float("nan")
        print(f"{band:<5}{d['days']:>7}{launch_pct:>8.1f}%{distr_pct:>8.1f}%{swing:>11.2f}pp"
              f"{fl:>9.2f}%{fd:>9.2f}%")
    print()
    print("判读：吸筹/派发触发占比或获利盘摆幅跨带差一个量级 ⇒ 固定阈值语义随波动率漂移，")
    print("      支持分波动带校准；同量级 ⇒ 阈值成立（C1 假设否掉）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
