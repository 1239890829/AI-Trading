# REST API

Base URL：`http://127.0.0.1:8000`（`/api` 前缀）。**82 个端点**（2026-08-31 与代码同步）。

统一响应：`{"data": ..., "meta": {...}}`（Envelope[T]，meta 含 `provider / is_realtime / is_stale / last_success_refresh / generated_at`）。
数据源失败返回 **HTTP 502**（前端显示错误态，绝不降级伪造）；错误统一契约 `{detail, code}`。

## 鉴权（B6 opt-in）

写端点（下表标 🔒）在 `ASHARE_API_TOKEN` 配置后要求 `X-API-Token` 头（或 `?token=`）；
后端未配置 token = 全放行（本地 dev 零配置）。前端部署时配 `NEXT_PUBLIC_API_TOKEN` 自动携带。

## 健康与市场总览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康：provider 链、last_success_refresh、失败计数、is_stale |
| GET | `/api/system/caches` | 进程内 TTL 缓存观测（P0-5）：各命名缓存的命中率/容量/逐出 |
| GET | `/api/market/overview` | 六大指数 + 两市成交额合计 |
| GET | `/api/market/breadth` | 市场宽度（涨跌/涨跌停家数，全市场快照口径） |
| GET | `/api/market/sentiment` | 情绪周期判定（阶段/温度/依据/置信/切换条件，60s 缓存） |
| GET | `/api/market/sentiment-history?days=10` | 情绪周期序列 + 周期起点定位（retro #17） |
| GET | `/api/market/ladder-check` | B4 数据源自证：ths 天梯 seal_nextday 交叉验证自算晋级率（2进3/高位存活，逐日 match/drift，10min 缓存） |
| GET | `/api/market/heatmap` | A 股云图（行业分组 treemap 载荷） |

## 行情与个股

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/quotes?symbols=` | 批量缓存行情；缺省返回全部自选 |
| GET | `/api/quotes/{symbol}` | 单只；缓存 miss → 实时链；`?source=` 指定源 |
| GET | `/api/kline/{symbol}?timeframe=1d&limit=250` | K 线（1m~1w，前复权） |
| GET | `/api/order-book/{symbol}` | 五档盘口（经交叉校验） |
| GET | `/api/trades/{symbol}?limit=50` | 逐笔成交 |
| GET | `/api/minute-line/{symbol}` | 当日分时（含均价/精确量比基线） |
| GET | `/api/minute-signals/{symbol}` | 做 T 信号（5 指标共振，可解释依据） |
| GET | `/api/minute-decisions?symbol=` | 做 T 决策链（读取时惰性结算） |
| GET | `/api/limit-up?date=` | 涨停池（按连板数排序） |
| GET | `/api/limit-break?date=` | 炸板池 |
| GET | `/api/themes?date=&min_boards=&sort=` | 题材梯队看板（连板天梯/成建制/健康度） |
| GET | `/api/auction/{symbol}` | 集合竞价快照（量比/未匹配量） |
| GET | `/api/auction-benchmark` | 短线风向标竞价基准 |
| GET | `/api/adjustment-events/{symbol}` | 复权事件（分红/送股，回测修正用） |
| GET | `/api/sparkline?symbols=&days=30` | 批量迷你走势（TDX 日K收盘，5min 缓存） |
| GET | `/api/search?q=` | 股票搜索（失败回退内置词表） |

## 投研数据

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/longhu?date=` | 龙虎榜（按净买额排序） |
| GET | `/api/longhu/{symbol}` | 个股龙虎榜历史 |
| GET | `/api/capital-flow/{symbol}` | 资金流 |
| GET | `/api/financials/{symbol}` | 财务三表 |
| GET | `/api/company/{symbol}` | 公司资料（F10） |
| GET | `/api/announcements/{symbol}?limit=` | 公告（60s 缓存） |
| GET | `/api/news/{symbol}?limit=` | 相关新闻（60s 缓存） |
| GET | `/api/boards` | 板块排行（行业/地域/概念题材/风格分组） |

## 模拟交易（paper）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/paper/account` | 账户（现金/市值/总资产） |
| GET | `/api/paper/positions` | 持仓（数量/成本/盈亏；休市日 last_price 为 null） |
| GET | `/api/paper/orders?status=` | 委托列表 |
| GET | `/api/paper/fills?symbol=` | 成交记录 |
| 🔒 POST | `/api/paper/orders` | 下单（限价；撮合 T+1/涨跌停拒/整手/费用） |
| 🔒 DELETE | `/api/paper/orders/{id}` | 撤单 |
| 🔒 POST | `/api/paper/reset` | 重置账户（清仓+清委托+资金复位，二次确认） |

| GET | `/api/news/digest/{symbol}?limit=` | 新闻+公告摘要：按重要度倒序，每条附 **重要度**（高/中/普通/低 + 加权分 + 命中项）、**消息面情绪**（偏正面/偏负面/分歧/中性 + 命中词）、**事实摘要**（取自正文，表格型正文整段丢弃回退标题）、**关键数字**（百分比/金额）。`model` 回传实际摘要器与降级原因 |

### 摘要口径（`app/news/`，与 `app/review/` 同构的"规则先行 + 可插拔降级"）

- **规则摘要器永远可用**，不依赖任何外部凭证；LLM 是增强层，配置 `ASHARE_NEWS_LLM_*` 后启用，失败自动降级
- **降级必须显式**：`model.degraded=true` 时带 `reason`，前端须标注来源——否则读者会以为摘要出自模型，实际出自关键词匹配
- **不臆造**：摘要只能截取原文；东财部分 `summary` 是行情表原文（如"计算机 688041 海光信息 -0.47 20875.30…"），检测到即整段丢弃并回退到标题，`digest_source` 会说明原因
- 输出只含 重要度/情绪/事实/数字，**不含任何买卖建议**（红线 3）

