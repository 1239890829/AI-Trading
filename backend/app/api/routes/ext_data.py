"""扩展数据路由（star 仓库整合，2026-09-07）：akshare 可用性 + 涨跌停池交叉校验。

交叉校验的价值（口径差异是已知特性而非 bug，见 docs/data-source-comparison.md）：
- 涨停家数存在多口径（breadth 收盘价落限价口径 / ths 涨停池 / 东财 push2ex 池），
  差 2~4 家正常；akshare（东财 push2ex）作为**独立第二源**，与 composite 链
  （ths 主、东财备）互为校验，单源静默缺数据时差异立即可见。
- 输出必须标注口径（用户定稿纪律），diff 集合给出具体代码而非只有计数。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_hub
from app.services.akshare_ext import AkshareExtError, AkshareExtService, get_akshare_ext
from app.services.quote_hub import QuoteHub

router = APIRouter(tags=["ext-data"])


def _symbols(records: list) -> dict[str, dict]:
    """composite 记录 → {symbol: 精简字段}（LimitUpRecord/LimitDownRecord 均有 symbol/name）。"""
    out = {}
    for r in records:
        dump = r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r)
        sym = dump.get("symbol")
        if sym:
            out[sym] = {"name": dump.get("name"), "reason": dump.get("reason")}
    return out


def _diff(composite_syms: dict, ak_syms: dict) -> dict:
    only_c = sorted(set(composite_syms) - set(ak_syms))
    only_a = sorted(set(ak_syms) - set(composite_syms))
    return {
        "only_in_composite": [{"symbol": s, "name": composite_syms[s].get("name")} for s in only_c],
        "only_in_akshare": [{"symbol": s, "name": ak_syms[s].get("name")} for s in only_a],
    }


@router.get("/ext/akshare/status")
async def akshare_status(service: AkshareExtService = Depends(get_akshare_ext)) -> dict:
    """akshare 可用性探测（不发起外部请求，仅探测本地导入）。"""
    return {"data": service.status()}


@router.get("/ext/akshare/pool-crosscheck")
async def akshare_pool_crosscheck(
    date_str: str = Query(alias="date", description="YYYY-MM-DD"),
    hub: QuoteHub = Depends(get_hub),
    service: AkshareExtService = Depends(get_akshare_ext),
) -> dict:
    """涨停/跌停池双源交叉校验：composite 链（ths 主/东财备）vs akshare（东财 push2ex）。"""
    try:
        trade_date = date.fromisoformat(date_str)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"日期格式非法：{date_str}（需 YYYY-MM-DD）") from exc

    try:
        composite_up = await hub.provider.get_limit_up_pool(trade_date)
        composite_down = await hub.provider.get_limit_down_pool(trade_date)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"composite 涨跌停池数据源失败：{exc}") from exc

    try:
        ak_up = await service.limit_up_pool(trade_date)
        ak_down = await service.limit_down_pool(trade_date)
    except AkshareExtError as exc:
        # 显式降级：composite 侧照常返回，akshare 侧带 kind 与 hint —— 不静默装作"两源一致"
        return {
            "data": {
                "trade_date": trade_date.isoformat(),
                "akshare": {"available": False, "kind": exc.kind, "detail": exc.detail},
                "composite": {
                    "up_count": len(composite_up),
                    "down_count": len(composite_down),
                },
                "note": "akshare 侧不可用，本次仅 composite 单源；口径见 docs/data-source-comparison.md",
            }
        }

    c_up_syms = _symbols(composite_up)
    a_up_syms = {r["symbol"]: {"name": r.get("name")} for r in ak_up}
    c_dn_syms = _symbols(composite_down)
    a_dn_syms = {r["symbol"]: {"name": r.get("name")} for r in ak_down}

    return {
        "data": {
            "trade_date": trade_date.isoformat(),
            "akshare": {"available": True},
            "composite": {"up_count": len(composite_up), "down_count": len(composite_down)},
            "akshare_counts": {"up_count": len(ak_up), "down_count": len(ak_down)},
            "up_diff": _diff(c_up_syms, a_up_syms),
            "down_diff": _diff(c_dn_syms, a_dn_syms),
            "note": "composite=ths 主源/东财备源；akshare=东财 push2ex 独立第二源。两口径差 2~4 家属正常（收录口径差异），持续偏离才需排查。",
        }
    }


@router.get("/ext/akshare/zt-previous")
async def akshare_zt_previous(
    date_str: str = Query(alias="date", description="YYYY-MM-DD（昨日涨停的**今日表现**查询日=今天）"),
    service: AkshareExtService = Depends(get_akshare_ext),
) -> dict:
    """昨日涨停今日表现（东财 push2ex，昨日封板时间/连板数/涨速）。

    复盘「昨涨停溢价」的第二口径交叉源：主口径 = 本系统快照+涨停池推导；
    本接口独立取数，两口径不一致时显式呈现（不静默择一）。
    """
    try:
        trade_date = date.fromisoformat(date_str)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"日期格式非法：{date_str}（需 YYYY-MM-DD）") from exc
    try:
        rows = await service.zt_pool_previous(trade_date)
    except AkshareExtError as exc:
        return {
            "data": {
                "trade_date": trade_date.isoformat(),
                "available": False,
                "kind": exc.kind,
                "detail": exc.detail,
            }
        }
    # 溢价汇总：均值/中位数/翻红率（change_pct 为今日涨跌幅 %）
    pcts = [r.get("change_pct") for r in rows if isinstance(r.get("change_pct"), (int, float))]
    summary = {}
    if pcts:
        srt = sorted(pcts)
        summary = {
            "count": len(pcts),
            "mean_pct": round(sum(pcts) / len(pcts), 2),
            "median_pct": round(srt[len(srt) // 2], 2),
            "red_ratio": round(sum(1 for p in pcts if p > 0) / len(pcts), 3),
        }
    return {
        "data": {
            "trade_date": trade_date.isoformat(),
            "available": True,
            "summary": summary,
            "rows": rows,
            "note": "东财 push2ex 口径；与系统主口径（快照+涨停池推导）互为交叉校验。",
        }
    }
