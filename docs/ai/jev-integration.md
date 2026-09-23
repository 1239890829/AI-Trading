# TypeSafe Jev 全局与 AShare AI Trader 集成蓝图

> **定位**：Jev 在本项目与 Codex 全局工作流中的唯一现役设计/运行说明。
> **状态**：2026-09-19；项目内 Jev 主线已通过 PR #31–#34 分批合入 `master`，后续状态仍以 `docs/retro-and-gaps.md` §6.0 为唯一真相源。
> **账本映射**：治理/额度/回退 → W08/GOV-024；Universal Verification 与语义中间层 → W05/IMP-045；JevRouter/受限能力路由 → W05/IMP-046；人工金标准、语义特征与额度实证 → W04/RSH-030；历史涨停/强连板/龙头研究本体 → W04/RSH-031。本文只定义设计与证据，不复制这些任务的状态。
> **原则**：Jev 是高速、低成本、结构化的**语义协处理器**，不是替代确定性代码、回测、风控、DeepSeek、Codex 或 ChatGPT 的“第二个大脑”。

## 1. 为什么引入 Jev

本项目已有大量成熟确定性能力：行情口径、T+1/涨跌停/整手/费用硬门、情绪阈值、因子 IC/ICIR 评估、回测、影子持仓、策略登记册、做T分钟信号等。这些任务**能算就必须算**，交给模型反而更差。

Jev 的价值集中在另一类问题：**需要一点自然语言/语义理解，但答案空间很小且可预先定义**。例如：

- 这条事件是政策/讲话/数据/传闻/公司事件中的哪类；
- 这条消息是否值得继续交给昂贵模型判断题材；
- 用户的问题需要行情、事件、账户还是研究工具；
- 一段检索候选与当前问题是否相关；
- Agent 当前操作是否偏离用户意图或存在危险；
- 一个代码 diff 是否违反“影子信号不能无证据进生产”这类语义规则；
- 一条失败案例主要属于选股、买点、退出、环境、数据还是执行问题。

目标不是“所有地方都调用 Jev”，而是形成：

```text
确定性代码
    ↓
Jev：窄语义判断 / 路由 / 过滤 / 验证
    ↓ 低置信、冲突、复杂
DeepSeek：廉价自由文本 / 中等复杂推理
    ↓ 真正困难
Codex / ChatGPT：复杂代码、综合研究、架构与审核
```

这样才能同时提升一致性并减少高级模型 token。

## 2. 当前全局基础设施

### 2.1 凭据与环境

- TypeSafe Key 存 macOS Keychain，service = `ai.typesafe.jev`。
- 新 shell 自动得到 `JEV_API_KEY` 与 `TYPESAFE_API_KEY`。
- GUI 登录会话通过 LaunchAgent 自动注入，Mac 重启/重新登录后仍可恢复。
- 仓库、`.env.example`、Codex/Claude MCP 配置中不保存明文 Jev Key。
- 项目后端只允许 `app/core/jev_client.py` 读取这两个变量，并有 AST 守卫禁止第二套凭据路径。

### 2.2 已安装的 Codex 全局能力

| 能力 | 当前落点 | 作用 |
|---|---|---|
| TypeSafe 官方 Skill | `~/.agents/skills/typesafe-ai` | 教 Codex 正确拆 Choice / Noul / Score |
| Jev Orchestrator | `~/.agents/skills/jev-orchestrator` | 每轮判断哪里该用 Jev、哪里应升级 |
| 通用 Jev MCP | `~/.local/bin/evaluate-mcp` | 所有 Codex/Claude 项目可直接做 typed evaluation |
| stdin Jev CLI | `~/.local/bin/jev-json` | Web ChatGPT/脚本无法直接用 MCP 时做 bounded 判断；敏感字段网络前拒绝 |
| Privacy-safe capability router | `~/.local/bin/jev-capability-route` | 基于固定版本 BillionsBobby/JevRouter；仅在 ≥2 个真实能力候选竞争时做权限/风险/确认/Schema 感知路由；不保存任务正文 |
| JevRouter source pin | `~/.local/share/jevrouter` | 上游 Router/Policy/Provider 内核；2026-09-19 验证 commit `c83660f8370f52124055b771a38d4a7ea06e8434`、44/44 tests；wrapper 每次校验 HEAD + tracked worktree clean，漂移则网络前拒绝 |
| 全局 usage 报表 | `~/.local/bin/jev-usage-report` | 汇总 Jev token/延迟/purpose 与 capability-routing 元数据；旧 task-router 仅作为 deprecated 历史记录 |
| jev-review | Codex plugin `0.1.1` | 非简单代码切片的结构化工程反馈 |
| jev-guard | Codex plugin `0.3.1` | 工具执行前后做意图/危险/提示注入防护 |
| jev-context | `~/.local/share/jev-context` | 大输出/代码候选的语义过滤，当前 ask-only |
| jev-code-search Skill | `~/.agents/skills/jev-code-search` | 指导何时使用 jev-context |
| jev-browser | `~/.agents/skills/jev-browser` | 浏览器研究/UI验证 |
| Jev Ultrafast | `~/.local/share/jev-ultrafast` | Jev 选动作/DOM目标，Browser Harness 执行 |
| cc-switch text bridge | localhost `127.0.0.1:8777` | 浏览器 TYPE_TEXT 使用当前 cc-switch DeepSeek |

### 2.3 当前生成模型

当前 cc-switch 的运行真值为 `~/.claude/settings.json`；2026-09-19 已实测 `deepseek-v4-flash` 可用。GLM 只保留在历史事故记录中，不再作为当前可用模型。

浏览器文本桥每次动态读取 cc-switch 当前模型，因此以后切 DeepSeek 版本不需要改 Jev Browser。

## 3. 社区 Jev 项目目录的模式归纳

2026-09-19 对 `logicrw/awesome-jev-projects` 及相关 Jev 社区目录做了两轮抓取与本地审计。目录总数会持续变化（不同抓取时点/目录甚至给出不同收录量），因此**不把“收录多少项目”当结论**；真正用于决策的是项目的 decision point、实现边界、调用频率、隐私/权限模型、可复现实测和我们自己的 A/B。对本项目最有迁移价值的不是“复制某个项目”，而是以下模式：

| 社区模式 | 代表方向 | 我们吸收的能力 |
|---|---|---|
| 模型/思考档路由 | Jev Codex Router | 先 shadow 记录“Jev 会选什么档”，不直接插代理层 |
| 上下文压缩 | jev-context / Winnow / compaction | 只在宽检索/大输出时过滤，保留 receipt 与 recall |
| Skill/MCP/Agent 路由 | skillbox / JevRouter / skill-gate | 用 bounded groups 选工具组，不让模型自由发明工具 |
| Agent supervisor / stopping | foreman-jev / limpet / taskuary | 完成度、偏题、继续/停止/升级判断 |
| Guardrail | jev-guard / pi-warden / agentgateway | 危险命令、提示注入、越权工具和用户意图检查 |
| Diff 偏好审查 | jev-pref | 把静态 lint 难表达的项目语义规则变成独立审查 |
| Code review | jev-review | baseline → 修复 → rescore，而不是把分数当目标 |
| Search/rerank | Blink / jev-search / jev-context | 传统召回后做 relevance，不替代召回本身 |
| Browser/Computer | jev-ultrafast | DOM 操作决策与目标选择，复杂 UI 回退普通 browser |
| Trading shadow risk | Prism 等 | 只作旁路/影子风险或语义特征，不直接成为下单信号 |
| Calibration/benchmark | benchmark/workbench/resilience | 任何阈值都用目标域标注与 A/B 证明，不凭 demo 宣称准确 |

## 4. Codex 全局工作流

### 4.1 每轮任务：先判断“要不要 Jev”，而不是先调用 Jev

旧实验 `jev-route` 会给每个非简单任务先分一次 `deterministic / jev / deepseek / codex / chatgpt_deep / human`。二次审计后**已停用并删除活动命令**：它不能真正切换当前宿主模型，却会为本来就明显的任务多花一次 Jev。

当前规则：

```text
任务进入
  ↓
确定性可完成 / 用户已指定工具 / 只有一个合法下一步？
  ├─ 是 → 直接执行，不调用 Jev
  └─ 否
       ↓
是否只是一个 bounded Choice/Noul/Score？
  ├─ 是 → evaluate / jev-json
  └─ 否
       ↓
是否存在 ≥2 个真实、当前可用的 Tool/Skill/MCP/CLI/Subagent 候选，
且哪一个更合适并不显然？
  ├─ 是 → jev-capability-route（JevRouter 内核）
  └─ 否 → DeepSeek / Codex / ChatGPT 按任务本身处理
```

