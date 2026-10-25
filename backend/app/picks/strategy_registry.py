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
4. **与文档同步**：键集合与 `docs/strategy/strategy-registry.md` §1 总表由测试守卫
   （`tests/test_strategy_registry.py`），改一处必须改另一处。

登记册（人工可读版）见 `docs/strategy/strategy-registry.md`；制度见 [[KB-DEC-019]]。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

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
    """一个策略键的元数据（与 `docs/strategy/strategy-registry.md` §1 总表一一对应）。"""

    key: str
    name: str
    status: str
    basis: str
    source: str
    evaluable: bool
    min_picks: int = STRATEGY_MIN_PICKS
    note: str = ""
    #: 对应的**战法核验产物键**（`app/research/verify_registry.py` 的产物名）。
    #: `None` = 该策略不走事件研究式核验（如组合级策略，用滚动健康度监控即可）。
    #: 有值时 `list_strategy_keys()` 会附带 `verification` 三态——让 `status`
    #: 背后有一份带时间戳的可回查证据，而不是人工誊写的一句话。
    verify_key: str | None = None
    # Display taxonomy belongs to this registry, not to note keyword guessing.
    discovery_category: str = "unknown"
    discovery_category_label: str = "未分类"
    discovery_session: str = "unknown"
    discovery_session_label: str = "时段未登记"
    discovery_summary: str = ""
    supports_saved_runs: bool = False


SPECS: tuple[StrategySpec, ...] = (
    StrategySpec(
        key="daily_picks",
        name="每日精选六维组合",
        status=STATUS_ACTIVE,
        basis=BASIS_MARKET_NEUTRAL,
        source="daily_pick_review",
        evaluable=True,
        note="当日持久候选；六维评分 + 换股上限 2 只；CUSUM仅消费同版本同窗口可信观察",
        discovery_category="multi_factor", discovery_category_label="多因子组合",
        discovery_session="daily", discovery_session_label="每日生成",
        discovery_summary="六维评分形成已存名单；入选与模拟执行分别核验",
        supports_saved_runs=True,
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
        discovery_category="intraday", discovery_category_label="盘中跟踪",
        discovery_session="intraday", discovery_session_label="盘中观察",
        discovery_summary="盘中首次入选记录与收盘观察，不代表自动成交",
        supports_saved_runs=True,
    ),
    StrategySpec(
        key="pullback_reversal",
        name="超跌反攻（候选B）",
        status=STATUS_OBSERVING,
        basis=BASIS_MARKET_NEUTRAL,
        source="scripts/verify_candidate_b_oos.py",
        evaluable=False,
        # 2026-09-11 重跑实测（口径已对齐产物）：中性 +1.33% 但**中性**中位 −0.08%、
        # 中性跑赢 49.3%（右偏）⇒ observe。⚠️ 早期记录写的是"中位/跑赢"未标口径，
        # 与"原始中位 +3.33%、跑赢 68.1%"混淆过——**判据一律取中性口径**。
        note="测试段（样本外）T+5 中性 +1.33% 但中性中位 −0.08%、中性跑赢 49.3%（右偏）"
             "⇒ 观察不接入；结论以 verify_registry 产物为准",
        verify_key="pullback_reversal",
        discovery_category="reversal", discovery_category_label="超跌反转",
        discovery_session="research", discovery_session_label="离线研究",
        discovery_summary="样本外观察，尚未接入",
    ),
    StrategySpec(
        key="triple_volume",
        name="三倍量战法",
        status=STATUS_REJECTED,
        basis=BASIS_MARKET_NEUTRAL,
        source="scripts/verify_triple_volume*.py",
        evaluable=False,
        note="近 250 日/1.79 万条实测负期望、胜率 33.5%/35.9% vs 基准 46.8% ⇒ 已否决",
        discovery_category="volume", discovery_category_label="量价形态",
        discovery_session="research", discovery_session_label="离线研究",
        discovery_summary="历史验证已否决，保留反例",
    ),
    StrategySpec(
        key="two_thirty_five",
        name="两点半五步法（社区原版）",
        status=STATUS_REJECTED,
        basis=BASIS_MARKET_NEUTRAL,
        source="scripts/verify_two_thirty_five.py",
        evaluable=False,
        # 2026-09-11 重跑实测：中性 −0.85%、胜率 39.3%、**年度为正 1/11**
        # （早期记录的"年度 2/11"与实测不符，已按产物更正）。
        note="五步全通过 T+5 −0.51%（中性 −0.85%）、胜率 39.3%、年度为正 1/11 ⇒ 已否决；"
             "结论以 verify_registry 产物为准",
        verify_key="two_thirty_five",
        discovery_category="timing", discovery_category_label="盘尾时段",
        discovery_session="research", discovery_session_label="离线研究",
        discovery_summary="盘尾五步方法已否决，保留反例",
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

    # Stream only consumed columns. Keep the complete historical denominator and
    # the original deterministic addition order without retaining ORM rows.
    by_date: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "good": 0, "bad": 0, "flat": 0, "sum": 0.0, "observed": 0}
    )
    with session_factory() as db:
        rows = db.execute(select(WatchLedger.trade_date, WatchLedger.verdict, WatchLedger.pnl_pct)
            .where(WatchLedger.verdict.is_not(None), WatchLedger.verdict != "")
            .order_by(WatchLedger.trade_date.asc(), WatchLedger.symbol.asc())
            .execution_options(yield_per=500))
        for day, verdict, pnl in rows:
            group = by_date[day]
            group["n"] += 1
            group[{"success": "good", "fail": "bad"}.get(verdict, "flat")] += 1
            if pnl is not None:
                group["sum"] += float(pnl)
                group["observed"] += 1
    return [dict(date=day, phase=None, n=g["n"], good=g["good"], bad=g["bad"], flat=g["flat"],
                 mean_excess=round(g["sum"] / g["observed"], 3) if g["observed"] else None)
            for day, g in sorted(by_date.items())]


