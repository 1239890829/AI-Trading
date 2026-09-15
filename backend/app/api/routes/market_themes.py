"""题材与总览：市场总览、题材板（含官方标记与多日校验）、入场清单、筹码。

（自 `market.py` 切出，2026-09-15 IMP-005 批 3。**只搬位置，未改逻辑**：
分片正文与原文件对应定义逐字相同。跨分片共用的信封辅助在 `market_envelope`。）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.deps import get_hub
from app.core.ttl_cache import cache_on
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


async def _verify_board_multi_day(request: Request, board_payload: dict) -> None:
    """T3/B3（architecture-design §1）：用同花顺官方板块 K 线交叉验证/替换
    板块 3/5/10 日涨跌幅（东财字段序推断值）。

    - 题材名（ths 体系）直接映射官方概念目录 → 板块 K 线 → 重算涨跌幅
    - 官方可得 → 覆盖 chg_3d/5d/10d + multi_day_verified=true（推断值保留在 *_inferred 供审计）
    - 不可用（无目录服务/题材不在目录/拉取失败）→ 保留推断值 + false，caveats 如实说明
    - 缓存命中的 payload 已验证过则直接跳过，避免每请求重复拉 K 线
    """
    svc = getattr(request.app.state, "theme_catalog", None)
    if svc is None:
        return
    cards = board_payload.get("themes") or []
    if any(((c.get("board") or {}).get("multi_day_verified")) for c in cards):
        return

    name_to_code = {t.name: t.code for t in svc.get_catalog(limit=1000)}
    targets: dict[str, list[tuple[dict, dict]]] = {}  # 目录代码 → (卡片, board dict) 列表
    for card in cards:
        board = card.get("board")
        if not board:
            continue
        code = name_to_code.get(card.get("theme") or "") or name_to_code.get(board.get("name") or "")
        if code:
            targets.setdefault(code, []).append((card, board))
    if not targets:
        return

    sem = asyncio.Semaphore(4)
    bars_by_code: dict[str, list[dict]] = {}

    async def _fetch(code: str) -> None:
        async with sem:
            try:
                bars_by_code[code] = await svc.fetch_board_bars(code)
            except Exception as exc:  # noqa: BLE001 - 单板块失败保留推断值
                log.warning("board bars %s unavailable: %s", code, exc)

    await asyncio.gather(*(_fetch(c) for c in targets))

    verified = 0
    position_upgraded = 0
    for code, pairs in targets.items():
        bars = bars_by_code.get(code)
        if not bars:
            continue
        official = official_multi_day_changes(bars)
        for card, board in pairs:
            for n in (3, 5, 10):
                key = f"chg_{n}d"
                if official.get(key) is not None:
                    board[f"{key}_inferred"] = board.get(key)
                    board[key] = official[key]
            board["multi_day_verified"] = all(
                official.get(f"chg_{n}d") is not None for n in (3, 5, 10)
            )
            if board["multi_day_verified"]:
                board["multi_day_source"] = "ths_official_kline"
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
                    f"板块 3/5/10 日涨跌幅已用同花顺官方板块 K 线交叉验证"
                    f"（{verified}/{card_total} 张卡片），持续性评估位置判定同步升级"
                    f"（{position_upgraded} 张）；未命中的题材保留东财字段序推断"
                )
                break


def _attach_official_flags(request: Request, themes_list: list[dict]) -> None:
    """L5（architecture-design §1）+ 09-08 簇级官方概念挂靠：见 app/services/official_match.py。

    盘面题材看板与猎场机会视图共用同一挂靠实现（成分重叠反查，非簇名精确匹配）。
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
    await _verify_board_multi_day(request, payload["data"])

    themes_list = list(payload["data"]["themes"])
    _attach_official_flags(request, themes_list)
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
    hit, payload = cache.get(trade_date)
    if hit:
        return payload
    # 读 Parquet 是同步阻塞调用，必须丢到线程池，否则会卡住事件循环
    # （曾导致整个服务无响应，连 /api/health 都超时）。
    try:
        board = await build_theme_board(
            hub.provider,
            trade_date,
            snapshot_map=await asyncio.to_thread(load_snapshot_map, request.app.state.snapshot_service, trade_date),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    payload = {"data": board, "meta": await dated_meta(hub, trade_date)}
    cache.set(trade_date, payload)
    return payload


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
