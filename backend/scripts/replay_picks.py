"""跨日回放：用真实历史数据验证组合稳定性与梯队阶段系数。

用法：
    .venv/bin/python scripts/replay_picks.py [--days 10] [--top 15] [--threshold 15] [--out docs/xxx.md]

能力边界（诚实标注）：完整六维里只有**梯队**与**技术**可由历史数据重建
（涨停池支持历史日期、K 线本来就是历史的）。消息/情绪/基本面/资金依赖
"当前快照"，无法回填到历史某日。所以本脚本验证的是：
    ① 换股门槛的多日稳定性效果（与无门槛对照）
    ② 梯队阶段系数的时变行为（退潮期是否确实压低地位分）
**不是**对完整选股质量的回测。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.data_providers import build_provider  # noqa: E402
from app.market import trade_calendar as tc  # noqa: E402
from app.market.tech_score import score_stock  # noqa: E402
from app.picks.engine import score_tech  # noqa: E402
from app.picks.echelon import (  # noqa: E402
    ROLE_BASE_SCORE,
    classify_echelon_role,
    classify_non_limit_up_role,
    score_echelon,
    theme_ladder_health,
)
from app.picks.engine import (  # noqa: E402
    MAX_PICKS,
    MAX_SWAPS_PER_DAY,
    REPLACE_THRESHOLD,
    apply_replacement_threshold,
)
from app.picks.replay import replay_picks  # noqa: E402
from app.services.theme_service import parse_theme_tags  # noqa: E402

#: 回放评分权重（仅梯队+技术两个可回放维度，归一化）
W_ECHELON = 0.6
W_TECH = 0.4

CONCURRENCY = 6


class RateLimiter:
    """极简间隔限流：两次请求之间至少间隔 1/rps 秒。

    存在的理由：2026-08-31 批量回放的高频请求触发腾讯 WAF 封禁，
    在线选股与详情页的 K 线一起遭殃。批量任务必须自带节流阀，
    绝不与在线服务抢配额。
    """

    def __init__(self, rps: float):
        self._interval = 1.0 / rps if rps and rps > 0 else 0.0
        self._last = 0.0

    async def wait(self) -> None:
        if self._interval <= 0:
            return
        loop = asyncio.get_running_loop()
        now = loop.time()
        delay = self._last + self._interval - now
        if delay > 0:
            await asyncio.sleep(delay)
        self._last = loop.time()


def _assert_off_hours(force: bool) -> None:
    """交易时段（09:15–15:05）默认拒绝运行批量任务。

    批量回放的请求量足以触发数据源限流（腾讯 WAF 已实证一次），
    而限流影响的是**所有在线用户**。收盘后跑是默认纪律；确需盘中跑加 --force。
    """
    now = datetime.now()
    minutes = now.hour * 60 + now.minute
    if 9 * 60 + 15 <= minutes <= 15 * 60 + 5 and not force:
        raise SystemExit(
            "当前处于 A 股交易时段（09:15–15:05），批量回放默认拒绝运行"
            "（会与在线服务抢数据源配额，曾触发腾讯 WAF 封禁）。"
            "收盘后再跑，或加 --force 强制（后果自负）。"
        )


async def _daily_context(provider, d: date, limiter=None) -> dict:
    """某交易日的涨停生态：个股记录 + 全市场最高板 + 题材统计。"""
    try:
        pool = await provider.get_limit_up_pool(d)
    except Exception as exc:
        print(f"  ! {d} 涨停池失败：{exc}", file=sys.stderr)
        return {"records": {}, "market_max_boards": 0, "themes": {}}
    records: dict[str, dict] = {}
    themes: dict[str, dict] = {}
    boards_all: list[int] = []
    for r in pool:
        boards = r.consecutive_boards or 1
        boards_all.append(boards)
        records[r.symbol] = {
            "consecutive_boards": boards,
            "break_count": r.break_count,
            "first_seal_time": r.first_seal_time,
            "float_market_cap": r.float_market_cap,
            "change_pct": r.change_pct,
            "name": r.name,
        }
        for tag in parse_theme_tags(r.reason):
            t = themes.setdefault(
                tag, {"symbols": [], "max_boards": 0, "levels": {}, "changes": []}
            )
            t["symbols"].append(r.symbol)
            t["max_boards"] = max(t["max_boards"], boards)
            t["levels"][boards] = t["levels"].get(boards, 0) + 1
            if r.change_pct is not None:
                t["changes"].append(r.change_pct)  # 题材当日等权涨幅（非涨停股的基准）
    return {
        "records": records,
        "market_max_boards": max(boards_all) if boards_all else 0,
        "themes": themes,
    }


def _echelon_for(ctx: dict, symbol: str, prev_ctx: dict | None = None) -> tuple[str, float, str | None, str, dict]:
    """个股在某日的梯队地位与地位分。

    :param prev_ctx: 前一交易日的涨停生态。题材阶段（启动/发酵/高潮/分歧/退潮）
        本质是**时序判定**——不传前一日统计就永远只能判"启动"，阶段系数的
        时变行为也就无从验证。
    """
    lu = ctx["records"].get(symbol)
    theme_name = None
    best_boards = -1
    for tag, st in (ctx.get("themes") or {}).items():
        if symbol in st["symbols"] and st["max_boards"] > best_boards:
            theme_name, best_boards = tag, st["max_boards"]
    stage_ctx = {"stage": None, "completeness": None}
    if theme_name:
        st = ctx["themes"][theme_name]
        prev_st = ((prev_ctx or {}).get("themes") or {}).get(theme_name) if prev_ctx else None
        stage_ctx = theme_ladder_health(
            limit_up_count=len(st["symbols"]),
            max_boards=st["max_boards"],
            levels=st["levels"],
            prev_limit_up_count=len(prev_st["symbols"]) if prev_st else None,
            prev_max_boards=prev_st["max_boards"] if prev_st else None,
        )
    if lu:
        role, basis = classify_echelon_role(
            is_limit_up=True,
            consecutive_boards=lu["consecutive_boards"],
            theme_max_boards=(ctx["themes"].get(theme_name) or {}).get("max_boards")
            or lu["consecutive_boards"],
            market_max_boards=ctx["market_max_boards"],
            float_market_cap=lu["float_market_cap"],
            first_seal_time=lu["first_seal_time"],
            break_count=lu["break_count"],
        )
    else:
        role, basis = classify_echelon_role(is_limit_up=False, excess_pct=None)
    score, score_basis = score_echelon(
        role=role, stage=stage_ctx["stage"], completeness=stage_ctx["completeness"]
    )
    return role, score, theme_name, basis, {"score_basis": score_basis, **stage_ctx}


def _carryover_echelon(
    ctx: dict,
    symbol: str,
    change_pct: float | None,
    theme_name: str | None,
    prev_ctx: dict | None = None,
) -> tuple[str, float, str | None, dict]:
    """昨日组合成员今日未涨停时的梯队地位：用"相对题材基准的超额"推导。

    carryover 的效果验证依赖这里——若一律按最低档近似，会低估保留成员的价值。
    """
    st = (ctx.get("themes") or {}).get(theme_name) if theme_name else None
    stage_ctx = {"stage": None, "completeness": None}
    if st:
        prev_st = ((prev_ctx or {}).get("themes") or {}).get(theme_name) if prev_ctx else None
        stage_ctx = theme_ladder_health(
            limit_up_count=len(st["symbols"]),
            max_boards=st["max_boards"],
            levels=st["levels"],
            prev_limit_up_count=len(prev_st["symbols"]) if prev_st else None,
            prev_max_boards=prev_st["max_boards"] if prev_st else None,
        )
    excess = None
    if change_pct is not None and st and st.get("changes"):
        excess = round(change_pct - sum(st["changes"]) / len(st["changes"]), 2)
    role, basis = classify_non_limit_up_role(excess_pct=excess)
    score, score_basis = score_echelon(
        role=role, stage=stage_ctx["stage"], completeness=stage_ctx["completeness"]
    )
    return role, score, theme_name, {
        "score_basis": score_basis,
        "basis": f"carryover：{basis}",
        **stage_ctx,
    }


async def _tech_scores(provider, symbols, days, limiter=None) -> tuple[dict, dict]:
    """每只股票按交易日切片的技术分 + 当日涨跌幅。K 线只拉一次全量，内存里按日切。"""
    out: dict[str, dict[date, float]] = {s: {} for s in symbols}
    chg: dict[str, dict[date, float]] = {s: {} for s in symbols}
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(sym: str) -> None:
        if limiter:
            await limiter.wait()
        async with sem:
            try:
                bars = await provider.get_kline(sym, "1d", None, None)
            except Exception:
                return
            dicts = [b.model_dump() if hasattr(b, "model_dump") else dict(b) for b in bars]
            for d in days:
                cutoff = d.isoformat()
                sliced = [b for b in dicts if str(b.get("ts") or "")[:10] <= cutoff][-250:]
                if len(sliced) < 30:  # 样本不足不给分（防飞刀口径需要足够 K 线）
                    continue
                s, _ = score_tech(score_stock(sliced))
                out[sym][d] = s
                last = sliced[-1]
                if str(last.get("ts") or "")[:10] == cutoff and last.get("change_pct") is not None:
                    chg[sym][d] = last["change_pct"]

    await asyncio.gather(*[one(s) for s in symbols])
    return out, chg


async def build_daily_ranked(days_n: int, top_n: int, limiter=None, weight_mode: str = "blended") -> dict:
    """拉取历史数据并构建每日候选评分（与策略参数无关，供 sweep 复用）。

    拆出来的原因：拉一次数据可以评估多组参数（换股上限/门槛），
    避免每组参数都重跑一遍网络请求。
    """
    provider = build_provider(settings)
    all_days = await tc.trading_days(provider)
    days = all_days[-days_n:] if len(all_days) > days_n else all_days
    if len(days) < 2:
        raise SystemExit("交易日不足 2 天，无法回放（换手指标无意义）")
    print(f"回放区间：{days[0]} ~ {days[-1]}（{len(days)} 个交易日）", file=sys.stderr)

    # ① 逐日涨停生态
    ctxs: dict[date, dict] = {}
    for d in days:
        if limiter:
            await limiter.wait()
        ctxs[d] = await _daily_context(provider, d, limiter)
        print(f"  {d}: 涨停 {len(ctxs[d]['records'])} 家 / 最高 {ctxs[d]['market_max_boards']} 板", file=sys.stderr)

    # ② 候选池：每日涨停股按"连板数优先"取前 top_n，跨日取并集
    per_day_syms: dict[date, list[str]] = {}
    for d in days:
        recs = ctxs[d]["records"]
        ranked = sorted(
            recs.items(), key=lambda kv: (-(kv[1]["consecutive_boards"] or 1), -(kv[1]["change_pct"] or 0))
        )
        per_day_syms[d] = [s for s, _ in ranked[:top_n]]
    universe = sorted({s for v in per_day_syms.values() for s in v})
    print(f"候选并集 {len(universe)} 只，开始拉 K 线…", file=sys.stderr)

    tech, changes = await _tech_scores(provider, universe, days, limiter)

    # ③ 逐日评分（梯队 + 技术）。carryover 版本额外把昨日组合成员纳入重评。
    stage_counter: Counter = Counter()
    role_counter: Counter = Counter()
    prev_theme_of: dict[str, str] = {}
    base_ranked: list[tuple[str, list[dict]]] = []
    carry_ranked: list[tuple[str, list[dict]]] = []
    prev_combo: list[str] = []

    for idx, d in enumerate(days):
        ctx = ctxs[d]
        prev_ctx = ctxs[days[idx - 1]] if idx > 0 else None

        def score_one(sym: str, is_carry: bool) -> dict | None:
            if sym in ctx["records"]:
                role, e_score, theme_name, _b, extra = _echelon_for(ctx, sym, prev_ctx)
                if theme_name:
                    prev_theme_of[sym] = theme_name
                basis = ""
            elif is_carry:
                role, e_score, theme_name, extra = _carryover_echelon(
                    ctx, sym, changes.get(sym, {}).get(d), prev_theme_of.get(sym), prev_ctx
                )
                basis = extra.get("basis", "")
            else:
                return None  # 非 carryover 且今日不在涨停池 → 无分可给
            t_score = tech.get(sym, {}).get(d)
            # weight_mode（消融验证 P3，2026-09-01 用户批准）：blended=梯队×0.6+技术×0.4
            # （现行口径）；tech_only=纯技术分（梯队维消融对照）。
            # 消融口径下技术分缺失给中性 50（不能因缺数据把票变相踢出对照）。
            if weight_mode == "tech_only":
                score, note = (round(t_score, 1), "") if t_score is not None else (50.0, "技术分缺失，中性 50")
            elif t_score is None:
                # 技术分缺失时只用梯队分，并标注（不臆造）
                score, note = e_score, "技术分缺失，仅按梯队分"
            else:
                score, note = round(e_score * W_ECHELON + t_score * W_TECH, 1), ""
            if not is_carry:
                stage_counter[extra["stage"] or "无题材"] += 1
                role_counter[role] += 1
            return {
                "symbol": sym, "score": score, "role": role, "theme": theme_name,
                "stage": extra["stage"], "echelon_score": e_score,
                "tech_score": t_score, "note": note, "basis": basis,
            }

        base_cands = [c for c in (score_one(s, False) for s in per_day_syms[d]) if c]
        base_cands.sort(key=lambda c: -c["score"])

        carry_cands = list(base_cands)
        for s in prev_combo:
            if s not in {c["symbol"] for c in carry_cands}:
                c = score_one(s, True)
                if c:
                    carry_cands.append(c)
        carry_cands.sort(key=lambda c: -c["score"])

        base_ranked.append((d.isoformat(), base_cands))
        carry_ranked.append((d.isoformat(), carry_cands))
        # 这里的调用只为维护 prev_combo（carryover 需要知道昨日组合是谁），
        # 与 sweep 的策略参数无关，故用默认常量；真正的评估在 evaluate() 里按参数跑
        kept, _ = apply_replacement_threshold(
            prev_combo, carry_cands, REPLACE_THRESHOLD, MAX_PICKS, MAX_SWAPS_PER_DAY
        )
        prev_combo = [k["symbol"] for k in kept]

    return {
        "days": days,
        "base_ranked": base_ranked,
        "carry_ranked": carry_ranked,
        "universe_size": len(universe),
        "stage_distribution": dict(stage_counter),
        "role_distribution": dict(role_counter),
        "weights": {"echelon": W_ECHELON, "tech": W_TECH},
    }


async def run(
    days_n: int,
    top_n: int,
    threshold: float,
    max_picks: int = MAX_PICKS,
    max_swaps: int | None = MAX_SWAPS_PER_DAY,
    limiter=None,
    weight_mode: str = "blended",
) -> dict:
    """跑一次完整回放：拉数据 + 按给定策略参数评估。"""
    built = await build_daily_ranked(days_n, top_n, limiter, weight_mode=weight_mode)
    return evaluate(built, threshold=threshold, max_picks=max_picks, max_swaps=max_swaps)


def evaluate(built: dict, *, threshold: float, max_picks: int, max_swaps: int | None) -> dict:
    """对已构建的每日候选评分按给定参数评估（纯计算，可反复调用——sweep 依赖这点）。"""
    days = built["days"]
    base_ranked, carry_ranked = built["base_ranked"], built["carry_ranked"]

    # 主结果：carryover + 门槛 + 换股上限
    result = replay_picks(carry_ranked, threshold=threshold, max_picks=max_picks, max_swaps=max_swaps)
    # 对照 A：有门槛但无 carryover（昨日成员不兜底）
    result["no_carryover"] = replay_picks(
        base_ranked, threshold=threshold, max_picks=max_picks, max_swaps=max_swaps
    )["stats"]
    # 对照 C：仅门槛、无每日换股上限（验证上限到底贡献了多少稳定性）
    result["threshold_only"] = replay_picks(
        carry_ranked, threshold=threshold, max_picks=max_picks, max_swaps=None
    )["stats"]
    # 对照 B：无门槛纯排序（无 carryover）
    result["baseline"] = replay_picks(
        base_ranked, threshold=0.0, max_picks=max_picks, max_swaps=None
    )["stats"]
    result["threshold_effect"]["swaps_avoided"] = (
        result["baseline"]["replacements_total"] - result["stats"]["replacements_total"]
    )
    result["threshold_effect"]["swaps_avoided_pct"] = (
        round(
            result["threshold_effect"]["swaps_avoided"]
            / result["baseline"]["replacements_total"] * 100, 1
        )
        if result["baseline"]["replacements_total"]
        else None
    )
    result["stage_distribution"] = built["stage_distribution"]
    result["role_distribution"] = built["role_distribution"]
    result["universe_size"] = built["universe_size"]
    result["weights"] = built["weights"]
    result["daily_detail"] = [
        {
            "date": d.isoformat(),
            "top": [
                {"symbol": c["symbol"], "score": c["score"], "role": c["role"],
                 "theme": c["theme"], "stage": c["stage"], "note": c["note"]}
                for c in cands[:8]
            ],
        }
        for d, cands in zip(days, [c for _, c in carry_ranked])
    ]
    return result


def render(result: dict) -> str:
    s, b = result["stats"], result["baseline"]
    nc = result["no_carryover"]
    to = result["threshold_only"]
    eff = result["threshold_effect"]
    lines = [
        "# 每日精选 · 跨日回放报告",
        "",
        f"- 回放区间：{result['daily'][0]['date']} ~ {result['daily'][-1]['date']}（{s['days']} 个交易日）",
        f"- 候选并集：{result['universe_size']} 只 · 组合容量 {result['max_picks']} · 换股门槛 {result['threshold']} 分 · 每日换股上限 {result.get('max_swaps') or '不限'} 只",
        f"- 评分维度：**梯队 {result['weights']['echelon']} + 技术 {result['weights']['tech']}**（消息/情绪/基本面/资金依赖当前快照，无法回放）",
        "",
        "## 一、组合稳定性（四种策略对照）",
        "",
        "| 指标 | 门槛+上限+carryover | 仅门槛+carryover | 门槛·无carryover | 无门槛纯排序 |",
        "|---|---|---|---|---|",
        f"| 换股总次数 | {s['replacements_total']} | {to['replacements_total']} | {nc['replacements_total']} | {b['replacements_total']} |",
        f"| 日均换股 | {s['replacements_per_day']} | {to['replacements_per_day']} | {nc['replacements_per_day']} | {b['replacements_per_day']} |",
        f"| 日均换手率 | {s['avg_turnover_pct']}% | {to['avg_turnover_pct']}% | {nc['avg_turnover_pct']}% | {b['avg_turnover_pct']}% |",
        f"| 平均持有天数 | {s['avg_holding_days']} | {to['avg_holding_days']} | {nc['avg_holding_days']} | {b['avg_holding_days']} |",
        f"| 最长连续持有 | {s['max_holding_days']} 天 | {to['max_holding_days']} 天 | {nc['max_holding_days']} 天 | {b['max_holding_days']} 天 |",
        f"| 期间出现过的标的 | {s['unique_symbols']} | {to['unique_symbols']} | {nc['unique_symbols']} | {b['unique_symbols']} |",
        "",
        f"**完整策略较纯排序少换 {eff['swaps_avoided']} 次（减少 {eff['swaps_avoided_pct']}%）。**",
        "",
        "> carryover = 昨日组合成员即使今日未进入候选池（没涨停/没上热榜），也兜底纳入重新评分。",
        "> 每日换股上限 = 每日最多换入几只；首次建仓不受限。",
        "",
        "## 二、每日组合轨迹",
        "",
        "| 日期 | 组合成员 | 当日换股 |",
        "|---|---|---|",
    ]
    for row in result["daily"]:
        swaps = "、".join(f"{r['out']}→{r['in']}" for r in row["replaced"]) or "—"
        lines.append(f"| {row['date']} | {' · '.join(row['symbols'])} | {swaps} |")

    lines += [
        "",
        "## 三、梯队阶段与角色分布（验证阶段系数的时变行为）",
        "",
        "**题材阶段分布**："
        + "、".join(f"{k} {v}" for k, v in sorted(result["stage_distribution"].items(), key=lambda kv: -kv[1])),
        "",
        "**梯队角色分布**："
        + "、".join(f"{k} {v}" for k, v in sorted(result["role_distribution"].items(), key=lambda kv: -kv[1])),
        "",
        f"（角色基础分对照：{'、'.join(f'{k} {v:.0f}' for k, v in ROLE_BASE_SCORE.items())}）",
        "",
        "## 四、每日候选前 8 名（含梯队地位与阶段）",
        "",
    ]
    for day in result["daily_detail"]:
        lines.append(f"**{day['date']}**")
        lines.append("")
        lines.append("| 代码 | 综合分 | 梯队角色 | 题材 · 阶段 |")
        lines.append("|---|---|---|---|")
        for c in day["top"]:
            theme = f"{c['theme']} · {c['stage']}" if c["theme"] else "—"
            lines.append(f"| {c['symbol']} | {c['score']} | {c['role']} | {theme} |")
        lines.append("")

    lines += [
        "---",
        "",
        "**结论口径**：本报告只验证换股门槛的多日稳定性与梯队阶段系数的时变行为，",
        "**不是**对完整六维选股质量的回测（消息/情绪/基本面/资金无法回填历史）。",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--top", type=int, default=15, help="每日候选池上限")
    ap.add_argument("--threshold", type=float, default=15.0)
    ap.add_argument("--max-swaps", type=int, default=MAX_SWAPS_PER_DAY, help="每日最多换入几只；0 表示不限")
    ap.add_argument("--rate-limit", type=float, default=8.0, help="每秒最多发起几个数据请求（批量任务节流阀）")
    ap.add_argument("--force", action="store_true", help="交易时段内强制运行（默认拒绝）")
    ap.add_argument("--out", type=str, default="")
    ap.add_argument(
        "--weight-mode",
        type=str,
        default="blended",
        choices=["blended", "tech_only"],
        help="blended=梯队×0.6+技术×0.4（现行）；tech_only=纯技术（消融对照）",
    )
    ap.add_argument(
        "--compare-ablation",
        action="store_true",
        help="消融对照：blended 与 tech_only 各跑一遍，输出并排对照表（消融验证 P3）",
    )
    args = ap.parse_args()

    _assert_off_hours(args.force)
    limiter = RateLimiter(args.rate_limit)

    if args.compare_ablation:
        # 消融对照（P3）：同一时段、同一门槛参数，仅权重口径不同。
        # 差值即「梯队维的边际贡献」；30 个交易日后配合 T+3 超额收益出验收结论。
        blended = asyncio.run(
            run(args.days, args.top, args.threshold, max_swaps=(args.max_swaps or None),
                limiter=limiter, weight_mode="blended")
        )
        tech_only = asyncio.run(
            run(args.days, args.top, args.threshold, max_swaps=(args.max_swaps or None),
                limiter=limiter, weight_mode="tech_only")
        )
        b_stat, t_stat = blended["stats"], tech_only["stats"]
        lines = [
            "# 消融对照：六维混合权重 vs 纯技术分（梯队维消融）",
            "",
            f"回放区间：{args.days} 个交易日 · 门槛 {args.threshold} · 每日换股上限 {args.max_swaps}",
            "",
            "| 指标 | blended（现行） | tech_only（对照） |",
            "|---|---|---|",
            f"| 交易日数 | {b_stat['days']} | {t_stat['days']} |",
            f"| 日均换手% | {b_stat['avg_turnover_pct']} | {t_stat['avg_turnover_pct']} |",
            f"| 平均持有天数 | {b_stat['avg_holding_days']} | {t_stat['avg_holding_days']} |",
            f"| 涉及标的数 | {b_stat['unique_symbols']} | {t_stat['unique_symbols']} |",
            f"| 日均组合分 | {_avg_score(blended)} | {_avg_score(tech_only)} |",
            "",
            "（组合收益对照需配合 T+3 超额验收，见联动方案 P3 验收标准；",
            " 30 个交易日后跑 `--days 30 --compare-ablation --out docs/ablation-report.md` 出正式报告）",
        ]
        report = "\n".join(lines)
    else:
        result = asyncio.run(
            run(args.days, args.top, args.threshold, max_swaps=(args.max_swaps or None),
                limiter=limiter, weight_mode=args.weight_mode)
        )
        report = render(result)
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n[已写入 {args.out}]", file=sys.stderr)


def _avg_score(result: dict) -> float | None:
    """回放结果里每日组合分的均值（对照表用；daily 为逐日轨迹）。"""
    vals = [p.get("score_avg") for p in result.get("daily", []) if isinstance(p.get("score_avg"), (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else None


if __name__ == "__main__":
    main()
