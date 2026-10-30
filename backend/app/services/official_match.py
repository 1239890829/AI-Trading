"""源原因标签、精确目录身份与相关成分导航分开。

``theme`` 保留源原因标签。只有完整同名的目录条目可给出 ``exact_catalog`` /
``catalog_code``；不拼别名、不用成员重叠改名。``official_matches`` 仅为兼容
旧消费者保留的成员重叠导航，不证明官方身份、共同驱动或候选资格。
候选资格仍仅沿既有概念容器，要求精确同名且全部支持股均为该目录的官方成分；行业映射不新增资格。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from uuid import uuid4

from copy import deepcopy
from datetime import datetime, timezone

from app.core.inflight_reads import LocalReadCapacityExceeded, join_existing_inflight, peek_inflight, read_inflight
from app.core.ttl_cache import TTLCache, cache_on

log = logging.getLogger(__name__)

#: 旧成员重叠导航的规模上限；不用于判断题材性质或排除精确目录身份。
MAX_CONCEPT_SIZE = 300
#: 簇挂靠最小命中数：≥2 只才算（单票噪声不挂）
MIN_HITS = 2
#: 每簇最多挂靠的官方概念数
MAX_MATCHES = 3
# Existing optional theme-view wait, shared by all foreground projections.
OFFICIAL_CATALOG_VIEW_BUDGET_SECONDS = 0.25
# Observation transport only; it does not extend the existing source client's 15s budget.
CATALOG_COMPLETION_WAIT_SECONDS = 15.0

_INDEX_LOCK_INIT = threading.Lock()


def catalog_identity_index(catalog) -> dict[str, dict]:
    """Index exact source names; ambiguous same-name identities are not chosen."""
    identities: dict[str, dict] = {}
    ambiguous: set[str] = set()
    for row in catalog:
        code, name = getattr(row, "code", None), getattr(row, "name", None)
        if (not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}\.TI", code)
                or not isinstance(name, str) or not name.strip()
                or getattr(row, "source", None) != "ths_official"
                or getattr(row, "catalog_active", None) is not True):
            continue
        identity = {"code": code, "name": name, "source": "ths_official",
                    "basis": "exact_name", "kind": getattr(row, "kind", "concept")}
        if name in identities and identities[name] != identity:
            ambiguous.add(name)
        identities[name] = identity
    return {name: identity for name, identity in identities.items() if name not in ambiguous}


def _official_symbol_index(svc) -> tuple[dict[str, list[tuple[str, str]]], dict[str, int]]:
    """symbol → [(概念 code, name)] 反查索引 + 概念成分规模表。"""
    index: dict[str, list[tuple[str, str]]] = {}
    sizes: dict[str, int] = {}
    catalog = list(catalog_identity_index(svc.get_catalog(limit=1000)).values())
    try:
        symbols_by_code = svc.member_symbols_bulk([t["code"] for t in catalog])
    except Exception:  # noqa: BLE001 - 批读失败仍保留原逐概念覆盖与容错
        symbols_by_code = None
    for t in catalog:
        code, name = t["code"], t["name"]
        try:
            symbols = (symbols_by_code.get(code, []) if symbols_by_code is not None
                       else [m.symbol for m in svc.get_members(code)])
            sizes[code] = len(symbols)
            for symbol in symbols:
                index.setdefault(symbol, []).append((code, name))
        except Exception:  # noqa: BLE001 - 单概念成员失败不影响整体索引
            continue
    return index, sizes


def _state(holder):
    """Accept FastAPI Request, app, or app.state without making services HTTP-bound."""
    if hasattr(holder, "app"):
        holder = holder.app
    return holder.state if hasattr(holder, "state") else holder


def _get_index(holder) -> tuple[dict[str, list[tuple[str, str]]], dict[str, int]]:
    state = _state(holder)
    # get/build/set 跨多次同步读。整段在 worker 内按 holder 合并冷建造。
    with _INDEX_LOCK_INIT:
        lock = getattr(state, "_official_index_lock", None)
        if lock is None:
            lock = threading.Lock()
            state._official_index_lock = lock
    with lock:
        cache = cache_on(state, "themes.official.index", 300, maxsize=2)
        key = ("index", getattr(state.theme_catalog, "revision", 0))
        hit, cached = cache.get(key)
        if not hit:
            cached = _official_symbol_index(state.theme_catalog)
            cache.set(key, cached)
        return cached


def _directory_resolution(directories: dict, observed_at: str) -> dict:
    """Validate the complete declared source scope before exact-name resolution."""
    from types import SimpleNamespace

    for value in directories.values():
        if isinstance(value, LocalReadCapacityExceeded):
            raise value
    rows, details = [], {}
    for kind in ("concept", "industry"):
        value = directories[kind]
        if isinstance(value, BaseException):
            raise RuntimeError(f"{kind}_directory_{type(value).__name__}")
        items = value.get("items") if isinstance(value, dict) else None
        if (not isinstance(value, dict) or value.get("source") != "ths_official"
                or value.get("kind") != kind or value.get("catalog_state") != "ready"
                or not isinstance(items, list) or type(value.get("total")) is not int
                or value["total"] != len(items) or not 0 < len(items) <= 1000):
            raise RuntimeError(f"{kind}_directory_incomplete")
        codes = set()
        for row in items:
            if (not isinstance(row, dict) or row.get("source") != "ths_official"
                    or row.get("kind") != kind or not isinstance(row.get("name"), str)
                    or not row["name"].strip() or not isinstance(row.get("code"), str)
                    or not re.fullmatch(r"[0-9]{6}\.TI", row["code"])
                    or row["code"] in codes):
                raise RuntimeError(f"{kind}_directory_invalid_identity")
            codes.add(row["code"])
            rows.append(SimpleNamespace(code=row["code"], name=row["name"], source="ths_official",
                                        kind=kind, catalog_active=True))
        version = hashlib.sha256(json.dumps(sorted(
            (r["code"], r["name"]) for r in items), ensure_ascii=False).encode()).hexdigest()
        details[kind] = {"state": "ready", "version": version,
                         "synced_at": value.get("synced_at"), "total": len(items)}
    version = hashlib.sha256(json.dumps(details, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    identities = catalog_identity_index(rows)
    ambiguous = sorted({row.name for row in rows} - identities.keys())
    for identity in identities.values():
        identity.update(catalog_version=version, observed_at=observed_at)
    return {"state": "available", "identities": identities, "ambiguous": ambiguous,
            "catalog_version": version, "observed_at": observed_at, "directories": details, "reason": None}


def _registered_owner_completion_key(svc, revision, owner) -> str | None:
    """Assign once on the existing task, even before its factory is scheduled."""
    if owner is None or type(revision) is not int or not 0 <= revision <= 0xFFFFFFFF:
        return None
    inflight_key = ("theme-official-identities", ("source-identity-v4-kinds", revision))
    if peek_inflight(svc, inflight_key) is not owner:
        return None
    value = getattr(owner, "_official_catalog_completion_key", None)
    if value is None:
        nonce = hashlib.sha256(f"{id(svc)}:{uuid4().hex}".encode()).hexdigest()[:24]
        value = f"{revision:08x}{nonce}"
        owner._official_catalog_completion_key = value
    return value


async def resolve_official_catalog(holder) -> dict:
    """Share the existing directory owners; no name aliases or independent registry."""
    state = _state(holder)
    svc = getattr(state, "theme_catalog", None)
    unavailable = {"state": "unavailable", "identities": {}, "ambiguous": [],
                   "catalog_version": None, "observed_at": None, "directories": {},
                   "reason": "catalog_service_unavailable"}
    if svc is None or not callable(getattr(svc, "read_board_catalog", None)):
        return unavailable
    revision = getattr(svc, "revision", 0)
    caller = asyncio.current_task()
    if caller is not None:
        caller._official_catalog_revision = revision
    # This replaces the former route-local primitive cache, not the source owner.
    cache = cache_on(svc, "themes.official.identities", 60, maxsize=4)
    key = ("source-identity-v4-kinds", revision)
    hit, result = cache.get(key)
    if hit:
        return deepcopy(result)

    async def load():
        hit, result = cache.get(key)
        if hit:
            return result
        # Opaque to consumers. The revision prefix locates the original bounded
        # cache entry; the nonce binds this service instance and this attempt.
        completion_key = _registered_owner_completion_key(svc, revision, asyncio.current_task())
        try:
            values = await asyncio.gather(*(svc.read_board_catalog(kind) for kind in
                                            ("concept", "industry")), return_exceptions=True)
            result = _directory_resolution(dict(zip(("concept", "industry"), values)),
                                           datetime.now(timezone.utc).isoformat())
            if getattr(svc, "revision", 0) != revision:
                raise RuntimeError("catalog_revision_changed")
            result["catalog_revision"] = revision
        except LocalReadCapacityExceeded:
            raise
        except Exception as exc:
            result = {**unavailable, "reason": str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}
        result["completion_key"] = completion_key
        cache.set(key, result, ttl=60 if result["state"] == "available" else 5)
        return result

    return await read_inflight(svc, ("theme-official-identities", key), load)


async def resolve_official_catalog_view(holder, *, wait_seconds: float = OFFICIAL_CATALOG_VIEW_BUDGET_SECONDS) -> dict:
    """Bound foreground waiting; the shared source owner continues and is reused."""
    waiter = asyncio.create_task(resolve_official_catalog(holder))
    try:
        done, _pending = await asyncio.wait({waiter}, timeout=max(0, wait_seconds))
        if done:
            return waiter.result()
        svc = getattr(_state(holder), "theme_catalog", None)
        revision = getattr(waiter, "_official_catalog_revision", None)
        key = ("source-identity-v4-kinds", revision)
        cache = getattr(svc, "_ttl_cache_themes.official.identities", None)
        if isinstance(cache, TTLCache):
            hit, completed = cache.get(key)
            if hit:
                return deepcopy(completed)
        owner = peek_inflight(svc, ("theme-official-identities", key)) if svc is not None else None
        pending = {"state": "unavailable", "identities": {}, "ambiguous": [], "catalog_version": None,
                   "observed_at": None, "directories": {}, "reason": "catalog_read_pending"}
        completion_key = _registered_owner_completion_key(svc, revision, owner)
        if completion_key:
            pending["completion_key"] = completion_key
        else:
            pending["reason"] = "completion_owner_untracked"
        return pending
    except LocalReadCapacityExceeded:
        # No source owner was admitted. Do not issue a completion ticket or
        # cache local contention as a directory-source failure.
        return {"state": "unavailable", "identities": {}, "ambiguous": [], "catalog_version": None,
                "observed_at": None, "directories": {}, "reason": "local_read_capacity_exceeded"}
    finally:
        if not waiter.done():
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)


async def complete_official_catalog(holder, completion_key: str) -> dict:
    """Observe a prior read only: cache miss is terminal and never calls a factory."""
    def terminal(reason, result=None):
        value = result or {}
        return {"scope": "current_directory_resolution", "completion_key": completion_key,
                "state": value.get("state", "unavailable"),
                "catalog_version": value.get("catalog_version"), "observed_at": value.get("observed_at"),
                "identities": deepcopy(value.get("identities") or {}),
                "ambiguous": list(value.get("ambiguous") or []), "reason": reason}

    if not isinstance(completion_key, str) or not re.fullmatch(r"[0-9a-f]{32}", completion_key):
        return terminal("invalid_completion_key")
    svc = getattr(_state(holder), "theme_catalog", None)
    if svc is None:
        return terminal("catalog_service_unavailable")
    revision = int(completion_key[:8], 16)
    if getattr(svc, "revision", 0) != revision:
        return terminal("catalog_revision_changed")
    key = ("source-identity-v4-kinds", revision)
    cache = getattr(svc, "_ttl_cache_themes.official.identities", None)
    if isinstance(cache, TTLCache):
        hit, result = cache.get(key)
        if hit:
            if result.get("completion_key") == completion_key:
                return terminal(result.get("reason"), result)
            return terminal("completion_unknown_or_superseded")
    inflight_key = ("theme-official-identities", key)
    owner = peek_inflight(svc, inflight_key)
    if owner is None:
        return terminal("completion_expired_or_unknown")
    if getattr(owner, "_official_catalog_completion_key", None) != completion_key:
        return terminal("completion_unknown_or_superseded")
    waiter = asyncio.create_task(join_existing_inflight(svc, inflight_key, owner))
    try:
        done, _pending = await asyncio.wait({waiter}, timeout=CATALOG_COMPLETION_WAIT_SECONDS)
        if not done:
            return terminal("completion_wait_timeout")
        try:
            found, result = waiter.result()
        except asyncio.CancelledError:
            return terminal("completion_owner_cancelled")
        except LocalReadCapacityExceeded:
            return terminal("local_read_capacity_exceeded")
        except Exception:
            return terminal("completion_owner_failed")
        if getattr(svc, "revision", 0) != revision:
            return terminal("catalog_revision_changed")
        if not found or result.get("completion_key") != completion_key:
            return terminal("completion_expired_or_unknown")
        return terminal(result.get("reason"), result)
    finally:
        if not waiter.done():
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)


def mapping_for_name(name: str, resolution: dict | None) -> dict:
    """Pure source-name projection; historical references use the same policy."""
    context = {"catalog_version": (resolution or {}).get("catalog_version"),
               "observed_at": (resolution or {}).get("observed_at"),
               "reason": (resolution or {}).get("reason")}
    if context["reason"] == "catalog_read_pending" and (resolution or {}).get("completion_key"):
        context["completion_key"] = resolution["completion_key"]
    identity = None
    if name == "未分类":
        state = "unclassified"
    elif not resolution or resolution.get("state") != "available":
        state = "unavailable"
    else:
        identity = resolution["identities"].get(name)
        state = "exact" if identity else "ambiguous" if name in resolution.get("ambiguous", []) else "unmapped"
    return {"theme": name, "exact_catalog": deepcopy(identity), "catalog_mapping_state": state,
            "catalog_mapping_context": context}


async def current_catalog_reference(holder, names, *, resolution: dict | None = None) -> dict:
    """Read-only current reference beside frozen records, never inside their facts."""
    wanted = list(dict.fromkeys(name for name in names if isinstance(name, str) and name.strip()))
    if resolution is None:
        resolution = await resolve_official_catalog_view(holder) if wanted else {}
    reference = {"scope": "current_directory_reference", "catalog_version": resolution.get("catalog_version"),
                 "observed_at": resolution.get("observed_at"),
                 "items": [mapping_for_name(name, resolution) for name in wanted]}
    if resolution.get("reason") == "catalog_read_pending" and resolution.get("completion_key"):
        reference["completion_key"] = resolution["completion_key"]
    return reference


def attach_official(holder, themes_list: list[dict], *, resolution: dict | None = None) -> None:
    """Apply a frozen directory projection; optional constituent reads cannot erase it.

    A synchronous caller must supply a resolved snapshot. Production readers use
    ``attach_official_async``. Missing snapshots fail closed instead of silently
    resolving only the concept half of the source scope.
    """
    for card in themes_list:
        card.update(mapping_for_name(card.get("theme"), resolution))
        card.update(catalog_code=None,
                    candidate_catalog_code=None, official_matches=[],
                    official_matches_basis="membership_overlap", catalog_membership_state="unavailable")
        for member in card.get("ladder") or card.get("stocks") or []:
            member.pop("official", None)
    if not resolution or resolution.get("state") != "available":
        return
    for card in themes_list:
        if card.get("is_unclassified") or card.get("theme") == "未分类":
            card["catalog_mapping_state"] = "unclassified"
            card["exact_catalog"] = None
            continue
        identity = card.get("exact_catalog")
        card["catalog_code"] = identity["code"] if identity else None

    state = _state(holder)
    svc = getattr(state, "theme_catalog", None)
    if svc is None or resolution.get("catalog_revision") != getattr(svc, "revision", 0):
        return  # frozen identity survives; mixed-revision membership cannot grant eligibility
    # Navigation remains the existing concept-only consumer. It does not grant
    # official identity and must not send industry codes to the concept DB.
    try:
        symbol_index, concept_sizes = _get_index(holder)
    except Exception:
        log.warning("optional official membership navigation unavailable")
        symbol_index, concept_sizes = {}, {}
    exact_codes = list({c["exact_catalog"]["code"] for c in themes_list
                        if (c.get("exact_catalog") or {}).get("kind") == "concept"})
    members_by_code = {}
    try:
        members_by_code = {code: set(symbols) for code, symbols
                           in svc.member_symbols_bulk(exact_codes).items()}
    except Exception:
        for code in exact_codes:
            try:
                members_by_code[code] = {m.symbol for m in svc.get_members(code)}
            except Exception:
                pass  # absent proof remains unknown, not an empty official set
    if resolution.get("catalog_revision") != getattr(svc, "revision", 0):
        return
    for card in themes_list:
        if card["catalog_mapping_state"] == "unclassified":
            continue
        member_rows = card.get("ladder") or card.get("stocks") or []
        identity = card.get("exact_catalog")
        code = identity["code"] if identity else None
        if identity and identity["kind"] == "concept" and code in members_by_code:
            official = members_by_code[code]
            card["catalog_membership_state"] = "available"
            for item in member_rows:
                item["official"] = item.get("symbol") in official
            supporting = card.get("supporting_symbols") or [item.get("symbol") for item in member_rows]
            if supporting and set(supporting) <= official:
                card["candidate_catalog_code"] = code
        symbols = {item.get("symbol") for item in member_rows if item.get("symbol")}
        match_counts = {}
        for symbol in symbols:
            for candidate_code, name in symbol_index.get(symbol, []):
                if concept_sizes.get(candidate_code, 10**9) <= MAX_CONCEPT_SIZE:
                    key = (candidate_code, name)
                    match_counts[key] = match_counts.get(key, 0) + 1
        matches = [{"code": code, "name": name, "hits": hits, "relationship": "membership_overlap",
                    "basis": "member_overlap", "source": "ths_official", "kind": "concept"}
                   for (code, name), hits in match_counts.items() if hits >= MIN_HITS]
        matches.sort(key=lambda value: -value["hits"])
        card["official_matches"] = matches[:MAX_MATCHES]


async def attach_official_async(holder, themes_list: list[dict], *, resolution: dict | None = None,
                                view_budget: float | None = OFFICIAL_CATALOG_VIEW_BUDGET_SECONDS) -> dict:
    """Use one source identity snapshot in every live consumer and new report."""
    if resolution is None:
        resolution = (await resolve_official_catalog(holder) if view_budget is None
                      else await resolve_official_catalog_view(holder, wait_seconds=view_budget))
    await asyncio.to_thread(attach_official, holder, themes_list, resolution=resolution)
    return resolution
