# 题材板块与个股盘中跟踪选股系统（选股 2.0）设计与落地计划

> 2026-09-02 起草。对应用户五条需求：多维打分 / 盘前预判 / 盘中验证与提醒 / 潜伏与买点 / 每日复盘与回测迭代。
> 原则：**规则必须是纯函数**（调度、取数、分发在服务层），线上与回测共用同一套规则，否则"回测通过的规则"与"线上跑的规则"必然漂移。
> 红线：不真实下单；缺数据显式 `unknown`，绝不猜；阈值集中成常量表，回测后可调；所有提醒标注"非投资建议"。

## 0. 五条需求 → 模块映射

| # | 需求 | 承载模块 | 现状 |
|---|---|---|---|
| 1 | 多维打分（消息/资金/情绪/基本面/技术） | `picks/engine.py` 六维 + `picks/regime.py` 动态权重 | 已有五维+梯队，需增强消息/资金/情绪三处 |
| 2 | 盘前预判 1–3 个方向 | `predict/`（ThemePrediction）扩展为 morning brief | 有预判引擎，无盘前节拍与固定产物 |
| 3 | 盘中验证与固定格式提醒 | **新增** `picks/intraday_rules.py` + `picks/watcher.py` | **缺失主体**（本设计的核心增量） |
| 4 | 追涨/潜伏、退潮判定、买点条件 | `intraday_rules.py` + `dragon_service.entry_checklist` | 雏形分散，需统一成规则库 |
| 5 | 复盘对照、胜率统计、模型迭代 | `review/` + `daily_pick_review` 扩展方向级对照 | 有个股复盘，无"盘前预判 vs 实际"对照 |

## 1. 现状盘点（2026-09-02 全模块调研）

**可直接复用**：
- 六维评分 `score_sentiment/news/tech/fundamental/capital` + `synthesize`（否决×0.4）+ `regime` 双周期权重
- 情绪引擎（heat/earning 双轴 + phase 矩阵 + **P0-3b 历史分位校准已落地**，percentile 可直接作为情绪分）
- 题材看板 `build_theme_board`（ths 涨停原因归因 + 东财板块涨幅/净流入 + ths 板块 K 交叉验证）与梯队角色/完整度
- 事件卡 `EventCard`（source_tier 1–5 / fact_kind / certainty / half_life / direction 链）
- 快照+breadth（分板涨跌停阈值双边判定）、龙虎榜四维分档、`NotifierRegistry`（console/in-app）
- 复盘链路（ReviewData → RulesAnalyzer → ReviewReport）

**需扩展**：
- `score_news` 现在只数利好/利空条数（+18/−25）→ 改事件强度加权
- `score_sentiment` 用绝对 phase 分值 → 接校准后分位
- `score_capital` 的 `net_inflow` 依赖东财单股接口（无独立 provider 方法，本机 push2his 被墙过）→ 需实测后定
- `predict` 有预判但未进盘中验证循环；`theme` 热度无时序落库（回测受限）

**缺失**：盘中节拍调度器、确认/证伪规则引擎、八段式提醒组装、方向级复盘对照、回测框架、真实推送通道（邮件/企微，P1 阻塞项不变）。

## 2. 架构：三节拍决策流水线

```
数据层（已有 provider/hub/snapshot/theme/events）
   │
因子层  picks/engine 六维 + calibration 分位 + echelon/gate
   │
决策层 ── 盘前 08:30–09:25：morning brief（方向排序 → 标的池 → 触发条件）落库
      ├─ 盘中 09:30–15:00：watcher 每 60s 一拍
      │     预判方向池 → confirm/falsify 规则 → 八段式提醒 → NotifierRegistry
      └─ 盘后 15:30：复盘对照（方向级 + 提醒级）→ 胜率统计 → 误判分类落库
   │
呈现层  前端新页「盘中跟踪」+ 现有 picks/predict 页增强
```

调度挂 `main.py` lifespan（与 review_scheduler / metric backfill 同模式）：盘前任务交易日 08:30 触发一次；watcher 盘中循环；盘后 15:35 对照任务。**全部可经 env 开关关闭**。

## 3. 多维打分（需求 1）

沿用 `regime` 三套权重（业绩驱动期：基本面 0.25；业绩空窗期：情绪 0.25；平衡），六维增强：

