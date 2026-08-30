"""分钟历史回拉（回测底座，docs/minute-chart-plan.md 模块 4.3）。

数据源边界（2026-08-30 实测）：
- 腾讯 mkline：单次 320 根封顶、**无翻页能力**（偏移参数返回 0 bars）
  → 1 分钟历史最深 1.3 天，60 日不可得；
- 新浪 CN_MarketDataService 5 分钟 K：datalen=1023 → **22 个交易日**；
- **TDX 协议（easy-tdx，本轮接入）**：5 分钟 **495 交易日（约 2 年）**、
  1 分钟 **94 交易日（4.5 个月）**——回测底座主源；免费、免 Key、vol 单位=股
  （与腾讯口径一致，实测 1,612,600 vs 1,613,900 股）；`Adjust.QFQ` 内置前复权
  （茅台 6/26 除权实测：NONE 1212.10 vs QFQ 1184.08，衔接正确）。

落地：
- sina 路径：5 分钟 → `data/parquet/minutes/{symbol}.parquet`（未复权，回测时用
  ths 复权事件流修正）；
- tdx 路径：分钟 K（QFQ）→ `data/parquet/minutes-tdx/{symbol}.parquet`
  （**已前复权**，回测标记 adjusted=True 跳过事件流修正，避免双重调整）。
两者均为引擎分钟点 schema（含 cum_volume/avg，口径与 /api/minute-line 一致）。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.services.parquet_store import read_parquet_safe, write_parquet_atomic

log = logging.getLogger(__name__)

BJ_OFFSET = timedelta(hours=8)
SINA_URL = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/"
            "CN_MarketDataService.getKLineData?symbol={symbol}&scale=5&ma=no&datalen=1023")

parquet_dir_default = Path(__file__).resolve().parents[3] / "data" / "parquet" / "minutes"
parquet_dir_tdx = Path(__file__).resolve().parents[3] / "data" / "parquet" / "minutes-tdx"


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
                # 原子写：进程被 kill 会留下"大小正常但内容损坏"的 parquet
                write_parquet_atomic(pl.DataFrame(pts), out / f"{sym}.parquet")
                result[sym] = len(pts)
                log.info("backfill %s: %d points", sym, len(pts))
            except Exception as exc:
                failures[sym] = str(exc)[:120]
                log.warning("backfill %s failed: %s", sym, exc)
    result["_failures"] = failures  # type: ignore[assignment]
    return result


def load_symbol(out_dir: Path | None, symbol: str) -> list[dict]:
    """读回 Parquet → 引擎点列表（按 ts 升序）。

    文件不存在**或损坏**都返回 []：分钟缓存是可选的，量比基线拿不到就退化，
    不该让一个坏文件把 /api/minute-line 整个打挂。
    """
    path = (out_dir or parquet_dir_default) / f"{symbol}.parquet"
    df, err = read_parquet_safe(path)
    if df is None:
        if err and "不存在" not in err:
            log.warning("minutes parquet 不可读，按无数据处理：%s | %s", path, err)
        return []
    rows = df.to_dicts()
    rows.sort(key=lambda r: r["ts"])
    return rows


# ================================================================ TDX（easy-tdx）

def _tdx_market(symbol: str):
    """6 位裸码 → easy-tdx Market 枚举。"""
    from easy_tdx import Market

    return Market.SH if symbol[0] in "69" else Market.SZ


def tdx_row_to_points(df, source: str = "tdx") -> list[dict]:
    """TDX 分钟 K DataFrame → 引擎分钟点 schema（纯函数，可单测）。

    量纲实测：vol 单位=股（与腾讯一致，600519 同日总量 1,612,600 vs 1,613,900）。
    QFQ 由数据源内置（Adjust.QFQ），此处不做二次复权。
    """
    import pandas as pd

    points: list[dict] = []
    cum_vol = 0
    cum_amt = 0.0
    cur_day = None
    for idx, row in df.iterrows():
        # 双形态兼容：get_stock_kline 返回平表（datetime 为列）；容忍 datetime 作索引的形态
        raw_ts = row["datetime"] if "datetime" in df.columns else idx
        ts_val = pd.to_datetime(raw_ts)
        day = ts_val.strftime("%Y-%m-%d")
        if day != cur_day:
            cur_day = day
            cum_vol = 0
            cum_amt = 0.0
        vol = float(row["vol"])
        amt = float(row["amount"])
        cum_vol += vol
        cum_amt += amt
        # df.datetime 已是北京时间 naive Timestamp；编码伪 UTC 与 minute-line 口径一致
        ts = (ts_val.to_pydatetime().replace(tzinfo=timezone.utc) - BJ_OFFSET).isoformat()
        points.append({
            "ts": ts,
            "price": round(float(row["close"]), 4),
            "volume": vol,
            "cum_amount": round(cum_amt, 2),
            "cum_volume": int(cum_vol),
            "avg": round(cum_amt / cum_vol, 3) if cum_vol else None,
            "source": source,
        })
    return points


def fetch_tdx_minutes(symbol: str, *, period: str = "5min", count: int = 24000) -> list[dict]:
    """TDX 拉分钟 K（同步阻塞，分页+QFQ 内置）→ 引擎分钟点 schema。

    period: "1min"（≈94 交易日）/ "5min"（≈495 交易日，约 2 年）。
    QFQ：数据源已前复权——回测侧须以 adjusted=True 语义消费，勿再叠加事件流修正。
    """
    from easy_tdx import Adjust, MacClient, Period

    period_map = {"1min": Period.MIN_1, "5min": Period.MIN_5}
    with MacClient() as client:
        df = client.get_stock_kline(
            _tdx_market(symbol).value, symbol,
            period=period_map[period], start=0, count=count, adjust=Adjust.QFQ,
        )
    if df is None or df.empty:
        raise ValueError(f"tdx kline empty for {symbol}")
    return tdx_row_to_points(df)


async def backfill_tdx(
    symbols: list[str],
    out_dir: Path | None = None,
    *,
    period: str = "5min",
    count: int = 24000,
) -> dict:
    """TDX 批量回拉（asyncio.to_thread 包同步 TCP IO）并落独立 Parquet。

    返回 {symbol: 点数}；失败 symbol 记入 `_failures`（与 sina backfill 同契约）。
    """
    import asyncio

    import polars as pl

    out = out_dir or parquet_dir_tdx
    out.mkdir(parents=True, exist_ok=True)
    result: dict[str, int] = {}
    failures: dict[str, str] = {}
    for sym in symbols:
        try:
            pts = await asyncio.to_thread(fetch_tdx_minutes, sym, period=period, count=count)
            write_parquet_atomic(pl.DataFrame(pts), out / f"{sym}.parquet")
            result[sym] = len(pts)
            log.info("tdx backfill %s: %d points", sym, len(pts))
        except Exception as exc:
            failures[sym] = str(exc)[:120]
            log.warning("tdx backfill %s failed: %s", sym, exc)
    result["_failures"] = failures  # type: ignore[assignment]
    return result


def load_vr_baseline(symbol: str, days: int = 5, out_dir: Path | None = None) -> list[float] | None:
    """精确量比基线（分时计划遗留②）：当日之前 N 个完整交易日的逐 bar 累计量均值。

    从 minutes-tdx parquet 读（量纲不受复权影响）。返回按 bar 序号对齐的
    同期累计量列表（N 日均，调用方按 slot 索引取用）；数据不足返回 None。
    **基线不含当日**——标准量比定义的分母是"过去 N 日"，含当日会被自身稀释。
    """
    rows = load_symbol(out_dir or parquet_dir_tdx, symbol)
    if not rows:
        return None
    by_day: dict[str, list[int]] = {}
    for r in rows:
        d = (datetime.fromisoformat(r["ts"]) + BJ_OFFSET).strftime("%Y%m%d")
        by_day.setdefault(d, []).append(int(r["cum_volume"]))
    all_days = sorted(by_day, reverse=True)
    if len(all_days) < days + 1:
        return None  # 不足 N+1 天（N 基线日 + 当日），无法构成同期基线
    complete = all_days[1:days + 1]  # 跳过最新一天（=当日），取其前 N 个完整日
    lengths = [len(by_day[d]) for d in complete]
    n = min(lengths)
    if n < 12:  # 完整日至少应有 48 bar（5m）；过短视为停牌/异常日
        return None
    baseline = [sum(by_day[d][i] for d in complete) / days for i in range(n)]
    return baseline
