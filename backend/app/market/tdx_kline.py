"""TDX 日K公共拉取（QFQ，easy_tdx）。

从 screener_service 提取的公共函数：回测引擎与选股器共用同一数据路径，
保证两处看到的前复权序列逐位一致。

缓存（调研采纳：回测数据加载缓存）：bars 拉取走 MacClient IPC，回测/参数
网格在同一标的同根数上反复回源是纯浪费。TTLCache 统一约定：容量有界
（(symbol, count) 组合有限）、失败/None 不缓存（下次重试）、可 invalidate。
"""
from __future__ import annotations

from app.core.ttl_cache import TTLCache

_BARS_CACHE = TTLCache("tdx-daily-bars", ttl=300.0, maxsize=64)


def tdx_daily_bars(symbol: str, count: int = 500, *, use_cache: bool = True) -> list[dict] | None:
    """TDX 日K（QFQ）→ [{ts, open, high, low, close, volume}]（升序）。

    失败返回 None（调用方决定降级/跳过）；None 不入缓存。
    market 参数必须是整数枚举值（Market.SH.value / Market.SZ.value）——
    传字符串会报 "required argument is not an integer"（选股器首跑全失败的实锤）。
    """
    key = (symbol, int(count))
    if use_cache:
        hit, cached = _BARS_CACHE.get(key)
        if hit:
            # 浅拷贝：防调用方原地改 list（增删 bar）污染共享缓存；bar dict 仍共享（约定只读）
            return list(cached)
    bars = _fetch_daily_bars(symbol, count)
    if use_cache and bars is not None:
        _BARS_CACHE.set(key, bars)
        return list(bars)  # miss 分支同样返回浅拷贝（防调用方误改污染缓存）
    return bars


def invalidate_bars_cache(symbol: str | None = None) -> None:
    """清缓存（symbol=None 全清；回测口径调整后调用方主动失效）。"""
    _BARS_CACHE.invalidate(symbol)


def _fetch_daily_bars(symbol: str, count: int) -> list[dict] | None:
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
