"""组合参数 × 情绪相位 分层敏感性（P2-38 审计 B4，§6.25）。

回答的问题（条件化审计 B4）：MIN_PICK_SCORE=50 / MAX_SWAPS_PER_DAY=2 / 换股门槛 15 分
这三个「全相位统一」的参数，最优值是否随市场热度变化？——用同一份回放数据
（build 一次），按三个**相位代理**分桶聚合逐日结果：

- 涨停家数三分位（情绪热度）
- 炸板率三分位（分歧度）
- 晋级率三分位（接力质量）

数据源：metric_history（249 日涨停生态）× replay_picks（60 日回放，与 replay_sweep
同一条 build 路径）。MIN_PICK_SCORE 轴 = 对 carry_ranked 施加分数下限构造变体
（入选门槛在回放语义中 = 候选分数下限）。

判读：若「最优参数组合」跨相位稳定 → 统一参数成立（B4 假设否掉）；
若跨相位翻转（如高热日要松上限、冰点日要紧门槛）→ 分相位参数有据。
⚠️ 评分维度仅梯队+技术（其余四维无法回放，见 replay_picks docstring）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json  # noqa: E402

from scripts.replay_sweep import build_daily_ranked  # noqa: E402
from scripts.replay_picks import evaluate  # noqa: E402
from app.picks.engine import MAX_PICKS, MAX_SWAPS_PER_DAY, REPLACE_THRESHOLD  # noqa: E402

METRIC_STORE = Path(__file__).resolve().parents[1] / "data" / "sentiment_metrics.json"
SWAPS_GRID = [1, 2, 3, 5]
FLOOR_GRID = [0.0, 40.0, 60.0]  # 0 = 无下限（对照）；50 为当前值


def load_phase_proxies() -> dict[str, dict]:
    """date → {limit_up, break_rate, promo}（metric_history 前向积累，249 日）。"""
    store = json.loads(METRIC_STORE.read_text(encoding="utf-8"))
    out = {}
    for d, row in store["days"].items():
        out[d] = {
            "limit_up": row.get("limit_up"),
            "break_rate": row.get("break_rate"),
            "promo": row.get("promo_1to2"),
        }
    return out


def tercile_labels(values: dict[str, float], labels: tuple[str, str, str]) -> dict[str, str]:
    """按三分位把日期映射到标签（确定性：分位点取自全期分布）。"""
    xs = sorted(v for v in values.values() if v is not None)
    if len(xs) < 9:
        return {}
    q1, q2 = xs[len(xs) // 3], xs[2 * len(xs) // 3]
    out = {}
    for d, v in values.items():
        if v is None:
            continue
        out[d] = labels[0] if v <= q1 else (labels[1] if v <= q2 else labels[2])
    return out


def apply_score_floor(built: dict, floor: float) -> dict:
    """对 carry_ranked/base_ranked 施加候选分数下限（入选门槛的回放语义）。"""
    if floor <= 0:
        return built
    variant = dict(built)
    variant["carry_ranked"] = [
        (d, [c for c in cands if c.get("score") is not None and c["score"] >= floor])
        for d, cands in built["carry_ranked"]
    ]
    variant["base_ranked"] = [
        (d, [c for c in cands if c.get("score") is not None and c["score"] >= floor])
        for d, cands in built["base_ranked"]
    ]
    return variant


def main() -> None:
    days_n = 60
    top_n = 15
    print(f"build 回放数据（{days_n} 交易日 · top {top_n}）…", file=sys.stderr)
    built = asyncio.run(build_daily_ranked(days_n, top_n))
    days = built["days"]

    proxies = load_phase_proxies()
    heat = tercile_labels({d: p.get("limit_up") for d, p in proxies.items()},
                          ("低热", "中热", "高热"))
    diverge = tercile_labels({d: p.get("break_rate") for d, p in proxies.items()},
                             ("低分歧", "中分歧", "高分歧"))
    relay = tercile_labels({d: p.get("promo") for d, p in proxies.items()},
                           ("弱接力", "中接力", "强接力"))
    covered = sum(1 for d in days if d.isoformat() in heat)
    print(f"相位代理覆盖 {covered}/{len(days)} 日（metric_history 249 日前向积累）", file=sys.stderr)

    # 逐组合评估 → 逐日明细
    combos = []
    for ms in SWAPS_GRID:
        for floor in FLOOR_GRID:
            variant = apply_score_floor(built, floor)
            res = evaluate(variant, threshold=REPLACE_THRESHOLD, max_picks=MAX_PICKS, max_swaps=ms)
            combos.append({"ms": ms, "floor": floor, "daily": res["daily"]})

    def agg(combo: dict, day_labels: dict[str, str], label: str) -> list[str]:
        sel = [row for row in combo["daily"] if day_labels.get(row["date"]) == label]
        if not sel:
            return ["—"] * 4
        n = len(sel)
        swaps = sum(len(r["replaced"]) for r in sel)
        turn = swaps / max(1, n) / max(1, MAX_PICKS) * 100
        scores = [r.get("score_avg") for r in sel if isinstance(r.get("score_avg"), (int, float))]
        avg_score = round(sum(scores) / len(scores), 1) if scores else None
        picks_avg = round(sum(len(r["symbols"]) for r in sel) / n, 1)
        return [str(n), f"{swaps / n:.2f}", f"{turn:.0f}%",
                f"{avg_score if avg_score is not None else '—'}", f"{picks_avg}"]

    lines = [
        "# 组合参数 × 情绪相位 分层敏感性（审计 B4）", "",
        f"- 回放 {len(days)} 交易日（{days[0]}~{days[-1]}）· 换股门槛固定 {REPLACE_THRESHOLD:.0f} 分 · "
        f"容量 {MAX_PICKS} · 评分=梯队+技术",
        f"- 组合格：换股上限 {SWAPS_GRID} × 分数下限 {FLOOR_GRID}（0=无下限对照；50=当前 MIN_PICK_SCORE）",
        "- 读法：**看「最优组合是否跨相位翻转」**——同一相位内不同组合的相对排序跨相位一致 ⇒ 统一参数成立",
        "",
    ]
    for axis_name, axis in [("涨停家数（热度）", heat), ("炸板率（分歧）", diverge),
                            ("晋级率（接力）", relay)]:
        buckets = sorted(set(axis.get(d.isoformat()) for d in days))
        lines.append(f"## 按{axis_name}")
        for bucket in buckets:
            lines.append(f"### {bucket}（{sum(1 for d in days if axis.get(d.isoformat()) == bucket)} 日）")
            lines.append("| 换股上限 | 分数下限 | 天数 | 日均换股 | 换手率 | 组合均分 | 均持仓数 |")
            lines.append("|---|---|---|---|---|---|---|")
            for combo in combos:
                cells = agg(combo, axis, bucket)
                tag = ""
                if combo["ms"] == MAX_SWAPS_PER_DAY and combo["floor"] == 50.0:
                    tag = "（当前）"
                lines.append(f"| {combo['ms']}{tag} | {combo['floor']:.0f} | " + " | ".join(cells) + " |")
            lines.append("")

    text = "\n".join(lines)
    print(text)
    out = Path(__file__).resolve().parents[2] / ".workbuddy" / "artifacts" / "b4-phase-sweep.md"
    out.write_text(text, encoding="utf-8")
    print(f"\n已存 {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
