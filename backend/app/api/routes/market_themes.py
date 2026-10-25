"""题材与总览：市场总览、题材板（含官方标记与多日校验）、入场清单、筹码。

（自 `market.py` 切出，2026-09-15 IMP-005 批 3。**只搬位置，未改逻辑**：
分片正文与原文件对应定义逐字相同。跨分片共用的信封辅助在 `market_envelope`。）
"""

from __future__ import annotations

import asyncio
import logging
import math
from copy import deepcopy
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.deps import get_hub
from app.core.ttl_cache import cache_on
from app.core.inflight_reads import read_inflight
from app.schemas.envelope import (
    Envelope,
    OverviewPayload,
    ThemeBoardPayload,
)
from app.services.quote_hub import QuoteHub
from app.services.market_snapshot import (
    default_trade_date,
    load_snapshot_map,
)
from app.services.dragon_service import apply_position_with_5d
from app.services.theme_catalog_service import official_multi_day_changes

router = APIRouter(tags=["market"])
log = logging.getLogger("app.api.routes.market")

# Optional K-line enrichment must not hold the basic theme list behind slow batches.
THEME_KLINE_VIEW_BUDGET_SECONDS = 0.25
THEME_KLINE_SOURCE_BUDGET_SECONDS = 15.0

from app.api.routes.market_envelope import (
    meta_payload,
    dated_meta,
)