COLLECTORS = {
    "daily_picks": _groups_from_daily_picks,
    "intraday_watch": _groups_from_watch_ledger,
}


# ---------------------------------------------------------------- 评估入口


def list_strategy_keys(*, include_candidates: bool = False) -> list[dict]:
    """登记册全量策略键（含不可评估者，便于前端/复盘展示"有哪些策略"）。

    S2-11：带 `verify_key` 的条目会附 `verification` 三态——`status` 从此有产物背书。
    核验产物是**离线**跑出来的（`strategy_verify` 要扫全历史），读取失败或缺失
    一律显式标注，**不静默省略字段**（三态纪律）。
    """
    out = []
    for s in SPECS:
        item = {
            "key": s.key, "name": s.name, "status": s.status,
            "basis": s.basis, "source": s.source,
            "evaluable": s.evaluable, "note": s.note,
            "discovery": {
                "category": s.discovery_category,
                "category_label": s.discovery_category_label,
                "session": s.discovery_session,
                "session_label": s.discovery_session_label,
                "summary": s.discovery_summary,
                "supports_saved_runs": s.supports_saved_runs,
            },
        }
        if s.verify_key:
            item["verification"] = _verification_of(s.verify_key)
        out.append(item)
    if include_candidates:
        from app.picks._strategy_reference_catalog import reference_catalog_entries

        out = [{**item, "catalog_kind": "registered"} for item in out]
        out.extend(reference_catalog_entries())
    return out


def _verification_of(key: str) -> dict:
    """读核验产物。任何异常都退化为 `available=False + reason`，
    绝不让一个坏 JSON 把整个登记册端点拖成 500。"""
    try:
        from app.research.verify_registry import verification_of

        return verification_of(key)
    except Exception as exc:  # noqa: BLE001
        log.warning("读取核验产物失败 key=%s: %s", key, exc)
        return {"available": False, "verdict": None, "headline": None,
                "reason": f"读取失败：{exc}"}


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


