"""龙虎榜跨日题材轨迹（ths 对标 B3，官方 inspirations 场景 16 方法学）。

回答「资金近 N 日在题材间怎么轮动」——现有龙虎榜是单日榜单，这里把
近 N 日**日榜**按概念标签聚合出迁徙轨迹，与热点验证环（消息→板块→游资确认）互补。

口径纪律（红线继承）：
- **只用 range_days==1 的日榜**。三日榜（range_days=3）是不同区间的累计值，
  混用会让净额方向错乱（2026-08-31 实测：星网锐捷日榜 -9783 万 vs 三日榜 +7252 万）。
- **概念等分守恒**（官方建议口径）：单股净额按其概念标签数等分，
  Σ题材分摊 = 股净额，保证题材维度合计与市场维度一致。这是**展示口径**
  而非真实资金拆分，前端必须标注。
- 无概念标签的股归「未分类」；净额缺失（None）的股跳过——不冒充 0。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

UNCLASSIFIED = "未分类"


def aggregate_concept_trail(daily_records: list[tuple[date, list]]) -> list[dict]:
    """逐日龙虎榜 → 跨日题材净额轨迹（纯函数，零 IO 可测）。

    :param daily_records: [(trade_date, records)] 按日**升序**；records 须为当日榜单
        （函数内再按 range_days==1 过滤一次作双保险）。
    :return: [{concept, daily: [{date, net}], total, first, last}] 按 |total| 降序。
        某概念当日无记录时 net=None（前端断线显示，不冒充 0）。
    """
    series: dict[str, list[float | None]] = defaultdict(list)
    labels: list[str] = []
    for d, records in daily_records:
        labels.append(d.isoformat())
        day_net: dict[str, float] = defaultdict(float)
        for rec in records:
            if getattr(rec, "range_days", 1) not in (None, 1):
                continue
            net = getattr(rec, "net_buy", None)
            if net is None:
                continue
            tags = [t.strip() for t in (getattr(rec, "concept_tags", None) or "").split(",") if t.strip()]
            if not tags:
                tags = [UNCLASSIFIED]
            share = net / len(tags)
            for t in tags:
                day_net[t] += share
        # 对齐：已有概念当日缺席 → 补 None；新概念往前补 None
        for t in series:
            series[t].append(day_net.pop(t, None))
        for t, v in day_net.items():
            series[t] = [None] * (len(labels) - 1) + [v]

    out = []
    for name, vals in series.items():
        present = [v for v in vals if v is not None]
        first = next((v for v in vals if v is not None), None)
        last = next((v for v in reversed(vals) if v is not None), None)
        out.append({
            "concept": name,
            "daily": [{"date": labels[i], "net": vals[i]} for i in range(len(labels))],
            "total": round(sum(present), 0) if present else None,
            "first": round(first, 0) if first is not None else None,
            "last": round(last, 0) if last is not None else None,
        })
    out.sort(key=lambda x: -(abs(x["total"]) if x["total"] is not None else 0))
    return out