`jev-capability-route` 只做 capability decision。Jev 给概率；Router 代码负责 availability、actor permissions、risk、confirmation、input schema 和安全 fallback。它不会执行选中的能力，也不会保存 request/context 正文，只写元数据 receipt。

Web ChatGPT 无法直接使用本机 MCP 时，可经已授权电脑调用 stdin-only `jev-json`；它与项目 adapter 一样在网络前拒绝敏感字段。

### 4.2 上下文

`jev-context` **当前不自动接管搜索**。本仓 5 组人工锚点实测：

- 已被 rg 召回的关键锚点，经 Jev 过滤 **100% 保留**；
- 5 组 weighted retrieval token reduction = **5.28%**；
- “情绪 → 空仓闸门”上游检索只召回 2/3 锚点，说明**召回问题不能靠 Jev 过滤修复**；
- 因此保持 `ask-only`：宽检索/大输出才用，精确 rg 不加模型开销。

### 4.3 安全

`jev-guard` 已实测：

- `git status` → ALLOW；
- 模拟 `rm -rf ~/` → DENY。

Guard 是第二视角，不替代 shell allowlist、权限、Git/CI、OS 隔离。

### 4.4 浏览器

Jev Ultrafast 已通过自身门禁：ruff、31 tests、JS check、build；Browser Harness 已连接 Chrome。

真实只读 smoke：

```text
example.com
→ Jev 选择 More information
→ Browser Harness 点击
→ IANA help/example-domains
→ DONE
```

约 3.4s 完成。

TYPE_TEXT 也已打通：

```text
Jev 选择输入框
→ localhost bridge
→ claude -p（工具禁用、无会话）
→ cc-switch 当前 deepseek-v4-flash 生成字段值
→ Browser Harness 输入/点击
```

本地表单端到端约 5.3s 完成。

## 5. AShare：事件、资讯与告警

### 5.1 统一 Jev adapter

新增 `backend/app/core/jev_client.py`，作为项目内唯一 TypeSafe HTTP 入口：

- Choice / Noul / Score 问题类型白名单；
- Key 仅从 `TYPESAFE_API_KEY/JEV_API_KEY` 读取；
- state/question 出现敏感字段名或密钥形态时直接 fail-closed；
- `jev_max_state_chars` 限制外发上下文；
- 429/503/529 最多小次数退避重试；
- HTTP/shape 失败只返回安全错误类型，不回显 TypeSafe 响应正文；
- telemetry 只记录 calls / tokens / latency / purpose / agreement / routing reduction，不记录用户正文；
- `data/jev/usage.jsonl` 跨重启追加**元数据收据**（时间/purpose/status/model/tokens/latency/reason），该目录被 `.gitignore` 明确排除；
- `backend/scripts/jev_usage_report.py` 零网络汇总历史收据；坏行显式计数；
- `GET /api/system/providers` 只读暴露 Jev 当前模式与进程内聚合统计，不触发 API 调用。

AST 守卫 `test_jev_single_source.py` 禁止业务模块出现第二套 TypeSafe endpoint/key 读取。

### 5.2 告警判读

原链：

```text
确定性冷却去重 → DeepSeek → notify / ignore / escalate
```

新链：

```text
确定性冷却去重
  ↓
Jev Choice：notify / ignore / escalate
  ├─ shadow：继续 DeepSeek，只记一致/分歧
  ├─ cascade + 高于校准阈值：直接消费 Jev
  └─ 低置信/失败：DeepSeek
```

为什么适合：DeepSeek 在这里主要做三分类，一条告警一个自由生成调用，属于典型“小决策用大模型”。

当前默认 = `shadow`，所以**用户可见提醒完全不变**。未来只有 RSH-030 用人工标签证明某置信区间安全，才允许开启 cascade。

### 5.3 pending 事件辅助

原链：规则判不出方向 → 批量 DeepSeek 生成 direction + theme。

新前置层：

- Jev 对每条事件做 Noul：“是否存在足够直接的非零 A 股题材催化”；
- **不让 Jev 生成题材名**；
- shadow：整批仍交 DeepSeek，并记录 Jev neutral/actionable 与 DeepSeek 结果的一致率；
- cascade：只允许 `Noul <= 校准阈值` 的**高把握中性/弱关联**事件跳过 DeepSeek；
- 非中性事件仍交 DeepSeek 做题材归属；
- 若剩余 DeepSeek 子批失败，整批仍不标 judged，包括 Jev 预判中性的事件，保留原“失败不半写、下轮可重试”契约。

这个方向能省 DeepSeek 的原因不是“让 Jev 替代题材推理”，而是**让明显不值得深入的事件提前退出**。

## 6. AShare：AI 助手 / Agent 路由

新增 `app/assistant/jev_tool_router.py`，只做“工具组 relevance”，不执行工具。

工具组：

1. `market_realtime`：行情/K线/分时/盘口/资金流/大宗；
2. `market_structure`：涨跌停/龙虎/热榜/异动/情绪/题材；
3. `events_news`：事件/新闻/传导链/公告；
4. `selection_research`：精选/因子/回测/盘前/复盘/做T记录；
5. `account_positions`：持仓/自选/模拟账户；
6. `governance`：参数变更/任务中心。

为什么按组而不是直接选一个工具：一个真实问题经常同时需要“持仓 + 公告 + 资金”，Noul 可以多组选中；单 Choice 会把多意图强行压成一个。

安全设计：

- `TOOL_SPECS` 仍是唯一执行白名单；
- Jev 只能缩短**提示词展示清单**，不能给系统增加工具；
- 路由失败 / shape 非法 / 没有组过阈值 → fail-open 到完整工具清单；
- shadow 路由与 DeepSeek 首轮**并行**，若用户回答已经完成而 shadow 尚未结束则取消，不为统计拖慢 `done`；
- telemetry 记录候选工具数、选中数、实际 DeepSeek 使用工具是否被 Jev shortlist 覆盖，不保存用户问题正文。

未来是否 cascade 的硬条件：shadow 的真实 `coverage_miss_rate` 足够低，且 prompt/token 节省有实测价值。

## 7. AShare：知识库、搜索与上下文

可以使用 Jev 的位置：

- KB/文档传统检索后的 relevance；
- 事件/题材证据召回后的 rerank；
- Codex 代码大候选搜索后的过滤；
- AI 助手将大块研究资料交给 DeepSeek 前的候选压缩。

不能用的位置：

- 不能用 Jev 替代 rg/vector/SQL 的**召回**；
- 不能因为 Jev 判 No 就宣称“仓库不存在”；
- 精确搜索只有几个结果时不应额外调用 Jev。

原因来自本仓 jev-context 金标实测：过滤 recall=100%，但一组 upstream retrieval recall=66.7%。所以必须“先保证召回，再谈过滤”。

## 8. AShare：选股、战法与起爆前识别

### 8.1 为什么这里适合“影子语义层”

项目现有选股已经有确定性结构：

- 六维评分；
- 市场相位与风格路由；
- 题材梯队/角色；
- 潜伏池；
- 临板雷达；
- 龙头前瞻；
- 断板/反包/弱转强；
- 影子持仓；
- 策略登记册与准入/退役；
- 回放/回测/证据门。

Jev 的价值是把这些**已有 point-in-time 事实**转成可比较的语义标签，而不是根据事后涨幅“讲故事”。

`app/research/jev_shadow.py` 当前某一 bounded shadow 实验使用**冻结候选集合**（仅是该实验的 choice space，不是猎场全局 taxonomy）：

- `lurk` 潜伏；
- `first_start` 首启；
- `limit_relay` 连板接力；
- `trend_acceleration` 趋势拉升；
- `break_to_trend` 断板转趋势；
- `restart` 再启动/二波；
- `pullback_reversal` 低位反转/超跌反攻；
- `event_driven` 事件驱动；
- `unclear` 证据混合/不足。

在这一次 bounded decision 内，Jev **只能从调用方冻结的候选集合中选择**，不能临场发明新战法。猎场全局分类仍以 `product/hunting-decision-design.md` 的开放多轴情境为准：未来出现新的可观察情境时先作为未归类线索/研究假设进入原证据与准入流程；若后续实验需要新的有限 choice space，应显式版本化该实验候选集，而不是把这里的九个标签扩写成系统封闭枚举。

### 8.2 起爆前增强

Jev 可以把已有早期证据组合成 shadow 标签：

```text
事件/政策新颖度
+ 题材传导直接性
+ 零星首板/相邻链启动
+ 热榜/飙升榜变化
+ 资金从孤票→扩散
+ 量能/筹码/龙头角色
+ 情绪周期位置
  ↓
Jev bounded semantic label
  ↓
“潜伏 / 首启 / 不清楚”等影子类别
  ↓
历史 outcome / Precision@K / walk-forward
```

