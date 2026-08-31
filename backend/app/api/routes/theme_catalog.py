"""题材字典与官方成分 API（linkage-design §3.5 T1）。

- GET  /api/themes/catalog?search=&limit=      题材全集（官方目录；空库自动同步一次）
- GET  /api/themes/catalog/{code}/members      官方成分（过期/缺失时懒同步；?refresh=1 强制）
- POST /api/themes/sync                        触发同步（写鉴权）：目录 + 可选指定成分 + 过期补齐
- GET  /api/themes/reconciliation?date=        涨停归因 × 官方成分 三方校验报告
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.deps import require_write_token
from app.services.theme_catalog_service import ThemeCatalogService, reconcile
from app.services.theme_service import parse_theme_tags

log = logging.getLogger(__name__)

router = APIRouter(tags=["theme-catalog"])


def get_service(request: Request) -> ThemeCatalogService:
    svc: ThemeCatalogService | None = getattr(request.app.state, "theme_catalog", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="题材目录服务未初始化")
    return svc


def _normalize_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().replace(".SH", "").replace(".SZ", "")
    if not sym.isdigit() or len(sym) != 6:
        raise HTTPException(status_code=400, detail=f"非法代码：{symbol!r}")
    return sym


async def _attribution_for_symbol(request: Request, symbol: str, trade_date: date | None = None) -> list[dict]:
    """涨停归因（ths reason 串，行为性归属 L2 层），按日期微缓存 60s。

    整个涨停池一次拉取按 symbol 建倒排，多次个股查询共享。
    ths 源不可用（链上无该 provider / 拉取失败）时返回空——归因是 best-effort，
    不让它拖垮官方成分的展示。注意：交易日盘中当日池随行情增长，盘前为空是正常语义。
    """
    import time as _time

    from app.services.theme_service import _pick_provider

    hub = request.app.state.hub
    cache_key = f"_stock_attribution_cache_{trade_date or 'default'}"
    cache = getattr(request.app.state, cache_key, None)
    now = _time.time()
    if cache and now - cache[0] < 60:
        amap: dict[str, list[str]] = cache[1]
        date_iso: str = cache[2]
    else:
        ths = _pick_provider(hub.provider, "ThsFuyaoProvider")
        if ths is None:
            return []
        try:
            if trade_date is None:
                from app.api.routes.market import _default_trade_date_async

                trade_date = await _default_trade_date_async(hub)
            pool = await ths.get_limit_up_pool(trade_date)
        except Exception as exc:  # noqa: BLE001
            log.warning("stock attribution: 涨停池拉取失败（跳过归因展示）: %s", exc)
            return []
        amap = {}
        for row in pool:
            tags = parse_theme_tags(getattr(row, "reason", None))
            if tags:
                amap[row.symbol] = tags
        date_iso = trade_date.isoformat()
        request.app.state.__setattr__(cache_key, (now, amap, date_iso))
    return [{"theme_name": t, "date": date_iso} for t in amap.get(symbol, [])]


@router.get("/themes/stock/{symbol}")
async def stock_themes(
    symbol: str,
    request: Request,
    date_str: str | None = Query(default=None, alias="date", description="YYYYMMDD，默认最近交易日"),
    svc: ThemeCatalogService = Depends(get_service),
) -> dict:
    """个股题材反查（linkage-design §3.2）：官方成分（L3）+ 涨停归因（L2）。

    首次访问时目录为空会自动同步一次。归因为 best-effort：ths 源不可用时
    官方成分照常返回；交易日盘前当日池为空属正常语义（?date= 可回看）。
    """
    sym = _normalize_symbol(symbol)
    trade_date: date | None = None
    if date_str:
        try:
            trade_date = datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"日期格式应为 YYYYMMDD：{date_str!r}") from exc
    if svc.catalog_size() == 0:
        try:
            await svc.sync_catalog()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"题材目录同步失败：{exc}") from exc

    official = svc.get_official_for_symbol(sym)
    attribution = await _attribution_for_symbol(request, sym, trade_date)

    # 有界懒同步：官方归属为空但当日有归因时，只补齐归因题材的成分再反查一次
    # （归因给了精确的题材名，避免为"查无归属"全量同步 390 个题材）。
    if not official and attribution:
        names = {t.name: t.code for t in svc.get_catalog(limit=1000)}
        codes = list({names[a["theme_name"]] for a in attribution if a["theme_name"] in names})
        if codes:
            with svc._sf() as db:  # noqa: SLF001 - 同包内复用会话工厂
                from sqlalchemy import select as _select

                from app.models.theme_catalog import ThemeMember as _TM

                synced = set(
                    db.execute(
                        _select(_TM.theme_code).where(_TM.theme_code.in_(codes))
                    ).scalars()
                )
            for code in codes:
                if code not in synced:
                    try:
                        await svc.sync_members(code)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("stock themes: 成分懒同步失败 %s: %s", code, exc)
            official = svc.get_official_for_symbol(sym)

    return {
        "data": {
            "symbol": sym,
            "official": official,
            "attribution": attribution,
        },
        "meta": {},
    }


@router.get("/themes/catalog")
async def theme_catalog(
    search: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=500, ge=1, le=1000),
    svc: ThemeCatalogService = Depends(get_service),
) -> dict:
    """题材全集（官方目录）。空库时自动同步一次（首次访问建库）。"""
    if svc.catalog_size() == 0:
        try:
            await svc.sync_catalog()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"题材目录同步失败：{exc}") from exc
    rows = svc.get_catalog(search=search or None, limit=limit)
    return {
        "data": {
            "count": len(rows),
            "total": svc.catalog_size(),
            "items": [{"code": r.code, "name": r.name, "source": r.source, "synced_at": r.synced_at.isoformat()} for r in rows],
        },
        "meta": {},
    }


@router.get("/themes/catalog/{code}/members")
async def theme_members(
    code: str,
    refresh: bool = Query(default=False),
    svc: ThemeCatalogService = Depends(get_service),
) -> dict:
    """官方成分快照。缺失或超过 TTL 时懒同步；refresh=1 强制重拉。"""
    code = code.strip()
    if "/" in code or "\\" in code or ".." in code:
        raise HTTPException(status_code=400, detail=f"非法题材代码：{code!r}")
    if refresh or not svc.get_members(code):
        try:
            await svc.sync_members(code)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"成分同步失败：{exc}") from exc
    rows = svc.get_members(code)
    return {
        "data": {
            "theme_code": code,
            "count": len(rows),
            "items": [{"symbol": r.symbol, "name": r.name, "source": r.attribution_source} for r in rows],
        },
        "meta": {},
    }


class ThemeSyncIn(BaseModel):
    """同步请求：不传 code 只同步目录；传 code 额外同步该题材成分；max_stale 补齐过期成分。"""

    code: str | None = None
    max_stale: int = Query(default=0, ge=0, le=100)


@router.post("/themes/sync", dependencies=[Depends(require_write_token)])
async def theme_sync(body: ThemeSyncIn, svc: ThemeCatalogService = Depends(get_service)) -> dict:
    try:
        catalog_count = await svc.sync_catalog()
        synced: dict[str, int] = {}
        if body.code:
            code = body.code.strip()
            synced[code] = await svc.sync_members(code)
        stale = await svc.sync_stale_members(max_themes=body.max_stale) if body.max_stale else []
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"同步失败：{exc}") from exc
    return {"data": {"catalog": catalog_count, "members": synced, "stale_synced": stale}, "meta": {}}


@router.get("/themes/reconciliation")
async def theme_reconciliation(
    request: Request,
    date_str: str | None = Query(default=None, alias="date", description="YYYYMMDD，默认最近交易日"),
    max_themes: int = Query(default=30, ge=1, le=100),
    svc: ThemeCatalogService = Depends(get_service),
) -> dict:
    """涨停归因 × 官方成分校验：归因冲突（有归因但非官方成分）与目录外题材。"""
    if svc.catalog_size() == 0:
        await svc.sync_catalog()

    # 涨停池归因（ths 官方 reason 串）——复用 theme_service 的取数定位方式
    hub = request.app.state.hub
    from app.services.theme_service import _pick_provider

    ths = _pick_provider(hub.provider, "ThsFuyaoProvider")
    if ths is None:
        raise HTTPException(status_code=503, detail="同花顺源不可用，无法取涨停归因")

    trade_date: date | None = None
    if date_str:
        try:
            trade_date = datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"日期格式应为 YYYYMMDD：{date_str!r}") from exc
    try:
        pool = await ths.get_limit_up_pool(trade_date)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"涨停池拉取失败：{exc}") from exc

    attributions = []
    claimed_themes: set[str] = set()
    for row in pool:
        tags = parse_theme_tags(getattr(row, "reason", None))
        if tags:
            attributions.append((row.symbol, getattr(row, "name", None) or "", tags))
            claimed_themes.update(tags)

    # 官方成分：按题材**名称**索引（归因串里是名称不是代码）；懒同步缺失部分
    members_by_theme: dict[str, set[str]] = {}
    theme_names: dict[str, str] = {t.name: t.code for t in svc.get_catalog(limit=1000)}
    code_by_name = theme_names
    for theme_name in claimed_themes:
        code = code_by_name.get(theme_name)
        if code is None:
            continue
        rows = svc.get_members(code)
        if not rows:
            try:
                await svc.sync_members(code)
                rows = svc.get_members(code)
            except Exception as exc:  # noqa: BLE001
                log.warning("reconciliation: 成分同步失败 %s: %s", code, exc)
        members_by_theme[theme_name] = {r.symbol for r in rows}

    report = reconcile(attributions, members_by_theme, theme_names)
    report["pool_size"] = len(pool)
    report["attributed"] = len(attributions)
    return {"data": report, "meta": {}}
