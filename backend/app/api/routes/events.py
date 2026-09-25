"""事件驱动 API（architecture-design §1 E1+E2）。

- GET  /api/events?active=&limit=            活跃事件列表（时效=半衰期×2 实时计算）
- GET  /api/events/impact                    影响力视图：四级分类 + L1/L2/L3 分级（§六.4 拍板）
- GET  /api/events/{id}                      事件详情（含方向映射行）
- GET  /api/events/flash-coverage           快讯各频道持久水位与断档状态
- GET  /api/events/{id}/stocks               标的池：方向题材 → 官方成分反查 + override
- GET  /api/events/symbol/{symbol}           个股相关活跃事件（详情页事件标签）
- POST /api/events                           手动注册单条事件（写鉴权）
- POST /api/events/{id}/review-revision      精确观察版本的人工修订复核
- POST /api/events/{id}/review-symbol-direction  多标的来源关联的逐股人工审定
- POST /api/events/{id}/link-withdrawal      人工确认新来源 ID 撤回旧观察

已删（2026-09-08 审查 P0-4，零消费方）：/events/extract、/events/collect（调度器直调
collect_news_events 函数）、旧版 /events/{id}/review。
红线：标的池只给「关联 + 依据 + 失效条件」，不构成买卖建议。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from pydantic import BaseModel, Field

from app.api.deps import normalize_symbol, require_write_token
from app.core.db import get_session_factory
from app.events.store import EventStore
from app.core.bjtime import beijing_now, beijing_now_naive  # S2-8 时区收敛

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


def _parse_dt(value: str | None) -> datetime | None:
    """宽松回解析（排序用）：解析失败 → None 降级，不抛 400（与入参校验不同职责）。"""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _judge_fields(row, directions=None) -> dict:
    """判定状态字段（单点收口：所有事件端点共用）。失败退化为 unknown，不臆造。"""
    from app.events.extract import JUDGE_STATUS_LABEL, judge_state

    try:
        pub = getattr(row, "published_at", None)
        selected = directions if directions is not None else (row.directions or [])
        dirs = [{"direction": d.direction, "chain": getattr(d, "chain", "")}
                for d in selected]
        st = judge_state(pub, dirs, half_life_hours=getattr(row, "half_life_hours", None))
        if st["status"] == "judged" and any(
            d.direction != 0 and getattr(d, "matched_by", None) == "manual" for d in selected
        ):
            # The interpretation version has the actual review time. Publication
            # time is not when a human adjudicated this symbol.
            st["judged_at"] = None
            st["reason"] = "含人工逐股审定；方向依据与审定时间见解释版本"
        return {
            "judge_status": st["status"],
            "judge_status_label": JUDGE_STATUS_LABEL.get(st["status"], st["status"]),
            "judged_at": st["judged_at"].isoformat(sep=" ") if st["judged_at"] else None,
            "judge_reason": st.get("reason") or None,
        }
    except Exception:  # noqa: BLE001 —— 判定是增强字段，失败不得拖垮事件列表
        return {"judge_status": "unknown", "judge_status_label": "未判定",
                "judged_at": None, "judge_reason": "判定状态计算失败"}


def _serialize(row, directions=None, *, now: datetime | None = None) -> dict:
    revision_pending_at = getattr(row, "revision_pending_at", None)
    now = now or beijing_now_naive()
    evidence_visible = EventStore.evidence_visible(row, now=now)
    selected = (directions if directions is not None else row.directions) if evidence_visible else []
    if not evidence_visible:
        judgement = {"judge_status": "unknown", "judge_status_label": "证据尚未可见",
                     "judged_at": None, "judge_reason": "发布时间或解释可见时点尚未到达；原版本仍可审计"}
    elif revision_pending_at is not None:
        judgement = {"judge_status": "unknown", "judge_status_label": "来源修订待复核",
                     "judged_at": None, "judge_reason": "新观察与当前解释不一致，机会判断已暂停"}
    else:
        judgement = _judge_fields(row, selected)
    out = {
        "id": row.id,
        "title": row.title,
        "url": row.url,
        "summary": row.summary,
        "source": row.source,
        "source_tier": row.source_tier,
        "published_at": row.published_at.isoformat(sep=" ") if row.published_at else None,
        "fact_kind": row.fact_kind,
        "certainty": row.certainty,
        "category": row.category,
        "half_life_hours": row.half_life_hours,
        "source_symbol": row.source_symbol,
        "status": row.status,
        "is_active": EventStore.is_active(row, now=now),
        "revision_pending_at": revision_pending_at.isoformat(sep=" ") if revision_pending_at else None,
        "interpretation_ref": getattr(row, "interpretation_ref", None) or {
            "event_id": row.id, "version_id": None, "observation_id": None,
            "available_at": None, "state": "unknown",
        },
        # 判定结果（2026-09-09 需求 2）：利好/利空/中性由 directions 承载，
        # 这里补「判定时间 + 判定状态」——状态是读时派生（judge_state 纯函数），
        # 不落库免迁移；待判超时自动收敛中性，避免事件长期挂在「待判」。
        **judgement,
        "directions": [
            {
                "target_type": d.target_type,
                "target": d.target,
                "direction": d.direction,
                "strength": d.strength,
                "chain": d.chain,
                "basis": d.basis,
                "matched_by": d.matched_by,
                "observation_id": getattr(d, "observation_id", None),
            }
            for d in selected
        ],
    }
    return out


def _serialize_interpretation(version) -> dict:
    return {
        "id": version.id, "event_id": version.event_id,
        "observation_id": version.observation_id,
        "effective_at": version.effective_at.isoformat(sep=" "),
        "state": version.state, "review_note": version.review_note,
        "payload": json.loads(version.payload_json),
    }


def _serialize_withdrawal_link(link, notice) -> dict:
    return {
        "id": link.id, "target_observation_id": link.target_observation_id,
        "notice_observation_id": notice.id, "notice_event_id": notice.event_id,
        "notice_source": notice.source, "notice_source_item_id": notice.source_item_id,
        "notice_title": notice.title, "notice_url": notice.url,
        "notice_available_at": notice.available_at.isoformat(sep=" "),
        "prior_interpretation_id": link.prior_interpretation_id,
        "withdrawn_interpretation_id": link.withdrawn_interpretation_id,
        "note": link.note, "linked_at": link.linked_at.isoformat(sep=" "),
    }


class ReviewedDirectionIn(BaseModel):
    target_type: Literal["theme", "symbol", "macro"]
    target: str = Field(min_length=1, max_length=64)
    direction: Literal[-1, 0, 1]
    strength: int = Field(ge=1, le=5)
    chain: str = Field(max_length=256)
    basis: str = Field(min_length=1, max_length=256)
    matched_by: Literal["manual"] = "manual"


class ReviewedInterpretationIn(BaseModel):
    source_tier: int = Field(ge=1, le=5)
    category: Literal["policy", "statement", "data", "rumor", "corporate", "other"]
    fact_kind: Literal["fact", "opinion", "rumor"]
    certainty: Literal["done", "proposed", "rumor"]
    half_life_hours: int = Field(ge=1, le=720)
    directions: list[ReviewedDirectionIn]


class RevisionReviewIn(BaseModel):
    expected_observation_id: int = Field(gt=0)
    action: Literal["adopt", "retain", "withdraw"]
    note: str = Field(min_length=1, max_length=2048)
    interpretation: ReviewedInterpretationIn | None = None


class WithdrawalLinkIn(BaseModel):
    target_observation_id: int = Field(gt=0)
    notice_observation_id: int = Field(gt=0)
    expected_interpretation_id: int = Field(gt=0)
    note: str = Field(min_length=1, max_length=2048)


class SymbolDirectionReviewIn(BaseModel):
    expected_observation_id: int = Field(gt=0)
    expected_interpretation_id: int = Field(gt=0)
    symbol: str = Field(pattern=r"^\d{6}$")
    direction: Literal[-1, 1]
    strength: int = Field(ge=1, le=5)
    chain: str = Field(min_length=1, max_length=256)
    basis: str = Field(min_length=1, max_length=256)
    note: str = Field(min_length=1, max_length=2048)


@router.get("/events")
async def list_events(
    active: bool = Query(default=True),
    limit: int = Query(default=30, ge=1, le=100),
    store: EventStore = Depends(get_store),
) -> dict:
    # 同步 SQLite 读搬线程池（2026-09-12）：实测 limit=30 约 7.9ms、limit=100 约 11ms，
    # 本端点被前端 30s 轮询，排在事件循环上会与 QuoteHub 秒级推送抢同一个循环。
    # `EventStore.list_events` 内部 per-call 建 session（`with self._sf()`）⇒ 不跨线程复用，
    # 符合「同步 IO 搬线程」的前提（[[KB-ENG-67]]）。
    rows = await asyncio.to_thread(store.list_events, active_only=active, limit=limit)
    return {"data": {"count": len(rows), "items": [_serialize(r) for r in rows]}, "meta": {}}


@router.get("/events/flash-coverage")
async def flash_coverage() -> dict:
    from app.news.flash import configured_columns
    from app.news.flash_state import FlashCheckpointStore

    channels = configured_columns()
    rows = await asyncio.to_thread(FlashCheckpointStore().snapshot, channels)
    return {"data": {"items": rows},
            "meta": {"note": "baseline_at 之前的历史未核全；gap_at 非空表示覆盖尚未闭环"}}


@router.get("/events/verify")
async def verify_events(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    store: EventStore = Depends(get_store),
) -> dict:
    """热点验证环（P1-6）：当日活跃事件的发酵四态（实时计算，不落库）。

    采样关联题材的「消息可见后新涨停」（封板时间 ≥ 来源发布与解释可见的较晚时点）+ 板块主力资金（f62），
    判定 `confirmed`（新涨停且净流入）/ `fermenting`（单一信号）/ `faded`
    （无新涨停且净流出或净额 0）/ `unknown`（窗口未到 <30min 或缺数据）。
    验证是时点快照，故实时现算、不落库（多次调用 = 多次采样，天然支持
    30/60min/收盘三次采样口径）。
    """
    from app.events.verify import verify_active_events
    from app.market import trade_calendar as tc

    hub = request.app.state.hub
    try:
        days = await tc.trading_days(hub.provider)
        td = tc.last_trade_date(days)
        if td is None:
            return {"data": {"trade_date": None, "count": 0, "items": [], "note": "交易日历不可用"}, "meta": {}}
        pool = await hub.provider.get_limit_up_pool(td)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"涨停池取数失败：{exc}") from exc

    pool_dicts = [r.model_dump() for r in (pool or [])]
    items = await verify_active_events(store, hub, pool_dicts, td, limit=limit)
    return {
        "data": {"trade_date": td.isoformat(), "count": len(items), "items": items},
        "meta": {"basis": "发酵四态：confirmed/fermenting/faded/unknown；新涨停=封板≥来源发布与解释可见较晚时点，资金=东财 f62；仅为同题时序，不证明事件因果"},
    }


@router.get("/events/impact")
async def impact_events(
    include_l3: bool = Query(default=False, description="是否包含 L3（默认不上）"),
    limit: int = Query(default=100, ge=1, le=200),
    sort: str = Query(default="relevance", description="relevance(盘面相关性) | time(最新) | impact(影响力)"),
    request: Request = None,
    store: EventStore = Depends(get_store),
) -> dict:
    """事件影响力视图（§六.4 拍板）：四级分类（国际时事/国家政策/市场热点/原材料涨价）
    + 三级影响力（L1 必上 / L2 选上 / L3 不上）。派生自既有 EventCard，不重建抽取管道。

    sort=relevance（默认，2026-09-04）：与当日盘面/情绪强关联的事件排前，
    每条附 rank_score/rank_reasons/rank_factors——排序依据可解释、可追溯。
    上下文不可用 → 显式降级为影响力+时效排序（reasons 注明），绝不冒充共振。
    """
    from app.events.impact import FOUR_LABEL, classify_four, derive_tags, impact_level

    if sort not in ("relevance", "time", "impact"):
        raise HTTPException(status_code=400, detail=f"sort 只支持 relevance/time/impact，收到 {sort!r}")

    # 同步 SQLite 读搬线程池（2026-09-12）：limit=100 实测约 11ms，前端 30s 轮询本端点。
    rows = await asyncio.to_thread(store.list_events, active_only=True, limit=limit)
    # naive 北京墙钟：与 _parse_dt 产出的 naive published_at 同语义相减（age_h 衰减），
    # 且与服务器本地时区解耦（P2-2 时区统一）
    now = beijing_now().replace(tzinfo=None)
    enriched: list[dict] = []
    counts = {"L1": 0, "L2": 0, "L3": 0}
    four_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
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
        tags = derive_tags(r.title, r.category)
        for t in tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1
        enriched.append({**_serialize(r), "four_category": four, "four_label": FOUR_LABEL[four],
                         "impact_level": level, "tags": tags})

    if sort == "relevance" and enriched:
        from app.events.ranking import collect_rank_context, score_event

        svc = getattr(request.app.state, "theme_catalog", None) if request else None
        theme_names = sorted({
            d["target"] for e in enriched for d in e["directions"] if d["target_type"] == "theme"
        })
        symbols = [d["target"] for e in enriched for d in e["directions"] if d["target_type"] == "symbol"]
        name_to_code = {}
        if svc is not None:
            try:
                name_to_code = {t.name: t.code for t in svc.get_catalog(limit=1000)}
            except Exception:  # noqa: BLE001
                name_to_code = {}
        ctx = await collect_rank_context(
            request.app.state, [n for n in theme_names if n in name_to_code], symbols,
        )
        for e in enriched:
            theme_dirs = [d["target"] for d in e["directions"] if d["target_type"] == "theme"]
            symbol_vals = [ctx.stock_chg.get(d["target"]) for d in e["directions"] if d["target_type"] == "symbol"]
            rank = score_event(
                impact_level=e["impact_level"],
                four=e["four_category"],
                source_tier=e["source_tier"],
                published_at=_parse_dt(e["published_at"]),
                half_life_hours=e["half_life_hours"],
                theme_names=theme_dirs,
                symbol_chg=symbol_vals,
                ctx=ctx,
                now=now,
            )
            e["rank_score"] = rank["score"]
            e["rank_reasons"] = rank["reasons"]
            e["rank_factors"] = rank["factors"]
        enriched.sort(key=lambda e: (-e["rank_score"], e["published_at"] or ""), )
    elif sort == "impact":
        tier_rank = {"L1": 0, "L2": 1, "L3": 2}
        enriched.sort(key=lambda e: (tier_rank.get(e["impact_level"], 3),
                                     -(e["source_tier"] or 0), e["published_at"] or ""))
    # sort == "time"：保持 store 的 published_at desc 原序

    # P1-1（2026-09-09）：题材辨识度记忆效应（KB-STOCK-25）——给事件方向题材附
    # 「近 30 日历史龙头」名单。资金对同题材"熟脸"有记忆：消息一来先拉档案里的老龙头。
    # 一次读档、多事件复用（leaders_for_theme archive 参数）；增强层失败不阻塞主流程。
    try:
        from app.services.leader_archive import get_archive, leaders_for_theme

        hub = getattr(request.app.state, "hub", None)
        if hub is not None:
            archive = await get_archive(hub.provider)
            for e in enriched:
                for d in e["directions"]:
                    if d["target_type"] == "theme" and d.get("target"):
                        mem = await leaders_for_theme(hub.provider, d["target"], archive=archive)
                        if mem:
                            d["memory_leaders"] = [
                                {"symbol": m["symbol"], "name": m["name"],
                                 "max_boards": m["max_boards"], "hit_days": m["hit_days"]}
                                for m in mem[:3]
                            ]
    except Exception:  # noqa: BLE001  记忆附加强层，失败仅降级（无记忆名单）
        log.warning("impact theme-memory enrich skipped", exc_info=True)

    return {"data": {"count": len(enriched), "counts_all": counts, "four_counts": four_counts,
                     "tag_counts": tag_counts, "sort": sort, "items": enriched}, "meta": {}}


@router.get("/events/{event_id}")
async def get_event(event_id: int, store: EventStore = Depends(get_store)) -> dict:
    row = store.get_event(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    observations = [
        {
            "id": o.id, "source": o.source, "source_item_id": o.source_item_id,
            "title": o.title, "summary": o.summary, "url": o.url,
            "source_published_at": o.source_published_at.isoformat(sep=" ") if o.source_published_at else None,
            "received_at": o.received_at.isoformat(sep=" "),
            "available_at": o.available_at.isoformat(sep=" "),
            "source_symbols": json.loads(o.source_symbols_json),
            "board_codes": json.loads(o.board_codes_json),
            "content_hash": o.content_hash, "change_kind": o.change_kind,
        }
        for o in store.observations_of(event_id)
    ]
    versions = [_serialize_interpretation(v) for v in store.interpretations_of(event_id)]
    review_target = store.pending_observation_of(event_id)
    links = [_serialize_withdrawal_link(link, notice)
             for link, notice in store.withdrawal_links_of(event_id)]
    return {"data": {**_serialize(row),
                     "observations": observations, "interpretations": versions,
                     "withdrawal_links": links,
                     "pending_review_observation_id": review_target.id if review_target else None}, "meta": {}}


@router.get("/events/{event_id}/interpretation")
async def event_interpretation_at(event_id: int, as_of: datetime,
                                  store: EventStore = Depends(get_store)) -> dict:
    """Replay the state that existed by a Beijing local wall-clock time."""
    if as_of.tzinfo is not None:
        from app.core.bjtime import BJ_TZ
        as_of = as_of.astimezone(BJ_TZ).replace(tzinfo=None)
    version = store.interpretation_at(event_id, as_of)
    return {"data": _serialize_interpretation(version) if version else None,
            "meta": {"state": "known" if version else "unknown",
                     "note": None if version else "该时点无已记录解释；旧事件不推断历史版本"}}


@router.post("/events/{event_id}/review-revision", dependencies=[Depends(require_write_token)])
async def review_event_revision(event_id: int, body: RevisionReviewIn,
                                store: EventStore = Depends(get_store)) -> dict:
    try:
        version = await asyncio.to_thread(
            store.review_revision, event_id,
            expected_observation_id=body.expected_observation_id,
            action=body.action, note=body.note,
            interpretation=body.interpretation.model_dump() if body.interpretation else None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"data": _serialize_interpretation(version), "meta": {}}


@router.post("/events/{event_id}/link-withdrawal", dependencies=[Depends(require_write_token)])
async def link_event_withdrawal(event_id: int, body: WithdrawalLinkIn,
                                store: EventStore = Depends(get_store)) -> dict:
    try:
        link, notice = await asyncio.to_thread(
            store.link_withdrawal, event_id,
            target_observation_id=body.target_observation_id,
            notice_observation_id=body.notice_observation_id,
            expected_interpretation_id=body.expected_interpretation_id,
            note=body.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"data": _serialize_withdrawal_link(link, notice), "meta": {}}


@router.post("/events/{event_id}/review-symbol-direction",
             dependencies=[Depends(require_write_token)])
async def review_event_symbol_direction(event_id: int, body: SymbolDirectionReviewIn,
                                        store: EventStore = Depends(get_store)) -> dict:
    try:
        version = await asyncio.to_thread(
            store.review_symbol_direction, event_id, **body.model_dump(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"data": _serialize_interpretation(version), "meta": {}}


@router.get("/events/{event_id}/stocks")
async def event_stocks(event_id: int, request: Request, store: EventStore = Depends(get_store)) -> dict:
    """标的池：方向题材 → 官方成分 + 人工 override（architecture-design §1 ④⑤）。

    成分为空的方向会懒同步一次官方成分；仍为空则如实返回空池
    （题材今日无关联标的 ≠ 数据错误）。
    """
    row = store.get_event(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    now = beijing_now_naive()
    if not EventStore.evidence_visible(row, now=now):
        return {"data": {"event": _serialize(row, now=now), "pools": []},
                "meta": {"note": "事件发布时间或解释版本尚未可见，标的池暂停",
                         "disclaimer": "标的池仅为事件关联成分，不构成买卖建议"}}
    if row.revision_pending_at is not None:
        return {"data": {"event": _serialize(row), "pools": []},
                "meta": {"note": "来源内容有未复核修订，标的池暂停",
                         "disclaimer": "标的池仅为事件关联成分，不构成买卖建议"}}
    svc = getattr(request.app.state, "theme_catalog", None)
    pools = []
    if svc is not None:
        name_to_code = {t.name: t.code for t in svc.get_catalog(limit=1000)}
        for d in row.directions:
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
        "data": {"event": _serialize(row), "pools": pools},
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
    - 方向题材 ∈ 该股官方归属题材（architecture-design §1 L3 归属反查）
    - 来源列出该股（source_symbol 或 symbol 方向行；多标的可为方向待判）
    """
    sym = normalize_symbol(symbol)
    svc = getattr(request.app.state, "theme_catalog", None)
    theme_names: set[str] = set()
    if svc is not None:
        theme_names = {m["theme_name"] for m in svc.get_official_for_symbol(sym)}

    matched = []
    # 2026-09-09：limit 50 → 300。此前每轮快讯新增几十条会把半天前的关键事件
    # （如 15:42 的北京商业航天）挤出 50 条窗口，个股关联"突然变 0"。
    # 另：row.directions 已由 list_events selectinload 预加载，直接用，免 N+1。
    # 同步 SQLite 读搬线程池（2026-09-12）：limit=300 实测约 21ms（个股详情热路径）。
    _rows = await asyncio.to_thread(store.list_events, active_only=True, limit=300)
    for row in _rows:
        dirs = row.directions
        if row.source_symbol == sym or any(
            d.target_type == "symbol" and d.target == sym for d in dirs
        ):
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


