"""突破确认条件（BREAKOUT_VOLUME_RATIO=2.0）的**盘中口径**验证（P2-33② 终判，可复跑）。

背景：日线近似（KB-STOCK-32 附记①）已证「量比 ≥2 确认突破」在 T 收盘进口径下是反选；
但生产触发器是**盘中**语义——现价上穿平台高点的**那个时刻**，量比用
`compute_volume_ratio`（当日累计量/昨日全天量/开市占比）实时计算。本脚本在
TDX 5 分钟数据（QFQ，≈2 年）上忠实重演盘中触发，回答：触发时刻量比 ≥2 的突破，
其「触发→当日收盘」收益是否优于量比不足的突破。

口径：
- 突破日定位用日线（T 收盘 > 近 10 日最高收盘，marketdb），按 symbol 抽样
  （seed 固定可复现），每只一次 TDX 5min 拉取（回测底座设计用途）；
- 触发 bar = 当日分钟序列中**首个** close ≥ platform_high 的 bar；
  实时量比 = `compute_volume_ratio(cum_volume, 昨日全天量, now_minutes)`——
  **import 生产函数**（与线上同一实现，P1-22 纪律）；
- 收益主口径 = 触发 bar price → 当日收盘（纯分钟内，无跨源复权基差）；
  辅口径 T+1 = 日线 adj 比率加成（相加近似，声明边界）；
  超额 = 减同日全市场 fwd 中位数（日线）；
- 双口径：全样本 / 剔除触发时刻涨幅 ≥9.5%（主板不可成交近似，P1-22 教训）。

判读纪律：样本 ≥120 且 t 显著才谈方向；与日线近似结论（反选）同号 ⇒ 常量降档
有据；反号 ⇒ 盘中触发确实不同（保留 2.0 并记录）。
"""
from __future__ import annotations

import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.bjtime import BJ_OFFSET, beijing_today  # noqa: E402
from app.market.minute_backfill import fetch_tdx_minutes  # noqa: E402
from app.picks.intraday_rules import BREAKOUT_VOLUME_RATIO, compute_volume_ratio  # noqa: E402

DB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"
HH_WINDOW = 10
SAMPLE_SYMBOLS = 220
SEED = 7
DAY_START = "2024-10-01"   # TDX 5min ≈495 交易日 ≈ 2024-09 起，留余量
LIMIT_PCT_MAIN = 0.095


def _bj_minutes_of_day(ts_iso: str) -> int:
    """分钟点 ts（伪 UTC 编码的北京时间）→ 当日分钟数（0-1439）。"""
    dt = datetime.fromisoformat(ts_iso).replace(tzinfo=timezone.utc) + BJ_OFFSET
    return dt.hour * 60 + dt.minute


def _bj_date(ts_iso: str) -> str:
    dt = datetime.fromisoformat(ts_iso).replace(tzinfo=timezone.utc) + BJ_OFFSET
    return dt.strftime("%Y-%m-%d")


def _t(m: float | None, s: float | None, n: int) -> float:
    if m is None or not s or n < 2:
        return 0.0
    return m / s * math.sqrt(n)


