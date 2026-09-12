"""核验：「修复」相位是否应并入 `intraday_rules` 的**进攻档**（账本 §6.5 结转 #4 / E 项）。

## 问题

`intraday_rules.rank_directions` 的 `fit` 项是**相位常量**：防守方向在 `退潮/冰点` 加分，
非防守方向在 `高潮/发酵` 加分，「修复」与「分歧」**两组都 0**（中性档）。
问题 = 「修复」该算进攻档还是中性档。`sentiment.engine.STRONG_PHASES` 含「修复」，
本模块不含 —— 这个分歧必须由数据裁决，不能靠概念推。

## 为什么相位要「重建」而不是读留痕

「修复」由**赚钱效应轴**决定，而该轴 4 项输入里 `median_pct` / `red_rate` /
`limit_down` **三项从未落库**（只存了热度轴的 3 项）；产线相位留痕只有 11 天、
盘前简报只有 8 天 ⇒ 必须现算。

## ⚠️ 已知口径替换（显式声明，不得当作等价）

| 输入 | 生产口径 | 本脚本 | 影响 |
|---|---|---|---|
| `median_pct` / `red_rate` | 收盘时点的**实时快照** | marketdb `daily_k_adj` 收盘价反推 | 日频下等价；盘中不等价（本脚本只做日频） |
| `limit_down` | 实时**跌停池快照** | `price_rules.limit_pct` 从 marketdb **原始收盘价**反推（容差 0.15pct，同 breadth），排除窗口内不足 60 根 K 线的次新 | 次新股/停牌复牌处可能少计 |
| `break_rate` | 实时炸板池 | 直接读**已落库的** `data/sentiment_metrics.json`（当时由真实炸板池算出） | 无替换 |

**校验通道**：与 `sentiment_history.phase` 的真实留痕逐日比对，一致率一并输出。
一致率低则本脚本结论**不可用**（先修口径再谈结论）。

## 判据（KB-ENG-39 / KB-ENG-44）

- 主指标 = **市场中性超额**（进攻篮 − 全市场等权），均值**必须**配中位数与跑赢比例；
- 「进攻」以**当日涨停池成员**为代理 —— 生产里「非防守方向」正是由涨停池题材构造的；
- 相位/热度/赚钱效应三轴一律 **import 生产函数**，核验口径 == 线上口径；
- 拿不到就记 `unchecked`，**绝不换口径顶替**（KB-ENG-55）。

## 用法

    cd backend && .venv/bin/python scripts/verify_intraday_phase_fit.py --days 250
    # 首次会回补历史涨停池（~0.1s/天）并缓存到 data/research/phase_verify/pools.json
    # 二次运行直接读缓存；--refresh 强制重拉。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.bjtime import BJ_TZ  # noqa: E402
from app.data_providers import build_provider  # noqa: E402
from app.market import price_rules, trade_calendar as tc  # noqa: E402
from app.market.marketdb_freshness import DEFAULT_DB_PATH  # noqa: E402
from app.sentiment.engine import (  # noqa: E402
    ADVERSE_PHASES,
    STRONG_PHASES,
    decide_phase,
    earning_axis,
    heat_axis,
    prev_zt_performance,
    promotion_rates,
)
from app.services.market_context import resolve_bands  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "research" / "phase_verify"
POOL_CACHE = CACHE_DIR / "pools.json"
METRICS_PATH = Path(__file__).resolve().parents[1] / "data" / "sentiment_metrics.json"

#: 与 breadth 同源的涨跌停容差（百分点）
_LIMIT_TOLERANCE = 0.15
#: 次新排除线：窗口内 K 线少于该数的标的不参与跌停计数（无名称列，用上市时长近似）
_MIN_BARS = 60
_MS_DAY = 86_400_000


# ---------------------------------------------------------------- 历史涨停池（缓存）


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=BJ_TZ).timestamp() * 1000)


def _load_pool_cache() -> dict:
    if POOL_CACHE.exists():
        try:
            return json.loads(POOL_CACHE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001  缓存损坏 → 重拉，不静默用坏数据
            return {}
    return {}


def _save_pool_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    POOL_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


async def fetch_pools(provider, days: list[date], *, refresh: bool) -> dict[str, list[dict]]:
    """逐日拉涨停池，只留核验需要的字段（symbol / boards / name）。"""
    cache = {} if refresh else _load_pool_cache()
    todo = [d for d in days if d.isoformat() not in cache]
    if todo:
        print(f"回补涨停池 {len(todo)} 天…", file=sys.stderr)
    for i, d in enumerate(todo, 1):
        try:
            pool = await provider.get_limit_up_pool(d)
        except Exception as exc:  # noqa: BLE001  单日失败不中断（缺失按 unknown 处理）
            print(f"  {d} 拉取失败：{exc}", file=sys.stderr)
            continue
        cache[d.isoformat()] = [
            {
                "symbol": str(r.symbol),
                "boards": max(1, int(r.consecutive_boards or 1)),
                "name": r.name,
            }
            for r in pool
        ]
        if i % 25 == 0:
            _save_pool_cache(cache)
    _save_pool_cache(cache)
    return cache


class _PoolRec:
    """`prev_zt_performance` / `promotion_rates` 只用到 symbol / consecutive_boards。"""

    __slots__ = ("symbol", "consecutive_boards")

    def __init__(self, symbol: str, boards: int) -> None:
        self.symbol = symbol
        self.consecutive_boards = boards


def _recs(raw: list[dict]) -> list[_PoolRec]:
    return [_PoolRec(r["symbol"], r["boards"]) for r in raw]


# ---------------------------------------------------------------- marketdb 切片


def load_bars(days: list[date]) -> tuple[dict[str, dict[str, tuple[float, float, float]]], dict[str, int]]:
    """{date_iso: {六位代码: (close_adj, close_raw, open_raw)}} + {六位代码: 窗口内 K 线根数}。

    键统一为**六位代码**：marketdb 的 `thscode` 带交易所后缀（600519.SH），
    而 ths 涨停池的 `symbol` 是裸代码（605188）——不归一会**静默匹配不上**。

    `open_raw` 供**可成交口径**使用：涨停池成员在当日封板是**买不到**的（收盘口径会
    把不可成交的封板溢价算成收益，见 KB-STOCK-31），故必须补一个「次日开盘买入」口径。
    """
    import duckdb

    lo, hi = _ms(days[0]), _ms(days[-1])
    con = duckdb.connect(str(DEFAULT_DB_PATH), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT k.thscode, k.date_ms, a.close_adj, k.close_price, k.open_price
            FROM daily_k k JOIN daily_k_adj a
              ON a.thscode = k.thscode AND a.date_ms = k.date_ms
            WHERE k.date_ms BETWEEN ? AND ?
            """,
            [lo, hi],
        ).fetchall()
    finally:
        con.close()

    bars: dict[str, dict[str, tuple[float, float, float]]] = defaultdict(dict)
    counts: Counter = Counter()
    for sym, dms, adj, raw, opn in rows:
        if adj is None or raw is None or opn is None:
            continue
        code = str(sym).split(".")[0]
        key = datetime.fromtimestamp(dms / 1000.0, tz=BJ_TZ).date().isoformat()
        bars[key][code] = (float(adj), float(raw), float(opn))
        counts[code] += 1
    return dict(bars), dict(counts)