@router.post("/events/backfill-directions", dependencies=[Depends(require_write_token)])
async def backfill_directions(
    request: Request,
    store: EventStore = Depends(get_store),
    days: int = Query(default=3, ge=1, le=30),
) -> dict:
    """存量事件方向重扫（2026-09-09 事故修复的补数据入口）。

    背景：快讯入库曾漏传 theme_names → 官方题材目录匹配整条链路失效，近 3 日
    558 条事件中 198 条无任何方向行（含「北京：加快发展商业航天产业」这类明确
    的板块政策利好），个股关联事件因此恒为 0。

    幂等：仅对「当前无 direction 行」的事件补抽，绝不覆盖已有判定；category
    仅当旧值为 other 时升级（store.backfill_event 保证）。可重复执行。
    """
    from datetime import timedelta

    from app.events.extract import build_event

    theme_names = _theme_names(request.app.state)
    if not theme_names:
        return {"data": {"scanned": 0, "filled": 0, "note": "题材目录未同步，跳过（不臆造方向）"}, "meta": {}}

    # 2026-09-09 时区口径：published_at 统一北京 naive，cutoff 也用北京 naive
    cutoff = beijing_now_naive() - timedelta(days=days)
    # 同步 SQLite 读搬线程池（2026-09-12）：limit=2000 实测约 83ms——本文件最重的同步阻塞。
    rows = await asyncio.to_thread(store.list_events, active_only=False, limit=2000)
    scanned = filled = 0
    for r in rows:
        if r.revision_pending_at is not None:
            continue
        pub = getattr(r, "published_at", None)
        if pub is None or pub < cutoff:
            continue
        if getattr(r, "directions", None):
            continue
        scanned += 1
        try:
            ev = build_event(
                r.title, source=r.source, url=r.url, published_at=pub,
                source_symbol=getattr(r, "source_symbol", None), theme_names=theme_names,
            )
            filled += 1 if store.backfill_event(
                r.id, category=ev.get("category"),
                half_life_hours=ev.get("half_life_hours"),
                directions=ev.get("directions") or [],
            ) else 0
        except Exception:  # noqa: BLE001 —— 单条失败不影响整批
            log.warning("backfill failed: event %s", getattr(r, "id", "?"))
    return {"data": {"scanned": scanned, "filled": filled, "days": days,
                     "theme_names": len(theme_names)}, "meta": {}}


