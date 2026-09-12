"""「放量上涨」拆解实证（retro §6.19 实证 A；可复跑，只读）。

背景：外部研究（2026-09-13 视频转述，2016-2025 全 A 931 万个股日）主张——
「放量上涨」是两个独立信息被打包：控制成交量后**方向**对次日收益 +0.147%、
控制方向后**放量**本身 −0.069%；异常量分组呈**倒 U**（适度放量最好、极端放量回落）；
异常量因子单独 RankIC ≈ −0.065。本仓多处规则内嵌「量比大=加分」假设
（tech_score / score_capital / intraday_rules / lurk_pool，见 volume_state.py §来源表），
该主张若在本仓数据成立，是它们的第一条实证边界。

本脚本在 **marketdb（10 年全 A 日 K，~1027 万个股日）** 上独立复刻，口径：

- 样本单元 = 个股交易日；`ret1 = T+1 收盘(复权) / T 收盘 − 1`（**信号强度口径**，
  不含手续费——可成交口径另见 strategy_verify 的市场中性/可成交性纪律）；
- 方向 `dir` = 当日 ret0 符号（±1，0 剔除）；量比 `vratio` = 当日量 / **过去 20 日均量**
  （不含当日，避免自污染）；要求上市 ≥60 日（次新量比不稳）；
- 拆解 = 2×2 计数加权单元均值：**方向效应**（控制量）= 涨侧均值 − 跌侧均值；
  **量效应**（控制方向）= 放量侧（vratio>1）均值 − 缩量侧均值；
- 倒 U = 当日截面 vratio 十分位 → 各组次日收益；
- RankIC = 当日截面 vratio 与 ret1 的秩相关（日频，截面 ≥100 只才计），
  报告均值 / t / >0 占比 / 分年度——阈值对照 factors/evaluate.py 的 IC_ABS_MIN=0.02；
- 稳健性：剔除当日近涨停（主板 ret0 ≥ 9.5%，不可建仓口径，P1-22 双口径教训）。

输出 Markdown 段落。**候选结论不直接改规则**——评分权重调整属口径变更，
实证结论先行入 KB，规则消费方另行拍板。
"""
from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.bjtime import beijing_today  # noqa: E402

# 层级：本脚本在 backend/scripts/，parents[1] = backend/（勿照抄 app/ 下模块的 parents[2]）
DB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"
MIN_HISTORY = 60          # 上市最少交易日（次新量比不稳）
MIN_CROSS_SECTION = 100   # 日频 IC 的最小截面
LIMIT_PROXY = 0.095       # 主板近涨停近似（不可建仓稳健性口径）


def _base_sql(where_extra: str = "") -> str:
    return f"""
WITH px AS (
  SELECT k.thscode, k.date_ms, k.volume, a.close_adj,
         COUNT(*) OVER (PARTITION BY k.thscode ORDER BY k.date_ms
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cnt
  FROM daily_k k JOIN daily_k_adj a USING (thscode, date_ms)
  WHERE k.volume > 0 AND a.close_adj > 0
),
feat AS (
  SELECT thscode, date_ms, volume, close_adj, cnt,
         close_adj / LAG(close_adj) OVER w - 1 AS ret0,
         LEAD(close_adj) OVER w / close_adj - 1 AS ret1,
         volume / NULLIF(AVG(volume) OVER (PARTITION BY thscode ORDER BY date_ms
             ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING), 0) AS vratio
  FROM px
  WINDOW w AS (PARTITION BY thscode ORDER BY date_ms)
),
base AS (
  SELECT *, CASE WHEN ret0 > 0 THEN 1 WHEN ret0 < 0 THEN -1 ELSE 0 END AS dir,
         CASE WHEN thscode LIKE '30%' OR thscode LIKE '68%' OR thscode LIKE '%.BJ'
              THEN 'growth' ELSE 'main' END AS board
  FROM feat
  WHERE cnt >= {MIN_HISTORY} AND vratio IS NOT NULL
    AND ret0 IS NOT NULL AND ret1 IS NOT NULL AND abs(ret1) < 0.5
    {where_extra}
)"""