用途：寻找“金健米业式起爆前结构”的**可迁移谓词**；禁止把某个历史案例名称固化成规则。

只有当某个 Jev 标签/选择在时间切分样本上证明有增益，才能申请进入 Challenger；未经证明永远不改变生产选股分。即使曾通过一次验证，后续也受 U48/GOV-027 的阶段性有效、衰退重验和成本收益纪律，不获得永久生产权。

### 8.3 战法适配而非战法创造

`choose_bounded_tactic(state, candidates)` 只允许从调用方已经通过硬规则筛选的候选战法/动作中选择：

- 候选只有 1 个 → **零模型调用**，确定性返回；
- 多候选 → Jev Choice；
- Jev 不得创造候选集合之外的新动作；
- 数值门、T+1、涨跌停、费用、仓位、风控和执行仍由代码决定。

这适合“当前市场结构更接近潜伏、接力、趋势还是观察”，不适合“让 Jev 随机发明买卖法”。

## 9. AShare：情绪与市场阶段

现有相位 `冰点/修复/发酵/高潮/分歧/退潮` 已由可复算指标和矩阵决定；Jev **不得替换**。

可增强点：

- 把新闻/政策/盘面语言转成 shadow 的“情绪叙事/资金一致性”特征；
- 判断事件是否与当前相位**语义一致/冲突**，作为复盘解释；
- 对“热度高但赚钱效应差”“消息热但资金收缩”做 bounded 结构标签；
- 用于误判案例归因和相位切换预测的研究特征。

生产相位与空仓闸门仍以现有数值口径为唯一真值；Jev 标签只能进入研究数据集，经 walk-forward 证明增益后再议。

## 10. AShare：因子系统

Jev 不计算 IC、ICIR、单调性、覆盖率，也不决定 PASS/CONDITIONAL/FAIL。

适合：

- 把文本公告/事件提炼成**概率型语义候选因子**；
- 候选因子进入 `factor novelty → evaluate → lifecycle governance` 原有流程；
- 对候选因子描述做语义去重/同义聚类，减少重复研究；
- 对因子失败做“数据/冗余/不稳定/样本不足/语义泄漏风险”等辅助归因。

任何 Jev 语义特征必须像普通因子一样接受时间对齐、样本外、消融、冗余和版本治理，**不能因为来自 AI 就跳过因子制度**。

## 11. AShare：做T与盘中战术

现有 `minute_signals.py` 已完成 point-in-time 数值信号：均价偏离、量价背离、量能突变、开盘突破、冷却、失效条件；`minute_decisions.py` 负责记录与 30 分钟结算。这些都是可复算的，Jev 不得替代。

Jev 的正确位置在**候选战术路由与失败归因**：

```text
分钟硬指标 → 产生合法候选动作集合
                  ↓
           Jev Choice（影子）
                  ↓
        哪种已验证战术更适合当前微状态
                  ↓
         minute_decisions 事后结算
                  ↓
   classify_review_failure 做结构化归因
                  ↓
       按日/相位/标的类型做消融
```

例如调用方可给出 `hold / low_absorb / high_reduce` 等**已通过硬规则的候选**。Jev 只能选，不得绕过 T+1、费用、涨跌停或真实持仓约束。

它可能带来的新增研究能力：

- 同样的 `minute_score=-0.6` 在不同相位/题材/角色下是否应该采用不同已验证战术；
- “低吸偏向出现，但题材已退潮”这类跨域条件是否会降低命中；
- 做T失败究竟更多来自 signal、entry timing、environment 还是 execution；
- 按失败类型而非只按 PnL 复盘，寻找可验证的策略改进假设。

以上都先是 shadow label，不直接发买卖指令。

## 12. AShare：复盘、进化与 Agent Supervisor

### 12.1 复盘失败归因

新增固定 `REVIEW_FAILURES`：

- `selection` 选股/题材/角色本身；
- `entry` 进入位置/时机；
- `exit` 退出/持有管理；
- `risk` 仓位/风险；
- `environment` 市场/题材环境变化；
- `data` 数据缺失/陈旧/口径错误；
- `execution` 系统/工具执行；
- `unknown` 证据不足。

用途是把大量自然语言复盘变成可聚合标签，找到“这个月主要亏在选错，还是买点追高”。它**不自动修参数**；参数变化仍走现有提案、实验、影子评估、回滚闭环。

### 12.2 进化议程前置筛选

现有 evolution 每日有 LLM 调用预算。Jev 后续可以前置：

- 哪些证据真正值得进入当日议程；
- 哪些 gap 已被已有任务覆盖；
- 哪些建议属于重复/低信息；
- 哪个问题适合参数实验、文档沉淀、代码调查或人工复核。

这样 DeepSeek 只综合高价值证据，而不是重新阅读所有原始告警/审计行。

### 12.3 Agent Supervisor

吸收 foreman/taskuary 类社区模式，但不增加新的高权限 Agent：

- 当前步骤是否仍服务原任务；
- 是否已满足验收条件；
- 是否出现 scope creep / 过度设计；
- 是继续、测试、回退、升级还是停止。

该能力在 Codex 层先做 advisory；项目内应用 Agent 也只能监督，不能扩大已有自治授权。

## 13. AShare：风险、模拟盘与执行

### 13.1 明确“不使用 Jev”的硬门

以下保持纯确定性：

- T+1；
- 涨停不能买 / 跌停不能卖；
- 100 股整手；
- 费用/滑点；
- 停牌/行情缺失；
- RiskEngine；
- 仓位上限；
- 模拟/真实账户边界；
- 真实券商下单/资金动作。

原因：这些规则有明确真值，模型介入只会增加不确定性。

### 13.2 Jev 可作为旁路 verifier

可做：

- “本次 Agent 工具调用是否看起来在尝试绕过硬门”；
- “用户意图是研究还是执行”；
- “解释文案是否把模拟结果说成真实成交”；
- “某条风险事件是否值得升级人工复核”。

Guard 只能增加一道止险，不得成为唯一安全门。

## 14. AShare：数据质量与数据源运维

Jev 适合处理**非数值规则能直接覆盖的语义异常**：

- provider error/log 分类；
- 新闻/公告正文是否与标题/摘要矛盾；
- 数据源返回错误页、验证码页、免责声明页却被当数据；
- source note 是否说明了口径限制；
- 同一实体跨源名称/单位的语义异常提示；
- 数据质量报告的失败簇归因。

不适合：

- 时间戳比较、单位换算、缺失率、数值范围、freshness；
- provider 熔断阈值；
- 交易日判断。

这些已有代码真值，继续确定性执行。

## 15. AShare：浏览器研究、网页核验和 UI 回归

优先场景：

- 官方公告/政策网页导航；
- 数据网站只读核验；
- 前端改动后真实 Chrome 页面验证；
- 登录态页面的只读检查；
- 自动点击、选择、滚动、读取最终状态；
- 需要输入时用 cc-switch 当前 DeepSeek 生成字段值。

回退普通 browser/computer-use 的场景：

- canvas；
- 复杂 iframe；
- 文件上传；
- 弹窗/多 tab；
- 非标准键盘控件；
- 需要视觉布局判断；
- Jev 低置信或 DOM 不完整。

金融红线：不得用 Jev Browser 点击券商真实买卖、确认订单、转账、改提现/风控设置。

## 16. 项目语义 lint：jev-pref

新增 `jev-pref.json`，目的不是重复 pytest，而是审查静态工具不容易表达的语义边界。

当前规则：

1. **gate**：不得把 Jev/LLM probability/confidence 当上涨概率、策略胜率、代码正确率；
2. **gate**：Jev/LLM/Agent 不得新增真实交易/资金路径或绕过硬门；
3. **advisory**：shadow/research/Jev 信号无证据门不得直接进生产评分/仓位/推送；
4. **advisory**：内部 Jev/Agent 调试治理信息不得包装成前台核心交易结论；
5. **advisory**：生产策略/因子/情绪/做T口径改变应有回测/影子/版本等可复算证据路径。

阈值先保守，积累真实 verdict 后再用 `jev-pref tune` 校准。

当前 core / event-alert / assistant / quant-shadow 四个代码域实测 `jev-pref review --files` 均为 **approve**，secrets suite 同时通过。`jev-review` 已直接经 MCP 完成真实 TypeSafe 调用；修后当前代码中事件 correctness=7.9 / reliability=8.1，助手 correctness=7.9 / modularity=8.2，未出现中高严重度项。工程改动仍以测试/smoke 证据为主，不以评分为目标。

