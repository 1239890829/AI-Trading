"""源原因标签、精确目录身份与相关成分导航分开。

``theme`` 保留源原因标签。只有完整同名的目录条目可给出 ``exact_catalog`` /
``catalog_code``；不拼别名、不用成员重叠改名。``official_matches`` 仅为兼容
旧消费者保留的成员重叠导航，不证明官方身份、共同驱动或候选资格。
候选资格仍要求精确同名且全部支持股均为该目录的官方成分。
"""
from __future__ import annotations

import logging
import re
import threading

from app.core.ttl_cache import cache_on

log = logging.getLogger(__name__)

#: 旧成员重叠导航的规模上限；不用于判断题材性质或排除精确目录身份。
MAX_CONCEPT_SIZE = 300
#: 簇挂靠最小命中数：≥2 只才算（单票噪声不挂）
MIN_HITS = 2
#: 每簇最多挂靠的官方概念数
MAX_MATCHES = 3

_INDEX_LOCK_INIT = threading.Lock()


def catalog_identity_index(catalog) -> dict[str, dict]:
    """Index exact source names; ambiguous same-name identities are not chosen."""
    identities: dict[str, dict] = {}
    ambiguous: set[str] = set()
    for row in catalog:
        code, name = getattr(row, "code", None), getattr(row, "name", None)
        if (not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}\.TI", code)
                or not isinstance(name, str) or not name
                or getattr(row, "source", None) != "ths_official"
                or getattr(row, "catalog_active", None) is not True):
            continue
        identity = {"code": code, "name": name, "source": "ths_official",
                    "basis": "exact_name"}
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


def attach_official(holder, themes_list: list[dict]) -> None:
    """Attach exact identity and separately labelled membership navigation.

    先清除本函数旧投影，目录不可用时明确 unknown，不沿用旧官方身份。
    """
    for card in themes_list:
        card.update(exact_catalog=None, catalog_mapping_state="unavailable", catalog_code=None,
                    candidate_catalog_code=None, official_matches=[],
                    official_matches_basis="membership_overlap")
        for member in card.get("ladder") or card.get("stocks") or []:
            member.pop("official", None)
    state = _state(holder)
    svc = getattr(state, "theme_catalog", None)
    if svc is None or svc.catalog_size() == 0:
        return
    try:
        symbol_index, concept_sizes = _get_index(holder)
        identities = catalog_identity_index(svc.get_catalog(limit=1000))
        by_code = {identity["code"]: identity for identity in identities.values()}
        exact_codes = list({identities[c["theme"]]["code"] for c in themes_list
                            if c.get("theme") in identities and not c.get("is_unclassified")})
        # Fresh membership read stays independent of the reverse-index cache.
        # One batch replaces N exact-name reads without extending freshness.
        try:
            members_by_code = {code: set(symbols) for code, symbols
                               in svc.member_symbols_bulk(exact_codes).items()}
        except Exception:
            members_by_code = {code: {m.symbol for m in svc.get_members(code)} for code in exact_codes}
        for card in themes_list:
            if card.get("is_unclassified") or card.get("theme") == "未分类":
                card["catalog_mapping_state"] = "unclassified"
                continue
            # 成员行：看板用 ladder，机会视图用 stocks（两结构 symbol 字段同名）
            member_rows = card.get("ladder") or card.get("stocks") or []
            # 成员级：簇名与目录精确同名时，成员在官方成分内打 official 布尔
            identity = identities.get(card.get("theme") or "")
            code = identity["code"] if identity else None
            card["exact_catalog"] = dict(identity) if identity else None
            card["catalog_mapping_state"] = "exact" if identity else "unmapped"
            card["catalog_code"] = code
            official = set()
            if code:
                official = members_by_code.get(code, set())
                for it in member_rows:
                    it["official"] = it.get("symbol") in official

            # Only an exact official name with the complete supporting membership
            # can supply the candidate container. Overlap remains navigation evidence.
            supporting = card.get("supporting_symbols") or [it.get("symbol") for it in member_rows]
            if code and supporting and set(supporting) <= official:
                card["candidate_catalog_code"] = code

            # 簇级：成分重叠挂靠
            symbols = {it.get("symbol") for it in member_rows if it.get("symbol")}
            match_counts: dict[tuple[str, str], int] = {}
            for sym in symbols:
                for code2, name2 in symbol_index.get(sym, []):
                    if concept_sizes.get(code2, 10**9) <= MAX_CONCEPT_SIZE:
                        match_counts[(code2, name2)] = match_counts.get((code2, name2), 0) + 1
            matches = [
                {"code": c, "name": n, "hits": h, "relationship": "membership_overlap",
                 "basis": "member_overlap", "source": (by_code.get(c) or {}).get("source", "unknown")}
                for (c, n), h in match_counts.items()
                if h >= MIN_HITS
            ]
            matches.sort(key=lambda x: -x["hits"])
            card["official_matches"] = matches[:MAX_MATCHES]
    except Exception:  # noqa: BLE001 - 挂靠失败不影响看板
        log.exception("attach official matches failed")
