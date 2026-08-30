"""分钟历史回拉（回测底座，docs/minute-chart-plan.md 模块 4.3）。

数据源边界（2026-08-30 实测）：
- 腾讯 mkline：单次 320 根封顶、**无翻页能力**（偏移参数返回 0 bars）
  → 1 分钟历史最深 1.3 天，60 日不可得；
- 新浪 CN_MarketDataService 5 分钟 K：datalen=1023 → **22 个交易日**，
  是免费渠道能拿到的最深分钟历史；
- 60 日 1 分钟需 miniQMT / 掘金（见 docs/orderbook-source-evaluation.md）——
  数据深度受限是数据源硬边界，本模块按可得数据显式降级，不臆造。

落地：新浪 5 分钟 K → 引擎分钟点 schema（含 cum_volume/avg，口径与
/api/minute-line 一致）→ Parquet `data/parquet/minutes/{symbol}.parquet`。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

BJ_OFFSET = timedelta(hours=8)
SINA_URL = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/"
            "CN_MarketDataService.getKLineData?symbol={symbol}&scale=5&ma=no&datalen=1023")

parquet_dir_default = Path(__file__).resolve().parents[3] / "data" / "parquet" / "minutes"


def to_sina_symbol(symbol: str) -> str:
    """6 位裸码 → 新浪带前缀代码（沪深；北交所未验证，回测池不含）。"""
    if symbol[0] in "69":
        return f"sh{symbol}"
    return f"sz{symbol}"


def strip_jsonp(raw: str) -> list:
    """剥掉新浪 JSONP 包装（``var _data=([...]);`` + 前置 script 注释）。"""
    m = re.search(r"\((.*)\)\s*;?\s*$", raw, re.S)
    if not m:
        raise ValueError("sina kline: JSONP 剥离失败")
    return json.loads(m.group(1))


async def fetch_sina_m5(client: httpx.AsyncClient, symbol: str) -> list[dict]:
    """拉新浪 5 分钟 K → 引擎分钟点 schema（volume 股，avg=累计额/累计量）。"""
    sym = to_sina_symbol(symbol)
    resp = await client.get(SINA_URL.format(symbol=sym))
    resp.raise_for_status()
    rows = strip_jsonp(resp.text)
    if not rows:
        raise ValueError(f"sina kline empty for {symbol}")

    points: list[dict] = []
    cum_vol = 0
    cum_amt = 0.0
    cur_day = None
    # 新浪 5 分钟 bar 的 volume/amount 是**本 bar**口径（茅台实测 5.9 万股/5min 合理）；
    # avg/cum_volume 按"交易日"重置累计——跨日累计会让均价线失真。
    for r in rows:
        day = r["day"][:10]
        if day != cur_day:
            cur_day = day
            cum_vol = 0
            cum_amt = 0.0
        price = float(r["close"])
        vol = float(r["volume"])
        amt = float(r["amount"])
        cum_vol += vol
        cum_amt += amt
        # day 字段是北京时间，编码成伪 UTC 与 /api/minute-line 的 ts 口径一致
        ts = (datetime.strptime(r["day"], "%Y-%m-%d %H:%M:%S")
              .replace(tzinfo=timezone.utc) - BJ_OFFSET).isoformat()
        points.append({
            "ts": ts,
            "price": price,
            "volume": vol,
            "cum_amount": round(cum_amt, 2),
            "cum_volume": int(cum_vol),
            "avg": round(cum_amt / cum_vol, 3) if cum_vol else None,
            "source": "sina_m5",
        })
    return points


async def backfill(symbols: list[str], out_dir: Path | None = None) -> dict:
    """批量回拉并落 Parquet。返回 {symbol: 点数}，失败的 symbol 记录并跳过。"""
    import polars as pl

    out = out_dir or parquet_dir_default
    out.mkdir(parents=True, exist_ok=True)
    result: dict[str, int] = {}
    failures: dict[str, str] = {}
    async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
        for sym in symbols:
            try:
                pts = await fetch_sina_m5(client, sym)
                pl.DataFrame(pts).write_parquet(out / f"{sym}.parquet")
                result[sym] = len(pts)
                log.info("backfill %s: %d points", sym, len(pts))
            except Exception as exc:
                failures[sym] = str(exc)[:120]
                log.warning("backfill %s failed: %s", sym, exc)
    result["_failures"] = failures  # type: ignore[assignment]
    return result


def load_symbol(out_dir: Path | None, symbol: str) -> list[dict]:
    """读回 Parquet → 引擎点列表（按 ts 升序）。"""
    import polars as pl

    path = (out_dir or parquet_dir_default) / f"{symbol}.parquet"
    if not path.exists():
        return []
    df = pl.read_parquet(path)
    rows = df.to_dicts()
    rows.sort(key=lambda r: r["ts"])
    return rows
