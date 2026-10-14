"""Read-only attribution evidence over the existing immutable event lineage.

This projection describes collected claims and relationships. It does not infer
profit beneficiaries, verify a claim, or change opportunity eligibility.
"""

from __future__ import annotations

import json
import re
from calendar import timegm
from datetime import datetime

from sqlalchemy import Integer, cast, case, func, or_, select
from sqlalchemy.orm import joinedload

from app.core.bjtime import BJ_TZ, beijing_now_naive
from app.events.extract import classify_certainty, _positive_hit
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
_MATERIAL_ACTION = (
    r"签约|签署|合同|订单|供货|供需|短缺|产能|量产|投产|涨价|降价|提价|政策|获批|预增|"
    r"澄清|调查|收购|重组|扩产|增资|"
    r"(?:拟|计划|实施|完成|启动|宣布|披露|审议|终止)[^。；;，,\n]{0,12}回购(?!概念|题材)|"
    r"回购(?:股份|股票|金额|计划|方案|进展|注销|完成|[0-9一二三四五六七八九十])|"
    r"投资(?!者)[^。；;，,\n]{0,24}(?:建设|设立|扩建|新建|成立|生产|研发)"
)
_TRADE_RECAP = (
    r"(?i)涨停|跌停|震荡反弹|低开高走|概念.{0,8}(?:上涨|下跌|走强)|ETF.{0,20}(?:涨|跌)[0-9]|"
    r"股价|涨幅|跌幅|成交额|成交量|换手率|主力资金|资金净流入|资金净流出"
)


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


def _auxiliary_judgment(payload: dict, row: EventCard, observation: EventObservation | None,
                        version: EventInterpretation | None, input_version) -> dict | None:
    """Expose a saved, bound auxiliary receipt; never an effective direction."""
    value = payload.get("llm_aux")
    if not isinstance(value, dict) or observation is None or version is None or input_version is None:
        return None
    evidence = value.get("evidence")
    if (type(value.get("event_id")) is not int or value["event_id"] != row.id
            or type(value.get("observation_id")) is not int or value["observation_id"] != observation.id
            or type(value.get("input_version_id")) is not int or value["input_version_id"] <= 0
            or input_version["id"] != value["input_version_id"] or input_version["id"] >= version.id
            or input_version["event_id"] != row.id or input_version["observation_id"] != observation.id
            or input_version["effective_at"] > version.effective_at or input_version["state"] != "active"
            or type(value.get("direction")) is not int or value["direction"] not in (-1, 0, 1)
            or value.get("verification") != "unverified" or value.get("eligibility_changed") is not False
            or not isinstance(evidence, dict) or evidence.get("field") not in {"title", "summary"}):
        return None
    theme = value.get("theme")
    if (theme is not None and (not isinstance(theme, str) or len(theme) > 64)) or (value["direction"] and not theme):
        return None
    if any(not isinstance(value.get(key), str) or len(value[key]) > 512 for key in ("reason", "chain")):
        return None
    quote = evidence.get("quote")
    source = getattr(observation, evidence["field"])
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 1024 or quote not in (source or ""):
        return None
    if value.get("state") != ("no_direction" if value["direction"] == 0 else "hypothesis"):
        return None
    return value


def _trade_recap_only(title: str, summary: str | None) -> bool:
    """Primary-clue display filter only; it does not change model candidates."""
    text = title + "\n" + (summary or "")
    if re.search(_MATERIAL_ACTION, text):
        return False  # Preserve material disclosures even when price action is also reported.
    return bool(re.search(_TRADE_RECAP, text))


