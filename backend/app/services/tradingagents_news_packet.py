"""Bounded, read-only public observations for an explicit research task.

The observation text is a collected claim, not verified truth or an old model's
explanation. Source symbols are never inferred from cards, directions or themes.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta
import hashlib
import json
import re
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import func, select

from app.core.bjtime import BJ_TZ, beijing_now
from app.events.attribution import read_attribution
from app.models.event import EventCard, EventInterpretation, EventObservation


METHOD = "existing-public-observation-v1"
_PUBLIC_HOSTS = {
    "东财快讯": {"finance.eastmoney.com"},
    "eastmoney": {"finance.eastmoney.com", "data.eastmoney.com"},
    "交易所公告": {"static.cninfo.com.cn", "www.sse.com.cn", "www.szse.cn"},
    "巨潮资讯": {"static.cninfo.com.cn", "www.cninfo.com.cn"},
    "上海证券交易所": {"www.sse.com.cn"},
    "深圳证券交易所": {"www.szse.cn"},
}
_PRIVATE_TEXT = re.compile(r"我的持仓|我的账户|您的账户|内部交易回执|本次模拟交易|"
                           r"(?i:api[_ -]?key|access_token|authorization\s*:|-----BEGIN .*PRIVATE KEY)")
_SECRET_QUERY = {"token", "access_token", "api_key", "apikey", "authorization", "password", "secret"}


def _clock(value):
    if not isinstance(value, datetime):
        raise ValueError("source_clock_missing")
    return value.replace(tzinfo=BJ_TZ) if value.utcoffset() is None else value.astimezone(BJ_TZ)


def _list(value, *, symbols=False):
    result = json.loads(value)
    if (not isinstance(result, list) or len(result) > 64
            or any(not isinstance(item, str) or not 1 <= len(item) <= 100 for item in result)):
        raise ValueError("source_metadata_invalid")
    if symbols and any(not re.fullmatch(r"\d{6}(?:\.(?:SH|SZ|BJ))?", item) for item in result):
        raise ValueError("source_metadata_invalid")
    return result


def _public(observation):
    if observation.source not in _PUBLIC_HOSTS or not isinstance(observation.url, str):
        return False
    try:
        url = urlsplit(observation.url)
        return (url.scheme in {"https", "http"} and not url.username and not url.password
                and url.hostname in _PUBLIC_HOSTS[observation.source]
                and url.port in {None, 80, 443}
                and not any(key.lower() in _SECRET_QUERY
                            for key, _ in parse_qsl(url.query) + parse_qsl(url.fragment)))
    except ValueError:
        return False


def _project(item, card, version, observation, symbol, as_of):
    ref, context, timing = (item.get(name) for name in ("interpretation_ref", "source_context", "timing"))
    if (not all(isinstance(value, dict) for value in (ref, context, timing))
            or any(type(ref.get(key)) is not int or ref[key] < 1
                   for key in ("event_id", "version_id", "observation_id"))
            or type(context.get("observation_id")) is not int
            or item.get("state") not in {"active", "expired"}
            or card.status != "active" or card.revision_pending_at is not None
            or version.state != "active" or ref.get("state") != "active"
            or ref.get("event_id") != card.id or ref.get("version_id") != version.id
            or ref.get("observation_id") != observation.id or context.get("observation_id") != observation.id
            or version.event_id != card.id or observation.event_id != card.id
            or version.observation_id != observation.id):
        raise ValueError("source_lineage_unavailable")
    if not _public(observation):
        raise ValueError("public_source_unavailable")
    if (not isinstance(observation.title, str) or not 1 <= len(observation.title.strip()) <= 512
            or observation.summary is not None and (not isinstance(observation.summary, str)
                                                    or len(observation.summary) > 8000)
            or _PRIVATE_TEXT.search(observation.title + "\n" + (observation.summary or ""))):
        raise ValueError("public_source_text_unavailable")
    if any(context.get(key) != getattr(observation, key) for key in ("title", "summary", "url")):
        raise ValueError("source_text_binding_invalid")
    published, received, available, effective = map(_clock, (
        observation.source_published_at, observation.received_at, observation.available_at, version.effective_at))
    if not published <= received <= available <= effective <= as_of:
        raise ValueError("source_clock_unavailable")
    if published < as_of - timedelta(days=7):
        raise ValueError("source_outside_seven_day_window")
    symbols = _list(observation.source_symbols_json, symbols=True)
    boards = _list(observation.board_codes_json)
    # Match the existing EventStore encoding exactly. This is normalized stored
    # content identity, never a hash of a publisher's HTTP body or full article.
    content = {"title": observation.title, "summary": observation.summary, "url": observation.url,
               "source_published_at": observation.source_published_at.isoformat(),
               "source_symbols": symbols, "board_codes": boards}
    digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if not isinstance(observation.content_hash, str) or digest != observation.content_hash:
        raise ValueError("source_content_hash_invalid")
    declared = any(value.split(".")[0] == symbol for value in symbols)
    relation = item.get("stock_relation") or {}
    return {"record_id": f"event-{card.id}-v{version.id}-o{observation.id}",
            "title": observation.title, "summary": observation.summary, "url": observation.url,
            "published_at": published.isoformat(), "received_at": received.isoformat(),
            "available_at": available.isoformat(), "source": observation.source,
            "source_kind": "existing_public_news_observation", "source_item_id": observation.source_item_id,
            "source_symbols": symbols, "board_codes": boards, "normalized_content_sha256": digest,
            "source_ref": {"event_id": card.id, "version_id": version.id, "observation_id": observation.id,
                           "interpretation_effective_at": effective.isoformat()},
            "relation": {"source_declares_requested_symbol": declared,
                         "recorded_relation": relation.get("status", "unknown"),
                         "commercial_connection_verified": False},
            "company_eligible": None if declared else False,
            "scope": "Collected public title/summary only; not full text or verified company impact. "
                     "Source association alone is not subject proof; unknown/global relations are not company-eligible."}


def read_news_packet(symbol: str, *, session_factory, as_of: datetime, limit: int = 12) -> dict:
    """Read one current bounded DB snapshot; never fetch, write, model or cache.

    ``as_of`` is a caller's aware capture cutoff, not historical replay. A new
    latest version after that cutoff stays excluded rather than reviving an old
    interpretation. Capture time belongs to wrapper metadata, not source time.
    """
    if (not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol)
            or not isinstance(as_of, datetime) or as_of.utcoffset() is None
            or not callable(session_factory) or type(limit) is not int):
        raise ValueError("invalid_news_packet_arguments")
    as_of, limit = as_of.astimezone(BJ_TZ), max(1, min(limit, 30))
    if as_of > beijing_now():
        raise ValueError("future_news_packet_cutoff")
    result = {"candidate_news": [], "missing": [], "source_status": "missing", "method": METHOD}
    rejected = 0
    try:
        with session_factory() as db:
            with db.no_autoflush:
                # Reuse the same Session and transaction for candidates and raw
                # observation rows. nullcontext cannot close the caller's read.
                candidates = read_attribution(lambda: nullcontext(db), symbol=symbol,
                    theme_names=set(), limit=limit, now=as_of)["data"]["items"][:limit]
                ids = []
                for item in candidates:
                    ref = item.get("interpretation_ref") or {}
                    identity = ref.get("version_id")
                    if type(identity) is int and identity > 0:
                        ids.append(identity)
                latest = select(func.max(EventInterpretation.id)).where(
                    EventInterpretation.event_id == EventCard.id).correlate(EventCard).scalar_subquery()
                rows = db.execute(select(EventCard, EventInterpretation, EventObservation).select_from(EventCard).join(
                    EventInterpretation, EventInterpretation.event_id == EventCard.id).join(
                    EventObservation, (EventObservation.id == EventInterpretation.observation_id)
                    & (EventObservation.event_id == EventCard.id)).where(
                    EventInterpretation.id.in_(ids), EventInterpretation.id == latest)).all() if ids else []
                bound = {version.id: (card, version, observation) for card, version, observation in rows}
                seen, text_chars = set(), 0
                for item in candidates:
                    try:
                        values = bound.get((item.get("interpretation_ref") or {}).get("version_id"))
                        if values is None:
                            raise ValueError("source_lineage_unavailable")
                        value = _project(item, *values, symbol, as_of)
                        if value["record_id"] in seen:
                            continue
                        text_chars += len(value["title"]) + len(value["summary"] or "")
                        if text_chars > 24000:
                            raise ValueError("public_source_text_budget_exceeded")
                        seen.add(value["record_id"])
                        result["candidate_news"].append(value)
                    except (ValueError, TypeError, KeyError):
                        rejected += 1
    except Exception:
        # No exception/path/source payload reaches the model or ordinary reader.
        result.update(candidate_news=[], source_status="failed")
        result["missing"].append("已有公开新闻读取失败；来源与公司关系保持未知。")
    else:
        result["source_status"] = "ready" if result["candidate_news"] else "missing"
        if not result["candidate_news"]:
            result["missing"].append("本次有界读取未取得可核身份与时刻的公开新闻，不代表公司没有新闻。")
    if rejected:
        result["missing"].append(f"{rejected}条候选未通过公开来源、时刻或原文版本绑定，未作为研究输入。")
    result["missing"].append("仅覆盖已有库的近7日最多30条相关候选；无全文、全网或社交覆盖保证。")
    result["captured_at"] = beijing_now().isoformat()
    return result