## 事件驱动（linkage-design §4）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/events?active=&limit=` | 活跃事件列表（时效=半衰期×2 实时计算） |
| GET | `/api/events/{id}` | 事件详情（含方向映射行） |
| GET | `/api/events/symbol/{symbol}` | 个股相关活跃事件（方向题材命中官方归属 or 事件源自该股） |
| GET | `/api/events/{id}/stocks` | 标的池：方向题材 → 官方成分反查 + override |
| POST | `/api/events` | 手动注册事件（写鉴权），规则抽取方向 |
| POST | `/api/events/extract` | 批量注册 items[]（写鉴权） |
| POST | `/api/events/collect` | 自选新闻批量抽取（写鉴权） |
| POST | `/api/events/{id}/review` | 人工裁决 resolved/rejected（写鉴权） |

方向判定为规则词典 v1（利好/利空动词 + 国产替代对冲），实体来自官方目录名与人工别名表；每行带 basis。LLM 增强层未接入。

## 题材目录与官方成分（linkage-design §3）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/themes/catalog?search=&limit=` | 题材全集（THS 官方概念目录，实测 390 个）；空库自动同步 |
| GET | `/api/themes/catalog/{code}/members?refresh=` | 官方成分快照（当前成分）；缺失/超 TTL 懒同步 |
| POST | `/api/themes/sync` | 触发同步（写鉴权）：目录 + 可选成分 + 过期补齐 |
| GET | `/api/themes/reconciliation?date=` | 涨停归因 × 官方成分校验：归因冲突 + 目录外题材 |
| GET | `/api/themes/hot?limit=30` | 题材人气（B1）：ths 热股 24h 榜 × 官方成分反查聚合（heat 合计/热股家数/榜内最高成员），60s 缓存 |

归属置信度分层与纠错机制见 docs/linkage-design.md §3.2-§3.4。

## 风控预检

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/risk/state` | 当前市场状态（7 档）+ 判定依据 + 对应仓位建议参数（单票/总仓上限、止损、加仓与高位股限制、回撤保护） |
| POST | `/api/risk/check-order` | 下单前风险预检（不实际下单）：数据质量、市场状态禁买、单票上限、总仓位上限、回撤保护、现金充足度、流动性提示；返回 `allowed / max_qty / reasons / warnings / state` |

`max_qty` 语义统一为「本次最多可下单股数」：买入取 单票上限 / 总仓位 / 现金 三者最小值（向下取整到 100 股），卖出取可卖数量（T+1）。
持仓计价口径：优先实时价，**缺失时回退成本价**（早期版本回退订单价，会把总仓位严重低估导致风控失效）。

## 量化工具

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/screener` | 全市场选股器：截面过滤（涨幅带/成交额/换手/排 ST·北交·次新）→ TDX 日K 六维评分卡（防飞刀口径），缓存 30 分钟，首跑约 20s |
| POST | `/api/backtest/run` | 单标的日线策略回测（防泄露引擎：as_of 视图/T+1/一字板拒/费用配置化；ma_cross / ma_breakout） |
| GET | `/api/backtest/strategies` | 可用策略清单 |

## 预警通知

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/alerts/channels` | 可用通知通道 + 默认通道 |
| GET | `/api/alerts/rules` | 规则列表 |
| 🔒 POST | `/api/alerts/rules` | 创建规则（价格/涨跌幅阈值，自选/指定/全市场范围） |
| GET | `/api/alerts/rules/{id}` | 单条规则 |
| 🔒 PUT | `/api/alerts/rules/{id}` | 更新规则 |
| 🔒 DELETE | `/api/alerts/rules/{id}` | 删除规则 |
| GET | `/api/alerts/events?limit=&rule_id=` | 触发记录 |
| 🔒 POST | `/api/alerts/events/{id}/ack` | 确认事件 |

## 盘后复盘与预判（AI）

| 方法 | 路径 | 说明 |
|---|---|---|
| 🔒 POST | `/api/review/run` | 触发盘后复盘（数据自采→规则分析→落库） |
| GET | `/api/review/reports` | 报告列表 |
| GET | `/api/review/reports/{trade_date}` | 单日报告（payload 完整 JSON） |
| GET | `/api/review/compare?a=&b=` | 两日报告对比 |
| GET | `/api/review/methodology/versions` | 方法论版本与采纳率 |
| GET | `/api/review/effectiveness` | 改进项历史效果统计 |
| 🔒 POST | `/api/predict/run` | 新题材预判（热榜候选→六维评分→梯队推演） |
| GET | `/api/predict/predictions` | 预判列表 |
| GET | `/api/predict/predictions/{target_date}` | 单日预判（证据链/介入计划） |
| 🔒 POST | `/api/predict/verify/{target_date}` | D1 四问验证（题材成立?/人气?/梯队对照?） |
| GET | `/api/predict/stats` | 命中率分层统计 |

## 自选股

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/watchlist` | 自选列表（含分组） |
| 🔒 POST | `/api/watchlist` | 加自选 |
| GET | `/api/watchlist/groups` | 分组列表 |
| 🔒 PUT | `/api/watchlist/{symbol}/group` | 改分组 |
| 🔒 DELETE | `/api/watchlist/{symbol}` | 删自选 |

## 错误约定

- `4xx`：参数/资源问题（body `{detail, code}`，code 如 `validation_error`）
- `502`：上游数据源失败（`upstream_failed` / `tdx_unavailable` / `snapshot_unavailable` 等）
- `503`：服务未就绪（冷启动/日历不可用）
- `401`：写端点 token 缺失/错误（配置 ASHARE_API_TOKEN 后）
