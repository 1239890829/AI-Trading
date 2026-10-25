# 盘后复盘 Agent · 架构与机制说明

> **速览（30 秒）**：本文说明**后端自动复盘能算出什么**（模块位置 `backend/app/review/`），
> 与 `daily-review-sop.md`（人工/LLM 复盘的「该怎么做」）互补。触发：每交易日收盘后自动运行，也可
> `POST /api/review/run` 补跑。产物 = 可复现的事实与统计 + 自动改进项；**策略衰减监测**走
> `picks/signal_health.py`（滚动胜率 + CUSUM 下漂，见 `kb/04-decisions.md` KB-DEC-019 决策三）。
> **导航**：模块结构 → 数据来源（现成落库资产）→ 维度与产出 → 处置闭环（改进项状态机）→ 失败降级。
>
> 模块位置：`backend/app/review/`
> 触发：每个交易日收盘后自动运行（也可手动 `POST /api/review/run` 补跑）
> 目标：对**当日操作**与**当日市场**做全方位、可追溯、可迭代的复盘

---

## 1. 整体架构

分层、单向、无隐藏分支。任何一步失败都必须**显式体现在报告里**（`DataGap` / `status=blocked`），而不是让整个复盘静默失败——"今天没跑出报告"比"跑出了缺数据的报告"更难排查。

```text
                ┌──────────────── 调度层 ────────────────┐
                │  review_scheduler（FastAPI lifespan 协程）│
                │  交易日 15:30(CST) 自动触发；不补历史      │
                └───────────────────┬───────────────────┘
                                     │ 触发 ReviewService.run(trade_date)
                ┌──────────────── 编排层 ────────────────┐
  ① 交易日解析 ──▶ ② 数据自采 ──▶ ③ 分析（ModelRouter）──▶ ④ 改进项合成 ──▶ ⑤ 元结论 ──▶ ⑥ 落库+落盘
   trade_calendar     collector      analyzers            synthesis       methodology     storage
                └────────────────────────────────────────┘
                                     │
                ┌──────────────── API 层 ────────────────┐
                │  /api/review/run | /reports           │
                │  /action-items | /effectiveness        │
                └────────────────────────────────────────┘
```

- **数据源（已有，复用，不重新发明）**：行情/宽度/情绪走 `QuoteHub` + `snapshot_service` + `market_context`；操作/持仓/账户走 `paper` 引擎的 SQLite 模型；题材梯队走 `theme_service`。
- **一处口径，两处消费**：情绪判定抽成 `services/market_context.py`，API 与复盘 Agent 共用同一实现，避免口径漂移。

---

## 2. 任务编排（ReviewService.run）

刻意保持**线性且可复现**：

| 步 | 动作 | 失败处理 |
|---|---|---|
| ① 交易日解析 | `trade_calendar.last_trade_date()` 取上一交易日；绝不 `date.today()` 推算（2026-08-29 自指事故） | 日历不可用 → 抛错，不让复盘用错日期 |
| ② 数据自采 | 市场（异步 IO）+ 交易（DB 同步，`asyncio.to_thread`）**并发**采集 | 单项失败 → 记 `DataGap`，其余继续 |
| ③ 分析 | `ModelRouter.analyze(data, method)` → 三维度结论 | LLM 抛错 → 降级规则引擎，重试一次 |
| ④ 改进项合成 | `build_action_items()`：阻断级缺失→P0；纪律/滑点/断层→P1/P2 | — |
| ⑤ 元结论 | `build_meta_insights()`：记录"哪种复盘方式有效/无效" | — |
| ⑥ 落库+落盘 | `save_report()`：SQLite 三表 + `data/review/reports/YYYYMMDD/<sha256>.json` 不可变版本，数据库指针决定当前版本 | 文件失败不提交；库提交失败保留原当前版本，孤立文件不当作报告 |

三维度（`DimensionResult`）：
- **trades 当日操作评估**：规则遵守（被拒单）、过度交易、执行滑点、有成本依据的盈亏归因与亏损单统计；缺成本批次则未知。
- **market 市场环境研判**：指数、情绪阶段（分歧/退潮/高潮…）、宽度、题材梯队断层。
- **system 系统表现诊断**：数据完整度、数据源健康、信号样本量、参数失效征兆。

---

## 3. 定时调度方式