| 维度 | 现状 | 增强（批次 A） | 缺失口径 |
|---|---|---|---|
| sentiment | phase 绝对分（修复 80…冰点 25）×0.7 + 题材涨家比 ±15 | phase 分改由**校准后 heat/earning 分位**合成：`50 + (heat_pct−50)×0.4 + (earning_pct−50)×0.4`；分位来自 `compute_market_sentiment().calibration.percentile`，库旧（stale≥3）自动回退绝对分并在 basis 标注 | 快照未就绪 → 中性 50（不变） |
| news | 利好条数 +18 / 利空 −25 | 事件强度：`Σ direction.strength × tier_weight(source_tier) × certainty_weight`，tier 1–5 → 权重 1.0/0.8/0.6/0.4/0.2，certainty done=1.0 / proposed=0.6 / rumor=0.3；利好求和为正项、利空为负项，映射到 ±45 修正 50 基准 | 无事件 → 50（不变） |
| capital | 东财单股 net_inflow + 量比 + LHB | 新增 provider 方法 `get_capital_flow(symbol)`（东财 f62 系，**先实测本机可达性**，被墙则降级腾讯量比+换手并在 basis 标注 unknown） | 全缺 → 50 + unknown 标注 |
| tech | score_stock 六维卡 | 不变 | — |
| fundamental | PE/增速/ROE/毛利率 | 不变 | — |
| echelon | 角色×阶段×完整度 | 不变 | — |

**否决项不变**（业绩暴雷、ST、停牌、空仓闸门×0.4）。

## 4. 盘前预判（需求 2）

`predict` 扩展出盘前节拍（不新建平行模块）：

1. **证据采集**（复用 `collector.collect_predict_evidence`）：近 24h EventCard（方向 ±1、strength、tier、certainty）、昨日题材强度榜、昨日情绪 phase 与晋级率分位、ths 竞价快照（09:15 后）。
2. **方向排序**（`intraday_rules.rank_directions`，纯函数）：
   `score = 事件强度和 × 1.0 + 题材近 3 日强度分 × 0.6 + 梯队完整度 × 0.4 + 情绪适配 × 0.3`
   ——退潮/冰点期防守方向（红利/银行）加成 +10，高潮期题材方向加成 +10（情绪适配项）。
3. **产物**：top 1–3 方向，每个含——逻辑依据（事件标题+链路）、标的池（题材成分 ∩ 梯队角色，≤10 只）、**触发条件**（板块涨幅阈值 / 龙头涨停 / 竞价溢价 >103%）、证伪条件（§5 反向）。落库 `prediction_reports`（复用），字段加 `direction_pool` / `trigger_conditions` / `falsify_conditions`。
4. 展示：前端 predict 页加「盘前简报」卡。

## 5. 盘中验证与提醒（需求 3，核心）

`picks/intraday_rules.py` 纯函数规则库，watcher 每 60s 调用。**所有阈值集中常量表**（批次 D 回测调参唯一入口）。

### 5.1 确认走强（全部满足才确认）

| # | 指标 | 阈值 | 数据源 | 缺数据时 |
|---|---|---|---|---|
| 1 | 板块涨幅 | ≥1.5%（10:00 前）/ ≥2.5%（10:00 后）——早盘冲高回落常态化，阈值分段 | 东财板块 push2delay（已有） | 该指标记 unmet+unknown，不默认通过 |
| 2 | 板块内涨停 | ≥2 家 或 出现 ≥3 板高度股 | ths 涨停池实时 | 同上 |
| 3 | 龙头强度 | 梯队最高股涨幅 ≥5% 或涨停 | 快照/涨停池 | 同上 |
| 4 | 量能 | 板块成交额占全市场比 ≥ 昨日同时段 ×1.3；无历史则量比 ≥1.5 | 快照+板块（历史需批次 D 落库，先用量比） | 降级用量比，basis 标注 |
| 5 | 环境 | 大盘非冰点/退潮 且 promo_1to2 分位 ≥20 | sentiment 引擎（已校准） | 环境未知 → 记 unmet |

### 5.2 证伪放弃（任一触发即证伪，方向降级观察或移除）

| # | 信号 | 阈值 |
|---|---|---|
| 1 | 峰值回撤 | 板块涨幅较盘中峰值回撤 ≥2 个百分点 |
| 2 | 龙头炸板 | 龙头炸板 且 板块内跌停 ≥1 |
| 3 | 转负持续 | 板块涨幅 <0 且持续 ≥15 分钟（连续 15 拍） |
| 4 | 环境证伪 | promo_1to2 分位 <20 或 phase 进入退潮/冰点 → 全部方向降级 |

