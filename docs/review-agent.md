# 盘后复盘 Agent · 架构与机制说明

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
                │  /api/review/run | /reports | /compare  │
                │  /methodology/versions | /effectiveness │
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
| ⑥ 落库+落盘 | `save_report()`：SQLite 三表 + `data/review/reports/YYYYMMDD.json` | 落盘失败不写库 |

三维度（`DimensionResult`）：
- **trades 当日操作评估**：规则遵守（被拒单）、过度交易、执行滑点、盈亏归因、亏损单占比。
- **market 市场环境研判**：指数、情绪阶段（分歧/退潮/高潮…）、宽度、题材梯队断层。
- **system 系统表现诊断**：数据完整度、数据源健康、信号样本量、参数失效征兆。

---

## 3. 定时调度方式

- **实现**：FastAPI `lifespan` 里 `asyncio.create_task(review_scheduler(...))`，进程内协程，**不依赖外部 cron**（部署环境暂无持久调度器）。
- **触发条件**（全部满足才跑，且当天只跑一次）：
  1. 今天在 `trade_calendar` 里是**交易日**（走权威日历，不靠 weekday 猜）；
  2. 当前北京时间已过 `review_run_hour:review_run_minute`（默认 15:30）；
  3. 今天还没跑过（`last_run` 去重）。
- **不补跑历史**：非交易日/时间未到就跳过。补跑某一天请用 `POST /api/review/run?trade_date=YYYYMMDD`，否则分不清"这份报告是哪天生成的"。
- **关闭**：`lifespan` 退出时 `stop.set()` 并 `await` 任务取消。
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
2. 若选 `llm` 但 `LLMAnalyzer.is_available()` 为 False（未配 base_url/api_key）→ **显式降级**到 `RulesAnalyzer`，记录 `degraded=True` + `reason`；
3. 若首选分析器运行抛错 → 降级到规则引擎重试一次；规则引擎再挂才真抛错（不吞）。
4. `ModelUsage` 把 `requested`（想用）与 `actual`（实际）**分开存**，并记 `fallback_chain` / `cost` / `latency_ms` —— 读者永远不会误以为结论来自 LLM 而实际来自规则引擎。
5. **LLM 分析器目前是占位壳**：`analyze()` 未实现（抛 `NotImplementedError`），等确定接口形态（OpenAI 兼容 / 各家国产）再补，不猜测字段产出不可用代码。

> 当前默认 `rules` 的原因：规则分析器**零成本、零外部依赖、结果可复现**——可复现是"方法论自我迭代"的硬前提（带随机性的结论无法判断哪个版本更有效）。

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
| GET | `/api/review/compare?from=&to=` | 两日对比（缺口修复/情绪迁移/改进项处置） |
| GET | `/api/review/methodology/versions` | 可用方法论版本 |
| GET | `/api/review/effectiveness?version=` | 采纳率/回退率 + 演进建议 |
| PATCH | `/api/review/action-items/{id}` | 处置单条改进项；body `{status, note, trade_date, category, title}`；`rejected`/`reverted` 必填 note；守卫三元组不符返回 409 |
| GET | `/api/review/action-items?status=&limit=` | 改进项清单（默认 pending，按 priority/id 升序） |

**改进项处置（PDCA 闭环的落点）**

- 状态取值 `pending | confirmed | applied | rejected | reverted`；回到 `pending` = 撤销处置，清 `resolved_at`。
- `{id}` 是 `review_action_items.id`（数据库主键），**不是** payload 里的 `AI-xxxxxxxx` 临时编号。
  `GET /api/review/reports/{trade_date}` 会把主键回填到 `action_items[].id`，前端直接拿它寻址。
- **id 漂移守卫（2026-09-01）**：id 是 SQLite rowid 别名且无 AUTOINCREMENT，报告重跑删除重建后
  id 会被复用甚至跨交易日串号（实测 111→72）。PATCH 请求体的 `trade_date/category/title`
  是守卫三元组——与表行现状不符（id 已漂移）返回 **409**，客户端刷新页面重取新 id 再操作；
  绝不静默写到恰好复用该 id 的别的改进项上。
- **`get_report` 会用表行状态覆盖 payload 快照**：payload 是生成时快照，处置只改表行；
  不同步的话界面会表现为"点了确认、回读还是待处置"（2026-09-01 实测）。
- 写接口走 `require_write_token`（未配 `ASHARE_API_TOKEN` 时全放行，本地 dev 零影响）。

---

## 8. 需要你确认的假设（未验证项）

1. **默认用规则引擎、LLM 留占位**：本机/项目内无可用 LLM API Key 与可调接口（Hy4 preview 是会话模型，非可调用 API）。是否要我现在接入某个具体 LLM 端点（需你提供 base_url / key / 模型名）？
2. **调度用进程内 asyncio 协程而非外部 cron**：当前部署（docker-compose）没有持久调度器。若以后要"进程重启也不漏跑"，需改外部 cron / k8s CronJob，或加"启动时补跑昨天"。
3. **复盘只覆盖模拟账户（paper）操作**：符合项目红线（禁止真实券商 / 真实下单）。真实账户对接不在范围内。
4. **题材梯队 / 龙虎榜依赖 ths 官方端点**：若 ths key 失效，这些维度会标 `gap` 降级而非臆测；是否需要加腾讯/东财备源补齐？
5. **同交易日重复生成 = 覆盖**（不追加）：认为"对某一天的判断应唯一"。若你要保留历次版本（如 v1/v2 都跑同一天），需改为按 `(trade_date, methodology_version)` 唯一。
6. **情绪/宽度口径完全复用现有 `market_context` / `snapshot_service`**：不另起炉灶，保证与前端展示口径一致。
7. **单账户**：复盘结论基于单个 paper 账户的全部操作；多账户隔离未考虑。
