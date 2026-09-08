# AI 控制台（大脑 + 执行层）方案

> 2026-09-08 用户需求：把现有「只问答」的助手升级为**主动承担任务执行、复盘、量化分析**的大脑；
> 菜单栏新增 AI 入口承载任务中心/复盘/告警/参数/日志，悬浮球保留轻量问答。
> 追加决策（同日）：**回测不单独成模块**、**预警并入 AI 面板（AI 判断 + 悬浮球提醒）**、**研究页 `/research` 整体下线**。
>
> 本文是**方案与取舍建议**，未实施代码；开工前需用户确认「待决断项」（§7）。

---

## 1. 结论摘要（TL;DR）

| 问题 | 结论 |
|---|---|
| 需求合理吗 | **合理，但必须"先可观测、后自动化"**。读与生成类（L0）可立即自动化；写类（L1/L2）必须先有变更单 + 回滚 + 审计三件套才允许接执行层 |
| 最大风险 | 不是"AI 乱答"，而是**AI 改了参数却无法追溯/无法回滚/无人知晓**。参数一旦被静默改写，全系统结论的可信度塌方 |
| 硬边界 | 真实下单/资金操作、对外推送、数据源凭据类操作 —— 代码层不提供工具（不只是提示词禁止） |
| 复盘能迁移吗 | **能，且迁移成本很低**。`ReviewTab` 是自包含组件（491 行，只依赖 4 个 `@/lib/api` 函数 + `Panel`），数据全走 REST，无页面级耦合（§5） |
| LLM 复盘→系统优化的闭环 | **后端已具备 80%，断在最后一公里**：`suggest_methodology_changes` 只被展示、**没有任何消费方**，改进建议无法落地为参数变更（§5.3） |
| 回测 | 前端不再占模块位；**后端引擎保留**，改由任务中心以「L0 只读任务」形态调用（不删能力、去 UI 占位） |
| 研究页 | 下线，302 到 `/agent?tab=…`；三个 tab 分别落到复盘/告警/任务中心（§6） |

---

## 2. 需求评估：合理性、边界风险与取舍

### 2.1 按"自动化收益 / 出错代价"分三级

| 级别 | 场景 | 建议 |
|---|---|---|
| **该自动化** | 生成复盘报告、跑数据体检、生成量化分析、批量取数、生成参数调整**建议**、整理改进项 | 直接上，无需人工确认（只读/生成，不改状态） |
| **该谨慎** | 参数覆盖变更、提醒规则创建/停用、自选股增删、触发耗时管线（生成简报/精选，消耗数据源配额） | 变更单 + 二次确认 + 自动留回滚点；限白名单参数与幅度 |
| **该禁止（硬边界）** | 真实下单/撤单/资金、对外推送（飞书等）、改数据源凭据/密钥、删除历史数据、关闭风控红线 | **代码层不注册工具**，不是提示词层面禁止 |

### 2.2 执行权限分级（L0–L3）

沿用既有 `require_write_token`（写接口鉴权，opt-in）与工具白名单机制（`assistant/tools.py` 已按此实现）：

| 级别 | 定义 | 是否需要确认 | 是否需要回滚点 | 现有先例 |
|---|---|---|---|---|
| **L0 只读** | 查行情/查库/生成报告/计算分析 | 否 | 否 | 14 个只读工具已落地 |
| **L1 可逆写** | 参数覆盖、提醒规则、自选股、`PATCH` 类状态变更 | 否（但需 dry-run 预览留痕） | **是**（自动记录 before/after） | `PATCH /review/action-items`、`style_router` 配置覆盖 |
| **L2 重操作** | 触发管线、批量（>N 条）变更、删除类 | **是（人工二次确认）** | 是 | `POST /review/run`（配额消耗） |
| **L3 禁止** | 资金/下单/对外推送/凭据 | — | — | 无工具，无端点 |

**关键约束**：L1/L2 的每一个工具必须声明 `scope`（影响面）与 `rollback`（如何撤销）；没声明回滚方式的写工具**不允许注册**。

### 2.3 误操作拦截：四道闸

1. **工具白名单**：不在注册表 → 直接拒绝（现成机制，已覆盖）。
2. **参数校验 + 值域钳制**：超限直接抛错，不静默收敛。先例：`style_router.OFFSET_MAX=0.06`、`WEIGHT_FLOOR/CEIL`（幅度越界 fail-fast，配置非法启动即报错）。
3. **dry-run 预览 + 二次确认**：L1 展示"将要改什么 / 影响哪些结论 / 如何撤销"；L2 必须显式点击确认。
4. **影响面与时段闸门**：批量 >N 条强制转人工；耗配额任务受交易时段/日次数限制；同一参数同一天内不允许被自动改写两次（防抖动）。

