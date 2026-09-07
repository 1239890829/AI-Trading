# trading 分组深度调研 + 系统盘点 + 优先级计划（2026-09-07）

> 方法：17 仓全量 clone 到 /tmp/research，逐仓实际运行核心能力（A 级）或精读源码（B 级），
> 系统侧以 grep/差集分析实证（不信文档）。证据等级：**A=本轮实测运行 / A'=前轮实测存档 / B=本轮读码未运行**。
> 上游清单基线：2026-09-07 20:46 拉取，17 仓（pandas-datareader/finhack/QuantMind/last30days/Polymarket 已出组）。

---

## 一、逐仓深度调研结论

### 1.1 Financial-API（HiThink-Tech，MIT）—— A 级实测

**结论：文档流无遗漏，但发现一个被完全忽略的官方 Python SDK。**

| 检查项 | 结果 |
|---|---|
| 本地 vendored docs vs 上游 | **0 差异**（diff -rq 实测），文档镜像最新 |
| 59 端点消费盘点 | REST 18 + dump 3 在用；特殊数据 11 端点 **100% 用满**；板块目录/成分/历史K 在用 |
| 未用端点实测 | limit-down-pool（无题材标签/连板数，东财已覆盖→不接）；valuations（74ms，PE/PB/PS/PCF 批量，风险过滤候选）；financials/indicators（growth/profitability 能力维度，PEAD 因子候选）；tickers-search、index prices/snapshot（44ms）、fund ETF 快照（均可用，低优先） |
| **最大遗漏** | **`python/marketdb` 官方 SDK（MIT）**：`checks/quality.py` 8 条 SQL 质量校验（行数/主键唯一/high≥low/OHLC 非负/复权事件主键）、`checks/freshness.py` 交易日滞后校验（滞后超阈值拒绝增量同步，强制重拉全量 dump）、`calculations/adjustment.py` 标准复权因子公式（ratio=((1+s+r)·close_pre)/(close_pre−d+r·p)）、bootstrap.py 一键建库同步 |

**对照**：自建 `backend/scripts/sync_marketdb.py`（272 行）**没有任何质量/新鲜度校验**——增量同步失败或 dump 缺口会静默进仓，直接污染 RPS/因子评估。这是本轮最高价值发现（→ 计划 P0-A）。

### 1.2 akshare（运行时依赖 1.18.94）—— A 级实测

9 个候选接口实测（本机网络域）：

| 接口 | 结果 | 价值 |
|---|---|---|
| `stock_zt_pool_previous_em` | **OK 218ms**，44 行，含昨日封板时间/昨日连板数/涨速/振幅 | **昨涨停溢价指标的官方交叉校验源**（现系统手工从快照+池推导） |
| `stock_lhb_detail_em` | **OK 668ms**，136 行，含「解读」列（机构动向+成功率） | 龙虎榜备源 + 解读文本增量 |
| `stock_zh_a_hist_min_em` / `stock_bid_ask_em` / `stock_zh_a_spot_em` / `stock_hot_rank_em` | 全部 FAIL（push2/push2his 域被墙） | 与既有结论一致：分钟K/五档/全市场快照**无新备源** |
| `fund_etf_hist_sina` / `bond_zh_cov` / `news_cctv` | OK | ETF 日K（大盘观察备用）；可转债（正交）；新闻联播稿（消息面，按推送纪律暂不用） |

现有 akshare_ext 只用 5 接口（三池×3 + CPI + 两融）→ 增量采纳见计划 P1-B。

### 1.3 Vibe-Trading（HKUDS，MIT）—— A 级实测

- **ChinaAEngine 实测 7/7 场景通过**：主板一字涨停拒买 / 正常开盘放行 / 一字跌停拒卖 / 禁做空 / 创业板 ±20% 带 / 整手取整 / 费用模型（买入 5.05=佣金min¥5+过户费；卖出 7.55=+印花税万5 单边）。
- 其 `_blocked_by_limit` 用 **pre_close 定 band、fill 价对 band 判定、未知 band 不伪造 block**（docstring 明确记录了他们修过的 lookahead bug）——与本项目 `limit_anomaly` 三态哲学互相印证。
- 其他增量：`risk_xray.py`、`regime.py`（市场状态）、`metrics.py`（含 fill turnover 序列）、**grounding gate**（中英双语防幻觉价格断言校验，含完整测试套件）——对系统 LLM 助手的「数字必须有据」检查是现成参照。

