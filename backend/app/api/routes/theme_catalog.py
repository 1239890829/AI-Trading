"""题材字典与官方成分 API（linkage-design §3.5 T1）。

- GET  /api/themes/catalog?search=&limit=      题材全集（官方目录；空库自动同步一次）
- GET  /api/themes/catalog/{code}/members      官方成分（过期/缺失时懒同步；?refresh=1 强制）
- POST /api/themes/sync                        触发同步（写鉴权）：目录 + 可选指定成分 + 过期补齐
- GET  /api/themes/reconciliation?date=        涨停归因 × 官方成分 三方校验报告
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.deps import get_hub, require_write_token
from app.core.ttl_cache import cache_on
from app.services.quote_hub import QuoteHub
from app.services.theme_catalog_service import (
    ThemeCatalogService,
    aggregate_theme_strength,
    official_multi_day_changes,
    reconcile,
)
from app.services.theme_service import parse_theme_tags

log = logging.getLogger(__name__)

# ops-only（2026-09-08 审查 P0-4 标注）：/themes/sync、/themes/reconciliation、
# /themes/catalog/index、/themes/catalog/{code}/members 前端零调用——它们是
# 目录运维/诊断端点（人工同步、成分对账、清单核查），保留不删；
# 其余端点均为前端活跃消费。
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
    不让它拖垮官方成分的展示（失败不缓存，下次请求重试）。
    注意：交易日盘中当日池随行情增长，盘前为空是正常语义。
    """
    from app.services.theme_service import _pick_provider

    hub = request.app.state.hub
    cache = cache_on(request.app.state, "themes.attribution", 60, maxsize=8)
    key = trade_date or "default"
    hit, cached = cache.get(key)
    if hit:
        amap: dict[str, list[str]] = cached[0]
        date_iso: str = cached[1]
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
        cache.set(key, (amap, date_iso))
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

    # 题材当日涨跌幅（2026-09-01）：官方板块指数口径（与概念目录同源，杜绝跨源
    # 名称匹配），附在 official 各项上——前端徽标展示、只保留最相关的少数题材
    # （用户反馈：29 个概念全量展示会把分时/K线挤下去）。
    try:
        changes = await svc.day_changes([o["theme_code"] for o in official])
        official = [{**o, "theme_chg_1d": changes.get(o["theme_code"])} for o in official]
    except Exception as exc:  # noqa: BLE001 涨跌幅是增强信息，失败不拖垮归属展示
        log.warning("stock themes: 板块日涨幅批量获取失败（chips 不带涨跌幅）: %s", exc)

    # 题材与今日整体涨跌行情的联动度（2026-09-01 用户反馈 #2）：方向一致家数占比
    # ——排序依据只用方向不依赖涨跌幅数值大小，「与今天行情最相关的题材排前列」。
    # 成分行情来自全市场快照（内存 O(1) 查），大盘方向取上证指数。
    try:
        from app.api.deps import get_hub
        from app.services.theme_catalog_service import direction_alignment

        hub = get_hub(request)
        snap = getattr(request.app.state, "snapshot_service", None)
        chg_by_symbol = {
            r["symbol"]: r.get("change_pct")
            for r in (snap.snapshot if snap is not None and snap.snapshot else [])
            if isinstance(r, dict)
        }
        sh_chg = next(
            (q.change_pct for q in hub.get_indices() if q.symbol == "000001" and q.change_pct is not None),
            None,
        )
        members_by = svc.member_symbols_bulk([o["theme_code"] for o in official])
        official = [
            {
                **o,
                "theme_align_1d": direction_alignment(
                    members_by.get(o["theme_code"], []), chg_by_symbol, sh_chg
                ),
            }
            for o in official
        ]
    except Exception as exc:  # noqa: BLE001 联动度是增强信息，失败不拖垮归属展示
        log.warning("stock themes: 方向联动度计算失败（chips 按涨跌幅排序兜底）: %s", exc)

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


