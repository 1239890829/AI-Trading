"""事件驱动 API（linkage-design §4.4 E1+E2）。

- GET  /api/events?active=&limit=            活跃事件列表（时效=半衰期×2 实时计算）
- GET  /api/events/impact                    影响力视图：四级分类 + L1/L2/L3 分级（§六.4 拍板）
- GET  /api/events/{id}                      事件详情（含方向映射行）
- GET  /api/events/{id}/stocks               标的池：方向题材 → 官方成分反查 + override
- POST /api/events                           手动注册单条事件（写鉴权）
- POST /api/events/extract                   批量注册（items[]，写鉴权）
- POST /api/events/collect                   自选新闻批量抽取（写鉴权）
- POST /api/events/{id}/review               人工裁决 resolved/rejected（写鉴权）

红线：标的池只给「关联 + 依据 + 失效条件」，不构成买卖建议。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from pydantic import BaseModel, Field

from app.api.deps import require_write_token
from app.api.routes.theme_catalog import _normalize_symbol  # 同包复用：代码归一
from app.core.db import get_session_factory
from app.events.store import EventStore

log = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


def get_store(request: Request) -> EventStore:
    return request.app.state.event_store


def _theme_names(app_state) -> list[str]:
    """官方目录题材名（抽取实体用）；目录未同步时为空（方向行会缺，但不臆造）。"""
    svc = getattr(app_state, "theme_catalog", None)
    if svc is None or svc.catalog_size() == 0:
        return []
    return [t.name for t in svc.get_catalog(limit=1000)]


def _parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail=f"时间格式无法解析：{value!r}（ISO 或 YYYY-MM-DD）")


def _serialize(row, directions=None) -> dict:
    out = {
        "id": row.id,
        "title": row.title,
        "url": row.url,
        "source": row.source,
        "source_tier": row.source_tier,
        "published_at": row.published_at.isoformat(sep=" ") if row.published_at else None,
        "fact_kind": row.fact_kind,
        "certainty": row.certainty,
        "category": row.category,
        "half_life_hours": row.half_life_hours,
        "source_symbol": row.source_symbol,
        "status": row.status,
        "is_active": EventStore.is_active(row),
        "directions": [
            {
                "target_type": d.target_type,
                "target": d.target,
                "direction": d.direction,
                "strength": d.strength,
                "chain": d.chain,
                "basis": d.basis,
            }
            for d in (directions if directions is not None else row.directions)
        ],
    }
    return out


@router.get("/events")
async def list_events(
    active: bool = Query(default=True),
    limit: int = Query(default=30, ge=1, le=100),
    store: EventStore = Depends(get_store),
) -> dict:
    rows = store.list_events(active_only=active, limit=limit)
    return {"data": {"count": len(rows), "items": [_serialize(r) for r in rows]}, "meta": {}}


@router.get("/events/impact")
async def impact_events(
    include_l3: bool = Query(default=False, description="是否包含 L3（默认不上）"),
    limit: int = Query(default=100, ge=1, le=200),
    store: EventStore = Depends(get_store),
) -> dict:
    """事件影响力视图（§六.4 拍板）：四级分类（国际时事/国家政策/市场热点/原材料涨价）
    + 三级影响力（L1 必上 / L2 选上 / L3 不上）。派生自既有 EventCard，不重建抽取管道。"""
    from app.events.impact import FOUR_LABEL, classify_four, impact_level

    rows = store.list_events(active_only=True, limit=limit)
    items: list[dict] = []
    counts = {"L1": 0, "L2": 0, "L3": 0}
    four_counts: dict[str, int] = {}
    for r in rows:
        four = classify_four(r.title, r.category)
        level = impact_level(
            r.title, four=four, certainty=r.certainty, fact_kind=r.fact_kind,
            source_tier=r.source_tier, n_directions=len(r.directions or []),
        )
        counts[level] += 1
        four_counts[four] = four_counts.get(four, 0) + 1
        if level == "L3" and not include_l3:
            continue
        items.append({**_serialize(r), "four_category": four, "four_label": FOUR_LABEL[four],
                      "impact_level": level})
    return {"data": {"count": len(items), "counts_all": counts, "four_counts": four_counts,
                     "items": items}, "meta": {}}


@router.get("/events/{event_id}")
async def get_event(event_id: int, store: EventStore = Depends(get_store)) -> dict:
    row = store.get_event(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    return {"data": _serialize(row, store.directions_of(event_id)), "meta": {}}


@router.get("/events/{event_id}/stocks")
async def event_stocks(event_id: int, request: Request, store: EventStore = Depends(get_store)) -> dict:
    """标的池：方向题材 → 官方成分 + 人工 override（linkage-design §4.4 ④⑤）。

    成分为空的方向会懒同步一次官方成分；仍为空则如实返回空池
    （题材今日无关联标的 ≠ 数据错误）。
    """
    row = store.get_event(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    svc = getattr(request.app.state, "theme_catalog", None)
    pools = []
    if svc is not None:
        name_to_code = {t.name: t.code for t in svc.get_catalog(limit=1000)}
        for d in store.directions_of(event_id):
            if d.target_type != "theme":
                continue
            code = name_to_code.get(d.target)
            if not code:
                pools.append({"target": d.target, "direction": d.direction, "strength": d.strength,
                              "chain": d.chain, "basis": d.basis, "stocks": [],
                              "note": "题材不在官方目录，无法反查成分"})
                continue
            members = svc.get_members(code)
            if not members:
                try:
                    await svc.sync_members(code)
                    members = svc.get_members(code)
                except Exception as exc:  # noqa: BLE001
                    log.warning("event stocks: 成分懒同步失败 %s: %s", code, exc)
            symbols = {m.symbol: m.name for m in members}
            for ov in svc.get_overrides(code):
                if ov.action == "exclude":
                    symbols.pop(ov.symbol, None)
                elif ov.action == "include":
                    symbols.setdefault(ov.symbol, ov.symbol)
            pools.append({
                "target": d.target,
                "direction": d.direction,
                "strength": d.strength,
                "chain": d.chain,
                "basis": d.basis,
                "stocks": [{"symbol": s, "name": n} for s, n in sorted(symbols.items())],
            })
    else:
        pools.append({"note": "题材目录服务未初始化，无法反查成分"})
    return {
        "data": {"event": _serialize(row, store.directions_of(event_id)), "pools": pools},
        "meta": {"disclaimer": "标的池仅为事件关联成分，不构成买卖建议"},
    }


@router.get("/events/symbol/{symbol}")
async def events_for_symbol(
    symbol: str,
    request: Request,
    limit: int = Query(default=5, ge=1, le=20),
    store: EventStore = Depends(get_store),
) -> dict:
    """与个股相关的活跃事件（E2 残留：详情页事件标签的数据源）。

    命中两条路径之一：
    - 方向题材 ∈ 该股官方归属题材（linkage-design §3.2 L3 归属反查）
    - 事件抽取自该股的新闻（source_symbol）
    """
    sym = _normalize_symbol(symbol)
    svc = getattr(request.app.state, "theme_catalog", None)
    theme_names: set[str] = set()
    if svc is not None:
        theme_names = {m["theme_name"] for m in svc.get_official_for_symbol(sym)}

    matched = []
    for row in store.list_events(active_only=True, limit=50):
        dirs = store.directions_of(row.id)
        if row.source_symbol == sym:
            matched.append({"event": row, "directions": dirs, "match_reason": "source"})
        else:
            hit = [d for d in dirs if d.target_type == "theme" and d.target in theme_names]
            if hit:
                matched.append({"event": row, "directions": hit, "match_reason": "theme"})
        if len(matched) >= limit:
            break

    return {
        "data": {
            "symbol": sym,
            "themes": sorted(theme_names),
            "count": len(matched),
            "items": [
                {**_serialize(m["event"], m["directions"]), "match_reason": m["match_reason"]}
                for m in matched
            ],
        },
        "meta": {},
    }


class EventItemIn(BaseModel):
    title: str = Field(min_length=4, max_length=512)
    url: str | None = None
    source: str | None = None
    published_at: str | None = None
    source_symbol: str | None = None
    is_announcement: bool = False


class EventExtractIn(BaseModel):
    items: list[EventItemIn] = Field(min_length=1, max_length=50)


class EventReviewIn(BaseModel):
    status: str  # resolved / rejected / active


@router.post("/events", status_code=201, dependencies=[Depends(require_write_token)])
async def register_event(body: EventItemIn, request: Request, store: EventStore = Depends(get_store)) -> dict:
    row, created = store.register(
        body.title,
        source=body.source,
        url=body.url,
        published_at=_parse_published(body.published_at),
        source_symbol=body.source_symbol,
        is_announcement=body.is_announcement,
        theme_names=_theme_names(request.app.state),
    )
    return {"data": {**_serialize(row, store.directions_of(row.id)), "created": created}, "meta": {}}


@router.post("/events/extract", dependencies=[Depends(require_write_token)])
async def extract_events(body: EventExtractIn, request: Request, store: EventStore = Depends(get_store)) -> dict:
    theme_names = _theme_names(request.app.state)
    created = 0
    duplicated = 0
    for item in body.items:
        row, is_new = store.register(
            item.title,
            source=item.source,
            url=item.url,
            published_at=_parse_published(item.published_at),
            source_symbol=item.source_symbol,
            is_announcement=item.is_announcement,
            theme_names=theme_names,
        )
        created += 1 if is_new else 0
        duplicated += 0 if is_new else 1
    return {"data": {"created": created, "duplicated": duplicated, "received": len(body.items)}, "meta": {}}


async def collect_news_events(app_state, include_limit_up: bool = False) -> dict:
    """自选股新闻批量抽取：对每个自选标的拉最近新闻，抽取事件卡（指纹去重）。

    路由与定时调度共用这一份实现——事件采集此前只有 HTTP 端点、没有调度，
    活跃事件长期只有手工录入的几条，选股消息面近乎空转（2026-08-31 盘点结论）。

    include_limit_up（2026-09-01 校验规则 R6）：非盘中轮次把最近交易日涨停股
    纳入采集范围（按连板数 Top30，控上游配额）。此前范围只有 自选∪昨日组合∪持仓，
    86 只涨停仅 5 只有入库消息——涨停归因 × 消息面三方核实"无料可对"。
    """
    svc = getattr(app_state, "theme_catalog", None)
    if svc is not None and svc.catalog_size() == 0:
        try:
            await svc.sync_catalog()
        except Exception as exc:  # noqa: BLE001
            log.warning("events collect: 目录同步失败 %s", exc)
    # 成分保鲜调度（2026-09-01 校验规则）：成分 TTL 到期的题材每轮补 40 个——
    # 此前成分只在手工 POST /api/themes/sync 时补，且 sync_catalog 会把
    # theme.synced_at 刷新导致成分永远不判过期，390 题材 352 个成分空缺。
    # 30min × 40 个 ⇒ 每天至少全量轮一遍，题材—个股归属不再漂移。
    if svc is not None:
        try:
            stale = await svc.sync_stale_members(max_themes=40)
            if stale:
                log.info("events collect: 补齐 %d 个题材的官方成分", len(stale))
        except Exception as exc:  # noqa: BLE001
            log.warning("events collect: 成分补齐失败 %s", exc)
    theme_names = _theme_names(app_state)
    hub = app_state.hub
    repo = app_state.watchlist_repo
    store = app_state.event_store
    # 采集范围 = 自选（前 20）∪ 昨日精选组合 ∪ 真实持仓。
    # 只采自选的话，精选成员/持仓标的的新闻永远不入库 → 消息面评分对它们
    # 永远空转（2026-08-31 实测：事件 23 条但组合成员零命中）。
    symbols: list[str] = []
    for s in repo.list_symbols()[:20]:
        if s not in symbols:
            symbols.append(s)
    try:
        from app.models.daily_pick import DailyPickSet

        with get_session_factory()() as db:
            row = (
                db.execute(select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(1))
                .scalar_one_or_none()
            )
        if row:
            for item in json.loads(row.items):
                if item.get("symbol") and item["symbol"] not in symbols:
                    symbols.append(item["symbol"])
    except Exception as exc:  # noqa: BLE001
        log.warning("events collect: 昨日组合读取失败 %s", exc)
    try:
        from app.services.real_position_service import load_positions

        for pos in load_positions(get_session_factory()):
            if pos.symbol not in symbols:
                symbols.append(pos.symbol)
    except Exception as exc:  # noqa: BLE001
        log.warning("events collect: 持仓读取失败 %s", exc)
    if include_limit_up:
        try:
            from app.market import trade_calendar as tc

            days = await tc.trading_days(hub.provider)
            td = tc.last_trade_date(days)
            if td:
                pool = await hub.provider.get_limit_up_pool(td)
                pool = sorted(pool or [], key=lambda r: (r.consecutive_boards or 0), reverse=True)
                added = 0
                for r in pool:
                    if len(symbols) >= 60 or added >= 30:  # 涨停 Top30、总量有界
                        break
                    if r.symbol not in symbols:
                        symbols.append(r.symbol)
                        added += 1
                if added:
                    log.info("events collect: 盘后轮次纳入涨停股 %d 只（%s）", added, td)
        except Exception as exc:  # noqa: BLE001
            log.warning("events collect: 涨停股范围扩展失败 %s", exc)
    symbols = symbols[:60]  # 有界
    created = duplicated = fetched = 0
    for symbol in symbols:
        try:
            news = await hub.provider.get_news(symbol, 5)
        except Exception as exc:  # noqa: BLE001
            log.warning("events collect: %s 新闻拉取失败 %s", symbol, exc)
            continue
        for row in (news or [])[:5]:
            title = str(row.get("title") or "").strip()
            if len(title) < 8:
                continue
            fetched += 1
            try:
                published = _parse_published(row.get("date"))
            except HTTPException:
                published = None
            _, is_new = store.register(
                title,
                source=row.get("source") or "东财",
                url=row.get("url"),
                published_at=published,
                source_symbol=symbol,
                theme_names=theme_names,
            )
            created += 1 if is_new else 0
            duplicated += 0 if is_new else 1
    return {"symbols": len(symbols), "fetched": fetched, "created": created, "duplicated": duplicated}


@router.post("/events/collect", dependencies=[Depends(require_write_token)])
async def collect_events(request: Request, include_limit_up: bool = Query(default=False)) -> dict:
    """手动触发一轮采集（写鉴权）；定时调度直接调 collect_news_events，不走 HTTP。

    include_limit_up 调试/复盘用途：与调度器的非盘中轮次同路径，把最近交易日
    涨停股 Top30 纳入采集（R6 校验规则，2026-09-01）。
    """
    return {
        "data": await collect_news_events(request.app.state, include_limit_up=include_limit_up),
        "meta": {},
    }


@router.post("/events/{event_id}/review", dependencies=[Depends(require_write_token)])
async def review_event(event_id: int, body: EventReviewIn, store: EventStore = Depends(get_store)) -> dict:
    try:
        row = store.set_status(event_id, body.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    return {"data": _serialize(row, store.directions_of(event_id)), "meta": {}}
