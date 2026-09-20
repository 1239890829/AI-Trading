# zipline-reloaded 评估笔记（2026-09-13，macOS arm64 实测）

## 1. 安装实测
- 版本：**zipline-reloaded 3.1.1**（PyPI 最新稳定）；Python 3.11.12（homebrew python@3.11）。
- **一次成功，31s**（清华镜像）：无任何依赖冲突。实际装上 numpy 2.4.6 / pandas 2.3.3 / bcolz-zipline 1.13.0（社区 fork 的 bcolz，兼容 numpy 2.x）——历史上 zipline 的 bcolz/numpy bounds 地狱在这个 fork 里已经解决，无需 `--no-deps` 二次尝试。
- 坑：无安装坑。API 坑在 `AssetDBWriter.write` 只收 DataFrame（dict 报 `KeyError: exchange`），内存 sqlite 必须走 `sqlalchemy.create_engine`（原生 sqlite3.Connection 无 `.begin`）；引擎 `get_loader` 必须返回**同一 loader 实例**（按对象身份分组批量加载，每次新建实例会 KeyError）。
- 未实测真实 bundle ingestion（需自建 `ingest` 函数 + 实现 `PipelineLoader`——**集成成本点**，数据接入层要自己写，对接我们的 TDX 日K/官方题材成分需要写自定义 bundle + loader）。

## 2. 架构核心
- **向量化截面范式**：Pipeline 是一张 Term 有向无环图（Factor/Filter/Classifier/Column），引擎按窗口批量物化（`window_length` 只取回看数据，天然防前视）；与 nautilus 的事件驱动 tick 范式完全互补/相反。
- 引擎：`SimplePipelineEngine(get_loader, asset_finder, default_domain)`；数据经 `DataFrameLoader`/自定义 loader 注入；域（Domain）决定日历与国家码。
- 扩展点：自定义 `DataSet`（`Column` 声明任意列，如连板高度/题材热度）、`CustomFactor`、`CustomFilter`、自定义 loader（对接 Parquet 快照即可）。

## 3. 核心功能清单
Pipeline API（因子/筛选/分类器 + 内存或 bundle 引擎）、TradingAlgorithm 模拟撮合（commission/slippage 可插拔：`PerShare`/`PerDollar`/`PerTrade`、`VolumeShareSlippage` 等）、bundle 数据版本管理（zipline CLI）、资产库（sqlite AssetFinder）、多日历（exchange_calendars 全集）。

## 4. 最小运行证据（`EVAL/demo_zipline_pipeline.py`）
CN_EQUITIES 域 + 内存 AssetFinder（3 只伪 A 股）+ 自定义 `CNStockDataSet(close/lianban)` + `SimpleMovingAverage(3)` + `CustomFactor` 动量 + `mom > 0` Filter Screen，`engine.run_pipeline` 输出因子矩阵（按 screen 过滤后仅剩动量为正的票），端到端跑通，**XSHG 日历开箱即用**（`domain.py:224 CN_EQUITIES = EquityCalendarDomain(CountryCode.CHINA, "XSHG")`）。

## 5. A 股适配逐项核查（源码 grep 实证）
| 项 | 结论 |
|---|---|
| T+1 | ❌ 无。仅美股式 `settled_cash`（ledger.py:724，T+2/3 现金结算思想），无持仓 T+1 锁定 |
| 涨跌停 | ❌ 无（`limit_up/price_limit` 全库 0 命中；仅 `halted` 无关项） |
| 整手 | ❌ 无 lot 概念（美股碎股口径） |
| 费用模型 | ✅ 可插拔抽象（`finance/commission.py`：CommissionModel 基类 + PerShare/PerDollar/PerTrade；slippage 同理），子类化即可实现印花税/过户费/佣金三段式 |
| 日历 | ✅ XSHG/XSHE 开箱即用（3.x Domain 机制） |
| L2/逐笔 | ❌ 完全无（日线/分钟 bar 向量化范式） |

## 6. License
Apache License 2.0（商用/闭源集成无负担）。

## 7. 对本项目的借鉴点（Pipeline API vs 我们的 app/factors/）
- **直接引入整个框架：不推荐**。它是「自建资产库 + 自建 bundle + 自建日历域」的重依赖体系，与我们 TDX 日K + Parquet 快照 + 官方题材目录的数据底座重复；收益主要是因子声明式 DAG，而我们的 qlib Alpha158 + TA-Lib + RankIC 评估闭环已在跑。
- **值得抄的抽象**（借鉴成本低）：① `Factor/Filter/Classifier` + `window_length` 回看语义——把「同日市场中性基准」等核验口径表达成 Filter 组合，天然防前视，可平移到 `app/research/strategy_verify.py` 的条件表达式 DSL；② Domain 概念（日历+国家码绑一处的口径单点）；③ `CustomFactor.compute(today, assets, out, *inputs)` 的「numpy 矩阵进、矩阵出」签名，比我们现在的逐因子实现更利于批量向量化。
- **结论**：Pipeline 是「因子研究的形态学参考」，不是可直接接入的引擎；真要用它的截面回测还需先写 bundle ingest（成本 > 收益）。