- **实现**：常驻协程，**不依赖外部 cron**（部署环境暂无持久调度器）。自 2026-09-15（`IMP-027`）
  起，全部常驻循环的声明集中在 `app/bootstrap/schedulers.py::register_schedulers()`，
  由 `SchedulerRegistry` 统一启动/收割（不再是散落的 `asyncio.create_task`）。
- **触发条件**（全部满足才跑，且当天只跑一次）：
  1. 今天在 `trade_calendar` 里是**交易日**（走权威日历，不靠 weekday 猜）；
  2. 当前北京时间已过 `review_run_hour:review_run_minute`（默认 15:30）；
  3. 数据库不存在当日报告（`storage.report_exists()` 持久去重，不靠内存`last_run`）。
- **不补跑历史**：非交易日/时间未到就跳过。补跑某一天请用 `POST /api/review/run`，JSON body指定`{"trade_date":"YYYYMMDD"}`，否则分不清"这份报告是哪天生成的"。
- **关闭**：`lifespan` 退出时由 `reg.shutdown()` 按**同一份声明**统一收割（能自己退的走 stop 事件，其余 cancel），不再有第二份手写停机清单。
- **开关**：`ASHARE_REVIEW_SCHEDULER_ENABLED=false` 可完全关闭自动调度，只保留手动触发。

---

## 4. 模型接入与降级策略

**配置化切换**（`backend/.env`，前缀 `ASHARE_`）：

| 配置 | 默认 | 说明 |
|---|---|---|
| `REVIEW_MODEL` | `rules` | `rules`（默认）\| `llm` |
| `REVIEW_LLM_BASE_URL` / `REVIEW_LLM_API_KEY` / `REVIEW_LLM_MODEL` | 空 | 仅 `llm` 模式需要 |
| `REVIEW_METHODOLOGY_VERSION` | `v1` | 默认方法论版本 |
| `REVIEW_RUN_HOUR` / `REVIEW_RUN_MINUTE` | 15 / 30 | 自动触发时间（CST） |

**降级路径**（`ModelRouter`）：
1. `resolve()` 先选配置指定的分析器；
2. 若选 `llm` 但 `LLMAnalyzer.is_available()` 为 False（HTTP配置不全，或所选CLI不可执行）→ **显式降级**到 `RulesAnalyzer`，记录 `degraded=True` + `reason`；
3. 若首选分析器运行抛错 → 降级到规则引擎重试一次；规则引擎再挂才真抛错（不吞）。
4. `ModelUsage` 把 `requested`（想用）与 `actual`（实际）**分开存**，并记 `fallback_chain` / `cost` / `latency_ms` —— 读者永远不会误以为结论来自 LLM 而实际来自规则引擎。
5. **LLM分析器已有实现**：先由规则生成事实底稿，再通过既有OpenAI兼容通道或获准本机CLI增强各维度`judgements`；原`findings/evidence`不由模型重写。输出逐条接地校验，失败或全部拒绝时保留规则原判；校验通过不等于事实独立核验或效果准入。用量另由既有`agent_budget`持久记录，缺失不估算。本轮未变更模型配置、权限或发起新模型调用。

> 默认配置仍是`rules`：不新增模型调用、事实底稿可复现。LLM增强需分开记录模型与输入版本、真实用量和研判；可复现的规则或模型研判均不自动证明策略有效。

---

## 5. 核心数据结构（`review/schemas.py`）

**设计原则：缺失是一等公民。** 任何字段取不到都必须落在 `DataGap` 里（带 `impact` 说明影响哪些结论），绝不用 0 / 空串 / 默认值冒充"没有"。

| 结构 | 作用 |
|---|---|
| `DataGap` | 数据缺失标注：`field/source/reason/impact/severity(warn\|block)` |
| `MarketSnapshot` / `TradingSnapshot` | 自采的市场 / 操作+账户快照（各自带 `gaps`） |
| `ReviewData` | 一次复盘的完整原始数据；`blocked_dimensions()` 计算被阻断维度 |
| `ModelUsage` | 模型实际使用情况（请求 vs 实际 / 降级 / 成本） |
| `DimensionResult` | 单维度结论：`findings`（事实）/ `judgements`（判断）/ `gaps` |
| `ActionItem` | 可落地改进项：`category(parameter\|strategy\|data\|process)` + `priority(P0\|P1\|P2)` + `target` + `proposed_change` + `status` |
| `MetaInsight` | 元结论：关于"复盘方式本身"是否有效 |
| `ReviewReport` | 完整报告（可整体 `model_dump()` 落 JSON / diff / 跨版本对比） |

