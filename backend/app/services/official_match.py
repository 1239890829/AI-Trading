"""簇 → 官方概念挂靠（09-08 用户反馈「代糖/玉米搜不到」的修复）。

**根因**：同花顺有**两套口径**——
①涨停原因动态标签（「功能糖」「减糖甜味剂」「玉米加工」，每日变）；
②概念板块目录（「代糖概念 885904」「玉米 885811」，静态成分，App 内展示口径）。
我们的题材聚类用①（保留原始标签），用户在同花顺看到的是②——簇名精确匹配
目录的旧挂靠逻辑对「功能糖」这类簇完全失效。

**修复**：按**成分重叠**反向挂靠——簇成员命中某官方概念 ≥2 只即挂靠
（official_matches，命中数降序 ≤3 个），并过滤全市场性大概念（成分 >300 只的
融资融券/深股通类命中几只毫无辨识度）。盘面题材看板与猎场机会视图共用导航。未分类不挂靠；候选资格另需精确同名
且全部支持股均为官方成分，部分重叠不等于共同驱动。
"""
from __future__ import annotations

import logging
import threading

from app.core.ttl_cache import cache_on

log = logging.getLogger(__name__)

#: 概念成分规模上限：超过此值的全市场性概念（融资融券/深股通/国企改革…）无辨识度
MAX_CONCEPT_SIZE = 300
#: 簇挂靠最小命中数：≥2 只才算（单票噪声不挂）
MIN_HITS = 2
#: 每簇最多挂靠的官方概念数
MAX_MATCHES = 3

_INDEX_LOCK_INIT = threading.Lock()


def _official_symbol_index(svc) -> tuple[dict[str, list[tuple[str, str]]], dict[str, int]]:
    """symbol → [(概念 code, name)] 反查索引 + 概念成分规模表。"""
    index: dict[str, list[tuple[str, str]]] = {}
    sizes: dict[str, int] = {}
    catalog = svc.get_catalog(limit=1000)
    try:
        symbols_by_code = svc.member_symbols_bulk([t.code for t in catalog])
    except Exception:  # noqa: BLE001 - 批读失败仍保留原逐概念覆盖与容错
        symbols_by_code = None
    for t in catalog:
        try:
            symbols = (symbols_by_code.get(t.code, []) if symbols_by_code is not None
                       else [m.symbol for m in svc.get_members(t.code)])
            sizes[t.code] = len(symbols)
            for symbol in symbols:
                index.setdefault(symbol, []).append((t.code, t.name))
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
    """给簇（themes_list 的每项）挂 official_matches + 成员级 official 布尔。

    目录服务不可用/为空时静默跳过（无徽标 ≠ 非成分，前端不得当负面信号）。
    """
    state = _state(holder)
    svc = getattr(state, "theme_catalog", None)
    if svc is None or svc.catalog_size() == 0:
        return
    try:
        symbol_index, concept_sizes = _get_index(holder)
        name_to_code = {t.name: t.code for t in svc.get_catalog(limit=1000)}
        exact_codes = list({name_to_code[c["theme"]] for c in themes_list
                            if c.get("theme") in name_to_code and not c.get("is_unclassified")})
        # Fresh membership read stays independent of the reverse-index cache.
        # One batch replaces N exact-name reads without extending freshness.
        try:
            members_by_code = {code: set(symbols) for code, symbols
                               in svc.member_symbols_bulk(exact_codes).items()}
        except Exception:
            members_by_code = {code: {m.symbol for m in svc.get_members(code)} for code in exact_codes}
        for card in themes_list:
            card["candidate_catalog_code"] = None
            if card.get("is_unclassified") or card.get("theme") == "未分类":
                card["official_matches"] = []
                card["catalog_code"] = None
                continue
            # 成员行：看板用 ladder，机会视图用 stocks（两结构 symbol 字段同名）
            member_rows = card.get("ladder") or card.get("stocks") or []
            # 成员级：簇名与目录精确同名时，成员在官方成分内打 official 布尔
            code = name_to_code.get(card.get("theme") or "")
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
                {"code": c, "name": n, "hits": h}
                for (c, n), h in match_counts.items()
                if h >= MIN_HITS
            ]
            matches.sort(key=lambda x: -x["hits"])
            card["official_matches"] = matches[:MAX_MATCHES]
            # 弹窗入口用的目录代码：优先簇名精确同名，其次仅供浏览的成分重叠
            card["catalog_code"] = code or (matches[0]["code"] if matches else None)
    except Exception:  # noqa: BLE001 - 挂靠失败不影响看板
        log.exception("attach official matches failed")