@router.get("/themes/catalog/strength")
async def theme_strength(
    request: Request,
    codes: str = Query(default="", description="逗号分隔题材代码（88xxxx.TI）；缺省=全部有成分的题材"),
    hub: QuoteHub = Depends(get_hub),
    svc: ThemeCatalogService = Depends(get_service),
) -> dict:
    """题材内资金合力（P1-5）：批量快照成分股，按题材聚合涨跌家数/等权涨幅/成交额/涨停家数。

    归属口径 = 官方成分反查；行情 = 腾讯批量快照（与个股行情同源）。成分跨题材去重，
    一次快照多方复用；结果缓存 60s（合力是分钟级感知，不必秒级刷新）。
    """
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    theme_members: dict[str, list[str]] = {}
    for c in (code_list or [t.code for t in svc.get_catalog()]):
        members = [m.symbol for m in svc.get_members(c)]
        if members:
            theme_members[c] = members[:200]
    if not theme_members:
        return {"data": {"themes": {}, "note": "题材成分尚未同步"}, "meta": {}}

    cache = cache_on(request.app.state, "themes.catalog.strength", 60, maxsize=8)
    cache_key = ",".join(sorted(theme_members))[:512]
    hit, payload = cache.get(cache_key)
    if hit:
        return payload

    all_symbols = sorted({s for members in theme_members.values() for s in members})
    # 2026-09-07 R3 收口：分批实现在 quote_enrich.fetch_quotes_batched
    from app.services.quote_enrich import fetch_quotes_batched

    found = await fetch_quotes_batched(hub, all_symbols)
    quotes_raw: dict[str, dict] = {
        q.symbol: {
            "symbol": q.symbol,
            "name": q.name,
            "price": q.price,
            "change_pct": q.change_pct,
            "amount": q.amount,
        }
        for q in found.values()
    }

    strength = aggregate_theme_strength(theme_members, quotes_raw)
    names = {t.code: t.name for t in svc.get_catalog()}
    payload = {
        "data": {
            "themes": {
                code: {**s, "name": names.get(code, code)} for code, s in strength.items()
            }
        },
        "meta": {"basis": "合力=官方成分批量快照聚合（涨跌家数/等权涨幅/成交额合计），数据有延迟"},
    }
    cache.set(cache_key, payload)
    return payload


@router.get("/themes/catalog/index")
async def theme_index(
    request: Request,
    code: str = Query(..., description="题材代码（88xxxx.TI）"),
    days: int = Query(default=60, ge=10, le=250),
    svc: ThemeCatalogService = Depends(get_service),
) -> dict:
    """官方板块指数日 K（ths 发布的 88xxxx.TI 指数序列，非自算）+ 多日涨跌幅。"""
    cache = cache_on(request.app.state, "themes.catalog.index", 120, maxsize=64)
    key = f"{code}:{days}"
    hit, payload = cache.get(key)
    if hit:
        return payload
    try:
        bars = await svc.fetch_board_bars(code)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"板块指数数据源失败：{exc}")
    if not bars:
        raise HTTPException(status_code=404, detail=f"{code} 无板块指数数据")
    recent = bars[-days:]
    multi = official_multi_day_changes(bars)
    payload = {
        "data": {
            "code": code,
            "series": [{"date": b["date"], "close": b["close"]} for b in recent],
            "chg_3d": multi.get("chg_3d"),
            "chg_5d": multi.get("chg_5d"),
            "chg_10d": multi.get("chg_10d"),
            "basis": "同花顺官方板块指数日 K（ths 发布序列）",
        },
        "meta": {},
    }
    cache.set(key, payload)
    return payload


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