**持久化三表**（`review/models.py`，结构化列用于检索，payload 存完整 JSON 用于还原）：
- `review_reports`：报告主体，`payload` 存完整 JSON，`report_path` 存落盘路径。
- `review_action_items`：改进项单独建表——它是唯一会被"确认/应用/回退"的东西。
- `review_meta_insights`：元结论，方法论自我迭代的证据链。

---

## 6. 方法论自我迭代机制

三层，刻意**不自动改方法论**（自动改会让"框架演进"变成不可归因的黑箱）：

1. **当场观察**（`build_meta_insights`）：每次复盘记录各维度 `effective / ineffective / unknown`（如"维度 X 因数据缺失被阻断"）。
2. **历史回顾**（`evaluate_historical_effectiveness`）：跨复盘统计各 `category` 改进项的**采纳率 / 回退率**（基于 `status`：confirmed/applied/reverted/rejected/pending）。
3. **演进建议**（`suggest_methodology_changes`）：样本≥5 且采纳率<20% → 提示"该维度疑似产出噪音，提高触发门槛"；回退率>30% → 提示"判据不可靠，复核证据链"。**只建议，不自动改。**

**方法论可版本化**（`review/config.py`）：
- 配置来源优先级：`data/review/methodology/<version>.yaml` > 代码内 `DEFAULT_METHODOLOGY`。
- 新增版本 = 新增一个 yaml（维度开关、`weight`、`thresholds`、P0 白名单），**不改代码**。
- 每次报告都带 `methodology_version` → 能统计"v2 比 v1 产生的改进项采纳率高多少"。

> 自我迭代的闭环：报告带版本 → 改进项被人工确认/应用/回退（更新 `status`）→ 历史统计揭示哪版方法论/哪类维度更有效 → 建议调参 → 新版本 yaml → 下轮对比。

---

## 7. REST 端点（`app/api/routes/review.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/review/run` | 手动触发；body `{trade_date?, methodology_version?}` |
| GET | `/api/review/reports` | 报告列表（结构化摘要） |
| GET | `/api/review/reports/{trade_date}` | 某交易日完整报告 |
| GET | `/api/review/effectiveness?version=` | 采纳率/回退率 + 演进建议 |
| PATCH | `/api/review/action-items/{id}` | 处置单条改进项；body `{status, note, implementation_evidence, trade_date, category, title}`；`applied`需实施及验证证据，`rejected`/`reverted`需理由；守卫三元组不符返回409 |
| GET | `/api/review/action-items?status=&limit=` | 改进项清单（未传status返回全部，按priority/id升序） |

`/review/compare`与`/review/methodology/versions`已于2026-09-08删除，存储/配置函数保留。当前页面按需读取两份已保存报告作有限对照，不恢复无人消费的API。

**改进项处置（PDCA 闭环的落点）**

- 状态取值 `pending | recorded | confirmed | applied | rejected | reverted`；回到 `pending` = 撤销处置，清 `resolved_at`。
- `{id}` 是 `review_action_items.id`（数据库主键），**不是** payload 里的 `AI-xxxxxxxx` 临时编号。
  `GET /api/review/reports/{trade_date}` 会把主键回填到 `action_items[].id`，前端直接拿它寻址。
- **id 漂移守卫（2026-09-01）**：id 是 SQLite rowid 别名且无 AUTOINCREMENT，报告重跑删除重建后
  id 会被复用甚至跨交易日串号（实测 111→72）。PATCH 请求体的 `trade_date/category/title`
  是守卫三元组——与表行现状不符（id 已漂移）返回 **409**，客户端刷新页面重取新 id 再操作；
  绝不静默写到恰好复用该 id 的别的改进项上。
- **`get_report` 会用表行状态覆盖 payload 快照**：payload 是生成时快照，处置只改表行；
  不同步的话界面会表现为"点了确认、回读还是待处置"（2026-09-01 实测）。
- 写接口受现役设备配对、能力与维护权限约束；普通读取不继承维护授权。历史R22的“未配token即全放行”不代表当前共享运行规则，现役鉴权以项目AGENTS及`docs/system/api.md`为准。

---

## 8. 当前能力与验收边界

