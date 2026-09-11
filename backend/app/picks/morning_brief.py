"""盘前简报（选股 2.0 §4，docs/stock-picking-system-2026-09-02.md，批次 B）。

职责：盘前（默认 08:40，交易日调度，可随时手动触发）采集三类证据 →
`intraday_rules.rank_directions` 排出 top 1-3 方向 → 每方向给出标的池 /
触发条件 / 证伪条件 / 进场模式 → 落盘。盘中 watcher（watcher.py）以当日
简报为跟踪清单；提醒追加进简报 payload 的 alerts 数组（当日去重）。

证据三条腿（与 rank_directions 的输入一一对应）：
1. 事件强度：EventStore 活跃事件的**题材**方向（direction=±1），
   strength × event_weight(tier, certainty) 净额（利好 − 利空）。
   active_only 已按 half_life 过滤过期事件。
2. 题材动能/梯队：上一交易日涨停池 parse_theme_tags 归因 → 家数/最高板/
   平均涨幅 → 映射 0-100；阶段与完整度复用 theme_ladder_health（同一口径）。
3. 情绪环境：compute_market_sentiment 的 phase 与 promo_1to2 历史分位
   （P0-3b 校准库），进 rank 的情绪适配项与 env 透出。

持久化决策（2026-09-02 评估后**改为文件存储**，不用 prediction_reports 表）：
predict 的 save_report 按 target_date 单键 upsert——同表共存时周末预判
（target=下一交易日）会被盘中简报行撞车互相覆盖，且 predict 的 get_report
按日期读取会把简报误当成预判返回。改用 data/picks/briefs/YYYYMMDD.json
（与 predict 的 REPORT_DIR 落盘同模式），零迁移、零串表；批次 C 复盘对照
若需 SQL 检索再加镜像表。

诚实边界（纪律沿用）：
- 盘前（09:25 集合竞价出价前）当日涨停池是空的，证据池取**上一交易日**；
  `_evidence_pool_date` 显式换算，绝不拿"今天的空池"冒充"没有题材"。
- 事件/涨停池/情绪任何一路失败 → 记入 missing 显式呈现，对应输入按 0/None，
  绝不让方向排序悄悄变成"只剩一条腿还在响"。
- volume_ratio 盘前无来源：触发条件里写明"盘中按 unknown 处理"，
  确认强度会被压档——这是设计行为，不是缺陷。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.market import trade_calendar as tc
from app.picks.echelon import theme_ladder_health
from app.picks.engine import event_weight
from app.picks.intraday_rules import (
    CONFIRM_HEIGHT_BOARDS,
    CONFIRM_LEADER_PCT,
    CONFIRM_MIN_LIMIT_UP,
    CONFIRM_PROMO_PCTILE,
    CONFIRM_THEME_PCT_EARLY,
    CONFIRM_THEME_PCT_LATE,
    CONFIRM_VOLUME_RATIO,
    FALSIFY_DRAWDOWN_PCT,
    FALSIFY_NEGATIVE_BEATS,
    entry_mode,
    is_performance_tag,
    rank_directions,
)
from app.core.bjtime import beijing_today  # S2-8 时区收敛（原在 metric_history）
from app.services.theme_service import normalize_theme, parse_theme_tags
from app.core.bjtime import beijing_now

log = logging.getLogger(__name__)

BRIEF_CONTEXT = "morning_brief"
BRIEF_ENGINE_VERSION = "brief_v1"
DIRECTIONS_CAP = 3          # 盘前方向数上限（§4：1–3 个）
POOL_CAP = 10               # 每方向标的池上限
EVENT_LIMIT = 50            # 活跃事件扫描上限
#: 防守方向识别关键词（红利银行等）。命中即视为防守：退潮/冰点期有情绪适配加成
DEFENSIVE_HINTS = ("银行", "红利", "煤炭", "电力", "公路", "保险", "公用", "石油", "高股息")

REPO_ROOT = Path(__file__).resolve().parents[3]
BRIEF_DIR = REPO_ROOT / "data" / "picks" / "briefs"


# ---------------------------------------------------------------- 纯函数：阈值文本


def trigger_conditions() -> list[str]:
    """确认走强的五项量化标准（§5.1）。阈值全部引用 intraday_rules 常量——
    批次 D 调参只改常量表，这里的文案自动跟随，不会出现两套数字。"""
    return [
        f"板块涨幅 ≥{CONFIRM_THEME_PCT_EARLY}%（10:00 前）/ ≥{CONFIRM_THEME_PCT_LATE}%（10:00 后）",
        f"板块涨停 ≥{CONFIRM_MIN_LIMIT_UP} 家 或 高度股 ≥{CONFIRM_HEIGHT_BOARDS} 板",
        f"龙头（梯队最高者）涨幅 ≥{CONFIRM_LEADER_PCT}% 或涨停",
        f"量比 ≥{CONFIRM_VOLUME_RATIO}（当前数据源缺量比 → 盘中按 unknown 处理，会压低确认强度）",
        f"环境：promo_1to2 分位 ≥{CONFIRM_PROMO_PCTILE} 且市场相位非退潮/冰点",
    ]


def falsify_conditions() -> list[str]:
    """证伪放弃的四项量化标准（§5.2）。"""
    return [
        f"较盘中峰值回撤 ≥{FALSIFY_DRAWDOWN_PCT} 个百分点",
        f"板块涨幅转负连续 ≥{FALSIFY_NEGATIVE_BEATS} 拍（60s/拍 ≈ 15 分钟）",
        "龙头炸板且板块内出现跌停（跌停数数据源暂缺 → 第一版该触发器不激活）",
        f"promo_1to2 分位 <{CONFIRM_PROMO_PCTILE} 或相位进入退潮/冰点 → 全部方向降级观察",
    ]


# ---------------------------------------------------------------- 纯函数：涨停池聚合


def _theme_stats_from_pool(pool: list[Any]) -> dict[str, dict]:
    """涨停池 → 题材聚合（纯函数）。leader = 该题材连板最高成员（并列取先出现）。

    阶段/完整度复用 theme_ladder_health（与题材看板同一口径，避免两套术语）。
    """
    themes: dict[str, dict] = {}
    for r in pool:
        boards = int(r.consecutive_boards or 1)
        for raw_tag in parse_theme_tags(getattr(r, "reason", None)):
            tag = normalize_theme(raw_tag)
            st = themes.setdefault(tag, {
                "limit_up": 0, "max_boards": 0, "changes": [], "levels": {}, "records": [],
            })
            st["limit_up"] += 1
            st["max_boards"] = max(st["max_boards"], boards)
            st["levels"][boards] = st["levels"].get(boards, 0) + 1
            if getattr(r, "change_pct", None) is not None:
                st["changes"].append(float(r.change_pct))
            st["records"].append({
                "symbol": r.symbol,
                "name": getattr(r, "name", None) or "",
                "boards": boards,
                "change_pct": getattr(r, "change_pct", None),
            })
    for st in themes.values():
        health = theme_ladder_health(
            limit_up_count=st["limit_up"], max_boards=st["max_boards"],
            reopen_rate=0.0, levels=st["levels"],
        )
        st["stage"] = health["stage"]
        st["stage_basis"] = health["stage_basis"]
        st["completeness"] = health["completeness"]
        st["avg_change"] = (
            round(sum(st["changes"]) / len(st["changes"]), 2) if st["changes"] else None
        )
        st["records"].sort(key=lambda x: (-x["boards"], x["symbol"]))
        st["leader"] = st["records"][0] if st["records"] else None
        st.pop("changes")
        st.pop("levels")
    return themes


def momentum_score(stat: dict | None) -> float:
    """题材动能 0-100：家数为主，高度与平均涨幅为辅（公式写死在此，回测可复算）。"""
    if not stat:
        return 0.0
    avg = stat.get("avg_change") or 0.0
    return round(min(100.0, stat.get("limit_up", 0) * 10 + stat.get("max_boards", 0) * 8 + max(0.0, avg) * 2), 1)


def echelon_score(stat: dict | None) -> float:
    """梯队 0-100：完整度（0-1 → 0-60）+ 高度（每板 10 分，封顶 40）。"""
    if not stat:
        return 0.0
    comp = stat.get("completeness")
    comp = 0.5 if comp is None else float(comp)
    return round(min(100.0, comp * 60 + min(4, stat.get("max_boards", 0)) * 10), 1)


def _evidence_pool_date(days: list[date] | None, now: datetime) -> tuple[date | None, str | None]:
    """盘前简报证据池日期 + 口径标注。返回 (pool_date, evidence_pool_basis)。

    N3（2026-09-04 复盘定案，2026-09-07 修复）：只要 `anchor == now.date()`
    就回退上一交易日——**任何时点生成的盘前简报都建立在上一交易日完整收盘
    数据上**。原实现只挡 09:25 前，09:25 后生成（调度器补跑/幂等重生成）会
    拿当日盘中池冒充"上一交易日"，文案自指且题材动能口径错。

    09:25 前当日池必然是空的：如果直接取 `last_trade_date`（今天在日历里就
    返回今天），题材动能全体归零，方向排序退化成"只剩事件强度一条腿"，
    且界面上完全看不出是口径错了。
    """
    if not days:
        return None, None
    anchor = tc.last_trade_date(days, asof=now.date())
    if anchor is None:
        return None, None
    if anchor == now.date():
        return tc.prev_trade_date(days, anchor), "prev_trade_date"
    return anchor, "last_trade_date"


def _direction_pool(
    stat: dict | None, extra_symbols: list[str], names: dict[str, str]
) -> list[dict]:
    """方向标的池：涨停池成员（按连板降序）+ 事件个股，cap POOL_CAP。

    role 是**提示性**口径（龙头/中军/跟风），精确判定盘中由
    classify_echelon_role 在有封单数据时做——这里不假装精确。
    """
    pool: list[dict] = []
    if stat:
        max_boards = stat.get("max_boards", 0)
        for i, rec in enumerate(stat["records"]):
            if i == 0:
                role = "龙头" if rec["boards"] >= 2 else "首板"
            elif rec["boards"] >= 2 and rec["boards"] >= max_boards - 1:
                role = "中军"
            else:
                role = "跟风"
            pool.append({
                "symbol": rec["symbol"], "name": rec.get("name") or "",
                "boards": rec["boards"], "role": role,
            })
    have = {p["symbol"] for p in pool}
    for s in extra_symbols:
        if len(pool) >= POOL_CAP:
            break
        if s in have:
            continue
        pool.append({"symbol": s, "name": names.get(s) or "", "boards": 0, "role": "事件标的"})
    return pool[:POOL_CAP]


# ---------------------------------------------------------------- 纯函数：证据 → 简报


def assemble_brief(evidence: dict) -> dict:
    """证据 → 简报 payload（纯函数，可被回测框架直接回放）。

    候选方向 = 涨停池题材 ∪ 事件题材方向；排序取 rank_directions top3。
    业绩/财报结果型题材（is_performance_tag）不作为跟踪方向——官方概念
    目录无对应板块指数，confirm 的板块涨幅永远拿不到，占坑只会稀释有效
    方向（回测 120 日 unmatched 28.6% 的根因）；被排除的名单落
    performance_skipped 如实呈现，不是静默丢弃。
    """
    phase = evidence.get("phase")
    themes: dict[str, dict] = evidence.get("themes") or {}
    ev_strength: dict[str, float] = evidence.get("event_strength") or {}
    ev_counts: dict[str, dict] = evidence.get("event_counts") or {}
    ev_symbols: dict[str, list[str]] = evidence.get("event_symbols") or {}
    names: dict[str, str] = evidence.get("event_symbol_names") or {}

    evidences = []
    perf_skipped: list[str] = []
    for tag in sorted(set(themes) | set(ev_strength)):
        if is_performance_tag(tag):
            perf_skipped.append(tag)
            continue
        stat = themes.get(tag)
        defensive = any(h in tag for h in DEFENSIVE_HINTS)
        ec = ev_counts.get(tag) or {}
        logic_parts: list[str] = []
        if tag in ev_strength:
            logic_parts.append(
                f"近24h事件净强度 {ev_strength[tag]:+.2f}（利好 {ec.get('bull', 0)} / 利空 {ec.get('bear', 0)} 条）"
            )
        if stat:
            avg = stat.get("avg_change")
            logic_parts.append(
                f"上一交易日（{evidence.get('pool_date')}）涨停 {stat['limit_up']} 家"
                f" / 最高 {stat['max_boards']} 板"
                + (f" / 平均 {avg:+.1f}%" if avg is not None else "")
                + (f" / 阶段「{stat['stage']}」" if stat.get("stage") else "")
            )
        if defensive:
            logic_parts.append("防守方向")
        if not logic_parts:
            logic_parts.append("仅有题材动能证据")
        evidences.append({
            "direction": tag,
            "event_strength": round(ev_strength.get(tag, 0.0), 2),
            "theme_momentum": momentum_score(stat),
            "echelon": echelon_score(stat),
            "defensive": defensive,
            "logic": "；".join(logic_parts),
        })

    ranked = rank_directions(evidences, phase)[:DIRECTIONS_CAP]
    directions = []
    for d in ranked:
        stat = themes.get(d["direction"])
        mode, mode_basis = entry_mode((stat or {}).get("stage"), phase)
        directions.append({
            "direction": d["direction"],
            "score": d["score"],
            "basis": d["basis"],
            "logic": d["logic"],
            "defensive": d["defensive"],
            "stage": (stat or {}).get("stage"),
            "stage_basis": (stat or {}).get("stage_basis") or [],
            "entry_mode": mode,
            "entry_basis": mode_basis,
            "pool": _direction_pool(stat, ev_symbols.get(d["direction"]) or [], names),
            "trigger_conditions": trigger_conditions(),
            "falsify_conditions": falsify_conditions(),
        })

    return {
        "brief_date": evidence.get("brief_date"),
        "generated_at": evidence.get("generated_at"),
        "trigger": evidence.get("trigger", "manual"),
        "context": BRIEF_CONTEXT,
        "engine_version": BRIEF_ENGINE_VERSION,
        "env": {
            "phase": phase,
            "promo_percentile": evidence.get("promo_percentile"),
            "bands_source": evidence.get("bands_source"),
            "pool_date": evidence.get("pool_date"),
            "is_trading_day": evidence.get("is_trading_day"),
        },
        "missing": evidence.get("missing") or [],
        "macro_note": evidence.get("macro_note"),
        "macro_events": evidence.get("macro_events"),
        "overnight_bias": evidence.get("overnight_bias"),
        "climate": evidence.get("climate"),
        "performance_skipped": perf_skipped,
        "directions": directions,
        "daily_plan": _daily_plan(evidence.get("pool_date")),
        "alerts": [],  # 盘中 watcher 追加（append_alert，当日去重）
    }


def _daily_plan(prev_pool_date) -> dict | None:
    """P1-6（2026-09-08 用户指令）：复盘 → 次日计划显式链路。

    三段上下文拼装（规则层，无 LLM）：昨日复盘结论 / 未完成 action_items /
    昨日进化议程执行结果。任一来源缺失显式标注（三态），不臆造。
    """
    import contextlib

    from datetime import date as _date

    if not prev_pool_date:
        return None
    try:
        prev = prev_pool_date if isinstance(prev_pool_date, _date) else _date.fromisoformat(str(prev_pool_date))
    except ValueError:
        return None
    plan: dict = {"based_on": str(prev), "review": None, "open_items": [], "agenda": None}

    # ① 昨日复盘结论（买点质量/失误数）
    with contextlib.suppress(Exception):
        from app.picks.review_store import get_report

        rep = get_report(prev)
        if rep is not None and rep.report:
            summary = (rep.report.get("summary") or {}) if isinstance(rep.report, dict) else {}
            plan["review"] = {
                "trade_date": str(prev),
                "picks_count": summary.get("picks_count"),
                "findings": (rep.report.get("findings") or [])[:3] if isinstance(rep.report, dict) else [],
            }

    # ② 未完成 action_items（pending/deferred，最多 3 条）
    with contextlib.suppress(Exception):
        from app.models.review import ReviewReport
        from sqlalchemy import select

        from app.core.db import get_session_factory

        with get_session_factory()() as db:
            rows = db.execute(
                select(ReviewReport).order_by(ReviewReport.id.desc()).limit(5)
            ).scalars().all()
            for r in rows:
                report = r.report or {}
                for ai in (report.get("action_items") or []):
                    if ai.get("status") in ("pending", "deferred"):
                        plan["open_items"].append({
                            "title": ai.get("title", "")[:60], "category": ai.get("category"),
                        })
                if len(plan["open_items"]) >= 3:
                    break

    # ③ 昨日进化议程执行结果
    with contextlib.suppress(Exception):
        from app.models.agent import AgentAgenda
        from sqlalchemy import select as _sel

        from app.core.db import get_session_factory

        with get_session_factory()() as db:
            row = db.execute(
                _sel(AgentAgenda).where(AgentAgenda.date == str(prev))
            ).scalars().first()
        if row is not None:
            items = row.items if isinstance(row.items, list) else []
            plan["agenda"] = {
                "date": row.date,
                "status": row.status,
                "items": [{"finding": i.get("finding", "")[:50], "class": i.get("class"),
                           "status": i.get("status")} for i in items[:5]],
            }

    plan["note"] = "今日计划=昨日复盘结论+未完成改进项+昨日议程执行结果的规则拼装；不构成买卖建议"
    return plan


# ---------------------------------------------------------------- IO：证据采集


async def _resolve_names(hub, symbols: list[str]) -> dict[str, str]:
    """事件个股名称解析（腾讯批量快照，best-effort；失败返回空表不阻断）。

    2026-09-07 R3 收口：分批实现在 quote_enrich.fetch_quotes_batched。
    """
    from app.services.quote_enrich import fetch_quotes_batched

    if not symbols:
        return {}
    found = await fetch_quotes_batched(hub, symbols)
    return {q.symbol: (q.name or "") for q in found.values() if q.symbol}


async def collect_climate_safe(today: date) -> dict | None:
    """气候一阶相位取数（P1-32，2026-09-11）。**永不抛异常、永不写 missing**。

    三态：`None` = 源不可得 → 前端**整块不渲染**（不谎称「中性」）。
    **刻意不并入 brief.missing**：气候是月更慢变量，天天在简报顶部挂一条 ⚠
    是纯噪音；取不到就不显示即可。抽成独立函数是为了让这条口径**可被直接测**
    （内联在 `collect_evidence` 里就只能靠读源码断言，属易碎测试）。
    """
    try:
        from app.market.climate import collect as _collect_climate

        return await _collect_climate(today)
    except Exception as exc:  # noqa: BLE001
        log.warning("brief evidence: climate failed: %s", exc)
        return None


async def collect_evidence(app_state) -> dict:
    """三类证据采集（IO 层）。任何一路失败记入 missing，不让排序静默降级。

    app_state 兼容 FastAPI 实例或其 .state 对象（调用方有 request.app 与
    lifespan 内 app 两种姿势，归一化收在入口，不向下游扩散）。
    """
    state = app_state.state if hasattr(app_state, "state") else app_state
    hub = state.hub
    now = beijing_now()
    missing: list[str] = []

    days: list[date] | None = None
    try:
        days = await tc.trading_days(hub.provider)
    except Exception as exc:
        log.warning("brief evidence: calendar failed: %s", exc)
        missing.append(f"交易日历不可用（{exc}）")
    pool_date, pool_basis = _evidence_pool_date(days, now)
    if days and beijing_today() not in days:
        missing.append(f"{beijing_today()} 非交易日（简报仅存档，盘中 watcher 不会跑）")

    themes: dict[str, dict] = {}
    if pool_date is not None:
        try:
            pool = await hub.provider.get_limit_up_pool(pool_date)
            themes = _theme_stats_from_pool(pool or [])
        except Exception as exc:
            log.warning("brief evidence: limit-up pool failed: %s", exc)
            missing.append(f"涨停池不可用（{exc}）")
    else:
        missing.append("无法定位涨停池日期")

    event_strength: dict[str, float] = {}
    event_counts: dict[str, dict] = {}
    event_symbols: dict[str, list[str]] = {}
    store = getattr(state, "event_store", None)
    if store is not None:
        try:
            for row in store.list_events(active_only=True, limit=EVENT_LIMIT):
                w = event_weight(row.source_tier, row.certainty)
                row_themes: list[str] = []
                row_syms: list[str] = []
                for d in row.directions:
                    if d.target_type == "theme" and d.target:
                        tag = normalize_theme(d.target)
                        if tag not in row_themes:
                            row_themes.append(tag)
                        if d.direction:
                            sign = 1 if d.direction == 1 else -1
                            event_strength[tag] = event_strength.get(tag, 0.0) + sign * d.strength * w
                            c = event_counts.setdefault(tag, {"bull": 0, "bear": 0})
                            c["bull" if d.direction == 1 else "bear"] += 1
                    elif d.target_type == "symbol" and d.target:
                        row_syms.append(d.target)
                for tag in row_themes:
                    bucket = event_symbols.setdefault(tag, [])
                    for s in row_syms:
                        if s not in bucket:
                            bucket.append(s)
        except Exception as exc:
            log.warning("brief evidence: events failed: %s", exc)
            missing.append(f"事件读取失败（{exc}）")
    else:
        missing.append("EventStore 未初始化")

    names = {}
    try:
        names = await _resolve_names(hub, sorted({s for v in event_symbols.values() for s in v}))
    except Exception as exc:
        log.warning("brief evidence: name resolve failed: %s", exc)

    phase = promo = bands_source = None
    try:
        from app.services.market_context import compute_market_sentiment

        sent = await compute_market_sentiment(hub, state.snapshot_service) or {}
        phase = sent.get("phase")
        bands_source = sent.get("bands_source")
        promo = (
            ((sent.get("calibration") or {}).get("percentile") or {}).get("promo_1to2") or {}
        ).get("percentile")
    except Exception as exc:
        log.warning("brief evidence: sentiment failed: %s", exc)
        missing.append(f"情绪环境不可用（{exc}）")

    # 宏观日历：①先验规则（非农，零外呼纯规则）②财经日历（百度，需外呼）
    macro_note: str | None = None
    try:
        from app.events.chains import macro_calendar_note

        macro_note = macro_calendar_note(beijing_today())
    except Exception as exc:
        log.warning("brief evidence: macro calendar failed: %s", exc)

    # 财经日历高信号事件（P1-8 残余，2026-09-10）：CPI/PPI/GDP/PMI/社融/M1M2/非农…
    # 三态：None=源不可得（记 missing），[]=当日确无高信号事件（真信息）
    macro_events: list[dict] | None = None
    try:
        from app.events.chains import select_macro_events
        from app.services.akshare_ext import get_akshare_ext

        rows = await get_akshare_ext().macro_calendar(beijing_today())
        macro_events = select_macro_events(rows)
    except Exception as exc:
        log.warning("brief evidence: macro calendar events failed: %s", exc)
        missing.append(f"宏观财经日历不可用（{exc}）")

    # 隔夜海外一阶输入 → 大盘方向偏向（P1-34，2026-09-11）
    # 三态：None=整块不可用（异常）；dict 内 stance=None=输入不足未判定（见 unjudged_reason）
    # 注意：**逐输入的 missing 不并入 brief.missing**——面板内已逐条如实展示，
    # 并入会让简报顶部多出最多 4 条 ⚠（噪音），只有整块失败才上 brief.missing。
    overnight_bias: dict | None = None
    try:
        from app.market.overnight_bias import collect as _collect_overnight

        overnight_bias = await _collect_overnight(beijing_today())
    except Exception as exc:
        log.warning("brief evidence: overnight bias failed: %s", exc)
        missing.append(f"隔夜海外输入不可用（{exc}）")

    # 气候一阶相位（ENSO/ONI，P1-32，2026-09-11）
    climate = await collect_climate_safe(beijing_today())

    return {
        "brief_date": beijing_today().strftime("%Y%m%d"),
        "generated_at": beijing_now().isoformat(),
        "is_trading_day": (beijing_today() in days) if days else None,
        "phase": phase,
        "promo_percentile": promo,
        "bands_source": bands_source,
        "pool_date": pool_date.isoformat() if pool_date else None,
        "evidence_pool_basis": pool_basis,
        "themes": themes,
        "event_strength": event_strength,
        "event_counts": event_counts,
        "event_symbols": event_symbols,
        "event_symbol_names": names,
        "missing": missing,
        "macro_note": macro_note,
        "macro_events": macro_events,
        "overnight_bias": overnight_bias,
        "climate": climate,
    }


# ---------------------------------------------------------------- IO：持久化（文件）


def _brief_path(target_date: str) -> Path:
    return BRIEF_DIR / f"{target_date}.json"


def save_brief(payload: dict) -> Path:
    """原子落盘（tmp + os.replace）：与盘中 append_alert 并发时不读半截文件。"""
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    target = payload.get("brief_date") or beijing_today().strftime("%Y%m%d")
    path = _brief_path(target)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_brief(target_date: str) -> dict | None:
    """读当日简报。文件损坏返回 None（显式走"无简报"路径，不抛异常炸调用方）。"""
    path = _brief_path(target_date)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("brief %s corrupted: %s", target_date, exc)
        return None


def brief_for_today() -> tuple[str, dict | None]:
    target = beijing_today().strftime("%Y%m%d")
    return target, load_brief(target)


def append_alert(target_date: str, alert: dict) -> bool:
    """盘中提醒追加进当日简报 payload。返回 False = 当日已有同 key（去重丢弃）。

    去重键由调用方生成：confirm = `{方向}:{个股}:confirm`，
    falsify = `{方向}:falsify`。读-改-写：单进程 asyncio 内无真正并发，
    原子落盘（save_brief）保证不留半截文件。
    """
    payload = load_brief(target_date)
    if payload is None:
        log.warning("append_alert: brief %s 不存在，提醒不落盘", target_date)
        return False
    alerts = payload.setdefault("alerts", [])
    # 单点兜底：调用方漏生成 key 时补一个当日唯一键（len 保证不撞），
    # 避免 key=None 落盘（前端 key={null} 触发 React key 警告）+ None 互相撞去重。
    key = alert.get("key")
    if not key:
        key = f"{alert.get('kind', 'alert')}:{alert.get('symbol') or alert.get('direction') or 'x'}:{len(alerts)}"
        alert["key"] = key
    if any(a.get("key") == key for a in alerts):
        return False
    alerts.append(alert)
    save_brief(payload)
    return True


# ---------------------------------------------------------------- 编排与调度


async def build_and_save(app_state, *, trigger: str = "manual") -> dict:
    evidence = await collect_evidence(app_state)
    evidence["trigger"] = trigger
    payload = assemble_brief(evidence)
    save_brief(payload)
    log.info(
        "morning brief saved: %s（%d 个方向；missing=%s）",
        payload["brief_date"], len(payload["directions"]), payload["missing"] or "无",
    )
    return payload


async def _is_trading_day(hub, d: date) -> bool:
    try:
        days = await tc.trading_days(hub.provider)
    except Exception as exc:
        log.warning("premarket brief: calendar failed: %s", exc)
        return False
    return d in (days or [])


async def _premarket_tick(
    app,
    *,
    now: datetime,
    last_run: str | None,
    run_hour: int,
    run_minute: int,
) -> str:
    """调度单步判定（抽出以便测试）：返回处置后的 last_run key。

    幂等链：内存 last_run（防同进程重复）→ 磁盘当日简报（防重启后覆盖，
    2026-09-02 实测事故）→ 交易日判定。三层全过才生成。
    """
    key = now.strftime("%Y%m%d")
    due = (now.hour, now.minute) >= (run_hour, run_minute) and now.hour < 12
    if not due or last_run == key:
        return last_run or ""
    existing = load_brief(key)
    if existing is not None:
        log.info(
            "premarket brief skipped: %s 当日简报已存在"
            "（generated_at=%s, trigger=%s），持久化幂等不覆盖",
            key, existing.get("generated_at"), existing.get("trigger"),
        )
        return key
    if await _is_trading_day(app.state.hub, now.date()):
        await build_and_save(app, trigger="schedule")
    else:
        log.info("premarket brief skipped: %s 非交易日", key)
    return key


async def premarket_scheduler(
    app,
    *,
    stop: asyncio.Event,
    run_hour: int,
    run_minute: int,
    check_interval_seconds: float = 60.0,
) -> None:
    """盘前简报调度（lifespan 任务）：交易日 run_hour:run_minute 后生成，当日幂等。

    幂等是**持久化**的：due 分支先查磁盘当日简报（load_brief），已存在则跳过——
    last_run 是内存态，12:00 前重启后端会清零，若无磁盘幂等会无条件重新生成
    当日简报，把盘前证据产出的方向/alerts 整体覆盖（2026-09-02 实测事故：
    09:15 的存储芯片简报被 09:49 重启后盘中池重新生成覆盖）。
    需要强制重生成走手动端点（显式意图，不受本幂等约束）。
    先置位 last_run 再跑：失败不整分钟重试风暴；
    12:00 后不再触发（过了盘前窗口的"补跑"只会产出过时证据）。
    """
    last_run: str | None = None
    while not stop.is_set():
        try:
            last_run = await _premarket_tick(
                app, now=beijing_now(), last_run=last_run,
                run_hour=run_hour, run_minute=run_minute,
            )
        except Exception:
            log.exception("premarket brief scheduler failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=check_interval_seconds)
