# nautilus_trader 评估笔记（2026-09-13，macOS arm64 实测）

## 1. 安装实测
- 版本：**nautilus_trader 1.221.0**（PyPI 稳定 wheel）；Python 3.11.12。
- **一次成功，13s**（清华镜像）：macOS arm64 预编译 wheel（Cython/Rust 核心已编译），pandas 之外的运行时依赖极少。
- **大坑：仓库 HEAD 文档已是 2.x 口径，与 1.221.0 wheel 存在 API 漂移**。实测踩了三处：`LoggingConfig` 无 `stdout_level`（2.x 才有，1.x 是 msgspec 字符串字段）；`LogLevel` 在 `nautilus_trader.common.enums`（教程的 `nautilus_trader.common` 导入失败）；`Bar` 构造签名 1.x 是 `(bar_type, o/h/l/c, volume, ts_event, ts_init)`（2.x 教程多了 price_prec/size_prec）。若接 2.x 需按 `MIGRATION_V2.md` 全面对照。
- 版本策略：要稳定走 1.221.x，追新走 `--pre`（2.x beta）——两套 API 不兼容，接入时必须钉死版本。

## 2. 架构核心（消息总线 + Rust 核心 + adapter 体系）
- **消息总线事件驱动**：`MessageBus` 单总线 + Actor 模型；`BacktestEngine` 组装 DataEngine / RiskEngine / ExecEngine / OrderEmulator，一切交互（数据、订单、成交、事件）走总线发布订阅，策略只实现 `on_bar/on_quote/on_data/on_start` 等回调——与我们 QuoteHub 的推拉混合完全不同范式，是「先注册组件、引擎按时间戳重放」的确定性回测。
- **Rust/Cython 核心**：撮合、订单簿、指标在编译层（`.so`），Python 侧只剩胶水；600 根 bar 回测初始化 43ms 级。
- **adapter 体系**：DataClient + ExecutionClient 双客户端抽象，官方 adapters = betfair/binance/bitmex/bybit/coinbase_intx/databento/dydx/hyperliquid/interactive_brokers/okx/polymarket/sandbox/tardis——**无 A 股 adapter**，接 L2 需自写一个 DataClient（把行情流翻译成 `QuoteTick/TradeTick/OrderBookDeltas` 发上总线）。
- 撮合扩展点：`add_venue(...)` 一处暴露 `fill_model`（`FillModel` 概率成交/滑点、`ProbabilisticFillModel`）、`fee_model`（MakerTaker/Fixed/PerContract + `ImportableFeeModelConfig` 工厂→任意自定义费用模型）、`latency_model`、`book_type`、`modules`（SimulationModule 插槽）、`bar_execution/trade_execution` 开关。

## 3. 核心功能清单
回测引擎（L1/L2/L3 订单簿撮合、tick/bar/trade 执行）、实时引擎（同套策略代码）、Portfolio/账户多币种、RiskEngine 预检（可 bypass）、指标库（EMA 等，注册到 bar 流自动更新）、Arrow 序列化、绩效报告（generate_order_fills_report / account_report / positions_report）。

## 4. 最小运行证据（`EVAL/demo_nautilus_backtest.py`）
合成 600 根 1 分钟 K 线（随机游走）+ 伪 A 股合约 `000001.XSHE` + **CASH 账户 CNY 起始 100 万（A 股口径：无杠杆、`allow_cash_borrowing=False` 禁做空）** + EMA(5/20) long-only 策略（金叉市价买 1000 股=10 手、死叉平仓）→ `engine.run()` 端到端跑通：**3 笔成交、账户报告/成交报告正常生成**（CASH 账户、费用模型默认零费）。

## 5. A 股适配逐项核查（源码 grep + 实测 import）
| 项 | 结论 |
|---|---|
| T+1 | ❌ 无内置概念（`t+1` 全库无命中；settlement 仅指期货结算币种）。可用 SimulationModule 或自定义撮合规则层实现当日买入锁仓 |
| 涨跌停 | ❌ 无 `price_limit/limit_up` 概念（`Price` 对象 + instrument 无涨跌停字段）。可在策略前置 RiskEngine 或 SimulationModule 里实现拒单 |
| 整手 | 🔶 部分：instrument 有 `size_increment`/lot 字段可约束数量精度，但无「100 股整手倍数」硬校验，需在 RiskEngine 加规则 |
| 费用模型 | ✅ **一等扩展点**：`FeeModel` 基类（MakerTaker/Fixed/PerContract 内置 + ImportableFeeModelConfig 动态加载），印花税/佣金/过户费三段式可直接子类化 |
| L2/逐笔 | ✅ **原生一等公民**：`BookType = L1_MBP/L2_MBP/L3_MBO`，`OrderBookDeltas`/`OrderBookDepth10` 数据类型 + `OrderBook` 模型 + `add_venue(book_type=...)` 撮合于订单簿——四仓对比中最强的 L2 支持 |

## 6. License
**GNU LGPL-3.0**（弱 copyleft：动态链接/独立使用无传染，但若修改库源码本身需开源；内部使用无碍，与 zipline 的 Apache-2.0 相比更「紧」一档）。

## 7. 对本项目的借鉴点（作为未来 L2 行情引擎的适配度与接入成本）
- **结论：nautilus 是四选一里唯一把 L2/逐笔当一等公民的引擎，方向上适配「未来接 L2」**；但接入成本集中于两块：① 自写 A 股 DataClient adapter（L2 行情流 → OrderBookDeltas 发总线，工作量中等、协议清晰）；② T+1/涨跌停/整手三条 A 股硬规则要做成 SimulationModule/自定义撮合层（我们的 `app/paper/engine.py` 已有现成规则语义可平移，但两套撮合并存有口径漂移风险——建议 nautilus 只做 L2 回测研究线，模拟交易线仍走自家引擎，红线 5 的撮合拦截不旁落）。
- **性价比判断**：当前无 L2 数据源（AGENTS.md 已裁：外部付费数据源不要了，四家均无 L2），L2 引擎属「等数据触发」项；届时 nautilus 的 LGPL-3.0 内部使用无碍。1.x/2.x API 正在断代，真要接入应等 2.x 稳定再钉版本。
- 可平移的抽象（不引库也能借鉴）：MessageBus 单总线 + Actor 回调的「策略与基础设施解耦」、FeeModel/FillModel/LatencyModel 的撮合可插拔三件套（可给 `app/paper/engine.py` 的费用与成交模型留同类扩展口）。