def _cells(con: duckdb.DuckDBPyConnection, where_extra: str = "") -> list[dict]:
    rows = con.execute(_base_sql(where_extra) + """
      SELECT dir, CASE WHEN vratio > 1 THEN 1 ELSE 0 END AS vol_hi,
             COUNT(*) AS n, AVG(ret1) AS m
      FROM base WHERE dir != 0 GROUP BY 1, 2 ORDER BY 1, 2
    """).fetchall()
    return [{"dir": d, "vol_hi": v, "n": n, "mean": m} for d, v, n, m in rows]


def effects_from_cells(cells: list[dict]) -> dict:
    """2×2 计数加权单元均值 → 方向/量效应（纯函数，供单测）。"""
    def wmean(pred):
        num = sum(c["n"] * c["mean"] for c in cells if pred(c))
        den = sum(c["n"] for c in cells if pred(c))
        return num / den if den else None

    up, down = wmean(lambda c: c["dir"] == 1), wmean(lambda c: c["dir"] == -1)
    hi, lo = wmean(lambda c: c["vol_hi"] == 1), wmean(lambda c: c["vol_hi"] == 0)
    return {
        "up": up, "down": down, "vol_hi": hi, "vol_lo": lo,
        "direction_effect": None if up is None or down is None else up - down,
        "volume_effect": None if hi is None or lo is None else hi - lo,
    }


