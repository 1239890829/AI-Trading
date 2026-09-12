# REST API

Base URL：`http://127.0.0.1:8000`（`/api` 前缀）。**170 个操作 / 160 条路径**（2026-09-10 实测回填；端点数以 `/openapi.json` 为权威，本文档按域分节供检索——**数字会随改动漂移，勿以本行为准**）。

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
| GET | `/api/market/entry-checklist?symbol=&date=` | **介入条件清单（P1-13）**：市场层 → 题材层 → 个股层**必须同时满足**的信号 + 回避项 + 失效条件 + 时间窗口。复用 `/api/themes` 与 sentiment 的既有 60s 缓存（**零额外上游**）。非题材成员返回通用清单 + `found:false`（个股层未判定）；`missing[]` 显式列出未取到的输入（三态：缺失不当地/中性假设）。**全部为条件陈述，不构成买卖建议** |

## 行情与个股

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/quotes?symbols=` | 批量缓存行情；缺省返回全部自选 |
| GET | `/api/quotes/{symbol}` | 单只；缓存 miss → 实时链；`?source=` 指定源 |
| GET | `/api/kline/{symbol}?timeframe=1d&limit=250` | K 线（1m~1w，前复权） |
| GET | `/api/order-book/{symbol}` | 五档盘口（经交叉校验） |
| GET | `/api/trades/{symbol}?limit=50` | 逐笔成交 |
| GET | `/api/minute-line/{symbol}` | 当日分时（含均价/精确量比基线） |
| GET | `/api/market/minute-signals/{symbol}` | **做 T 偏向信号（P1-24）**：分钟级五指标引擎产出「偏向 + 依据（逐指标 evidence）+ 失效条件」。**触发即记录**到决策库（幂等副作用：引擎前缀稳定 ⇒ 同 bar 重算同结果，按 `(symbol, trigger_ts)` 去重，重复请求 `recorded=0`）。`signals:[]` = 确无信号；`degraded[]` = 输入缺失导致的降级（缺昨日量/缺波动率…），**如实透传不补假数据**。**不构成买卖建议** |
| GET | `/api/market/minute-decisions?symbol=&limit=` | **做 T 决策库（P1-24）**：决策记录 + 三分类结果（`correct` 方向最优价差≥8bp / `wrong` 反向不利价差达阈 / `invalid` 窗口内无有效价差 / `expired` 数据不足不判定 / `null` 待结算）+ **leave-one-out 错误归因**（剔除哪个指标会翻转结论）。读列表时**惰性结算**到期记录；盘后 15:35 另有批量结算保证无人看页面时样本也累积 |
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
- `GET /api/speed-rank` — 题材/成分股 5 分钟涨速榜（同花顺涨速口径；theme 或 symbols 参数；惰性采样）

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

| 方法 | 路径 | 说明 |
|---|---|---|
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
| GET | `/api/themes/catalog/strength?themes=` | 题材内资金合力（P1-5）：官方成分批量快照聚合（涨跌家数/等权涨幅/涨停数/成交额），60s 缓存 |
| GET | `/api/themes/catalog/index?code=&days=30` | 官方板块指数日 K + 3/5/10 日涨跌幅（板块级交叉验证） |

归属置信度分层与纠错机制见 docs/summary/architecture-design.md §3.2-§3.4。

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
| GET | `/api/watchlist/groups` | 分组列表（持久分组表 ∪ 成员派生，空分组也在） |
| 🔒 POST | `/api/watchlist/groups` | 新建空分组（重名 409） |
| 🔒 PUT | `/api/watchlist/groups/{name}` | 重命名分组（级联成员；「默认」保护 400，新名冲突 409） |
| 🔒 DELETE | `/api/watchlist/groups/{name}` | 删除分组（成员回落「默认」；「默认」保护 400） |
| 🔒 PUT | `/api/watchlist/{symbol}/group` | 改分组 |
| 🔒 DELETE | `/api/watchlist/{symbol}` | 删自选 |

## 错误约定

- `4xx`：参数/资源问题（body `{detail, code}`，code 如 `validation_error`）
- `502`：上游数据源失败（`upstream_failed` / `tdx_unavailable` / `snapshot_unavailable` 等）
- `503`：服务未就绪（冷启动/日历不可用）
- `401`：写端点 token 缺失/错误（配置 ASHARE_API_TOKEN 后）

## 真实持仓（/api/real/*，与 /api/paper/* 模拟账户完全独立）

- `GET /api/real/positions` — 持仓视图：流水加权聚合 + 手动覆盖 + 行情补现价/市值/浮动盈亏；含 cleared（已清仓）与 total 汇总
- `POST /api/real/trades` — 记一笔买入/卖出（实际成交价 fill_price 必填）
- `DELETE /api/real/trades/{id}` — 删除流水（修正历史）
- `PATCH /api/real/positions/{symbol}` — 手动覆盖持仓数量/总成本（覆盖后以覆盖为准）
- `DELETE /api/real/positions/{symbol}` — 整只删除（清空该标的流水与覆盖）

## 每日精选（/api/picks/*，六维规则版多角色评分；≤5 只；收盘定次日+换股门槛 15 分+每日换股上限 2 只）

- `GET /api/picks/today` — 当日组合（含 meta：六维权重/炒作阶段 regime/空仓闸门 gate；items 含梯队地位/题材阶段/风险档位/止损参考位/失效条件/observation_only）
- `POST /api/picks/generate` — 生成/刷新组合（写鉴权）：候选池 → 题材基准超额判梯队 → 六维评分（权重按炒作阶段切换）→ 换股门槛 → 空仓闸门处理
- `GET /api/picks/history` — 历史组合（一致性回溯）
- `POST /api/picks/review/generate` — 生成复盘：逐只超额 vs 上证 + 九类归因（含买点质量/情绪误判/踏空）
- `GET /api/picks/review?date=` — 复盘日志
- `GET /api/picks/meta` — 走坏原因分布（周末调权建议输入）

## 交易智能体（/api/agent/*，AI 控制台；L0-L3 分级执行 · 全程留痕可回滚）

> 2026-09-10 补录：本域此前完全不在本索引里（属"文档缺域"）。该域 26 个操作**已全数列出**（实测自 `/openapi.json`）。

**任务中心（执行层）**

- `GET /api/agent/task-types` — **可创建**任务类型（= 有 handler 的类型：生成复盘报告 / 数据体检）。登记类条目（mutation 变更留痕 / escalation 告警升级待办）**不在此列**——它们没有执行体，出现只会建出必然失败的任务（见 KB-ENG-41）
- `POST /api/agent/tasks` 🔒 — 创建并启动任务（同类型互斥；未知/登记类类型返回 422）
- `GET /api/agent/tasks` — 任务列表（`?type=&limit=`；含**只读留痕合一**：议程自动执行以 `read_only` 条目并入同一时间线）
- `GET /api/agent/tasks/{id}` — 任务详情（步骤轨迹 + `params` 全量：登记类条目的正文就在 params 里）
- `POST /api/agent/tasks/{id}/cancel` 🔒 — 取消（只读留痕条目不可取消）
- `POST /api/agent/tasks/{id}/resolve` 🔒 — **处置待办（P1-36）**：`{outcome: done\|dismissed, note?}` → succeeded / canceled；仅 `needs_confirm` 可改，终态幂等
- `GET /api/agent/audit` — 执行层审计（`?target=&task_id=`）
- `GET /api/agent/agenda` / `GET /api/agent/agendas` — 当日议程 / 历史议程
- `POST /api/agent/agenda/run` 🔒 — 手动跑一次进化议程（降级兜底；常规由 15:45 定时自动执行）

**提醒与告警判读（降噪层）**

- `GET /api/agent/triage` — 判读历史（`?verdict=notify\|ignore\|escalate`；`model` 区分 llm / rules / **llm_fallback**）
- `GET /api/agent/triage/pending` — 悬浮球待提醒（仅 `notify` 且未确认，**6 小时时效**，缺 symbol/name 的整条过滤）
- `POST /api/agent/triage/{id}/ack` 🔒 — 气泡确认
- `POST /api/agent/triage/run` 🔒 — 手动触发一轮判读（常规由后台 worker 每 30s 自动跑）
- 纪律：判读**不发飞书**；`escalate` 由 `_save` 登记为任务中心待办（`needs_confirm`），人工处置（KB-DEC-020）

**参数配置 / 实验 / 元评估 / 知识库**

- `GET /api/agent/params` · `POST /api/agent/params/change` 🔒 — 参数运行态 / 提交变更单（白名单 + fail-fast）
- `GET /api/agent/params/changes` · `POST /api/agent/params/changes/{id}/apply` 🔒 · `POST .../rollback` 🔒 — 变更单历史 / 生效 / 回滚
- `GET /api/agent/params/survival` · `GET /api/agent/params/rollback-reasons` — 存活率三数分工 / 回滚归因
- `GET /api/agent/experiments` — 实验记录本（A 类变更 30 日后置验证，劣化自动回滚）
- `GET /api/agent/meta-review` · `POST /api/agent/meta-review/run` 🔒 — 元评估周报
- `GET /api/agent/kb/tree` · `GET /api/agent/kb/file?path=` — 知识库浏览（docs/kb）
