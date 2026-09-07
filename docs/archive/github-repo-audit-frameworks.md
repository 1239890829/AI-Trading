# GitHub star 分组 trading 审计（第 6 组）：vnpy + freqtrade + OpenBB + AlphaMaster

> 实际执行：freqtrade为重点（#211 深挖回测引擎），其余三仓按原计划完成（#215/#217/#218/#243）。
> 方法：clone 到 `/tmp/gh-audit/`；freqtrade 跑真实回测并用源码检索确认 T+1/涨跌停处理；vnpy 精读 EventEngine 与 A 股 gateway；OpenBB 跑通 SDK 并实测 A 股数据覆盖；AlphaMaster 快速质量判定。
> 证据等级标注：**A = 实际运行验证**；**B = 读了代码未运行**；**C = 只看元数据/README**。
> 说明：本稿为审计结论整理落盘。

---

## 一、freqtrade（重点：回测引擎可用性）

**一句话定位**：加密货币量化交易框架中的事实标准，回测 + 超参优化（hyperopt）+ 交易机器人一体。

### 1.1 实测：跑真实回测 + 源码级核查（证据 A）

- 真实回测跑通，框架工程质量优秀（回测缓存、指标产出、报告排版都成熟）。
- **源码检索（grep 零命中）确认两个架构级缺失：**
  1. **无 T+1 约束**——加密货币 T+0 世界观，代码中不存在「当日买入不可卖出」的任何处理路径；
  2. **无涨跌停概念**——不存在价格连续性中断、排队成交、炸板等 A 股微观结构处理。
- 结论：**freqtrade 回测引擎对本项目不可用**——这两项不是参数能补的，是撮合假设的根本差异。硬用会产出系统性乐观偏差的回测结果。

### 1.2 真金：hyperopt 损失函数设计（证据 A/B）——License 红线：GPLv3，**只抄设计不抄码**

| # | 抄什么 | 细节 | 落点 |
|---|---|---|---|
| 1 | **以 max_drawdown 为目标的损失函数** | hyperopt 内置 `MaxDrawDownHyperOptLoss`：优化目标不是收益最大化而是回撤最小化——与本项目风控优先的定位一致 | backtest.py 参数扫描 |
| 2 | **p-value / SQN 绩效指标** | Sharpe 之外补充统计显著性（p-value）与系统质量数（SQN），防「运气好的一段行情」被误认为有效策略 | 绩效指标成套（第 2 组 25 项的补充） |
| 3 | **回测缓存** | 同参数同数据 hash 不重复回测；参数扫描提速数个量级 | backtest.py 扩展 |

### 1.3 判定

**引擎不用；损失函数思路 + 统计指标 + 缓存设计采纳（设计级，规避 GPLv3 传染）。**

---

## 二、vnpy

**一句话定位**：国内应用最广的量化交易框架，事件驱动架构（EventEngine）+ 全品类 gateway。

### 2.1 实测评估（证据 B）

- EventEngine：单线程队列 + 订阅分发的经典实现。本项目 watcher/quote_hub 已有自己的 asyncio 事件结构，**换成 vnpy EventEngine 是负改造**（引入跨范式依赖，无行为增益）。
- A 股 gateway：其 CTP/股票 gateway 面向实盘柜台对接，需要券商柜台资质/账户体系，个人研究场景用不上。

**判定：不引入。**

---

## 三、OpenBB

**一句话定位**：开源 Bloomberg 终端，强项是「能力注册表」式的 provider 抽象层。

### 3.1 实测：SDK 跑通 + A 股覆盖实测（证据 A）

- SDK 安装跑通，provider 抽象层设计确实优秀（统一命令行 → 任意 provider 注册即插）。
- **A 股数据覆盖实测：为零。** 注册的 provider（FMP/Polygon/Yahoo 等）无一提供 A 股涨停池/题材/连板数据。

### 3.2 真金：provider 能力注册表模式（证据 B）

- 其 `ProviderInterface`：每个 provider 声明自己「能提供什么端点 + 需要什么凭据 + 数据的字段 schema」，运行时按能力查询路由请求。
- 对本项目的映射：现有 4 个 provider（tencent/eastmoney/sina/ths）的可用能力散落在各 provider 方法里，没有「能力注册 + 按能力路由」的显式结构——这正是 `docs/data-source-comparison.md` 行动清单落地后会越来越痛的点（多源竞速 + 备源切换需要知道谁有什么）。

**判定：代码不引入；provider 能力注册表模式采纳为设计参考（低优先级，待 provider 健康可观测体系稳定后再评估）。**

---

## 四、AlphaMaster

**一句话定位**：宣称 AI 选股的项目。

### 4.1 判定（证据 C/快速验证）

- 快速验证：代码质量与文档完整性不足以支撑深入研究，star/社区信号弱，无独有设计。

**判定：忽略（低质量项目，不在收束清单展开）。**

---

## 五、本组收束

| 仓库 | 引入本体 | 采纳设计 | 红线 |
|---|---|---|---|
| freqtrade | 否（无 T+1/涨跌停，架构级缺失） | max_drawdown 损失函数、p-value/SQN、回测缓存 | GPLv3 只抄设计 |
| vnpy | 否（EventEngine 负改造） | — | — |
| OpenBB | 否（A 股覆盖为零） | provider 能力注册表模式（低优先级） | — |
| AlphaMaster | 否（低质量） | — | — |
