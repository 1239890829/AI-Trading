# AShare AI Trader · 项目总整理（MASTER）

> 生成于 2026-08-29。本文档是**全项目唯一总览**：事无巨细覆盖技术栈/架构/数据源/模块/API/前端/交易系统/质量/测试/配置/部署/安全/阶段状态/欠缺。细节文档在各节标注链接。
> 当前快照：**118 测试全绿 · 前端 build 通过 · 34 次提交 · 后端 5255 行 + 前端 2941 行**。

---

# 一、项目概览

## 1.1 定位
A 股实时行情 + AI 量化投研 + 模拟交易工作台。**只做**行情展示/数据分析/投研/选股观察/模拟交易/回测。**第一阶段禁止**：连接真实券商、自动真实下单、无数据依据的确定性买卖结论、mock 冒充实盘。

## 1.2 当前状态快照
- 后端：FastAPI（Python 3.11），32 个 REST 端点 + 1 个 WebSocket，**四源 Provider 链**（ths→tencent→eastmoney→sina）+ mock
- 前端：Next.js 16 App Router，9 页面 + 10+ 组件，终端式工作台
- 数据：全市场快照（5550 只）落 Parquet；SQLite 业务库
- 测试：118 用例全绿；ESLint/pyflakes/tsc 门禁零问题
- 运行：双端本地运行中（8000/3000）

---

# 二、技术栈全表

| 层 | 技术 | 版本/说明 |
|---|---|---|
| 后端框架 | FastAPI | ≥0.115 |
| 数据校验 | Pydantic v2 | schemas + settings |
| ORM/DB | SQLAlchemy 2 + SQLite | 业务库（自选/账户/持仓/订单）|
| 行情存储 | Parquet（polars） | 全市场快照时点落库 |
| HTTP 客户端 | httpx（AsyncClient, trust_env=False） | 行情源直连不走系统代理 |
| 实时推送 | WebSocket（uvicorn[standard]） | /ws/quotes |
| 测试 | pytest | 118 用例 |
| 静态检查 | pyflakes | 后端门禁 |
| 前端框架 | Next.js 16.3.3 (App Router) + React 19 + ESLint 9 flat config | |
| 样式 | Tailwind CSS 3.4（darkMode class） | 自定义 up=红涨 down=绿跌 |
| 图表 | lightweight-charts 4.2 | K线/分时/副图 |
| 字体/对齐 | 系统栈 + tabular-nums | 数据等宽 |
| 前端检查 | ESLint（next/core-web-vitals） | 零告警门禁 |
| 部署 | Dockerfile + docker-compose（开发用） | |

---

# 三、目录结构（逐文件说明）