## 17. 额度与成本治理

“Jev 很便宜”不能直接推导“系统一定省额度”。项目必须同时量：

### Jev 侧

- requests；
- model version；
- input/output tokens；
- latency；
- error/retry；
- purpose。

### 大模型侧

- DeepSeek 调用次数；
- DeepSeek 输入上下文长度；
- Codex/ChatGPT 是否少读文件、少跑深推理；
- 升级率。

### 质量侧

- 与人工金标准的一致率；
- false negative / false positive；
- 低置信覆盖率；
- 路由真实工具 coverage；
- 过滤 anchor recall；
- 选股/战法语义特征的 OOS/Precision@K 增量。

只有：

```text
目标质量 >= 基线
且
昂贵模型调用/token 显著下降
```

才能写“节省额度”。同一文件/证据包/候选集在版本与内容哈希未变化时应优先复用已验证的结构化摘要、分类或 rerank 结果；精确查询、小候选集和确定性规则直接零模型返回。缓存命中本身不算节省结论，仍需在同任务质量下比较 ChatGPT/Codex/DeepSeek 的实际上下文、调用次数、升级率和返工量。

已拿到的真实证据：

- 24 条项目事件 Jev 审计：26,534 input tokens，按当时公开价格估算约 $0.001114；这是研究样本，不是账单；
- jev-context 5 组人工锚点：已召回锚点过滤 recall=100%，weighted retrieval token reduction=5.28%，因此**暂不自动化**；
- 告警/事件/助手当前全部默认 shadow，所以不会因为“省额度”改变生产行为；
- 项目 `data/jev/usage.jsonl` 与用户级 `~/.local/share/jev-global/usage.jsonl` 只记元数据；`backend/scripts/jev_usage_report.py` 和全局 `jev-usage-report` 可零网络汇总；
- 旧 `jev-route` 已停用，历史 `routes.jsonl` 只保留为 deprecated 实验数据；新 `capability-routes.jsonl` 只记录真正发生“多能力竞争”时的 decision_id / selected / jev_choice / confidence / token / latency / filtered/fallback 元数据。只有与实际 Codex/DeepSeek/ChatGPT 使用量和任务质量对照后，才能算额度节省。

## 18. 分阶段启用

### Phase 0 — 已完成基础设施

- Keychain；
- TypeSafe Skill；
- evaluate MCP + stdin-only `jev-json`；
- privacy-safe `jev-capability-route`（BillionsBobby/JevRouter 内核）+ metadata-only `jev-usage-report`；旧 `jev-route` 已停用；
- jev-review（MCP 真实 smoke 已通过，按需用于高影响/语义复杂代码切片）；
- jev-guard；
- jev-context ask-only；
- jev-browser + DeepSeek text bridge；
- Jev Orchestrator；
- 项目统一 adapter 与 telemetry；
- jev-pref 项目语义策略。

### Phase 1 — 已落地、默认 shadow

- alert triage；
- event llm_aux actionability；
- assistant tool-group router；
- bounded quant research labels。

### Phase 2 — 需要真实样本后才能 cascade

- 告警高置信三分类跳过 DeepSeek；
- 高置信中性事件跳过 DeepSeek；
- 助手工具清单自动缩短；
- KB/rerank 在高召回前提下自动过滤。

启用条件必须来自 RSH-030，不以 demo/单次 confidence 决定。

### Phase 3 — 研究验证后才允许进入 Challenger

- 选股形态 Jev 标签；
- 战法适配；
- 情绪语义特征；
- 文本事件/公告因子；
- 做T状态语义；
- 复盘失败归因。

必须经过 time-split / walk-forward / 消融 / 策略登记册准入。

### 永不由 Jev 接管

- 真实交易执行；
- 风控硬门；
- 数值计算；
- IC/ICIR；
- 回测真值；
- 交易日/时间；
- 数据单位；
- 账户资金；
- 权限认证。

## 19. 完整运行流程

```text
                         ┌─────────────────────┐
                         │     用户 / 系统事件   │
                         └─────────┬───────────┘
                                   ↓
                         ┌─────────────────────┐
                         │   确定性规则 / 硬门   │
                         │ 数值·权限·风控·时间    │
                         └─────────┬───────────┘
                              已解决│未解决
                     ┌────────────┘
                     ↓
              直接返回/执行
                                   ↓
                         ┌─────────────────────┐
                         │   Jev bounded layer │
                         │ Choice/Noul/Score   │
                         └─────────┬───────────┘
                     高置信低风险 │ 模糊/冲突/高风险
                    ┌─────────────┴──────────────┐
                    ↓                            ↓
          结构化结果/过滤/路由              DeepSeek
                    │                            │
                    │                       复杂仍不足
                    │                            ↓
                    │                    Codex / ChatGPT
                    └─────────────┬──────────────┘
                                  ↓
                         ┌─────────────────────┐
                         │ 测试 / 回测 / Guard │
                         │ jev-review/pref     │
                         └─────────┬───────────┘
                                  ↓
                         ┌─────────────────────┐
                         │ telemetry / ledger  │
                         │ 质量·成本·升级率      │
                         └─────────┬───────────┘
                                  ↓
                      证据够 → 调阈值 / Challenger
                      证据不足 → 继续 shadow
```

## 20. 决策纪律

1. **能用代码就不用 Jev**。
2. **能用 Jev 就不先烧高级模型**，但前提是答案空间 bounded 且目标域已验证。
3. **Jev 失败必须可回退**，不能让增强层拖垮主功能。
4. **Jev 低置信不硬吃**。
5. **Noul 是 yes probability，不是独立 confidence，更不是交易胜率**。
6. **浏览器 DONE 不是验收**，必须独立检查最终页面状态。
7. **省额度必须实测**。
8. **选股/做T/因子增益必须回测**。
9. **Jev 不能扩大 Agent 权限**。
10. **所有项目模块复用统一 adapter/telemetry，不再各写一套外呼。**
11. **单一候选、确定性步骤、用户已明确指定工具时禁止为了“用 Jev”再发一次判断。**
12. **社区 Jev 工具只有同时满足“独特问题 + 有可测收益 + 不扩大权限/隐私面 + 调用频率可控”才进入活跃栈。**

## 21. 2026-09-19 二次价值审计：以 TypeSafe 官方 Use Case Map 为基准

来源：

