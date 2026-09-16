"""通知中心空态诊断（`BUG-016` 可做子项③，2026-09-16）。

## 为什么需要它

用户现场反馈：**「为什么消息通知一个也没有呢，新闻也没有在里面」**。
取证结论（`BUG-016` 轮，机械可复现）：「新闻不进通知」= **设计**（`IMP-028` 收敛），
而「个股机会 0 条」在历史上**从未有过 1 条** —— 但**读取侧无法区分**下面三种完全不同的处境：

| 处境 | 用户该做什么 | 系统原来能否表达 |
|---|---|---|
| **真无机会**：链路跑了，候选全被否决 | 看否决原因，判断是设计生效还是阈值不可达 | ❌ 与下一行**同形** |
| **链路未跑**：当轮根本没到判定阶段 | 查调度/时段/上游名单 | ❌ 与上一行**同形** |
| **上游空**：盘前就没选出候选 | 看盘前选股 | ❌ 同形 |

三者都表现为 `{"items": [], "count": 0}`。**空响应体本身不含任何能区分它们的信息**
——这正是「不可解释」的根因，也是本模块存在的唯一理由。

⚠️ **本模块只增加可诊断面，不改任何推送口径**（红线：改档位门属交易信号口径变更，须用户拍板）。
它**不重跑判定**、**不臆造**：一律读**已落库的判定证据**，读不到就说读不到。

## 数据来源（全部是既有事实，无新增写入）

1. `opportunity_decision_snapshot`（`RSH-026` 首批建立，`stage='notification'`）——
   买点链每一拍逐股落库的判定证据（`decision` + `evidence.gate_reason` /
   `confidence_tier` / `vetoes` / `observation_only`）。**它的有无即"链路跑没跑"**。
2. `daily_pick_set`（当日盘前精选）—— 候选数、置信档分布、分数区间、空仓闸门状态。
   **它的有无即"上游有没有选出票"**。

## 状态机（四态 + 一降级，互斥且穷尽）

```
no_pick_set  ← 当日精选名单缺失或为空        （上游空：盘前没选出候选）
no_run       ← 有精选名单，但无 notification 判定记录（链路未跑到判定阶段）
ran_rejected ← 有判定记录，全部 rejected      （设计生效 或 阈值不可达 —— 给出原因供判断）
ran_eligible ← 有判定记录且有 eligible/notified（若此时通知仍空 ⇒ 才是真缺陷）
unavailable  ← 读库失败（显式降级，不假装"没有"）
```

⚠️ **`no_run` 与 `ran_rejected` 必须分开**：前者要查调度与时段，后者要看原因。
把两者合并（都报"0 条机会"）就是把「无证据」写成「证据表明没有」——
`BUG-016` 轮已实测踩过一次同类错（把 `opportunity_decision_snapshot` **0 行**读作
"买点循环未跑到判定阶段"，而那 0 行只是**表刚建立**、运行中的进程未必带那段代码）。

## 计数口径（易错，故显式声明）

- `polls` = 判定**拍数**（同一拍同一股一条记录）；
- `reject_reasons` 的 `count` **按 symbol 去重**，不是记录数 ——
  盘中每 60s 一拍，同一只票会被否决几十次，按记录数统计会得到「15 条否决」
  这种**把 1 只票说成 15 次问题**的误导读数；
- ⚠️ `reasons` 只反映**最新一拍**的原因（每股取 `as_of` 最大的一条），
  **不是全天原因分布**。真库实测就撞到过：13:15 读是「置信档 observe 不足」，
  13:21 同一只票已变成「快照无现价（不臆造）」——
  **把最新原因当成全天主因**，会得出「档位门已不是瓶颈」的错误结论
  （而 `BUG-016` 待拍板的恰恰就是档位门）。要看全天分布须另做聚合，本模块**不做**，
  以免把"某一步的现状"包装成"全链路的结论"。
- ⚠️ 更隐蔽的一条：`reasons` 只记**首个未过的门**。`evaluate_buy_points`
  **按门顺序短路**（`快照无现价` → `置信档` → 红线 → 闸门 → 买区 → 涨停区），
  前面没过就**不评估**后面 ⇒ **「原因里没提档位」不等于「档位已通过」**。
  实测形态：最新拒因为「快照无现价（不臆造）」，而 `tier_counts` 同时显示 `observe`
  —— 两条都真，但**不可推出"档位门是通的"**。（[[KB-ENG-103]] 同族：
  只看到经过短路后的一个结果，会得出与事实相反的结论。）
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from sqlalchemy import select

from app.core.bjtime import beijing_now
from app.core.db import get_session_factory

log = logging.getLogger(__name__)

#: 判定记录阶段名（与 `opportunity_learning.STAGES` 同源语义；此处只读不写）
NOTIFICATION_STAGE = "notification"

#: 已通过判定、**应当**出现在通知中心里的 decision 取值。
#: `notified` = 已落库推送；`suppressed` = 当日已推过而按 key 去重（也算"本应有条目"）；
#: `eligible` = 通过判定但尚未派发（例如通知通道不可用）⇒ 同样算"本应有"。
ELIGIBLE_DECISIONS = ("notified", "suppressed", "eligible")

#: 置信档由高到低（`meta_confidence`：`STRONG_SCORE=75` / `EXECUTABLE_SCORE=60`）。
#: ⚠️ **不写"全集"**：未知档位不静默丢弃，而是单列进 `unknown_tiers`（fail-loud）。
TIER_ORDER = ("strong", "executable", "observe")


class _Unavailable(RuntimeError):
    """读库失败（显式降级信号，不吞成"没有数据"）。"""


def _load_json(raw: Any, default: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001  坏值不阻塞诊断，退回 default
        return default


def _collect_pick_set(db, trade_date: str) -> dict:
    """当日盘前精选摘要：候选数 / 档位分布 / 分数区间 / 闸门。

    `daily_pick_set` 缺失 ⇒ `{"present": False}`（= 上游空，不是错误）。
    """
    from app.models.daily_pick import DailyPickSet

    row = db.execute(
        select(DailyPickSet).where(DailyPickSet.date == trade_date)
    ).scalar_one_or_none()
    if row is None:
        return {"present": False, "count": 0}

    items = _load_json(row.items, []) or []
    meta = _load_json(row.meta, {}) or {}
    tiers: Counter[str] = Counter()
    scores: list[float] = []
    observation_only = 0
    no_buy_range = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        tiers[str((it.get("confidence") or {}).get("tier"))] += 1
        score = it.get("score")
        if isinstance(score, (int, float)):
            scores.append(float(score))
        if it.get("observation_only"):
            observation_only += 1
        if it.get("buy_range") is None:
            no_buy_range += 1

    gate = meta.get("gate") or {}
    return {
        "present": True,
        "count": len(items),
        "tier_counts": dict(tiers),
        "score_range": [min(scores), max(scores)] if scores else None,
        "observation_only": observation_only,
        "no_buy_range": no_buy_range,
        "gate": {
            "stand_aside": bool(gate.get("stand_aside")),
            "level": gate.get("level"),
            "phase": gate.get("phase"),
            "reasons": list(gate.get("reasons") or []),
        },
    }


def _collect_decisions(db, trade_date: str) -> dict:
    """当日 `notification` 阶段判定证据 → 拍数 / decision 分布 / 逐股原因。

    **按 symbol 取最新一条**：判定每拍写一条，同一只顾多次被否 ⇒
    逐股原因必须去重，否则会把"1 只票"读成"15 次问题"（见模块 docstring）。
    """
    from app.models.opportunity_learning import OpportunityDecisionSnapshot

    rows = db.execute(
        select(OpportunityDecisionSnapshot)
        .where(
            OpportunityDecisionSnapshot.trade_date == trade_date,
            OpportunityDecisionSnapshot.stage == NOTIFICATION_STAGE,
        )
        .order_by(OpportunityDecisionSnapshot.as_of)
    ).scalars().all()
    if not rows:
        return {"present": False, "polls": 0, "by_decision": {}, "symbols": [], "reasons": [],
                "top_tier": None, "unknown_tiers": [], "last_as_of": None}

    latest: dict[str, Any] = {}
    for r in rows:
        latest[r.symbol] = r          # 已按 as_of 升序 ⇒ 后者覆盖前者 = 最新
    by_decision: Counter[str] = Counter(r.decision for r in latest.values())

    reason_bucket: dict[str, list[str]] = {}
    tiers: Counter[str] = Counter()
    unknown_tiers: set[str] = set()
    for sym, r in latest.items():
        ev = _load_json(r.evidence, {}) or {}
        tier = ev.get("confidence_tier")
        if tier:
            tiers[str(tier)] += 1
            if str(tier) not in TIER_ORDER:
                unknown_tiers.add(str(tier))
        if r.decision not in ELIGIBLE_DECISIONS:
            reason = (ev.get("gate_reason") or "").strip() or "未记录否决原因"
            reason_bucket.setdefault(reason, []).append(sym)

    top_tier = next((t for t in TIER_ORDER if t in tiers), None)
    return {
        "present": True,
        "polls": len(rows),
        "symbols": sorted(latest),
        "by_decision": dict(by_decision),
        "tier_counts": dict(tiers),
        "top_tier": top_tier,
        "unknown_tiers": sorted(unknown_tiers),
        "reasons": [
            {"reason": reason, "count": len(syms), "symbols": sorted(syms)}
            for reason, syms in sorted(reason_bucket.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        ],
        "last_as_of": max(r.as_of for r in rows).isoformat(sep=" "),
    }


def _classify(pick_set: dict, decisions: dict) -> tuple[str, str]:
    """四态判定 → `(state, note)`。互斥且穷尽（`pick_set` 必有一态）。"""
    if not pick_set.get("present") or not pick_set.get("count"):
        return "no_pick_set", (
            f"当日精选名单为空（{pick_set.get('count', 0)} 只候选）⇒ 盘前就没有可跟踪标的，"
            "个股机会自然为 0。这是**上游**结果，与买点门控无关；"
            "请到「每日精选」核对当日选股与空仓闸门。"
        )
    if not decisions.get("present"):
        return "no_run", (
            f"当日精选有 {pick_set['count']} 只候选，但**没有任何买点判定记录** ⇒ "
            "判定链当轮未跑到（常见原因：非交易日 / 不在 09:30–15:05 盘中窗口 / "
            "调度未启用）。此时「0 条通知」**不是**「没有机会」，而是**链路没跑**——"
            "二者在旧响应体里同形，本条即为区分。"
        )
    if decisions["by_decision"].get("rejected", 0) and not any(
        decisions["by_decision"].get(d) for d in ELIGIBLE_DECISIONS
    ):
        top = decisions.get("top_tier")
        return "ran_rejected", (
            f"判定链**已跑**（{decisions['polls']} 拍 / {len(decisions['symbols'])} 只），"
            f"全部被否决；当前最高置信档 = **{top or '无'}**"
            "（需 executable / strong 才推）。下面是逐股**最新一拍**的否决原因。"
            "⚠️ 两条读法纪律：① 判定**按门顺序短路**，只记**首个未过的门**"
            "（`快照无现价` → `置信档` → 红线 → 闸门 → 买区 → 涨停区，前面没过就不评估后面）"
            "⇒ **「原因里没提档位」不等于「档位已通过」**；"
            "② 原因逐拍变化，**不是全天分布**。"
            "全被否决既可能是**设计生效**（弱市不出手），也可能是**阈值不可达**；"
            "本摘要只指出**发生了什么**，不判定哪种 —— 那需要用户拍板口径。"
        )
    return "ran_eligible", (
        "判定链已跑且**存在通过判定的候选**，但通知中心仍为空 ⇒ 这才是需要排查的形态"
        "（上游通过而未落条目）。请检查提醒规则 `__picks_buy_point__` 是否已创建、"
        "派发通道是否可用。"
    )


def notification_diagnostics(
    trade_date: str | None = None, *, session_factory=None,
) -> dict:
    """通知中心空态诊断摘要（**只读**，不重跑判定、不改推送口径）。

    :param trade_date: 目标交易日（`YYYY-MM-DD`）；缺省取**北京当日**。
    :returns: `{"state", "trade_date", "as_of", "pick_set", "decisions", "note"}`
              —— 读库失败时 `state="unavailable"` + `note` 说明原因（**不抛异常**、
              **也不假装"没有机会"**：通知端点不应因为诊断不可用而整体失败）。
    """
    day = trade_date or beijing_now().date().isoformat()
    as_of = beijing_now().replace(tzinfo=None).isoformat(sep=" ")
    try:
        sf = session_factory or get_session_factory()
        with sf() as db:
            pick_set = _collect_pick_set(db, day)
            decisions = _collect_decisions(db, day)
    except Exception as exc:  # noqa: BLE001  诊断不可用不得拖垮通知端点
        log.exception("notification diagnostics failed")
        return {
            "state": "unavailable",
            "trade_date": day,
            "as_of": as_of,
            "pick_set": {"present": False, "count": 0},
            "decisions": {"present": False, "polls": 0},
            "note": (
                f"诊断数据读取失败（{type(exc).__name__}: {exc}）⇒ **本字段为空不等于没有机会**，"
                "请按服务端日志排查。"
            ),
        }

    state, note = _classify(pick_set, decisions)
    return {
        "state": state,
        "trade_date": day,
        "as_of": as_of,
        "pick_set": pick_set,
        "decisions": decisions,
        "note": note,
    }
