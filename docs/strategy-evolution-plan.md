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

---

## 4. P1 首批实施记录（2026-09-08 盘前完成，commit dc8ef65）

用户指令「看看还有哪些方案计划还没做的，按优先级执行」→ 按数据依赖链落地 P1 前五项：

1. **excess_pct nullable 迁移**（`f6b2c8e4a9d3`）：列 NULLABLE + 移除遗留 server DEFAULT 0（nullable 列上留默认会让「省略列」的写入方重新引入缺失→0 歧义）；真实库 28 行无损、roundtrip 验证。**顺带修复存量缺陷**：e7a2 迁移用 `get_engine()`（指向 settings 主库）而非迁移连接——全新库/测试库上 daily_pick 两表从未建到正确的库，被旧测试断言盲区掩盖，本次 reflect 时炸出并修复（改 `op.get_bind()`）。
2. **signal_health 预警接线**：独立系统规则 `__signal_health__`（不混入 watcher 个股事件流；通知中心 `_NOTIF_RULE_NAMES` 扩容 + 新增 risk 类目「策略风险/健康预警」）+ 当日同状态去重（snapshot.status 比对；warning→drift 升级允许再发）+ 复盘 run() 尾部自动调用。
3. **复盘报告「策略健康」维度**（`review/strategy_health.py`）：信号健康度（滚动胜率/超额/CUSUM）+ 相位对账（`sentiment/reconcile.py`：昨日 switch_conditions 解析目标集 vs 今日实际相位；四态 transition_confirmed/held/off_path/unavailable——「延续」如实标注不冒充命中，今日相位缺失判 unavailable 不判 off_path）。warning→P1 / drift→P0 自动生成改进项（走指纹继承）。
4. **派发/吸筹规则化**（`picks/chip_signal.py`）：派发警示=获利盘≥85%+集中度≤0.45+高位（窗口≥75%分位）+放量（5/20日均量≥1.3）+滞涨（3日≤1.5%）；启动观察=集中度≤0.40+获利盘30~70%+低位（≤45%）+回踩主峰±3%+缩量（≤0.7）。**只留痕不进权重**（bases.chip + 卡片徽标），滚动验证胜率后再议。量能与价均取 provider 日 K；chip 缺仓显式降级不臆造。
5. **meta 置信层规则版**（`picks/meta_confidence.py`）：红线 veto/异动扣分/冰点退潮相位/派发警示→最高观察；缺维→最高可执行；score≥75+全维+相位∈{修复,发酵,高潮}→强执行；score≥60→可执行。gate 空仓闸门保留为兜底。卡片透出三档徽标（理由悬停可见）。

**测试**：新增 test_chip_signal / test_meta_confidence / test_phase_reconcile / test_signal_alert + nullable ORM 语义 + 迁移链 nullable 断言；全量 pytest EXIT=0、pyflakes 清零、tsc/eslint 0 错、vitest 211 通过。
**真实库验证**：健康度 insufficient（5 组合日，胜率 0.857 / 超额 +3.63，三态不判 ok）；600865 主峰支撑、000910 主峰压力，信号无误报。
**生效条件**：8000 于午休 12:05 重启（one-time automation `0623af0b`）后新代码生效；今晚 /generate 产出的组合卡携带 confidence/chip_signal。

**P1 剩余（未执行）**：个股资金流接入 / tech_score 权重 IC 复核 / 性能基线 / 数据质量门 / applied 半自动写回；AI 大脑 P1（工具白名单扩容/上下文扩展/15:35 LLM 综述）另批。

---

## 5. P1 第二批实施记录（2026-09-08 盘中完成）

用户指令「继续」→ 落地 P1 剩余五项 + AI 大脑 P1 批次 + halt_risk 决策修复：

1. **halt_risk ST 板块判定修复**（用户确认「修复」）：`board_of` 改为**代码前缀优先**——300/301→gem(20%)、688/689→star(20%)、8/4/920→bse(30%)，仅沪深主板 ST 用 5%/12%；ST 名称判定收紧为前缀/词边界匹配（防子串误命中）。双创 ST 此前被按 5% 漏判连板。
2. **个股资金流接入**（`market/stock_flow.py` + `GET /api/stock-flow`）：东财 ulist 扩 secid 至个股（复用 fund_flow 的 http/解析，同源不漂移）；北交所无个股资金流 → `no_data` 显式列出；失败不缓存。**watcher 大单异动**：题材成员当日主力净额首破 0.3 亿 → `flow_surge` 提醒（首破语义每日一次，流出侧不报——炸板/跌停池覆盖；阈值经验初值未校准）。冒烟：首破→报警、二拍→去重、未破线→静默。
3. **性能基线**（`core/perf.py` + `GET /api/system/metrics`）：API per-route p50/p95/p99（中间件记路由模板，404 折叠数字段）、DuckDB 慢查询（>300ms 才记）、同步时长趋势（sync_history.json 保留 30 条）。内存环形缓冲零持久化。
4. **数据质量门**（`market/marketdb_quality.py` + `GET /api/system/marketdb-quality`）：在既有 8 条结构检查之上补近窗业务校验——空值率、**复权序列单日涨跌 >31%**（原始序列跳变可能是除权不能当异常）、零收盘；每次同步落 quality_report.json，error 维持退出码 4、warn 可见不失败。
5. **applied 半自动写回**（`review/writeback.py` + PATCH applied 响应）：改进项置 applied 时从标题/说明提取「PARAM 旧值→新值」意图，匹配参数注册表（halt_risk/chip_signal/watcher/CUSUM 阈值），**运行时读当前值**生成 diff（含 stale 漂移提示）；不认识/读不到显式 unresolved。全自动写回仍按方案排除——改文件由人执行。
6. **tech_score 权重 IC 复核**（`scripts/factor_ic_review.py`，报告 `docs/factor-ic-review-20260908.md`）：全市场 500 只 × 近 60 日横截面，Spearman(T+5)。结果：**trend mean IC −0.084（负向显著）**、kdj/rsi 弱负、macd/volume/pattern 噪音区。报告已加解读警示：**全市场截面 ≠ 选股池条件口径**（trend 在强势池内可能是前提而非收益因子），降权前必须先做池内条件 IC——未改任何权重。
7. **AI 大脑 P1**（ai-brain-plan §3.4 P1 项）：工具白名单 10→14（+picks/positions/sentiment/events）；上下文注入扩展到「持仓+最近精选+watcher 事件摘要」（best-effort、接地校验证据池同步扩展）；**15:35 LLM 收盘综述**（`GET /api/assistant/daily-summary`，叙事层 vs 15:45 审计层分工，LLM 失败 available=False 绝不发占位文）+ automation `00a60b78`（工作日 15:35 飞书 text 推送）。LLM 网关健康前提已核实：09-07 复盘 model_degraded=0。

**测试**：test_stock_flow / test_perf / test_marketdb_quality / test_writeback 新增 + test_halt_risk 扩双创 ST 用例 + **test_provider_health 演练竞态修复**（0.2s 冷却两次被全量跑击穿 → 2.0s 冷却 + 轮询自愈）。pyflakes 清零、tsc 0 错、eslint 0 errors、vitest 211 过。
**生效条件**：8000 于 12:05 重启（automation `0623af0b` 已扩验证清单：/api/system/metrics、/api/stock-flow）。
**P1 收官**：strategy-evolution-plan §2 路线图 P1 批次八项全部落地；P2（HMM/席位画像/meta-labeling 数据版/walk-forward 门禁/回测成本建模）按方案依赖数据积累，待排期。