### 2.4 可追溯：三件套（沿用行情溯源纪律）

行情侧已有「来源 / 数据时间 / 口径」三件套（`context.format_quote_line`）。执行层同构：

| 对象 | 留痕内容 |
|---|---|
| 每次任务 | `run_id` + 步骤轨迹（输入、调用的工具、输出摘要、LLM 模型与 prompt 摘要 hash、耗时、成功/失败原因） |
| 每次写操作 | 审计日志：`actor`（用户/AI/调度）、`before` / `after`、回滚点 ID、确认人 |
| 每份报告 | `llm_enhanced` 标记（**已实现**：`analyzers.py` 对 LLM 增强维度打标）+ 数据来源清单 |

### 2.5 必须人工确认的场景（清单）

- 任何影响**实盘/模拟持仓**的动作（连带：批量卖出、清仓类）
- 参数变更超出白名单或超出安全幅度（如单维权重偏移 >0.06、权重越界）
- 删除类（报告、配置版本、历史记录）
- 触发外部副作用（推送、发消息、调用付费数据源的重跑）
- 同一结论在**证据不足**时（如样本 <N 日、IC 置信不足）——系统应显示"证据不足，不建议自动采纳"并拦住

### 2.6 取舍建议（我的判断）

1. **先做"看得见"，再做"自动改"**：P0 只上 L0（任务中心 + 复盘迁移 + 日志审计），把 AI 的执行过程全程留痕；参数自动变更放 P1 且默认"建议态"，需人工点确认才生效。
2. **参数自动优化不要一步到位**：当前因子库口径与池内口径不一致（全市场 trend IC 为负但≠池内口径），自动改权重缺乏可信证据。正确顺序是：建议 → 影子 A/B（`picks_shadow` 已有对照组）→ 达到阈值才允许转正。
3. **回测：去 UI、留能力**。前端模块位取消；引擎保留给任务中心调用（`scripts/backtest_picks.py`、MCP 工具清单仍在用）。避免为了"面板整洁"砍掉有真实消费方的能力。
4. **告警：AI 做"判读"，不做"转发"**。规则触发仍是确定性的，AI 只负责 triage（值得提醒 / 忽略 / 升级），理由必须写出来。

---

## 3. 产品与交互设计

### 3.1 信息架构

```
菜单栏（nav-bar）
  工作台 / 盘面 / 市场 / 猎场 / AI 控制台★ / …        ← 新增「AI 控制台」(/agent)
                                    │
                          ┌─────────┴──────────┐
                     AI 控制台（全页面板）     悬浮球（保留）
                     · 任务中心（默认页）      · 轻量问答
                     · 复盘                    · 预警气泡（AI 已判定值得提醒的事件）
                     · 提醒与告警              · 点击气泡 → 跳 AI 控制台对应详情
                     · 数据源
                     · 参数配置
                     · 执行日志与审计
                     · 量化分析
```

- **AI 控制台** = 任务/历史/配置的"系统侧"；**悬浮球** = 随手问 + 即时提醒，不承载长流程。
- 历史记录**统一收纳在面板内**：任务历史、报告历史、变更历史、告警历史，悬浮球只保留当前会话。

### 3.2 模块职责与交互流程

#### ① 任务中心（P0，核心）

- **职责**：创建/执行/追踪任务；展示状态机、步骤轨迹、产物、失败原因、可重跑/可回滚。
- **任务类型（首批）**：`生成复盘` `生成盘前简报` `数据体检` `参数建议` `因子/信号分析` `回测（按需）`。
- **状态机**：`queued → running → (succeeded | failed | canceled | needs_confirm)`
  - `needs_confirm`：L1/L2 任务停在预览态等人工确认（这是"误操作拦截"的落地位）。
- **交互**：右上角「新建任务」→ 选类型 → 填参数（表单 + JSON 双模式）→ 提交 → 列表实时轮询 → 点开看步骤轨迹 → 成功看产物（报告卡片），失败看原因与重试按钮；L1/L2 显示「确认执行 / 撤销」。

#### ② 复盘（P0，迁移自研究页）