```text
ashare-ai-trader/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI 组装：lifespan(建库/种子/引擎/3后台任务) + 路由挂载 + CORS
│   │   ├── api/
│   │   │   ├── deps.py                # get_hub / get_watchlist_repository
│   │   │   └── routes/
│   │   │       ├── health.py          # GET /api/health（含 provider 链/stale/失败计数）
│   │   │       ├── market.py          # 行情/宽度/情绪(+历史序列)/K线/盘口/分时/逐笔/资金/财务/公司/公告/新闻(60s缓存)/板块/涨停/炸板/龙虎榜/搜索/竞价/复权/sparkline
│   │   │       ├── watchlist.py       # 自选 CRUD + 分组
│   │   │       ├── paper.py           # 模拟交易（账户/持仓/委托/撤单/成交/重置）
│   │   │       ├── review.py          # 盘后复盘（run/reports/compare/versions/effectiveness）
│   │   │       ├── screener.py        # GET /screener 全市场选股器（快照过滤+TDX日K评分）
│   │   │       ├── backtest.py        # POST /backtest/run + GET /backtest/strategies
│   │   │       └── predict.py         # 新题材预判（run/list/get/verify/themes）
│   │   ├── core/
│   │   │   ├── config.py              # Settings（ASHARE_* 环境变量）
│   │   │   └── db.py                  # 引擎(:memory:→StaticPool) + 幂等迁移 + session
│   │   ├── models/
│   │   │   ├── watchlist.py           # 自选（含 group_name）
│   │   │   └── paper.py               # 模拟账户/持仓/订单
│   │   ├── schemas/market.py          # Quote/OrderBook/Trade/Kline/LimitUp/LongHu/Board/Quality + 审计字段
│   │   ├── repositories/watchlist_repo.py
│   │   ├── services/
│   │   │   ├── quote_hub.py           # 行情轮询/缓存/校验/订阅广播/seq/stale 降级（含休市日 mark_all_stale）
│   │   │   ├── snapshot_service.py    # 全市场快照(新浪)→宽度→Parquet
│   │   │   ├── screener_service.py    # 选股器编排：截面过滤→TDX日K→评分（TTL 30min+single-flight）
│   │   │   ├── heatmap_service.py     # 云图聚合（快照×TDX HY 行业映射，24h 缓存）
│   │   │   └── market_context.py      # 大盘上下文 + compute_market_sentiment（实时情绪，复盘共用）
│   │   ├── market/
│   │   │   ├── normalizer.py          # 东财全族字段→统一 schema（含财务/席位/公告/新闻/搜索）
│   │   │   ├── sina_market.py         # 新浪全市场快照（Market Center）
│   │   │   ├── breadth.py             # 涨跌/涨跌停家数/成交额（N/C 新股排除）
│   │   │   ├── tech_score.py          # 选股器六维评分卡（防飞刀口径，SCORER_VERSION 版本化）
│   │   │   ├── backtest.py            # 日线回测引擎（代码级防泄露+内置双策略+报告）
│   │   │   ├── tdx_kline.py           # TDX 日K公共拉取（QFQ；选股器/回测共用数据路径）
│   │   │   ├── trade_calendar.py      # 交易日历（ths 官方→指数K线→持久化兜底）
│   │   │   ├── sentiment_history.py   # 情绪周期序列（落库/回填/周期定位）
│   │   │   ├── minute_signals.py      # 做 T 信号引擎（5 指标 as_of 流式）
│   │   │   ├── minute_decisions.py    # 做 T 决策链记录与结算（leave-one-out 归因）
│   │   │   ├── minute_backfill.py     # 分时历史落盘（新浪 5m→Parquet）
│   │   │   └── minute_backtest.py     # 做 T 回测底座（as_of 逐日+样本内外）
│   │   ├── data_providers/
│   │   │   ├── base.py                # MarketDataProvider 协议
│   │   │   ├── ths.py                 # 同花顺官方 fuyao（链首）
│   │   │   ├── tencent.py             # 腾讯（快照/五档/K线/分时/K线搜索）
│   │   │   ├── sina.py                # 新浪（快照备源/板块排行/资金流）
│   │   │   ├── eastmoney.py           # 东财（涨停池/龙虎榜/席位/财务/公司/公告/新闻/搜索/K线备源）
│   │   │   ├── mock.py                # 确定性演示（clock 可注入）
│   │   │   └── composite.py           # 链式 failover + 切换日志
│   │   ├── data_quality/validator.py  # 5级质量 + 全部规则
│   │   ├── sentiment/engine.py        # 情绪阶段判定（可解释）
│   │   ├── paper/engine.py            # 模拟交易撮合引擎
│   │   ├── review/                    # 盘后复盘 Agent（每日收盘自动复盘）
│   │   │   ├── schemas.py             # 核心数据结构（DataGap 是一等公民）
│   │   │   ├── config.py              # 方法论配置（可版本化 yaml 外置）
│   │   │   ├── models.py              # 持久化三表（reports/action_items/meta_insights）
│   │   │   ├── collector.py           # 数据自采（缺失标 gap，不臆测）
│   │   │   ├── analyzers.py           # Analyzer 协议 + RulesAnalyzer(默认) + LLMAnalyzer(占位)
│   │   │   ├── model_router.py        # 分析器路由（配置切换+降级+成本记录）
│   │   │   ├── synthesis.py           # 改进项合成（优先级+预期影响）
│   │   │   ├── methodology.py         # 元结论 + 历史效果统计（自我迭代）
│   │   │   ├── storage.py             # 落库+落盘+检索+对比
│   │   │   └── service.py             # 编排 + 调度器（含收盘后自动触发 + 预判验证钩子）
│   │   ├── predict/                   # 新题材预判（周末/盘后，docs/theme-prediction.md）
│   │   │   ├── schemas.py             # 预判报告/证据/梯队/介入计划（fail_conditions 一等公民）
│   │   │   ├── models.py              # 持久化两表（reports + themes 明细行）
│   │   │   ├── collector.py           # 热榜+个股新闻+涨停史+龙虎榜+情绪交叉采集
│   │   │   ├── engine.py              # 六维评分卡 + 梯队推演 + 介入计划（权重版本化）
│   │   │   ├── storage.py             # 落库+落盘+检索+命中率分层统计
│   │   │   └── service.py             # 编排 + 目标日四问验证 + 复盘钩子
│   │   └── websocket/routes.py        # /ws/quotes
│   ├── tests/（27 文件 329 用例；含 test_backtest 12 防泄露、test_screener 8、test_sentiment_history 8、test_write_token 4）
│   ├── requirements.txt + requirements.lock / alembic.ini + migrations/（baseline 91f8ea3c3a3e + a7c3e91d2f44 情绪序列）/ Dockerfile / .env（key，gitignored）
├── apps/web/
│   ├── app/（10 路由页面：workbench/market/watchlist/boards/heatmap/limit-up/themes/screener/backtest/longhu）
│   ├── components/（15 组件：图表族 kline-chart-pro/minute-chart/replay-chart、detail/ 六子件、sparkline、nav-bar 等）
│   ├── hooks/use-quote-stream.ts      # WS+降级轮询
│   ├── lib/api.ts（ApiError+超时+token）+ format.ts + technical-analysis.ts（防飞刀口径）
│   ├── lib/*.test.ts（vitest 18 用例）+ vitest.config.ts
│   └── types/market.ts
├── data/（ashare.db + parquet/snapshots/ + parquet/minutes-tdx/ + trade_calendar.json）
├── docs/（14 篇 + 本文档）
├── scripts/bootstrap.sh
├── docker-compose.yml / .env.example / README.md
└── .github/workflows/ci.yml  # 四门禁：pytest/pyflakes + tsc/vitest/ESLint
```

