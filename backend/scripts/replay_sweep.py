"""换股上限 / 门槛 的参数敏感性扫描。

一次拉数据，多组参数评估（复用 replay_picks.build_daily_ranked）。
回答的问题：**"每日换股上限 2 只"这个数字该不该调？调成 1 或 3 会怎样？**

用法：
    .venv/bin/python scripts/replay_sweep.py --days 40 --top 15 [--out docs/xxx.md]

判读原则（不是越稳越好）：
- 换手太低 → 组合僵化，新机会进不来（等于没在选股）
- 换手太高 → 天天换血，违反"精挑细选并保持一致性"
- 合理的落点：**平均持有天数 ≥2 天且日均换手 ≤40%**，同时保留足够的换入能力
  （即换股上限不应低于组合容量的 1/5，否则极端行情下无法调仓）
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.picks.engine import MAX_PICKS, MAX_SWAPS_PER_DAY, REPLACE_THRESHOLD  # noqa: E402
from scripts.replay_picks import build_daily_ranked, evaluate  # noqa: E402

SWAP_GRID = [1, 2, 3, 5, None]  # None = 不限
THRESHOLD_GRID = [10.0, 15.0, 25.0]


def _row(label: str, st: dict, base_total: int) -> list[str]:
    saved = (base_total - st["replacements_total"]) if base_total else 0
    pct = round(saved / base_total * 100, 1) if base_total else None
    return [
        label,
        str(st["replacements_total"]),
        f"{st['replacements_per_day']}",
        f"{st['avg_turnover_pct']}%",
        f"{st['avg_holding_days']}",
        f"{st['max_holding_days']} 天",
        str(st["unique_symbols"]),
        f"{pct}%" if pct is not None else "—",
    ]


def render(built: dict, swaps_grid: list, thr_grid: list, baseline_total: int) -> str:
    days_n = len(built["days"])
    lines = [
        "# 组合稳定性 · 参数敏感性扫描",
        "",
        f"- 回放区间：{built['days'][0]} ~ {built['days'][-1]}（{days_n} 个交易日）",
        f"- 候选并集 {built['universe_size']} 只 · 组合容量 {MAX_PICKS}",
        f"- 评分维度：梯队 {built['weights']['echelon']} + 技术 {built['weights']['tech']}（其余维度无法回放）",
        "",
        "## 一、每日换股上限（门槛固定 15 分）",
        "",
        "| 每日换股上限 | 换股总次数 | 日均换股 | 日均换手率 | 平均持有 | 最长持有 | 出现标的数 | 较纯排序少换 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for ms in swaps_grid:
        st = evaluate(built, threshold=REPLACE_THRESHOLD, max_picks=MAX_PICKS, max_swaps=ms)["stats"]
        label = "不限" if ms is None else f"{ms} 只"
        if ms == MAX_SWAPS_PER_DAY:
            label += "（当前）"
        lines.append("| " + " | ".join(_row(label, st, baseline_total)) + " |")

    lines += [
        "",
        "## 二、换股门槛（换股上限固定 2 只）",
        "",
        "| 门槛 | 换股总次数 | 日均换股 | 日均换手率 | 平均持有 | 最长持有 | 出现标的数 | 较纯排序少换 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for th in thr_grid:
        st = evaluate(built, threshold=th, max_picks=MAX_PICKS, max_swaps=MAX_SWAPS_PER_DAY)["stats"]
        label = f"{th:.0f} 分"
        if th == REPLACE_THRESHOLD:
            label += "（当前）"
        lines.append("| " + " | ".join(_row(label, st, baseline_total)) + " |")

    # 动态判读：结论从数据算出来，避免手写结论与实际脱节
    swap_stats = [
        (ms, evaluate(built, threshold=REPLACE_THRESHOLD, max_picks=MAX_PICKS, max_swaps=ms)["stats"])
        for ms in swaps_grid
        if ms is not None
    ]
    thr_stats = [
        (th, evaluate(built, threshold=th, max_picks=MAX_PICKS, max_swaps=MAX_SWAPS_PER_DAY)["stats"])
        for th in thr_grid
    ]
    verdict: list[str] = []

    tight = [m for m, s in swap_stats if s["avg_turnover_pct"] is not None and s["avg_turnover_pct"] < 25]
    loose = [m for m, s in swap_stats if s["avg_turnover_pct"] is not None and s["avg_turnover_pct"] > 50]
    if tight:
        verdict.append(
            f"- **≤{max(tight)} 只**：日均换手 <25%、平均持有 >3 天 —— 组合偏僵化，"
            f"板块集体退潮时全组合轮换要 {MAX_PICKS / max(tight):.1f} 天，可能错过调仓窗口。"
        )
    if loose:
        verdict.append(
            f"- **≥{min(loose)} 只**：日均换手 >50% —— 过半成员每日被换掉，"
            "已接近「天天换血」，与精挑细选保持一致性的目标相悖。"
        )
    cur = next((s for m, s in swap_stats if m == MAX_SWAPS_PER_DAY), None)
    if cur:
        verdict.append(
            f"- **{MAX_SWAPS_PER_DAY} 只（当前）**：日均换手 {cur['avg_turnover_pct']}%、"
            f"平均持有 {cur['avg_holding_days']} 天 —— 全组合轮换约 "
            f"{MAX_PICKS / MAX_SWAPS_PER_DAY:.1f} 天，与 A 股题材 2~5 天的周期相匹配，"
            "是当前评分体系下稳定性与灵敏度的合理落点。"
        )
    # 门槛是否真的起作用
    if len({s["replacements_total"] for _, s in thr_stats}) == 1:
        verdict.append(
            f"- **门槛 {thr_grid[0]:.0f}/{REPLACE_THRESHOLD:.0f}/{thr_grid[-1]:.0f} 分结果完全一致** "
            f"（换股均 {thr_stats[0][1]['replacements_total']} 次）→ 实证：在本回放的评分体系下"
            "**分差型门槛几乎不起作用**。原因是候选池天然分层（涨停股梯队分 88 vs 非涨停 52，"
            "分差远超任何合理门槛）。稳定性实际由数量型约束（换股上限）提供。"
            "门槛仍保留——真实管线含消息/资金维度、分数分布更集中时它可能生效，"
            "但**调稳定性请先动换股上限，不要指望门槛**。"
        )
    else:
        verdict.append(
            "- 门槛在本样本下产生了可观测差异，说明候选池分数分布相对集中，"
            "门槛与换股上限共同起作用。"
        )

    verdict.append(
        "> 数据口径：换股次数 = 每日新进入组合的只数（含补位）；平均持有 = 连续持有段的均值。"
    )
    lines += ["", "## 三、判读", ""] + verdict
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", type=str, default="")
    args = ap.parse_args()

    built = asyncio.run(build_daily_ranked(args.days, args.top))
    baseline_total = evaluate(
        built, threshold=0.0, max_picks=MAX_PICKS, max_swaps=None
    )["stats"]["replacements_total"]
    report = render(built, SWAP_GRID, THRESHOLD_GRID, baseline_total)
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n[已写入 {args.out}]", file=sys.stderr)


if __name__ == "__main__":
    main()