- **职责**：复盘报告列表/详情、改进项（采纳/驳回）、有效性统计、LLM 执行复盘。
- **交互**：选日期（支持深链 `?date=`）→ 报告详情（结论/改进项/证据）→ 改进项一键「生成参数变更单」→ 进参数配置模块确认生效。
- **LLM 执行**：`ASHARE_REVIEW_MODEL=rules|llm`（已支持），执行后报告带 `llm_enhanced` 标记；可由任务中心一键「用 LLM 重跑复盘」。

#### ③ 提醒与告警（P1，自研究页迁入 + AI 判读升级）

- **职责**：规则 CRUD（价格/涨跌幅，scope=自选/指定/全市场）、事件流（确认 ack）、**AI 判读层**、通知通道设置。
- **AI 判读**：规则触发 → 生成 `AlertEvent` → AI triage（结合当下相位/持仓/重复度）输出 `{verdict: notify|ignore|escalate, reason}` → 仅 `notify` 才在悬浮球弹泡，`escalate` 进任务中心待办。
- **纪律**：**不因告警直接发飞书**（遵守 2026-09-08 推送定稿：飞书只保留盘中买点卡）；告警在**系统内**呈现。

#### ④ 数据源（P1）

- **职责**：展示各 provider 能力等级与健康（复用 `provider_capabilities` 注册表）、配额/限流（ths 429 是系统性风险）、降级可见（熔断切源时界面明示）、数据源参数配置（不改凭据）。
- **交互**：卡片列表（源 → 能力 → 今日成功率 → 最近失败原因）→ 点击看明细与"最近一次成功时间"。

#### ⑤ 参数配置（P1，系统优化闭环的落点）

- **职责**：白名单参数的查看/变更/回滚/版本对比；变更单（来源可追溯到某次复盘的某条改进项）。
- **机制**：参数走**运行时覆盖层**（先例：`style_router._load_override()` 每次读配置，改后无需重启）；每次变更写版本（before/after + 来源 + 生效时间），一键回滚。
- **门禁**：白名单 + 幅度钳制 + 证据门槛（样本/IC 达标才允许"自动建议"；转正需影子 A/B 结果）。

#### ⑥ 执行日志与审计（P0，与任务中心同步上线）

- **职责**：统一时间线（任务/写操作/告警/推送/数据源异常）、按对象检索、导出；提供**回滚**入口。
- **保留**：≥90 天（写操作审计长期保留）。

#### ⑦ 量化分析（P2）

- **职责**：信号健康（`signal_health` 已有前端零消费，正好转正）、RPS、因子 IC 与因子看板、影子 A/B 对照结果、胜率/偏离值等统计。
- **回测**：不占模块位，作为任务中心的一类任务（引擎保留）。

### 3.3 关键数据结构（TS）

```ts
// ---- 任务 ----
type TaskType = "review" | "brief" | "data_check" | "param_advice" | "factor_ic" | "backtest";
type TaskStatus = "queued" | "running" | "succeeded" | "failed" | "canceled" | "needs_confirm";

interface AgentTask {
  id: string;
  type: TaskType;
  status: TaskStatus;
  params: Record<string, unknown>;     // 任务入参（表单/JSON 同源）
  created_by: "user" | "ai" | "scheduler";
  created_at: string;                  // ISO
  started_at?: string;
  finished_at?: string;
  steps: TaskStep[];                   // 步骤轨迹（可追溯）
  result_ref?: { kind: "report" | "artifact"; id: string };
  error?: { code: string; message: string; retryable: boolean };
  risk_level: "L0" | "L1" | "L2";
  rollback_ref?: string;               // 有写操作时必带
}

interface TaskStep {
  index: number;
  name: string;                        // 如 "拉取涨停池" / "LLM 生成结论"
  input_summary: string;
  output_summary: string;
  llm?: { model: string; prompt_hash: string; enhanced: boolean };  // 对齐全报告 llm_enhanced 标记
  duration_ms: number;
  ok: boolean;
}

// ---- 参数变更（系统优化闭环）----
interface ParamChange {
  id: string;
  param_key: string;                   // 白名单键，如 "picks_style_offsets_json"
  before: unknown;
  after: unknown;
  source: { type: "review_action_item" | "manual" | "ai_suggestion"; id?: string };
  evidence?: { sample_days: number; ic?: number; win_rate?: number }; // 证据不足不允许自动转正
  status: "draft" | "applied" | "rolled_back";
  applied_at?: string;
  rolled_back_at?: string;
  task_id?: string;
}

// ---- 告警判读 ----
interface AlertTriage {
  event_id: string;
  verdict: "notify" | "ignore" | "escalate";
  reason: string;                      // 必须给出可读理由
  model: string;                       // rules | llm
  created_at: string;
}

// ---- 审计 ----
interface AuditEntry {
  id: string;
  actor: "user" | "ai" | "scheduler";
  action: string;                      // "param.apply" / "alert_rule.create" …
  target: string;
  before?: unknown;
  after?: unknown;
  task_id?: string;
  rollback_ref?: string;
  at: string;
}
```