### 1.4 easytrader（10.2k★）—— B 级 + 环境受限

**重大反转：新增 `easytrader/miniqmt/miniqmt_trader.py`**——封装 xtquant 官方 SDK（XtQuantTrader），完整覆盖：限价/市价（沪深市价类型映射表）、委托/成交/错误/异步回报回调、持仓/资金/撤单查询。**推翻 2026-09-07 上午「平台锁死」的 troubleshoot 结论**（当时只评估了 THS/银河 UI 自动化路线）。
环境限制：xtquant 仅 Windows（macOS 无法实测运行，B 级证据）。→ 实盘路径核心选项（见 §四）。

### 1.5 其余各仓（B 级读码 / A' 前轮存档）

| 仓库 | 本轮结论 |
|---|---|
| **qlib** | A'（前轮完整 workflow 1273s 跑通）；范式不同构判定维持；volume_threshold/板块化涨跌停设计已吸收进 backtest.py |
| **freqtrade** | 加密域；绩效指标/Walk-Forward/跟踪止盈口径已吸收（GPL 代码永不引入）；freqai/protections 未用（加密特有，不采） |
| **myhhub/stock** | TA-Lib 25 指标+CDL 已登记；本轮确认无独有数据面（需 MySQL 未运行）；维持「方法论台账」 |
| **TradingAgents** | 多 agent 编排思想已吸收（picks/engine 五分析师）；需 LLM key 未重跑 |
| **ai-hedge-fund** | 新发现 `event_study/` v2（CAR+bootstrap CI+market model）——面向美股 DataClient 未运行；**思想可移植到 predict/verify 的效果评估** |
| **OpenBB** | provider_capabilities 模式来源；安装重未重跑，维持台账 |
| **vnpy** | 新增 `vnpy/alpha`（Dataset/Model/Strategy/Lab，vnpy 4.x 量化研究方向）；gateway 框架不变；对 miniQMT 路径而言过重 |
| **daily_stock_analysis** | 多平台 bot（feishu_stream/dingtalk/discord）+ 7 数据 fetcher + **SKILL.md 打包模式**（stock_analyzer skill 化）——skill 打包思想值得抄 |
| **WeKnora** | RAG 平台，与交易正交（文档知识库可选用，非交易能力） |
| **cvxportfolio** | 组合优化与打板范式正交；A'（前轮实测 4 次数据全空仓）；维持重评触发=上游修复 |
| **quantskills** | 社区 skill 目录站（214 资产快照），参考型，无代码引入 |
| **Sequoia-X** | **仍无 LICENSE**（本轮复核）——维持忽略 |
| **AlphaMaster** | **AGPL v3**（badge+LICENSE 实证）+ MT5/加密域——一票否决；**建议取消 star** |

---

## 二、系统功能盘点（T2）

### 2.1 功能域 × 数据源 × 仓库映射

