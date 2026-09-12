"""盘后方向对照与提醒收益回算（选股 2.0 §7，docs/summary/stock-strategy.md，批次 C）。

三件事（§7.1–7.3）：
1. **方向级对照**（默认 15:35 调度，可手动触发）：盘前 top 方向 vs 当日实际盘面
   → 四分类：发酵（确认规则当日曾全满足）/ 半发酵（满足 ≥3 项）/
   证伪（触发任一证伪）/ 无波动。结果回填进当日简报文件（directions[].review）。
2. **提醒级对照**：confirm 提醒 → 以提醒日收盘价为参考价，T+1 / T+3 日 K 收益回算，
   增量回填进 alerts[].meta.returns（未到期的标记 pending，次日自动续算）。
3. **误判分类**（classify_failure 方向级扩展）：逻辑失效 / 阈值过敏 /
   数据缺失误导 / 环境突变——批次 D 调参的分层输入。

持久化续用批次 B 决策（文件，不进 prediction_themes）：
- `prediction_themes` 的 apply_verify 按 target_date 定位、按 theme 名覆盖——
  简报方向与 predict 题材同表时，周末预判（target=下一交易日）与盘中方向
  （target=当天）会在同一天撞行互相覆盖；
- predict 的 hit_stats 按 verdict 分组聚合全表，简报方向没有 verdict 语义，
  混入会把预判命中率统计系统性污染。
文件聚合的量级（≤30 天 × 3 方向 × 若干提醒）读盘毫无压力，批次 C 不需要 SQL。

口径纪律（沿用批次 B）：
- tracker 内存态（重启丢失）优先——它有盘中峰值/证伪触发器；不可用时降级
  「收盘快照口径」，note 里显式标注，绝不冒充盘中口径。
- 收盘快照的「发酵」判据 = 可判定项全部满足、无明确不满足（量比恒 unknown
  不阻塞——与 intraday_rules docstring「量比缺失仍可给 0.75 提示」语义对齐；
  盘中 tracker.confirmed 才是五项全过的严格口径）。
- 提醒收益参考价 = 提醒日收盘价（第一版提醒 payload 未存提醒时刻现价，
  不臆造分时价格）；个股当日无 K 线（停牌）→ 该档收益留空，不拿邻近日期冒充。
- 证伪优先于发酵（终态口径）：确认后回撤证伪的方向按「证伪」归档，
  「确认过」的事实由 confirmed 标志与误判分类的「阈值过敏」保留。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.market import trade_calendar as tc
from app.picks.intraday_rules import CONFIRM_THEME_PCT_LATE, confirm_signal
from app.picks.morning_brief import BRIEF_DIR, load_brief, save_brief
from app.core.bjtime import beijing_now

log = logging.getLogger(__name__)

REVIEW_CONTEXT = "intraday_review"
#: 半发酵线：5 项确认条件满足 ≥3 项（§7.1）
HALF_CONFIRM_MET = 3
#: 数据缺失误导线：整拍缺数据占比 ≥30% 时该方向的"无波动/半发酵"结论不可信
MISSING_MISLEAD_RATIO = 0.3
#: 收盘快照按晚盘确认线口径重算（15:35 已过 10:00 分界）
CLOSE_SNAPSHOT_MINUTES = 15 * 60 + 35

OUTCOME_FERMENT = "发酵"
OUTCOME_HALF = "半发酵"
OUTCOME_FALSIFIED = "证伪"
OUTCOME_FLAT = "无波动"
OUTCOMES = (OUTCOME_FERMENT, OUTCOME_HALF, OUTCOME_FALSIFIED, OUTCOME_FLAT)
#: 误判四分类（§7.3）——批次 D 网格调参的分层维度
FAILURE_LOGIC = "逻辑失效"
FAILURE_OVERSENSITIVE = "阈值过敏"
FAILURE_DATA_GAP = "数据缺失误导"
FAILURE_ENV = "环境突变"


# ---------------------------------------------------------------- 纯函数：判定与分类


def closing_confirmed(conf: dict) -> bool:
    """收盘快照口径的「确认规则当日曾全满足」：无明确不满足、且至少一项
    **方向级**可判定项满足。环境项（promo 分位/相位）是全方向共享的，
    不构成方向证据——只有环境满足而题材证据全 unknown 的方向按无波动处理。

    量比恒 unknown 不阻塞本口径——盘中 tracker.confirmed 才是五项全过的
    严格口径；若这里也按严格口径，发酵将因数据源缺口永不可达，复盘失去意义。
    """
    checks = conf.get("checks") or []
    known_directional = [
        c for c in checks if c.get("met") is not None and c.get("key") != "environment"
    ]
    return (
        conf.get("unmet_count", 0) == 0
        and bool(known_directional)
        and any(c["met"] for c in known_directional)
    )


def classify_outcome(
    *,
    falsified: bool,
    tracker_confirmed: bool,
    closing_confirmed: bool,
    met_count: int | None,
) -> str:
    """方向级四分类（§7.1）。优先级：证伪 > 发酵 > 半发酵 > 无波动（终态口径）。"""
    if falsified:
        return OUTCOME_FALSIFIED
    if tracker_confirmed or closing_confirmed:
        return OUTCOME_FERMENT
    if met_count is not None and met_count >= HALF_CONFIRM_MET:
        return OUTCOME_HALF
    return OUTCOME_FLAT


def classify_failure_direction(
    *,
    confirmed: bool,
    falsified: bool,
    falsify_keys: list[str],
    missing_ratio: float | None,
    actual_pct: float | None,
) -> str | None:
    """方向级误判分类（§7.3，classify_failure 的扩展）。返回 None = 不算误判。

    判定顺序即优先级（环境突变 > 阈值过敏 > 数据缺失误导 > 逻辑失效）：
    - 环境突变：证伪由环境触发器引起（promo 分位/相位）——不是方向本身的错；
    - 阈值过敏：确认过又当日证伪（回撤/转负）——确认线太敏感的信号；
    - 数据缺失误导：未确认未证伪但缺数据拍占比过高——"没走出来"可能是没看到；
    - 逻辑失效：未确认未证伪且收盘板块涨幅连晚盘确认线都没碰到——盘前逻辑
      （事件/题材动能）没能兑现。
    """
    if falsified and "environment" in falsify_keys:
        return FAILURE_ENV
    if confirmed and falsified:
        return FAILURE_OVERSENSITIVE
    if (
        not confirmed and not falsified
        and missing_ratio is not None and missing_ratio >= MISSING_MISLEAD_RATIO
    ):
        return FAILURE_DATA_GAP
    if (
        not confirmed and not falsified
        and actual_pct is not None and actual_pct < CONFIRM_THEME_PCT_LATE
    ):
        return FAILURE_LOGIC
    return None


def next_trade_dates(days: list[date], anchor: date, n: int) -> list[date]:
    """anchor 之后的 n 个交易日（日历升序；官方日历含未来日期）。"""
    return [d for d in days if d > anchor][:n]


def compute_alert_returns(
    closes: dict[date, float], *, d0: date, t1: date | None, t3: date | None
) -> dict:
    """提醒收益回算（纯函数）。参考价 = 提醒日（D0）收盘价。

    个股当日无 K 线（停牌/新股）→ 全档留空（不拿邻近交易日冒充）；
    T+1/T+3 当日无 K 线 → 该档留空、complete=False，次日续算。
    """
    ref = closes.get(d0)
    out = {
        "ref_date": d0.isoformat(),
        "ref_price": ref,
        "t1_date": t1.isoformat() if t1 else None,
        "t1_return": None,
        "t3_date": t3.isoformat() if t3 else None,
        "t3_return": None,
        "complete": False,
    }
    if ref is None or ref <= 0:
        return out
    if t1 is not None and closes.get(t1) is not None:
        out["t1_return"] = round((closes[t1] / ref - 1) * 100, 2)
    if t3 is not None and closes.get(t3) is not None:
        out["t3_return"] = round((closes[t3] / ref - 1) * 100, 2)
    out["complete"] = out["t3_return"] is not None
    return out


def should_run_review(
    now: datetime,
    *,
    run_hour: int,
    run_minute: int,
    brief_exists: bool,
    already_reviewed: bool,
) -> bool:
    """调度判定（纯函数）。已到触发点且当日简报尚未被 schedule 触发复盘过。

    already_reviewed 读的是**简报文件里的 review.trigger**（持久化），不是内存
    变量——复盘调度沿用 review_scheduler 的教训：重启会清空内存，而"时间已过
    触发点"在重启后天然成立，内存去重等于每次重启都重跑。
    """
    if (now.hour, now.minute) < (run_hour, run_minute):
        return False
    if now.hour >= 23:  # 深夜不再补跑，避免与次日盘前简报窗口纠缠
        return False
    return brief_exists and not already_reviewed


# ---------------------------------------------------------------- 纯函数：单方向复盘


def review_direction(
    d: dict, tracker: dict | None, closing_theme: dict | None, env: dict
) -> dict:
    """单方向复盘（无 IO）。tracker=盘中状态（watcher.state 的 tracker 项），
    closing_theme=收盘涨停池题材节拍（pct 已匹配东财板块涨幅）。"""
    theme = dict(closing_theme or {})
    conf = confirm_signal(
        theme_pct=theme.get("pct"),
        theme_limit_up=theme.get("limit_up") if theme else None,
        theme_max_boards=theme.get("max_boards") if theme else None,
        leader_pct=theme.get("leader_pct") if theme else None,
        volume_ratio=None,  # 收盘快照同样无量比来源 → unknown（见 closing_confirmed）
        promo_percentile=env.get("promo_percentile"),
        phase=env.get("phase"),
        now_minutes=CLOSE_SNAPSHOT_MINUTES,
    )
    confirmed = bool((tracker or {}).get("confirmed"))
    falsified = bool((tracker or {}).get("falsified"))
    falsify_keys = [
        t.get("key") for t in (tracker or {}).get("falsify_triggers") or [] if t.get("key")
    ]
    beats = (tracker or {}).get("beats") or 0
    missing_beats = (tracker or {}).get("missing_beats") or 0
    missing_ratio = (missing_beats / beats) if beats else None

    outcome = classify_outcome(
        falsified=falsified,
        tracker_confirmed=confirmed,
        closing_confirmed=closing_confirmed(conf),
        met_count=conf["met_count"],
    )
    failure = classify_failure_direction(
        confirmed=confirmed,
        falsified=falsified,
        falsify_keys=falsify_keys,
        missing_ratio=missing_ratio,
        actual_pct=theme.get("pct"),
    )

    leader = None
    if theme.get("leader_symbol"):
        leader = f'{theme["leader_symbol"]} {theme.get("leader_name") or ""}'.strip()

    notes: list[str] = []
    if tracker is None:
        notes.append("盘中 tracker 不可用（重启丢失），按收盘快照口径复盘")
    if theme.get("pct") is None:
        notes.append("板块涨幅未匹配到东财板块（unknown），涨幅项按缺失计")
    if failure == FAILURE_DATA_GAP and missing_ratio is not None:
        notes.append(f"缺数据拍占比 {missing_ratio:.0%}，结论可信度受限")

    return {
        "outcome": outcome,
        "failure_class": failure,
        "confirmed": confirmed,
        "falsified": falsified,
        "falsify_keys": falsify_keys,
        "closing_met": conf["met_count"],
        "closing_total": len(conf["checks"]),
        "closing_unknown": conf["unknown_count"],
        "actual_pct": theme.get("pct"),
        "actual_limit_up": theme.get("limit_up") if theme else None,
        "actual_max_boards": theme.get("max_boards") if theme else None,
        "actual_leader": leader,
        "missing_ratio": round(missing_ratio, 2) if missing_ratio is not None else None,
        "note": "；".join(notes),
        "source": "tracker" if tracker is not None else "closing_snapshot",
    }


# ---------------------------------------------------------------- IO：收盘事实


def _normalize_state(app_state):
    return app_state.state if hasattr(app_state, "state") else app_state


async def collect_closing_facts(state) -> dict:
    """收盘口径的当日事实：涨停池题材归因（复用 watcher 的同一份数据路径）
    + 东财板块涨幅匹配 + 情绪环境（每天一次，全量重算不心疼配额）。

    复用 watcher._beat_themes_from_pool / _board_pcts / match_board_pct——
    盘中与盘后同一口径，不出现"复盘用的板块涨幅和盘中不是一套"。
    """
    from app.picks.watcher import _beat_themes_from_pool, _board_pcts, match_board_pct

    hub = state.hub
    now = beijing_now()
    missing: list[str] = []
    days = None
    try:
        days = await tc.trading_days(hub.provider)
    except Exception as exc:
        missing.append(f"交易日历不可用（{exc}）")
    td = tc.last_trade_date(days, asof=now.date()) if days else None

    pool: list = []
    if td is not None:
        try:
            pool = await hub.provider.get_limit_up_pool(td) or []
        except Exception as exc:
            missing.append(f"涨停池不可用（{exc}）")
    else:
        missing.append("无法定位涨停池日期")
    themes = _beat_themes_from_pool(pool)
    board_pct, board_count = await _board_pcts(hub)
    for tag, st in themes.items():
        st["pct"] = match_board_pct(tag, board_pct)

    env: dict = {"phase": None, "promo_percentile": None}
    try:
        from app.services.market_context import compute_market_sentiment

        sent = await compute_market_sentiment(hub, state.snapshot_service) or {}
        env = {
            "phase": sent.get("phase"),
            "promo_percentile": (
                ((sent.get("calibration") or {}).get("percentile") or {}).get("promo_1to2") or {}
            ).get("percentile"),
        }
    except Exception as exc:
        missing.append(f"情绪环境不可用（{exc}）")

    return {
        "pool_date": td.isoformat() if td else None,
        "pool_count": len(pool),
        "board_count": board_count,
        "themes": themes,
        "env": env,
        "missing": missing,
    }


def _tracker_map(state) -> dict[str, dict]:
    """内存态 watcher 的 tracker 状态（按方向索引）。无 watcher / 重启后 = 空表。"""
    watcher = getattr(state, "picks_watcher", None)
    if watcher is None:
        return {}
    try:
        return {t["direction"]: t for t in watcher.state().get("trackers") or []}
    except Exception as exc:  # watcher 半初始化等极端情况不炸复盘
        log.warning("intraday review: tracker state unavailable: %s", exc)
        return {}


# ---------------------------------------------------------------- IO：编排


async def run_review(app, *, trigger: str = "manual") -> dict:
    """当日简报的方向级对照 + 全量提醒收益回填。

    手动触发可重复执行（覆盖 review 字段）；schedule 触发当日幂等
    （简报里已有 schedule 复盘就跳过，手动的不拦——手动先跑的复盘口径
    比没有强，schedule 到点会用 tracker 内存态重算出更完整的一版）。
    """
    state = _normalize_state(app)
    now = beijing_now()
    if (now.hour, now.minute) < (9, 25):
        # 盘前跑复盘 = 拿"今天的空池"当"全天没动静"，是 morning_brief
        # _evidence_pool_date 同款自指污染——显式拒绝，不产出垃圾结果。
        return {
            "ok": False, "reason": "market_not_open", "brief_date": now.strftime("%Y%m%d"),
            "detail": "09:25 前当日盘面尚未形成，复盘无意义",
        }
    target = now.strftime("%Y%m%d")
    payload = load_brief(target)
    if payload is None:
        return {"ok": False, "reason": "no_brief", "brief_date": target}
    if trigger == "schedule" and (payload.get("review") or {}).get("trigger") == "schedule":
        return {"ok": True, "skipped": "already_reviewed", "brief_date": target}

    directions = payload.get("directions") or []
    trackers = _tracker_map(state)
    facts = await collect_closing_facts(state)
    env = facts.get("env") or {}
    reviews = []
    for d in directions:
        tag = d.get("direction") or ""
        rv = review_direction(d, trackers.get(tag), (facts.get("themes") or {}).get(tag), env)
        d["review"] = rv
        reviews.append({"direction": tag, "outcome": rv["outcome"], "failure_class": rv["failure_class"]})

    payload["review"] = {
        "context": REVIEW_CONTEXT,
        "reviewed_at": beijing_now().isoformat(),
        "trigger": trigger,
        "pool_date": facts.get("pool_date"),
        "pool_count": facts.get("pool_count"),
        "board_count": facts.get("board_count"),
        "env": {"phase": env.get("phase"), "promo_percentile": env.get("promo_percentile")},
        "tracker_source": bool(trackers),
        "outcomes": {r["direction"]: r["outcome"] for r in reviews},
        "missing": facts.get("missing") or [],
    }
    save_brief(payload)

    backfill = await backfill_alert_returns(state)

    # 猎场批次 A（需求 9/10）：跟踪台账收盘清算——入选价 vs 当日收盘，逐股判定
    # + 统计。失败只记日志（清算幂等，下一轮补）。
    ledger_settled = None
    with contextlib.suppress(Exception):
        from app.picks.watch_ledger import get_day, settle_day, validate_previous_day
        from app.core.bjtime import beijing_now as _bnow
        from app.core.db import get_session_factory as _gsf

        tdate = _bnow().date().isoformat()
        provider = _tencent_provider(state.hub) or state.hub.provider
        closes: dict[str, float] = {}
        for r in get_day(tdate, _gsf()):
            if r["status"] != "tracking":
                continue
            with contextlib.suppress(Exception):
                dc = await _daily_closes(provider, r["symbol"])
                c = dc.get(_bnow().date())
                if c is not None:
                    closes[r["symbol"]] = c
        ledger_settled = settle_day(tdate, closes, _gsf())
        # 次日持续性验证（闭环「验证」段）：T-1 行写回 T 收盘表现
        d1_n = validate_previous_day(closes, _gsf())
        log.info("watch ledger settle: %s | D+1 验证 %s 行", ledger_settled, d1_n)

    log.info(
        "intraday review saved: %s（%d 方向 %s；提醒回填 %s）",
        target, len(reviews),
        {r["direction"]: r["outcome"] for r in reviews},
        backfill.get("alerts_updated"),
    )
    return {"ok": True, "brief_date": target, "directions": reviews, "alert_backfill": backfill,
            "ledger_settled": ledger_settled}


# ---------------------------------------------------------------- IO：提醒收益回算


def _tencent_provider(hub):
    composite = hub.provider if hasattr(hub.provider, "providers") else None
    return next(
        (p for p in (composite.providers if composite else [hub.provider])
         if getattr(p, "name", "") == "tencent"),
        hub.provider,
    )


def _parse_brief_date(s: Any) -> date | None:
    try:
        return datetime.strptime(str(s), "%Y%m%d").date()
    except Exception:
        return None


async def _daily_closes(provider, symbol: str) -> dict[date, float]:
    """日 K → {交易日: 收盘价}。腾讯日 K 的 ts 是交易日 0 点（UTC），
    ts.date() 即交易日；失败返回空表（调用方按 pending 处理，不臆造）。

    2026-09-09 修复：日 K timeframe 全栈统一是 "1d"（tencent/eastmoney/mock/
    ths 一致），此前硬编码 "day" → 任何 provider 承接都抛错被 suppress 吞掉
    → settle 收盘价恒空 → 台账 verdict 全 None（胜率失真根因）。
    """
    try:
        bars = await provider.get_kline(symbol, "1d")
    except Exception as exc:
        log.warning("alert returns: kline %s failed: %s", symbol, exc)
        return {}
    closes: dict[date, float] = {}
    for b in bars or []:
        try:
            d = b.ts.date()
            if b.close is not None and d.year >= 2020:
                closes[d] = float(b.close)
        except Exception:
            continue
    return closes


def list_brief_payloads(limit: int = 30) -> list[dict]:
    """按日期倒序读简报文件（跳过损坏文件，不中断其余）。"""
    if not BRIEF_DIR.exists():
        return []
    out: list[dict] = []
    for path in sorted(Path(BRIEF_DIR).glob("*.json"), reverse=True):
        if not path.stem.isdigit():
            continue
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            log.warning("brief %s corrupted: %s", path.name, exc)
        if len(out) >= limit:
            break
    return out


async def backfill_alert_returns(state, *, lookback: int = 30) -> dict:
    """confirm 提醒的 T+1/T+3 收益增量回填（跨全部近期简报文件）。

    已 complete（t3 有值）的提醒跳过；t3 未到期或数据缺失的每日续算——
    直到 complete 或超过 lookback 窗口。停牌股该档留空，不拿邻近交易日冒充。
    """
    state = _normalize_state(state)
    hub = state.hub
    payloads = list_brief_payloads(lookback)
    out = {
        "ok": True, "briefs_scanned": len(payloads),
        "alerts_updated": 0, "alerts_pending": 0, "alerts_kline_failed": 0,
    }
    has_candidates = any(
        a.get("kind") == "confirm" and a.get("symbol")
        for p in payloads for a in (p.get("alerts") or [])
    )
    if not has_candidates:
        return out  # 无提醒可回填：不取日历/provider（测试 fake hub 也无需实现这些）
    try:
        days = await tc.trading_days(hub.provider)
    except Exception as exc:
        return {"ok": False, "detail": f"交易日历不可用（{exc}）", "alerts_updated": 0}
    provider = _tencent_provider(hub)
    closes_cache: dict[str, dict[date, float]] = {}
    for p in payloads:
        alerts = [a for a in (p.get("alerts") or []) if a.get("kind") == "confirm" and a.get("symbol")]
        if not alerts:
            continue
        d0 = _parse_brief_date(p.get("brief_date"))
        if d0 is None:
            continue
        t_dates = next_trade_dates(days, d0, 3)
        t1 = t_dates[0] if t_dates else None
        t3 = t_dates[2] if len(t_dates) >= 3 else None
        dirty = False
        for a in alerts:
            meta = a.setdefault("meta", {})
            old = meta.get("returns")
            if isinstance(old, dict) and old.get("t3_return") is not None:
                continue  # 已完整，永不重算
            sym = a["symbol"]
            if sym not in closes_cache:
                closes_cache[sym] = await _daily_closes(provider, sym)
            closes = closes_cache[sym]
            if not closes:
                out["alerts_kline_failed"] += 1
                out["alerts_pending"] += 1
                continue  # 无 K 线不落盘（防止空 returns 冒充已回算）
            new_ret = compute_alert_returns(closes, d0=d0, t1=t1, t3=t3)
            meta["returns"] = new_ret
            dirty = True
            out["alerts_updated"] += 1
            if not new_ret["complete"]:
                out["alerts_pending"] += 1
        if dirty:
            save_brief(p)
    return out


# ---------------------------------------------------------------- 纯函数：胜率统计


def _win_rate(rows: list[dict], key: str) -> dict:
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return {"n": 0, "win_rate": None, "avg_win": None, "avg_loss": None,
                "profit_loss_ratio": None, "avg_return": None}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v <= 0]
    avg_win = round(sum(wins) / len(wins), 2) if wins else None
    avg_loss = round(sum(losses) / len(losses), 2) if losses else None
    plr = (
        round(avg_win / abs(avg_loss), 2)
        if avg_win is not None and avg_loss is not None and avg_loss < 0
        else None
    )
    return {
        "n": len(vals),
        "win_rate": round(len(wins) / len(vals) * 100, 1),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_loss_ratio": plr,
        "avg_return": round(sum(vals) / len(vals), 2),
    }


def _stats_from_payloads(payloads: list[dict]) -> dict:
    daily: list[dict] = []
    outcome_counts: dict[str, int] = {k: 0 for k in OUTCOMES}  # 全键稳定形态（前端免判 key）
    failure_counts: dict[str, int] = {}
    alert_rows: list[dict] = []
    for p in payloads:
        dirs = p.get("directions") or []
        reviewed = [
            (d.get("direction"), d.get("review") or {})
            for d in dirs
            if (d.get("review") or {}).get("outcome")
        ]
        day_outcomes = [r["outcome"] for _, r in reviewed]
        for _, r in reviewed:
            outcome_counts[r["outcome"]] = outcome_counts.get(r["outcome"], 0) + 1
            fc = r.get("failure_class")
            if fc:
                failure_counts[fc] = failure_counts.get(fc, 0) + 1
        confirms = [a for a in (p.get("alerts") or []) if a.get("kind") == "confirm"]
        for a in confirms:
            ret = (a.get("meta") or {}).get("returns") or {}
            alert_rows.append({
                "date": p.get("brief_date"),
                "direction": a.get("direction"),
                "symbol": a.get("symbol"),
                "name": a.get("name"),
                "t1_return": ret.get("t1_return"),
                "t3_return": ret.get("t3_return"),
            })
        daily.append({
            "date": p.get("brief_date"),
            "directions": len(dirs),
            "reviewed": len(reviewed),
            "fermented": day_outcomes.count(OUTCOME_FERMENT),
            "half": day_outcomes.count(OUTCOME_HALF),
            "falsified": day_outcomes.count(OUTCOME_FALSIFIED),
            "flat": day_outcomes.count(OUTCOME_FLAT),
            "alerts": len(confirms),
        })
    return {
        "daily": daily,
        "directions": {
            "total": sum(d["reviewed"] for d in daily),
            "outcomes": outcome_counts,
            "failures": failure_counts,
        },
        "alert_t1": _win_rate(alert_rows, "t1_return"),
        "alert_t3": _win_rate(alert_rows, "t3_return"),
        "alerts": alert_rows[-50:],  # 明细截尾：统计在上，全量在简报文件里
        "sample_note": "样本外跟踪 ≥20 条前，统计仅供参考；参考价=提醒日收盘价（第一版未存提醒时刻现价）",
    }


def intraday_stats(limit: int = 30) -> dict:
    """胜率统计（近 limit 个简报日的聚合，§7.2）。纯文件聚合，零 SQL。"""
    return _stats_from_payloads(list_brief_payloads(limit))


# ---------------------------------------------------------------- IO：调度


async def intraday_review_scheduler(
    app,
    *,
    stop: asyncio.Event,
    run_hour: int = 15,
    run_minute: int = 35,
    check_interval_seconds: float = 60.0,
) -> None:
    """盘后对照调度（lifespan 任务）：交易日 15:35 后对照当日简报，当日幂等。

    幂等判定读简报文件（should_run_review 的 already_reviewed）——重启安全；
    非交易日自然跳过（无简报文件）。无简报不报警（盘前调度失败时这里安静，
    手动端点 /intraday-review/run 可补跑）。
    """
    while not stop.is_set():
        try:
            now = beijing_now()
            target = now.strftime("%Y%m%d")
            payload = load_brief(target) if now.hour >= run_hour else None
            if should_run_review(
                now,
                run_hour=run_hour,
                run_minute=run_minute,
                brief_exists=payload is not None,
                already_reviewed=(payload or {}).get("review", {}).get("trigger") == "schedule",
            ):
                await run_review(app, trigger="schedule")
            # 批次 D：题材热度时序前向落库（独立于复盘成败；自带磁盘幂等，
            # 已落库的 tick 不会再碰行情配额）。窗口与复盘一致：run_hour–23 点。
            # B1：飙升榜收盘快照同 tick 落库（独立文件、独立去重，失败互不影响）。
            if run_hour <= now.hour < 23:
                from app.picks.heat_history import record_daily_heat, record_daily_skyrocket

                await record_daily_heat(app)
                await record_daily_skyrocket(app)
                # 板块资金流收盘快照（board_flow L2 落盘：≥15:05 内部守卫 + 当日幂等）
                try:
                    from app.market.board_flow import snapshot_daily_if_closed

                    await snapshot_daily_if_closed()
                except Exception:
                    log.warning("boardflow daily snapshot failed", exc_info=True)
                # 分钟决策库（P1-24）：盘后扫描当日跟踪标的 → 记录做 T 信号 → 结算。
                # 自带当日幂等 + 台账为空则跳过；TDX 直连是阻塞调用故走线程池。
                try:
                    from app.market.minute_decisions import scan_and_settle_today

                    scan = await asyncio.to_thread(scan_and_settle_today, app)
                    if scan.get("recorded") or scan.get("settled"):
                        log.info("minute decisions: %s", scan)
                except Exception:
                    log.warning("minute decisions scan failed", exc_info=True)
        except Exception:
            log.exception("intraday review scheduler failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=check_interval_seconds)
