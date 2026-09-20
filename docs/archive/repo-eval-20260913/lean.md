# QuantConnect Lean 评估笔记（repo-eval-20260913）

- 仓库：github.com/QuantConnect/Lean（浅克隆，2026-09 主干）
- 机器约束：本机无 Docker 无 dotnet → **不做运行时实测**，纯架构级评估（读 README/源码结构 + grep 核查）
- 评估日期：2026-09-13

## 1. 评估方法

`git clone --depth 1` 后通读目录结构（Engine / Common / Brokerages / Algorithm.Framework / Data），用 grep 核查关键机制：模型接口（IFillModel 等）、市场常量（Common/Market.cs）、交易数据库（Data/market-hours/market-hours-database.json、Data/symbol-properties/symbol-properties-database.csv）、涨跌停/T+1/整手关键词、License 文件。以下结论均标注源码出处。

## 2. 引擎架构（事件驱动 + 可插拔 handler）

- 顶层 `Engine/Engine.cs: Run()` 组装六类 handler（`LeanEngineAlgorithmHandlers`：DataFeed / TransactionHandler / Results / RealTime / Setup / Messages）+ `AlgorithmManager` 推进算法时间循环；数据按 resolution（tick/minute/hour/daily）由 `DataManager`+`SubscriptionManager` 分发。**严格事件驱动回测引擎**，支持 live trading（同一套算法代码接不同 brokerage）。
- **Reality Modeling（现实性建模）接口体系**——这是 Lean 最有价值的资产，全部 per-security 可插拔：
  - `Common/Orders/Fills/IFillModel.cs`（成交模型，默认 `FillModel`/`EquityFillModel`/`ImmediateFillModel`）
  - `Common/Orders/Slippage/ISlippageModel.cs`（滑点，含 `VolumeShareSlippageModel` 市量冲击、`MarketImpactSlippageModel`）
  - `Common/Orders/Fees/IFeeModel.cs`（费用，36 个实现：IB/CharlesSchwab/TDAmeritrade/**IndiaFeeModel**/Zerodha/Binance…，**无 A 股**）
  - `Common/Securities/IBuyingPowerModel.cs`（购买力/保证金，`CashBuyingPowerModel` 会做 `orderQuantity % LotSize` 整手取整并拒不足一手的单）
  - 另有 `ISettlementModel`（`ImmediateSettlementModel`/`DelayedSettlementModel`，T+N 资金结算——注意这是**资金交收**，不是 A 股 T+1 持仓限制）、`IVolatilityModel`、`IPriceVariationModel`（tick 最小变动）。
- Brokerage 抽象：`Brokerages/Brokerage.cs` + `BrokerageFactory.cs`；**真实券商实现已外移到独立仓（Lean.Brokerages.*）**，本仓只留抽象层 + Backtesting/Paper 两个实现。
- `Algorithm.Framework/`：五模块策略框架，Alphas（EmaCross/Macd/Rsi/HistoricalReturns/PairsTrading）→ Insight → Portfolio（EqualWeighting/BlackLitterman/ConfidenceWeighted/InsightWeighting/MaxSharpe 优化器）→ Risk → Execution，C# 与 Python 双实现并列。
- 语言：算法层 C# 与 Python（Python.NET 运行时，Algorithm.Python/ 大量 .py 示例）双支持；引擎核心纯 C#。

## 3. Universe Selection（多阶段选股抽象）

- `Common/Data/UniverseSelection/`：**Coarse 粗筛 → Fine 精筛两阶段**。`CoarseFundamental`（Market/Price/**DollarVolume**/PriceFactor/AdjustedPrice——量价级粗筛，日更全市场）、`FineFundamental`（`Common/Data/Fundamental/FineFundamental.cs`，财报级基本面字段）。
- 其余 Universe 类型：`ConstituentsUniverse`（指数成分）、`ETFConstituentUniverse`、`ScheduledUniverse`、`UserDefinedUniverse`、期权/期货链 `OptionChainUniverse`/`FuturesChainUniverse`。
- 每日触发 selector 返回 Symbol 子集 → 自动增删订阅，配 `SecurityChanges` 事件通知算法。**与本仓「截面过滤→评分」的 screener 思路同构，但 Lean 版是引擎级原生抽象（订阅自动跟随选股结果）**。

## 4. 数据层格式

- **不是 parquet**。全仓 .cs grep "parquet" 零命中。自研二进制容器：`Common/Util/LeanData.cs`（GenerateZipFilePath/GenerateZipEntryName）+ `Common/Data/LeanDataWriter.cs`——**每 symbol×resolution×tickType 一个 .zip，内含 CSV entry**（ZipStreamWriter），目录树按 market/symbol/resolution 组织。
- 交易日历 `Data/market-hours/market-hours-database.json`（每市场 exchange hours + holidays），合约规格 `Data/symbol-properties/symbol-properties-database.csv`。
- 数据本身是商业品：官方数据靠 QuantConnect 云订阅（DLC），本地开源引擎自带的是格式与读盘逻辑。