- TypeSafe 官方：[Example use cases](https://docs.typesafe.ai/concepts/use-case-map)
- 社区雷达：[awesome-jev-projects](https://logicrw.github.io/awesome-jev-projects/)
- JevRouter：[BillionsBobby/JevRouter](https://github.com/BillionsBobby/JevRouter)

官方 Use Case Map 给出的核心设计原则与本项目当前方向高度一致：**代码拥有 control flow，TypeSafe 负责 semantic decisions / language understanding**。因此本轮不再追求“哪里都塞 Jev”，而是按官方 decision shape 判断是否值得使用。

### 21.1 官方类别 → 本系统映射

| 官方类别 | 官方含义（摘要） | 本系统当前状态 | 结论 |
|---|---|---|---|
| AI Automation Software | 代码控制流程，Jev 处理高频语义判断 | alert / event / assistant shadow | **已正确落地** |
| Real-time applications | 快速语义决策嵌入 UI/实时循环 | alert / browser | **保留，但禁止进入交易执行硬门** |
| AI MapReduce over Big Data | 大语料搜索、Agent trace 分类、特征抽取 | 尚未生产化 | **高价值研究方向，必须离线批处理+预算上限** |
| Universal Verification | 验证 prompt / extraction / tool call / 引用 / 回答，识别 hallucination / injection / failure | guard/pref 有一部分；投资研究证据验证尚未完整 | **下一优先级最高的缺口** |
| Harness Engineering | model routing、context retrieval、guardrail、trace classification | evaluate / JevRouter / guard / context / review | **已形成全局层，但本轮收敛调用频率** |
| Search & Retrieval | relevance / rerank / context selection | jev-context ask-only；KB 自动 rerank 未开启 | **有条件价值，先保 recall** |
| Model routing | intent/difficulty/risk → 更合适模型 | 旧 task-router 停用；capability router 生效 | **模型切换暂不自动化** |
| LLM Guardrails | 检查 input/output/tool call | jev-guard + 项目 verifier 计划 | **保留；敏感数据最小化** |
| Semantic code lint | 团队语义规则 | jev-pref | **保留按需，不全 diff 常开** |
| Feature extraction | 从文本提概率语义特征，交经典 ML / ground-truth 验证 | research/jev_shadow.py | **高价值，但只进研究/Challenger** |
| Structured data extraction | 从非结构文本取已知字段 | 当前多数已有确定性解析 | **低优先；parser 失败兜底才考虑** |

### 21.2 官方地图暴露出的真正新增机会

#### A. Universal Verification：比“再加一个选股分”更值得优先

最值得补的是**证据支持关系验证**，不是让 Jev 多预测一次涨跌：

```text
DeepSeek / Assistant 生成结论
        ↓
把“结论 + 最小证据片段”交 Jev
        ↓
Noul/Choice：
- 证据是否真的支持结论？
- 是否把 rumor 写成 fact？
- 是否遗漏限定条件？
- 工具返回是否与最终文案矛盾？
        ↓
通过 / 降级措辞 / 重新取证 / 升级人工
```

这直接对应现有 IMP-045 的摘要/引用 verifier，不新建第二套任务。优先应用于：

- AI 助手引用新闻/公告后的结论；
- 事件 → 题材传导解释；
- 研究报告中的“证据支持结论”；
- Web research 的来源一致性；
- 自动复盘中的因果归因文案。

**不适合**让 Jev 自己验证事实真伪；事实仍要回源。Jev 判断的是“给定证据是否支持给定主张”。

#### B. AI MapReduce：离线挖掘事件/Agent trace，不进盘中热路径

适合把大量历史材料拆成小判断：

- 历史新闻/公告按事件类型、直接性、题材传导、确定性分桶；
- Agent tool trace 按失败类型/无效步骤/重复调用分类；
- 大量复盘按 selection/entry/exit/risk/environment/data/execution 聚合；
- 从历史语料产生候选语义因子，再交 RSH-030 walk-forward。

这类任务可以批量、低优先级运行，并设置**最大样本数 / 最大 Jev input tokens / 可中断**。不应在用户每次打开页面时重新跑。

#### C. Search / Rerank：只做“第二阶段精排”

官方允许 semantic search / ranking，但本仓实测已经证明：Jev 不能修复上游没召回。正确结构始终是：

```text
SQL / rg / vector / 规则召回
        ↓
候选集已经足够高 recall
        ↓
Jev relevance / ranking
        ↓
只把 Top-N 交给 DeepSeek/Codex
```

因此 jev-context 保持 ask-only；未来 KB/研究资料 rerank 也必须先有人工 anchor recall 基线。

## 22. 社区组件二次审计：最终保留 / 降级 / 停用

判断标准只有四个：

1. 是否解决**独特问题**，而不是和现有工具重复；
2. 是否有本机/本仓的**可复现实证**；
3. 是否不会扩大权限、secret、源码外发或持久化面；
4. 调用频率是否可控，节省的高价模型成本是否有机会覆盖 Jev 开销。

| 组件/模式 | 最终状态 | 为什么值得/不值得 | 调用纪律 |
|---|---|---|---|
| typesafe-ai Skill | **保留** | 方法论来源，不主动产生 API 成本 | 只指导怎么拆 typed decision |
| evaluate / typesafe-mcp | **保留，核心 primitive** | 通用 Choice/Noul/Score；避免每项目自造 HTTP | 只有真正 bounded question 才调用 |
| jev-json | **保留** | Web ChatGPT/脚本无法 MCP 时的安全 stdin 接口 | 不做高层推理，不传 secret |
| BillionsBobby/JevRouter 内核 | **新增保留，条件调用** | 权限/风险/confirmation/schema/大候选路由是独特价值；44/44 tests + 真实 API 权限过滤已验证 | 仅 ≥2 个真实能力候选且选择不明显 |
| 上游 JevRouter 默认 CLI receipt | **不启用** | 会保存完整 request/context/raw response 到项目 .jevrouter/，与 metadata-only 原则冲突 | 使用 privacy-safe wrapper |
| 旧 jev-route | **停用并删除活动命令** | 不能真正切当前 Codex 模型；每个非简单任务先调用一次是额外浪费 | 历史 routes.jsonl 仅保留审计 |
| jev-guard | **保留** | 安全价值独立；只读工具可 skip，无 Jev 调用；写/危险/不可信结果才判断 | 不把它当 sandbox；敏感内容最小化 |
| jev-review | **保留按需** | 对高影响/语义复杂代码提供第二视角；真实 MCP 已验证 | 简单机械修改/纯文档不强制调用；tests/CI 永远优先 |
| jev-pref | **保留项目级按需** | 能表达“shadow 不得无证据进生产”等静态 lint 难表达规则 | 只审治理相关/高影响 diff；大 diff 分切片 |
| jev-context | **保留 ask-only** | filter recall 对已召回锚点 100%，但本仓 weighted reduction 仅 5.28% | 只用于宽搜索/大输出；精确 rg/少量结果禁止调用 |
| jev-browser / Ultrafast | **保留条件调用** | 未知 DOM 下一步选择有真实价值；CLICK/TYPE_TEXT 已端到端验证 | 已知 deterministic 步骤直接 Browser Harness；只有动作/目标不确定才用 Jev |
| 0xNatoshi Jev Codex Router | **暂不采用** | 自动模型/effort 路由有潜力，但需要额外代理链；本机没有其依赖 router，且不能证明优于现状 | 等真实会话 A/B 后再议 |
| Agent supervisor / Stop hook 类 | **暂不安装** | 可能减少早停，但会增加每轮/每停点调用；现有 ledger + tests + completion rules 已较强 | 只有出现可量化“早停问题”再试验 |
| Trading-as-Jev-signal 项目 | **只吸收实验方法，不安装** | 正确价值是 shadow risk/feature，不是 Jev 直接 BUY/SELL | 所有交易结果必须回测/影子/硬门 |

### 22.1 jev-guard 为什么没有被删

它看似“每个工具都问 Jev”，但源码/文档显示：

- Read/Grep/Glob/WebFetch 等 read-only tool 可直接 skip；
- 本地 edit/search 的结果扫描也有 skip；
- instruction-file scan 有 content-hash cache；
- 主要成本集中在真正有副作用的 action、外部不可信内容、技能/规则审计。

这与本项目的高自治 Codex 工作流匹配。它的价值不是“提升代码质量”，而是**在 agent 自动化增强后给工具执行面增加第二道语义安全检查**，因此与 jev-review / jev-pref 不重复。

### 22.2 jev-context 为什么不删除也不自动化

它已经证明：

- 条件 filter recall（对已召回人工锚点）= 100%；
- weighted retrieval payload reduction = 5.28%；
- upstream retrieval 有 2/3 的漏召回案例。

所以它不是无价值，而是**收益只在候选很多时出现**。保留 ask-only 比“删除”或“全局 auto”都更合理。

### 22.3 Browser Jev 的调用门槛

```text
已知 URL + 已知 selector/检查条件
→ 直接 Browser Harness / 普通 browser，不用 Jev

页面结构未知，但 DOM/ARIA 控件可枚举
→ jev-browser

canvas / iframe / upload / popup / 视觉布局
→ 普通 browser/computer-use

任何 DONE
→ 独立验证最终状态
```

这样避免“每次浏览器验证都让 Jev 重新决定本来已知的下一步”。

## 23. BillionsBobby/JevRouter：为什么采用内核、但不照搬默认安装

### 23.1 它解决了什么我们原来没有完整解决的问题

JevRouter 的价值不是“再做一个 Choice”，而是把 Jev 决策和**本地策略层**明确分开：

```text
真实候选能力
  ↓
Jev：哪个最合适？给概率/置信
  ↓
Router：
- available？
- actor permissions 足够？
- risk 是否允许？
- 是否必须 confirmation？
- input schema 合法？
  ↓
selected / needs_confirmation / no_decision / safe fallback
```

这正好补上普通 `evaluate` MCP 的一个缺口：`evaluate` 负责 typed judgment，但不会替你管理宿主能力权限和风险。

### 23.2 本机验证

- 上游固定到 commit `c83660f8370f52124055b771a38d4a7ea06e8434`；
- `npm run typecheck` 通过；
- 上游 tests：**44/44 pass**；
- 真实 TypeSafe route：`jev-1.13.0`；
- 一次普通 3 候选路由约 1.57s / 395 input tokens；
- 正确 manifest + actor permissions smoke：Jev 以 **0.99** 选中 `edit_code`，但 caller 没有 `write_repo`，Router 标记 `filtered=true / actor_missing_permissions:write_repo`，并安全退到 `search_code`；
- privacy-safe wrapper 再测：同样能保留 `jev_choice=edit_code`，实际 `selected=run_tests`，约 **698ms / 383 input tokens**，且不生成项目 `.jevrouter` 目录。

这个结果证明：**Jev 的判断可以错在“用户最想做什么”与“调用者当前被允许做什么”之间，但代码策略层仍能把它收回来。**

### 23.3 为什么不用上游默认 CLI receipt

上游 `route --stdin` 会把完整 receipt 写到当前目录 `.jevrouter/decisions/`，其中包含：

- request；
- context；
- candidate ids；
- raw Jev response；
- probabilities / confidence。

对一个独立路由项目这是优点，但对我们的全局 Codex 工作流会扩大：

- 私有任务正文落盘；
- 项目目录污染；
- Git 误提交面；
- 长期日志清理负担。

因此采用**SDK 内核 + 本机 privacy-safe wrapper**：

`~/.local/bin/jev-capability-route`

只持久化：

- decision_id；
- status；
- selected；
- jev_choice；
- confidence；
- model；
- input/output token；
- latency；
- candidate_count / filtered_count；
- fallback reason。

不持久化 request/context。

### 23.4 JevRouter 使用门槛

必须同时满足：

1. 候选数 ≥ 2；
2. 候选必须是当前宿主**真的能调用**的能力；
3. 选择存在语义歧义，不能靠确定性规则直接决定；
4. candidate manifest 的 permission / risk / availability 真实填写；
5. 高风险动作最终仍由宿主/人类确认。

单一候选会由 wrapper 在**网络请求前直接拒绝**，避免浪费 Jev。

## 24. OpenRouter：当前明确不接入

### 24.1 Jev 不会因为走 OpenRouter 就变免费

2026-09-19 核对 OpenRouter 模型页：TypeSafe Jev 1.13 仍是 **$0.042 / 1M input tokens，output $0**。因此为了调用 Jev 去配置 OpenRouter Key没有成本优势。

JevRouter 已支持直接 `JEV_API_KEY`，而我们已经通过 macOS Keychain 持久化 TypeSafe Key，所以：

```text
Jev / JevRouter
→ 继续直接 TypeSafe
→ 不增加 OpenRouter 中间层
```

### 24.2 `openrouter/free` 是另一回事

OpenRouter 确实提供 `openrouter/free` 生成式模型路由，token 价格为 0；当前 Free 方案公开限制约 **50 requests/day**，而且会从可用免费模型中动态选择。

它适合：

- 临时实验；
- 非关键文本生成；
- DeepSeek 故障时人工选择的低保证应急。

它不适合：

- 生产交易/研究事实链；
- 稳定的 Jev typed decision；
- 需要固定模型行为的 browser text helper；
- 需要高请求量或明确 SLA 的后台任务。

当前已有 cc-switch DeepSeek，新增 OpenRouter free fallback 只会扩大 provider 分叉和不确定性，所以**不配置**。

### 24.3 Key 处理结论

- 本轮**没有**把 OpenRouter Key 写入 `.zshenv` / `.zprofile` / Codex config / Claude settings；
- 当前 JevRouter 走现有 Keychain TypeSafe Key；
- 如果未来确实需要 OpenRouter，应重新生成新的 Key，再放 Keychain/secret manager，不复用聊天中出现过的旧 Key。

## 25. Jev 调用预算纪律：怎样防止“便宜所以滥用”

Jev 单价低，不代表每个判断都应该调用。真正需要控制的是**调用次数 × 输入长度 × 是否真的替代了更贵步骤**。

### 25.1 调用前四问

每次准备调用 Jev 前必须依次判断：

1. **能不能确定性解决？** 能 → 不调用。
2. **答案空间是否 bounded？** 不是 → 交 DeepSeek/Codex/ChatGPT。
3. **有没有至少两个真实候选/一个明确 yes-no 属性/一个明确 rubric？** 没有 → 不调用。
4. **结果会不会改变下一步，或产生可测量证据？** 不会 → 不调用。

### 25.2 高频位置的预算规则

| 场景 | 当前规则 | 原因 |
|---|---|---|
| Codex capability routing | 只有 ≥2 个真实候选且选择不明显 | 避免每个任务前固定烧一轮 |
| jev-guard | 按插件 read-only skip / cache；不额外手动重复 guard | 安全调用值得，但不要双重检查同一动作 |
| jev-review | 高影响/语义复杂/跨模块 coherent slice 才跑 | 小机械 diff 的 tests/static 足够 |
| jev-pref | 触及治理语义/生产 admission/交易红线时跑 | 普通格式/文案 diff 不值得 |
| jev-context | 只有候选很多、baseline payload 显著时跑 | 本仓实测平均只省 5.28% |
| jev-browser | 下一动作/目标元素不确定时跑 | 已知操作直接 Harness 更便宜 |
| event/alert/assistant runtime | 继续 shadow，按 telemetry 决定是否 cascade | 需要目标域校准 |
| 大规模历史挖掘 | batch/offline + 样本/token 上限 | 防止 MapReduce 变成无界账单 |

### 25.3 什么时候应该主动删掉一个 Jev 用法

满足任一条件就进入删除/停用候选：

- 30–50 次真实调用后，几乎从不改变确定性/大模型下一步；
- 质量不提升，而且高级模型 token 也没有下降；
- 平均输入比被替代的大模型上下文还大；
- 失败/低置信导致大多数请求仍要升级，形成双重成本；
- 与另一个 Jev 组件解决同一问题但多维护一层；
- 为了接入它需要扩大权限、保存大量正文或复制 secret。

旧 `jev-route` 就是本轮按这个标准被停用的第一个实例。

## 26. 后续 Jev 优先级：只做最有增量的三类

### P0：Universal Verification（IMP-045 继续）

优先实现最小 evidence verifier，而不是再加一个 router：

```text
claim + source excerpt
→ Jev Noul/Choice
→ supported / contradicted / insufficient
→ 低置信回源或 DeepSeek
```

目标指标：引用支持 precision、rumor→fact 误写率、重新取证率、额外延迟。

### P1：Research/KB second-stage rerank（IMP-045）

只对高-recall 候选集合做 relevance/ranking；先建立人工 anchor，再决定自动化。目标是减少交给 DeepSeek/Codex 的上下文，而不是替代 SQL/vector/rg。

### P1：Semantic feature extraction + historical MapReduce（RSH-030）

把事件直接性、政策确定性、题材传导、叙事拥挤、相位一致性等做成 shadow feature，和价格/资金/情绪特征一起做 time-split / walk-forward / ablation。

只有 ground-truth 证明增量后才进 Challenger。

### 暂缓

- 自动切 Codex 模型/effort：先没有足够真实会话 A/B；
- Stop/Foreman supervisor：当前没有量化的“Agent 经常过早停止”问题；
- Jev 直接交易 BUY/SELL/HOLD：违反本项目决策纪律；
- Structured extraction 全面 Jev 化：现有确定性 parser 成本更低、可审计性更强。

## 27. 二次审计后的最终活跃栈

```text
                           ┌────────────────────┐
                           │  Deterministic code │
                           │ rules/tests/risk    │
                           └─────────┬──────────┘
                                     ↓
              ┌────────────────────────────────────┐
              │     Jev primitive / router layer   │
              │ evaluate · jev-json                │
              │ jev-capability-route (conditional) │
              └─────────────┬──────────────────────┘
                            ↓
         ┌──────────────────┼────────────────────┐
         ↓                  ↓                    ↓
   jev-guard          jev-pref/review      jev-browser
  risky/untrusted      high-impact diff     ambiguous DOM
         │                  │                    │
         └──────────────────┼────────────────────┘
                            ↓
                  DeepSeek / Codex / ChatGPT
                    only when actually needed
                            ↓
                    tests / backtest / CI
                            ↓
                    metadata-only telemetry
```

**不在活跃默认链里的东西**：旧 `jev-route`、自动模型代理、Jev Stop-hook、OpenRouter provider、Jev 直接交易决策、jev-context auto mode。

## 28. Universal Verification 第一批落地（2026-09-19）

新增 `backend/app/core/semantic_verify.py`：只做 **evidence → claim** 支持关系判断，不做事实检索。输出固定为 `supported / contradicted / insufficient`，单次请求可并行验证多条 claim。

首批助手接线原则：

- 现有 deterministic grounding 继续第一优先；已经能机械证明数字编造/交易指令越权时，不再多花一次 Jev；
- verifier 默认 `off`，显式 `shadow` 才启用；
- shadow 通过 StreamingResponse background task 在流结束后运行，不改变 SSE 协议、不阻断回答、不增加首 token 或 `done` 延迟；
- **只要存在 `extra_block` 就整轮跳过 verifier**：不仅 evidence 私有，最终 claim 本身也可能复述持仓/精选/内部结论，不能只“删 evidence”后继续发 claim；
- 只要使用过任一非公共工具也整轮跳过；只有纯公共市场/新闻/事件工具链才允许进入 Jev。持仓、自选、模拟账户、参数治理等私有上下文与由其生成的 claim 一律不外发；用户问题本身若出现“我的持仓/仓位/成本价/账户/余额/自选/资产/盈亏”等私人语义，也整轮跳过，防止 claim 复述用户提供的私有信息；
- claim 由确定性分句/事实标记筛选，最多 4 条；证据默认最多 12,000 字符，避免无边界 token 消耗。

真实 synthetic smoke：

- “合同已签署并生效” → `supported`；
- “净利润同比增长50%”而 evidence 未披露增速 → `insufficient`；
- “公司已取消合同”而 evidence 明确合同生效 → `contradicted`；
- `jev-1.13.0`，986 input tokens，约 1.35s。

这只是接线 smoke，不是准确率结论。是否常开 shadow、是否未来产生用户可见 verifier 状态，必须由 RSH-030 金标准决定。

## 29. RSH-030 人工金标准 v1

新增 `backend/scripts/jev_goldset.py` 和固定样本队列 `data/labels/jev_goldset_events_v1.jsonl`。

### 29.1 样本纪律

- 从真实 `event_card` 稳定分层抽样；v1 固定 seed = `jev-rsh030-v1`；
- 共 240 条，`policy / statement / data / rumor / corporate / other` 各 40 条；
- 现有规则结果只放在 `reference_rule`，**绝不复制到 human**；
- `human.category / human.certainty / human.actionable` 初始全部为 `null`；
- scorer 只把已填写的 `human` 当真值，规则标签不能参与 accuracy 计算。

### 29.2 使用方式

```bash
cd backend
python scripts/jev_goldset.py export-events \
  --db ../data/ashare.db \
  --out ../data/labels/jev_goldset_events_v1.jsonl \
  --count 240 --seed jev-rsh030-v1

# 人工标注前：允许 human 为空
python scripts/jev_goldset.py validate ../data/labels/jev_goldset_events_v1.jsonl

# 人工标注完成后必须严格校验
python scripts/jev_goldset.py validate ../data/labels/jev_goldset_events_v1.jsonl --require-human

# 预测结果必须另存 predictions.jsonl，再和 human 真值评分
python scripts/jev_goldset.py score ../data/labels/jev_goldset_events_v1.jsonl predictions.jsonl
```

v1 导出已机械验证：240 行、6 类各 40、`human_complete=0`；使用同一真实库 + seed 再导出与入库队列 `cmp` 逐字一致，未标注队列 SHA-256 = `fc8776d15f563b10b694b8108be84f24ad331c85b536045c9689018f194c3ef7`（189,954 bytes）。严格校验在未人工标注时返回失败，这是预期行为，用来阻止“未标完就算准确率”。该 hash 是**未标注队列指纹**；人工填写 `human` 后文件 hash 改变属正常现象。

### 29.3 Jev 预标注与人工审核优先级（不写 human）

为了降低 240 条人工标注的机械成本，新增 `predict-jev / prioritize / compare-reference` 三个子命令。它们的职责严格分开：

- `predict-jev`：用固定 `jev-1.13.0` 为每条 event 生成独立 prediction；**绝不修改 gold queue 的 `human` 字段**；
- `prioritize`：按“规则/Jev 分歧 + Jev 自身不确定度”给人工审核排序，优先看信息量最大的样本；
- `compare-reference`：只计算现有规则与 Jev 的 agreement/cross-tab/confidence 分布，输出字段明确叫 `agreement_not_accuracy`；现有规则不是 human ground truth。

产物：

- `data/labels/jev_goldset_events_v1_predictions_jev-1.13.0.jsonl`
- `data/labels/jev_goldset_events_v1_predictions_jev-1.13.0.meta.json`
- `data/labels/jev_goldset_events_v1_review_priority.jsonl`
- `data/labels/jev_goldset_events_v1_review_priority.meta.json`
- `data/labels/jev_goldset_events_v1_rule_agreement.json`

完整 240 条实跑：40 个请求（batch=6），返回模型全为 `jev-1.13.0`；input **206,482 tokens**、output 29,441、总网络往返约 34.64s、平均约 **866ms/request**。按当时公开 $0.042 / MTok input 估算约 **$0.008672**（不是账单）。预测文件 SHA-256 = `c344ce0a881db27f6c22001a43d4d9e3876cd699ddd4be333d3105606a0ddc08`；review-priority SHA-256 = `7b5242d941215bcca5365cb7e7e51099fabee31eaa907a5fe351abed143778be`。

`prioritize` / `compare-reference` 现在必须同时读取 prediction meta，并核对 `queue_sha256` 与 prediction row count；版本错配在分析前直接失败。review-priority 另写 meta sidecar，保存 queue/prediction/review-priority 三个指纹和原因统计。

**与现有规则的一致率（不是准确率）**：

- category：133/240 = **55.42%**；
- certainty：187/240 = **77.92%**；
- actionable：138/240 = **57.50%**。

审核优先级统计：category 分歧 107、certainty 分歧 53、actionable 分歧 102；category confidence <0.75 有 90 条，certainty confidence <0.75 有 56 条；actionable Noul 在 0.25–0.75 的不确定区间有 **150/240**。

这批结果还削弱了“Jev 很快就能大量过滤 event”的假设：actionable Noul **<=0.05 为 0/240**、<=0.10 为 9/240、<=0.20 为 41/240，>=0.90 也是 0/240。当前 `events/llm_aux` 的极保守 `<=0.05` neutral cascade 在这份**平衡研究队列**上不会节省任何深化调用。该队列不是生产 pending pool，所以不能据此直接改阈值；正确下一步仍是完成 human gold 后做 threshold/Precision/Recall/成本联合校准。

### 29.4 真实 API 舍入边界

240 条首轮运行曾在中途安全失败：Choice probability 展示值因逐项舍入不满足“精确 sum=1±0.001”。输出使用原子写入，因此失败后没有留下半份 predictions/meta。二次审计 JevRouter 官方 provider 也只校验每个概率有限且在 [0,1]，不强制序列化后的展示值精确求和。

预标注工具现在只容忍**两位小数逐项舍入可解释的上界**：`max(0.001, 0.0051 × label_count)`；仍要求每个 probability 在 [0,1]、keys 与 criteria 完全一致，超出舍入上界继续 fail-closed；raw probabilities 原样保存，不私自归一化。这个修正有专门回归用例，不能退化成“任意概率和都接受”。

### 29.5 Gold scoring 的完成度门

`score` 默认要求所有 row 的 `human.category / human.certainty / human.actionable` 都合法且完成；未完成时直接拒绝，防止部分样本被误写成“完整准确率”。只有人工明确需要查看标注进度时，才可显式传 `--allow-partial`；输出会带 `human_complete / human_total / partial=true`，不能作为生产阈值依据。prediction event_id 也必须唯一，重复 ID 直接 fail-closed。


## 30. RSH-031：历史涨停/强连板/龙头研究中的 Jev 边界

详细研究协议见 [历史涨停/强连板/龙头研究蓝图](../research/limit-up-dragon-research.md)。这里仅固定 Jev 的全局调用边界，避免未来实现把“适合语义抽取”误解成“适合直接预测涨停”。

### 30.1 允许：历史语料的 bounded MapReduce

对当时已经可见的新闻、公告、政策、涨停原因与证据文本，Jev 可以批量输出预定义 Choice/Noul/Score 特征，例如：

- event_type / certainty / novelty；
- source_type / source_reliability；
- directness_to_company / directness_to_theme；
- 产业链或题材分支映射的 bounded 候选；
- stale/priced-in、narrative_crowding、phase_consistency 的研究标签；
- evidence conflict / claim support 的 verifier 结果。

输入窗口、事件时间、样本身份、题材成员和控制流由确定性代码拥有；Jev 不得看到未来收益后再生成早期特征。

### 30.2 允许：检索后的第二阶段精排

先用确定性时间/实体/关键词/来源约束召回当时可见材料，再由 Jev 做相关性、直接性、冲突度精排。是否真的减少昂贵模型调用，要和相同 recall 基线做实测；“Jev 便宜”不是省额度的证明。

### 30.3 允许：自动复盘的 Universal Verification

研究摘要出现“X 因 Y 涨停”“A 题材传导到 B 分支”“该消息在起爆前已存在”等主张时，可用现有 verifier 思路核：

- claim 是否由 evidence 支持；
- evidence 的 available_at 是否早于 decision_asof；
- 公司直接受益与题材相关是否被混写；
- 相关性是否被写成已证因果；
- 同一来源是否被重复包装成多份独立证据。

失败只降低/撤销该解释，不改变价格事实或交易硬门。

### 30.4 人工 anchor 与晋级

RSH-031 可提出按市场状态、事件类型、板高、题材阶段分层的历史涨停域人工 anchor，但标注工具、prediction/human 分离、gold 完成度门、threshold/Precision/Recall/成本校准仍统一归 RSH-030。Jev 预标不得写入 human。

任何 Jev 历史语义特征若想进入猎场，仍和普通因子一样经过 point-in-time、matched baseline、walk-forward、ablation、multiple-testing、成本/可成交及最近 untouched holdout；通过后也只先作为 challenger/shadow。

### 30.5 禁止

- 让 Jev 直接回答“某股明天涨停概率”并用于生产排序；
- 用最终连板高度、后续涨幅或后来新闻反向生成早期语义；
- 把 Jev confidence 当策略胜率；
- 让 Jev 调交易硬门、仓位、权限、证券规则或策略准入；
- 因为 Jev 能把赢家故事解释得更顺，就宣称起爆前识别能力提升。

## 31. 2026-09-21 多窗口审计：实际使用与 Web/Codex 边界

- **可用性**：本机凭据有效；统一 `app.core.jev_client` bounded live smoke 成功返回 `jev-1.13.0`。官方 Jev 仍属 early access，因此“API 可用”与“目标域可靠”分开。
- **真实使用**：审计时项目 `data/jev/usage.jsonl` 为 172 calls（169 success / 3 failed），主要 `alert_triage=168`、`event_llm_aux=4`；bounded live smoke 后 173 calls。2026-09-21 晚间再用真实 `/api/assistant/chat` 请求查询 600519 行情/新鲜度，usage 新增 `assistant_tool_router=1`，`jev-1.13.0`、status=ok、约 711ms；总计 174 calls（171 success / 3 failed）。这证明 assistant shadow 消费者已真实运行，但不证明 human accuracy、cascade 收益或业务增益。
- **仍未证实**：RSH-030 的 240 条 human gold 仍为 0/240；该字段只能由独立人工完成，Jev/ChatGPT 预标不得回写。`jev_assistant_verify_mode=off` 属显式关闭，不是故障。
- **网页端**：ChatGPT 产品当前没有原生 TypeSafe/Jev tool，因此网页模型不能像内置工具一样直接调用；但在本项目已授权 Remote Desktop/本机执行环境下，网页 ChatGPT 可调用统一 `jev_client`，本轮已实测。故“网页端绝对无法用 Jev”不成立。
- **Codex**：Codex/其它 coding agent 更适合加载 TypeSafe Agent Skill 并把 Jev 作为代码工作流中的 bounded router/reviewer；这不意味着 Jev 替代 Codex。实现、测试、Git 修改仍交 coding agent/执行侧，Jev 只给 typed probabilistic judgment。
- **RSH-026 等任务**：数据库身份、数值、时间、原子性、回测和交易硬门必须由确定性代码/数据证据判定；RSH-026 没让 Jev 做核心判断是正确设计。对高影响多轮 release 可选用一次 bounded semantic review 辅助发现语义遗漏，但 Jev 缺席本身不构成失败。


## 32. 2026-09-23 可观测性、命名、降级与 Codex 同源契约

本节是对现役架构的**收口增强**，不是另建第二套 JEV 系统。2026-09-23 重新核对 `master` 后确认：`app/core/jev_client.py`、`assistant/jev_tool_router.py`、`research/jev_shadow.py`、gold-set / usage report 与对应测试均已存在；因此此前“JEV 尚未接入生产/研究代码”的聊天判断作废，后续一律以仓库现状为准。

### 32.1 命名边界

- JEV 专属模块、API、任务、可视化组件、状态与回退字段使用 `JEV`（用户可见）或 `jev_`（代码标识）命名。
- “交易智能体 / 猎场 / 复盘 / 通用 LLM”等混合能力仍按真实职责命名；不能为了形式统一把整个 AI/Agent 域改叫 JEV。
- JEV 输出来源与 rules / DeepSeek / fallback 必须可区分；fallback 不得伪装成 JEV 判断。

### 32.2 可视化不是“思维链”

JEV 的项目价值来自 bounded Choice / Noul / Score 与结构化概率，不存在需要展示给用户的自由文本推理链。前端不得把模型内部推理、事后故事或 LLM 解释冒充“JEV 决策链”。

JEV Decision Trace 的最小可见字段：

- `purpose` / 消费者；
- 调用时点与可关联业务对象（存在稳定 ID 时才关联）；
- question type 与**固定 rubric/criteria 的标识**，不保存用户私人正文；
- Choice / Noul / Score 的结构化输出、probabilities / confidence（仅 API 真有该字段时）；
- JEV model/version、latency、status（ok / skipped / failed）；
- fallback reason / fallback target；
- downstream consumer 是否真正采用；未来存在独立 outcome/human label 时可并排展示，但不得回写当时 trace。

### 32.3 前端落点

不新增顶层 `/jev` 应用。第一入口复用现有 `/agent` 受控控制台，新增明确命名的 **JEV** 页签，先展示健康/版本/模式、历史 usage、运行期结构化 Decision Trace 与降级状态。

业务页面采用“有事实才出现”的轻嵌入：

- 猎场：只有该机会/研究记录确实存在 JEV trace 时显示 JEV 标记/展开项；当前生产选股若 JEV 未参与，禁止显示“JEV 为什么选它”。
- 复盘：有可关联 trace 时并排“当时 JEV 输出”和后来的 outcome/human label。
- 个股详情：只有形成稳定、可追溯的个股 JEV trace 后再接时间线；不为占位新增空 Tab。

### 32.4 降级矩阵

JEV 永远是增强层。消费者必须各自拥有无 JEV 的基线，而不是依赖一个万能 fallback：

| JEV 消费者 | JEV 不可用/未配置/超时/非法输出时 |
|---|---|
| alert triage | 继续现有 DeepSeek；再失败按既有规则提醒并显式标 fallback |
| event llm_aux | 继续现有 DeepSeek/下一批重试语义；不得写半批伪结果 |
| assistant tool router | fail-open 到原完整 TOOL_SPECS / 现有 LLM 工具选择 |
| semantic verifier | 保留 deterministic grounding 与既有回答；JEV verifier 记 skipped/failed |
| quant/research shadow | 记 unavailable/unknown，不生成标签，不阻断生产链 |
| Codex JEV review/router | 退回 tests/static analysis/现有 Codex 工作流；JEV 缺席本身不是缺陷 |

低置信的处理继续由各 owner 的校准策略决定；不得新造全局“低于 X 一律怎样”的万能阈值。

### 32.5 Trace 存储纪律

当前 `data/jev/usage.jsonl` 的 metadata-only 设计保留，不能为了看起来“可解释”就把用户问题、持仓、内部证据包或完整 prompt/output 全量落盘。

第一实施片优先复用已有状态、usage、业务记录，并允许增加**运行期、无正文的结构化 trace ring buffer**供网页与 Codex 同源读取；它只保存 purpose / status / model / typed answer summary / latency / fallback reason，不保存 `state`、用户正文或 evidence 正文，进程重启后可丢失。只有未来某个业务闭环确实需要跨重启回放，并已有稳定业务 ID/消费者时，才为该 owner 增加最小持久 trace；不得预先建设万能 JEV 日志表。

### 32.6 Codex 同源观察

Codex 不需要第二套项目 JEV 账本。项目侧状态/trace 通过与网页相同的只读后端契约读取；Codex 全局 TypeSafe Skill、evaluate MCP、jev-review/guard/context/browser 继续按 §2/§25 的调用门槛工作。插件本身的临时本机输出不冒充项目运行事实。

### 32.7 社区与插件取舍

2026-09-23 再看 awesome-jev-projects，值得吸收的是“决策工作台/决策日志/校准与漂移观察”这类**模式**，它们加强了本节的版本、trace 与校准方向；没有证据要求把外部项目整包引入。当前 Codex 已有 JEV Orchestrator、官方 Skill、MCP、review/guard/context/browser 等能力，**暂不新造通用 JEV 插件**。只有出现当前栈解决不了、跨项目重复发生且有可测收益的问题时再开 LAB；目录更新继续由 innovation radar WATCH，不以项目数量或热度自动生成开发任务。

### 32.8 工程与效果分离

- 工程可观测性与真实降级承接：W05/IMP-055 + W08/GOV-024。
- JEV 准确率、threshold、cascade、额度节省：仍只由 W04/RSH-030 的独立 human gold / A/B 证明。
- 猎场/策略效果：仍走 RSH-026/031、IMP-020/049/053 等原 owner；JEV trace 的存在不构成选股增益证据。
