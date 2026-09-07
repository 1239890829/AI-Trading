# 策略进化整体方案（Strategy Evolution Plan）

> 2026-09-08 · 基于外部调研（qlib/聚宽/米筐/通联系平台、López de Prado 工作流、CYQ 筹码理论、HMM regime 文献）与系统内部盘点（tech_score v3 / 情绪引擎 / watcher / 复盘闭环）。
> 原则：**补齐而非重造**——系统已有的骨架（情绪相位、regime 权重、复盘 action_items、黄金样本）保留，方案把缺失的「反馈-进化」环补上。

---

## 0. 调研提炼：外部可取之处

| 来源 | 可借鉴做法 | 对本系统的映射 |
|---|---|---|
| qlib（微软） | 滚动 IC 监控（IC<0.05 告警）、动态因子池（regime→因子权重）、滚动训练防衰减 | 信号健康度监控器；tech_score 八维权重按 regime/相位复核 |
| 米筐因子分析 | IC 分析、分层回测、因子衰减曲线报告 | 每日精选/watcher 信号的滚动胜率与衰减曲线 |
| 聚宽/掘金 | 策略全生命周期（研究→回测→模拟→实盘） | akquant 通路已通，选股规则回测化（P2） |
| López de Prado | **meta-labeling**（side/size 分离）、triple-barrier 标注、CUSUM 漂移过滤、walk-forward | ① 环境特征做 meta 层给信号定置信档；② 信号失效用 CUSUM；③ 策略改动走 walk-forward |
| CYQ 筹码理论（陈浩） | 低位单峰密集=吸筹、高位密集=派发、获利盘比例、筹码集中度、「上峰不移下跌不止/下峰锁定行情未尽」 | 筹码分布引擎（marketdb 日K+换手衰减自算，ths 无此端点） |
| HMM regime 文献 | 三态高斯 HMM+Viterbi 做 regime 切换，regime-aware 规则跑赢基准 | 现有情绪相位是规则版 regime；HMM 作为 P2 试验项，不推翻现有相位 |

**用户未提及、需补齐的盲区**：
1. **风险管理**：波动率目标仓位、单票风险预算（ATR 止损已有字段，缺仓位侧规则）。
2. **交易成本**：回测未建模滑点/佣金——akquant 回测时必须带成本参数，否则「回测胜率」不可信。
3. **过拟合防护**：calibration.py 历史分位校准是好底子；补「策略改动必须过 walk-forward」纪律。
4. **数据质量门**：marketdb 同步后缺边界校验（价格跳变、空值率）。

---

## 1. 五大方向：现状 → 差距 → 方案

### 方向 1 动态适应市场（策略失效预警与切换）

**现状**：情绪引擎六相位（冰点/修复/发酵/高潮/分歧/退潮）+ switch_conditions 文本；regime.py 两态权重选择器；watcher 环境项（promo 分位）。**但没有任何「策略自身表现」的监控**——策略失效了系统不会知道，只能靠人看复盘发现。

**差距**：相位互转条件是文本没有自动对账；无信号级滚动命中率；无漂移检测。

**方案**：
- **P0 信号健康度监控器（signal_health）**：对每日精选（T+1/T+5 表现回填自 marketdb）与 watcher confirm/falsify 建立命中记录，滚动 20 次胜率/期望收益 + **CUSUM 漂移检测**；胜率跌破历史基线 → 显式预警（进通知中心 + 自动生成复盘 action_items）。
- **P1 相位对账自动化**：昨日 switch_conditions 预测 vs 实际相位 → 对账结果进复盘报告。
- **P2 HMM regime 试验**：用 marketdb 十年指数日K 训练三态 HMM，与现有六相位对照，仅作参考信号不接管。

### 方向 2 主力行为识别（量价 + 筹码）

**现状**：大盘资金流已接（东财超大/大单净额）；龙虎榜日榜+席位明细+题材迁徙轨迹已有；**个股级资金流未接、筹码分布完全缺失**；ths 官方无筹码端点（capability-map 已核对）。

**差距**：「高位派发陷阱」「龙头启动」目前只有情绪/梯队信号，没有成本结构视角。

**方案**：
- **P0 筹码分布引擎（chip_service）**：marketdb 日K + 换手率衰减模拟（经典 CYQ 算法），输出**获利盘比例、筹码集中度、密集峰位置（支撑/压力）、主峰相对现价位置**。纯计算、零新增数据依赖。
- **P0.5 派发/吸筹规则化（P1 首项）**：筹码指标 × 量价组合判定——高位密集+放量滞涨+获利盘>85% → 派发警示；低位单峰+缩量回踩主峰 → 启动观察。先进 tech_score 附注与每日精选理由，验证胜率后再进权重。
- **P1 个股资金流接入**（东财 fflow 已在技术栈内）→ 大单异动进 watcher 信号集。
- **P2 游资席位画像**：席位明细已有，做「席位 × 后 5 日胜率」跟踪表。

### 方向 3 自有策略体系（融合统一框架）

