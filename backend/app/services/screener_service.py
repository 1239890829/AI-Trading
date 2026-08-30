"""全市场选股器服务（Phase 5）：快照截面过滤 → TDX 日K → 技术评分卡。

数据链：
1. 快照 Parquet（新浪全市场，~5550 只）做截面硬过滤（流动性/涨幅带/排除 ST 与北交所）；
2. 候选池（按成交额 Top N）逐只拉 TDX 日K（QFQ，250 根，easy_tdx 免 Key）；
3. tech_score.score_stock 出可解释评分卡；按分排序输出。

失败纪律：单只日K拉取失败 → 跳过并计入 failed，不臆造评分；
TDX 整体不可用 → 404 语义交由路由（code=tdx_unavailable），不返回半假结果。
缓存：结果按参数组合 TTL 30 分钟（评分随日K日频变化，无需更短），
single-flight 防同 key 并发重复计算。
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.screener import ScreenerItem, ScreenerPayload, ScreenerSignal
from app.market.tech_score import SCORER_VERSION, score_stock

log = logging.getLogger(__name__)

CACHE_TTL = 1800  # 秒
DEFAULT_POOL_LIMIT = 150  # 进入日K评分的候选上限（TDX 拉取耗时 ~150×100ms）
BJ_PREFIXES = ("43", "83", "87", "92")  # 北交所代码前缀（科创板 688 不在此列）


def load_snapshot_rows(parquet_dir: Path) -> tuple[list[dict], str | None]:
    """最新快照全列行 + 数据时点（ticktime）。找不到快照返回空。"""
    import polars as pl

    base = parquet_dir / "snapshots"
    if not base.exists():
        return [], None
    for day_dir in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
        files = sorted(day_dir.glob("*.parquet"))
        if not files:
            continue
        df = pl.read_parquet(files[-1])
        tick = None
        if "ticktime" in df.columns:
            tick = df["ticktime"].drop_nulls()[-1] if df["ticktime"].drop_nulls().len() else None
        return df.to_dicts(), str(tick) if tick is not None else None
    return [], None


def filter_universe(
    rows: list[dict],
    *,
    change_low: float,
    change_high: float,
    min_amount_yi: float,
    min_turnover: float,
    exclude_st: bool,
    exclude_bj: bool,
    pool_limit: int,
) -> list[dict]:
    """截面硬过滤 + 按成交额取候选池。纯函数便于测试。"""
    out: list[dict] = []
    for r in rows:
        price = r.get("price") or 0
        if price <= 0:  # 停牌/无效
            continue
        name = str(r.get("name") or "")
        if exclude_st and ("ST" in name.upper() or "退" in name):
            continue
        sym = str(r.get("symbol") or "").zfill(6)
        if exclude_bj and sym.startswith(BJ_PREFIXES):
            continue
        pct = r.get("change_pct")
        if pct is None or not (change_low <= pct <= change_high):
            continue
        amount = r.get("amount") or 0
        if amount < min_amount_yi * 1e8:
            continue
        turnover = r.get("turnover_rate")
        if turnover is None or turnover < min_turnover:
            continue
        out.append({**r, "symbol": sym, "name": name})
    out.sort(key=lambda r: r.get("amount") or 0, reverse=True)
    return out[:pool_limit]


def _tdx_daily_bars(symbol: str, count: int = 250) -> list[dict] | None:
    """TDX 日K（QFQ）→ tech_score bars；失败返回 None（调用方跳过）。

    market 参数必须是整数枚举值（Market.SH.value / Market.SZ.value）——
    传字符串会报 "required argument is not an integer"（首跑全 150 只失败的实锤）。
    """
    from easy_tdx import Adjust, MacClient, Market, Period

    market = (Market.SH if symbol[0] in "69" else Market.SZ).value
    with MacClient() as client:
        df = client.get_stock_kline(
            market, symbol, period=Period.DAILY, start=0, count=count, adjust=Adjust.QFQ
        )
    if df is None or len(df) == 0:
        return None
    bars: list[dict] = []
    for _, row in df.iterrows():
        raw_ts = row["datetime"] if "datetime" in df.columns else row.name
        bars.append({
            "ts": str(raw_ts),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume") or 0),
        })
    return bars


def _rank_pct(values: list[float], v: float) -> float:
    if not values:
        return 0.5
    return sum(1 for x in values if x <= v) / len(values)


def build_payload(
    rows: list[dict],
    *,
    change_low: float,
    change_high: float,
    min_amount_yi: float,
    min_turnover: float,
    exclude_st: bool,
    exclude_bj: bool,
    exclude_new: bool,
    limit: int,
    pool_limit: int = DEFAULT_POOL_LIMIT,
) -> ScreenerPayload:
    """全流程（同步，含 TDX 拉取）。调用方负责放线程 + 缓存。"""
    pool = filter_universe(
        rows,
        change_low=change_low, change_high=change_high,
        min_amount_yi=min_amount_yi, min_turnover=min_turnover,
        exclude_st=exclude_st, exclude_bj=exclude_bj,
        pool_limit=pool_limit,
    )
    amounts = [float(r.get("amount") or 0) for r in pool]
    turnovers = [float(r.get("turnover_rate") or 0) for r in pool]

    # 一次连接批量拉日K（同步）；单只失败跳过
    items: list[ScreenerItem] = []
    failed = 0
    for r in pool:
        sym = r["symbol"]
        try:
            bars = _tdx_daily_bars(sym)
        except Exception as exc:
            log.warning("screener: daily bars failed for %s: %s", sym, exc)
            bars = None
        if not bars:
            failed += 1
            continue
        if exclude_new and len(bars) < 60:
            continue  # 次新/长停牌：样本不足，直接排除
        scored = score_stock(
            bars,
            amount_rank_pct=_rank_pct(amounts, float(r.get("amount") or 0)),
            turnover_rank_pct=_rank_pct(turnovers, float(r.get("turnover_rate") or 0)),
        )
        if scored is None:
            if exclude_new:
                continue
            failed += 1
            continue
        items.append(ScreenerItem(
            symbol=sym,
            name=r.get("name") or sym,
            price=float(r.get("price") or 0),
            change_pct=float(r.get("change_pct") or 0),
            turnover_rate=float(r.get("turnover_rate")) if r.get("turnover_rate") is not None else None,
            amount_yi=round(float(r.get("amount") or 0) / 1e8, 2),
            float_cap_yi=round(float(r["nmc"]) / 1e4, 1) if r.get("nmc") else None,
            score=scored["score"],
            grade=scored["grade"],
            bias=scored["bias"],
            signals=[ScreenerSignal(**s) for s in scored["signals"]],
            summary=scored["summary"],
            fail_conditions=scored["fail_conditions"],
        ))

    items.sort(key=lambda x: x.score, reverse=True)
    return ScreenerPayload(
        items=items[:limit],
        scanned=len(rows),
        filtered=len(pool),
        scored=len(items),
        failed=failed,
        snapshot_time=None,  # 路由层填
        computed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        scorer_version=SCORER_VERSION,
        disclaimers=[
            "评分为多因子技术共振强度，仅描述技术面状态，不构成买卖建议",
            "评分基于 TDX 前复权日K与截面流动性分位，日频更新",
        ],
    )


class ScreenerService:
    """带 TTL 缓存与 single-flight 的选股器入口。"""

    def __init__(self, parquet_dir: Path):
        self.parquet_dir = parquet_dir
        self._cache: dict[tuple, tuple[float, ScreenerPayload]] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}

    def _cache_key(self, **kw) -> tuple:
        return tuple(sorted(kw.items()))

    async def run(self, **params) -> ScreenerPayload:
        key = self._cache_key(**params)
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            payload = hit[1]
            payload.cached = True
            return payload
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < CACHE_TTL:
                payload = hit[1]
                payload.cached = True
                return payload
            rows, tick = await asyncio.to_thread(load_snapshot_rows, self.parquet_dir)
            if not rows:
                raise RuntimeError("snapshot unavailable")
            payload = await asyncio.to_thread(build_payload, rows, **params)
            payload.snapshot_time = tick
            self._cache[key] = (time.monotonic(), payload)
            return payload
