"""行情源共同请求的基准指数；保留市场身份，不能以股票撞码补齐。"""

INDEX_CATALOG = (
    ("000001", "SH", "上证指数"),
    ("399001", "SZ", "深证成指"),
    ("399006", "SZ", "创业板指"),
    ("000688", "SH", "科创50"),
    ("000300", "SH", "沪深300"),
    ("000852", "SH", "中证1000"),
)
INDEX_MARKETS = {symbol: market for symbol, market, _ in INDEX_CATALOG}
INDEX_SUBSCRIPTIONS = {market.lower() + symbol for symbol, market, _ in INDEX_CATALOG}
