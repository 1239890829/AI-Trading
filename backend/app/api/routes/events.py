"""事件驱动 API（linkage-design §4.4 E1+E2）。

- GET  /api/events?active=&limit=            活跃事件列表（时效=半衰期×2 实时计算）
- GET  /api/events/{id}                      事件详情（含方向映射行）
- GET  /api/events/{id}/stocks               标的池：方向题材 → 官方成分反查 + override
- POST /api/events                           手动注册单条事件（写鉴权）
- POST /api/events/extract                   批量注册（items[]，写鉴权）
- POST /api/events/collect                   自选新闻批量抽取（写鉴权）
- POST /api/events/{id}/review               人工裁决 resolved/rejected（写鉴权）

红线：标的池只给「关联 + 依据 + 失效条件」，不构成买卖建议。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import require_write_token
from app.api.routes.theme_catalog import _normalize_symbol  # 同包复用：代码归一
from app.events.store import EventStore

log = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


def get_store(request: Request) -> EventStore:
    return request.app.state.event_store


def _theme_names(request: Request) -> list[str]:
    """官方目录题材名（抽取实体用）；目录未同步时为空（方向行会缺，但不臆造）。"""
    svc = getattr(request.app.state, "theme_catalog", None)
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
        theme_names=_theme_names(request),
    )
    return {"data": {**_serialize(row, store.directions_of(row.id)), "created": created}, "meta": {}}


@router.post("/events/extract", dependencies=[Depends(require_write_token)])
async def extract_events(body: EventExtractIn, request: Request, store: EventStore = Depends(get_store)) -> dict:
    theme_names = _theme_names(request)
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


@router.post("/events/collect", dependencies=[Depends(require_write_token)])
async def collect_events(request: Request, store: EventStore = Depends(get_store)) -> dict:
    """自选股新闻批量抽取：对每个自选标的拉最近新闻，抽取事件卡（去重）。"""
    svc = getattr(request.app.state, "theme_catalog", None)
    if svc is not None and svc.catalog_size() == 0:
        try:
            await svc.sync_catalog()
        except Exception as exc:  # noqa: BLE001
            log.warning("events collect: 目录同步失败 %s", exc)
    theme_names = _theme_names(request)
    hub = request.app.state.hub
    repo = request.app.state.watchlist_repo
    symbols = repo.list_symbols()[:20]  # 有界：自选过大时截断
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
    return {"data": {"symbols": len(symbols), "fetched": fetched, "created": created, "duplicated": duplicated},
            "meta": {}}


@router.post("/events/{event_id}/review", dependencies=[Depends(require_write_token)])
async def review_event(event_id: int, body: EventReviewIn, store: EventStore = Depends(get_store)) -> dict:
    try:
        row = store.set_status(event_id, body.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    return {"data": _serialize(row, store.directions_of(event_id)), "meta": {}}
