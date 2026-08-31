# 整体架构与联动方案（2026-09-01）

> 出发点（用户三原则）：① 克制新增——除非充分论证，否则不新增独立页面/板块；② 以联动为核心——系统能力取决于板块间数据流转而非页面数量；③ 以选股器为典型场景做端到端设计。
> 本方案**零新增页面、零新增一级导航**；所有改造落在既有模块的接口、数据层与联动上。
> 姊妹文档：docs/system-review-2026-09-01.md（功能/性能评审，问题条目用 D*/M*/A*/O* 编号引用）。

---

## 〇、结论先行

1. **系统缺的不是模块，是"总线"。** 六维选股的全部因子数据都已存在且链路打通（消息面 d8e4e52、基本面 ede98be 之后），但模块之间靠三种脆弱方式衔接：REST 轮询巧合同步、window CustomEvent 裸字符串、URL 参数传递。联动改造的主线是**把这三者收敛成一条类型化的数据层**，而不是加功能。
2. **三个实锤断点（已核代码，非推测）：**
   - `search-box.tsx:52` 派发 `watchlist-changed`，**全站无任何组件监听**（grep 实证）——加自选后左栏要等 10s 轮询才出现新行；
   - 后端 `/ws/quotes` **已实现** `{"action":"subscribe"}` 动态订阅（`websocket/routes.py:59-64`），前端 `use-quote-stream.ts` 未用——自选集合每次变化整条 WS 重连，行情空窗；
   - 事件→选股是**单向链**：事件采集入库、选股读取、复盘归因，但"哪条事件带来了收益/亏损"从未回写事件权重（E4 在阶段 B 挂了三周）。
3. **选股器已具备端到端骨架**：候选池→深评→六维加权（regime 双权重表）→一票否决→组合稳定约束→空仓闸门→复盘九类归因→角色胜率。本方案不重构它，做三件事：补消融验证（回答"六维组合到底比单维强多少"）、补落选者落库（消融的数据地基）、补事件收益回写（因子自校准闭环）。

---

## 一、现有模块清单与依赖关系

### 1.1 分层依赖图（数据从左向右流）

