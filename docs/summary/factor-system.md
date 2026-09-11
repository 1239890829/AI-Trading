# 因子体系汇总（Factor System Summary）

> **定位**：因子库的**结论与口径**汇总（建设方案 + IC 复核的精华）；治理制度与登记册保留原位，见文末。
> **现役不在此**：`factor-lifecycle-governance.md`（全生命周期制度，**入口**）、`factor-candidates.md`（候选因子登记册）。
> **整理**：2026-09-10。

---

## 1. 三层结构（来源：factor-library-design）

```
factor-candidates.md     候选登记册（来源/原始定义/数据可行性分级/状态）
factor-lifecycle-governance.md  全生命周期制度（挖掘→评估→入库→使用→出库→衰减，六环）
app/factors/library.py   注册表 = 唯一口径锚（运行时消费方）
app/factors/evaluate.py  评估引擎（IC 计算）
```

- 定位：把散落在 11+ 模块的"准因子"收拢为**唯一口径、评估准入、可验证**的因子库。
- **P0 评估闭环已实施（2026-09-07）**；P1/P2 待做。

## 2. 数据质量实测结论（**必读口径**）

- **幸存者偏差实锤**：库内"最后交易日早于全库最新 90 天"的股票 = **0 只** → dump 是"当前在市股票的全历史"，**2016~2026 退市股完全不在库**。
- 影响：**全期回测的多头端收益被系统性高估**（退市股缺失）。所有基于全历史的回测结论必须挂此警示。
- 评估边界：截面 <30 只的交易日跳过；横截面分位类维度（rps/liquidity）在抽样集上算不出全市场口径，须剔除或换算法。

## 3. tech_score 权重 IC 复核（2026-09-08）

样本 500 只（等步抽样，可复跑）× 60 个有效交易日；前向 T+5；Spearman 秩相关；判定线 |IC|≥0.05 有效 / 0.02~0.05 弱 / <0.02 疑噪音。

| 维度 | mean IC | ICIR | IC>0 | 判定 |
|---|---|---|---|---|
| trend | **-0.0836** | -0.417 | 41.7% | **有效（负向）**——降权/反用候选 |
| kdj | -0.0387 | -0.257 | 36.7% | 弱 |
| rsi | -0.0384 | -0.292 | 51.7% | 弱 |
| volume | -0.010 | -0.077 | 46.7% | 疑似噪音 |
| macd | 0.0018 | 0.01 | 51.7% | 疑似噪音 |
| pattern | -0.0008 | -0.016 | 46.7% | 疑似噪音 |

### 处置原则（**不自动改权重**）
1. 判定"有效且正向"的维度：维持或考虑上调——**上调必须递增 `SCORER_VERSION` 并走 walk-forward**。
2. 判定"疑似噪音"的维度：**优先怀疑实现质量**（该维是否经常输出常数/中性档），其次才是降权。
3. 判定"负向有效"的维度：**先复核口径方向是否写反**，确认后再议反向或降权。

## 4. 口径警示（**最易犯的错**）

> **本 IC 是全市场横截面口径，不是选股池口径。**
> `tech_score` 实际只作用于**强势候选池**（趋势是入场前提）——"趋势维在全市场 T+5 反转"与"趋势维在池内的表现"可能完全不同甚至相反。
> 实证印证：2026-09-09 池内条件 IC 复核发现 **amihud20 在全市场为正（+0.030）而在涨停池内为负（-0.082）——方向翻转**；池内激活因子示例：kmid2（+0.060/ICIR 0.35/65% 日为正）、max20（+0.052/ICIR 0.30/61%，lag1 可执行口径）。

**结论：任何因子结论必须同时标"池 + 时点"**（全市场 / 涨停池 / 临板池；当日 / lag1）。

## 5. 09-02 十项候选因子核查（2026-09-10 逐项实测）

来源：`system-review-2026-09-02.md` §4.2（该文已归档，正文并入 `summary/review-governance.md §1`，
但当时只留了一句「10 项可补因子未全实施」——**本次从 git 历史取回原始清单逐项对代码复核**，
避免「文档写未做、实际已完成」的重复开发，这是继 09-10 全量重盘 7 处偏差后的又一次同类纠偏）。

