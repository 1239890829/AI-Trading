"""Read-only attribution evidence over the existing immutable event lineage.

This projection describes collected claims and relationships. It does not infer
profit beneficiaries, verify a claim, or change opportunity eligibility.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from app.core.bjtime import BJ_TZ, beijing_now_naive
from app.events.extract import classify_certainty
from app.models.event import (
    EventCard, EventDirection, EventInterpretation, EventObservation, EventWithdrawalLink,
)

_DIMENSIONS = (
    ("company", "公司事件", ("收购", "重组", "控制权", "订单", "业绩", "投产", "回购")),
    ("policy", "政策与时事", ("政策", "国务院", "工信部", "财政部", "监管", "规划", "制裁", "关税")),
    ("industry", "产业变化", ("产业链", "行业", "产能", "供给", "库存", "国产替代")),
    ("demand", "需求与海外催化", ("需求", "采购", "资本开支", "海外", "英伟达", "NVIDIA", "AI服务器")),
    ("price", "产品与原料价格", ("涨价", "降价", "提价", "原材料", "原料", "成本", "粮价", "油价")),
    ("sudden", "突发事件", ("事故", "爆炸", "停产", "战争", "冲突", "供应中断")),
    ("weather", "气候与灾害", ("厄尔尼诺", "拉尼娜", "台风", "干旱", "洪水", "减产")),
    ("social", "社交舆情", ("舆情", "热搜", "社交媒体", "微博", "网传", "传闻", "辟谣")),
    ("market_structure", "市场交易线索", ("涨停", "连板", "换手", "资金流", "抱团", "轮动", "异动")),
)
_CATEGORY_DIMENSIONS = {"corporate": "company", "policy": "policy", "rumor": "social"}
_QUALIFIERS = (
    ("proposal", "拟议与推进条件", ("拟", "计划", "意向", "预案", "审批", "须经", "需经")),
    ("incomplete", "尚未兑现条件", ("尚未", "未形成", "未签署", "未完成", "正在验证")),
    ("scope", "需保留的条件与说明", ("规模较小", "不确定", "暂无法", "以公告为准", "不构成",
                              "不涉及", "不存在", "未参与", "没有", "暂无", "澄清", "辟谣")),
)
_SOURCE_LABELS = {5: "官方披露", 4: "一线权威", 3: "主流财经", 2: "聚合转载", 1: "自媒体"}
_CATEGORIES = ("policy", "statement", "data", "rumor", "corporate", "other")
_FACT_KINDS = ("fact", "opinion", "rumor")
_CERTAINTIES = ("done", "proposed", "rumor", "unknown")
_STATE_LABELS = {
    "active": "当前解释", "pending": "来源修订待复核", "withdrawn": "解释已撤回",
    "expired": "观察窗口已过", "unknown": "历史身份未记录", "unavailable": "证据尚未可见",
}


def _clock(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=BJ_TZ).isoformat() if value.tzinfo is None else value.astimezone(BJ_TZ).isoformat()


def _payload(version: EventInterpretation | None) -> dict:
    try:
        value = json.loads(version.payload_json) if version else {}
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _valid_direction_record(value) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        # "market" is produced by the existing NFP/rate chains. It is valid
        # evidence context, but _relation only maps symbol/theme rows to stocks.
        isinstance(value.get("target_type"), str) and value["target_type"] in ("symbol", "theme", "macro", "market")
        and isinstance(value.get("target"), str) and bool(value["target"].strip())
        and type(value.get("direction")) is int and value["direction"] in (-1, 0, 1)
        and type(value.get("strength")) is int and 1 <= value["strength"] <= 5
        and isinstance(value.get("chain"), str) and isinstance(value.get("basis"), str)
        and isinstance(value.get("matched_by"), str)
        and (value.get("observation_id") is None or
             (type(value["observation_id"]) is int and value["observation_id"] > 0))
    )


def _valid_payload(payload: dict) -> bool:
    return (
        isinstance(payload.get("title"), str) and bool(payload["title"].strip())
        and "summary" in payload and (payload["summary"] is None or isinstance(payload["summary"], str))
        and "url" in payload and (payload["url"] is None or isinstance(payload["url"], str))
        and type(payload.get("source_tier")) is int and 1 <= payload["source_tier"] <= 5
        and isinstance(payload.get("category"), str) and payload["category"] in _CATEGORIES
        and isinstance(payload.get("fact_kind"), str) and payload["fact_kind"] in _FACT_KINDS
        and isinstance(payload.get("certainty"), str) and payload["certainty"] in _CERTAINTIES
        and isinstance(payload.get("directions"), list)
        and all(_valid_direction_record(value) for value in payload["directions"])
    )


def _known_value(value, allowed: tuple[str, ...]) -> str:
    return value if isinstance(value, str) and value in allowed else "unknown"


def _context(observation: EventObservation | None, row: EventCard) -> dict:
    return {
        "title": observation.title if observation else row.title,
        "summary": observation.summary if observation else row.summary,
        "url": observation.url if observation else row.url,
        "observation_id": observation.id if observation else None,
        "change_kind": observation.change_kind if observation else "legacy_unknown",
    }


def _tags(text: str, specifications: tuple) -> list[dict]:
    lowered = text.lower()
    return [{"key": key, "label": label, "matched_terms": [t for t in terms if t.lower() in lowered]}
            for key, label, terms in specifications if any(t.lower() in lowered for t in terms)]


def _relation(symbol: str | None, row: EventCard, directions: list[dict],
              theme_names: set[str], state: str, observation_id: int | None) -> dict:
    direct = [d for d in directions if d.get("target_type") == "symbol" and d.get("target") == symbol]
    themes = [d for d in directions if d.get("target_type") == "theme" and d.get("target") in theme_names]
    chosen = direct[0] if direct else None
    raw_direction = chosen.get("direction") if chosen else None
    valid_direction = type(raw_direction) is int and raw_direction in (-1, 0, 1)
    identity = (chosen or {}).get("observation_id")
    identity = identity if type(identity) is int and identity > 0 else None
    if symbol is None:
        status, label = "unknown", "未指定个股，需逐股核查"
    elif chosen and not valid_direction:
        status, label = "unknown", "方向记录格式无效，需重新核查"
    elif chosen and chosen.get("matched_by") == "llm_aux":
        status, label = "unverified_model", "模型关联假设待验证"
    elif chosen and identity != observation_id:
        status, label = "unknown", "逐股方向的来源身份待核"
    elif chosen and raw_direction in (-1, 1):
        status, label = "direct_symbol", "已记录逐股方向依据"
    elif chosen or row.source_symbol == symbol:
        status, label = "source_association", "来源关联，个股影响待核"
    elif themes and all(d.get("matched_by") == "llm_aux" for d in themes):
        status, label = "unverified_model", "模型题材假设待验证"
    elif themes:
        status, label = "theme_only", "仅题材归属关联"
    else:
        status, label = "unknown", "暂无该股关联依据"
    # A theme direction does not establish this company's benefit or harm.
    effective = chosen.get("direction") if chosen and status == "direct_symbol" and state == "active" else None
    return {"status": status, "label": label, "direction": effective,
            "recorded_direction": raw_direction if valid_direction else None,
            "observation_id": identity,
            "chain": chosen["chain"] if chosen and isinstance(chosen.get("chain"), str) else "",
            "basis": chosen["basis"] if chosen and isinstance(chosen.get("basis"), str) else "",
            "matched_by": chosen["matched_by"] if chosen and isinstance(chosen.get("matched_by"), str) else None,
            "matched_themes": sorted({d["target"] for d in themes})}


def project_event_attribution(row: EventCard, version: EventInterpretation | None,
                              observation: EventObservation | None,
                              pending: EventObservation | None, *, symbol: str | None,
                              theme_names: set[str], now: datetime,
                              withdrawal_context: dict | None = None) -> dict:
    """Pure projection; all supplied identities were read in one DB transaction."""
    payload = _payload(version)
    if observation is not None and observation.event_id != row.id:
        observation = None  # A foreign observation cannot support this event.
    context = _context(observation, row)
    text = f"{context['title']}\n{context['summary'] or ''}"
    directions = payload.get("directions", []) if version else [
        {"target_type": d.target_type, "target": d.target, "direction": d.direction,
         "chain": d.chain, "basis": d.basis, "matched_by": d.matched_by}
        for d in row.directions
    ]
    valid_payload = _valid_payload(payload) if version else False
    # Keep safe identity rows for explanatory recorded_direction, but never
    # pass array/object targets or metadata into membership/lookups.
    directions = [d for d in directions if isinstance(d, dict)
                  and isinstance(d.get("target_type"), str)
                  and isinstance(d.get("target"), str)] if isinstance(directions, list) else []
    if version is None or not valid_payload:
        state = "unknown"
    elif version.state != "active":
        state = version.state if version.state in _STATE_LABELS else "unknown"
    elif row.status != "active":
        state = "withdrawn"
    elif row.revision_pending_at is not None:
        state = "pending"
    elif (observation is None or observation.available_at > now or observation.received_at > now
          or (observation.source_published_at is not None and observation.source_published_at > now)):
        state = "unavailable"
    elif (now - row.published_at).total_seconds() >= row.half_life_hours * 7200:
        state = "expired"
    else:
        state = "active"
    dimensions = _tags(text, _DIMENSIONS)
    category = _known_value(payload.get("category") if version else row.category, _CATEGORIES)
    category_key = _CATEGORY_DIMENSIONS.get(category)
    if category_key and not any(d["key"] == category_key for d in dimensions):
        label = next(label for key, label, _ in _DIMENSIONS if key == category_key)
        dimensions.append({"key": category_key, "label": label, "matched_terms": []})
    qualifiers = [{"kind": d["key"], "label": d["label"], "matched_terms": d["matched_terms"]}
                  for d in _tags(text, _QUALIFIERS)]
    # Word highlights are context hints, not a semantic denial. Reuse the
    # extractor's narrow business-denial contract instead of treating phrases
    # such as "没有重大财务变化" or a positive clarification as denials.
    business_denial = bool(text.strip()) and classify_certainty(
        context["title"], context["summary"])[1] == "unknown"
    if business_denial:
        qualifiers.append({"kind": "denial", "label": "原文含明确业务否认", "matched_terms": []})
    certainty = _known_value(payload.get("certainty") if version else row.certainty, _CERTAINTIES)
    if certainty == "proposed" and not any(q["kind"] == "proposal" for q in qualifiers):
        qualifiers.append({"kind": "proposal", "label": "已记录阶段为拟议", "matched_terms": []})
    if certainty == "unknown":
        qualifiers.append({"kind": "scope", "label": "事件阶段未核定", "matched_terms": []})
    fact_kind = _known_value(payload.get("fact_kind") if version else row.fact_kind, _FACT_KINDS)
    fact_status, fact_label = {"rumor": ("rumor", "来源内容为传闻"),
                              "opinion": ("opinion", "来源内容为观点解读"),
                              "fact": ("source_recorded", "已记录来源内容")}.get(
                                  fact_kind, ("unknown", "来源内容类别待核"))
    if observation is None:
        fact_status, fact_label = "unknown", "来源观察身份未记录"
    limitations = ["来源内容与规则关联不等于事实独立核实或涨停因果证明。",
                   "这是当前解读视图，不是首次入选时的历史判断依据。",
                   "仅覆盖已采集事件与已缓存题材关系，不代表所有驱动。",
                   "当前使用已采集标题与摘要，未自动获取全文；未记录的条件仍未知。"]
    relation = _relation(symbol, row, directions, theme_names, state,
                         observation.id if observation else None)
    if version is None:
        relation["status"], relation["label"] = "unknown", "历史关联未核"
        relation["direction"] = None
    elif not valid_payload:
        relation["status"], relation["label"] = "unknown", "解释记录格式待核"
        relation["direction"] = None
    if certainty == "unknown":
        relation["direction"] = None
        limitations.append("事件阶段未核定，原始方向记录保留，当前不提供有效个股方向。")
    if business_denial:
        relation["direction"] = None
        limitations.append("原文含否认或澄清；原始方向记录保留，但此视图不据此确认个股受益。")
    if relation["status"] == "theme_only":
        limitations.append("题材归属只用于发现线索，尚未核实公司业务受益或成本压力。")
    if relation["status"] == "unverified_model":
        limitations.append("模型辅助方向未经独立验证，不用于改变选股资格。")
    if state != "active":
        limitations.append("本解释当前不提供可消费的个股方向；旧依据保留供核对。")
    if version and not valid_payload:
        limitations.append("解释记录缺失或格式不完整，不能视为有效归因。")
    if any(q["kind"] in {"denial", "proposal", "incomplete", "scope"} for q in qualifiers):
        limitations.append("需保留原文中的否认、拟议、规模与兑现条件，不能改写为确定受益。")
    alternatives = []
    if len(dimensions) > 1:
        alternatives.append("来源同时涉及多个维度，尚未证明各维度对股价的贡献。")
    if any(type(d.get("direction")) is int and d["direction"] == -1 for d in directions) and any(
        type(d.get("direction")) is int and d["direction"] == 1 for d in directions
    ):
        alternatives.append("事件对不同对象的方向可能相反，需要逐股核查传导关系。")
    source_tier = payload.get("source_tier") if version else row.source_tier
    source_tier = source_tier if type(source_tier) is int and 1 <= source_tier <= 5 else None
    return {
        "event_id": row.id, "title": payload["title"] if isinstance(payload.get("title"), str) else context["title"],
        "summary": payload.get("summary") if isinstance(payload.get("summary"), str) else None,
        "url": payload.get("url") if isinstance(payload.get("url"), str) else None,
        "state": state, "state_label": _STATE_LABELS[state], "is_active": state == "active",
        "certainty": certainty, "fact_kind": fact_kind,
        "dimensions": dimensions,
        "source": {"collector": observation.source if observation else row.source,
                   "original_publisher": None, "source_tier": source_tier,
                   "source_tier_label": _SOURCE_LABELS.get(source_tier, "来源等级未知"),
                   "source_item_id": observation.source_item_id if observation else None,
                   "content_hash": observation.content_hash if observation else None},
        "timing": {"source_published_at": _clock(observation.source_published_at) if observation else None,
                   "received_at": _clock(observation.received_at) if observation else None,
                   "available_at": _clock(observation.available_at) if observation else None,
                   "interpretation_effective_at": _clock(version.effective_at) if version else None,
                   "card_published_at": _clock(row.published_at),
                   "source_time_known": bool(observation and observation.source_published_at)},
        "interpretation_ref": {"event_id": row.id, "version_id": version.id if version else None,
                               "observation_id": version.observation_id if version else None,
                               "available_at": _clock(version.effective_at) if version else None,
                               "state": version.state if version else "unknown"},
        "fact_support": {"status": fact_status, "label": fact_label,
                         "note": "来源等级描述发布渠道；本文未做事实独立核验。"},
        "stock_relation": relation,
        "market_response": {"status": "unknown", "status_label": "市场响应待核",
                            "verification_scope": "market_response", "fact_verified": False,
                            "causality_verified": False, "market_response_only": True,
                            "basis": "该只读视图未独立计算市场响应"},
        "qualifiers": qualifiers, "source_context": context,
        "pending_context": _context(pending, row) if pending else None,
        "review_note": version.review_note if version else None,
        "withdrawal_context": withdrawal_context,
        "alternatives": alternatives, "limitations": limitations,
    }


def read_attribution(session_factory, *, symbol: str | None = None,
                     theme_names: set[str] | None = None, event_id: int | None = None,
                     limit: int = 12, now: datetime | None = None) -> dict:
    """Bounded, three-SELECT current view. Never fetch, sync, write, or notify."""
    now = now or beijing_now_naive()
    if now.tzinfo is not None:
        now = now.astimezone(BJ_TZ).replace(tzinfo=None)
    themes = theme_names or set()
    limit = max(1, min(limit, 30))
    with session_factory() as db:
        connection = db.connection()
        if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
            # sqlite3 otherwise does not begin a transaction for SELECT. Keep
            # the card/version, bound observations and withdrawal in one snapshot.
            connection.exec_driver_sql("BEGIN")
        latest_id = select(func.max(EventInterpretation.id)).where(
            EventInterpretation.event_id == EventCard.id).correlate(EventCard).scalar_subquery()
        stmt = select(EventCard, EventInterpretation).outerjoin(
            EventInterpretation, EventInterpretation.id == latest_id).options(joinedload(EventCard.directions))
        if event_id is not None:
            stmt = stmt.where(EventCard.id == event_id)
        else:
            relevant_direction = select(EventDirection.id).where(
                EventDirection.event_id == EventCard.id,
                or_((EventDirection.target_type == "symbol") & (EventDirection.target == symbol),
                    (EventDirection.target_type == "theme") & EventDirection.target.in_(sorted(themes))),
            ).exists()
            stmt = stmt.where(or_(EventCard.source_symbol == symbol, relevant_direction))
        stmt = stmt.where(EventCard.published_at <= now,
                          or_(EventInterpretation.id.is_(None), EventInterpretation.effective_at <= now))
        pairs = db.execute(stmt.order_by(EventCard.published_at.desc(), EventCard.id.desc()).limit(limit)).unique().all()
        ids = [row.id for row, _ in pairs]
        observations = {}
        pending_by_event = {}
        withdrawals = {}
        if ids:
            # At most one pending revision/variant per returned event, plus the
            # exact observation each immutable interpretation cites.
            pending_ids = select(func.max(EventObservation.id)).join(
                EventCard, EventCard.id == EventObservation.event_id).where(
                    EventObservation.event_id.in_(ids), EventCard.revision_pending_at.is_not(None),
                    EventObservation.change_kind.in_(("revision", "variant")),
                    EventObservation.received_at >= EventCard.revision_pending_at,
                    EventObservation.received_at <= now, EventObservation.available_at <= now,
                ).group_by(EventObservation.event_id)
            bound_ids = [version.observation_id for _, version in pairs if version]
            rows = db.execute(select(EventObservation).where(or_(
                EventObservation.id.in_(bound_ids), EventObservation.id.in_(pending_ids)))).scalars().all()
            observations = {obs.id: obs for obs in rows}
            for obs in rows:
                if obs.event_id in ids and obs.change_kind in {"revision", "variant"}:
                    old = pending_by_event.get(obs.event_id)
                    if old is None or old.id < obs.id:
                        pending_by_event[obs.event_id] = obs
            version_ids = [version.id for _, version in pairs if version]
            links = db.execute(select(EventWithdrawalLink, EventObservation).join(
                EventObservation, EventWithdrawalLink.notice_observation_id == EventObservation.id
            ).where(EventWithdrawalLink.withdrawn_interpretation_id.in_(version_ids),
                    EventWithdrawalLink.linked_at <= now,
                    EventObservation.available_at <= now,
                    EventObservation.received_at <= now)).all()
            for link, notice in links:
                withdrawals[link.withdrawn_interpretation_id] = {
                    "event_id": notice.event_id, "observation_id": notice.id,
                    "title": notice.title, "summary": notice.summary, "url": notice.url,
                    "source": notice.source, "source_item_id": notice.source_item_id,
                    "available_at": _clock(notice.available_at),
                    "linked_at": _clock(link.linked_at), "review_note": link.note,
                }
        items = []
        for row, version in pairs:
            observation = observations.get(version.observation_id) if version else None
            pending = pending_by_event.get(row.id) if row.revision_pending_at is not None else None
            if pending and observation and pending.id == observation.id:
                pending = None  # The pending version already cites this exact source context.
            items.append(project_event_attribution(row, version, observation, pending,
                                                   symbol=symbol, theme_names=themes, now=now,
                                                   withdrawal_context=withdrawals.get(version.id) if version else None))
    return {"data": {"schema_version": 1, "symbol": symbol, "count": len(items), "items": items},
            "meta": {"view": "current_attribution", "historical_selection_basis": False,
                     "opportunity_eligibility_changed": False,
                     "coverage": "已采集事件与已缓存题材关系，非全市场因果覆盖",
                     "disclaimer": "仅供归因研究，不构成买卖建议；来源、关联与市场响应分别核查。"}}