def _fill_limit_up(i: int) -> float:
    """该板块的跌停幅度（百分点）。"""
    return float(price_rules.limit_pct(f"{i:06d}"))


# ---------------------------------------------------------------- 相位重建


def reconstruct(
    days: list[date],
    pools: dict[str, list[dict]],
    bars: dict[str, dict[str, tuple[float, float]]],
    counts: dict[str, int],
    bands: dict,
) -> list[dict]:
    """逐日重建热度/赚钱效应两轴与相位（全部走生产函数）。

    ⚠️ `bands` **必须**来自 `market_context.resolve_bands()`（生产口径：
    env 覆盖 > 历史分位校准 > 经验值）。首版漏了校准层、直接用模块默认经验档，
    结果重建相位与产线只有 8/10 一致且分布失真（退潮 78%）——**档位是口径的一部分**。
    """
    metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))["days"]
    out: list[dict] = []
    for i, d in enumerate(days):
        if i == 0 or i + 1 >= len(days):
            continue  # 需要 D-1（算昨日表现/晋级率）与 D+1（算前瞻）
        prev_d, next_d = days[i - 1], days[i + 1]
        key, pk, nk = d.isoformat(), prev_d.isoformat(), next_d.isoformat()
        if key not in pools or pk not in pools or key not in bars or pk not in bars or nk not in bars:
            continue
        raw_today, raw_prev = pools[key], pools[pk]
        pool_today, pool_prev = _recs(raw_today), _recs(raw_prev)

        # ① 合成「快照」：生产用实时快照，这里用 marketdb 收盘价反推 change_pct
        snap: list[dict] = []
        for sym, (adj_t, _raw_t, _open_t) in bars[key].items():
            prev = bars[pk].get(sym)
            if not prev or prev[0] <= 0:
                continue
            snap.append({"symbol": sym, "change_pct": (adj_t / prev[0] - 1) * 100, "name": None})

        prev_perf = prev_zt_performance(pool_prev, snap)
        promo = promotion_rates(pool_today, pool_prev)

        # ② 热度轴输入：涨停家数/最高伴随来自池；炸板率读已落库的真实值
        limit_up = len(pool_today)
        max_board = max((r["boards"] for r in raw_today), default=0)
        break_rate = (metrics.get(key) or {}).get("break_rate")

        # ③ 跌停家数：marketdb 原始收盘价反推（口径替换，见模块 docstring）
        limit_down = 0
        for sym, (_adj_t, raw_t, _open_t) in bars[key].items():
            if counts.get(sym, 0) < _MIN_BARS:
                continue
            prev = bars[pk].get(sym)
            if not prev or prev[1] <= 0:
                continue
            pct = (raw_t / prev[1] - 1) * 100
            if pct <= -(_fill_limit_up(int(sym) if sym.isdigit() else 0) - _LIMIT_TOLERANCE):
                limit_down += 1

        heat = heat_axis(limit_up, max_board, break_rate, bands=bands.get("heat"))
        earn = earning_axis(
            promo.get("promo_1to2"),
            prev_perf.get("median_pct"),
            prev_perf.get("red_rate"),
            limit_down,
            bands=bands.get("earning"),
        )
        prev_max_board = max((r["boards"] for r in pools[pk]), default=0)
        phase, basis = decide_phase(
            heat["level"], earn["level"],
            max_board=max_board, limit_up=limit_up, height_dropped=max_board < prev_max_board,
        )

        # ④ 前瞻（**双口径**，KB-STOCK-31 强制）：进攻篮 = 当日涨停池成员
        #    · 收盘口径  close(D) → close(D+1)：把「当日封板买不到」的溢价算成收益
        #    · 可成交口径 open(D+1) → close(D+1)：剔除次日**一字板开盘**（开盘即封，买不到）
        nxt, cur_bars = bars[nk], bars[key]
        rets: list[float] = []
        execs: list[float] = []
        for r in raw_today:
            a, b = cur_bars.get(r["symbol"]), nxt.get(r["symbol"])
            if not a or not b or a[0] <= 0 or b[2] <= 0:
                continue
            rets.append((b[0] / a[0] - 1) * 100)
            lim = _fill_limit_up(int(r["symbol"]) if r["symbol"].isdigit() else 0)
            gap = (b[2] / a[1] - 1) * 100 if a[1] > 0 else 0.0
            if gap >= lim - _LIMIT_TOLERANCE:
                continue  # 次日一字板开盘 ⇒ 买不到，不入可成交口径
            execs.append((b[1] / b[2] - 1) * 100)
        mkt: list[float] = []
        mkt_exec: list[float] = []
        for sym, (adj_t, _r, _o) in cur_bars.items():
            b = nxt.get(sym)
            if b and adj_t > 0:
                mkt.append((b[0] / adj_t - 1) * 100)
                if b[2] > 0:
                    mkt_exec.append((b[1] / b[2] - 1) * 100)

        out.append({
            "date": key,
            "phase": phase,
            "basis": basis,
            "heat_level": heat["level"],
            "heat_raw": heat["raw"],
            "earning_level": earn["level"],
            "earning_raw": earn["raw"],
            "limit_up": limit_up,
            "max_board": max_board,
            "break_rate": break_rate,
            "limit_down": limit_down,
            "promo_1to2": promo.get("promo_1to2"),
            "median_pct": prev_perf.get("median_pct"),
            "red_rate": prev_perf.get("red_rate"),
            "attack_n": len(rets),
            "attack_ret": round(sum(rets) / len(rets), 3) if rets else None,
            "market_ret": round(sum(mkt) / len(mkt), 4) if mkt else None,
            "attack_excess": (
                round(sum(rets) / len(rets) - sum(mkt) / len(mkt), 3) if rets and mkt else None
            ),
            "exec_n": len(execs),
            "exec_excess": (
                round(sum(execs) / len(execs) - sum(mkt_exec) / len(mkt_exec), 3)
                if execs and mkt_exec else None
            ),
        })
    return out