### 5.3 八段式提醒（固定格式，`build_alert` 生成）

```
【盘中机会】方向：粮食安全
1. 个股：XXX（代码）· 梯队角色：龙头/中军/跟风
2. 触发指标：板块 +2.8%（阈值 2.5%）· 涨停 3 家 · 龙头 +6.1% · 量比 1.7 · 环境 发酵
3. 逻辑：厄尔尼诺预期 → 减产传导种业（事件：…，tier2，done）
4. 买入区间：现价 ±3% ∩ 支撑/压力（build_buy_range）
5. 止损：买入价 −5% 或跌破分时前低（取更近者）
6. 仓位：基础 10% × 确认强度系数（0.5/0.75/1.0），上限 20%
7. 风险点：板块高位（涨停家数分位 >80）· 明日竞价溢价 <103% 则一日游
8. 状态：非投资建议，模拟跟踪
```

提醒经 `NotifierRegistry.dispatch` 分发（现 console/in-app；企微/邮件为 P1 阻塞项）。同方向同个股**当日去重**（内存 + 落库指纹）。

## 6. 潜伏与买点（需求 4）

### 6.1 追涨 vs 潜伏（`entry_mode`）

| 板块阶段（judge_theme_stage） | 情绪相位 | 模式 |
|---|---|---|
| 启动/发酵 | 非退潮 | **追涨**（只做龙头/中军，跟风不追） |
| 主升/高潮 | 高潮 | 追涨但仓位减半（高位） |
| 分歧 | — | **潜伏低吸**（等回调企稳） |
| 退潮 | 退潮/冰点 | **观望**（仅记录不提醒） |

### 6.2 退潮 vs 趋势结束（`ebb_or_end`，三者取一）

| 判定 | 条件（同时满足越多越确定） |
|---|---|
| 短暂退潮（可再启动） | promo_1to2 分位 ≥40，或 高度未断层（最高板−次高板 ≤1），或 龙头仍站 MA5 |
| 趋势结束（离场） | 分位 <20 **且** 高度断层 ≥2 **且** 龙头跌破 MA10 |
| 观望带 | 介于两者之间，不给出方向性结论 |

### 6.3 进场条件（`entry_plan`）

- **回调企稳**（潜伏）：回踩 MA5/MA10 缩量（量比 <0.8）+ 分时不破前低 + 板块涨幅仍 >0 → 买入区间 = MA5 附近 ±2%，止损 = MA10 下方 1%
- **放量突破**（追涨）：突破近 20 日平台高点 + 量比 ≥2 + 板块共振（涨幅 ≥1.5%）→ 买入区间 = 突破价 ±1.5%，止损 = 平台高点 −3%
- **龙头回封**：炸板后回封 + 封单比 ≥ dragon_service 阈值 + 二次分时不破均价线 → 买入区间 = 涨停价（排板），止损 = 炸板价 −2%
- **观望**：环境冰点/退潮、题材无明确龙头、晋级率分位 <20、个股停牌/ST/业绩雷（复用 gate/veto）

## 7. 每日复盘与迭代（需求 5）

1. **方向级对照**（15:35 任务）：盘前 top 方向 → 实际板块涨幅/涨停数 → 分类：发酵（确认规则当日曾全满足）/ 半发酵（满足 ≥3 项）/ 证伪（触发任一证伪）/ 无波动。落库 `prediction_themes.verify_*`（复用）。
2. **提醒级对照**：每条盘中提醒 → T+1、T+3 收益（日 K 回算）→ 胜率/均盈亏比统计，API `/api/picks/intraday-review`。
3. **误判分类**（`classify_failure` 扩展方向级）：逻辑失效（事件证伪）/ 阈值过敏（确认后立刻回撤）/ 数据缺失误导 / 环境突变。每类计数进 review agent 摘要，作为批次 D 调参输入。
4. 前端：盘中跟踪页底部加「今日盘前 vs 实际」对照表 + 近 30 日胜率曲线。

## 8. 回测验证（需求 5 的"大量历史案例"——诚实的数据边界）