```
┌─ 采集层 ──────────────────────────────────────────────────────────────┐
│ QuoteHub(5s轮询)  SnapshotService(60s轮询/300s落库Parquet)            │
│ EventCollector(30min)  SpeedSampler(惰性)  TDX日K底座  东财财务/龙虎榜 │
└──────┬───────────────────────┬──────────────────────┬────────────────┘
       ▼                       ▼                      ▼
┌─ 存储/共享状态 ───────────────────────────────────────────────────────┐
│ QuoteHub内存缓存(WS广播) │ SQLite: watchlist/paper/alert/real/        │
│ (snapshot/quotes/stale)  │ daily_pick_set+review/events/real_trade    │
│ ttl_cache(11处统一)      │ Parquet: data/parquet/snapshots/           │
└──────┬───────────────────────────────────────────────┬────────────────┘
       ▼                                               ▼
┌─ 分析层（全部纯函数，可单测）─────────────────────────────────────────┐
│ sentiment(六相位)  echelon(梯队角色)  regime(权重选择)  gate(空仓闸门) │
│ tech_score(防飞刀)  risk_tier/stop_loss  review(九类归因)  predict    │
└──────┬────────────────────────────────────────────────────────────────┘
       ▼
┌─ 组合层 ─────────────────────────────────────────────────────────────┐
│ picks/engine: score_* × 6 → synthesize(加权+一票否决) → 换股约束      │
│ (REPLACE_THRESHOLD=15 + MAX_SWAPS_PER_DAY=2 + carryover) → 买入区间   │
└──────┬────────────────────────────────────────────────────────────────┘
       ▼
┌─ 输出/服务层 ────────────────────────────────────────────────────────┐
│ REST 96端点 + /ws/quotes  │ 风控引擎v1(下单预检,与picks独立)          │
└──────┬────────────────────────────────────────────────────────────────┘
       ▼
┌─ 前端（5导航，零新增）───────────────────────────────────────────────┐
│ 工作台(workbench+StockDetailPanel) │ 盘面/tape │ 市场/market          │
│ 每日精选/picks │ 研究/research(回测|预警[|复盘:A2])                   │
│ 跨组件：URL参数(routing.ts) + CustomEvent×3 + 各页独立fetch+轮询     │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.2 模块输入输出表（联动设计的原始素材）

| 模块 | 输入 | 输出（谁在消费） | 通信方式 |
|---|---|---|---|
| QuoteHub | ths批量快照（自选+指数） | Quote[]；WS `snapshot/quotes/stale` | REST `/api/quotes` + WS 广播 |
| SnapshotService | 新浪全市场56页 | 全市场快照（宽度/云图/候选池/回测地基）；Parquet 落库 | REST（heatmap/breadth/financials 间接） |
| EventCollector | 东财新闻/公告（自选∪昨日精选∪持仓范围） | EventCard + 方向行（symbol/theme, ±1/0） | SQLite；30min 调度 |
| 情绪引擎 | 涨停池/炸板池/快照 | phase 六相位+温度+晋级率 | REST `/api/market/sentiment`；picks 生成时进程内调用 |
| 题材梯队 | 涨停池+官方成分目录 | ThemeBoard（唯一归属/分级/健康度/合力） | REST `/api/themes`（60s 后端缓存） |
| 选股引擎 | 上述全部 | DailyPickSet（≤5只+meta+replaced） | REST `POST /api/picks/generate`；SQLite |
| 复盘 Agent | 快照+组合+事件 | DailyPickReview（九类归因）+ 元结论 | SQLite；15:30 调度 |
| 回测/回放 | TDX 日K + Parquet | 净值/指标/四策略对照 | REST + 脚本 |
| 前端各页 | — | — | **各自 fetch + setInterval 轮询（O2 清单）**；跨组件靠 3 种 CustomEvent 裸字符串 |

---

## 二、问题与冗余（联动视角；功能冗余见评审报告 D1/D2/M1-M3，此处不重复）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| P1 | `watchlist-changed` 派发后**无人监听** | grep 实证（仅 search-box 一处 dispatch） | 加自选→左栏 10s 才刷新，用户感知"没加上" |
| P2 | WS 动态订阅后端已备、前端未用 | routes.py:59 vs use-quote-stream.ts:18(key 重连) | 自选每次增删→WS 重连→行情空窗→降级风险 |
| P3 | 同一数据多副本多轮询：行情在 workbench(marketOverview+quotes) 与 market(5请求/10s) 各拉一份；涨停池被 market速览/tape涨停生态/picks候选池 三处消费 | O2 轮询清单 | 请求量翻倍、数据一致性靠巧合 |
| P4 | CustomEvent 三种名字裸字符串、无 payload 类型 | paper-changed/real-changed/watchlist-changed | 新联动无契约可依，改名即断 |
| P5 | 事件链单向：无"事件→收益"回写 | 阶段 B 表 E4 挂起 | 消息因子无法自校准，event_expired 归因永远停在分类 |
| P6 | 落选者不落库（DailyPickSet.items 仅 ≤5 只） | models/daily_pick.py:25 | 消融验证（六维 vs 单维）无历史数据地基 |
| P7 | 题材方向事件不计入单股消息分（防过度外推） | picks.py:300-302 | 题材利好→成分股的传导只能靠 echelon 维间接体现，时效损失 |
| P8 | gate/phase/regime 是"生成时快照"，盘中前端只能靠 picks 页 meta 看到，其他页面无感 | market/workbench 无 gate 显示 | 空仓闸门提示只在精选页，用户在盘面操作时看不到 |

---

## 三、统一数据层与事件/消息机制（零新增页面的实现载体）

### 3.1 后端：一个聚合端点 + 一条扩展的 WS（复用现有 QuoteHub）

**① `GET /api/system/context`（新端点，非新模块）**
聚合当前"市场上下文"单对象：

```json
{ "trade_date": "2026-09-01", "phase": "分歧", "temperature": 52,
  "gate": {"stand_aside": false, "level": null, "reasons": []},
  "regime": "业绩空窗期", "weights_version": "...", "snapshot_ts": "...",
  "combo": {"date": "...", "count": 5, "stand_aside_count": 0} }