- 复盘默认配置仍为规则分析器；LLM增强实现及降级边界见§4。代码实现、现场配置、实际调用与效果验收分别取证，不因历史文档索要新凭据。
- 自动调度由现有进程内注册表承接；进程未运行或错过窗口不构成已采集。手动历史生成只读可证明属于该日的材料，当前行情不得填回历史日。
- 操作仅覆盖模拟交易。账户scope、来源日期与原消息身份必须保留；没有历史估值或成本批次时分别标未知，不能借当前账户补齐。
- 题材、板块与竞价能力按现役owner和逐段源时实测；返回请求日期不证明底层材料属于该日。2026-10-25修订的日期绑定规则取代旧“按参数指定日即同日”的假设。
- 同日报告保存与改进项更新沿原持久化流程；本轮不重写旧报告，不新增模型、策略晋级或自动应用改进项。工程完整、运行采集与策略效果分别验收。


## 2026-10-25 市场复盘、新闻与方法读者

用户批准将本地TSP比较中有价值的复盘、新闻和方法发现路径接入原系统；采用产品组织，不复制其模板CSS、seed、伪绩效、假发送/成交或无权限后端。保留当前主题、左右布局、原地详情与一个实际列表滚动owner。工程与未来策略效果分别验收。

复盘市场及交易快照新增`date_bound_v1`：各段独立保留来源、源时刻、接收时刻、来源日、请求日、状态与原因。只有日期匹配且来源/质量可核的`verified/partial`段进入同日事实与分析；接收时刻不能补造源日期。指定历史日不读当前宽度、情绪、竞价或重生成旧归因，未来日期拒绝。旧报告不重写、旧值作为`reported_values`或原报告保留，缺元数据不升级。

原板块owner只有按日Top50资金排序存档、没有逐行源钟，因此可独立阅读和排序“原按日采集参考”，不得叫全市场涨跌榜或已核同日事实。历史账户余额与持仓估值不拿当前快照填入；该日创建委托的当前/保存状态不当作当日新增成交。未绑定旧逐股归因不计可信summary；卖出成交价减委托价不是已实现利润，缺成本批次则利润未知。

原复盘入口新增市场/检查改进双视角：市场事实及来源、有限板块强弱、同版原入选与观察、明确绑定的站内留痕/渠道接受/模拟状态、原简报触发与证伪条件、历史报告对照。`selection_journey`只按原消息origin、selection_ref、source_version绑定，禁止按同股猜订单；accepted不证明送达或已读。`outlook`只读该日已保存简报，不新增模型预测、收费调用或回填原判断。

新闻快讯与事件分析共用原EventStore：快讯模式只读最新有限原记录，含失效历史；不触发盘面排名/题材记忆取数，不增加通知未读。支持重要、关联已存名单、并购及来源文字筛选，逐条按需读取原始观察与解释版本。名单日期/版本独立，原引用与同股关联分开，修订不倒填首次依据。方法目录继续读原登记册；最近运行只读原DailyPickSet/OpportunityDecisionRun/Snapshot，扫描行、入选股、观察表现及实际成交不混分母。耗时未记录保持未知，不导入候选35条定义或伪胜率。

免费源仅准入已核的美联储货币政策官方RSS，用原快讯loop每15分钟有限读取近30日/最多30项，原ID/hash/三时钟进入原观察库。它只服务宏观事实阅读，零股票方向，排除自动LLM，不改变评分/机会资格或外部投递。关闭开关、源失败/时效与feed窗口可见；未声明完整历史。金十需要Token，免费额度/留存展示许可未证，不接；其他候选按实际失败或无消费者价值排除，不重复试探求成功。来源核验归[数据源说明](../data/data-sources.md#2026-10-25-官方宏观rss与只读新闻补源)。

旧报告的空精选条目只说明该报告没有可展示条目，不证明当日零入选。新闻事实/阶段展示为原分类标记，不能当作独立核验；非活跃可能来自修订、处置、证据或时效，不一律写成过期。方法拒绝理由只消费原`selection_reason/score_shortfall`，技术分不是理由；盘中有限投影优先原rank行，再读硬门行，只有rank计数与原运行一致且依据可核时展示原入选确数，其他阶段截断仍保留。

本批完整本地门禁、准确HEAD发布、实际页面读取/交互及运行限制归handoff和PR回执；设计覆盖、测试通过与收益增益不得互代。
