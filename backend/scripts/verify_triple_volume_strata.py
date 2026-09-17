"""三倍量战法——**市值/价格 分层复测**（审计 C4，§6.25）。

背景：KB-STOCK-27 的否决是**全样本均值判据**（近 250 日负期望、胜率 33.5/35.9% vs
市场 46.8%）。条件化审计（KB-STOCK-36）要求检验：负期望是否均匀，还是**集中在其
它层、而「炒小炒差」域（小盘低价）存在被均值掩盖的正期望子集**。

口径（与 verify_triple_volume.py 完全同源）：
- 信号 = volume ≥ 3×前 5 日均量；数据 = marketdb daily_k（**未复权**，除权噪声声明同前）；
- 持有 = T 收盘进 → T+5 收盘出（不含费用/滑点/涨跌停不可成交——快速统计口径）；
- 对照 = **同层全市场**（同层内全部股票×日的 fwd5 均值——层内中性，不是全市场中性）；
- 价格档 = 信号日收盘绝对价（历史数据，无前视）：低价 <5 / 中价 5-20 / 高价 >20；
- 市值档 = 最新快照 nmc（**前视近似**——历史市值不可得，股票跨档漂移会少量污染，
  仅用于分层归属不做收益计算）。

判定（预注册）：任一层「信号−同层市场」差值 ≥ +1pp 且信号样本 ≥300 → 该层列**观察项**
（KB-STOCK-36①：条件化候选，仍需完整回测确认）；全部层 < +1pp → 否决维持。
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "marketdb" / "market.duckdb"
SNAP_GLOB = str(ROOT.parent / "data" / "parquet" / "snapshots" / "*" / "*.parquet")  # 快照落项目根（非 backend/data）
LOOKBACK_DAYS = 250
VOL_MULTIPLE = 3.0
VOL_LOOKBACK = 5
FWD_DAYS = 5
PASS_MARGIN_PP = 1.0
PASS_MIN_N = 300


def load_cap_proxy() -> dict[str, float]:
    """最新快照 nmc（万元）→ 亿。前视近似声明见模块头。"""
    import glob

    import pandas as pd

    files = sorted(glob.glob(SNAP_GLOB))
    out: dict[str, float] = {}
    if files:
        t = pd.read_parquet(files[-1], columns=["symbol", "nmc"])
        for sym, v in t.itertuples(index=False):
            if v:
                out[sym] = v / 1e4  # ⚠️ sina nmc 单位=万元
    return out


def main() -> int:
    caps = load_cap_proxy()
    con = duckdb.connect(str(DB), read_only=True)
    max_ms = con.execute("select max(date_ms) from daily_k").fetchone()[0]
    min_ms = max_ms - LOOKBACK_DAYS * 86_400_000 * 1.5
    window = "PARTITION BY thscode ORDER BY date_ms"
    lags = ", ".join(
        f"LEAD(close_price,{i}) OVER ({window}) AS f_close_{i}" for i in range(1, FWD_DAYS + 1)
    )
    sql = f"""
    WITH base AS (
      SELECT thscode, date_ms, high_price, low_price, close_price, volume,
             AVG(volume) OVER (PARTITION BY thscode ORDER BY date_ms
                               ROWS BETWEEN {VOL_LOOKBACK} PRECEDING AND 1 PRECEDING) AS avg_prev,
             LAG(close_price,1) OVER ({window}) AS prev_close,
             {lags}
      FROM daily_k
      WHERE date_ms >= {int(min_ms)}
    )
    SELECT thscode, close_price,
      (volume >= {VOL_MULTIPLE} * avg_prev) AS is_triple,
      CASE WHEN f_close_{FWD_DAYS} IS NOT NULL AND close_price > 0
           THEN (f_close_{FWD_DAYS} / close_price - 1) * 100 END AS fwd5
    FROM base
    WHERE avg_prev IS NOT NULL AND avg_prev > 0 AND volume > 0 AND close_price > 0
    """
    rows = con.execute(sql).fetchall()
    con.close()

    def price_band(px: float) -> str:
        return "低价<5" if px < 5 else ("中价5-20" if px <= 20 else "高价>20")

    def cap_band(sym: str) -> str:
        v = caps.get(sym.split(".")[0])  # thscode=600519.SH，快照键=裸码
        if v is None:
            return "未知"
        return "<30亿" if v < 30 else ("30-100亿" if v < 100 else
               ("100-300亿" if v < 300 else ">300亿"))

    strata: dict[tuple[str, str], dict] = {}
    for sym, px, is_triple, fwd5 in rows:
        if fwd5 is None:
            continue
        key = (price_band(px), cap_band(sym))
        st = strata.setdefault(key, {"sig": [], "all": []})
        st["all"].append(fwd5)
        if is_triple:
            st["sig"].append(fwd5)

    order_p = ["低价<5", "中价5-20", "高价>20"]
    order_c = ["<30亿", "30-100亿", "100-300亿", ">300亿"]
    lines = [
        f"# 三倍量 × 市值/价格 分层复测（审计 C4）——窗口 {LOOKBACK_DAYS} 交易日 · 持有 {FWD_DAYS} 日",
        "",
        "| 价格档 | 市值档 | 信号n | 信号均值% | 同层市场% | 差值pp | 胜率% |",
        "|---|---|---|---|---|---|---|",
    ]
    verdicts: list[str] = []
    for pb in order_p:
        for cb in order_c:
            st = strata.get((pb, cb))
            if not st or not st["sig"]:
                continue
            sig_m = sum(st["sig"]) / len(st["sig"])
            all_m = sum(st["all"]) / len(st["all"])
            diff = sig_m - all_m
            win = sum(1 for x in st["sig"] if x > 0) / len(st["sig"]) * 100
            flag = ""
            if diff >= PASS_MARGIN_PP and len(st["sig"]) >= PASS_MIN_N:
                flag = " ⭐观察"
                verdicts.append(f"{pb}×{cb}: +{diff:.2f}pp (n={len(st['sig'])})")
            lines.append(f"| {pb} | {cb} | {len(st['sig'])} | {sig_m:.2f} | {all_m:.2f} | "
                         f"{diff:+.2f} | {win:.1f}{flag} |")
    lines += ["", f"**裁定**：{'⭐ 观察项候选——' + '；'.join(verdicts) if verdicts else '全部层差值 < +1pp，否决维持（无条件均值判据在分层后依旧成立）'}",
              "", "⚠️ 快速统计口径：未复权/不含费用/不含涨跌停不可成交——观察项仍需完整回测确认。"]
    text = "\n".join(lines)
    print(text)
    out = Path(__file__).resolve().parents[2] / "artifacts" / "research" / "c4-triple-volume-strata.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"\n已存 {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
