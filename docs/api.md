# REST API

Base URL：`http://127.0.0.1:8000`（`/api` 前缀）。统一响应：`{"data": ..., "meta": {...}}`，
meta 含 `provider / is_realtime / is_stale / last_success_refresh / generated_at`。
数据源失败返回 **HTTP 502**（前端显示错误态，绝不降级伪造）。

## 已实现（Phase 1-2）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查：provider、last_success_refresh、consecutive_failures、last_error、is_stale |
| GET | `/api/market/overview` | 六大指数 + 两市成交额合计 |
| GET | `/api/quotes?symbols=600519,000001` | 缓存行情批量；缺省返回全部自选 |
| GET | `/api/quotes/{symbol}` | 单只（缓存） |
| GET | `/api/kline/{symbol}?timeframe=1d&limit=250&start=&end=` | K 线，timeframe: 1m/5m/15m/30m/60m/1d/1w |
| GET | `/api/order-book/{symbol}` | 五档盘口（经交叉校验） |
| GET | `/api/trades/{symbol}?limit=50` | 逐笔成交 |
| GET | `/api/limit-up?date=YYYY-MM-DD` | 涨停池（按连板数排序） |
| GET | `/api/longhu?date=YYYY-MM-DD` | 龙虎榜（按净买额排序） |
| GET | `/api/search?q=` | 股票搜索（eastmoney suggest，失败回退内置词表，source 标注） |
| GET/POST/DELETE | `/api/watchlist[/{symbol}]` | 自选股 CRUD（SQLite） |

## 规划（按开发顺序 §23）

Phase 3：`/api/market/breadth` `/api/market/sentiment` `/api/boards`
Phase 4：`/api/capital-flow` `/api/news` `/api/announcements` `/api/financials/{symbol}`
`/api/valuation/{symbol}` `/api/shareholders/{symbol}` `/api/margin/{symbol}`
Phase 5：`POST /api/screeners/run`（评分输出统一结构 §10）
Phase 6：`GET/POST /api/backtests`、`/api/backtests/{id}`、`/api/paper/orders`、`/api/paper/account`、`/api/paper/positions`
Phase 7：`POST /api/research/analyze`（多 Agent 流水线）、`/api/audit`
Phase 8：`POST /api/alerts`

错误约定：`4xx` 参数/资源问题；`502` 上游数据源失败（body.detail 含原因）；`503` 未实现占位。
