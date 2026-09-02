"""选股 2.0 批次 D：确认规则历史回测与网格调参（§8 回测协议）。

## 协议（docs/stock-picking-system-2026-09-02.md §8）

- **伪盘前预判**：T-1 日涨停池归因题材 top3 作为 T 日"预判方向"
  （消息面无历史、前向积累前的替代锚）。
- **确认回放**：对每个 (T, 题材) 跑 §5.1 确认规则（**收盘口径日 K 近似**）。
  线上与回测必须是同一份规则代码——阈值经 `confirm_signal` 的可选覆盖参数
  注入网格值，线上调用方不传（None = 模块常量）。
- **收益口径**：确认触发 → T+1 开盘买入、T+3 收盘卖出（板块指数口径）。
- **网格**：板块涨幅 1.0/1.5/2.0/2.5 × 量比 1.3/1.5/2.0 命中率表。
  报告给建议，**不自动回写常量**——防过拟合，回写须人工拍板。
- **防过拟合**：样本 ≥120 个交易日；报告必须同时给"触发"与"未触发"基线对比。

## 数据边界（诚实降级，报告局限区必须携带）

- **phase 不回放**：精确回放需要全市场快照历史（缺）→ confirm 输入 phase=None，
  环境项仅由 promo 分位判定（promo_1to2 在截止 T 日窗口的分位，与线上
  `calibration.describe` 同口径）。退潮/冰点否决在回测中测不到。
- **题材→板块映射**：与盘中 `watcher.match_board_pct` 同口径
  （精确 → 双向包含取最短板块名）；匹配不到的方向如实计入 `unmatched`。
- **leader_pct**：题材内连板最高成员的涨停池 `change_pct`（涨停事实，
  非臆造）；缺字段 → unknown 三态。
- 板块涨幅/量比（turnover 比）/前瞻收益：ths 官方板块日 K
  （`theme_catalog_service.parse_board_bars` 已解析 open/turnover）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from app.picks.intraday_rules import confirm_signal
from app.sentiment.calibration import percentile_of
from app.services.theme_service import parse_theme_tags

log = logging.getLogger(__name__)

#: 伪盘前预判的方向数（§8 协议：top3）
TOP_N_DIRECTIONS = 3
#: 网格（§8：板块涨幅 1.0/1.5/2.0/2.5 × 量比 1.3/1.5/2.0）
GRID_PCT = (1.0, 1.5, 2.0, 2.5)
GRID_VR = (1.3, 1.5, 2.0)
#: 报告给出建议所需的最低触发样本数（低于此只呈现不建议）
MIN_TRIGGERED_FOR_ADVICE = 30


# ---------------------------------------------------------------- 纯函数：题材聚合


def top_tags(pool, top_n: int = TOP_N_DIRECTIONS) -> list[dict]:
    """T-1 涨停池归因题材 top：[{tag, count, max_boards}]。

    家数降序、最高连板次序（并列时字典序稳定）。reason 缺失的记录
    归入不了任何题材，直接跳过（与 watcher 盘中口径一致）。
    """
    agg: dict[str, dict] = {}
    for r in pool:
        for tag in parse_theme_tags(getattr(r, "reason", None)):
            a = agg.setdefault(tag, {"tag": tag, "count": 0, "max_boards": 0})
            a["count"] += 1
            a["max_boards"] = max(a["max_boards"], getattr(r, "consecutive_boards", None) or 1)
    return sorted(agg.values(), key=lambda x: (-x["count"], -x["max_boards"], x["tag"]))[:top_n]


def tag_stats(pool, tag: str) -> dict | None:
    """T 日涨停池中某题材的确认输入：{limit_up, max_boards, leader_pct}。

    龙头 = 连板最高者（并列取涨幅高者）。无成员 → None（该题材当日冷却，
    不是 unknown——题材没涨停股本身就是事实）。
    """
    members = [r for r in pool if tag in parse_theme_tags(getattr(r, "reason", None))]
    if not members:
        return None
    leader = max(
        members,
        key=lambda r: (
            getattr(r, "consecutive_boards", None) or 1,
            getattr(r, "change_pct", None) if getattr(r, "change_pct", None) is not None else -1e9,
        ),
    )
    return {
        "limit_up": len(members),
        "max_boards": max(getattr(r, "consecutive_boards", None) or 1 for r in members),
        "leader_pct": getattr(leader, "change_pct", None),
    }


def match_board_name(tag: str, board_names: list[str]) -> str | None:
    """题材标签 → ths 官方板块名：精确 → 双向包含取最短（语义最贴近）。

    与盘中 `watcher.match_board_pct` 同口径（那边返回涨幅、这边返回名字，
    匹配语义一致：绝不拿不相干的板块冒充）。匹配不到 → None。
    """
    names = [n for n in board_names if n]
    if tag in names:
        return tag
    candidates = [n for n in names if tag in n or n in tag]
    if not candidates:
        return None
    return min(candidates, key=len)


# ---------------------------------------------------------------- 纯函数：板块日 K 视图


def day_view(bars: list[dict], i: int) -> dict | None:
    """板块日 K 第 i 根（需 i≥1）的回测视图。

    pct = T 日涨幅；vr = turnover(T)/turnover(T-1)（收盘口径，与
    compute_volume_ratio 收盘 elapsed=1.0 等价）；fwd = T+1 open →
    T+3 close 收益（%）。任一输入缺失 → 对应项 None；fwd 窗口越界 → None。
    """
    if i < 1 or i >= len(bars):
        return None
    b, prev = bars[i], bars[i - 1]
    if not b.get("close") or not prev.get("close"):
        return None
    pct = round((b["close"] / prev["close"] - 1) * 100, 2)
    to, to_p = b.get("turnover"), prev.get("turnover")
    vr = round(to / to_p, 3) if (to and to_p) else None
    fwd = None
    if i + 3 < len(bars):
        b1, b3 = bars[i + 1], bars[i + 3]
        if b1.get("open") and b3.get("close"):
            fwd = round((b3["close"] / b1["open"] - 1) * 100, 2)
    return {"date": b["date"], "pct": pct, "vr": vr, "fwd": fwd}


# ---------------------------------------------------------------- 纯函数：样本装配与网格


def build_samples(
    *,
    pools_by_day: dict[str, list],
    bars_by_board: dict[str, list[dict]],
    board_names: list[str],
    metric_rows: list[dict],
    top_n: int = TOP_N_DIRECTIONS,
) -> tuple[list[dict], dict]:
    """逐交易日装配 (T, 题材) 样本（纯函数，输入为预取数据）。

    - 预判方向 = T-1 涨停池归因题材 top_n；
    - promo 分位 = T 日 promo_1to2 在 metric_history 截止 T 日（含）窗口的
      分位——与线上 `calibration.describe(rows)` 同口径；
    - phase 恒 None（回放边界，见模块 docstring）。

    Returns:
        (samples, stats)。stats 含 {days, unmatched, no_board_bars, promo_cover}
        供报告"数据边界"区如实呈现。
    """
    promo_series = sorted(
        ((r.get("date"), r.get("promo_1to2")) for r in metric_rows),
        key=lambda x: x[0] or "",
    )
    dates = sorted(pools_by_day)
    samples: list[dict] = []
    unmatched = no_board_bars = promo_cover = 0
    for i in range(1, len(dates)):
        prev_d, d = dates[i - 1], dates[i]
        pool_prev, pool = pools_by_day[prev_d], pools_by_day[d]
        vals = [v for dt, v in promo_series if dt is not None and dt <= d and v is not None]
        today_promo = next((v for dt, v in promo_series if dt == d), None)
        promo_pctile = (
            round(percentile_of(sorted(vals), float(today_promo)), 1)
            if vals and today_promo is not None else None
        )
        if promo_pctile is None:
            promo_cover += 1
        for info in top_tags(pool_prev, top_n):
            tag = info["tag"]
            bname = match_board_name(tag, board_names)
            if bname is None:
                unmatched += 1
            bars = bars_by_board.get(bname or "") or []
            idx = next(
                (k for k, b in enumerate(bars) if b.get("date") == d), None
            )
            view = day_view(bars, idx) if (bars and idx is not None and idx >= 1) else None
            if bname is not None and view is None:
                no_board_bars += 1
            stats = tag_stats(pool, tag)
            samples.append({
                "date": d,
                "tag": tag,
                "board": bname,
                "fwd": view["fwd"] if view else None,
                "inputs": {
                    "theme_pct": view["pct"] if view else None,
                    "theme_limit_up": stats["limit_up"] if stats else None,
                    "theme_max_boards": stats["max_boards"] if stats else None,
                    "leader_pct": stats["leader_pct"] if stats else None,
                    "volume_ratio": view["vr"] if view else None,
                    "promo_percentile": promo_pctile,
                    "phase": None,
                    "now_minutes": 14 * 60,  # 收盘口径 → 默认 late 线（网格覆盖时不生效）
                },
            })
    stats = {
        "days": len(dates) - 1 if len(dates) > 1 else 0,
        "unmatched": unmatched,
        "no_board_bars": no_board_bars,
        "promo_cover": promo_cover,
    }
    return samples, stats


def _stats_of(fwds: list[float]) -> dict:
    """收益序列的 {n, win_rate, mean, median}；空序列 n=0。"""
    n = len(fwds)
    if not n:
        return {"n": 0, "win_rate": None, "mean": None, "median": None}
    s = sorted(fwds)
    med = s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 2)
    return {
        "n": n,
        "win_rate": round(sum(1 for v in fwds if v > 0) / n, 3),
        "mean": round(sum(fwds) / n, 2),
        "median": med,
    }


def evaluate_sample(sample: dict, *, pct_thr: float, vr_thr: float) -> bool:
    """单样本按网格阈值跑确认规则（同一份 confirm_signal 代码）。"""
    c = confirm_signal(
        **sample["inputs"], theme_pct_thr=pct_thr, volume_ratio_thr=vr_thr
    )
    return bool(c["confirmed"])


def run_grid(
    samples: list[dict],
    *,
    pct_grid: tuple[float, ...] = GRID_PCT,
    vr_grid: tuple[float, ...] = GRID_VR,
) -> list[dict]:
    """网格扫描：每组阈值的触发/未触发收益对比（§8 协议核心产出）。

    fwd 缺失（板块 K 线窗口尾）的样本不计入触发或基线，单独在 meta 计数。
    """
    with_fwd = [s for s in samples if s["fwd"] is not None]
    all_fwds = [s["fwd"] for s in with_fwd]
    rows: list[dict] = []
    for p in pct_grid:
        for v in vr_grid:
            trig = [s["fwd"] for s in with_fwd if evaluate_sample(s, pct_thr=p, vr_thr=v)]
            untrig = [
                s["fwd"] for s in with_fwd if not evaluate_sample(s, pct_thr=p, vr_thr=v)
            ]
            rows.append({
                "pct_thr": p,
                "vr_thr": v,
                "triggered": _stats_of(trig),
                "untriggered": _stats_of(untrig),
                "baseline_all": _stats_of(all_fwds),
                "excess": (
                    round(_stats_of(trig)["mean"] - _stats_of(all_fwds)["mean"], 2)
                    if trig and all_fwds else None
                ),
            })
    return rows


def best_combo(rows: list[dict]) -> dict | None:
    """按触发样本数 ≥MIN_TRIGGERED_FOR_ADVICE 且超额收益最高的组合给建议。

    样本不足不硬凑建议（防过拟合纪律）——返回 None，报告写明。
    """
    eligible = [
        r for r in rows
        if r["triggered"]["n"] >= MIN_TRIGGERED_FOR_ADVICE and r["excess"] is not None
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda r: r["excess"])


def format_report(
    rows: list[dict],
    samples: list[dict],
    stats: dict,
    *,
    window: tuple[str, str] | None,
) -> str:
    """回测报告（markdown）。数据边界与防过拟合声明是必备区，不是附录。"""
    lines = [
        "# 选股 2.0 确认规则网格回测报告（批次 D）",
        "",
        f"- 回测窗口：{window[0]} ~ {window[1]}（{stats['days']} 个交易日，协议要求 ≥120）"
        if window else "- 回测窗口：未知",
        f"- 样本：(交易日 × top{TOP_N_DIRECTIONS} 预判题材) 共 {len(samples)} 条",
        f"- 协议：T-1 题材 top{TOP_N_DIRECTIONS} 为伪预判 → §5.1 确认规则 → "
        "T+1 开盘买、T+3 收盘卖（板块指数口径）",
        "",
        "## 数据边界（诚实降级）",
        "",
        f"- phase 不回放（需全市场快照历史）：环境项仅由 promo 分位判定，"
        f"退潮/冰点否决测不到；promo 分位缺失日 {stats['promo_cover']} 天",
        f"- 题材→ths 板块匹配失败 {stats['unmatched']} 条、板块 K 线缺当日数据 "
        f"{stats['no_board_bars']} 条（这些样本 confirm 仍跑但收益不计入）",
        "- 龙头涨幅用涨停池 change_pct（涨停事实）；缺失为 unknown 三态",
        "",
        "## 网格结果",
        "",
        "| 涨幅线 | 量比线 | 触发数 | 触发胜率 | 触发均值% | 未触发均值% | 全样本均值% | 超额% |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        t, u, b = r["triggered"], r["untriggered"], r["baseline_all"]
        wr = "—" if t["win_rate"] is None else f"{t['win_rate']:.1%}"
        lines.append(
            f"| {r['pct_thr']} | {r['vr_thr']} | {t['n']} "
            f"| {wr} "
            f"| {'—' if t['mean'] is None else t['mean']} "
            f"| {'—' if u['mean'] is None else u['mean']} "
            f"| {'—' if b['mean'] is None else b['mean']} "
            f"| {'—' if r['excess'] is None else r['excess']} |"
        )
    best = best_combo(rows)
    lines += ["", "## 建议", ""]
    if best is None:
        lines.append(
            f"无组合触发样本 ≥{MIN_TRIGGERED_FOR_ADVICE} 且超额可算——"
            "本次不给调参建议（防过拟合：宁缺毋滥）。可扩大回测窗口后重跑。"
        )
    else:
        lines.append(
            f"- 建议组合：涨幅线 **{best['pct_thr']}%** × 量比线 **{best['vr_thr']}**"
            f"（触发 {best['triggered']['n']} 次、胜率 "
            f"{best['triggered']['win_rate']:.1%}、超额 {best['excess']:+.2f}%）"
        )
        lines.append("- 是否回写 `intraday_rules` 常量表**须人工拍板**：单窗口最优"
                     "可能是过拟合，建议换窗口复验后再定。")
    lines += [
        "",
        "## 防过拟合声明",
        "",
        f"- 样本 {len(samples)} 条 / {stats['days']} 交易日"
        + ("（满足 ≥120 交易日协议）" if stats["days"] >= 120 else "（**不足 120 交易日协议**，结果仅供参考）"),
        "- 每组阈值同时呈现触发与未触发基线；超额 = 触发均值 − 全样本均值",
        "- 报告不自动回写常量",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- IO：数据采集与入口


async def collect(
    *,
    trade_days: list[date],
    pool_fetcher,
    bars_fetcher,
    catalog: list[dict],
    metric_rows: list[dict],
    top_n: int = TOP_N_DIRECTIONS,
    gap: float = 0.15,
) -> tuple[list[dict], dict, dict[str, list[dict]]]:
    """采集回测数据（IO 层；fetcher 注入以便测试与换源）。

    Args:
        trade_days: 升序交易日（调用方截好窗口，需比样本多 1 天供 T-1）。
        pool_fetcher: async (d: date) -> list[LimitUpRecord]（ths 涨停池）。
        bars_fetcher: async (code: str, calendar_days: int) -> list[dict]
            （parse_board_bars 产物；调用方决定窗口长度，须覆盖全回测期+3 日）。
        catalog: [{code, name}]（ths 官方题材目录）。
        metric_rows: metric_history.history()（本地，无请求）。
        gap: 相邻涨停池请求间隔（秒），别把 ths 配额打满。

    Returns:
        (samples, stats, bars_by_board)——build_samples 的装配产物 + 板块 K 线
        原始数据（报告/复查用）。
    """
    pools_by_day: dict[str, list] = {}
    prev: list | None = None
    prev_date: str | None = None
    for d in trade_days:
        pool = await pool_fetcher(d)
        await asyncio.sleep(gap)
        if pool is None:
            pool = []
        if prev is not None and pool and {r.symbol for r in pool} == {r.symbol for r in prev}:
            # 疑似数据源回退（metric_history 同款哨兵）：该日样本全跳
            log.warning("backtest: identical pool for %s / %s, skip", prev_date, d)
        else:
            pools_by_day[d.isoformat()] = pool
        prev, prev_date = pool, d.isoformat()

    # 需要的板块：所有 (T-1 预判题材) 匹配到的板块去重
    board_names = [c["name"] for c in catalog]
    needed: set[str] = set()
    for d in sorted(pools_by_day)[1:]:
        for info in top_tags(pools_by_day[d], top_n):
            n = match_board_name(info["tag"], board_names)
            if n:
                needed.add(n)
    code_by_name = {c["name"]: c["code"] for c in catalog}
    span = max(((trade_days[-1] - trade_days[0]).days + 10) if trade_days else 0, 400)
    bars_by_board: dict[str, list[dict]] = {}
    for name in sorted(needed):
        code = code_by_name.get(name)
        if not code:
            continue
        try:
            bars_by_board[name] = await bars_fetcher(code, span)
        except Exception as exc:
            log.warning("backtest: board bars %s(%s) failed: %s", name, code, exc)
            bars_by_board[name] = []
        await asyncio.sleep(gap)
    samples, stats = build_samples(
        pools_by_day=pools_by_day,
        bars_by_board=bars_by_board,
        board_names=board_names,
        metric_rows=metric_rows,
        top_n=top_n,
    )
    return samples, stats, bars_by_board


async def run_backtest(*, days: int = 120, provider, catalog_fetcher, bars_fetcher) -> str:
    """一键回测：窗口截取 → 采集 → 网格 → 报告文本。

    provider 需实现 get_limit_up_pool；交易日历走 trade_calendar.tc.trading_days
    （与 morning_brief / metric_history 同口径）。官方日历**含未来日期**（盘后
    调度要用），回测窗口必须先截至"今天之前"再取尾部——未来的空池会污染样本；
    今日也不入窗（盘中池是半截数据，且 T+1/T+3 前瞻收益必然缺失）。
    """
    from app.market import trade_calendar as tc
    from app.market.trading_status import beijing_now

    all_days = await tc.trading_days(provider, lookback_days=days + 30)
    today = beijing_now().date()
    past = sorted(d for d in all_days if d < today)
    window = past[-(days + 1):]
    if len(window) < days + 1:
        log.warning("backtest: only %d trade days available (< %d)", max(len(window) - 1, 0), days)
    catalog = await catalog_fetcher()
    metric_rows = metric_history_rows()
    samples, stats, _ = await collect(
        trade_days=window,
        pool_fetcher=provider.get_limit_up_pool,
        bars_fetcher=bars_fetcher,
        catalog=catalog,
        metric_rows=metric_rows,
    )
    rows = run_grid(samples)
    w = (window[0].isoformat(), window[-1].isoformat()) if window else None
    return format_report(rows, samples, stats, window=w)


def metric_history_rows() -> list[dict]:
    """metric_history 读取（隔离成函数：测试可 monkeypatch）。"""
    from app.sentiment import metric_history

    return metric_history.history()