# ---------------------------------------------------------------- 统计与判读


def _series(rows: list[dict], phase: str, field: str = "exec_excess") -> list[float]:
    return [r[field] for r in rows if r["phase"] == phase and r.get(field) is not None]


def _stats(v: list[float]) -> dict:
    if not v:
        return {"n": 0}
    mean = sum(v) / len(v)
    sd = statistics.stdev(v) if len(v) > 1 else 0.0
    se = sd / (len(v) ** 0.5) if len(v) > 1 else 0.0
    return {
        "n": len(v),
        "mean": round(mean, 3),
        "median": round(statistics.median(v), 3),
        "win_rate": round(sum(1 for x in v if x > 0) / len(v), 3),
        "t": round(mean / se, 2) if se > 0 else None,
    }


def report(rows: list[dict], history: dict[str, str], prod: dict[str, dict]) -> int:
    print("=" * 78)
    print("① 口径校验：与产线留痕逐项比对（review_reports.payload 是关键——它留下了")
    print("   两轴的 raw/level 与 prev_perf，比只比相位强得多；sentiment_history 只留相位）")
    print("=" * 78)
    common = [r for r in rows if r["date"].replace("-", "") in history]
    fields = ("phase", "heat_raw", "heat_level", "earning_raw", "earning_level",
              "median_pct", "red_rate", "limit_down")
    tally = {f: [0, 0] for f in fields}
    print(f"  {'日期':<12}{'重建相位':<10}{'产线相位':<10}{'热度':<12}{'赚钱':<12}{'中位':>9}{'翻红':>9}{'跌停':>7}")
    for r in common:
        key = r["date"].replace("-", "")
        p = prod.get(key) or {}
        want = history[key]
        sent = p.get("sentiment") or {}
        h, e = sent.get("heat") or {}, sent.get("earning") or {}
        pp = sent.get("prev_perf") or {}
        mine = {
            "phase": r["phase"], "heat_raw": r["heat_raw"], "heat_level": r["heat_level"],
            "earning_raw": r["earning_raw"], "earning_level": r["earning_level"],
            "median_pct": r["median_pct"], "red_rate": r["red_rate"],
            "limit_down": r["limit_down"],
        }
        theirs = {
            "phase": want, "heat_raw": h.get("raw"), "heat_level": h.get("level"),
            "earning_raw": e.get("raw"), "earning_level": e.get("level"),
            "median_pct": pp.get("median_pct"), "red_rate": pp.get("red_rate"),
            "limit_down": (p.get("breadth") or {}).get("limit_down"),
        }
        for f in fields:
            a, b = mine[f], theirs[f]
            if b is None:
                continue
            tally[f][0] += 1
            if a == b:
                tally[f][1] += 1
        print(f"  {r['date']:<12}{r['phase']:<10}{want:<10}"
              f"  {r['heat_raw']:>3}/{r['heat_level']}vs{h.get('raw')}/{h.get('level')}"
              f"  {r['earning_raw']:>3}/{r['earning_level']}vs{e.get('raw')}/{e.get('level')}"
              f"{str(r['median_pct']):>9}{str(r['red_rate']):>9}{str(r['limit_down']):>7}")
    print()
    for f in fields:
        n, ok = tally[f]
        if n:
            flag = "✓" if ok == n else "✗"
            print(f"  {f:<15}{ok}/{n}  {flag}")
    rate = tally["phase"][1] / tally["phase"][0] if tally["phase"][0] else 0.0

    print()
    print("=" * 78)
    print("② 相位频次（重建区间内）")
    print("=" * 78)
    cnt = Counter(r["phase"] for r in rows)
    tot = len(rows)
    for ph in ("冰点", "修复", "发酵", "高潮", "分歧", "退潮"):
        if cnt.get(ph):
            print(f"  {ph:<4} {cnt[ph]:>4} 天  {cnt[ph] / tot:6.1%}")

    print()
    print("=" * 78)
    print("③ 进攻篮次日市场中性超额（%），按相位分组")
    print("   主口径 = **可成交**（次日开盘买入、剔除一字板开盘）；括号内为收盘口径作对照")
    print("=" * 78)
    print(f"  {'相位':<6}{'天数':>5}{'均值(可成交)':>13}{'中位':>9}{'跑赢比例':>10}{'t':>8}"
          f"{'均值(收盘)':>12}")
    for ph in ("冰点", "修复", "发酵", "高潮", "分歧", "退潮"):
        v = _series(rows, ph)
        s = _stats(v)
        if not s["n"]:
            continue
        c = _stats(_series(rows, ph, "attack_excess"))
        print(f"  {ph:<6}{s['n']:>5}{s['mean']:>13.3f}{s['median']:>9.3f}"
              f"{s['win_rate']:>10.1%}{str(s['t']):>8}{c.get('mean', float('nan')):>12.3f}")

    def _grp(phases: tuple) -> list[float]:
        return [x for ph in phases for x in _series(rows, ph)]

    strong, adverse, rep = _grp(("发酵", "高潮")), _grp(("退潮", "冰点")), _series(rows, "修复")
    print()
    print("=" * 78)
    print("④ 判读（主口径 = 可成交）")
    print("=" * 78)
    if not rep:
        print("  ✗ 重建区间内**没有**「修复」日 —— 样本为空，本核验无法裁决（记 unchecked）")
        return 1

    def _line(name: str, v: list[float]) -> None:
        if not v:
            return
        print(f"  {name:<10} n={len(v):<4} 均值 {sum(v) / len(v):+.3f}%"
              f"  中位 {statistics.median(v):+.3f}%  跑赢 {sum(1 for x in v if x > 0) / len(v):.1%}")

    _line("修复", rep)
    _line("高潮+发酵", strong)
    _line("退潮+冰点", adverse)
    if rep and strong and adverse:
        d_strong = sum(rep) / len(rep) - sum(strong) / len(strong)
        d_adverse = sum(rep) / len(rep) - sum(adverse) / len(adverse)
        print()
        print(f"  修复 − (高潮+发酵) = {d_strong:+.3f}pp")
        print(f"  修复 − (退潮+冰点) = {d_adverse:+.3f}pp")
        print("  ⇒ " + (
            "修复更接近**强势组** → 支持并入进攻档"
            if abs(d_strong) < abs(d_adverse) else
            "修复更接近**弱势组** → 支持维持中性档（不并入）"
        ))
    print()
    print(f"  生产常量：STRONG_PHASES={STRONG_PHASES}  ADVERSE_PHASES={ADVERSE_PHASES}")
    # 进攻档**直接读被测模块**，不写死字面量——否则本条输出自己就会过期（会失真的标注）
    from app.picks import intraday_rules as ir

    tier = tuple(ir._ATTACK_PHASES)
    print(f"  intraday_rules 进攻档（实读模块）= {tier}"
          + ("  ← 与 STRONG_PHASES 一致 ✓" if tier == tuple(STRONG_PHASES)
             else "  ← ⚠️ 与 STRONG_PHASES 不一致，属口径分歧"))
    if rate < 0.7:
        print(f"  ⚠️ 相位一致率仅 {rate:.0%}（<70%）⇒ 重建未获校验，**结论不可用**")
        return 2
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=250, help="回看交易日数（默认 250）")
    ap.add_argument("--refresh", action="store_true", help="强制重拉历史涨停池（清缓存）")
    ap.add_argument("--json", type=Path, default=None, help="把逐日明细落盘")
    args = ap.parse_args()

    async def run() -> int:
        provider = build_provider(settings)
        all_days = await tc.trading_days(provider)
        days = all_days[-(args.days + 2):]
        print(f"区间：{days[0]} ~ {days[-1]}（{len(days)} 个交易日）", file=sys.stderr)

        pools = await fetch_pools(provider, days, refresh=args.refresh)
        bars, counts = load_bars(days)
        # 档位必须与产线同源（env 覆盖 > 历史分位校准 > 经验值）
        heat_bands, earn_bands, source, meta = resolve_bands(days)
        print(f"分档来源：{source}"
              f"（{meta.get('reason')}；窗口 {meta['window']['start']}~{meta['window']['end']}"
              f"，stale_days={meta['window']['stale_days']}）", file=sys.stderr)
        rows = reconstruct(days, pools, bars, counts, {"heat": heat_bands, "earning": earn_bands})
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

        db = Path(__file__).resolve().parents[2] / "data" / "ashare.db"
        history: dict[str, str] = {}
        prod: dict[str, dict] = {}
        if db.exists():
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                history = {
                    str(r[0]): str(r[1])
                    for r in con.execute("SELECT trade_date, phase FROM sentiment_history")
                }
                for td, pl in con.execute("SELECT trade_date, payload FROM review_reports"):
                    try:
                        payload = json.loads(pl) if isinstance(pl, str) else pl
                    except Exception:  # noqa: BLE001  坏行跳过，不因一行坏掉整张校验
                        continue
                    node = ((payload or {}).get("data") or {}).get("market") or {}
                    if node:
                        prod[str(td)] = node
            finally:
                con.close()
        # 无 review payload 的日子退回只比相位
        for k, ph in history.items():
            prod.setdefault(k, {"sentiment": {"phase": ph}})
        return report(rows, history, prod)

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