| 功能域 | 核心端点/服务 | 数据源链路 | 依赖的 trading 仓库 |
|---|---|---|---|
| 实时行情/快照/五档/分时 | /quotes, /kline, /minute-line, /order-book, /trades | ths→tencent→eastmoney→sina 四源熔断链 | Financial-API（主源） |
| 涨停/炸板/连板 | /limit-up, /limit-down, /market/ladder-check | ths 池系（东财跌停池补位）+ marketdb | Financial-API + market-dumps |
| 板块/题材 | /themes/*, /market/board-fund-flow | ths 板块目录/成分/历史K + 东财板块资金流 | Financial-API |
| 龙虎榜 | /longhu, /market/longhu/theme-trail | ths（带题材标签）+ 东财 | Financial-API |
| 竞价 | /auction/*, /auction-benchmark | ths（唯一源） | Financial-API |
| 热度/异动/飙升 | /market/heat/*, /market/anomalies | ths hot-stock/skyrocket/anomaly 系 | Financial-API |
| 日历 | trade_calendar | ths 官方主源 + persisted | Financial-API |
| 全市场日K仓 | marketdb (DuckDB) | ths market-dumps（daily-k 全量 + 10d 增量 + 复权因子） | Financial-API/market-dumps |
| 双源校验/宏观 | /ext/akshare/* | akshare push2ex 三池 + datacenter | akshare |
| 情绪/ Breadth | /market/sentiment, /market/breadth | 全市场快照自算 + ths 池 | Financial-API |
| 选股/晨报/盘中 | /picks/*, /morning-brief, /intraday-* | 全域数据 + tech_score v3 八维 | Financial-API + akshare + 思想（qlib/TradingAgents/freqtrade） |
| 因子库 | app/factors（37 因子） | marketdb daily_k_adj 离线跑批 | 思想（qlib Alpha158/ai-hedge-fund） |
| 回测 | /backtest/run 等 | marketdb + 自建引擎 | 思想（qlib/freqtrade） |
| 复盘 | /review/* | 本系统数据 + ths | — |
| 推送 | build_push_cards + 飞书 | 本系统数据 | 思想（daily_stock_analysis） |
| 交易执行 | /paper/*（模拟盘） | 内部撮合 | **无实盘通道（→ §四）** |

### 2.2 孤立 / 未派上用场的功能（差集分析 + grep 实证）

**后端建成、前端未接线**（有测试维护，非死代码）：

| 功能 | 规模 | 消费现状 |
|---|---|---|
| **predict 全组**（/predict/* 5 端点） | engine 372 + storage 182 + schemas 78 行 | 前端 0 引用、脚本 0 引用；仅 test_predict 维护。**整组未派上用场** |
| **/risk/state、/risk/check-order** | risk/engine 188 + state_classifier 73 行 | 风控引擎在 picks gate 内被用（test_picks_risk_gate），但独立 HTTP API 无人调 |
| **/backtest/walkforward** | backtest.py 内 | 前端只用 run/mandates/strategies，walkforward 端点 0 消费 |
| **/review/run、/compare、/effectiveness、/methodology/versions** | review 服务内 | 报告/行动项已接线；这 4 个分析型端点无前端消费（疑似 automation 用，**需逐个确认再处置**） |
| **/events GET 列表、POST extract/collect** | events 系统 | 事件详情/影响/个股已接线；列表+提取+收集端点在 09-07 清理后前端 0 引用 |
| **/assistant/entity-dict** | assistant 服务 | 仅 /chat 被消费 |
| **/market/heat/rank-trend、/longhu/{symbol}** | market 路由 | 个股详情页未接（rank-trend 思想已进长廊跟踪） |

**因子库影响范围（用户问点）**：37 因子 → `run_factor_eval.py` 离线跑批 → `backend/data/factors/eval_report.json`（IC/ICIR/覆盖率/冗余标注）→ **人工查阅**。tech_score v3 的八维权重是手工定的，**未由因子评估结果驱动**；factors 与 picks/backtest 之间没有运行时调用链。即：因子库当前是「研究仪表盘」，不是「决策输入」。

---

## 三、互补与替代评估（T3）

| 现有实现 | trading 候选 | 判定 |
|---|---|---|
| 自建 sync_marketdb（无校验） | **Financial-API 官方 marketdb SDK**（MIT，quality/freshness/复权公式） | **替代性增强**：移植校验层，官方口径优先（P0-A） |
| 手工昨涨停溢价推导 | **akshare stock_zt_pool_previous_em**（push2ex 稳定域） | **交叉校验源**：哨兵断言 + 溢价计算双口径互证（P1-B） |
| ths 龙虎榜（题材标签强） | akshare stock_lhb_detail_em（解读文本） | 备源登记，不切主链 |
| backtest.py 成本模型 | **Vibe-Trading ChinaAEngine**（MIT） | 口径互相印证；其「min ¥5 佣金 + 过户费」两项目前 backtest.py 若缺则补（P2） |
| LLM 助手数字可信 | Vibe-Trading grounding gate | 思想参照：关键数字断言需「工具调用佐证」检查 |
| predict 效果评估 | ai-hedge-fund event_study（CAR+bootstrap） | 思想参照：若 predict 激活则引入事件窗评估 |
| 实盘执行 | **easytrader MiniqmtTrader + xtquant** | **唯一可行正路**（见 §四） |
| skill 化分析器 | daily_stock_analysis SKILL.md 模式 | 思想参照：未来把晨报/复盘 skill 化 |

**明确不替代**：分钟K/五档/逐笔（push2 域全域被墙，维持腾讯/新浪）；qlib/vnpy/cvxportfolio 本体引入（范式/重量不符）。

---

## 四、实盘交易接入方案（T5，国信证券）

### 4.1 路径核实（2026-09 检索，多源一致）