### 3.4 面板布局

```
┌──────────────────────────────────────────────────────────────────────┐
│ AI 控制台    [任务中心] [复盘] [告警] [数据源] [参数] [日志] [量化]  │  ← 模块 tab（URL ?tab=）
│                                            [新建任务 ▾]  L0/L1/L2 指示│
├───────────────┬──────────────────────────────────────────────────────┤
│ 列表/筛选     │ 详情区（Panel，h-full；min-h-0 + overflow-y-auto）    │
│ （状态/类型   │  · 任务：步骤轨迹（时间线）+ 产物 + 失败原因 + 重跑   │
│   /时间）     │  · 复盘：报告 + 改进项（可生成变更单）                │
│               │  · 告警：事件 + AI 判读结论                           │
│  历史记录     │  · 参数：当前值 / 变更历史 / 回滚按钮                 │
│  （统一收纳） │  · 日志：审计时间线                                   │
└───────────────┴──────────────────────────────────────────────────────┘
```

- **契约**：Panel 作 tab 视图根节点必须 `h-full`（或 `flex-1 + min-h-0`），否则 `overflow-y-auto` 不触发被外层静默裁剪（既有纪律）。
- **三态纪律**：所有数据沿用 `undefined=骨架 / null=空态 / 值=渲染`，缺失显式标注，绝不显示 0 或 `unknown`。

### 3.5 实现优先级

| 批次 | 内容 | 验收标准 |
|---|---|---|
| **P0** | AI 入口 + 面板骨架 + **任务中心（仅 L0）** + **复盘迁移** + **执行日志/审计表** + 研究页 302 下线 | 能在面板里创建"生成复盘"任务、看到步骤轨迹与产物；研究页访问自动跳 `/agent`；每次写操作有审计记录 |
| **P1** | 提醒与告警迁入 + **AI 判读层** + 数据源模块 + **参数配置（变更单 + 回滚）** | 告警经 AI 判读后才在悬浮球提示；参数变更可一键回滚且无需重启；改参数全程留痕 |
| **P2** | 量化分析（信号健康转正、因子 IC、影子 A/B 对照） + 参数"建议→A/B→转正"门禁 | 参数自动建议有证据门槛；转正需 A/B 达标；全部可追溯 |

---

## 4. 从"问答"到"大脑"：执行层落地路径

1. **任务化**：把已有能力包装成任务类型（复盘、简报、体检、分析），统一走任务中心的状态机与留痕。
2. **工具扩容（受控）**：在只读工具之外注册 L1 写工具（参数覆盖、提醒规则、自选股），每个工具声明 `scope` + `rollback`。
3. **判读层**：告警/事件经 AI triage 后才呈现，解决"规则噪音"问题。
4. **闭环**：复盘 → 改进项 → 参数变更单 → （影子 A/B）→ 生效 → 下一轮复盘验证（§5.3）。

---

## 5. 复盘模块：迁移可行性与闭环补齐（用户点名）

### 5.1 可迁移性实证

| 检查项 | 结果 |
|---|---|
| `ReviewTab` 是否自包含 | ✅ 491 行，仅依赖 `@/lib/api` 的 4 个函数（`getReviewReports`/`getReviewReport`/`getReviewEffectiveness`/`updateActionItemStatus`）+ `Panel` + `StockLink`，无页面级耦合 |
| 数据是否走 API | ✅ 全部 REST，无页面内状态外泄 |
| 后端端点 | ✅ `POST /review/run`（`require_write_token`）、`GET /review/reports`、`GET /review/reports/{date}`、`PATCH /review/action-items/{id}`、`GET /review/action-items`、`GET /review/effectiveness` |
| 布局契约 | ⚠️ 迁移时按 Panel 契约给 `h-full`（原研究页由 `FadeSwap` 提供 `min-h-0 flex-1`） |
| 深链 | ✅ 已支持 `?date=YYYY-MM-DD`（并有格式校验），迁到 `/agent?tab=review&date=…` 即可 |

**结论：可以直接搬，改动集中在路由与外壳，组件本身几乎不用动。**

