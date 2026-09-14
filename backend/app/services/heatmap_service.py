"""A 股云图（heatmap）数据服务：全市场快照 × 行业映射 → treemap 载荷。

三要素与来源（2026-08-30 实测）：
- 涨跌幅 / 流通市值 / 名称：新浪全市场快照行自带（change_pct / nmc 万元）——零新增请求；
- 个股→行业归属：TDX board-list(HY) + board-members（免 Key，~50ms/板块，
  行业板块约 90 个），内存缓存 24h；失败时个股归入「未分类」并在响应里标注
  industry_coverage，不臆测。

聚合为纯函数（build_heatmap），组内只保留流通市值 Top N 个股、
其余并入「其他」块——5550 只全量渲染既看不清也拖垮交互。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_CACHE_TTL = 24 * 3600.0
#: 失败后的**重试冷却**（秒）。原实现用 `_industry_failed` 标志位一旦置位就
#: 在每个请求上短路返回，使下面 `_CACHE_TTL` 的冷却与重试分支**永不可达**——
#: 一次瞬时失败（TDX 抖动 / 网络断）会让本进程此后整个生命周期内
#: 全市场云图都落「未分类」，且看不出是坏在哪一天的哪一次失败。
_FAIL_COOLDOWN = 600.0
_industry_cache: dict[str, str] = {}
_industry_cached_at: float = 0.0
_industry_retry_after: float = 0.0

# 同步版 get_industry_map / invalidate_industry_cache 已删除（2026-09-07
# 健康度审查 C3：全仓 0 引用，路由只用 async 版）。


async def get_industry_map_async(provider=None) -> dict[str, str]:
    """个股 → 行业板块名（TDX HY 板块直连，24h 内存缓存；失败返回空映射由调用方降级）。"""
    global _industry_cache, _industry_cached_at, _industry_retry_after
    if _industry_cache:
        if time.time() - _industry_cached_at < _CACHE_TTL:
            return _industry_cache
    elif time.time() < _industry_retry_after:
        # 上次失败且在冷却期内：先不重试（避免每个请求都撞），但**不置永久失败**
        return {}

    import asyncio

    from easy_tdx import BoardType, MacClient

    def _sync() -> dict[str, str]:
        out: dict[str, str] = {}
        with MacClient() as client:
            boards = client.get_board_list(BoardType.HY)
            for _, b in boards.iterrows():
                industry = str(b.get("name") or "").strip()
                code = str(b.get("code") or "").strip()
                if not industry or not code:
                    continue
                try:
                    members = client.get_board_members(code)
                    for _, m in members.iterrows():
                        sym = str(m.get("code") or "").strip()
                        if len(sym) == 6 and sym.isdigit():
                            out[sym] = industry
                except Exception as exc:
                    log.warning("heatmap: board members %s(%s) failed: %s", industry, code, exc)
        return out

    try:
        _industry_cache = await asyncio.to_thread(_sync)
        _industry_cached_at = time.time()
        _industry_retry_after = 0.0
        log.info("heatmap industry map loaded: %d symbols", len(_industry_cache))
    except Exception as exc:
        # 已有的旧映射继续用（行业归属变化慢，陈旧映射远好于整场「未分类」）；
        # 完全无缓存时才靠 _industry_retry_after 做退避重试。
        _industry_retry_after = time.time() + _FAIL_COOLDOWN
        log.warning("heatmap industry map unavailable（%ds 后重试）: %s", int(_FAIL_COOLDOWN), exc)
    return _industry_cache


def build_heatmap(
    rows: list[dict],
    industry_map: dict[str, str],
    *,
    top_per_group: int = 12,
) -> dict:
    """全市场快照行 → 云图聚合（纯函数）。

    :param rows: snapshot 行（symbol/name/change_pct/nmc 万元/amount）
    :param industry_map: symbol → 行业名（空 = 全部归「未分类」）
    :param top_per_group: 每组保留流通市值 Top N，其余并入「其他」
    """
    groups: dict[str, list[dict]] = {}
    skipped = 0
    for r in rows:
        price = r.get("price")
        pct = r.get("change_pct")
        nmc = r.get("nmc")  # 万元
        if not price or price <= 0 or pct is None or not nmc or nmc <= 0:
            skipped += 1
            continue
        sym = r["symbol"]
        industry = industry_map.get(sym) or "未分类"
        groups.setdefault(industry, []).append({
            "symbol": sym,
            "name": r.get("name") or sym,
            "change_pct": round(pct, 2),
            "price": price,
            "float_cap_yi": round(nmc / 1e4, 2),  # 万元 → 亿
            "amount_yi": round((r.get("amount") or 0) / 1e8, 3),
        })

    out_groups = []
    total_up = total_down = total_flat = 0
    total_amount_yi = 0.0
    total_stocks = 0
    for industry, stocks in groups.items():
        stocks.sort(key=lambda s: -s["float_cap_yi"])
        top, rest = stocks[:top_per_group], stocks[top_per_group:]
        if rest:
            rest_cap = sum(s["float_cap_yi"] for s in rest)
            rest_amt = sum(s["amount_yi"] for s in rest)
            rest_pct = round(sum(s["change_pct"] * s["float_cap_yi"] for s in rest) / rest_cap, 2) if rest_cap else None
            top.append({
                "symbol": "", "name": f"其他({len(rest)}只)", "change_pct": rest_pct,
                "price": None, "float_cap_yi": round(rest_cap, 2), "amount_yi": round(rest_amt, 3),
                "is_aggregate": True,
            })
        for s in stocks:
            if s["change_pct"] > 0:
                total_up += 1
            elif s["change_pct"] < 0:
                total_down += 1
            else:
                total_flat += 1
            total_amount_yi += s["amount_yi"]
        total_stocks += len(stocks)
        out_groups.append({
            "industry": industry,
            "float_cap_yi": round(sum(s["float_cap_yi"] for s in stocks), 2),
            "change_pct_w": round(
                sum(s["change_pct"] * s["float_cap_yi"] for s in stocks)
                / max(sum(s["float_cap_yi"] for s in stocks), 1e-9), 2),
            "count": len(stocks),
            "stocks": top,
        })
    out_groups.sort(key=lambda g: -g["float_cap_yi"])

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": total_stocks,
        "skipped_no_quote": skipped,
        "industry_coverage": round(
            sum(1 for r in rows if industry_map.get(r.get("symbol") or ""))
            / max(len(rows), 1), 3) if rows else 0.0,
        "breadth_summary": {"up": total_up, "down": total_down, "flat": total_flat},
        "total_amount_yi": round(total_amount_yi, 1),
        "groups": out_groups,
    }
