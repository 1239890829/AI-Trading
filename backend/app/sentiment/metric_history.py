"""情绪原始指标的**历史回补**与增量维护（P0-3b 的数据底座）。

## 为什么需要它

分位校准要有历史样本，而本地 `sentiment_history` 表只有 3 行、快照 Parquet 只有 5 天。
但**涨停池/炸板池可以用历史日期回补**——实测 ths `get_limit_up_pool(date)` 可回溯
**至少 2 年**（2024-12-31 仍返回 46 条），且**没有东财 push2ex 那种"非交易日静默回退"**
（31 个交易日的涨停家数 46–137 各不相同，见 `docs/system-review-2026-09-02.md` §4.4）。

于是 `limit_up` / `max_board` / `break_rate` / `promo_1to2` / `promo_2to3` 五个指标
都能从历史回算，`calibration.py` 才有米下锅。

## 存什么

**只存算好的每日指标，不存原始涨停池**——120 天 × ~90 条记录会膨胀到几 MB，
而校准只需要那五个数字。指标在遍历时流式算出（当天池 + 前一日池），算完即弃。

## 回退陷阱哨兵

东财 `push2ex` 传非交易日会**静默返回最近交易日数据**（见 `docs/data-sources.md` §3.1），
本项目已因此误判过一次情绪阶段。ths 实测无此问题，但不能只信一次实测——
若某天与前一天的涨停池**股票集合完全相同**，判定为疑似回退，该日指标**不入库**
并计入 `suspicious`，宁可少一个样本也不要污染分布。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from app.market.trading_status import beijing_now

log = logging.getLogger(__name__)

_STORE_PATH = Path(__file__).resolve().parents[2] / "data" / "sentiment_metrics.json"

# 请求间隔（秒）：批量回溯别把 ths 配额打满
_FETCH_GAP = 0.15

METRIC_KEYS = ("limit_up", "max_board", "break_rate", "promo_1to2", "promo_2to3")


def beijing_today() -> date:
    """北京时区的今天。别用 date.today()——容器/本机时区不一定是 CST。"""
    return beijing_now().date()


def _boards_of(pool) -> dict[str, int]:
    from app.sentiment.engine import _boards_of as _engine_boards

    return _engine_boards(pool)


def daily_metrics(pool_prev, pool_today, break_pool) -> dict:
    """单日原始指标（**纯函数**）。口径与 `engine.promotion_rates` 完全一致。

    实测对齐：2026-08-28 本函数 promo_1to2 = 0.150，库里 sentiment_history 存 0.15。
    """
    from app.sentiment.engine import promotion_rates

    b_today = _boards_of(pool_today)
    limit_up = len(b_today)
    max_board = max(b_today.values()) if b_today else 0
    promo = promotion_rates(pool_today, pool_prev)
    # break_pool=None 表示"炸板池拉取失败"（未知），[] 才是真的"今日无炸板"。
    # 二者混用会把失败伪造成 0% 炸板率——看似有效的假数据比缺数据更毒。
    if break_pool is None:
        n_break = None
        break_rate = None
    else:
        n_break = len(break_pool)
        total = n_break + limit_up
        break_rate = round(n_break / total, 4) if total else None
    return {
        "limit_up": limit_up,
        "max_board": max_board,
        "break_rate": break_rate,
        "promo_1to2": promo["promo_1to2"],
        "promo_2to3": promo["promo_2to3"],
        "n_break": n_break,
    }


def _is_same_pool(a, b) -> bool:
    """疑似回退判定：两天涨停池的股票集合完全相同。"""
    if not a or not b or len(a) != len(b):
        return False
    return {r.symbol for r in a} == {r.symbol for r in b}


async def _fetch_day(provider, d: date) -> tuple[list, list | None]:
    pool = await provider.get_limit_up_pool(d)
    await asyncio.sleep(_FETCH_GAP)
    try:
        brk = await provider.get_limit_break_pool(d)
    except Exception as exc:  # 炸板池缺失不应拖垮整轮回补
        log.warning("limit-break pool %s failed: %s", d, exc)
        brk = None  # None=拉取失败（break_rate 记 unknown），不是"无炸板"
    await asyncio.sleep(_FETCH_GAP)
    return pool or [], brk


def _as_date(v) -> date:
    """日历元素归一：接受 date / datetime / ISO 字符串。

    **2026-09-10 实测事故**：调度侧直取 provider 原始日历（**字符串**）传给 `backfill`，
    而本函数按 `date` 比较 → `TypeError: '<' not supported between instances of
    'str' and 'datetime.date'`；异常被调度器的 `except Exception` 收成一条 ERROR 日志，
    结果**库静默停在 2026-09-01、连续 6 个交易日没更新**，而界面照旧写着
    「按近 241 个交易日的历史分位校准」——窗口早已漂移却无人能一眼看出。

    归一做在这里：传错类型不该是一个沉默数周的数据缺陷（调用方同时已修）。
    """
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


async def backfill(
    provider,
    trade_days: list,
    lookback: int = 120,
    *,
    store_path: Path | None = None,
    force: bool = False,
    today: date | None = None,
) -> dict:
    """回补近 `lookback` 个交易日的指标（增量：已有日期不重拉）。

    **绝不回补"今天"**：盘中涨停池是半截数据（家数还在涨、炸板未定型），
    入库会把分布往盘中口径拉偏。今天的数据等下一个周期（次日）再补——
    对 120 天窗口而言 1 天滞后可忽略，而口径纯净不可妥协。

    :param trade_days: 交易日序列（``date`` / ISO 字符串均可，内部归一；见 `_as_date`）

    Returns:
        `{added, skipped, suspicious, total, days}`——`suspicious` 为疑似回退
        被剔除的天数，非 0 时应告警。
    """
    path = store_path or _STORE_PATH
    today = today or beijing_today()
    store = load(path)
    # 多取一天：最早那天的指标需要它的前一日；再剔除今天（见 docstring）。
    # 先去重再排序——重复日期会让相邻两天被当成"同一天"参与 _is_same_pool 判定。
    days = [d for d in sorted({_as_date(x) for x in trade_days}) if d < today][-(lookback + 1):]

    pool_by_day: dict[date, list] = {}
    added = skipped = suspicious = 0

    # 只在需要时拉：若某日及其前一日都已在库且非 force，跳过网络请求
    for i in range(1, len(days)):
        prev_d, d = days[i - 1], days[i]
        key = d.isoformat()
        if not force and key in store["days"]:
            skipped += 1
            continue
        if prev_d not in pool_by_day:
            pool_by_day[prev_d] = (await _fetch_day(provider, prev_d))[0]
        pool_prev = pool_by_day[prev_d]
        pool_today, brk = await _fetch_day(provider, d)
        pool_by_day[d] = pool_today

        if _is_same_pool(pool_prev, pool_today):
            # 疑似数据源回退（见模块 docstring）：宁可缺样本也不污染分布
            log.warning("suspicious identical limit-up pool for %s and %s, skipped", prev_d, d)
            suspicious += 1
            continue
        m = daily_metrics(pool_prev, pool_today, brk)
        m["date"] = key
        store["days"][key] = m
        added += 1

    store["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store["lookback"] = lookback
    _save(store, path)
    return {
        "added": added, "skipped": skipped, "suspicious": suspicious,
        "total": len(store["days"]), "days": sorted(store["days"])[-lookback:],
    }


def load(store_path: Path | None = None) -> dict:
    """读回指标库。文件不存在/损坏 → 空库（不抛异常，调用方据此判定需回补）。"""
    path = store_path or _STORE_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("days"), dict):
            return raw
    except Exception as exc:
        # 库损坏/不可读 → 空库口径继续（校准自然回退 defaults），但必须留痕
        log.warning("sentiment metric history unreadable, treating as empty: %s", exc)
    return {"updated_at": None, "lookback": None, "days": {}}


def history(store_path: Path | None = None, limit: int | None = None) -> list[dict]:
    """按日期升序的指标序列（供 `calibration.calibrate_bands` 消费）。"""
    store = load(store_path)
    rows = [store["days"][k] for k in sorted(store["days"])]
    return rows[-limit:] if limit else rows


def percentile_of_value(
    metric: str, value: float | None, store_path: Path | None = None
) -> dict | None:
    """把**当日实测值**放进历史样本里算分位（0–100）。样本不足或值缺失 → None。

    与 `calibration.describe()` 的区别（2026-09-10 修正）：`describe` 取的是
    **历史库里最后一行**的分位，而库里最后一行是"上一个交易日"（`backfill` 刻意
    不回补今天，见其 docstring）——把它当成"今天的分位"用，等于每天用昨天的位置
    描述今天，且库一旦停更就变成"用上个月的位置描述今天"（实测陈旧 6 个交易日）。
    本函数显式接收当日值，回答的才是「**今天**处在历史什么位置」。
    """
    if value is None:
        return None
    from app.sentiment.calibration import percentile_of

    vals = sorted(
        float(r[metric]) for r in history(store_path) if r.get(metric) is not None
    )
    if not vals:
        return None
    return {
        "value": float(value),
        "percentile": round(percentile_of(vals, float(value)), 1),
        "samples": len(vals),
    }


def _save(store: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(store, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