### 5.2 迁移步骤（P0）

1. 新建 `apps/web/app/agent/page.tsx`（面板骨架 + `?tab=` 路由）；`ReviewTab` 原样引入（补 `h-full`）。
2. `nav-bar`：`研究` → `AI 控制台`（`/agent`）。
3. `nav-targets.ts`：白名单 `/research` → `/agent`；`research_review` → `/agent?tab=review`、`research_alerts` → `/agent?tab=alerts`、`research_backtest` → `/agent`（键名保留，兼容助手既有跳转）；同步 `routing.originLabel`、助手 `PAGE_TITLES`。
4. `next.config.ts`：`/research` → `/agent?tab=review`、`/research?tab=alerts` → `/agent?tab=alerts`、`/backtest` → `/agent`、`/alerts` → `/agent?tab=alerts`。
5. 删除 `apps/web/app/research/page.tsx`；`backtest-tab.tsx`/`alerts-tab.tsx`（告警迁入后删）/ `review-tab.tsx` 处置见 §6。
6. 更新 `nav-targets.test.ts`（3 处断言）与 `review-tab.test.tsx` 路径引用。

### 5.3 「LLM 执行复盘 → 系统优化」闭环：断点与补齐

**现状（代码实证）**：

- ✅ `ASHARE_REVIEW_MODEL=rules|llm` 已支持 LLM 复盘；LLM 增强维度打 `llm_enhanced` 标记（可追溯）。
- ✅ 改进项（action item）可 `PATCH` 采纳/驳回；`effectiveness` 统计采纳率并给出 `suggestions`。
- ❌ **断点**：`suggest_methodology_changes()` 只在 `GET /review/effectiveness` 里被**展示**，**全仓无任何消费方** —— 建议无法变成参数变更，闭环停在"看得到但改不了"。

**补齐设计（P1）**：

```
复盘报告(LLM) → 改进项 → [AI] 生成参数变更单（白名单 + 幅度钳制 + 证据门槛）
             → 人工确认（或 A/B 达标自动） → 写入运行时覆盖层（无需重启）
             → 下一轮复盘验证效果 → 不达标自动回滚（回滚点来自变更单）
```

- 变更单必须携带 `source`（哪份报告的哪条改进项）与 `evidence`（样本天数/IC/胜率）；证据不足 → 只生成"建议"，不允许自动生效。
- 参数走运行时覆盖层（先例 `style_router._load_override()`），避免"改参数要重启"的高风险路径。

---

## 6. 研究页下线方案（追加决策）

| 原模块 | 去向 |
|---|---|
| 复盘 tab | → AI 控制台「复盘」（原样迁移，§5.2） |
| 预警 tab | → AI 控制台「提醒与告警」（P1，含 AI 判读升级） |
| 回测 tab | **不迁移**；后端引擎 + `api/backtest/*` 保留，改由任务中心以任务形态调用（消费方：`scripts/backtest_picks.py`、MCP 工具清单 `run_backtest`、`ai-brain-plan` akquant 通路） |

- **前端清理**：`research/page.tsx` 删除；`backtest-tab.tsx` 删除（回测 UI 取消）；`alerts-tab.tsx` 在告警迁入后删除；`api.ts` 中 `getBacktestStrategies/getBacktestMandates/runBacktest` 是否一并清理 → 见 §7 待决断。
- **测试清理**：`alerts-tab.test.tsx`、`review-tab.test.tsx`（随组件迁移更新路径）。
- **文档同步**：`docs/INDEX.md`、`docs/api.md`、`PROJECT-MASTER.md`（研究页相关表述）、`architecture-redesign.md`（13→5 导航那段的后续演进）。

---

## 7. 待决断项（开工前需确认）

1. **AI 入口命名与位置**：`AI 控制台`（`/agent`）放在导航哪个位置（建议替换原「研究」位）。
2. **参数自动生效的口径**：默认"建议态需人工确认"，还是允许白名单内小幅变更自动生效（我建议前者，跑满一个 A/B 周期后再放开）。
3. **回测的存废**：仅去 UI（推荐），还是连后端引擎一并归档（会影响 `backtest_picks.py` 与 MCP 契约）。
4. **`api.ts` 回测函数**：保留（供任务中心用）还是随前端一起清。
5. **告警 AI 判读的兜底**：LLM 不可用时（09-04 有全天降级先例）是否退回"规则直接提醒"（建议：退回且界面标注"AI 判读不可用，按规则提醒"）。