def _deciles(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    return con.execute(_base_sql() + """
      SELECT d, COUNT(*) AS n, AVG(ret1) AS m
      FROM (SELECT *, ntile(10) OVER (PARTITION BY date_ms ORDER BY vratio) AS d FROM base)
      GROUP BY d ORDER BY d
    """).fetchall()


def _daily_ic(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    return con.execute(_base_sql() + """
      SELECT date_ms, ic, n FROM (
        SELECT date_ms, CORR(r1, r2) AS ic, COUNT(*) AS n FROM (
          SELECT date_ms,
                 RANK() OVER (PARTITION BY date_ms ORDER BY vratio) AS r1,
                 RANK() OVER (PARTITION BY date_ms ORDER BY ret1) AS r2
          FROM base)
        GROUP BY date_ms)
      WHERE n >= {mincs} AND ic IS NOT NULL ORDER BY date_ms
    """.replace("{mincs}", str(MIN_CROSS_SECTION))).fetchall()


def decile_shape(dec: list[tuple]) -> dict:
    """十分位次日收益 → 倒 U 形状摘要（纯函数，供单测）：峰值组与极端组落差。"""
    peak_d, _, peak_m = max(dec, key=lambda x: x[2])
    d10_n, d10_m = dec[-1][1], dec[-1][2]
    return {"peak_d": peak_d, "peak_m": peak_m, "d10_m": d10_m,
            "tail_gap": d10_m - peak_m, "d10_n": d10_n}


def ic_stats(daily: list[tuple]) -> dict:
    """日频 IC 序列 → 均值 / t / >0 占比 / 分年度（纯函数，供单测）。"""
    ics = [ic for _, ic, _ in daily]
    n = len(ics)
    mean = sum(ics) / n
    if n < 2 or max(ics) == min(ics):
        # 退化情形（单点/全同值）：浮点均值噪声会让方差非零、t 爆炸，显式判 0
        var = 0.0
    else:
        var = sum((x - mean) ** 2 for x in ics) / (n - 1)
    std = math.sqrt(var)
    t = mean / std * math.sqrt(n) if std > 0 else 0.0
    pos = sum(1 for x in ics if x > 0) / n
    yearly: dict[int, list[float]] = {}
    for date_ms, ic, _ in daily:
        y = date.fromtimestamp(date_ms / 1000).year
        yearly.setdefault(y, []).append(ic)
    return {
        "n_days": n, "mean": mean, "t": t, "pos_ratio": pos,
        "yearly": {y: sum(v) / len(v) for y, v in sorted(yearly.items())},
    }


def _fmt_pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.3f}%"


def main() -> int:
    if not DB.exists():
        print(f"ERROR: marketdb 不存在：{DB}", file=sys.stderr)
        return 1
    con = duckdb.connect(str(DB), read_only=True)
    try:
        n_all, = con.execute(_base_sql() + "SELECT COUNT(*) FROM base").fetchone()
        span = con.execute(
            _base_sql() + "SELECT to_timestamp(MIN(date_ms)/1000), to_timestamp(MAX(date_ms)/1000) FROM base"
        ).fetchone()

        print(f"## 「放量上涨」拆解实证（marketdb 10y，{beijing_today().isoformat()}）\n")
        print(f"- 样本：{n_all:,} 个股日（{str(span[0])[:10]} ~ {str(span[1])[:10]}，"
              f"上市≥{MIN_HISTORY}日、|次日收益|<50% 剔除数据错误）")
        print("- 口径：ret1 = T+1/T 收盘（复权）−1；vratio = 当日量/过去20日均量（不含当日）\n")

        print("### 1. 2×2 拆解：方向与量的独立贡献（次日收益均值，计数加权）\n")
        cells = _cells(con)
        eff = effects_from_cells(cells)
        print("| 方向 | 量 | 样本 | 次日收益 |")
        print("|---|---|---|---|")
        label = {(1, 1): "涨+放量", (1, 0): "涨+缩量", (-1, 1): "跌+放量", (-1, 0): "跌+缩量"}
        for c in cells:
            print(f"| {label[(c['dir'], c['vol_hi'])]} | — | {c['n']:,} | {_fmt_pct(c['mean'])} |")
        print(f"\n- **方向效应（控制量）= {_fmt_pct(eff['direction_effect'])}**"
              f"（涨 {_fmt_pct(eff['up'])} vs 跌 {_fmt_pct(eff['down'])}）")
        print(f"- **量效应（控制方向）= {_fmt_pct(eff['volume_effect'])}**"
              f"（放量 {_fmt_pct(eff['vol_hi'])} vs 缩量 {_fmt_pct(eff['vol_lo'])}）")
        print("- 对照外部研究：方向 +0.147% / 量 −0.069%——看同号性与量级\n")

        print("### 2. 量比十分位 → 次日收益（倒 U 检验）\n")
        print("| 当日截面量比分位 | 样本 | 次日收益 |")
        print("|---|---|---|")
        dec = _deciles(con)
        for d, n, m in dec:
            print(f"| D{d} | {n:,} | {_fmt_pct(m)} |")
        shape = decile_shape(dec)
        print(f"\n- 峰值组 = D{shape['peak_d']}（{_fmt_pct(shape['peak_m'])}）；"
              f"最极端组 D10 = {_fmt_pct(shape['d10_m'])}"
              f"（对比峰值落差 {_fmt_pct(shape['tail_gap'])}/日）")

        print("### 3. 异常量因子 RankIC（当日截面 vratio vs 次日收益）\n")
        ic = ic_stats(_daily_ic(con))
        print(f"- 日频 IC：均值 **{ic['mean']:+.4f}** · t={ic['t']:+.2f} · "
              f">0 占比 {ic['pos_ratio']:.1%} · 有效截面日 {ic['n_days']}")
        print("- 分年度均值：" + " · ".join(f"{y}:{v:+.4f}" for y, v in ic["yearly"].items()))
        print("- 对照外部研究：IC ≈ −0.065（异常量越大次日越差）；"
              "对照本仓因子门槛 |IC|≥0.02、|ICIR|≥0.30\n")

        print("### 4. 稳健性：剔除当日近涨停（主板 ret0 ≥ 9.5%，不可建仓）\n")
        where = ("AND NOT (ret0 >= 0.095 AND board = 'main')")
        cells2 = _cells(con, where)
        eff2 = effects_from_cells(cells2)
        print(f"- 方向效应 {_fmt_pct(eff2['direction_effect'])} · 量效应 "
              f"{_fmt_pct(eff2['volume_effect'])}（样本 {sum(c['n'] for c in cells2):,}）")
        print("\n> 口径声明：收益为信号强度口径（不含费用/一字板不可成交）；"
              "ST 与北交所 30% 板未单列。**结论只标注实证事实，规则消费方另行拍板。**")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