def _primary_reason(items: list[dict]) -> dict:
    """One exact source clue, not a causal verdict or a replacement for provider reason."""
    out = {"status": "unconfirmed", "label": "线索未确认",
           "reason": "仅题材命中、交易复述或未绑定来源不能确认该股触发原因。",
           "event_id": None, "version_id": None, "observation_id": None, "relation": "unknown",
           "timing_relation": "time_unknown", "excerpt": None, "qualifiers": [],
           "causality_verified": False, "fact_verified": False, "interpretation_view": "current"}
    candidates = []
    for item in items:
        ref, context, relation = item["interpretation_ref"], item["source_context"], item["stock_relation"]
        if (item["state"] != "active" or item["fact_support"]["status"] != "source_recorded"
                or relation["status"] not in {"direct_symbol", "source_association"}
                or not ref["version_id"] or not ref["observation_id"]
                or ref["observation_id"] != context["observation_id"]
                or relation["observation_id"] != context["observation_id"]
                or _trade_recap_only(context["title"], context["summary"])):
            continue
        timing = item.get("timing_relation", {}).get("status", "response_time_unknown")
        if timing == "response_time_unknown" and not item["timing"]["source_time_known"]:
            timing = "time_unknown"
        priority = {"known_before_response": 0, "response_time_unknown": 1,
                    "late_source_observation": 2, "interpretation_after_response": 2, "time_unknown": 3}[timing]
        candidates.append((priority, item, timing))
    if not candidates:
        return out
    _, item, timing = min(candidates, key=lambda value: value[0])
    context = item["source_context"]
    fields = [name for name in ("summary", "title") if context[name] and context[name].strip()]

    def excerpt_priority(name):
        value = context[name]
        condition = min(({"incomplete": 0, "proposal": 1, "scope": 2}.get(q["kind"], 3)
                         for q in item["qualifiers"] if any(term in value for term in q["matched_terms"])), default=3)
        # Keep explicit conditions in either source field. Without conditions,
        # prefer the reported action to a summary consisting only of price data.
        business_denial = classify_certainty(value)[1] == "unknown"
        return not business_denial, condition, False if condition < 3 else not bool(re.search(_MATERIAL_ACTION, value))

    field = min(fields, key=excerpt_priority)
    text = context[field]
    # Prefer the sentence containing an explicit source condition. The full
    # source_context remains the drilldown when a long sentence needs clipping.
    terms = [term for q in item["qualifiers"] for term in q["matched_terms"] if term in text]
    position = min((text.index(term) for term in terms), default=0)
    start = max(text.rfind("。", 0, position), text.rfind("；", 0, position), text.rfind("\n", 0, position)) + 1
    ends = [end for delimiter in ("。", "；", "\n") if (end := text.find(delimiter, position)) >= 0]
    end = min(ends) + 1 if ends else len(text)
    if end - start > 200:
        start = max(start, position - 60)
        end = min(end, start + 200)
    quote = text[start:end]
    status = "unconfirmed" if timing == "time_unknown" else "later_clue" if timing in {
        "late_source_observation", "interpretation_after_response"} else "source_clue"
    # A short status leaves room for the source excerpt in a 260px sidebar.
    # The full reason, timing relation and false verification flags stay intact.
    label = {"source_clue": "来源线索", "later_clue": "事后资料",
             "unconfirmed": "时点待核"}[status]
    return {**out, "status": status, "label": label,
            "reason": "仅引用当前保存的来源；未核实为涨停主因。" if timing != "response_time_unknown" else
                      "未提供市场响应时刻，仅展示当前直接关联资料，不确认触发先后。",
            "event_id": item["event_id"], "version_id": item["interpretation_ref"]["version_id"],
            "observation_id": context["observation_id"], "relation": item["stock_relation"]["status"],
            "timing_relation": timing, "excerpt": {"field": field, "quote": quote,
                                                   "truncated": start > 0 or end < len(text)},
            "qualifiers": item["qualifiers"]}


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
                              withdrawal_context: dict | None = None,
                              auxiliary_input_version=None) -> dict:
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
    if (relation["direction"] == 1 and relation["matched_by"] in {"source", "name", "name-stem", "alias", "board"}
            and "「进展」" in relation["basis"] and _positive_hit(text) is None):
        relation["direction"] = None
        relation["label"] = "原规则方向与来源条件冲突，待复核"
        limitations.append("原规则的进展方向与未兑现条件冲突，保留原记录但当前方向需复核。")
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
        "llm_aux": _auxiliary_judgment(payload, row, observation, version, auxiliary_input_version),
        "withdrawal_context": withdrawal_context,
        "alternatives": alternatives, "limitations": limitations,
    }


