"""簇 → 官方概念挂靠（09-08 用户反馈「代糖/玉米搜不到」的修复）。

**根因**：同花顺有**两套口径**——
①涨停原因动态标签（「功能糖」「减糖甜味剂」「玉米加工」，每日变）；
②概念板块目录（「代糖概念 885904」「玉米 885811」，静态成分，App 内展示口径）。
我们的题材聚类用①（当日生态最准），用户在同花顺看到的是②——簇名精确匹配
目录的旧挂靠逻辑对「功能糖」这类簇完全失效。

**修复**：按**成分重叠**反向挂靠——簇成员命中某官方概念 ≥2 只即挂靠
（official_matches，命中数降序 ≤3 个），并过滤全市场性大概念（成分 >300 只的
融资融券/深股通类命中几只毫无辨识度）。盘面题材看板与猎场机会视图共用。
"""
from __future__ import annotations

import logging

from app.core.ttl_cache import cache_on

log = logging.getLogger(__name__)

#: 概念成分规模上限：超过此值的全市场性概念（融资融券/深股通/国企改革…）无辨识度
MAX_CONCEPT_SIZE = 300
#: 簇挂靠最小命中数：≥2 只才算（单票噪声不挂）
MIN_HITS = 2
#: 每簇最多挂靠的官方概念数
MAX_MATCHES = 3


def _official_symbol_index(svc) -> tuple[dict[str, list[tuple[str, str]]], dict[str, int]]:
    """symbol → [(概念 code, name)] 反查索引 + 概念成分规模表。"""
    index: dict[str, list[tuple[str, str]]] = {}
    sizes: dict[str, int] = {}
    for t in svc.get_catalog(limit=1000):
        try:
            members = svc.get_members(t.code)
            sizes[t.code] = len(members)
            for m in members:
                index.setdefault(m.symbol, []).append((t.code, t.name))
        except Exception:  # noqa: BLE001 - 单概念成员失败不影响整体索引
            continue
    return index, sizes


def _get_index(request) -> tuple[dict[str, list[tuple[str, str]]], dict[str, int]]:
    cache = cache_on(request.app.state, "themes.official.index", 300, maxsize=2)
    hit, cached = cache.get("index")
    if not hit:
        svc = request.app.state.theme_catalog
        cached = _official_symbol_index(svc)
        cache.set("index", cached)
    return cached


def attach_official(request, themes_list: list[dict]) -> None:
    """给簇（themes_list 的每项）挂 official_matches + 成员级 official 布尔。

    目录服务不可用/为空时静默跳过（无徽标 ≠ 非成分，前端不得当负面信号）。
    """
    svc = getattr(request.app.state, "theme_catalog", None)
    if svc is None or svc.catalog_size() == 0:
        return
    try:
        symbol_index, concept_sizes = _get_index(request)
        name_to_code = {t.name: t.code for t in svc.get_catalog(limit=1000)}
        for card in themes_list:
            # 成员行：看板用 ladder，机会视图用 stocks（两结构 symbol 字段同名）
            member_rows = card.get("ladder") or card.get("stocks") or []
            # 成员级：簇名与目录精确同名时，成员在官方成分内打 official 布尔
            code = name_to_code.get(card.get("theme") or "")
            if code:
                official = {m.symbol for m in svc.get_members(code)}
                for it in member_rows:
                    it["official"] = it.get("symbol") in official

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
    except Exception:  # noqa: BLE001 - 挂靠失败不影响看板
        log.exception("attach official matches failed")