@router.get("/market/overview", response_model=Envelope[OverviewPayload])
async def market_overview(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    """指数行情 + 两市成交额合计。

    成交额口径（2026-09-01 修正，对标同花顺）：原实现把 indices 里 SH/SZ 全部
    指数的成交额求和——沪深300/中证1000/创业板指与上证指数/深证成指成分互相
    重叠，重复求和导致总额虚高 40%+。同花顺口径 = 沪市全市场 + 深市全市场，
    与全市场快照求和一致（compute_breadth.total_amount 同源），故改用快照。
    快照未就绪（冷启动数秒）时诚实返回 null，前端显示 --。

    三态披露（2026-09-14）：`total_amount is None` 其实有**两种**成因——
    ① 上游尚未就绪（冷启动 / 被 WAF 限流，会自动恢复）；② 真的没有数据。
    原先只给 `null`，前端一律渲染 `--`（= 缺失），把「未判定」塌缩成「没有」，
    用户无法分辨"正在加载"还是"坏了"——实际报障：「两市成交额怎么没出来了」。
    故一并返回 `total_amount_freshness`（直接复用 S2-1 契约，**不另造判据**）。"""
    indices = hub.get_indices()
    total_amount = None
    snap = getattr(request.app.state, "snapshot_service", None)
    if snap is not None and snap.snapshot:
        total_amount = round(sum(r.get("amount") or 0 for r in snap.snapshot), 2)
    return {
        "data": {
            "indices": [q.model_dump(mode="json") for q in indices],
            "total_amount": total_amount,
            "total_amount_freshness": (
                snap.freshness().model_dump(mode="json") if snap is not None else None
            ),
        },
        "meta": meta_payload(hub),
    }


async def _official_identities_cached(request: Request) -> dict:
    """Share a local primitive projection; canceled views do not abandon its thread."""
    from app.services.official_match import catalog_identity_index

    svc = request.app.state.theme_catalog
    revision = getattr(svc, "revision", 0)
    cache = cache_on(svc, "themes.official.identities", 60, maxsize=4)
    key = ("exact-catalog-v2", revision)
    hit, result = cache.get(key)
    if hit:
        return deepcopy(result)

    async def load():
        hit, result = cache.get(key)
        if hit:
            return result
        try:
            rows = await asyncio.to_thread(svc.get_catalog, limit=1000)
            identities = catalog_identity_index(rows)
            if getattr(svc, "revision", 0) != revision:
                raise RuntimeError("catalog_revision_changed")
            result = {"state": "available", "identities": identities, "reason": None}
        except Exception as exc:
            result = {"state": "unavailable", "identities": {}, "reason": type(exc).__name__}
        cache.set(key, result, ttl=60 if result["state"] == "available" else 5)
        return result

    return await read_inflight(svc, ("theme-official-identities", cache, key), load)


async def _official_bars_cached(request: Request, code: str, trade_date: date) -> dict:
    """One bounded provider-owned read, shared after UI waiters disconnect."""
    state = request.app.state
    svc = state.theme_catalog
    owner = getattr(getattr(state, "hub", None), "provider", svc)
    cache = cache_on(svc, "themes.official.bars", 60, maxsize=256)
    key = ("exact-catalog-v2", code, trade_date.isoformat(), getattr(svc, "revision", 0))
    hit, result = cache.get(key)
    if hit:
        return deepcopy(result)

    async def load():
        hit, result = cache.get(key)
        if hit:
            return result
        sem = getattr(svc, "_board_fetch_sem", None)
        if sem is None:
            sem = getattr(owner, "_theme_kline_sem", None)
            if sem is None:
                sem = owner._theme_kline_sem = asyncio.Semaphore(4)

        async def fetch():
            async with sem:
                return await svc.fetch_board_bars(code)

        try:
            # The deadline includes waiting for the shared source concurrency gate.
            raw = await asyncio.wait_for(fetch(), timeout=THEME_KLINE_SOURCE_BUDGET_SECONDS)
            if not isinstance(raw, list):
                raise ValueError("invalid bar list")
            bars = []
            seen = set()
            for row in raw:
                if not isinstance(row, dict) or not isinstance(row.get("date"), str):
                    raise ValueError("invalid bar identity")
                day = date.fromisoformat(row["date"])
                if day.isoformat() != row["date"] or row["date"] in seen:
                    raise ValueError("invalid or duplicate bar date")
                seen.add(row["date"])
                close = row.get("close")
                if isinstance(close, bool) or not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0:
                    raise ValueError("invalid bar close")
                if day <= trade_date:
                    bars.append(dict(row))
            bars.sort(key=lambda row: row["date"])
            result = {"state": "available" if bars else "unavailable", "bars": bars,
                      "as_of": bars[-1]["date"] if bars else None,
                      "reason": None if bars else "no_bars_at_or_before_requested_date"}
        except Exception as exc:  # source failure remains unknown, briefly shared
            result = {"state": "unavailable", "bars": [], "as_of": None,
                      "reason": type(exc).__name__}
        cache.set(key, result, ttl=60 if result["state"] == "available" else 5)
        return result

    return await read_inflight(owner, ("theme-official-bars", id(svc), key), load)


def _restore_unverified_board(board: dict) -> None:
    for n in (3, 5, 10):
        key = f"chg_{n}d"
        if f"{key}_inferred" in board:
            board[key] = board[f"{key}_inferred"]
    for key in ("multi_day_source", "multi_day_source_code", "multi_day_source_name",
                "multi_day_as_of", "multi_day_cutoff_date"):
        board.pop(key, None)
    board["multi_day_verified"] = False


async def _verify_board_multi_day(request: Request, board_payload: dict, trade_date: date) -> None:
    """T3/B3（architecture-design §1）：用同花顺官方板块 K 线交叉验证/替换
    板块 3/5/10 日涨跌幅（东财字段序推断值）。

    - 源原因名与目录正式名称精确相同 → 该目录代码 K 线 → 重算涨跌幅
    - 官方可得 → 覆盖 chg_3d/5d/10d + multi_day_verified=true（推断值保留在 *_inferred 供审计）
    - 不可用（无目录服务/题材不在目录/拉取失败）→ 保留推断值 + false，caveats 如实说明
    - 按代码、目标日期与目录版本缓存 K 线；非必需增强超过列表等待窗则明确 pending
    """
    deadline = asyncio.get_running_loop().time() + THEME_KLINE_VIEW_BUDGET_SECONDS
    svc = getattr(request.app.state, "theme_catalog", None)
    cards = board_payload.get("themes") or []
    if svc is None:
        for card in cards:
            board = card.get("board")
            if board:
                _restore_unverified_board(board)
                board.update(multi_day_catalog=None, multi_day_verification_state="catalog_unavailable",
                             multi_day_requested_trade_date=trade_date.isoformat())
        return
    catalog_waiter = asyncio.create_task(_official_identities_cached(request))
    catalog_done = set()
    try:
        catalog_done, _pending = await asyncio.wait(
            {catalog_waiter}, timeout=max(0, deadline - asyncio.get_running_loop().time()))
    finally:
        if not catalog_waiter.done():
            # Cancel only this request's waiter. The service read owner drains
            # the real worker and retains its result for the next base payload.
            catalog_waiter.cancel()
            await asyncio.gather(catalog_waiter, return_exceptions=True)
    result = None
    if catalog_done:
        try:
            result = catalog_waiter.result()
        except (Exception, asyncio.CancelledError) as exc:
            result = {"state": "unavailable", "reason": type(exc).__name__}
    if result is None or result["state"] != "available":
        for card in cards:
            board = card.get("board")
            if board:
                _restore_unverified_board(board)
                board.update(multi_day_catalog=None,
                             multi_day_verification_state="pending" if result is None else "catalog_unavailable",
                             multi_day_reason="catalog_read_pending" if result is None else result.get("reason"),
                             multi_day_requested_trade_date=trade_date.isoformat())
        return
    identities = result["identities"]
    targets: dict[str, list[tuple[dict, dict]]] = {}  # 目录代码 → (卡片, board dict) 列表
    for card in cards:
        board = card.get("board")
        if not board:
            continue
        board["multi_day_requested_trade_date"] = trade_date.isoformat()
        identity = identities.get(card.get("theme") or "") if not card.get("is_unclassified") else None
        board_source_mismatch = identity is not None and board.get("name") != identity["name"]
        if board_source_mismatch:
            identity = None  # a different provider board cannot inherit this code's interval facts
        # A previous fuzzy-name upgrade or a renamed catalog cannot stay verified.
        # Restore available inferred facts, without inventing missing prior values.
        if (identity is None or board.get("multi_day_catalog") != identity
                or board.get("multi_day_cutoff_date") != trade_date.isoformat()):
            _restore_unverified_board(board)
        if identity is None:
            board["multi_day_verified"] = False
            board["multi_day_catalog"] = None
            board["multi_day_verification_state"] = "board_source_mismatch" if board_source_mismatch else "unmapped"
            continue
        if (board.get("multi_day_verified") is True and board.get("multi_day_catalog") == identity
                and board.get("multi_day_source") == "ths_official_kline"
                and board.get("multi_day_cutoff_date") == trade_date.isoformat()):
            continue
        board["multi_day_catalog"] = dict(identity)
        board["multi_day_verification_state"] = "pending"
        board.pop("multi_day_reason", None)
        targets.setdefault(identity["code"], []).append((card, board))
    if not targets:
        return

    waiters = {asyncio.create_task(_official_bars_cached(request, code, trade_date)): code for code in targets}
    done = set()
    try:
        done, _pending = await asyncio.wait(
            waiters, timeout=max(0, deadline - asyncio.get_running_loop().time()))
    finally:
        # These are only request waiters. read_inflight shields the real provider
        # owners; their bounded source reads drain and populate the reusable cache.
        pending = [task for task in waiters if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    results = {}
    for task in done:
        try:
            results[waiters[task]] = task.result()
        except (Exception, asyncio.CancelledError) as exc:
            results[waiters[task]] = {"state": "unavailable", "bars": [], "reason": type(exc).__name__}

    verified = 0
    position_upgraded = 0
    for code, pairs in targets.items():
        result = results.get(code)
        if result is None:
            continue  # pending: the base list can return before optional IO completes
        bars = result.get("bars")
        if not bars:
            for _card, board in pairs:
                board["multi_day_verification_state"] = "source_unavailable"
                board["multi_day_reason"] = result.get("reason")
            continue
        official = official_multi_day_changes(bars)
        for card, board in pairs:
            for n in (3, 5, 10):
                key = f"chg_{n}d"
                if official.get(key) is not None:
                    board.setdefault(f"{key}_inferred", board.get(key))
                    board[key] = official[key]
            board["multi_day_verified"] = all(
                official.get(f"chg_{n}d") is not None for n in (3, 5, 10)
            )
            has_official = any(official.get(f"chg_{n}d") is not None for n in (3, 5, 10))
            board["multi_day_verification_state"] = "verified" if board["multi_day_verified"] else "partial"
            if has_official:
                board["multi_day_source"] = "ths_official_kline"
                board["multi_day_source_code"] = code
                board["multi_day_source_name"] = board["multi_day_catalog"]["name"]
                board["multi_day_as_of"] = bars[-1].get("date")
                board["multi_day_cutoff_date"] = trade_date.isoformat()
                board.pop("multi_day_reason", None)
            if board["multi_day_verified"]:
                verified += 1
            # P1-6：官方 5 日涨幅到位后，把持续性评估的位置判定从 active_days
            # 代理升级为真实区间涨幅（chg_5d 不可得时保持代理口径，不硬凑）。
            persistence = card.get("persistence")
            if official.get("chg_5d") is not None and persistence:
                apply_position_with_5d(persistence, official["chg_5d"])
                position_upgraded += 1

    if verified or position_upgraded:
        caveats = board_payload.setdefault("caveats", [])
        card_total = sum(len(v) for v in targets.values())
        for i, text in enumerate(caveats):
            if "board_multi_day_verified" in text:
                caveats[i] = (
                    f"精确同名目录的板块 3/5/10 日涨跌幅已用同花顺官方板块 K 线交叉验证"
                    f"（{verified}/{card_total} 张卡片），持续性评估位置判定同步升级"
                    f"（{position_upgraded} 张）；未命中的题材保留东财字段序推断"
                )
                break


def _attach_official_flags(request: Request, themes_list: list[dict]) -> None:
    """L5（architecture-design §1）+ 09-08 簇级官方概念挂靠：见 app/services/official_match.py。

    盘面题材看板与猎场机会视图共用精确目录身份；成分重叠另作相关导航。
    """
    from app.services.official_match import attach_official

    attach_official(request, themes_list)


@router.get("/themes", response_model=Envelope[ThemeBoardPayload])
async def themes(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，默认最近交易日"),
    min_boards: int = Query(default=0, ge=0, le=20, description="仅保留最高连板 ≥ 该值的题材"),
    min_count: int = Query(
        default=2, ge=1, le=50,
        description="仅保留涨停家数 ≥ 该值的题材；默认 2 以滤掉个股独立行情",
    ),
    sort: str = Query(default="strength", description="strength(综合强度) | boards(连板高度) | count(涨停家数)"),
    limit: int = Query(default=30, ge=1, le=100),
    request: Request = None,
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """题材梯队看板：涨停池按题材容器重组，输出连板天梯 + 强度指标 + 阶段判断。

    与 /api/limit-up 的区别：涨停池是平铺列表，本接口以题材为容器，
    给出「梯队是否成建制、资金是否持续」的结构化结论。结果缓存 60s。
    """
    if sort not in ("strength", "boards", "count"):
        raise HTTPException(status_code=400, detail="sort 仅支持 strength / boards / count")
    trade_date = date.fromisoformat(date_str) if date_str else await default_trade_date(hub)

    payload = await _theme_board_cached(request, hub, trade_date)

    # T3/B3：官方板块 K 线交叉验证（缓存的 payload 已验证过时内部直接跳过）
    await _verify_board_multi_day(request, payload["data"], trade_date)

    # 挂靠会写卡片与嵌套成员标记；worker 只修改本请求副本。
    themes_list = deepcopy(payload["data"]["themes"])
    await asyncio.to_thread(_attach_official_flags, request, themes_list)
    if min_boards > 0:
        themes_list = [t for t in themes_list if t["performance"]["max_boards"] >= min_boards]
    if min_count > 1:
        themes_list = [t for t in themes_list if t["performance"]["limit_up_count"] >= min_count]
    if sort == "boards":
        themes_list.sort(key=lambda t: (-t["performance"]["max_boards"], -t["strength_score"]))
    elif sort == "count":
        themes_list.sort(key=lambda t: (-t["performance"]["limit_up_count"], -t["strength_score"]))

    out = dict(payload["data"])
    out["themes"] = themes_list[:limit]
    out["filters"] = {"sort": sort, "min_boards": min_boards, "min_count": min_count, "limit": limit}
    return {"data": out, "meta": payload["meta"]}


async def _theme_board_cached(request: Request, hub: QuoteHub, trade_date: date) -> dict:
    """题材看板（60s 缓存，与 /api/market/themes 共用同一缓存槽）。

    抽出来的理由：介入条件清单（/entry-checklist）也要这份 payload，重复取数等于
    把同一份重计算做两遍。`build_theme_board` 抛 RuntimeError（涨停池不可用）→ 502。
    """
    from app.services.theme_service import build_theme_board

    cache = cache_on(request.app.state, "market.themes", 60, maxsize=16)
    # Catalog renames/retirements must invalidate derived identity and K-line claims.
    # The version also excludes payloads created by the former fuzzy-name policy.
    key = ("exact-catalog-v2", trade_date,
           getattr(getattr(request.app.state, "theme_catalog", None), "revision", 0))
    hit, payload = cache.get(key)
    if hit:
        return deepcopy(payload)

    async def load():
        # Keep the original 60s completed cache; coalesce the whole cold factory,
        # including failures, without changing snapshot or source-time facts.
        try:
            board = await build_theme_board(
                hub.provider, trade_date,
                snapshot_map=await asyncio.to_thread(load_snapshot_map, request.app.state.snapshot_service, trade_date),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"data": board, "meta": await dated_meta(hub, trade_date)}

    async def cached():
        _, payload = await cache.get_or_set(key, load)
        return payload

    return await read_inflight(hub.provider, ("theme-board", cache, key), cached)


async def _market_phase_cached(request: Request, hub: QuoteHub) -> str | None:
    """当前市场相位（走共享 60s 情绪槽，见 `get_cached_sentiment`）。

    判不出来（日历不可用/计算失败）→ None = 未判定，绝不猜一个相位。
    降级姿势是**本消费方**的业务决策（相位缺失只影响市场层维度，不阻断清单），
    所以异常在这里吞、不写缓存（TTLCache 契约），`/market/sentiment` 仍照常 503。
    """
    from app.services.market_context import get_cached_sentiment

    try:
        result = await get_cached_sentiment(request.app.state, hub)
    except Exception as exc:  # noqa: BLE001  相位缺失只影响市场层维度，不阻断清单
        log.warning("entry-checklist: market phase unavailable: %s", exc)
        return None
    return (result or {}).get("phase")


@router.get("/market/entry-checklist")
async def market_entry_checklist(
    symbol: str = Query(min_length=6, max_length=12, description="裸6位代码或带后缀"),
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，默认最近交易日"),
    request: Request = None,
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """介入条件清单（P1-13）：三层必须同时满足的信号 + 回避项 + 失效条件 + 时间窗口。

    回答「等确认往往已涨一轮，追进去又被套」——不给"明天买 X"，只给信号清单：
    市场层（环境允许不允许）→ 题材层（题材处在什么阶段）→ 个股层（封板质量够不够），
    三层都过才谈价位；同时给出「什么情况说明判断错了」的失效条件。

    输入**全部复用**已有计算：题材看板（60s 缓存，含 dragon_score/封单质量/题材阶段）
    + 情绪判定（60s 缓存，提供市场相位）。个股不在任何题材梯队时 found=False，
    只给市场层通用条件并显式标注「个股层未判定」，绝不臆造角色与封单质量。
    红线 3：输出是条件清单，不是买卖建议。
    """
    from app.services.dragon_service import entry_checklist
    from app.services.theme_service import entry_checklist_from_board

    td = date.fromisoformat(date_str) if date_str else await default_trade_date(hub)
    payload = await _theme_board_cached(request, hub, td)
    phase = await _market_phase_cached(request, hub)

    found = entry_checklist_from_board(payload["data"], symbol, market_phase=phase)
    if found is not None:
        data = {"found": True, **found, "market_phase": phase}
    else:
        data = entry_checklist(symbol=symbol, market_phase=phase)
        data.update({
            "found": False,
            "trade_date": td.isoformat(),
            "market_phase": phase,
            "note": (
                f"该标的 {td.isoformat()} 不在任何题材梯队（当日未涨停或未归入题材）"
                "→ 只给市场层通用条件，个股层的封单质量/角色/题材阶段均未判定。"
            ) + data["note"],
        })
    return {"data": data, "meta": meta_payload(hub)}


@router.get("/chip")
async def market_chip(
    symbol: str = Query(min_length=6, max_length=12, description="裸6位代码或带后缀"),
    full: bool = Query(default=False, description="回传全网格分布（研究用）"),
) -> dict:
    """筹码分布（CYQ 近似）：获利盘/集中度/主密集峰/支撑压力（方向2 数据基础）。

    口径：流通盘=窗口均量/1%假设换手（approx=True 恒标注）；形态稳健、
    获利盘绝对值仅供参考。marketdb 缺仓时 available=False 显式降级。
    """
    from app.market.chip import get_chip_service

    payload = get_chip_service().distribution(symbol, full=full)
    return {"data": payload, "meta": {}}