| 路径 | 门槛 | 判定 |
|---|---|---|
| **国信 miniQMT（极简模式）+ xtquant** | 20 日日均资产 **10 万**；免费；C4 风险测评；半年交易经验；**需客户经理渠道申请**（APP 自助开户的账户申请不了量化权限） | ✅ **正路**。国信在支持 miniQMT 的券商清单内（多来源一致）；软件无年费，仅正常佣金 |
| QMT 完整版 | 50 万 | 功能过剩，不需要 |
| easytrader THS/银河 UI 自动化 | 零门槛 | ❌ 两次实测平台锁死（09-07 troubleshoot），且脆弱 |
| 金太阳 APP 智能条件单 | 零门槛 | 过渡方案：非 API，支持简单条件触发 |
| Ptrade | 50 万级 | 不考虑 |

**「无固定资金门槛」的诚实结论**：正规券商 API 通道中，miniQMT 的 10 万已是最低档（大同/德邦等小券商「超低门槛」对国信客户无意义——换券商成本更高）。零门槛选项只有条件单过渡或与客户经理协商门槛。

### 4.2 分步落地方案

1. **权限开通（用户操作）**：联系国信客户经理 → 确认「带独立交易模式的 miniQMT」→ 转入资金保持 20 日日均 10 万 → 风险测评 C4 → APP 业务办理提交量化权限 → 1-3 工作日审核。
2. **Windows 环境准备**（xtquant 仅 Windows）：本机虚拟机（Parallels/VMware）或百元级云 Windows 主机；安装国信 QMT 客户端，登录勾选「独立交易/极简模式」；miniQMT 需保持客户端登录在线。
3. **桥接层**（系统侧，macOS）：Windows 上跑一个极薄的 **trade-bridge**（FastAPI，仅内网监听），封装 xtquant 下单/撤单/持仓/资金查询；macOS 后端通过 Tailscale/局域网调用桥。**下单必须经风控闸门**（复用 picks execution-gate + risk engine），桥只接受白名单操作。
4. **灰度序列**：paper 盘（现有）→ 桥查询-only（只读持仓/资金对账）→ 1 手级最小单人工确认 → 条件触发的半自动（单笔金额上限 + 日内笔数上限）→ 自动化。
5. **红线**：桥进程与 QMT 客户端心跳监测，断线即熔断；所有委托落库（/real/trades）；单日额度硬上限；不对接打板排队单（miniQMT 通道延迟不适合打板抢板，只适合接力/低吸确认单）。

---

## 五、优先级执行计划

| 优先级 | 项 | 动作 | 状态 |
|---|---|---|---|
| **P0-A** | marketdb 质量/新鲜度校验 | 移植官方 SDK 的 8 条 SQL + freshness 交易日滞后校验进 sync_marketdb.py（MIT 合法），同步失败显式 blocked | 本轮执行 |
| **P0-B** | 知识库重构 | docs/INDEX.md 类别索引 + 3 个已演示 HTML 删除（提取有效结论）+ 过时审计文档归档 + MEMORY.md 导引图化 | 本轮执行 |
| **P0-C** | 实盘接入决策文档 | 本报告 §四 单独落盘 docs/live-trading-guosen-plan.md；等用户确认资金门槛与 Windows 环境后启动桥接开发 | 方案落盘，开发待确认 |
| **P1-B** | 昨涨停溢价交叉源 | akshare zt_pool_previous 接入 akshare_ext（带 TTL + 三态），复盘哨兵双口径互证 | 本轮执行（小改动+测试） |
| **P1-C** | 取消 star 建议 | AlphaMaster（AGPL+域不符）建议取消；其余维持 | 待用户执行 |
| **P2-D** | backtest 成本口径对照 | 对照 ChinaAEngine 补齐最低佣金/过户费（若缺） | 排队 |
| **P2-E** | 孤立端点处置 | predict 组激活方案（事件窗评估 + 因子联动）或显式标记 dormant；review/events 孤立端点逐个确认 automation 消费后处置 | 排队（需逐个确认） |
| **P2-F** | grounding gate 思想 | assistant 数字断言校验（引用 Vibe-Trading 设计，不抄码） | 排队 |

**刻意不做**：分钟K/五档备源（全域被墙，无解）；qlib/vnpy/cvxportfolio/OpenBB 本体引入；基金 28 端点；新闻消息面接入（推送纪律）。
