"""策略级登记册的代码侧承载（P1-37 / P1-38）。

为什么需要本模块：`signal_health.py` 的监控对象原本只有「每日精选组合」**一级**，
且这一级是**隐式**的——没有具名、没有清单、无法回答"我们到底在跑几个策略、
哪个在衰减"。本模块把策略变成**具名的键**，并给每个键挂上取数适配器。

设计要点：
1. **评估逻辑复用**：`evaluate_signal_health` 已是纯函数（接收按日聚合序列，
   不关心来源）⇒ 本模块只做「策略键 → 当日序列」的适配，**不重写评估**。
2. **口径必须显式**（[[KB-ENG-39]] 教训）：不同策略的"表现"口径不同——
   - `market_neutral`：已扣同日市场均值的**超额**（`daily_pick_review.excess_pct`）
     ⇒ CUSUM 测的是「**alpha 是否衰减**」；
   - `absolute`：**绝对收益**（`watch_ledger.pnl_pct`，入选价→收盘价）
     ⇒ CUSUM 测的是「**这个策略自身是否在变差**」，**不是**"还有没有 alpha"。
   两者不可互相解释，故 payload 必带 `basis` 字段，消费方据此措辞。
3. **三态纪律**：样本不足不判 ok —— `insufficient`（组日数不足）/ `thin`
   （总笔数不足）/ `no_pipeline`（该策略无逐日落库，本就无法滚动评估）。
   三者都**不告警**（那不失效信号）。
4. **与文档同步**：键集合与 `docs/strategy-registry.md` §1 总表由测试守卫
   （`tests/test_strategy_registry.py`），改一处必须改另一处。

登记册（人工可读版）见 `docs/strategy-registry.md`；制度见 [[KB-DEC-019]]。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.db import get_session_factory
from app.picks.signal_health import evaluate_signal_health

log = logging.getLogger(__name__)

#: 策略级最小笔数门槛（组日数达标但笔数太少时统计不可靠：
#: 10 天 × 1 笔 = 10 个样本，与 10 天 × 20 笔 = 200 个样本不可同日而语）。
STRATEGY_MIN_PICKS = 20

BASIS_MARKET_NEUTRAL = "market_neutral"
BASIS_ABSOLUTE = "absolute"

STATUS_ACTIVE = "active"
STATUS_OBSERVING = "observing"
STATUS_REJECTED = "rejected"


@dataclass(frozen=True)
class StrategySpec:
    """一个策略键的元数据（与 `docs/strategy-registry.md` §1 总表一一对应）。"""

    key: str
    name: str
    status: str
    basis: str
    source: str
    evaluable: bool
    min_picks: int = STRATEGY_MIN_PICKS
    note: str = ""


SPECS: tuple[StrategySpec, ...] = (
    StrategySpec(
        key="daily_picks",
        name="每日精选六维组合",
        status=STATUS_ACTIVE,
        basis=BASIS_MARKET_NEUTRAL,
        source="daily_pick_review",
        evaluable=True,
        note="收盘定次日；六维评分 + 换股上限 2 只；已有 CUSUM 监控",
    ),
    StrategySpec(
        key="intraday_watch",
        name="盘中跟踪台账",
        status=STATUS_ACTIVE,
        basis=BASIS_ABSOLUTE,
        source="watch_ledger",
        evaluable=True,
        note="盘中首见即登记 → 收盘清算 verdict；P1-38 首次接入监控。"
        "口径为绝对收益（入选价→收盘），非超额",
    ),
    StrategySpec(
        key="pullback_reversal",
        name="超跌反攻（候选B）",
        status=STATUS_OBSERVING,
        basis=BASIS_MARKET_NEUTRAL,
        source="scripts/verify_candidate_b_oos.py",
        evaluable=False,
        note="样本外 +1.33% 但中位 −0.08%、跑赢比例 49.3%（右偏）⇒ 观察不接入",
    ),
    StrategySpec(
        key="triple_volume",
        name="三倍量战法",
        status=STATUS_REJECTED,
        basis=BASIS_MARKET_NEUTRAL,
        source="scripts/verify_triple_volume*.py",
        evaluable=False,
        note="近 250 日/1.79 万条实测负期望、胜率 33.5%/35.9% vs 基准 46.8% ⇒ 已否决",
    ),
    StrategySpec(
        key="two_thirty_five",
        name="两点半五步法（社区原版）",
        status=STATUS_REJECTED,
        basis=BASIS_MARKET_NEUTRAL,
        source="scripts/verify_two_thirty_five.py",
        evaluable=False,
        note="五步全通过 −0.51%、胜率 39.3%、年度 2/11 ⇒ 已否决",
    ),
)

BY_KEY: dict[str, StrategySpec] = {s.key: s for s in SPECS}


# ---------------------------------------------------------------- 取数适配层


def _groups_from_daily_picks(session_factory) -> list[dict]:
    """「每日精选」序列：复用 signal_health 的唯一取数实现（不重写）。"""
    from app.picks.signal_health import collect_daily_pick_groups

    return collect_daily_pick_groups(session_factory)


def _groups_from_watch_ledger(session_factory) -> list[dict]:
    """「盘中跟踪」序列：按 trade_date 聚合已清算行。

    口径注意（见模块 docstring 第 2 条）：
    - `verdict` 映射 success→good / fail→bad / flat→flat；
    - `pnl_pct` 是**绝对收益**（entry_price → close_price），不是超额；
    - **未清算行（status=tracking、verdict IS NULL）一律不参与**——
      "还没结算" ≠ "持平"（三态纪律）。
    """
    from collections import defaultdict

    from sqlalchemy import select

    from app.models.watch_ledger import WatchLedger

    with session_factory() as db:
        rows = db.execute(
            select(WatchLedger).order_by(
                WatchLedger.trade_date.asc(), WatchLedger.symbol.asc()
            )
        ).scalars().all()

    by_date: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "good": 0, "bad": 0, "flat": 0, "ex": []}
    )
    for r in rows:
        if not r.verdict:  # 未清算：不参与统计
            continue
        g = by_date[r.trade_date]
        g["n"] += 1
        if r.verdict == "success":
            g["good"] += 1
        elif r.verdict == "fail":
            g["bad"] += 1
        else:
            g["flat"] += 1
        if r.pnl_pct is not None:
            g["ex"].append(float(r.pnl_pct))

    return [
        {
            "date": d, "phase": None, "n": g["n"],
            "good": g["good"], "bad": g["bad"], "flat": g["flat"],
            "mean_excess": round(sum(g["ex"]) / len(g["ex"]), 3) if g["ex"] else None,
        }
        for d, g in sorted(by_date.items())
    ]


COLLECTORS = {
    "daily_picks": _groups_from_daily_picks,
    "intraday_watch": _groups_from_watch_ledger,
}


# ---------------------------------------------------------------- 评估入口


def list_strategy_keys() -> list[dict]:
    """登记册全量策略键（含不可评估者，便于前端/复盘展示"有哪些策略"）。"""
    return [
        {
            "key": s.key, "name": s.name, "status": s.status,
            "basis": s.basis, "source": s.source,
            "evaluable": s.evaluable, "note": s.note,
        }
        for s in SPECS
    ]


def collect_strategy_health(session_factory, key: str, window: int | None = None) -> dict:
    """单策略键的健康度。不可评估 → `no_pipeline`（显式，不是 ok 也不是 0）。"""
    spec = BY_KEY.get(key)
    if spec is None:
        return {"strategy_key": key, "status": "unknown", "reason": f"未登记的策略键：{key}"}

    base = {
        "strategy_key": spec.key,
        "name": spec.name,
        "lifecycle": spec.status,   # active/observing/rejected —— 生命周期态，区别于健康度
        "basis": spec.basis,
        "source": spec.source,
        "note": spec.note,
    }

    if not spec.evaluable or spec.key not in COLLECTORS:
        return {
            **base,
            "status": "no_pipeline",
            "reason": "该策略无逐日落库，无法滚动评估（证据在 scripts/verify_*.py，需重跑）",
        }

    kwargs = {"min_picks": spec.min_picks}
    if window is not None:
        kwargs["window"] = window

    try:
        groups = COLLECTORS[spec.key](session_factory)
    except Exception as exc:  # noqa: BLE001 — 显式降级，绝不静默当 ok
        log.warning("strategy_registry: %s 取数失败：%s", spec.key, exc)
        return {**base, "status": "error", "reason": str(exc)}

    out = evaluate_signal_health(groups, **kwargs)
    return {**base, **out}


def collect_all_strategy_health(session_factory) -> dict:
    """逐键评估（每键独立状态，**不合并**——合并会掩盖单键衰减）。"""
    items = [collect_strategy_health(session_factory, s.key) for s in SPECS]
    return {
        "strategies": items,
        "counts": {
            "total": len(items),
            "evaluable": sum(1 for s in SPECS if s.evaluable),
            "attention": sum(
                1 for i in items if i.get("status") in ("warning", "drift")
            ),
        },
        "caveat": (
            "basis=market_neutral → CUSUM 测 alpha 衰减；basis=absolute → 测策略自身是否变差"
            "（两者不可互相解释）；insufficient/thin/no_pipeline 均为「判不出」，不是 ok"
        ),
    }


def strategy_verdicts(session_factory) -> list[dict]:
    """仅返回需要关注的策略键（warning/drift），供告警与复盘消费。"""
    out = collect_all_strategy_health(session_factory)
    return [s for s in out["strategies"] if s.get("status") in ("warning", "drift")]


def collect_all_strategy_health_default() -> dict:
    """生产入口（用全局 session factory）。"""
    return collect_all_strategy_health(get_session_factory())
