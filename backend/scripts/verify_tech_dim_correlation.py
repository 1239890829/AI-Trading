"""tech_score 五维同源假设的截面相关验证（P2-33③，可复跑，只读）。

背景（冗余审查 + 外部研究「信息互补」论断）：`market/tech_score.py` 八维中
trend / macd / kdj / rsi / pattern **五维全部源自同一份价格序列**——若它们的截面
分数高度相关，则六维评分的技术块实际信息多样性低于表面（同源信号重复计权）。

方法：取 3 个样本截面日（跨牛熊）的全市场个股，各取近 60 根日 K（raw 日 K 即可——
MA 排列 / MACD / KDJ / RSI 对个股常数比例调整不变），跑生产 `score_stock`，对
维度分数做**截面 Pearson 相关矩阵**。判读参照因子去冗余口径 IC_CORR_DEDUP=0.70：
|r| ≥ 0.6 视为高度重叠、0.3-0.6 中度、<0.3 相对独立。

诚实边界：liquidity（需池内成交额分位）与 rps（需全市场 RPS 服务）在本离线场景
取中性值，不参与矩阵（它们的信号源本就不同于价格序列，不影响"五维同源"的检验）。
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.bjtime import beijing_today  # noqa: E402
from app.market.tech_score import score_stock  # noqa: E402

DB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"
SAMPLE_DATES = ("2021-06-01", "2023-12-01", "2026-06-01")
DIMS = ("trend", "macd", "kdj", "rsi", "pattern", "volume")
MIN_BARS = 60


def _to_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    vx = sum((a - mx) ** 2 for a in xs) ** 0.5
    vy = sum((b - my) ** 2 for b in ys) ** 0.5
    if vx == 0 or vy == 0:
        return 0.0
    return cov / (vx * vy)


def _load_bars(con: duckdb.DuckDBPyConnection, day: date) -> dict[str, list[dict]]:
    rows = con.execute(
        """
        WITH hist AS (
          SELECT k.thscode, k.date_ms, k.open_price, k.high_price, k.low_price,
                 k.close_price, k.volume,
                 COUNT(*) OVER (PARTITION BY k.thscode ORDER BY k.date_ms
                                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cnt
          FROM daily_k k WHERE k.volume > 0
        ),
        day_cnt AS (
          -- 每股在选定日的累计 K 数（= 截至当日总样本）与当日 date_ms 上界
          SELECT thscode, date_ms AS day_ms, cnt AS total
          FROM hist WHERE to_timestamp(date_ms/1000)::DATE = ?
        ),
        bars AS (
          SELECT h.*, ROW_NUMBER() OVER (PARTITION BY h.thscode ORDER BY h.date_ms DESC) AS rn_desc
          FROM hist h JOIN day_cnt d USING (thscode)
          WHERE h.date_ms <= d.day_ms AND d.total >= ?
        )
        SELECT thscode, date_ms, open_price, high_price, low_price, close_price, volume
        FROM bars WHERE rn_desc <= ? ORDER BY thscode, date_ms
        """,
        [day, MIN_BARS, MIN_BARS],
    ).fetchall()
    bars: dict[str, list[dict]] = {}
    for thscode, date_ms, o, h, l, c, v in rows:
        bars.setdefault(thscode, []).append({
            "ts": date_ms, "open": o, "high": h, "low": l, "close": c, "volume": v,
        })
    return bars


def main() -> int:
    if not DB.exists():
        print(f"ERROR: marketdb 不存在：{DB}", file=sys.stderr)
        return 1
    con = duckdb.connect(str(DB), read_only=True)
    try:
        # date_ms 统一为北京日终（UTC 16:00）——交易日以 to_timestamp::DATE（=北京日−1 的
        # UTC 标签）计；对相关分析而言标签本身无谓，只需全库一致。取 DISTINCT 交易日
        # 列表，把样本日对齐到「首个不早于目标」的真实交易日（护栏：找不到就跳过该样本）。
        trading_days = [r[0] for r in con.execute(
            "SELECT DISTINCT to_timestamp(date_ms/1000)::DATE AS d FROM daily_k ORDER BY d"
        ).fetchall()]
        print(f"## tech_score 五维同源假设·截面相关验证（marketdb，{beijing_today().isoformat()}）\n")
        for day in SAMPLE_DATES:
            target = date.fromisoformat(day)
            chosen = next((d for d in trading_days if d >= target), None)
            if chosen is None:
                print(f"- {day}：库内无对齐交易日，跳过")
                continue
            actual = chosen.isoformat()
            bars_map = _load_bars(con, chosen)
            dim_rows: list[dict[str, float]] = []
            for _sym, bars in bars_map.items():
                r = score_stock(bars)
                if r is None:
                    continue
                d = r["dimensions"]
                if all(k in d and d[k] is not None for k in DIMS):
                    dim_rows.append({k: float(d[k]) for k in DIMS})
            if len(dim_rows) < 100:
                print(f"- {actual}：有效截面不足（{len(dim_rows)}），跳过")
                continue
            print(f"### 截面 {actual}（n={len(dim_rows)}）\n")
            head = "| 维度 | " + " | ".join(DIMS) + " |"
            sep = "|---|" + "---|" * len(DIMS)
            print(head)
            print(sep)
            for a in DIMS:
                cells = []
                for b in DIMS:
                    r = _pearson([x[a] for x in dim_rows], [x[b] for x in dim_rows]) if a != b else 1.0
                    cells.append(f"{r:+.2f}" if a != b else "1")
                print(f"| {a} | " + " | ".join(cells) + " |")
            pairs = [(a, b) for i, a in enumerate(DIMS) for b in DIMS[i + 1:]]
            high = [(a, b, _pearson([x[a] for x in dim_rows], [x[b] for x in dim_rows]))
                    for a, b in pairs]
            high = [(a, b, r) for a, b, r in high if abs(r) >= 0.6]
            print("\n高度重叠对（|r|≥0.6，对照 IC_CORR_DEDUP=0.70）：" + (
                "、".join(f"{a}-{b} {r:+.2f}" for a, b, r in high) if high else "无") + "\n")
        print("> 口径声明：liquidity/rps 维取中性值不参与矩阵（信号源本就不同）；"
              "raw 日 K 对 MA/MACD/KDJ/RSI 的序关系不变（个股常数比例调整不变）。")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