| # | 因子（原优先级） | 实测状态 | 证据 / 阻塞 |
|---|---|---|---|
| 1 | 情绪阈值改滚动分位数（P0） | ✅ 已完成 | `sentiment/calibration.py` 等分位切档（k 档取 p(100/k)）+ `metric_history` 历史库 + `market_context.resolve_bands()`（env > 分位 > 经验值）；闸门层进一步按分位判，见 KB-DEC-015 |
| 2 | 连板晋级率（分层 1进2 / 2进3）（P0） | ✅ 已完成 | `promo_1to2` / `promo_2to3` 均已进校准与历史指标库；闸门消费 `promotion_1to2` 并带分位口径 |
| 3 | 竞价溢价比（P0） | ✅ 已完成 | `app/market/auction_premium.py` + `GET /api/auction-premium`（昨日涨停股今日竞价溢价分布，与复盘 collector 共用采集） |
| 4 | 封板率（P1） | ✅ 等价已有（两粒度） | 题材级 `seal_success_rate = 1 − reopen_rate`（`theme_service.py:1234`）；市场级「炸板率」即其补数且已双源化（P1-19）。**数学上与国信口径（最高价涨停且收盘涨停 / 最高价涨停）等价**，无需另立字段 |
| 5 | 昨日涨停今日表现改**中位数**（P1） | ✅ 已完成 | `sent.prev_perf.median_pct`，闸门以 `PREV_ZT_MEDIAN_FLOOR` 消费（抗极值口径已落地） |
| 6 | 主力资金流（超大单+大单，10 日最优、需市值中性化）（P1） | ❌ 数据阻塞 | 只有**板块级** f62（board_flow daykline，已登记 `board-streak-interaction`）与**个股单点快照**；缺**个股 × 历史逐日**序列 → 无法算因子。解锁需个股资金流历史源 |
| 7 | 龙虎榜共振分（P1） | ❌ 未做（**唯一非数据缺口项**） | 输入**已具备**：`net_buy` / `buy_amount` / `sell_amount` / `org_net_value` / `hot_money_net_value` / `hot_rank`（实测 `GET /api/longhu?date=` 历史可回填）。**阻塞在架构**：因子引擎跑 `marketdb`(duckdb)，龙虎榜在 `ashare.db`(sqlite)，跨库 join 不可行 → 需先落库到 marketdb 或改评估引擎外部数据接入 |
| 8 | 反转因子 `return_1m` 取负（P1） | ✅ 等价已有 | `library.py` 已注册 mom5/10/20/60/120；**反转就是这些因子的负 IC 方向使用**，无需另立因子——直接读 §3 的 IC 实测即可（trend 维 IC −0.084 即实证） |
| 9 | 换手率因子 `turn_1m`（样本期 5 天最优）（P2） | ❌ 数据阻塞 | **无流通股本** → 换手率只能用成交额代理（`evaluate.py::DATA_QUALITY_NOTES['turnover']`）。已登记 `turnover-rate-family`（C 层），解锁后 liq20/amihud20 一并升级为真换手口径 |
| 10 | 业绩超预期（超一致预期 20%+）（P2） | ❌ 数据阻塞 | 无一致预期数据；已登记 `pead-earnings-drift`（C 层，指向 HiThink Financial-API / akshare） |
| 11 | 快报/预告（沪深300 内最强双向因子）（P2） | ❌ 数据阻塞 | 同 #10 数据源；合并登记在 `pead-earnings-drift` 的 note 里 |

**核查结论：11 项中 6 项已完成或等价实现，5 项数据阻塞，0 项「数据具备却未实现」。**
唯一非数据阻塞项（#7 龙虎榜共振分）卡在**跨库架构**而非缺数据 —— 已补登候选登记册，
落地路径与前置条件见其条目。

> **另**：原表「不建议采纳」清单（筹码因子 / 商誉现金流 / ATR 仓位 / RSI-MACD 背离 / 量比）
> 依据的是检索到的反证，**不因时间推移自动失效**；若要重开需提供新实证。

### 5.1 评估产物的运行时消费（S2-11，2026-09-11）

**此前闭环断在最后一米**：`app/factors/evaluate.py::run_full_eval` 能产出 37 因子 × 4 窗口的
IC/ICIR 报告，`data/factors/eval_report.json` 也在磁盘上，但**全站零运行时消费**——
进化大脑的 `factor_ic` 是硬编码的 `{"available": False, "note": "月度复核到期接入"}`。

现已接通：

| 部件 | 位置 | 职责 |
|---|---|---|
| 只读访问层 | `app/factors/report.py` | `load_report` / `freshness` / `top_factors` / `ic_evidence`（三态，40 天超期） |
| 议程接入 | `evolution.py::factor_ic` → `_collect_factor_ic()` | 实测 16 PASS / 7 conditional / 14 fail 进每日议程 |
| 月度复核调度 | `evaluate.py::factor_eval_scheduler` | 每月 1 日 17:30；报告缺失或超 40 天才跑；`last_attempt_ymd` 防失败时反复重跑 |

**两条纪律**：
1. **方向由实测 IC 符号定**（`direction = 1 / -1 / None`），不取 `FactorDef.note` 里的「预期方向」。
   负 IC 亦是有信息——A 股短周期动量常见反转（实测 `corr_pv20` IC −0.069、ICIR −0.733）。
2. **样本内声明**：`caveat` 明写「IC 由本地全历史回测算出，样本内结论；**未做样本外验证**前
   不得直接当选股权重」（准入三级态见 [[KB-DEC-019]]）。消费方不得把这行字吞掉。

**调度注意**：`factor-eval` 的开关 `factor_eval_enabled` **必须留在
`SCHEDULER_SWITCH_ATTRS`** 里——否则测试环境不会关掉它，会在每个测试里真跑 duckdb 全历史扫描。

### 5.2 仍未做的因子体系欠债（来自 factor-library-design，与本表不重复）

| 项 | 内容 | 阻塞 |
|---|---|---|
| 事件/消息面因子 | 传导链方向强度、题材核心度（G4） | ⏳ 需 ≥60 交易日样本积累 |
| `RANK20` | qlib 截面排名（需展开 LAG(1..19) 列） | ✅ 数据具备，纯实现工作（P1） |
| 多窗口扫描 | 同算子 × 窗口 5/10/30/60（验证窗口敏感性） | ✅ 数据具备（P1） |
| 其余 TA-Lib 指标 | MFI / OBV / ADX / CCI… 逐个转正 | MFI/OBV 优先（与 fund_flow 互验） |
| 生命周期六环 | 见 `factor-lifecycle-governance.md` | — |

## 原始文档指针

| 原始文档 | 处置 |
|---|---|
| summary/factor-system.md | 精华并入本文 §1/§5 + `factor-lifecycle-governance.md`；原件归档 |
| factor-ic-review-20260908.md | 精华并入本文 §3/§4；原件归档 |
| factor-lifecycle-governance.md | **保留原位**（治理入口） |
| factor-candidates.md | **保留原位**（登记册数据） |