```

- 消费方：工作台头部（现 risk 徽章旁边加 gate 状态）、市场页情绪面板、精选页横幅、盘面页页头——**同一份状态，五处共用，替代各页单独拉 sentiment/picksMeta**。
- 缓存 30s（ttl_cache 现成机制），成本一次内存聚合。

**② WS 消息类型扩展（同一条 /ws/quotes 连接）**
QuoteHub 现有 `_broadcast("stale")` 模式推广：情绪重算/闸门翻转/regime 切换时，向所有连接追加 `{"type":"context","data":{...}}`（与 context 端点同构）。前端收到即更新 store——**P8 就地解决**：盘中闸门从"翻页才能看到"变成"翻脸就推送"。
- 触发点：`compute_market_sentiment` 的 30min 级重算 + gate 条件翻转检测（退潮/跌停家数阈值），不必高频。

**③ 事件收益回写（P5，E4 落地）**
复盘 Agent 产出 `DailyPickReview.reason_category=event_expired` 时，反查该股命中过的活跃事件 → `event_outcome` 字段（+1 印证 / −1 证伪）→ 消息评分的强度系数按近期命中率微调（bounds ±20%，可解释、可回滚）。**不引入 ML，就是一张表 + 一个系数**。

### 3.2 前端：`lib/app-context.ts` 轻量共享层（useSyncExternalStore，零依赖）

不引 Redux/Zustand（克制原则：React 18+ 内置机制足够）。单例 store 承载四类共享状态：

| 状态 | 内容 | 替代现状 |
|---|---|---|
| `quotes` | WS 推送的唯一副本 | workbench `useQuoteStream` 与 market 索引卡各持一份 |
| `context` | phase/gate/regime/trade_date | market 10s 轮询里的 3 个低频请求 + picksMeta |
| `watchlist` | 符号集+分组 | workbench loadBase 里的 getWatchlist |
| `combo` | 当日组合摘要（5 符号+stand_aside） | picks 页外其他页看不到组合 |

- 组件用 `useAppContext(selector)` 订阅（selector 粒度控制重渲染范围，顺带解决 O3 的全量重渲染）。
- **迁移策略（渐进，不做大爆炸）**：Phase 2 先迁 workbench+market 两处；picks/detail 保持现状（它们的数据时效要求不同）；Phase 4 视收益决定是否全量。
- `lib/events.ts`：三种 CustomEvent 收敛为类型化常量 + `emit()/on()` 帮助函数，payload 带类型（P4）。watchlist-changed 补上监听方（P1 一行修复）。

### 3.3 联动矩阵（现状 → 目标；全部复用既有模块）

| 联动对 | 现状 | 目标 | 载体 |
|---|---|---|---|
| 自选 → 行情订阅 | 变化即重连（P2） | WS subscribe 消息更新 | 前端一行改动 |
| 自选写入 → 左栏刷新 | 10s 轮询巧合（P1） | watchlist-changed 真正生效 | events.ts + workbench 监听 |
| 行情/闸门 → 所有页面 | 各页各自拉（P3/P8） | context store + WS 推送 | /api/system/context + WS type:context |
| 题材 chip → 盘面聚焦 | ✅ 已通（/tape?tab=themes&focus=） | 不动 | URL 参数 |
| 涨停池 ↔ 题材证据下钻 | ✅ 已通（成员高亮/返回） | 不动 | URL 参数 |
| 事件 → 选股消息分 | ✅ 已通（symbol 方向） | 增强：题材方向受控传导（P7，Phase 4 待消融验证后开） | echelon 维加成 |
| 选股 → 持仓 | 组合在精选页，持仓在详情 tab | context.combo 进工作台：自选列表"持仓/组合"两个视角并列 | store.combo |
| 复盘 → 选股 | 九类归因+角色胜率 ✅ | 事件收益回写（3.1③）+ 消融报告 | event_outcome 表 |
| 回测 → 选股 | mandate 解耦 ✅ | 消融回放（4.5）| replay 脚本扩展 |

---

## 四、选股器端到端设计（典型场景）

### 4.1 六维因子表（来源 / 计算位置 / 时序 / regime 权重 / 降级口径）

| 维度 | 数据来源（全部既有管线） | 计算位置 | 更新时序 | 业绩驱动期 | 业绩空窗期 | 数据缺失时 |
|---|---|---|---|---|---|---|
| sentiment | 涨停池+炸板池+全市场快照 → 情绪引擎六相位；题材合力涨家比 | `score_sentiment` | **生成时一次**（全局，非逐股） | 0.10 | **0.25** | 缺失=中性 50，不打折 |
| news | EventStore 活跃事件 → `extract_symbol_direction`（symbol 方向 ±1/0） | `score_news` | 采集 30min；生成时读取 | 0.22 | 0.20 | 无命中=中性 50；方向待判也算命中（诚实显示） |
| tech | ths→tencent 日K（熔断兜底）→ `score_stock` 防飞刀六卡 | `score_tech` | 生成时拉 250 根 | 0.18 | 0.18 | <60 根=中性 |
| fundamental | 快照 PE（ths 缺字段→`fill_valuation` 腾讯补）+ 东财财务（营收/净利同比+ROE+毛利率） | `score_fundamental` | 生成时 | **0.25** | 0.05 | 分项缺失=该项不计分（basis 注明） |
| capital | 新浪资金流 `net_main` | `score_capital` | 生成时 | 0.10 | 0.12 | 缺失=中性 |
| echelon | 涨停池联动唯一归属 → `classify_echelon_role` × `theme_ladder_health`（题材阶段系数） | `score_echelon` | 生成时 | 0.15 | **0.20** | 非涨停/无题材=角色分中性档 |

权重表来源：`app/picks/regime.py` `WEIGHTS_BY_REGIME`（regime 由财报日历窗口 × 业绩事件密度 ≥30%/≤12% 双重判定；六项和恒为 1）。权重版本化随 DailyPickSet.meta 落库——**历史复盘永远按当日口径还原**。

### 4.2 端到端时序（T 日收盘后 `POST /generate`，产出 T+1 组合）

```
T 日 15:05 后
① 候选池: 事件命中股 ∪ 涨停池 ∪ ths热股榜TopN (cap40, _prio=来源优先级×涨幅)
   ①a carryover: 昨日组合成员无条件纳入重评（防"没进榜就消失"——60日回放实测纯涨停池日均换手60%的教训）