def read_attribution(session_factory, *, symbol: str | None = None,
                     theme_names: set[str] | None = None, event_id: int | None = None,
                     limit: int = 12, now: datetime | None = None,
                     pool_date: str | None = None, response_as_of: datetime | None = None) -> dict:
    """Bounded current view. Never fetch, sync, write, or notify."""
    now = now or beijing_now_naive()
    if now.tzinfo is not None:
        now = now.astimezone(BJ_TZ).replace(tzinfo=None)
    if response_as_of is not None and response_as_of.tzinfo is not None:
        response_as_of = response_as_of.astimezone(BJ_TZ).replace(tzinfo=None)
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
        # Exact, observation-bound stock directions precede source associations
        # and theme-only discovery, before LIMIT. No name/substring inference.
        direct_identity = select(EventDirection.id).where(
            EventDirection.event_id == EventCard.id, EventDirection.target_type == "symbol",
            EventDirection.target == symbol, EventDirection.matched_by != "llm_aux",
            EventDirection.observation_id == EventInterpretation.observation_id,
        ).exists()
        relation_order = case((direct_identity, 0), (EventCard.source_symbol == symbol, 1), else_=2)
        # Reuse is_active's exact microsecond / two-half-life boundary. Expired,
        # pending or withdrawn direct rows must not evict a still-usable source.
        now_us = timegm(now.timetuple()) * 1_000_000 + now.microsecond
        published_us = (
            cast(func.strftime("%s", func.substr(EventCard.published_at, 1, 19)), Integer) * 1_000_000
            + cast(func.substr(EventCard.published_at, 21, 6), Integer)
        )
        visible_source = select(EventObservation.id).where(
            EventObservation.id == EventInterpretation.observation_id,
            EventObservation.event_id == EventCard.id, EventObservation.available_at <= now,
            EventObservation.received_at <= now, EventObservation.source_published_at.is_not(None),
            EventObservation.source_published_at <= now,
        ).exists()
        # Main-source eligibility also requires a recorded fact. Keep active
        # rumor/opinion rows readable, but do not let them evict that source.
        usable = (EventCard.fact_kind == "fact") & (EventCard.status == "active") & EventCard.revision_pending_at.is_(None) & (
            EventInterpretation.state == "active") & (
            published_us + EventCard.half_life_hours * 7_200_000_000 > now_us) & visible_source
        # Use the same bounded lexical contract as the final primary picker.
        # Recent price reports remain readable, but cannot fill the candidate
        # window before an older direct material source.
        source_text = EventCard.title + "\n" + func.coalesce(EventCard.summary, "")
        recap_only = source_text.regexp_match(_TRADE_RECAP) & ~source_text.regexp_match(_MATERIAL_ACTION)
        ordered = stmt.order_by(case((usable, 0), else_=1), relation_order,
                                case((recap_only, 1), else_=0),
                                EventCard.published_at.desc(), EventCard.id.desc())
        if response_as_of is None:
            pairs = db.execute(ordered.limit(limit)).unique().all()
        else:
            # Current versions, grouped by response timing. This is not historical replay.
            before = select(EventObservation.id).where(
                EventObservation.id == EventInterpretation.observation_id,
                EventInterpretation.effective_at <= response_as_of,
                EventObservation.available_at <= response_as_of,
                EventObservation.received_at <= response_as_of,
                EventObservation.source_published_at.is_not(None),
                EventObservation.source_published_at <= response_as_of,
            ).correlate(EventInterpretation).exists()
            prior_pairs = db.execute(ordered.where(before).limit(limit)).unique().all()
            later_pairs = db.execute(ordered.where(~before).limit(limit)).unique().all()
            pairs = prior_pairs + later_pairs
        ids = [row.id for row, _ in pairs]
        observations = {}
        pending_by_event = {}
        withdrawals = {}
        auxiliary_inputs = {}
        input_by_version = {}
        if ids:
            # A JSON number is not proof of an input interpretation's identity.
            # Only returned auxiliary journals need this one bounded metadata read.
            for _, version in pairs:
                auxiliary = _payload(version).get("llm_aux")
                identity = auxiliary.get("input_version_id") if isinstance(auxiliary, dict) else None
                if version and type(identity) is int and identity > 0:
                    input_by_version[version.id] = identity
            if input_by_version:
                inputs = db.execute(select(
                    EventInterpretation.id, EventInterpretation.event_id, EventInterpretation.observation_id,
                    EventInterpretation.effective_at, EventInterpretation.state,
                ).where(EventInterpretation.id.in_(set(input_by_version.values())))).mappings().all()
                auxiliary_inputs = {value["id"]: value for value in inputs}
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
                                                   withdrawal_context=withdrawals.get(version.id) if version else None,
                                                   auxiliary_input_version=auxiliary_inputs.get(input_by_version.get(version.id)) if version else None))
    data = {"schema_version": 1, "symbol": symbol, "count": len(items), "items": items}
    if response_as_of is not None or pool_date is not None:
        data["response_context"] = {"pool_date": pool_date, "response_as_of": _clock(response_as_of),
                                    "view": "current_with_response_context", "historical_replay": False}
    if response_as_of is not None:
        for item in items:
            timing = item["timing"]
            clocks = {key: datetime.fromisoformat(value).astimezone(BJ_TZ).replace(tzinfo=None)
                      if value else None for key, value in timing.items()
                      if key in {"source_published_at", "available_at", "received_at", "interpretation_effective_at"}}
            if any(value is None for value in clocks.values()):
                status, label = "time_unknown", "时间证据不完整，首封前是否可用待核"
            elif max(clocks["available_at"], clocks["received_at"], clocks["source_published_at"]) > response_as_of:
                status, label = "late_source_observation", "市场响应后才留存的来源，仅供后续核对"
            elif clocks["interpretation_effective_at"] > response_as_of:
                status, label = "interpretation_after_response", "当前解释形成晚于市场响应"
            else:
                status, label = "known_before_response", "响应前已留存材料与解释，因果仍未核实"
            item["timing_relation"] = {"status": status, "label": label}
        data["items"] = [item for item in items if item["timing_relation"]["status"] == "known_before_response"]
        data["later_items"] = [item for item in items if item["timing_relation"]["status"] != "known_before_response"]
        data["count"] = len(data["items"])
    data["primary_reason"] = _primary_reason(items)
    data["evidence_counts"] = {
        "scope": "returned_rows_only", "returned": len(items),
        "theme_only": sum(item["stock_relation"]["status"] == "theme_only" for item in items),
        "unknown": sum(item["stock_relation"]["status"] == "unknown" for item in items),
        "source_time_unknown": sum(not item["timing"]["source_time_known"] for item in items),
    }
    return {"data": data,
            "meta": {"view": "current_attribution", "historical_selection_basis": False,
                     "opportunity_eligibility_changed": False,
                     "coverage": "已采集事件与已缓存题材关系，非全市场因果覆盖",
                     "disclaimer": "仅供归因研究，不构成买卖建议；来源、关联与市场响应分别核查。"}}