---

# 四、数据管线与 Provider 链

## 4.1 管线
```text
数据源 → Provider Adapter → Data Normalizer → Data Quality Validator → Cache/QuoteHub → REST/WebSocket → 前端
```
- 前端零直连；后端批量轮询（5s 行情 / 60s 快照 / 5min Parquet）
- 每条数据必带 `source / quality(high|medium|low|stale|invalid) / quality_reasons / received_at / data_timestamp`

## 4.2 Provider 链（当前 `chain(ths→tencent→eastmoney→sina)`）
| 优先 | Provider | 能力 | 认证 | 备注 |
|---|---|---|---|---|
| 1 | **ths**（同花顺官方 fuyao） | 快照/指数/涨停池(含原因)/炸板池/龙虎榜(含概念)/交易日历；财务三表+估值+竞价待接 | X-api-key（.env） | 官方 59 端点；**不含 L2/分钟K/tick** |
| 2 | **tencent** | 快照+五档(PE/PB/市值/涨跌停价)/日周K+分钟K/分时+量/搜索 | 无 | 主行情源（ths 失败时） |
| 3 | **eastmoney** | 涨停池/龙虎榜/席位/F10公司/公告/新闻/搜索/财务/K线 | 无 | push2 系本机被 WAF 限流；datacenter 正常 |
| 4 | **sina** | 快照备源/五档/板块排行/资金流/全市场快照 | Referer | |
| 尾 | **mock** | 确定性演示 | - | 只能单独用，永不混链 |
- **逐方法 failover**：空结果/异常自动切下一源；切换写 `switch_log`；全链失败 → 全部标 `stale`、health=degraded
- 详见 docs/data-sources.md（字段口径全部实测记录）

## 4.3 数据质量规则（data_quality/validator.py）
- 结构非法 → **invalid**：代码非6位、价格≤0、负量/额、high<low、价出区间、时间戳在未来、盘口交叉/乱序
- 可疑 → **low**：跳价>板块涨跌停幅（主板10/双创20/北交30/ST5）、涨跌幅与昨收矛盾>1pct、时间倒退、缺价
- 系统 → **stale**（Hub 刷新失败）、**medium**（预留延迟源）
- 低质量后果：AI 禁用、回测禁用、前端强制风险标识

---

# 五、REST API 全表（56 端点，**完整清单与鉴权说明见 docs/api.md**，此处保留增量与要点）