② 批量快照预筛: 剔ST/退市/无价；carryover 优先进入深评（DEEP_DIVE_CAP=24）
③ 全局一次: market_phase（情绪引擎）→ regime（日历×事件密度）→ weights_for(regime)
③c gate: 炸板率+跌停家数+... 五条件 OR → stand_aside（仅提示，不改分——红线3）
④ 逐股并发深评(CONCURRENCY semaphore): 六维 sub 分 + basis（每维独立 try/catch 降级）
⑤ synthesize: 加权和 + 一票否决 → 综合分
⑥ 组合约束(顺序即优先级):
   gate(stand_aside→横幅+买入范围收紧) > 硬性失效剔除 > 换股门槛(新候选≥现任+15分)
   > MAX_SWAPS_PER_DAY=2 > MAX_PICKS=5
⑦ 输出: 买入区间(±3% ∩ 技术位收敛) + 风险档位 + 止损(max(档位值,1.5×ATR%) clamp 3~12%)
   + 失效条件(事件/题材/均线客观参照) → 落库 items+meta+replaced
T+1 盘中: gate 实时状态(WS context) + 盘中硬性失效(炸板/跌停/黑天鹅)提前移除
T+1 收盘: 复盘 Agent → 九类归因 + 角色胜率 + 事件收益回写(本方案新增)
```

### 4.3 优先级仲裁（为什么是这个顺序）

1. **gate 最高**：空仓闸门否决的是"今天该不该玩"，比"玩哪只"高一个层级——退潮期六维再高的分也是接飞刀。
2. **组合稳定次之**：分差门槛在涨停股主导的候选池下无效（梯队分差动辄 30+），所以数量上限 MAX_SWAPS_PER_DAY=2 才是真稳定器——60 日参数扫描实证（换手上限 2 只=39.7% vs 不限 68.8%）。
3. **六维综合分**只在以上约束内排序。
4. **新鲜度（_prio）**仅决定"谁进得了深评"，不影响最终排序——深评名额有限时的公平规则。

### 4.4 与五类模块的打通现状与缺口

| 模块 | 已打通 | 缺口 → 本方案动作 |
|---|---|---|
| 涨停梯队 | echelon 全维度接入；题材卡片→涨停池证据下钻 | 组合卡片→"梯队来源"跳转盘面（链接，Phase 1 顺手） |
| 时事新闻 | 30min 采集→symbol 方向→消息分 | 事件收益回写（3.1③）；题材传导（P7，Phase 4 消融后开） |
| 自选股 | 采集范围含自选前 20 | 自选∩组合差集提示："自选里这只为什么没进组合"（可解释性，Phase 3） |
| 行情盘面 | 快照预筛/买入区间/盘中失效 | gate 盘中推送（3.1②）；组合符号进行情订阅（carryover 标的 WS 常订） |
| 历史回测 | 四策略对照回放 | **消融回放**（4.5）——回答"组合到底比单维强多少" |

### 4.5 组合优势论证与消融验证设计（本方案补充的关键环节）

**机制论证（为什么六维组合优于任一单维）：**
- 单情绪：退潮期全对，但选不出"选谁"——梯度问题交给梯队维；
- 单技术：防飞刀但会系统性错过发酵初期的首板（技术指标滞后于情绪拐点）——催化时效交给消息维；
- 单消息：追高接盘典型（利好落地即出货，REASON_CATEGORIES 第一条就是 event_expired）——入场结构交给技术维；
- 单基本面：业绩空窗期完全失灵（regime 存在的理由）；
- 单梯队：只会追龙头，首板新题材看不见（候选池的热股榜/事件来源补位）。
- 组合的本质是**让每个维度的典型失败模式被其他维度覆盖**，且 regime 切换权重表保证"不同市况下由对的维度主导"。

**诚实边界**：以上是机制推演；60 日回放只覆盖梯队+技术两维，**完整六维的历史优势未被验证**——这是全系统最大的未知，也是消融验证要回答的问题。

**消融设计（不新增页面，扩展 replay 脚本）：**
1. 前置：generate 落库候选池全量 sub 分（P6 修复，一张 `daily_pick_candidate` 表或 items 扩容，alembic 一份）；
2. 策略组：{仅 tech} / {仅 echelon} / {tech+echelon=现行回放口径} / {六维(用落库 sub 分按当日 meta 权重重算)} / {现行完整约束}；
3. 指标：T+1 / T+3 / T+5 相对上证超额中位数、胜率、最大回撤、换手率；
4. 验证标准：30 交易日样本，六维组在 T+3 超额中位数与胜率**至少不劣于**任一单维组，且回撤小于单消息组——达标则组合口径转正；不达标则按维度逐个查因（归因九类正好是排查工具）。

---

## 五、引擎与因子体系（分层契约）

```
数据层(采集/清洗/质量五级/熔断)
  → 因子层: score_sentiment/news/tech/fundamental/capital/echelon
     契约: 纯函数 (输入 dict|None) → (0-100分, basis字符串)；缺失=中性不臆测
  → 规则层: 一票否决(vetoes) / 换股门槛 / 数量上限 / gate 五条件 / regime 权重表
  → 组合层: synthesize → apply_replacement_threshold → build_buy_range
  → 风控层: risk_tier / stop_loss_reference / exit_discipline / build_invalidations
     （与风控引擎 v1 的下单预检独立成双闸：选股侧"该不该买"，交易侧"能不能买"）
  → 输出层: DailyPickSet(items+meta+replaced) → 前端瀑布流卡片
  → 校验环: 复盘九类归因 → 角色胜率 → 事件收益回写 → (人工)权重微调建议
