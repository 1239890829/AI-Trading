"""KB-TRADE-13 归因×胜率统计（P1-4，可复跑，周报数据源）

用途：按「归因类别/分层 × 清算结果」聚合胜率，暴露系统性误判类别 → 反哺选股标准。
消费：手动跑或议程周报挂载（输出 Markdown 段落可入 docs/daily-review/）。

口径（与 settle_day 一致）：
- verdict：success（收盘≥入选价）/ flat（亏损≤2%）/ fail（>2%）
- 胜率 = success / (success+fail)（flat 计入"不败"，单列）
- 维度 A：layer 分层战绩（pre_limit/quiet_starting/today_strongest/watch_no_entry）
- 维度 B：reason.kind 归因（五类：时事/消息/基本面/情绪/技术——opportunities 链写入；
  临板雷达行无 kind，归 layer=技术面临板 类别统计时如实标注缺失）
- 样本 < 10 的类别标"样本不足"，不当结论（宁缺毋滥 KB-TRADE-11）
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import sqlite3

DB = Path(__file__).resolve().parents[2] / "data" / "ashare.db"

KIND_LABEL = {"news": "时事/消息", "newsletter": "时事/消息", "event": "时事/消息",
              "fundamental": "基本面", "emotion": "情绪面", "technical": "技术面",
              "theme": "题材共振", None: "（无归因）"}


def _kinds(reason_raw: str | None) -> list[str]:
    if not reason_raw:
        return [None]
    try:
        r = json.loads(reason_raw)
    except Exception:
        return [None]
    k = r.get("kind")
    # kind 可能是 "news+emotion" 复合标签，拆开统计（每只可计多类）
    return [p for p in str(k).split("+") if p] if k else [None]


def report() -> str:
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT layer, reason, verdict FROM watch_ledger WHERE verdict IS NOT NULL"
    ).fetchall()
    con.close()

    by_layer: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_kind: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for layer, reason, verdict in rows:
        by_layer[layer][verdict] += 1
        for k in _kinds(reason):
            by_kind[str(k)][verdict] += 1

    def fmt(d: dict[str, int], *, min_n: int = 10) -> str:
        n = sum(d.values())
        win = d.get("success", 0)
        fail = d.get("fail", 0)
        judged = win + fail
        w = f"{win/judged*100:.0f}%" if judged else "--"
        line = f"  样本 {n:3d} | success {win:3d} / flat {d.get('flat',0):3d} / fail {fail:3d}"
        if judged >= min_n:
            return line + f" | 胜率 {w}"
        if judged:
            return line + f" | 胜率 {w}（样本<{min_n}，仅供参考）"
        return line + " | 无判定样本"

    out = ["### 台账归因×胜率（KB-TRADE-13 / P1-4）", ""]
    out.append("**A. 按分层（layer）**")
    for layer in sorted(by_layer):
        out.append(f"- {layer}:{fmt(by_layer[layer])}")
    out.append("")
    out.append("**B. 按归因 kind**（opportunities 链才有 kind；临板雷达无归因单独列出）")
    for k in sorted(by_kind, key=lambda x: -sum(by_kind[x].values())):
        out.append(f"- {KIND_LABEL.get(k, k)}: {fmt(by_kind[k])}")
    out.append("")
    out.append("_口径：success=收盘≥入选价 / flat=亏损≤2% / fail=>2%；胜率=success/(success+fail)_")
    return "\n".join(out)


if __name__ == "__main__":
    print(report())
    sys.exit(0)
