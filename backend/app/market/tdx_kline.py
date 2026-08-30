"""TDX 日K公共拉取（QFQ，easy_tdx）。

从 screener_service 提取的公共函数：回测引擎与选股器共用同一数据路径，
保证两处看到的前复权序列逐位一致。
"""
from __future__ import annotations


def tdx_daily_bars(symbol: str, count: int = 500) -> list[dict] | None:
    """TDX 日K（QFQ）→ [{ts, open, high, low, close, volume}]（升序）。

    失败返回 None（调用方决定降级/跳过）。
    market 参数必须是整数枚举值（Market.SH.value / Market.SZ.value）——
    传字符串会报 "required argument is not an integer"（选股器首跑全失败的实锤）。
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