def main() -> int:
    if not DB.exists():
        print(f"ERROR: marketdb 不存在：{DB}", file=sys.stderr)
        return 1
    con = duckdb.connect(str(DB), read_only=True)

    # ① 日线定位突破对（DAY_START 起）+ symbol 抽样
    print("① 定位突破样本（日线）...", file=sys.stderr)
    breakout = con.execute(
        """
        WITH px AS (
          SELECT k.thscode, k.date_ms, k.close_price, k.volume, a.close_adj,
                 COUNT(*) OVER (PARTITION BY k.thscode ORDER BY k.date_ms
                                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cnt
          FROM daily_k k JOIN daily_k_adj a USING (thscode, date_ms)
          WHERE k.volume > 0 AND a.close_adj > 0
        ),
        feat AS (
          SELECT thscode, date_ms, close_adj, volume, cnt,
                 MAX(close_adj) OVER (PARTITION BY thscode ORDER BY date_ms
                     ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) AS hh10
          FROM px
        )
        SELECT thscode, to_timestamp(date_ms/1000)::DATE AS d, hh10
        FROM feat
        WHERE cnt >= 60 AND to_timestamp(date_ms/1000)::DATE >= ?
          AND close_adj > hh10
        """,
        [DAY_START],
    ).fetchall()
    by_symbol: dict[str, list[tuple]] = {}
    for thscode, d, hh in breakout:
        by_symbol.setdefault(thscode, []).append((d, float(hh)))
    rng = random.Random(SEED)
    symbols = sorted(by_symbol)
    rng.shuffle(symbols)
    chosen = symbols[:SAMPLE_SYMBOLS]
    print(f"   突破 symbol 总数 {len(by_symbol)}，抽样 {len(chosen)}（seed={SEED}）", file=sys.stderr)

    # ② 同日市场基线（日线 fwd 中位数，作超额参照）
    mkt = dict(con.execute(
        """
        WITH px AS (
          SELECT thscode, date_ms,
                 LEAD(close_adj, 1) OVER (PARTITION BY thscode ORDER BY date_ms) AS n1,
                 close_adj
          FROM daily_k_adj
        )
        SELECT to_timestamp(date_ms/1000)::DATE, median(n1 / close_adj - 1)
        FROM px WHERE n1 IS NOT NULL AND close_adj > 0
        GROUP BY 1
        """
    ).fetchall())
    # T+1 日线比率（per symbol per day）
    nxt = {}
    for thscode, d_ms, n1, c in con.execute(
        """
        SELECT thscode, date_ms,
               LEAD(close_adj) OVER (PARTITION BY thscode ORDER BY date_ms),
               close_adj
        FROM daily_k_adj
        """
    ).fetchall():
        if n1 and c:
            nxt[(thscode, datetime.fromtimestamp(d_ms / 1000, tz=timezone.utc).date())] = n1 / c - 1

    con.close()

    # ③ 逐 symbol 拉 TDX 5min 并重演盘中触发
    triggers: list[dict] = []  # {symbol, day, vr, tier, r_intraday, r_t1, limit_hit}
    ok_syms = 0
    for i, sym in enumerate(chosen):
        try:
            pts = fetch_tdx_minutes(sym[3:] if False else sym, period="5min", count=24000)
        except Exception as exc:  # noqa: BLE001
            print(f"   [{i+1}/{len(chosen)}] {sym} 拉取失败：{type(exc).__name__}", file=sys.stderr)
            continue
        if len(pts) < 100:
            continue
        ok_syms += 1
        # 按北京日分组（保序）
        days: dict[str, list[dict]] = {}
        for p in pts:
            days.setdefault(_bj_date(p["ts"]), []).append(p)
        day_keys = sorted(days)
        for d, hh in by_symbol.get(sym, []):
            ds = d.isoformat()
            if ds not in days:
                continue
            idx = day_keys.index(ds)
            if idx == 0:
                continue
            prev_vol = sum(b["volume"] for b in days[day_keys[idx - 1]])
            if prev_vol <= 0:
                continue
            bars = [b for b in days[ds] if b["volume"] > 0]
            if len(bars) < 12:
                continue
            trig = next((b for b in bars if b["price"] >= hh), None)
            if trig is None:
                continue
            minute_of_day = _bj_minutes_of_day(trig["ts"])
            vr = compute_volume_ratio(trig["cum_volume"], prev_vol, minute_of_day)
            if vr is None:
                continue
            last_close = bars[-1]["price"]
            r_intraday = last_close / trig["price"] - 1
            prev_close = bars[0]["price"]  # 首 bar≈昨收附近（5min 首根）
            gain_at_trig = trig["price"] / prev_close - 1 if prev_close else 0.0
            r_t1 = nxt.get((sym, d))
            triggers.append({
                "symbol": sym, "day": ds, "vr": vr,
                "r_intraday": r_intraday, "r_t1": r_t1,
                "gain_at_trig": gain_at_trig,
                "mkt_t1": mkt.get(d),
                "main": not (sym.startswith("30") or sym.startswith("68")),
            })
    print(f"② TDX 拉取成功 {ok_syms}/{len(chosen)} 只；盘中触发事件 {len(triggers)} 个", file=sys.stderr)

    # ④ 分组统计
    def stats(rows: list[dict]) -> dict:
        n = len(rows)
        if n == 0:
            return {"n": 0}
        xs = [r["r_intraday"] for r in rows]
        m = sum(xs) / n
        s = (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5 if n > 1 else 0.0
        out = {"n": n, "m": m, "t": _t(m, s, n)}
        t1 = [(r["r_t1"] - (r["mkt_t1"] or 0)) for r in rows if r["r_t1"] is not None]
        if len(t1) >= 30:
            m1 = sum(t1) / len(t1)
            s1 = (sum((x - m1) ** 2 for x in t1) / (len(t1) - 1)) ** 0.5
            out |= {"n_t1": len(t1), "m_ex_t1": m1, "t_ex_t1": _t(m1, s1, len(t1))}
        return out

    def bucket(rows: list[dict]) -> dict:
        return {
            f"vr≥{BREAKOUT_VOLUME_RATIO}（确认组）": stats([r for r in rows if r["vr"] >= BREAKOUT_VOLUME_RATIO]),
            "vr 1.0-2.0（突破未确认）": stats([r for r in rows if 1.0 <= r["vr"] < BREAKOUT_VOLUME_RATIO]),
            "vr <1.0（缩量突破）": stats([r for r in rows if r["vr"] < 1.0]),
        }

    for label, rows in (
        ("全样本", triggers),
        ("剔除触发时涨幅 ≥9.5%（主板）",
         [r for r in triggers if not (r["main"] and r["gain_at_trig"] >= LIMIT_PCT_MAIN)]),
    ):
        print(f"\n### {label}\n")
        print("| 组 | 样本 | 触发→收盘 均值 | t | T+1 市场中性超额 | t |")
        print("|---|---|---|---|---|---|")
        for name, s in bucket(rows).items():
            if s.get("n", 0) == 0:
                print(f"| {name} | 0 | — | — | — | — |")
                continue
            print(f"| {name} | {s['n']:,} | {s['m'] * 100:+.3f}% | {s['t']:+.1f} | "
                  f"{s.get('m_ex_t1', 0) * 100:+.3f}%（n={s.get('n_t1', 0)}） | {s.get('t_ex_t1', 0):+.1f} |")
        # 极端分层
        hi = [r for r in rows if r["vr"] >= 3]
        if hi:
            s = stats(hi)
            print(f"\n- 极端分层 vr≥3：n={s['n']:,} · 触发→收盘 {s['m'] * 100:+.3f}%（t={s['t']:+.1f}）")

    print(f"\n> 口径声明：触发=分钟序列首个 close≥平台高（{HH_WINDOW} 日最高收盘，日线定位）；"
          f"量比=生产 compute_volume_ratio（昨日全天量基线，与线上同实现）；"
          "主收益=触发bar→当日收盘（纯 TDX QFQ 分钟内）；T+1 辅口径=日线 adj 比率相加近似+同日市场中位中性；"
          "5min bar 粒度（实际触发在 bar 内更早时刻，偏差 ≤5 分钟）。")
    print(f"> 生成：{beijing_today().isoformat()} · seed={SEED} · 突破定位窗口 {HH_WINDOW} 日 · 样本期 {DAY_START} 起")
    return 0


if __name__ == "__main__":
    sys.exit(main())