| 维度 | 可回测性 | 方案 |
|---|---|---|
| 涨停池/炸板池/连板天梯 | ✅ ths 可回溯 ≥2 年（已实测） | 直接回放 |
| 板块涨幅/成交额 | ✅ ths 官方板块 K 线（theme_catalog 已接） | 回放近 1 年 |
| 情绪指标（含晋级率） | ✅ `metric_history` 已有 120 天，可扩到 250 天 | 分位规则直接回放 |
| 个股日 K | ✅ ths 腾讯日 K | T+1/T+3 收益回算 |
| **消息面** | ❌ 新闻源无历史 | 只能**前向积累**：从上线日起落库事件快照；辅以人工案例标注（用户复盘时录入"当时看到了什么消息"） |
| 盘中分时 | ⚠️ 仅 5 天快照 + 4 只票 minutes | 规则中的分时条件回测时用日 K 近似（OHLC），上线后积累分钟级 |

**回测协议**（批次 D）：
- 伪盘前预判：T-1 日题材强度 top3 作为 T 日"预判方向"（消息面缺席的替代锚）；
- 对每个 T：跑 §5.1 确认规则（用 T 日实时口径的日 K 近似）→ 统计确认信号触发后 T+1 开盘买入的 T+3 收益 vs 全市场基线；
- 产出：各阈值网格（板块涨幅 1.0/1.5/2.0/2.5 × 量比 1.3/1.5/2.0）的命中率表 → 择优回写常量表；
- **防止过拟合**：样本 ≥120 个交易日、报告必须同时给"规则触发日"与"未触发日"的基线对比。

## 9. 分批落地计划

| 批次 | 内容 | 状态 |
|---|---|---|
| **A** | `picks/intraday_rules.py` 规则引擎（rank/confirm/falsify/entry_mode/ebb_or_end/entry_plan/build_alert，纯函数+全量测试）；`score_sentiment` 接校准分位；`score_news` 事件强度 | ✅ 2026-09-02（commit `482601a`） |
| **B** | 盘前节拍（morning brief 生成+落盘+API）+ `picks/watcher.py` 盘中调度 + 提醒分发与去重 | ✅ 2026-09-02（见下方实现注记） |
| C | 复盘对照任务 + 胜率统计 API + 前端「盘中跟踪」页（predict 页简报卡不做，见注记） | ✅ 2026-09-02（见下方实现注记） |
| D | 板块热度时序落库（前向）+ 回测框架 + 网格调参报告 + `get_capital_flow` 实测定源 | 待 C 完 |

### 批次 B 实现注记（2026-09-02）

- `picks/morning_brief.py`：`collect_evidence`（事件强度/涨停池题材聚合/情绪环境三路证据，失败进 `missing` 显式呈现）→ `assemble_brief`（**纯函数**，rank_directions top3 + 每方向标的池≤10/触发 5 条/证伪 4 条/entry_mode）→ `build_and_save`。盘前证据池日期走 `_evidence_pool_date`：**09:25 前取上一交易日**（当日盘前池必空，直接取 `last_trade_date` 会让题材动能全体归零且界面看不出是口径错）。
- **持久化改文件**（`data/picks/briefs/YYYYMMDD.json`，原子写），不用 prediction_reports 表——predict 的 `save_report` 按 target_date 单键 upsert，同表共存时周末预判与盘中简报会互相覆盖；`get_report` 也会把简报误当预判返回。批次 C 若需 SQL 检索再加镜像。
- `picks/watcher.py`：`DirectionTracker` 纯状态机（峰值/转负计数/证伪一次性/确认提醒按 (方向,个股) 当日去重、每方向每日上限 3 条防龙头轮动刷屏）+ `IntradayWatcher` + `collect_beat_inputs`（ths 涨停池归因 + 东财板块涨幅精确/最短包含匹配，匹配不到=unknown）+ `watcher_loop`（`in_trading_window` 内每 60s 一拍）。提醒分发：`append_alert` 去重 → 系统规则 `__picks_watcher__` → `record_trigger` → NotifierRegistry。
- API：`POST /api/picks/morning-brief/generate`、`GET /api/picks/morning-brief/today`、`GET /api/picks/watcher/state`、`POST /api/picks/watcher/beat`（手动单拍与 watcher_loop 走同一代码路径）。
- 配置：`premarket_brief_enabled/hour/minute`、`picks_watcher_enabled/interval_seconds/env_refresh_seconds`（env 缓存默认 10 分钟，全量情绪计算不必每拍重算）。
- 已知边界（第一版诚实降级）：volume_ratio 与板块内跌停数数据源缺 → 恒 unknown（量比压确认强度档位；leader_break 触发器不激活）；沙箱时钟非交易时段无法验证 trading=True 的真实取拍，状态机由 24 条单测锁定，盘中实证待用户重启后端后自然发生。