@router.get("/themes/catalog/{code}/detail")
async def theme_catalog_detail(
    code: str,
    request: Request,
    refresh: bool = Query(default=False),
    svc: ThemeCatalogService = Depends(get_service),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """概念详情（2026-09-08 用户需求，参考同花顺概念页设计）：

    - **全部成分**（默认）：官方成分**全量**（已保证与同花顺逐符号一致），带
      当日行情与涨停标注——补「只有涨停聚合簇、看不到概念全貌」的缺口。
    - **细分 tab**：当日涨停成员按 **ths 涨停原因官方标签**（parse_theme_tags
      原始标签，逐字不经改写）分组——同花顺官方归因口径。
    - 官方 API 无二级概念层级（四类 tag 已实测穷尽，见 09-08 调研），
      子概念成分待数据源支持后接入，此处不做关键词自创分组。
    """
    code = code.strip()
    if "/" in code or "\\" in code or ".." in code:
        raise HTTPException(status_code=400, detail=f"非法题材代码：{code!r}")
    if refresh or not svc.get_members(code):
        try:
            await svc.sync_members(code)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"成分同步失败：{exc}") from exc

    cache = cache_on(request.app.state, "themes.catalog.detail", 60, maxsize=16)
    hit, payload = cache.get(code)
    if hit:
        return payload

    rows = svc.get_members(code)
    theme = next((t for t in svc.get_catalog(limit=1000) if t.code == code), None)
    symbols = [r.symbol for r in rows]

    # 当日行情：全市场快照（内存实时，含换手率/流通市值——fetch_quotes_batched
    # 的 quote 对象没有这两个字段，用户 09-08 反馈弹窗缺「开板/换手/流通市值」）
    snap_by_symbol: dict[str, dict] = {}
    try:
        snap = getattr(request.app.state, "snapshot_service", None)
        for row in getattr(snap, "snapshot", None) or []:
            if row.get("symbol"):
                snap_by_symbol[row["symbol"]] = row
    except Exception as exc:  # noqa: BLE001  快照缺失 → 行情字段三态降级
        log.warning("concept detail snapshot unavailable %s: %s", code, exc)

    # 快照缺失（盘后重启清空/个别股不在快照）→ 两级兜底：
    # ① 当日 parquet 存档（与盘面题材看板同源，含换手/市值——盘后也有）；
    # ② 批量行情补涨幅/现价（quote 对象无换手/市值——那两字段只来自快照，
    #    缺失显式 --，不臆造）
    missing = [s for s in symbols if s not in snap_by_symbol]
    if missing:
        try:
            from app.api.routes.market import _default_trade_date_async, _load_snapshot_map

            td = await _default_trade_date_async(hub)
            parquet_rows = await asyncio.to_thread(
                _load_snapshot_map,
                request,
                td,
                ["symbol", "change_pct", "turnover_rate", "nmc", "price"],
            )
            for sym2, row2 in parquet_rows.items():
                if sym2 not in snap_by_symbol:
                    snap_by_symbol[sym2] = row2
        except Exception as exc:  # noqa: BLE001
            log.warning("concept detail parquet fallback failed %s: %s", code, exc)
    missing = [s for s in symbols if s not in snap_by_symbol]
    if missing:
        try:
            from app.services.quote_enrich import fetch_quotes_batched

            for q in (await fetch_quotes_batched(hub, missing)).values():
                snap_by_symbol.setdefault(q.symbol, {
                    "symbol": q.symbol, "price": q.price, "change_pct": q.change_pct,
                })
        except Exception as exc:  # noqa: BLE001
            log.warning("concept detail quote fallback failed %s: %s", code, exc)

    # 当日涨停归因（ths 官方 reason，逐字口径；rec 另带封单/首封时间/连板数）
    # + 东财增强（开板次数）。换手/流通市值以全市场快照为准（口径一致）。
    pool_by_symbol: dict[str, Any] = {}
    em_by_symbol: dict[str, Any] = {}
    try:
        from app.api.routes.market import _default_trade_date_async

        td = await _default_trade_date_async(hub)
        for rec in await hub.provider.get_limit_up_pool(td) or []:
            if rec.symbol:
                pool_by_symbol[rec.symbol] = rec
    except Exception as exc:  # noqa: BLE001  归因缺失 → 细分 tab 缺席（不臆造）
        log.warning("concept detail limit-up pool failed %s: %s", code, exc)
    try:
        from app.services.theme_service import _em_enhancement_map

        em_by_symbol = await _em_enhancement_map(hub.provider, await _default_trade_date_async(hub))
    except Exception as exc:  # noqa: BLE001  开板缺失 → 字段 None
        log.warning("concept detail em enhance failed %s: %s", code, exc)

    members: list[dict] = []
    tag_groups: dict[str, list[str]] = {}
    for r in rows:
        row = snap_by_symbol.get(r.symbol, {})
        ths_rec = pool_by_symbol.get(r.symbol)
        em = em_by_symbol.get(r.symbol)
        change_pct = row.get("change_pct")
        reason = (ths_rec.reason if ths_rec else "") or ""
        tags = parse_theme_tags(reason)
        nmc_wan = row.get("nmc")  # 流通市值（万元）
        seal_amount = getattr(ths_rec, "seal_amount", None) if ths_rec else None
        members.append({
            "symbol": r.symbol,
            "name": r.name,
            "change_pct": change_pct,
            "price": row.get("price"),
            "turnover_rate": row.get("turnover_rate"),
            "float_market_cap_yi": round(nmc_wan / 1e4, 2) if nmc_wan else None,  # 万元→亿
            "limit_up": bool(reason) or (change_pct or 0) >= 9.7,
            "break_count": (getattr(em, "break_count", None) if em else None),
            "seal_amount": seal_amount,  # 原样（元），前端 fmtAmount 自适应
            "boards": getattr(ths_rec, "consecutive_boards", None) if ths_rec else None,
            "reason": reason or None,
            "tags": tags,
        })
        # 细分分组：仅当日涨停成员（官方归因标签直通）
        if reason:
            for t in tags:
                tag_groups.setdefault(t, []).append(r.symbol)

    # 排序（09-08 用户反馈「涨停的靠前」）：涨停在前，组内涨幅降序；
    # 非涨停组按涨幅降序——看盘直觉顺序
    members.sort(key=lambda m: (not m["limit_up"], -(m["change_pct"] or -999)))

    payload = {
        "data": {
            "code": code,
            "name": theme.name if theme else code,
            "total": len(members),
            "limit_up_count": sum(1 for m in members if m["limit_up"]),
            "members": members,
            "tag_groups": [{"tag": t, "symbols": syms} for t, syms in
                           sorted(tag_groups.items(), key=lambda kv: -len(kv[1]))],
            "meta_note": "成分=同花顺官方目录（逐符号一致）；细分=当日涨停成员的 ths 涨停原因官方标签",
        },
        "meta": {},
    }
    cache.set(code, payload)
    return payload


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