@router.post("/events/llm-aux-judge", dependencies=[Depends(require_write_token)])
async def llm_aux_judge_route(request: Request) -> dict:
    """pending 事件 LLM 辅助判定（P2-3 层1）手动触发。攒批 + 每事件一次。

    命中（direction≠0 且题材匹配目录）写 direction 行 matched_by=llm_aux；
    无论命中与否整批记 llm_judged_at（防重复烧钱）。失败整批跳过下轮重试。
    默认关闭（event_llm_aux_enabled=False 返回 skipped）——需显式开启。
    线程池执行（LLM 调用阻塞，不卡事件循环）。
    """
    import asyncio

    from app.events.llm_aux import judge_pending_batch

    theme_names = _theme_names(request.app.state)
    result = await asyncio.to_thread(
        judge_pending_batch, theme_names=theme_names,
    )
    return {"data": result, "meta": {}}


@router.get("/events/focus/themes")
async def theme_focus(
    request: Request,
    store: EventStore = Depends(get_store),
    days: int = Query(default=1, ge=1, le=7),
    limit: int = Query(default=8, ge=1, le=30),
) -> dict:
    """按板块聚合事件 → 次日关注方向（2026-09-09 需求 1）。

    聚合口径（全部来自方向行，不臆造）：
    - 板块 = directions.target（target_type=theme）；
    - 利好/利空 = direction ±1 计数（direction=0 只计「关联」不计方向）；
    - 排序：净方向（利好-利空）→ 事件条数 → 最新事件时间；同分时按板块名稳定排序；
    - 每条方向附带 judge_status（含待判/中性/过期），前端可据此标注可信度。

    用途：盘后汇总「明天看什么」——北京十五五规划这类政策利好会在这里
    以「商业航天 +N 利好」的形式冒头，而不必等人去翻快讯流。
    """
    from datetime import timedelta

    from app.events.extract import judge_state

    # 2026-09-09 时区口径：published_at 统一北京 naive，cutoff 也用北京 naive
    cutoff = beijing_now_naive() - timedelta(days=days)
    # 同步 SQLite 读搬线程池（2026-09-12）：limit=2000 实测约 85ms——本文件最重的同步阻塞。
    rows = await asyncio.to_thread(store.list_events, active_only=False, limit=2000)
    buckets: dict[str, dict] = {}
    for r in rows:
        if r.revision_pending_at is not None:
            continue
        pub = getattr(r, "published_at", None)
        if pub is None or pub < cutoff:
            continue
        for d in getattr(r, "directions", None) or []:
            if getattr(d, "target_type", "theme") != "theme" or not getattr(d, "target", None):
                continue
            b = buckets.setdefault(d.target, {
                "theme": d.target, "events": 0, "positive": 0, "negative": 0,
                "neutral": 0, "judged": 0, "pending": 0, "samples": [], "latest": None,
            })
            b["events"] += 1
            dir_v = int(getattr(d, "direction", 0) or 0)
            if dir_v > 0:
                b["positive"] += 1
            elif dir_v < 0:
                b["negative"] += 1
            else:
                b["neutral"] += 1
            st = judge_state(pub, [{"direction": dir_v, "chain": getattr(d, "chain", "")}],
                             half_life_hours=getattr(r, "half_life_hours", None))["status"]
            if st == "judged":
                b["judged"] += 1
            elif st == "pending":
                b["pending"] += 1
            if len(b["samples"]) < 3:
                b["samples"].append({"title": r.title, "direction": dir_v,
                                     "judge_status": st, "url": getattr(r, "url", None)})
            if b["latest"] is None or (pub and pub > b["latest"]):
                b["latest"] = pub
    out = []
    for b in buckets.values():
        b["net"] = b["positive"] - b["negative"]
        b["latest"] = b["latest"].isoformat(sep=" ") if b["latest"] else None
        out.append(b)
    out.sort(key=lambda x: (-x["net"], -x["events"], x["latest"] or "", x["theme"]))
    return {"data": {"days": days, "count": len(out), "items": out[:limit]}, "meta": {}}


class EventItemIn(BaseModel):
    title: str = Field(min_length=4, max_length=512)
    url: str | None = None
    source: str | None = None
    published_at: str | None = None
    source_symbol: str | None = None
    is_announcement: bool = False


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
    current = store.get_event(row.id)
    return {"data": {**_serialize(current), "created": created}, "meta": {}}


# POST /events/extract 与旧 /events/{id}/review 已删（2026-09-08 审查 P0-4：
# 前端与 scripts 零调用；批量抽取走 collect_news_events，人工裁决无消费方）。


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


# POST /events/collect 路由壳已删（2026-09-08 审查 P0-4）：collect_news_events
# 由盘后调度器直接函数调用，HTTP 壳无用武之地；调试用 pytest 直接调函数。
