"""看板 IO 编排：并发取数 + 逐题材建卡（`build_theme_board` 及其取数辅助）。

`backend/app/services/theme_service.py` 的内部切片（IMP-005 批 4，2026-09-15 从 1383 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与前置注释与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app.services.theme_service` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, timedelta
from app.core.bjtime import beijing_today

from .attribution import (
    UNCLASSIFIED,
    assign_primary_themes,
)
from .board_match import (
    _board_index,
)
from .card import (
    _build_card,
)
from .core import (
    _pick_provider,
    log,
)
from .normalize import (
    normalize_theme,
    parse_theme_tags,
)
async def build_theme_board(
    provider,
    trade_date: date,
    *,
    lookback_days: int = 5,
    snapshot_map: dict[str, dict] | None = None,
    fetch_snapshot=None,
) -> dict:
    """构建题材梯队看板。

    :param provider: composite provider（需提供 get_limit_up_pool / get_limit_break_pool）
    :param trade_date: 交易日（必须是交易日，非交易日的日期回退问题见 data-sources.md）
    :param lookback_days: 回溯天数，用于「连续活跃天数」与「前一日溢价」
    :param snapshot_map: symbol -> {"change_pct": float}；为空则溢价相关指标为 None
    :param fetch_snapshot: 可选回调，用于惰性获取快照（测试友好）
    """
    if snapshot_map is None and fetch_snapshot is not None:
        try:
            snapshot_map = await fetch_snapshot()
        except Exception as exc:  # pragma: no cover - 防御
            log.warning("snapshot unavailable for theme board: %s", exc)
            snapshot_map = None

    # ---- 1. 当日涨停池（主源 ths：带题材归因）----
    try:
        today_pool = await provider.get_limit_up_pool(trade_date)
    except Exception as exc:
        raise RuntimeError(f"涨停池获取失败：{exc}") from exc
    if not today_pool:
        return {
            "trade_date": trade_date.isoformat(),
            "themes": [],
            "summary": {"limit_up_total": 0, "theme_count": 0, "note": "当日无涨停数据"},
        }

    # ---- 2~5 并发：东财增强维度 / 板块指标 / 历史涨停池 / 炸板率 / 竞价强弱 ----
    # 串行会在慢源上叠加耗时（曾把请求拖到 3 分钟并压垮事件循环）。
    enhance, board_index, history, market_break_rate, auction_gaps = await asyncio.gather(
        _em_enhancement_map(provider, trade_date),
        _board_index(),
        _load_history(provider, trade_date, lookback_days),
        _market_break_rate(provider, trade_date, len(today_pool)),
        _auction_gaps(provider, trade_date, today_pool),
    )
    # history 由近及远，第 0 个就是最近的前一交易日
    prev_pool = history[0][1] if history else None

    prev_boards_map: dict[str, int] = {}
    prev_theme_stats: dict[str, dict] = {}
    # 昨日各题材的涨停股集合：溢价必须按题材算，用全市场口径会让所有题材溢价雷同
    prev_theme_symbols: dict[str, set[str]] = defaultdict(set)
    if prev_pool:
        for rec in prev_pool:
            prev_boards_map[rec.symbol] = rec.consecutive_boards or 0
            for th in [normalize_theme(t) for t in parse_theme_tags(rec.reason)] or [UNCLASSIFIED]:
                prev_theme_symbols[th].add(rec.symbol)
        prev_theme_stats = _theme_stats(prev_pool)

    # 按交易日由近及远排列（含当日），供「连续活跃天数」与「每日涨停家数」使用
    ordered_days: list[tuple[str, list]] = [(trade_date.isoformat(), today_pool)]
    ordered_days += [(d.isoformat(), p) for d, p in history]

    daily_counts: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for day_label, pool in ordered_days:
        for theme, st in _theme_stats(pool).items():
            daily_counts[theme].append((day_label, st["count"]))

    # 连续活跃天数：从当日往前，题材每天都出现涨停才累加（衡量资金是否持续）
    active_days: dict[str, int] = defaultdict(int)
    for theme in {normalize_theme(t) for r in today_pool for t in parse_theme_tags(r.reason)}:
        cnt = 0
        for _, pool in ordered_days:
            tags = {normalize_theme(t) for r in pool for t in parse_theme_tags(r.reason)}
            if theme in tags:
                cnt += 1
            else:
                break
        active_days[theme] = cnt

    # ---- 7. 断板股：昨日连板、今日不在涨停池 ----
    market_max_boards = max((r.consecutive_boards or 0) for r in today_pool)

    # 题材 → 成员（梯队联动归属：每只票只归属一个主题材，见 assign_primary_themes。
    # 旧实现按标签全量塞入，同一梯队被拆散到多张卡片、题材重复计数，实测拆散率 22%）
    primary_of = assign_primary_themes(today_pool)
    theme_members: dict[str, list] = defaultdict(list)
    stock_themes: dict[str, list[str]] = defaultdict(list)
    for rec in today_pool:
        tags = parse_theme_tags(rec.reason)
        # 归一化后必须去重：同一只票可能同时带「黄金+珠宝加工」，两者都归到「黄金珠宝」，
        # 不去重会导致同一 symbol 在题材卡片里出现两次，触发 React key 冲突。
        themes = []
        for t in tags:
            nt = normalize_theme(t)
            if nt not in themes:
                themes.append(nt)
        if not themes:
            themes = [UNCLASSIFIED]
        stock_themes[rec.symbol] = themes
        theme_members[primary_of[rec.symbol]].append(rec)

    cards: list[dict] = []
    for theme, members in theme_members.items():
        cards.append(
            _build_card(
                theme=theme,
                members=members,
                stock_themes=stock_themes,
                enhance=enhance,
                board_index=board_index,
                prev_boards_map=prev_boards_map,
                prev_theme_stats=prev_theme_stats,
                market_max_boards=market_max_boards,
                active_days=active_days.get(theme, 1),
                daily_counts=daily_counts.get(theme, []),
                market_break_rate=market_break_rate,
                snapshot_map=snapshot_map,
                prev_theme_symbols=prev_theme_symbols.get(theme, set()),
                auction_gaps=auction_gaps,
            )
        )

    cards.sort(key=lambda c: -c["strength_score"])

    # ---- 7. 断板股：昨日连板、今日不在涨停池 ----
    today_symbols = {r.symbol for r in today_pool}
    broken = []
    if prev_pool:
        for rec in prev_pool:
            if (rec.consecutive_boards or 0) >= 2 and rec.symbol not in today_symbols:
                broken.append(
                    {
                        "symbol": rec.symbol,
                        "name": rec.name,
                        "prev_boards": rec.consecutive_boards,
                        "themes": [normalize_theme(t) for t in parse_theme_tags(rec.reason)],
                        "role": "断板",
                    }
                )

    return {
        "trade_date": trade_date.isoformat(),
        "prev_trade_date": (prev_pool[0].trade_date.isoformat() if prev_pool else None),
        "themes": cards,
        "broken_ladder": broken,
        "summary": {
            "limit_up_total": len(today_pool),
            "theme_count": len(cards),
            "market_max_boards": market_max_boards,
            "market_break_rate": market_break_rate,
            "top_theme": cards[0]["theme"] if cards else None,
        },
        "caveats": [
            "题材归因来自同花顺官方涨停原因，非交易时段数据为最近交易日收盘口径",
            "板块 3/5/10 日涨跌幅为东财字段序推断，未经 K 线交叉验证（board_multi_day_verified=false）",
            "炸板池不带题材字段，题材级只能用「开板过的涨停股占比」近似",
        ],
    }


# ---------------------------------------------------------------- 内部辅助


async def _em_enhancement_map(provider, trade_date: date) -> dict[str, dict]:
    """取东财涨停池的增强维度。失败时返回空 dict（降级而非报错）。"""
    target = _pick_provider(provider, "EastmoneyProvider")
    if target is None:
        return {}
    try:
        rows = await target.get_limit_up_pool(trade_date)
    except Exception as exc:
        log.warning("eastmoney limit-up enhancement unavailable: %s", exc)
        return {}
    return {r.symbol: r for r in rows}


async def _load_history(provider, trade_date: date, lookback_days: int) -> list[tuple[date, list]]:
    """并发回溯历史涨停池，返回 [(date, pool), ...]（由近及远）。

    只用同花顺：题材归因与连板数只有它的口径一致。
    并发而非串行——串行时单个慢源会把整体拖到分钟级。
    """
    ths = _pick_provider(provider, "ThsFuyaoProvider")
    if ths is None:
        return []
    dates = [trade_date - timedelta(days=i) for i in range(1, lookback_days + 1)]
    results = await asyncio.gather(
        *[ths.get_limit_up_pool(d) for d in dates], return_exceptions=True
    )
    out: list[tuple[date, list]] = []
    for d, res in zip(dates, results):
        if isinstance(res, Exception) or not res:
            log.debug("limit-up pool %s unavailable: %s", d, res)
            continue
        out.append((d, list(res)))
    return out


async def _market_break_rate(provider, trade_date: date, limit_up_count: int) -> float | None:
    """全市场炸板率 = 炸板数 / (涨停数 + 炸板数)。

    **双源直取（P1-19 收尾，2026-09-10）**：优先 ths，抛异常时回落东财 push2ex。
    两家都用 `_pick_provider` 直取实例、不走 composite 链——理由见 `_pick_provider`：
    composite 会在每个失败源上串行重试（8s × 4 源），5 日回溯能拖到分钟级。
    此前只取 ths，**ths 一挂这里就静默退化成近似口径**。
    """
    for cls in ("ThsFuyaoProvider", "EastmoneyProvider"):
        p = _pick_provider(provider, cls)
        if p is None:
            continue
        try:
            broken = await p.get_limit_break_pool(trade_date)
        except Exception as exc:
            log.debug("limit-break pool unavailable (%s): %s", cls, exc)
            continue  # 换下一源；两源都失败 → 返回 None（调用方退化为近似并标注）
        if not broken or limit_up_count <= 0:
            return None  # 拿到但空/无涨停 → 如实 None，不再换源（避免拿另一源口径硬凑）
        return round(len(broken) / (len(broken) + limit_up_count), 4)
    return None


async def _auction_gaps(provider, trade_date: date, pool: list) -> dict[str, float] | None:
    """当日集合竞价高开幅度 map（B2：龙头打分竞价强弱维度的数据源）。

    双守卫，任一不满足返回 None（打分缺口中性）：
    - trade_date 必须是**今天**——ths 竞价端点只有当日数据，历史回看若套上
      今日竞价就是跨日污染（本项目数据源日期回退的坑踩过多次）；
    - provider 需实现 get_auction_snapshot（mock 桩/旧链可能没有）。
    分批 ≤100 只（ths 单次上限）；非就绪条目（not_ready/suspended）跳过不标 gap。
    """
    if trade_date != beijing_today():
        return None
    fn = getattr(provider, "get_auction_snapshot", None)
    if fn is None:
        return None
    symbols: list[str] = []
    for rec in pool:
        if rec.symbol not in symbols:
            symbols.append(rec.symbol)
    out: dict[str, float] = {}
    for i in range(0, len(symbols), 100):
        batch = symbols[i : i + 100]
        try:
            rows = await fn(batch, stage="final")
        except Exception as exc:  # noqa: BLE001 - 竞价缺失只降级不阻断看板
            log.warning("auction snapshot unavailable: %s", exc)
            continue
        for r in rows or []:
            status = r.get("data_status")
            pct = r.get("auction_pct")
            if status not in (None, "ready", "final") or pct is None:
                continue
            try:
                out[r["symbol"]] = float(pct)
            except (TypeError, ValueError):
                continue
    return out or None


def _theme_stats(pool: list) -> dict[str, dict]:
    """统计一批涨停池中各题材的 (家数, 最高板)。"""
    stats: dict[str, dict] = {}
    for rec in pool:
        tags = parse_theme_tags(rec.reason)
        themes = [normalize_theme(t) for t in tags] or [UNCLASSIFIED]
        for th in themes:
            st = stats.setdefault(th, {"count": 0, "max_boards": 0})
            st["count"] += 1
            st["max_boards"] = max(st["max_boards"], rec.consecutive_boards or 0)
    return stats