@router.get("/themes/hot")
async def themes_hot(
    request: Request,
    limit: int = Query(default=30, ge=5, le=50, description="热股榜截取条数（官方榜共 30 只）"),
    svc: ThemeCatalogService = Depends(get_service),
) -> dict:
    """题材人气（B1 热股榜消费端）：ths 热股榜 × 官方成分反查 → 题材级人气聚合。

    - stocks：热股原榜（24 小时榜，含 rank/heat/rank_change），附官方归属题材名；
    - themes：按官方成分聚合（heat 合计 / 热股家数 / 榜内最高排名成员），按人气倒序；
    - 归属只用官方成分反查，不用关键词猜；无归属热股仅出现在 stocks（诚实口径）。
    人气为 ths 口径的估算数据，60s 缓存；ths 源不可用时 502，前端静默降级（看板不依赖它）。
    """
    from app.services.theme_catalog_service import aggregate_hot_themes
    from app.services.theme_service import _pick_provider

    cache = cache_on(request.app.state, "themes.hot", 60, maxsize=1)
    hit, payload = cache.get(limit)
    if hit:
        return payload

    hub = request.app.state.hub
    ths = _pick_provider(hub.provider, "ThsFuyaoProvider")
    if ths is None:
        raise HTTPException(status_code=503, detail="同花顺源不可用，无热股榜数据")
    try:
        stocks = await ths.get_hot_stock_list("day")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"热股榜数据源失败：{exc}") from exc

    stocks = stocks[:limit]
    official_by_symbol: dict[str, list[dict]] = {}
    for s in stocks:
        try:
            official_by_symbol[s["symbol"]] = svc.get_official_for_symbol(s["symbol"])
        except Exception:  # noqa: BLE001 — 归属查询失败按无归属处理，不让目录问题拖垮热股榜
            official_by_symbol[s["symbol"]] = []
    payload = {"data": aggregate_hot_themes(stocks, official_by_symbol), "meta": {}}
    cache.set(limit, payload)
    return payload


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
    if trade_date is None:
        # ths 端点要求具体日期（date_ms(None) 会崩），与其它路由一致先解析最近交易日
        from app.api.routes.market import _default_trade_date_async

        trade_date = await _default_trade_date_async(hub)
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
