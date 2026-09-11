"""个股级资金流（策略进化 P1 方向 2：大单异动进 watcher 信号集）。

## 数据源与复用

东财 push2 ulist（与 fund_flow.py 大盘口径**同一端点**，secids 从指数换成
个股「市场前缀.代码」）。复用 fund_flow 的 http 客户端/数值解析/五档键名，
避免同源逻辑两处漂移。

## 口径纪律（红线：不虚构）

- f62/f66/f72/f78/f84 = **当日累计**净额（元→亿），分钟级增量由消费方
  （watcher）两次拍差分得出，本模块不做差分；
- **北交所（8/4/920 开头）东财无个股资金流数据** → 进 `no_data` 显式列出，
  绝不混入 available 集合冒充成功；
- 空响应/异常重试 2 次（东财间歇空响应，实测 ~1/3 概率）；失败不缓存。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.core.bjtime import BJ_TZ
from app.core.ttl_cache import TTLCache
from app.market.fund_flow import _FLOW_KEYS, _em_num, _http

log = logging.getLogger(__name__)

#: 单次查询标的数上限（ulist 一次请求；watcher 一拍题材成员远小于此）
MAX_SYMBOLS = 60

_CACHE = TTLCache("stock-flow", ttl=30.0, maxsize=8)

#: f184 主力净占比（%）、f124 更新时间戳（秒）
_STOCK_FIELDS = ("f12", "f14", "f62", "f66", "f72", "f78", "f84", "f184", "f124")


def stock_secid(symbol: str) -> str | None:
    """A 股代码 → 东财 secid；北交所/非法代码 → None（无个股资金流数据）。"""
    sym = str(symbol or "").strip()
    if not sym.isdigit() or len(sym) != 6:
        return None
    if sym.startswith("6"):
        return f"1.{sym}"
    if sym.startswith(("0", "3")):
        return f"0.{sym}"
    return None


def _pct(v) -> float | None:
    """f184 主力净占比是**百分数**，不走 _em_num（那是金额口径 /1e8）。"""
    if v in (None, "-", ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_stock_flow(rows: list[dict]) -> dict[str, dict]:
    """ulist diff 行 → {code: {name, main/super_/big/mid/small, main_pct, as_of, available}}。

    available=False = 行存在但五档全 None（停牌/无数据），不臆造。
    """
    out: dict[str, dict] = {}
    for row in rows or []:
        code = str(row.get("f12", ""))
        if not code.isdigit() or len(code) != 6:
            continue
        vals = {k: _em_num(row.get(f)) for f, k in _FLOW_KEYS.items()}
        ts = row.get("f124")
        out[code] = {
            "name": row.get("f14"),
            **vals,
            "main_pct": _pct(row.get("f184")),
            "as_of": datetime.fromtimestamp(ts, BJ_TZ).strftime("%H:%M:%S") if ts else None,
            "available": any(v is not None for v in vals.values()),
        }
    return out


async def get_stock_flow(symbols: list[str]) -> dict:
    """批量个股资金流（当日累计五档净额，亿元）。

    :return: {items: {code: {...}}, no_data: [代码], as_of, degraded: []}
             全部失败时 items={} 且 degraded 非空（三态，绝不填 0）。
    """
    syms: list[str] = []
    for s in symbols or []:
        sym = str(s or "").strip()
        if sym.isdigit() and len(sym) == 6 and sym not in syms:
            syms.append(sym)
    syms = syms[:MAX_SYMBOLS]
    if not syms:
        return {"items": {}, "no_data": [], "as_of": None, "degraded": ["symbols 为空"]}

    key = tuple(syms)
    hit, cached = _CACHE.get(key)
    if hit:
        return cached

    secids = [(s, stock_secid(s)) for s in syms]
    query = ",".join(sec for _, sec in secids if sec is not None)
    no_data = [s for s, sec in secids if sec is None]  # 北交所等：源无数据，显式列出
    rows: list[dict] | None = None
    for attempt in range(3):
        try:
            resp = await _http().get(
                "https://push2.eastmoney.com/api/qt/ulist.np/get",
                params={"fltt": 2, "secids": query, "fields": ",".join(_STOCK_FIELDS)},
                headers={"Referer": "https://quote.eastmoney.com/"},
            )
            resp.raise_for_status()
            diff = ((resp.json().get("data") or {}).get("diff") or [])
            if diff:
                rows = diff
                break
            raise ValueError("empty diff")
        except Exception as exc:  # noqa: BLE001
            log.warning("stock flow ulist attempt %s failed: %s", attempt + 1, exc)
            await asyncio.sleep(1.0)

    if rows is None:
        out = {"items": {}, "no_data": no_data, "as_of": None,
               "degraded": ["东财个股资金流不可用（已重试）"]}
        return out  # 失败不缓存

    items = parse_stock_flow(rows)
    # 请求了但响应里没有的（除北交所等已知无数据外）也归入 no_data，可见性优先
    returned = set(items)
    no_data += [s for s, sec in secids if sec is not None and s not in returned]
    as_of = next((it["as_of"] for it in items.values() if it.get("as_of")), None)
    out = {"items": items, "no_data": no_data, "as_of": as_of, "degraded": []}
    _CACHE.set(key, out)
    return out