**现状**：tech_score v3 八维（个股技术面）+ picks 六维×regime 两态（选股综合）+ gate 硬规则（执行闸门）。三套各自成立，**但融合方式是静态规则拼接**——八维权重固定、gate 是二值开关、信号间没有统一的置信度语言。

**差距**：不符合「各模块相互串联、取长补短、优胜劣汰」——问题在缺一个**反馈层**：哪路信号在当前相位下历史更准，没有数据支撑。

**方案**：
- **P0（随 signal_health 落地）**：健康度数据按「信号 × 相位」分桶统计——这就是融合的原料。
- **P1 meta 置信层（规则版）**：综合分 + 相位 + 筹码健康 → 输出「观察/可执行/强执行」三档置信，替代 gate 硬规则的二值跳变（gate 保留为兜底）。
- **P1 tech_score 权重优胜劣汰**：按近 60 日「各维度分位与 T+5 收益秩相关」季度复核，产出 action_items 走人工 confirm（**不自动改权重**，防过拟合+保审计）。
- **P2 meta-labeling 数据版**：信号命中记录积累 ≥200 样本后，用树模型学 Pr(正确|环境特征)，替换规则版置信层。

### 方向 4 数据与性能

**现状**：TTLCache 有命中率端点、quote_hub 1s 轮询+退避、marketdb 16:30 增量同步。**缺 API 分位延迟、DuckDB 慢查询统计、marketdb 数据质量校验**。

**方案**：
- **P1 性能基线**：`/api/system/metrics` 扩展——API p95 延迟、DuckDB 查询耗时 top、同步任务时长趋势。
- **P1 数据质量门**：marketdb 同步后自动校验（空值率、单日涨跌超限告警、复权连续性抽检），异常写 DataGap。

### 方向 5 复盘驱动迭代

**现状**：复盘编排+action_items 状态机（pending→confirmed→applied→reverted）+黄金样本回归，人工闭环已通。**缺口**：① `applied` 只改状态不落引擎（闭环止步人工改代码）；② 信号历史命中没有结构化沉淀，复盘时「某信号最近准不准」只能口算。

**方案**：
- **P0（signal_health 直接服务）**：命中记录即复盘素材——复盘报告自动带「信号健康度」段落。
- **P1 applied 半自动写回**：applied 时生成目标参数的 diff 提示（文件+当前值+建议值），人工一键确认后生效；不做全自动写回。
- **P2 策略改动 walk-forward 门禁**：权重/阈值类 action_items 应用前，必须过 akquant walk-forward 验证（通路已建）。

---

## 2. 分阶段路线图

| 批次 | 内容 | 判定标准 |
|---|---|---|
| **P0（本轮执行）** | ① signal_health 服务+API+测试；② chip_service 筹码引擎核心+API+测试；③ 本方案文档 | 两服务 pytest 全绿，marketdb 真实数据端到端验证 |
| **P1** | 相位对账自动化 / 派发吸筹规则化 / 个股资金流 / meta 置信层(规则版) / tech_score 权重复核 / 性能基线 / 数据质量门 / applied 半自动写回 | 每项独立可验收，先提后做逐项确认 |
| **P2** | HMM regime 试验 / 游资席位画像 / meta-labeling 数据版 / walk-forward 门禁 / 回测成本建模 / 新闻情绪特征入模 | 均依赖 P0/P1 数据积累（≥200 样本或满季度） |

**不做什么（防过度工程）**：不引入 HMM 接管现有情绪相位（规则版可解释且已校准）；不做自动参数写回引擎；不做实时 LLM 盘中决策（watcher 60s 状态机已覆盖，LLM 做叙事层）。

---

## 3. P0 实施记录（2026-09-08 凌晨完成）

- **signal_health**：`backend/app/picks/signal_health.py` + `GET /api/picks/signal-health`。数据源：DailyPickReview 命中记录（verdict/excess_pct）按组合日聚合 + DailyPickSet.meta.market_phase join；滚动 20 组合日胜率/期望超额 + CUSUM 单侧下漂（δ=0.10，阈值 1.5）。三态：`ok|warning|drift|insufficient`（样本 <10 组合日显式 insufficient）。真实库验证：5 组合日 → insufficient（win_rate 0.857 / mean_excess +3.63，样本不足不判 ok）。**预警接线（通知中心/自动 action_items）为 P1**。
- **chip_service**：`backend/app/market/chip.py` + `GET /api/chip?symbol=`（与 /api/quotes 同挂载风格，full=true 回传全网格）。真实库验证：600865 close=10.32 获利盘 39%/主峰 8.617=支撑/压力 11.405；000910 集中度 0.229 高度密集贴现价。`approx=True` 恒标注近似口径。
- **额外发现（P0 级数据缺陷，已止血）**：`daily_pick_review.excess_pct` 列 NOT NULL DEFAULT 0——daily_review 在基准缺失时写 None 会被 ORM default 固化成 0.0，与「超额恰为 0」不可区分（违反评审 B21 语义）。真实库暂未被污染（无 0.0 行）。止血：写入侧 note 打 `[基准缺失]` 前缀保留可甄别性；signal_health 输出带 caveat；**列 nullable 化迁移列 P1**。