def saved_method_runs(session_factory) -> dict:
    """Project existing run owners; no execution, market read or outcome fabrication."""
    import json
    import math
    from sqlalchemy import case, select
    from app.models.daily_pick import DailyPickSet
    from app.models.opportunity_learning import OpportunityDecisionRun, OpportunityDecisionSnapshot
    from app.core.bjtime import beijing_now, BJ_TZ
    from app.picks.selection_history import diagnostics

    def checked_obj(value, kind):
        try:
            data = json.loads(value) if isinstance(value, str) else value
            return data if isinstance(data, kind) else None
        except (ValueError, TypeError):
            return None

    def obj(value, kind):
        data = checked_obj(value, kind)
        return data if data is not None else kind()

    def number(value):
        return value if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) else None

    def daily_reason(row):
        reason = row.get('reason')
        if isinstance(reason, str) and reason.strip():
            return reason
        selection_reason = row.get('selection_reason')
        if selection_reason == 'below_min_score':
            shortfall = number(row.get('score_shortfall'))
            return ('低于原入选分线；原记录差额 ' + format(shortfall, 'g') + ' 分'
                    if shortfall is not None and shortfall >= 0 else '低于原入选分线；原差额未记录')
        if selection_reason == 'capacity_or_replacement':
            return '原记录为容量或换股约束；具体原因未区分'
        if isinstance(selection_reason, str) and selection_reason:
            return f'原入选原因未识别（{selection_reason}）'
        return ''

    now = beijing_now()
    today = now.date().isoformat()
    result = {key: {'state': 'missing', 'reason': '没有已保存运行；未按读取时间构造'}
              for key in ('daily_picks', 'intraday_watch')}
    with session_factory() as db:
        daily = db.scalar(select(DailyPickSet).where(DailyPickSet.date <= today)
                          .order_by(DailyPickSet.date.desc()).limit(1))
        if daily:
            stored_meta = checked_obj(daily.meta, dict)
            meta = stored_meta if stored_meta is not None else {}
            stored_lists = [checked_obj(daily.items, list), checked_obj(daily.rejected, list)]
            valid_lists = [rows is not None and all(isinstance(row, dict) for row in rows)
                           for rows in stored_lists]
            selected, rejected = [[row for row in (rows or []) if isinstance(row, dict)]
                                  for rows in stored_lists]
            problems = ([] if stored_meta is not None else ['运行元数据 JSON 损坏或结构不符'])
            problems.extend(f'{label} JSON 损坏或结构不符' for label, valid
                            in zip(['入选名单', '拒绝名单'], valid_lists) if not valid)
            funnel = obj(meta.get('selection_funnel'), dict)
            state = 'saved' if not problems else 'partial' if any(valid_lists) or selected or rejected else 'error'
            result['daily_picks'] = {'state': state, 'trade_date': daily.date,
                'reason': '；'.join(problems),
                'source_version': meta.get('selection_version'), 'run_at': meta.get('generated_at'),
                'strategy_version': obj(meta.get('selection_contract'), dict).get('strategy_version'),
                'elapsed_ms': None, 'possibly_truncated': len(selected) > 200 or len(rejected) > 200,
                'counts': {'candidates': number(meta.get('candidate_count')), 'scored': number(funnel.get('scored')),
                           'selected': len(selected) if valid_lists[0] else None,
                           'rejected_saved': len(rejected) if valid_lists[1] else None},
                'criteria': {'min_pick_score': number(meta.get('min_pick_score')),
                             'max_picks': number(meta.get('max_picks'))},
                'items': [{'symbol': str(row.get('symbol') or ''), 'name': str(row.get('name') or row.get('symbol') or ''),
                           'state': state, 'score': number(row.get('score')),
                           'reason': daily_reason(row),
                           'diagnostics': diagnostics(row, meta),
                           'basis': row.get('bases') if isinstance(row.get('bases'), dict) else {}}
                          for state, rows in [('selected', selected), ('rejected', rejected)]
                          for row in rows[:200] if isinstance(row, dict)],
                'semantics': 'saved_selection_not_execution_or_fill'}
        run = db.scalar(select(OpportunityDecisionRun).where(
            OpportunityDecisionRun.trade_date <= today, OpportunityDecisionRun.as_of <= now.replace(tzinfo=None),
            OpportunityDecisionRun.scenario == 'intraday_opportunity')
            .order_by(OpportunityDecisionRun.trade_date.desc(), OpportunityDecisionRun.as_of.desc(),
                      OpportunityDecisionRun.run_id.desc()).limit(1))
        if run:
            rows = db.scalars(select(OpportunityDecisionSnapshot).where(
                OpportunityDecisionSnapshot.run_id == run.run_id,
                OpportunityDecisionSnapshot.stage.in_(['rank', 'hard_gate']))
                .order_by(case((OpportunityDecisionSnapshot.stage == 'rank', 0), else_=1),
                          OpportunityDecisionSnapshot.id).limit(200)).all()
            items, selected_symbols, invalid_evidence = [], set(), False
            rank_rows_read, rank_evidence_known = 0, True
            for row in rows:
                stored_evidence = checked_obj(row.evidence, dict)
                invalid_evidence = invalid_evidence or stored_evidence is None
                evidence = stored_evidence if stored_evidence is not None else {}
                capacity = evidence.get('effective_limit')
                if row.stage == 'rank':
                    rank_rows_read += 1
                    rank_evidence_known = rank_evidence_known and stored_evidence is not None
                    if row.decision == 'ranked':
                        rank_evidence_known = rank_evidence_known and (
                            not isinstance(capacity, bool) and isinstance(capacity, int) and capacity >= 1
                            and row.rank is not None and row.rank >= 1
                            and isinstance(evidence.get('within_display_capacity'), bool)
                            and evidence['within_display_capacity'] == (row.rank <= capacity))
                    elif row.decision not in {'unknown', 'rejected', 'blocked'}:
                        rank_evidence_known = False
                selected = (row.stage == 'rank' and row.decision == 'ranked'
                            and evidence.get('within_display_capacity') is True
                            and not isinstance(capacity, bool) and isinstance(capacity, int)
                            and row.rank is not None and 1 <= row.rank <= capacity)
                if selected:
                    selected_symbols.add(row.symbol)
                if selected:
                    state = 'selected'
                elif row.decision in {'rejected', 'blocked'}:
                    state = 'rejected'
                elif (row.stage == 'hard_gate' and row.decision == 'passed'
                      or row.stage == 'rank' and row.decision == 'ranked' and stored_evidence is not None):
                    state = 'candidate'
                else:
                    state = 'unknown'
                items.append({'symbol': row.symbol, 'name': row.name or row.symbol,
                    'state': state, 'stage': row.stage, 'decision': row.decision,
                    'score': number(evidence.get('score')),
                    'diagnostics': diagnostics(evidence, {}),
                    'reason': str(evidence.get('reason') or evidence.get('filter_reason')
                                  or evidence.get('linkage_basis') or row.decision),
                    'rank': row.rank, 'source_theme': row.source_theme, 'snapshot_id': row.snapshot_id})
            at = run.as_of.replace(tzinfo=BJ_TZ) if run.as_of.tzinfo is None else run.as_of.astimezone(BJ_TZ)
            result['intraday_watch'] = {'state': 'partial' if invalid_evidence else 'saved', 'trade_date': run.trade_date,
                'reason': '部分快照依据 JSON 损坏或结构不符' if invalid_evidence else '',
                'source_version': run.run_id, 'run_at': at.isoformat(), 'strategy_version': run.strategy_version,
                'elapsed_ms': None, 'data_state': run.data_state,
                'counts': {'candidate_rows': run.candidate_rows, 'rank_rows': run.rank_rows,
                           'hard_gate_rows': run.hard_gate_rows,
                           'selected': len(selected_symbols) if rank_rows_read == run.rank_rows and rank_evidence_known else None},
                'items': items, 'possibly_truncated': len(rows) >= 200,
                'semantics': 'saved_run_rows_not_unique_stock_outcomes_or_fills'}
    return result