## 5. A 股适配核查（逐项 grep 实证）

| 项 | 结论 | 证据 |
|---|---|---|
| 市场常量 | **无 A 股市场** | `Common/Market.cs`：usa/india/fx/加密/CME 系/HKFE 等，无 china/sse/szse |
| 交易日历 | **无** | market-hours-database.json 中 "china" 零命中 |
| 合约规格 | **无 A 股条目** | symbol-properties-database.csv 全文仅 1 处 "china"（CME FT5 中国期货）；无 100 股/手配置 |
| 费用模型 | **无 A 股** | Fees/ 有 India/Zerodha/Samco（印度齐全）、美国券商系、加密系 |
| 涨跌停 | **无** | 全仓仅美股 LULD「Limit Up-Limit Down Price Band」quote 条件标志（QuoteConditionFlags.cs:172），无 A 股 ±10%/±20% 限价拒单/排队模型 |
| T+1 | **无** | DelayedSettlementModel 注释自述 "T+3 or T+1" 指资金交收；无持仓冻结/买入当日不可卖模型 |
| 整手 | **机制通用，配置缺失** | `SymbolProperties.LotSize` + `BuyingPowerModel`/`CashBuyingPowerModel` 按 LotSize 取整拒单——给 A 股配 LotSize=100 即可用，但库内无现成条目 |
| 时区 | 仅 tzdb 常量 | `TimeZones.Shanghai` 存在（供将来配市场用），无对应市场实体 |
| 券商通道 | 理论 IB 可通 A 股 | 仓内 IB 实现已外移，只剩 `InteractiveBrokersBrokerageModel` 默认模型（CNH 保证金条目）；本仓红线禁真实券商，此项无关紧要 |
| **结论** | **A 股支持 = 零现成，需自建市场/日历/费率/成交/涨跌停/T+1 全套 model + 数据转换** | — |

## 6. License

- 引擎代码：**Apache License 2.0**（LICENSE 文件全文确认）。
- 注意边界：引擎开源，但 QuantConnect 云平台、官方数据（DLC）为商业服务；自建数据按其 zip 格式灌入可行。

## 7. 支持成本判断与借鉴点

**支持成本（对本项目）——高，不建议引入运行时**：
- 运行时：.NET 8 SDK 或 Docker（本机两者皆无，与「部署先放后面」现状叠加更重）；
- 集成：纯 Python 后端（FastAPI/SQLite/Parquet）要跨语言调用 C# 引擎，或走 Python.NET / 子进程 JSON IPC；策略需重写为 Lean 算法结构（OnData/Universe/Framework），与本仓「六维评分 + 撮合硬拦截 + basis 输出」全部对不上号；
- 数据：需把 TDX 日 K / 四源链数据转成 Lean zip 容器格式，或自写自定义数据源类；
- A 股：第 5 节全部缺项要自己实现（且涨跌停排队、T+1 属于撮合语义，改 Lean 的 fill/settlement 模型需要 C# 功底）。
- 本仓已有撮合引擎（T+1/涨跌停/整手/费用全硬拦截）在 A 股语义上比 Lean 默认能力更完整。

**设计级借鉴（真正有价值的部分）**：
1. **Reality Modeling 接口体系**：把「成交/滑点/费用/购买力/结算」做成 per-security 可插拔 model 接口 + 参数对象（FillModelParameters/OrderFeeParameters 等）——本仓撮合是硬编码拦截流程，若未来要支持「影子账户模拟不同成交假设」，这是最成熟的接口切分范本；
2. **五模块 Framework**（Alpha→Insight→PortfolioConstruction→Risk→Execution）：Insight 把「预测 + 置信 + 时效」封装为一等公民，与本仓「六维评分→组合→风控预检→撮合」管线同构，其 Risk 模块（下单前可拒绝/缩量）与本仓 RiskEngine 预检同位——可参考其接口边界而不是搬代码；
3. **Universe Selection 的 Coarse/Fine 两阶段**：粗筛（量价、日更全市场）→ 精筛（基本面、贵数据少拉），与本仓「截面过滤 → TDX 日K 评分」一致，佐证本仓分两步的做法是行业惯例；其「选股结果自动增删订阅」机制可对照本仓自选/标的池的订阅管理；
4. **market-hours-database.json 的做法**：交易日历+交易时段声明为数据文件（含半日市/节假日），比本仓 trade_calendar.json + 硬编码时段更可配置，可作为本仓日历兜底体系的演进参考。