```

**因子注册表**（落地形式：`app/picks/manifest.py` 常量 + 本文档，不改运行时结构）——每因子登记：名称、输入端点/表、版本（SCORER_VERSION 类）、所属权重键、降级口径、复盘归因类别映射。价值：新因子接入前先过这张表回答"输入谁提供、缺失怎么降级、归因落在哪类"，防再次出现 net_main 式"数据在、取数没接"。

**盘面对比与复盘校验**：
- 生成时 `_prio`（来源×涨幅）与深评 24 只名单随 meta 落库 → 复盘可对比"入选 5 只 vs 落选 19 只"的 T+1 表现——这是比消融更便宜的持续验证（入选组超额为正是系统有效的最低要求）；
- 角色胜率按日累积（2b719f3 已落）；事件回写见 3.1③。

---

## 六、分阶段实施路径（每步含验证标准；全程 pytest 全绿 + tsc/eslint 门禁 + 浏览器截图验收 + 提交前还原 trade_calendar.json）

### Phase 1 · 联动止血（1 次迭代，纯前端 + 0 后端）✅ 已完成 2026-09-01（db98366）
- **内容**：P1 watchlist-changed 补监听；P2 前端接入 WS `subscribe` 消息（后端已就绪）；P4 `lib/events.ts` 类型化事件；picks 卡"梯队来源"跳转链接。
- **验证**：① vitest：加自选 → `watchlist-changed` 断言左栏刷新被调用；② 浏览器 devtools WS 面板：加/删自选连接不重连（Network→WS 只有一条）；③ 截图：加自选后左栏 ≤1s 出现新行。

### Phase 2 · 数据聚合（1 次迭代，后端 1 端点 + 前端 store）
- **内容**：`GET /api/system/context`（ttl_cache 30s）；WS `type:"context"` 推送；`lib/app-context.ts` store；workbench+market 接入（gate 徽章进工作台头部，market 低频请求降频——O2 一并做）。
- **验证**：① pytest：context 端点字段契约（trade_date/phase/gate/regime 四键必有）；② Network 计数：market 页常态请求 5/10s → 指数 10s + context 推送（实测对比截图）；③ 工作台头部 gate 徽章在人工构造 stand_aside 数据时显示（测试 fixture）。

### Phase 3 · 消融地基与复盘消费（1-2 迭代）
- **内容**：P6 候选池全量分落库（alembic）；replay 消融策略组；"落选 vs 入选"对比进复盘；自选∩组合差集提示；事件收益回写表（3.1③，bounds ±20%）。
- **验证**：① pytest：落库行包含候选池 24 只 sub 分；② 回放脚本产出五策略对照表（T+3 超额中位数/胜率/回撤），六维组不劣于单维组；③ 复盘页（A2 研究页 tab）能看到对比结论。

### Phase 4 · 因子增强（等前置数据，不阻塞前三期）
- **内容**：P7 题材方向受控传导（题材方向 × 成分归属 → echelon 小权重加成，先影子计算两个交易日对比后再启用）；LLM 逐维增强（等凭据）；因子收益回写驱动权重微调建议（人工确认后改表）。
- **验证**：影子期对比报告（开启/不开启的 T+3 超额差异）；LLM 增强后 basis 保留规则版原分（可对比可回滚）。

### 明确不做（防复发，与 AGENTS.md §4 对齐）
不新增页面/导航；不引状态管理库；不做盘中全量因子重算（生成时点计算+盘中仅 gate/失效）；LLM 不进关键路径（规则版永远可独立出分）；不改 synthesize 接口签名（历史组合回溯依赖它）。

---

## 七、必要性与不可替代性声明（对应原则 ①，逐项自查）

| 改动 | 必要性 | 不可替代性 | 对现有结构的影响 |
|---|---|---|---|
| /api/system/context 端点 | P3/P8：五页面共用状态现在靠 5×轮询各拉 | 轮询无法做到"闸门翻转即知"；无此端点则每页自拼 gate+phase+regime 三请求 | 纯增量端点，旧端点不动 |
| WS context 消息类型 | gate 盘中可见性 | CustomEvent 只能浏览器内、REST 只能拉——推送必须走既有 WS | QuoteHub._broadcast 泛化，模式已有（stale） |
| lib/app-context.ts | P3：行情双副本、O3 全量重渲染 | useSyncExternalStore 是 React 官方外置 store 契约，零新依赖 | 渐进接入，旧页面不强制迁移 |
| lib/events.ts | P1/P4：断链与裸字符串 | 无——一行修复 + 类型契约 | 纯收敛，行为不变 |
| daily_pick_candidate 落库 | 消融验证无历史数据则永远"机制推演" | 落选者分只此一次，不存即失 | 新表，items 结构不动 |
| 事件收益回写 | P5：因子自校准闭环缺失 | 归因九类已产出信号，只差落表 | 新表 + score_news 系数读取，bounds 可回滚 |