核心入口速查（🔒=B6 写鉴权）：
| 方法 | 路径 | 说明 | 数据源 |
|---|---|---|---|
| GET | /api/health | 健康：链名/last_success/失败计数/last_error/stale | - |
| GET | /api/market/overview | 六指数+两市成交额 | ths→tencent |
| GET | /api/market/breadth | 宽度（涨跌/涨停跌家数/总额） | 新浪全市场 |
| GET | /api/market/sentiment | 情绪判定（阶段/温度/依据/置信/切换条件，60s缓存） | 快照+涨停池 |
| GET | /api/market/sentiment-history | 情绪周期序列+周期起点定位（retro #17） | sentiment_history 表 |
| GET | /api/market/heatmap | A 股云图（行业 treemap 载荷，24h 行业缓存） | 快照+TDX HY |
| GET | /api/quotes?symbols= | 批量缓存行情 | 链 |
| GET | /api/kline/{symbol} | K线 timeframe 1m~1w 前复权 | tencent→东财 |
| GET | /api/minute-line/{symbol} | 当日1分钟分时+均价+精确量比基线 | 腾讯 |
| GET | /api/minute-signals/{symbol} | 做 T 信号（5 指标共振+依据） | 分时+快照 |
| GET | /api/minute-decisions | 做 T 决策链（读取时惰性结算） | minute_decisions 表 |
| GET | /api/order-book/{symbol} | 五档（交叉校验后返回） | 腾讯→东财 |
| GET | /api/trades/{symbol} | 逐笔（东财 details；本机限流→502 显性化） | 东财 |
| GET | /api/capital-flow/{symbol} | 资金流30日（主力/超大/大/中/小单+口径） | 新浪 MoneyFlow |
| GET | /api/financials/{symbol} | 财务摘要8期（去重+倒序） | 东财业绩报表 |
| GET | /api/company/{symbol} | 公司资料 + `boards`(全量混合标签) + `board_groups`(行业/地域/概念/风格指数四组) | 东财 F10+CoreConception |
| GET | /api/limit-up / limit-break | 涨停池(含原因)/炸板池 | ths→东财 |
| GET | /api/auction/{symbol} · /auction-benchmark | 集合竞价快照/风向标基准 | ths 官方 |
| GET | /api/adjustment-events/{symbol} | 复权事件（分红/送股，回测修正用） | ths 官方 |
| GET | /api/sparkline?symbols= | 自选迷你走势（批量 TDX 日K收盘，5min 缓存） | TDX |
| GET | /api/themes | 强势题材梯队看板（题材容器/连板天梯/阶段/强度/断板股） | ths+东财+Parquet快照 |
| GET | /api/longhu · /longhu/{symbol} | 龙虎榜总览/席位明细+上榜历史 | ths→东财 datacenter |
| GET | /api/boards?type= | 行业84/概念排行（60s缓存） | 新浪闪电排行 |
| GET | /api/search?q= | 股票搜索（仅6位A股） | 东财suggest→腾讯smartbox→mock |
| GET | /api/announcements/{symbol} · /news/{symbol} | 公告/新闻（60s TTL 缓存，技术债#4） | 东财 |
| GET/POST/DELETE | /api/watchlist… | 自选 CRUD + groups + 改组 | SQLite |
| GET/🔒POST | /api/paper/* | 模拟交易（account/positions/orders/fills） | 撮合引擎 |
| 🔒 POST | /api/paper/reset | 重置模拟账户（可传 initial_cash） | 撮合引擎 |
| GET | /api/screener | 全市场选股器（截面过滤→TDX日K六维评分卡，30min 缓存） | Parquet+TDX |
| POST | /api/backtest/run · GET /backtest/strategies | 日线策略回测（防泄露引擎） | TDX 日K |
| 🔒 POST | /api/review/run | 手动触发复盘（调度器之外的补跑入口） | 自采+分析 |
| GET | /api/review/reports[/{date}] · /compare · /methodology/versions · /effectiveness | 复盘检索/对比/方法论演进 | SQLite+JSON |
| 🔒 POST | /api/predict/run · /predict/verify/{date} | 新题材预判与 D1 验证 | ths热榜+东财新闻 |
| GET | /api/predict/predictions[/{date}] · /stats | 预判检索与命中率分层 | SQLite |
| WS | /ws/quotes | snapshot/quotes/stale/pong + subscribe | Hub |

完整 56 端点逐条说明：**docs/api.md**（与代码同步维护）。

---

# 六、前端明细

## 6.1 页面（7）
| 路由 | 内容 | 状态 |
|---|---|---|
| /workbench | 终端主页面：左栏[指数迷你卡(可收起)+自选分组+列表] │ 右[详情终端] | ✅ |
| /market | 总览+宽度卡+情绪面板+涨停速览 | ✅ |
| /watchlist | 自选管理（分组输入/改组/删除） | ✅ |
| /boards | 板块排行（行业/概念切换） | ✅ |
| /themes | 题材梯队看板（单容器纵向滚动；卡片=强弱分级+当日涨跌幅+个股梯队[层级+角色]；归属按当日联动唯一判定，拆散率 22%→0；涨停池降级为证据下钻页，卡片「涨停池↗」带日期直达） | ✅ 新增 2026-08-29 重构 |
| /limit-up | 涨停池（含涨停原因、日期查询） | ✅ |
| /longhu | 龙虎榜总览（净买额排序） | ✅ |
| /stock/[symbol] | 307 重定向 → /workbench?symbol= | ✅（已合并） |

## 6.2 详情终端（StockDetailPanel，工作台右栏 300px，单卡片）
- 顶部紧凑行情条：名称/代码/质量/＋自选 │ 大字价格(tick闪烁) + 涨跌 │ 11项指标小字条 │ 数据时间/来源
- 中部左（图表区，页签）：**K线**（K线Pro：MA5/10/20/60+BOLL+成交量+均量线+MACD/成交额副图开关、默认聚焦20日、＋/−/20D 缩放按钮、技术评估条、B/S 成交标记、持仓成本黄虚线）│ **分时**（面积图+量能副图）│ **资金图**（30日主力柱状）
- 中部右（常驻列，页签）：**盘口**（五档）│ **逐笔** │ **交易**（账户摘要+买卖表单[涨跌停提示/费用预估]+持仓+挂单撤单+成交记录[日期/方向/价格/数量/费用]+重置账户[二次确认]）│ **资料**（板块概念chips+主营+简介+最近财报摘要卡）│ **资讯**（公告/新闻原文链接）
- 滚动纪律：容器 overflow-hidden，仅表格/列表内部滚动

## 6.3 关键机制
- useQuoteStream：WS snapshot/quotes/stale + 15s 心跳 + 指数退避重连 + 3 失败降级 REST 轮询
- `watchlist-changed` / `paper-changed` 全局事件 → 即时刷新
- PriceFlash：价格 tick 闪红/绿（prefers-reduced-motion 全关）
- 主题切换（dark 默认）/ 全局搜索（防抖+快捷加自选）/ 骨架与空态/错误态全覆盖

---

# 七、模拟交易系统（paper/engine.py）

- 模型：PaperAccount(cash) / PaperPosition(quantity, frozen_today[T+1], cost_price) / PaperOrder(状态机)
- 撮合规则：涨停拒买、跌停拒卖、停牌拒、买入100股整手、资金/可卖校验、限价≥现价按现价成交否则挂单（5s 轮询）、撤单全额退款（含预扣佣金）
- 费用：佣金万2.5最低5元 + 卖出印花税0.05%（配置化 FEE dict）
- T+1 解冻：官方交易日历（ths，24h缓存）→ 回退自然日
- 重置：`POST /api/paper/reset` → 清仓 + 清委托与成交历史 + 资金回到 `initial_cash`（可传自定义额度）
- 已知边界：仅限价单；无部分成交；滑点=0（配置保留）；单账户
- 测试：9 引擎用例（成交/T+1阻断与放行/涨停跌停拒/碎股拒/资金拒/挂单撤单/reset×2）

---

# 八、测试与门禁

| 套件 | 覆盖 |
|---|---|
| test_quality_validator(11) | 全部质量规则与板块阈值 |
| test_normalizer(9) | 腾讯/东财/新浪/THS 真实fixture解析 |
| test_api(12) | 全端点 + WS + watchlist + live fallback + paper 成交/重置 |
| test_providers_chain(8) | failover/空结果/搜索过滤/K线fixture |
| test_paper_engine(9) | 撮合全规则 + reset 清仓/自定义初始资金 |
| test_sentiment(4) | 冰点/高潮/退潮/指标结构 |
| test_market_breadth(5) | 宽度/涨跌停/N/C排除 |
| test_mock_provider(6) | 演示数据确定性 |
| test_watchlist_repo(2) | CRUD+分组 |
| test_board_classifier(8) | 东财板块四分类（真实茅台 fixture + 字符串 IS_PRECISE 回归） |
- 门禁：pytest 326 全绿 + tsc 0 + ESLint 0 error（20 warn 挂账） + pyflakes 0 + next build 0 + 截图验收
- 注：pyflakes 此前从未真正为 0（6 处既存未用导入/变量），2026-08-29 清理至 0，后续按 0 卡

---

# 九、配置与环境变量（.env，gitignored）

| 变量 | 默认 | 说明 |
|---|---|---|
| ASHARE_DATA_PROVIDER | tencent | 主源（ths/tencent/sina/eastmoney/mock） |
| ASHARE_PROVIDER_FALLBACKS | tencent,eastmoney,sina | 备源链 |
| ASHARE_THS_API_KEY | - | 同花顺官方 key（已配于 backend/.env） |
| - | 远端 | github.com/1239890829/AI-Trading（SSH，master 已跟踪）；CI 见 .github/workflows/ci.yml |
| ASHARE_POLL_INTERVAL_SECONDS | 5 | 行情轮询 |
| ASHARE_SNAPSHOT_POLL/SAVE_INTERVAL_SECONDS | 60/300 | 全市场快照与Parquet |
| ASHARE_DATABASE_URL | data/ashare.db | 业务库 |
| ASHARE_PARQUET_DIR | data/parquet | 快照落库 |
| NEXT_PUBLIC_API_BASE | http://127.0.0.1:8000 | 前端连后端 |

---

# 十、部署运维与已知坑（全部踩过）

1. pip 必须清华镜像（`-i https://pypi.tuna.tsinghua.edu.cn/simple`）；系统代理会让 pip/httpx 卡死
2. 行情客户端 `trust_env=False`（国内直连）；勿给后端配代理
3. dev 运行时禁止 `next build`（.next 冲突，已踩两次）
4. 东财 push2 本机 WAF 限流 → 已由腾讯/新浪链路兜底
5. SQLite 迁移用 `engine.begin()`（2.0 无 engine.execute）
6. 前端金额/数量字段合并注意 undefined 覆盖（估值补源 bug）

---

# 十一、安全

- API Key 只存 backend/.env（gitignored）；.env.example 只放占位
- 前端零密钥；无真实券商接口（系统层面不提供）
- 撮合风控硬拦截不可绕过（§七）

---

# 十二、阶段状态（Phase 1-8 逐项）

| 阶段 | 子项 | 状态 |
|---|---|---|
| 1 基础框架 | 目录/配置/SQLite/日志/健康检查/布局/搜索/主题 | ✅ 全部 |
| 2 行情基础设施 | Provider协议/四源链/Normalizer/5级质量/QuoteHub/REST/WS/指数/个股/K线/分时/盘口/逐笔 | ✅ 全部 |
| 3 市场与板块 | 全市场快照/宽度/情绪周期判定/板块排行/涨停池/炸板池 | ✅；余：题材事件树/生命周期、左栏sparkline |
| 4 投研数据 | 龙虎榜总览+席位+历史/资金流/财务/估值/公司资料/公告/新闻 | ✅；余：营业部关系图谱、筹码、解禁、两融、大宗 |
| 5 量化系统 | 多因子技术评估(MA/MACD/KDJ/RSI/形态) + 全市场选股器（截面过滤→TDX日K→六维评分卡，防飞刀修正） | 🔶；余：市场状态/仓位建议/风控引擎/更多副图 |
| 6 模拟交易与回测 | 撮合引擎(T+1/涨跌停/费用/挂单) + 交易页签(买卖/持仓/挂单撤单/成交记录) + K线B/S点与持仓成本线 + 重置账户 + 日线回测引擎(代码级防泄露+双策略+净值曲线页) | 🔶；余：历史回放/左栏持仓组 |
| 7 AI 系统 | Researcher/Critic/Strategist/Auditor/MCP/Skills/记忆/审计 | ⬜（多因子评估是其地基） |
| 8 通知与部署 | 预警/通知/生产部署/监控 | ⬜（开发compose已有） |

## 近期路线（下一刀优先级）
1. ~~**交易前端打磨**：持仓成本线画上K线、成交记录列表页~~ ✅ 已完成（2026-08-29）
2. ~~**重置账户入口**~~ ✅ 已完成（2026-08-29）
3. ~~**概念题材 chips 过滤风格标签**（"大盘股/MSCI中国"混入"白酒"）~~ ✅ 已完成（2026-08-29）
4. ~~**盘后复盘 Agent 模块**~~ ✅ 已完成（2026-08-29）：`app/review/`（自采/规则分析/模型路由降级/方法论版本化/元结论自我迭代/落库检索对比）+ 调度器（交易日 15:30 自动触发）+ 6 个 REST 端点。详见 docs/review-agent.md
5. ~~**题材梯队模块重构**~~ ✅ 已完成（2026-08-29）：梯队联动归属 `assign_primary_themes`（连板密度多数票，唯一归属，8/28 实测拆散率 22%→0、创新药 3 板龙头回归本队）；强弱分级 `strength_tier`（领涨/强势/活跃/观察，溢价为负一票否决）；卡片重排（顶部分级+当日涨跌幅，底部梯队列表，重指标折叠）；滚动修复（页面根容器缺 h-full）；涨停池保留为证据下钻页。测试 204→216
6. ~~**新题材预判模块**~~ ✅ 已完成（2026-08-30）：`app/predict/` 六维评分卡（热榜/消息级别/新闻联动/环境/新鲜度/资金，权重版本化）+ 梯队推演 + 介入计划（成功率校准区间）+ D1 四问自动验证（挂复盘 Agent）。周末房产政策实测：我爱我家 #5 热榜 → 龙头候选、0.635 可能成立。详见 docs/theme-prediction.md
7. ~~**B1 统一错误契约 + B2 出参 schema 首批**~~ ✅ 已完成（2026-08-30）：`core/errors.py`（AppError/UpstreamError + 四层 handler，全部错误统一 `{detail, code}`）+ `schemas/envelope.py`（Envelope[T] 泛型信封），首批 9 端点挂 response_model（quotes/kline/order-book/trades/limit-up/limit-break/longhu/search，与既有模型 1:1 零丢字段）；themes/sentiment 等聚合形态第二批
8. **新闻/公告 AI 摘要**（Phase 7 前哨）
9. ~~**Phase 5 选股器 + 评分系统**~~ ✅ 已完成 v1（2026-08-30）：`app/market/tech_score.py`（六维评分卡：趋势0.25/MACD0.20/KDJ0.15/RSI0.10/量价0.15/流动性0.15，可解释依据+失效条件，SCORER_VERSION 版本化）+ `app/services/screener_service.py`（快照截面过滤→候选池 Top150→TDX 日K QFQ→评分，TTL 30min 缓存+single-flight）+ `/api/screener`（Envelope 严格建模）+ `/screener` 页（条件工具条+评分排行表+依据 chips，行点击跳个股）。**防飞刀三修正**（零轴下 MACD 不计 bull、空头排列超卖衰减×0.3、放量下杀≠温和放量——2 年回测 avg_dev 主因的针对性防御）。真实跑：5550→150→148 评分 0 失败 17.6s。测试 287→295
10. Phase 6 后半：回测引擎（按 docs/backtest-rules.md 强制禁令）

---

# 十三、欠缺与技术债（完整清单 → docs/retro-and-gaps.md）

- 功能欠缺 10 项、布局待优化 6 项、技术债 7 项——全部表格式记录，完成划一项
- 关键：StockDetailPanel 拆分、前端 vitest、Next 升级、新闻缓存、交易日历兜底

---

# 十四、行为基线（不可回退）

1. 一屏锁定，滚动只在容器内
2. 红涨绿跌、tabular-nums、数据带来源/时间/质量
3. mock 永不冒充实盘；失败标 stale 不伪造
4. 撮合规则硬拦截不可绕过；第一阶段无真实券商接口
5. 技术结论只给偏向+依据，禁止确定性买卖建议
6. API key 只在 .env；每阶段完成跑全部门禁 + 截图验收 + git checkpoint

---

# 十五、里程碑（33 commits 精选）

`d3856d7` P1-2基线 → `8ecfdb5` P6撮合 → `842bb68` 同花顺官方源 → `ed97223` 布局v3 → `9735649` 自选分组 → `2d67147` 图表增强 → `7114006` P4收尾 → `0e80811` P6-s2 交易页签+B/S → `11e8552` 盘点 → 当前 HEAD `0e80811`+docs×3

---

# 十六、文档索引

architecture / data-sources / **data-source-comparison** / data-dictionary / api / websocket / backtest-rules / longhu / sentiment / **theme-sentiment-methodology** / risk-management / mcp / deployment / ui-redesign-plan / retro-and-gaps / **PROJECT-MASTER（本文档）**
