"""候选因子结构新颖性筛查（`RSH-003` 切片，可复跑，**只读**）。

用法：
    cd backend && .venv/bin/python -m scripts.factor_novelty                  # 全部候选
    .venv/bin/python -m scripts.factor_novelty willr20 cmo20                 # 指定候选
    .venv/bin/python -m scripts.factor_novelty --lookback 250 --horizon 5 --json /tmp/n.json

## 候选为什么是这四个（不是随手挑的）

账本 `RSH-003` 要求「按结构新颖性优先研究，先做相关性/排序重合/条件贡献筛查」。
本脚本的候选分两类，**各有明确职责**——这不是"再加四个指标"，而是对**判据本身**的检验：

| 候选 | 来源 / 口径 | 预期 | 职责 |
|---|---|---|---|
| `willr20` | Larry Williams %R = (HH−C)/(HH−LL)×(−100)，取 20 日窗 | `duplicate` | **机制自证①**：与既有 `rsv20` 是**仿射**关系（WILLR = 100·rsv20 − 100）⇒ 排序信息完全相同。判据若能抓不到它，就是失效的 |
| `cmo20` | Chande 动量振荡器 = 100·(Σ涨−Σ跌)/(Σ涨+Σ跌)，20 日 | `duplicate` | **机制自证②**：与既有 `sump20` 仿射（CMO = 200·sump20 − 100）。**不同族、不同实现路径**的第二条独立证据 |
| `kurt20` | 20 日收益**超额峰度**（DuckDB `kurtosis`：Fisher 定义 + 样本量偏置校正） | 待实测 | **判别力对照**：池内 40 项**无任何高阶矩**（最低阶是标准差/相关系数）⇒ 若判据把真新的也判重，说明它在"一律判重"、不具判别力 |
| `skew20` | 20 日收益**偏度**（DuckDB `skewness`） | 待实测 | 同上（第二条对照） |

⚠️ **预期列不是判据**：上表的"预期"只说明**该候选是用来检验什么的**；实际档位一律以实测为准
（本仓纪律：方向/结论由实测定，不取文档里的预期——见 `tests/test_factor_report.py`）。
⚠️ **本脚本不新增任何计权因子**：`kurt20` / `skew20` 即便实测 `distinct`，也只是"没有明显冗余证据"，
**不等于应当准入**（准入还需过 `evaluate` 的 IC/ICIR/分层/覆盖率那一整套）；是否转正**待用户拍板**。
⚠️ **`cci20` 为什么不在候选里**：CCI 的分母是**平均绝对偏差（MD）**，而 DuckDB 的 `mad()` 是
**中位绝对偏差**（同名不同物，实测 `duckdb_functions()` 描述确认）⇒ 照抄会**静默换口径**
（数值看着合理、语义已变）。要正确实现需给 base 链加一层（窗口内 `avg(|x − avg(x)|)`），属
**框架扩展**，不在本切片内（已记入账本 `RSH-003` 行的待办）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.bjtime import beijing_now  # noqa: E402
from app.factors.library import FactorDef  # noqa: E402
from app.factors.novelty import (  # noqa: E402
    DEFAULT_LOOKBACK_DAYS,
    screen_candidates,
)

DB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"

#: 20 日窗（与既有 `sump20` / `rsv20` 同窗，使"仿射"结论**可证**）。
_W20 = ("PARTITION BY thscode ORDER BY date_ms "
        "ROWS BETWEEN 19 PRECEDING AND CURRENT ROW")

CANDIDATES: tuple[FactorDef, ...] = (
    FactorDef(
        "willr20", "probe", 21,
        "-100.0 * (max20_h - close_price) / NULLIF(max20_h - min20_l, 0)",
        "TA-Lib WILLR 口径（Larry Williams %R）= (HH−C)/(HH−LL)×(−100)。"
        "**机制自证用**：与既有 `rsv20` = (C−LL)/(HH−LL) 为仿射关系"
        "（WILLR = 100·rsv20 − 100）⇒ 排序信息完全相同。"
        "TA-Lib 默认周期为 14；此处取 20 是**刻意**与 `rsv20` 同窗，"
        "否则两者只是近似、结论会退化成「未定」，失去自证价值。",
    ),
    FactorDef(
        "cmo20", "probe", 21,
        "200.0 * (sump20_num / NULLIF(sump20_den, 0)) - 100.0",
        "Chande 动量振荡器 CMO = 100·(Σ涨−Σ跌)/(Σ涨+Σ跌)，20 日。"
        "**机制自证用**：base 的 `sump20_num/sump20_den` 即 Σ涨/(Σ涨+Σ跌)（既有 `sump20` 因子）"
        "⇒ CMO = 200·sump20 − 100，仿射。与 `willr20` 构成**第二条独立**证据"
        "（不同族、不同实现路径）。",
    ),
    FactorDef(
        "kurt20", "higher_moment", 21,
        f"CASE WHEN COUNT(ret1) OVER ({_W20}) >= 20 "
        f"THEN kurtosis(ret1) OVER ({_W20}) END",
        "20 日收益**超额峰度**（DuckDB `kurtosis`：Fisher 定义 + 按样本量的偏置校正）。"
        "**判别力对照**：池内 40 项无任何高阶矩 ⇒ 结构上是新信息源。"
        "⚠️ 口径声明：这是 DuckDB 的实现定义，**不是** TA-Lib/文献某一特定版本；"
        "窗口有效样本 <20 → NULL（与 vola20/std20 同口径，不凑 0）。",
    ),
    FactorDef(
        "skew20", "higher_moment", 21,
        f"CASE WHEN COUNT(ret1) OVER ({_W20}) >= 20 "
        f"THEN skewness(ret1) OVER ({_W20}) END",
        "20 日收益偏度（DuckDB `skewness`：样本偏度）。同 `kurt20` 的判别力对照。"
        "窗口有效样本 <20 → NULL。",
    ),
)

_BY_NAME = {c.name: c for c in CANDIDATES}


def _fmt(v: float | None, digits: int = 3) -> str:
    return "  --" if v is None else f"{v:+.{digits}f}"


def _render(report: dict, elapsed: float) -> str:
    meta = report["meta"]
    lines = [
        f"## 因子结构新颖性筛查（{beijing_now():%Y-%m-%d %H:%M}）",
        "",
        f"- 口径：前瞻 **T+1 收盘进 / T+{meta['horizon']} 收盘出**；回看 **{meta['lookback_days']} 交易日**"
        f"（实际得 {meta['n_candidate_days']} 个截面日）；既有池 **{meta['n_incumbents']}** 项；"
        f"单日成熟样本门槛 {meta['min_cross_section']}",
        f"- 阈值：可证重复 |秩相关| ≥ **{meta['duplicate_rank_corr']}**；"
        f"冗余提示 ≥ **{meta['redundant_hint_rank_corr']}**（沿用 `IC_CORR_DEDUP`，不新造）；"
        f"头部集 = 前 {meta['topk']['frac']:.0%} 且 ≥{meta['topk']['min']} 只；"
        f"有效截面日 < {meta['min_days_for_verdict']} ⇒ `insufficient_sample`",
        "",
    ]
    for c in report["candidates"]:
        near = c["nearest"]
        lines.append(f"### `{c['name']}` → **{c['verdict']}**")
        lines.append("")
        if near is None:
            lines.append("- 最近邻：无（无有效截面日达标的配对）")
        else:
            lines.append(
                f"- 最近邻：`{near['incumbent']}`（{near['category']}）· "
                f"秩相关 {_fmt(near['rank_corr_mean'])} · 有效日 {near['n_days']}"
            )
        lines.append(f"- 头部重合率（前 K 集合）：{_fmt(c['topk_overlap_mean'])}"
                     f"（中位 {_fmt(c['topk_overlap_median'])}）")
        ic_u = c["ic_unconditional"]
        cond = "、".join(f"{d['group']} {_fmt(d['ic'])}" for d in c["ic_conditional"])
        lines.append(f"- IC：无条件 {_fmt(ic_u)}；近邻三分位组内（低/中/高）= {cond}"
                     f"；条件 |IC| 最大 {_fmt(c.get('ic_conditional_max_abs'))}")
        eligible = [p for p in c["pairs"] if p["verdict_eligible"]]
        runners = "、".join(
            f"`{p['incumbent']}` {_fmt(p['rank_corr_mean'])}" for p in eligible[1:4]
        )
        lines.append(f"- 次近邻：{runners or '无'}")
        if c["thin_pairs"]:
            thin = "、".join(
                f"`{p['incumbent']}` {_fmt(p['rank_corr_mean'])}（{p['n_days']} 日）"
                for p in c["thin_pairs"][:3]
            )
            lines.append(
                f"- ⚠️ 未采信（有效日 < {meta['min_days_for_verdict']}，"
                f"更像但样本不足）：{thin}"
            )
        lines.append("")
    lines.append(f"> 口径与边界：{meta['note']}")
    lines.append(f"> 耗时 {elapsed:.1f}s。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="候选因子结构新颖性筛查（只读）")
    ap.add_argument("names", nargs="*", help="候选名（默认全部）")
    ap.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK_DAYS)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--json", default=None, help="把完整报告另存为 JSON")
    ap.add_argument("--db", default=str(DB))
    #: ⚠️ **必须原样传 `argv`，不得写 `argv or []`**（2026-09-16 实测缺陷，[[KB-ENG-108]]）：
    #: 命令行调用时 `argv is None` ⇒ `argv or []` 塌缩成 `parse_args([])`
    #: ⇒ **整条命令行被静默丢弃**，全部参数回落默认值。
    #: 后果是三者同时发生且互不相关地表象：`--db` 指了合成库却打开真实 marketdb、
    #: `--json` 不落盘、`--lookback 8` 仍按 250 跑满全表 ⇒ 被误诊成"性能问题/环境负载"。
    #: `parse_args(None)` 才是"读 `sys.argv[1:]`"，与显式传空列表是**不同语义**，故不合并二者。
    args = ap.parse_args(argv)

    if not Path(args.db).exists():
        print(f"ERROR: marketdb 不存在：{args.db}", file=sys.stderr)
        return 1
    names = args.names or [c.name for c in CANDIDATES]
    unknown = [n for n in names if n not in _BY_NAME]
    if unknown:
        print(f"ERROR: 未知候选 {unknown}；可选 {sorted(_BY_NAME)}", file=sys.stderr)
        return 1

    con = duckdb.connect(args.db, read_only=True)
    try:
        t0 = time.time()
        report = screen_candidates(
            con,
            [_BY_NAME[n] for n in names],
            lookback_days=args.lookback,
            horizon=args.horizon,
        )
        elapsed = time.time() - t0
    finally:
        con.close()

    report["generated_at"] = beijing_now().isoformat(timespec="seconds")
    report["elapsed_sec"] = round(elapsed, 2)
    print(_render(report, elapsed))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n（完整报告已写入 {args.json}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