**依赖与风险**：真实推送通道缺（P1 阻塞，in-app 先顶）；东财个股资金流本机曾测不可达（D 批实测决定）；消息面回测不可行（§8 前向积累）；沙箱内后台进程会被收割（watcher 验证走单调用取证法）。

### 批次 C 实现注记（2026-09-02）

- `picks/review_intraday.py`：15:35 方向级对照（`run_review`）+ 提醒收益回算（`backfill_alert_returns`）+ 胜率统计（`intraday_stats`）+ 调度器。复盘结果写进**当日简报文件** `payload["review"]`、收益写回 `alerts`——续用批次 B 的文件持久化（predict `apply_verify` 按 target_date 撞行、`hit_stats` 会把无 verdict 语义的简报方向混进预判统计，故不进 prediction_themes）。
- **四分类终态口径**：优先级 证伪 > 发酵 > 半发酵 > 无波动；确认后证伪按证伪归档（`confirmed` 标志保留，不丢信息）。`closing_confirmed` 用收盘宽口径：量比恒 unknown 不阻塞、环境项全方向共享**不构成方向证据**（只认 `key != "environment"` 的方向级可判定项——否则零题材证据的方向会被环境项误判为发酵，end_to_end 测试抓出后修正）。
- **误判四分类**（确定性映射，顺序即优先级）：环境突变（证伪触发器含 environment）> 阈值过敏（confirmed 且 falsified）> 数据缺失误导（未确认未证伪且 missing_ratio ≥ 0.3）> 逻辑失效（未确认未证伪且收盘涨幅 < 晚期确认线）。输出进对照表，作批次 D 调参输入。
- **提醒收益回算**：参考价 = 提醒日（D0）收盘价（第一版未存提醒时刻现价，不臆造分时价）；腾讯日 K `ts.date()` 即交易日；T 日无 K 线（停牌）该档留空不冒充；t3 complete 后永不重算，未 complete 每日续算。胜率/盈亏比由 `intraday_stats` 按 verdict 聚合，样本外提示（≥20 条前仅供参考）。
- **调度持久化幂等**：`should_run_review` 的 already_reviewed 读简报文件里 `review.trigger == "schedule"`（非内存变量——重启清内存 + 时间已过 = 重跑，review_scheduler 同款教训）；manual 触发不拦 schedule；09:25 前拒绝执行（盘前空池自指防护，测试须 monkeypatch `beijing_now` 钉时钟）。配置：`picks_review_enabled/hour/minute`（默认 15:35，`.env.example` 已注释）。
- API：`GET /api/picks/intraday-review?limit=30`、`POST /api/picks/intraday-review/run`（no_brief → 404，其余失败 → 409）。
- 前端：`apps/web/app/intraday/page.tsx`「盘中跟踪」页（导航新增）——EnvStrip / 方向卡（盘前模式徽章 + review 徽章）/ watcher 状态 / 提醒八段式 + T+1/T+3 收益 / 对照表 / 四卡统计 + 逐日对照 + 胜率堆叠柱。**predict 页简报卡不做**：前端无 predict 页（grep 确认无预判 UI），简报卡已落新页，重复建卡违背导航收敛。
- **范围外发现（P1，待用户决策，本批次不动批次 A/B 代码）**：
  1. **confirm 量比 gate 疑似死代码**：production 中 volume_ratio 恒 unknown → `confirm_signal` 严格门槛（`unmet==0 and met==len(checks)`）使盘中确认**永不触发**，批次 B 文案「会压低确认强度」与实际行为不符。选项 a：批次 D 落板块成交额/量比数据源；选项 b：放宽 gate 允许 unknown 量比按 0.75 强度档确认。
  2. **premarket_scheduler 内存幂等**：`last_run` 是进程内变量，交易日 12:00 前重启后端会**无条件覆盖当日已有简报**（实测 8011 验证时把 09:15 已有简报覆盖为空 directions/空 alerts）。建议对齐 review 的持久化幂等：due 分支先查磁盘当日简报是否已由 schedule 生成。
