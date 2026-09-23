# TypeSafe Jev 全局与 AShare AI Trader 集成蓝图

> **定位**：Jev 在本项目与 Codex 全局工作流中的唯一现役设计/运行说明。
> **状态**：2026-09-24 用户批准全系统应用方案，见附录 A；既有 §1–§31 保留历史设计与已实现事实，和附录 A 冲突的未来取舍以后者为准。项目内既有 Jev 主线已通过 PR #31–#34 分批合入 `master`；任务状态只在所属 stage。
> **账本映射**：见附录 A §14 与各原 owner stage；治理/额度/回退 → GOV-024，雷达 → GOV-025，生命周期 → GOV-027，事件/猎场 → IMP-048/049，语义核验/路由 → IMP-045/046，人工 gold 与研究 → RSH-030/031。本文只定义设计与证据，不复制任务状态。
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

## 附录 A. 2026-09-24 已批准的全系统应用、可视化与维护方案（v0.4）

> 原方案依据 `master@52971cfcc345cf2d9b3a172352a9c8ec69dcd0fa`；用户于 2026-09-24 回复“通过”。审查与 31 项未完成任务对照见 [时点证据](../review/jev-system-scan-20260924.md)。先前回退的 §32 起策略、PR #109/#110 和撤销编号不因本附录恢复。所有未来实现仍需按 stage 门序、独立证据和发布流程执行。

### 1. 总体判断：能力可以深入，但每个判断必须有人使用

**可以实现猎场中的 Jev 部分。** 已有统一 adapter、事件/提醒实际消费者、研究情境判断、机会快照与结果记录，可以在原模块内逐片实现。需要补齐的是开放情境与证据契约、语义结果关联、失效更新、真实消费者及降级，而不是另造猎场或统一评分引擎。工程可实现不等于已经证明选股增益。

建议形成三类使用方式：

1. **业务判断**：事件修订、公司关联、证据支持、反证、理由质量、知识适用、通知新增价值。
2. **研究与治理辅助**：历史文本特征、复盘问题归类、人工抽样、版本变更影响、账本缺口候选；都保留独立证据和原 owner。
3. **工程辅助**：有歧义的能力选择、批量标签、语义代码反馈、UI 明确候选比较；简单精确任务继续用代码或直接判断。

共同流程：已有事实与候选 → 有限语义问题 → 类型化结果 → 确定性组合/权限/预算 → 消费或弃权 → 结果反馈。复杂方案与代码由生成模型承担；价格计算、有效期计算、撮合、风控、数据库约束、权限和发布门禁仍由代码承担。官方明确披露 Jev 在精确数值、日期比较、多层推理和无关长上下文上的局限，设计据此拆小问题。[Jev 能力边界](https://docs.typesafe.ai/model-jaggedness/jev-1.13)

基于全链路比较，而非按用户举例逐项加功能，**首个业务试点建议聚焦“已有事件的新增/修订是否改变某个股机会的理由”。** 它能贯通事件、猎场、提醒和复盘，且有清楚的反例与人工验收；先用冻结样本验证，再到门接线。其余方向分批择优，不同时开启全部实验。视觉展示只用于让用户理解实际变化或让维护者定位问题；后台批处理/归并等有价值能力无需新增可视化。

### 2. 现状、可复用基础与真实缺口

| 对象 | 当前已核事实 | 新方案需要完成的部分 |
|---|---|---|
| `core/jev_client.py` | 开关、密钥缺失、输入审查、长度/预算、返回校验、有限重试、usage；429/503/529 可重试 | 消费者共享总时限、细分失败、模型实测身份、关联版本与有限恢复；不能假称已有完整熔断 |
| `events/llm_aux.py` | Jev 失败仍走现有 DeepSeek；生成模型整批失败时不标已判，留待重试 | 原文修订/冲突和可知时点证据，不能故障后伪填 neutral |
| `services/alert_triage.py` | shadow/cascade；原生成模型不可用则保留规则提醒；当前 shadow 也有等待 Jev 的路径 | 防可选 shadow 拖延通知；建议/裁决/入队/送达分别关联 |
| `assistant/jev_tool_router.py`、助手路由 | 受限只读工具组，失败回完整原授权工具；shadow 异步路径已存在 | 必要工具覆盖、实际执行反馈、核验覆盖和降级原因 |
| `core/semantic_verify.py` | supported / contradicted / insufficient；针对给定证据 | 源可信度、召回完整性仍需其他证据；失败显示未验证 |
| `research/jev_shadow.py` | 有单选情境、受限战法、复盘失败类别；本轮 app/scripts 调用检索未见生产消费者，测试可见 | 作为研究种子；改成符合猎场开放多轴/多假设的契约后才能接线 |
| `picks/intraday_opportunity.py` | distinctiveness、certainty 等现有规则/数值逻辑，assemble/participants/风险字段 | 保留这些分数原义；另表达语义理由维度，不重命名为 Jev 置信度 |
| `models/opportunity_learning.py` | append-only 阶段快照、run（含零结果）、独立 outcome/revision、KB 引用 | 复用事实身份；现表不等于完整 opportunity episode/version 生命周期，必要契约迁移由 IMP-049 拥有 |
| 猎场页面和详情组件 | 原卡片、分区、跟踪/复盘入口可扩展 | 沿现有 UI 展示证据、等待项、变化和失败，不新建每战法一个页面 |
| `backend/scripts/repo_watch.py` | 已有已采纳仓库增量跟踪与报告，无自动安装 | 加 Jev 来源须经原 owner；新案例发现与已采纳依赖跟踪分开处理 |
| Codex 本机插件 | review 0.1.1、guard 0.3.1 源码可查；router 元数据回执存在 | 安装、加载、调用、生效分别核；不能宣称所有 Codex 行为已被观察 |

此前同日只读 usage 快照是 464 条本地记录（461 成功、3 失败），其中提醒 435、事件 26、助手 2、smoke 1；它不是每次 HTTP 尝试或全局 Codex 总量。本轮没有额外推理调用。阶段页最近的人工 gold 状态仍记 0/240，本轮未重新读取私人标签，不能声称标注已完成。

2026-09-23 22:57 北京时间，运行选择器仍返回 **G1 / IMP-048**。本方案的价值排序不改变当前施工门序。

### 3. 全项目扫描、应用地图与价值裁定

结构索引覆盖277个后端Python文件、188处路由声明、30处后台注册、133个前端源文件，另94个脚本/迁移/工作流文件；46项现役任务中31项未完成逐项对照。完整数据链、范围/限制见 [全项目扫描附件](../review/jev-system-scan-20260924.md#1-全项目结构与数据链)，原始时点索引保留在获批前本机 `artifacts/radar/jev-visualization-20260923/project-scan-inventory.json`；长期判断以本附件和源码复核为准，任务分析见 [账本对照附件](../review/jev-system-scan-20260924.md#2-全部未完成任务逐项对照)。索引覆盖不等于每个函数逐行审计、全部接口运行通过或已证明模型收益。

评价顺序：先看真实消费者和已有实现，再看最小确定性修补能否解决；只有仍有语义歧义且可独立标注/比较，才保留Jev候选。收益需覆盖模型调用、fallback、人工、延迟、维护和失败代价；未知写未知，不用主观总分或Stars装作净收益。

#### 3.1 全功能应用/不应用裁定

| 功能及源码落点 | 候选与取舍 | 价值验证 / 降级 / 是否需要视觉 |
|---|---|---|
| 多源行情、盘口逐笔、K线分时；data_providers、market/normalizer/tdx_*、quote_enrich | 不接：可用性、价格单位、身份与故障主备都有代码判据 | 原质量/来源/时点显示足够，不加模型故障面 |
| 数据健康、哨兵、trade_calendar、marketdb_quality | 不接实时判定；新供应商文字口径变动条件试验 | 看人工发现错误字段的增量；无 Jev 回官方文档+实抓，不影响数据红线 |
| 数据存储、迁移、snapshot、Parquet、DuckDB、SQLite | 不接：一致性、恢复与写者归属要机械证明 | 保留恢复测试，无需 Jev 图 |
| 搜索、自选股/分组；watchlist与search-box | 精确搜索/增删不接；自然语言筛选仅在明确需求时沿助手候选条件 | 条件回显供用户编辑；误解析率高则退普通搜索，不另建入口 |
| 公司资料、财务、公告、news_digest | 保留：将摘要中的重要度/方向等窄标签与自由摘要分开比较；核验业务/主体/期间与原文关系 | 同质量少生成成本或少错引用；失败现有规则/LLM摘要。数字计算和财报修订事实不替换 |
| article正文获取、flash游标、新闻水位 | 网络取文/分页不接；跨转述来源谱系与实质重复候选保留 | 不同事实被错并是关键错误；pending回原事实，不删除材料 |
| extract分类/确定性、llm_aux未判批次 | 保留已有接线，扩展必要题材候选与支持关系 | 独立人工类别/方向/可操作性样本；失败原LLM，再失败未判 |
| EventCard修订/否认/撤回；store/impact/ranking | 强候选：新材料是否改变旧事实，作为机会理由重评输入 | 关键更正召回、错误失效、用户核查时间；候选不直接覆写事实ID |
| 事件verify与市场发酵 | 条件试验：同义题材/原因关联，事实仍由当时行情支撑 | 不能用“语义相关+随后涨了”证明因果；失败原匹配或未知 |
| 产业/政策传导chains | 保留有限相邻证据边的支持/限制判断 | 每条边有出处，复杂多跳交生成模型+人；没有证据不补传导图 |
| 官方目录、别名、主题成分；theme_service/official_match | 条件试验：只处理别名字典未覆盖且已有合理候选的关系 | 现有成分重叠已解决部分别名问题；先测剩余错误，不能重做或改官方成员 |
| 题材梯队/断板/热度/竞价/龙虎榜 | 大部分不接；涨停原因多主题语义核对与上述共享 | 连板、封板时间、席位净额、热度不交 Jev；观点不冒充资金真实意图 |
| 板块异动；board_surge.match_news_events | 保留：在原事件候选中核对是否确有关联/纯同词 | 当前互含匹配+固定recency是基线；先修/测排序语义，Jev不承担时间排序。标关联材料，不称因果归因 |
| 情绪周期、宽度、资金流、云图 | 不接现有数值状态；市场叙事与数值矛盾只在报告核验中复用 | 状态计算/配色/面积可复算；不另加“Jev市场情绪”双源 |
| ENSO、商品链、隔夜偏向、宏观日历 | 文本适用限制按需核验；不新增 Jev 涨跌预测 | 当前气候链未获支持、商品隔夜权重归零等历史证据保留；模型常识不能复活 |
| 晨报与次日计划；morning_brief | 先修真实消费链；之后可核“计划引用了什么、是否被新材料推翻” | 当前已见不存在模块的惰性导入+吞错路径，归BUG-009；不让生成模型填貌似完整计划 |
| 每日精选候选/五维分；picks_pipeline/engine | 原召回和算分保持；理由质量/业务关联作研究特征 | 比同池增量与未选分母；不能用“更多理由”直接抬分 |
| 潜伏/RPS/接力/临板/三倍量/筹码/风格路由 | 数值资格不接；情境含义与适用反例并入同一猎场证据能力 | 不再为各战法各造一个Jev模型；限定有效域，效果失败回原策略 |
| 盘中机会、跟踪、时机与失效；runtime/watcher/buy_point | 强候选：多假设语义证据、理由变化与缺证更新 | 版本前后可核，原规则等待/触发不受未验证语义影响 |
| 盘中分时T信号、minute_decisions | 信号/30分钟结果/执行关联不接；错误解释复用复盘归类 | 候选分类不是自动“做T”动作；无需每个信号再模型调用 |
| 风险、position_engine/exit_engine、入场清单 | 仓位/止盈止损/T+1/停牌硬门不接；非结构化持仓理由核验仅未来有授权公开材料试点 | 不外发账户细节；风险提醒照常；不能因为语义信心高解除拦截 |
| 手工真实记账、模拟订单、撤单/重置、对账 | 不接执行或记账；拒绝状态用已有代码说明 | 精度、幂等、可恢复胜过概率判断；不造“智能审批” |
| 提醒噪声、升级与变化；alert_triage | 保留已有consumer；只评估残余语义噪声和新变化 | 关键漏报与通知时限硬约束；故障保留规则通知 |
| 渠道偏好、outbox/read-state、通知落点 | 不接机械通知状态；文案语义检查可离线抽样 | Jev不能证明送达或已读，状态本身由回执决定 |
| 日报/盘后/逐股复盘；review/daily_review/review_intraday | 保留多因候选、正反过程、叙述与事实一致核验 | 未选/未成交/弃权都保留；回规则报告/人工，不伪造因果 |
| 重复改进项；evolution._repeat_pending_items | 保留：标题不同但同缺陷的候选归并、相似标题不同目标的反例 | 现按category+title前60字聚合；先确定性身份，再语义补漏，原ID/证据/状态不合并抹除 |
| methodology/meta_review有效性 | Jev可判提案与证据是否相关；首先修“有判断就effective”的口径风险 | 改善必须链接实施/独立结果，模型自评不能替换原错误代理；后台报告即可 |
| KB正文/检索/失效/引用；kb_routing、agent kb | 保留：适用性、支持/反证/引用覆盖；按既有任务验证 | 有无KB消融，实际引用不等有效，原始检索失败可完整回退 |
| 预测历史库predict | 不新建功能：REST已退出，仅复盘auto-verify等残余消费者 | 如语义核验需要，复用报告层；不因存在旧表重启新预测平台 |
| 因子新颖性/策略/回测/grid/OOS | Jev仅文字假设归类与特征生成；因子结构/相关性/统计检验继续代码 | 假设变多会加剧多重试验，必须预算+预注册；没有增益就停止 |
| 研究晋级/实验/回滚；strategy_readiness、agent_params | 不接批准与自动执行；证据叙述一致性仅附加提示 | hash/owner/实际fill/审批凭证均原硬门，模型不能改结果 |
| 助手选工具/参数/引用；registry/dispatch/verifier | 保留现有组路由+引用核验；有限缺证查询候选 | 必要工具召回/事实支持/真实调用分开；失败原授权工具集合 |
| 助手能力误否认；cognition→evolution | 新保留候选：识别“有获准工具却未正确使用”和确实无权/源失败 | 当前有任意工具调用就跳过该规则；先测结构化required-vs-used改进，Jev仅残余文本歧义，避免重试打转 |
| 账户工具与本地assistant会话 | 不新增外发；可用性由权限/工具事实判，私有会话持久化单独决定 | 不以Jev追踪名义上传历史或账户资料 |
| agent任务中心/agenda/C提案 | 重复建议、范围/证据不匹配提示可复用；生成与执行仍原机制 | 业务action items≠工程stage任务，不自动关单/写账本/改码 |
| 系统健康、性能、后台资源竞争/WS慢消费者 | 不接实时调度/性能控制；海量重复日志语义归簇仅未来有痛点 | 先结构化错误码和实际profile，不能用模型路由代替隔离/锁/并发治理 |
| 前端布局、状态文案、空/错/取消、动效/Canvas | Jev仅重复性明确候选的语义核对；视效、键盘、焦点、滚动/图表不接 | 原design-taste/真实交互是基线；无节时则不调用 |
| 工程账本/需求对账、重大决策传播 | 仅模糊重复/语义验收缺口候选；阶段选择与状态代码核 | 建议归原owner待批准，不能替代当前任务、优先级或阶段 |
| CI、文档链接、公开仓扫描、临时资产清理 | 不接门禁/删除裁决；未知失败文本聚类可按需辅助 | 精确SHA、哈希、权限、恢复必须代码；低调用资产不自动删 |
| Codex能力/代码反馈/大批标签 | 按已装现役工具有限使用，源码搜索仍ask-only | 端到端质量成本和实际采纳可核；不要求用户看每次调用图 |
| 外部雷达、插件/模型更新 | 保留增量来源→少量候选→原证据→实验→有权批准 | 更新/故障不依赖Jev自己，失败pending，不自动升级生产 |

#### 3.2 这次扫描新增的实质判断

1. **板块异动的消息关联值得试，但要先区分语义和时间问题。** `board_surge.match_news_events()` 当前用题材名互含，且名为recency的乘子实际固定为1.0。代码可改善时间排序；Jev只比较候选事件与主题是否确有关联，不能拿模型解决排序公式问题，也不能把关联称成已证实原因。
2. **主题别名不是空白模块。** `official_match` 已用成分重叠挂靠，`mine_theme_linkage` 已复用这一机制。新方案只试现有方法未覆盖的语义关系，不做重复“智能题材映射”；官方成员身份不能由Jev发明。
3. **新闻摘要内存在可拆的窄任务。** `news/llm.py` 同时让生成模型写摘要、给重要度/方向等标签。可比较规则底稿+Jev窄标签+确有必要才生成摘要，但必须证明完整任务节省，不能因为调用单价低就拆成更多调用。
4. **复盘重复问题归并有真实消费者。** `evolution._repeat_pending_items()` 用category和title前60字聚合；Jev可以在候选对中找同义问题并保留各原ID与状态。应先复用结构化target/来源消除容易重复；剩余歧义不足就不接模型。
5. **助手能力误否认值得独立评价。** `cognition.looks_like_false_denial()` 在有任意工具调用时直接跳过，可能遗漏“调用了无关工具仍否认能力”。先以工具声明/权限/调用结果确定事实，再用Jev判断残余表述歧义；不能把真实无权限误当缺陷而反复重试。
6. **复盘有效性代理需要先纠偏。** `review/methodology.build_meta_insights()` 把有judgements记effective，这是产出代理而非净收益证据。Jev可检查建议是否有依据，但也不能作为新的效果真值；实际修复归原复盘/治理owner，当前只登记审查发现，不改代码或stage。
7. **晨报缺口不能被漂亮内容掩盖。** `_daily_plan()` 中旧惰性导入与吞异常仍可静态观察，已有BUG-009承接。先修真实消费，不让模型凭空补计划。
8. **有明确否证的方向不重启。** 气候/商品传导的现役源码保留未获支持或隔夜失效证据；Jev语义常识不能赋予这些链新权重。预测旧REST已退出，也不因找到旧表就另建Jev预测页。

以上是源码层发现与候选，未重演生产事故、未估造发生频次。涉及缺陷的修复需沿原账本派工；它们用于判断“应先修什么、Jev能改变哪一小步”。

#### 3.3 哪些投入目前有依据

**保留深化设计**：事件修订/引用支持、机会理由变化、提醒残余语义、助手引用与能力覆盖、复盘同义问题归并。这些有真实消费者、可见baseline和独立人工判据，最容易把收益与失败说清楚。

**条件实验**：理由质量转选股特征、知识重排、题材别名、摘要标签分工、数据口径语义、UI/账本辅助。先量剩余错误、批量规模和核查成本；规则或直接推理足够时不接Jev。

**不接/退出候选**：精准数值、行情状态、资金/风控/撮合/权限/调度、自动批准/发布、模型自己证明效果、每模块一个插件。它们缺少必要语义空间或会放大故障与治理成本。

这三类是设计价值结论，不是P0/P1/P2或新执行阶段；2026-09-24 已按原阶段门和既有任务优先级登记相容子范围。新能力的实际质量、选股增益和节省仍待实验，本轮不能把“值得验证”冒充“收益已实现”。

#### 3.4 保留候选如何证明“值得做”

这些是候选的验证卡，不是待办列表。先测真实错误/频率和人工时间，若基线没有痛点则不启动外呼试验。下表均沿§13预算/期限与§10降级；新部署或影响业务输出必须有更高层准入证据。

| 候选 / 消费者 | 必须先比较的便宜基线 | 主要价值假设与验收 | 否证/停止条件 |
|---|---|---|---|
| 修订/反证→事件、猎场、提醒 | ID/摘要hash/时间/同源转引+已有关键词；补原文召回 | 独立人工判关键更正召回、误失效；同一判读被多个消费者正确采用 | 原文/候选缺失是主因；改善召回已解决；关键更正漏检或错误失效增多 |
| claim↔证据→助手/摘要/复盘 | 现有数字/引用ID/时点检查；直接提取原文 | 降低无来源/过度推断，同时保留必要覆盖；少人工定位时间 | 只是修正文风；召回不足被误判反证；高置信仍大量错核 |
| 未识别主题/板块消息关联→盘面/猎场 | 现别名、成分重叠、时间排序修补 | 同人工关系标签提高精确率/召回；不破坏原ID与官方成员 | 无剩余错误；新模型只复述词面；引入主题误合并 |
| 重复建议与问题簇→复盘/agenda | target+证据ID+规范化title+错误码 | 更少重复人工处置、真实不同问题不被吞并 | 每周只有少量条目无需模型；错误合并或隐去关键问题；不得因聚类而自动抑制P0提醒 |
| 能力误否认→助手/维护 | required-tool覆盖、权限、实际返回码；现关键词规则 | 在确有可用工具的独立样本中发现漏调用/误否认，少错误指控 | 无权/源失败被当能力缺口；重试循环/隐私外发；规则修补已够 |
| 窄标签分工→新闻摘要 | 当前规则底稿+整批LLM；缓存/合批先优化 | 相同完整摘要质量/覆盖下，端到端费用或时间下降 | 拆成更多调用、fallback高导致更贵更慢，关键标签退化 |
| 理由质量/业务暴露→研究筛选 | 现数值/规则/文本检索特征，无Jev champion | 时间外消融显示净增量、成本可承担，机会/实际fill分母正确 | 只有故事更好、没有OOS增量；泄漏/同事件重复导致假收益；退回仅解释或退出 |
| 轻量编排→三类真实助手任务 | 现有工具与编排；先改善原召回/提示 | 相比重框架候选同任务质量不退化，维护/调用/延迟净成本更低 | 必要能力无法完成；只省输入却增人工；两天候选预算无法证明则报告未定，不假定轻量必胜 |

收益类型明确分开：纠错与可靠性、用户核查成本、工程维护成本、模型费用、研究增量。一个候选只需证明真实必要的一项，但不能牺牲关键质量换另一项。后端批标签/重复问题归并默认只产机器可读结果与必要报告，无用户消费需求就不建图。

### 4. 猎场怎么实现：从可查理由走向有证据的筛选增量

#### 4.1 一条最小纵向链路

已有事件/公告与历史可见原文 → 代码绑定公司、主题、时点与引用候选 → Jev 判“新增是否改变理由、引用是否支持、有什么反证” → 代码生成或关联机会版本 → 猎场详情显示 → 必要变化通知 → 后续 outcome/实际 shadow fill 分别复盘。

每次只围绕一个业务对象和一个主问题，先离线冻结样本。Jev 返回候选/证据关系后，业务代码决定是否采用；旧版本 append-only，不覆盖历史解释。不把 GET 页面读取变成推理触发器，不对每个 tick 重问模型。未验证的新语义部分在 shadow 保留，对外仍使用原已获准结果。

工程入口建议：`core/jev_client.py` 复用调用；`events/llm_aux.py` 与事件事实契约供证据；`research/jev_shadow.py` 承载离线窄问题验证；`picks/intraday_opportunity.py`/runtime 和 learning 服务消费获准语义记录；`models/opportunity_learning.py` 关联快照与后续结果；前端 `hunting/page.tsx`、`components/hunting/intraday-sections.tsx` 和详情呈现。实际文件拆分以冻结切片为准，不承诺所有改动都在这些文件。

#### 4.2 情境要开放组合

驱动（公告/行业/政策/纯盘面/未知）、结构（启动/加速/分歧/修复等候选）、角色（领涨/跟随/补涨等假设）、环境、时点、执行条件分别保留；来源明确的数值结构由代码算，Jev 只补语义歧义。多种假设可以共存，缺证时保留 unknown。现有研究函数的单选 archetype 不能直接成为唯一猎场分类器。

战法选择只在已有准入候选间辅助匹配；没有候选就无匹配，只有一个可用候选直接判断，不为调用创造竞争。任何 Jev 建议都不能把观察对象直接转为可执行对象。

#### 4.3 个股“原因评分”的具体设计

建议先显示**理由质量分项及依据**，再决定是否值得研究成选股特征。以下是待人工标注验证的量表，首片只选最有用的 1–2 项，避免一次堆满。

| 维度 | 拟议输出与锚点 | 用途与边界 |
|---|---|---|
| 引用支持 | supported / contradicted / insufficient；引用原文 ID/片段 | 引用是否说了这件事，不证明原文真实或未来涨幅 |
| 业务直接性 | 0 明确不适用；1 只有题材相似；2 间接关联有资料；3 直接业务关联但关键范围缺失；4 明确业务关系且范围有原文 | 帮人区分公司关系的距离；未知单独记 null，不当 0 |
| 增量信息 | 重复 / 增量支持 / 增量削弱 / 实质修订 / 不明确 | 新鲜内容与发布时间新是两回事；媒体转载不重复加分 |
| 落地阶段 | 传闻 / 意向 / 已公布 / 已确认实施 / 结果披露 / 不明确 | 类别本身不等于大小或收益，阶段顺序不直接转权重 |
| 反证与缺口 | 明确矛盾 / 关键缺证 / 暂未发现；指到证据 | “暂未发现”不能叫“无风险”；矛盾不能被高分平均掉 |
| 时效、来源与市场响应 | 时间/交易日/源版本、独立来源去重、量价等由代码算 | 与语义分并列，旧数据/不可执行不能靠理由高分抵消 |

Score 的等级是有锚点的语义评价；模型分布和 confidence 只作内部校准，前台先显示等级及证据，避免 3.87/4 或 92.6 分的虚假精确。官方组合评分模式可供拆维度参考，但它没有给 A 股权重或收益保证。[TypeSafe 组合评分](https://docs.typesafe.ai/patterns/composite-scoring)

**进入个股选择有三级：**

1. 解释：展示依据、反证和未知，帮助用户人工核对；与现有数值评分分开。
2. 研究：新增独立 shadow 特征，检验同一候选宇宙中的排序/覆盖变化，不改变生产排序、默认筛选或通知抑制。
3. 准入：时间外、消融、成本与实际执行证据达标，经现有审批后才进入已有评分体系；保留无 Jev champion 和可回退特征开关。

主观“原因好”与未来收益间可能没有增量关系；若只有解释价值，就停在第一级。禁止把人工愿意看、模型信心高、理由多或当日上涨当作策略收益已证实。

### 5. 决策是否过时：拆成四个可操作问题

| 过时类型 | 谁判断 | 触发与处置 |
|---|---|---|
| 行情/有效窗口过期 | 代码：交易日、时间、质量、价格/状态规则 | 立即标 stale/expired 或重检，不等待 Jev |
| 理由被新事实改变 | Jev 只判断新旧已提供材料的关系，复杂推理升级 | 否认、修订、政策阶段变更、公司业务变化等触发；先标待重评，相关增强不沿用旧结论 |
| 策略/知识不再适用 | 统计与 owner 生命周期规则为主；Jev 提反例/适用域变化候选 | 独立样本退化、成本上升、市场域改变；隔离或降权按既有准入，不能凭一次语义分退役 |
| 规划/工程决定过时 | 版本、依赖、代码状态比对；Jev 对文本冲突作提示 | 上游弃用、已有实现取代、任务前提改变；原 owner 复核，不自动改账本 |

维护每项既有机制的 basis/version、as_of/available_at、适用域、复核日期、反证触发、退出与重开条件，留在原登记册。对象到期与复核日期由代码比较，不委托模型算日期。

新事件只重评引用了相关证据的活跃对象：先靠证据 ID/公司/主题依赖找到集合，再合并短窗变化、去重、设并发和对象上限；不能全市场反复全量判读。还应抽查依赖映射漏掉的对象，防止只有“已经知道有关”的关系才会更新。

新证据到来后，旧评价保留为历史；界面标“新增材料待核”“此前理由被修订”，并链接版本差异。迟到结果若 basis 已变，仅作旧版本记录，不能覆盖当前机会。语义重评失败时保留原始材料和待处理状态，不继续给“仍然有效”的绿标。

### 6. 复盘、知识与未来研究

#### 6.1 复盘应发现流程缺陷

数值事实由代码提供：候选何时出现、当时知道什么、被过滤原因、是否通知、是否可执行、是否实际成交、合法退出及成本。Jev 在这些事实和原文上做有限问题归类：理由证据不足、事件传导假设被挑战、环境不适用、退出依据不一致等。

保留多种可能因素与未解释项。现有 `classify_review_failure` 的单一主类别只适合初筛，不构成因果诊断。盈利也检查坏过程，亏损也可能是合规执行的自然结果；必须包含未选中、未通知、未成交、弃权和过期对象。参考轨收益不冒充可实现收益，实际成交只消费 IMP-053 执行域事实。

可视化采用“决策时证据→后续变化→动作事实→结果→问题候选”时间线。盘后资料只进结果/修订轨，不倒写决策输入。不自动根据复盘叙述调参数、改代码或生成已应用的 KB 规则。

#### 6.2 KB 的深用与退出

Jev 辅助挑选适用条目、定位矛盾与过时建议，系统记录实际引用和拒绝原因。复盘发现重复问题先关联已有 KB，不批量创造同义知识。新结论必须保留失败/适用域/证据版本；只有被真实消费者采用且有增量才保留。被频繁引用不代表有效。

#### 6.3 语义特征发现

可以研究“具体业务暴露、催化成熟度、限制条款、修订方向”等文本特征，生成模型提出候选问题、Jev 作有限判断、代码构建特征，研究工具评价。官方有类似文本特征发现范式，但其演示领域不能外推股票效果。[官方特征发现示例](https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery)

冻结研究集、验证集和最后留出集；控制候选问题数量与反复试验，记录失败臂，不用最后留出集继续挑问题。按时间前推、同事件去重、历史可见文本、制度/行业分层检验；避免只挑涨停赢家、只收录存续股票和盘后解释。Jev 不接未来 outcome 输入，预测标签不混入独立人工 gold。

### 7. UI 选型：可以辅助，何时值得用

设计主线遵循现役 design-taste 与 IMP-050：紧凑金融工作台、红涨绿跌、真实状态、上下文连续性。Jev 对已有文字/DOM 语义和候选说明作有限比较；当前所核 Jev 是文本模型，不能代替截图审美、视觉层级或 Canvas 验收。

| 有价值的问题 | 候选与输入 | 谁作最终判断 |
|---|---|---|
| 理由详情放何处 | inline / Drawer / Modal；任务是否需要持续看列表、是否需要大图、移动端约束 | 简单情形按设计规则；真正模糊且重复的多候选才用 Jev 辅助 |
| 同时有很多信息，哪些先展示 | 用户本次任务、已有字段、必须显示的时间/来源/失效；有限布局候选 | 设计者与用户任务测试；不能把硬必显项按分数隐藏 |
| 状态文字是否误导 | stale / pending / unavailable / rejected / no_fill 文案候选及实际状态 | Jev 提语义不匹配；状态覆盖和真实行为由测试验证 |
| 需求是否漏状态 | 需求片段与有限状态矩阵 | Jev 提遗漏候选；DOM/键盘/焦点/滚动/取消/响应式实测验收 |
| UI 技术库选型 | 当前依赖、许可证、体积、维护、可访问性、适配成本与实测 | 事实收集/性能测试优先；Jev 只帮归类对需求的匹配 |

一次性选两个组件，直接分析通常足够，不需要增加模型层。禁止让 Jev 每次打开页面重新决定导航、主题、组件布局或不可见地改变用户偏好。若没有可比较的返工减少/漏状态改善，就保留现有设计流程。

### 8. 账本与 Codex：减少重复核对，保留权责和事实

#### 8.1 后续账本能得到什么帮助

先由 `ledger-runtime-selection.py`、doc-health、Git 和阶段页得到合法候选与版本。再针对大的歧义集合做有界提示：任务可能重复、验收可能遗漏、实现已覆盖旧提案、依赖语义可能冲突、旧结论可能被新提交推翻。

输出格式是“原任务 ID / 原文位置 / 候选问题 / 新证据 / 建议 owner”，不是自动产生新权威状态。精确编号、阶段排序、状态字段、依赖完成与否用代码核。即使 Jev 建议高优先级，也不能越过门序、批准自己的成果或自行关单。

重点价值是**在继续选刀前缩短查证时间，提前发现坏前提**。一次任务继续不默认消耗一次 Jev；仅有两项、规则明确或用户已指派时直接执行。没有发现质量/节时证据就退出该辅助。

#### 8.2 Codex 的使用怎样体现在可视化中

只显示实际观测到的工具调用：来源=Codex/MCP/CLI/插件；任务关联（宿主确实提供才记）、候选能力、模型建议、权限过滤、实际选择/工具结果、review 前后版本、fallback 与耗时/token。**调用记录≠宿主内部推理**，不能展示不存在的内部思维链或宣称覆盖所有会话。

业务侧显示“此理由由哪个已获准判读支持”；工程侧在现有报告/详情入口显示“Codex 本轮用了哪个 Jev 能力、改变了什么、哪项测试或人工结果验证它”。没有 session/object 关联就显式未关联，不按相近时间猜配。metadata-only 路由回执继续沿用，禁止恢复完整 request/context 日志。

社区 `jev-in-codex` 当前 README 把范围收敛到批量标签，并披露移除未表现出足够增益的能力；不能把目录摘要当成 Codex 全自动路由或上下文优化已成立。[项目原始说明](https://github.com/teempai/jev-in-codex)

### 9. 可视化：用同一业务身份看清采用与结果

#### 9.1 三层阅读

- **业务层**：猎场/个股/事件详情显示“为什么、证据是否新、反证、等什么、失效条件、当前是否只是研究”；默认用人能理解的状态，不铺一屏模型指标。
- **解释层**：展开原文、候选判断、规则处理、实际采用、通知/动作与后续结果；将“建议”与“执行事实”分列。
- **维护层**：既有报告或后台看质量、弃权/升级、版本、覆盖、延迟、费用、故障与回退；先单来源报告证明排错价值，再决定是否需要产品入口。

示例（仅设计示意，无真实个股结论）：某公告原文直接涉及候选公司业务；Jev 判为直接关联但金额范围缺证；规则行情已过期；卡片显示“理由待补证 / 行情待刷新”，不会因语义分高而显示可执行。新公告否认业务时生成后续版本，旧判断仍可追溯。

#### 9.2 最小契约与数据边界

复用既有业务/usage 记录，按需要增加关联字段：logical_decision_id、attempt_id、业务 ID/版本、basis digest、source refs、as_of/available_at、问题/候选/策略版本、requested/resolved model、typed answer、accepted/rejected/pending、fallback source/reason、latency/usage、outcome refs。外部插件字段拿不到就 unknown；不为填字段重跑模型。

不建第二套机会事实、任务总账、预算账或全局机制注册表；观测读取原 owner 的事实。跨域最少共用一个可版本化的观测/失败语义约定，运行状态各宿主管理，不强建全局网关。截图、全文和私有上下文不默认保存；原文指向受权限控制的已有存储。插件 hooks 采集范围须先证明宿主支持，不能承诺拦截所有工具。

新增非业务 telemetry 试点拟保留 30 天/100MB，上限先到先轮转，并记录丢弃数；业务审计、gold、预算事实和必要版本证据不随 telemetry 清除。重放存档判断与重新请求模型是两种操作，后者不是确定性重放。

### 10. Jev 不可用时的完整降级

#### 10.1 分类失败，避免“出错就换更贵模型”

| 类别 | 处理策略 |
|---|---|
| off / 未配置 | 直接用本模块原获准路径；显示未启用，不启动重试 |
| 输入不合法、候选缺失、隐私拒绝、权限不符 | 修输入/请求必要澄清或停该判断；不得换供应商绕开拒绝 |
| 预算不足/记账失败 | 停止受预算约束的外呼；不能换 LLM 或记 0 消耗继续 |
| 401/403、模型不存在、schema/协议漂移 | 停相应能力并提示维护，不盲目重试；未经验证不换 latest |
| 429/配额、5xx、网络/超时 | 在剩余总时限和预算内按状态有限重试；超过即走模块 fallback |
| 返回有效但低置信、冲突、域外、质量退化 | 弃权/人工或原获准升级；不得通过重复提问直到得到想要的答案 |
| 取消、迟到、basis 已变 | 不采用过期结果；记录真实结束与已发生消耗，不能假退款 |
| 观测输出失败 | 非关键 telemetry 可丢弃并计数；既有资源账/业务事务失败沿原硬规则 |

#### 10.2 各消费者的退路

| 消费者 | Jev 不可用后的行为 | 仍必须保留的约束 |
|---|---|---|
| 事件辅助 | 回现有 DeepSeek；也失败则 pending/明确未判并有限重试 | 不伪造 neutral/actionable，不覆盖原文，不把整批故障标成功 |
| 提醒 | 回现有 LLM；也失败走规则提醒并说明未做语义过滤 | 硬风险与必需通知不被模型故障抑制；身份去重/静默/权限沿原规则 |
| 猎场解释 | 显示原规则理由、已有证据与“语义未评估”；新证据待核 | 不用缺失分=0，不把历史分当本版本新判读 |
| 获准语义选股特征 | 切到预先验证的无 Jev 策略版本；无获准退路则暂停对应增强 | 不临时重配权重、不跳过撮合/风控、不把缺特征的策略仍称原策略 |
| 复盘/KB/离线标签 | 保存待处理批次和已有事实，可用人工核验 | 不自动写 gold、不补“正常”标签、不丢失败对象 |
| 助手工具路由 | 回原授权工具集合，由现有生成模型/明确规则选择 | 原权限与 schema 过滤不变，不暴露原本无权的工具 |
| 引用 verifier | 显示未验证/覆盖不足；依赖验证的高影响输出暂缓或人工核验 | 模型不可用不是 supported，源本身真实性另核 |
| Codex evaluate / capability router | 当前 Codex 基于已有工具、证据与授权直接处理窄任务 | 不自动切宿主模型/effort，不恢复每任务前置路由 |
| Codex jev-review | 明确记录“复评不可用”，继续能独立完成的测试和整改；提交人工独立审核 | 不伪造评分或静默豁免；是否可发布仍看原项目门禁，必要豁免必须来自有权 owner |
| jev-guard | 可选语义提示失效则记录缺口，宿主权限/沙箱仍生效；依赖额外批准的动作暂停或走真实人工批准 | 安装源码默认 fail-open 不等于安全放行；若现场 fail-closed 已阻断，不自动修改配置绕过 |
| jev-context / code-search | 回 rg/原检索与有界人工阅读，保留原候选和必要上下文 | ask-only 不变；过滤失败不能当无证据，不能静默截断关键内容 |
| jev-browser | 回正常 browser/computer-use，依据新 DOM/截图重新确认状态 | 不盲重放点击；上传/发送等动作身份需核，DONE 不当验收 |
| browser TYPE_TEXT bridge | 既有生成桥不可用时，由当前可用获准宿主产生文字并经正常浏览器输入；否则停在待输入 | 不扩权限、不绕 localhost-only、不改变禁工具和禁持久化 |
| 创新雷达/候选筛选 | 来源差异照常由代码采集；Jev 不可用时规则/人工初筛或 pending | 不因筛选器不可用丢所有候选，不靠 Jev 自批自己的升级 |

本机 guard 0.3.1 源码的默认错误行为是 fail-open，review 0.1.1 源码默认单请求 30 秒且最多重试 2 次；这些是源码默认，不是本轮实测生效配置。需要在实际宿主测试“断网/拒绝/超时/返回坏 schema”后才能承诺平滑降级。hook 阻断时不能保证当前 agent 总能继续；记录原因并保留用户明确可审的恢复入口。

#### 10.3 时限、熔断、缓存和恢复

- **总时限**：每个消费者先测基线并明确 deadline；Jev 尝试、等待、重试、生成模型升级共用剩余时间/预算，不能各自把超时叠加。交互路径达不到原时限就移为后台增强；后台队列独立并发/资源限额。
- **重试与熔断**：按 endpoint/credential scope/model/consumer 区分故障；无效输入不污染供应商可用性统计。尊重 Retry-After，但超过 deadline 就不等；指数退避加抖动，重试计费。拟议 E2 初值为连续 3 次基础设施失败后冷却 60 秒、半开只放 1 个探针；生产参数须按真实流量与配额核准，不能从默认数值宣称可靠。
- **缓存**：只有输入证据 digest、问题/候选/策略/模型与必要用户权限范围一致、且证据仍有效才可复用；过期缓存只供历史查看。负缓存用短期明确状态，不永久冻结候选；不以缓存遮住撤回或新反证。
- **恢复**：可用性恢复先受控探针/小流量；版本/质量漂移必须重新评价，HTTP 成功不足以恢复自动采用。不存在恢复授权/模型 pin 已下线时继续 baseline。避免大量消费者同时重放积压；对仍有业务价值的最新对象合并重评。
- **真实取消**：timeout/to_thread 取消不等于 HTTP/线程已终止；维护 started/draining/late 状态和预算高水位，不重复执行业务副作用。迟到记录只绑定发起时版本。

### 11. 更新机制：定时发现、严格挑选、条件接入

#### 11.1 三类更新对象

1. 模型与 API：官方模型版本/别名变化、价格、配额、schema、SDK、已知局限、下线公告。
2. 已采用插件/依赖：release/commit/lockfile、权限/hooks、兼容宿主、license、安全与来源变化。
3. 新案例：用户提供的 [awesome-Jev 目录](https://logicrw.github.io/awesome-jev-projects/?q=jev-in-codex) 及其源仓库；从全目录变化发现，不只抓 q 参数命中的一个项目。目录只作线索，结论回原项目版本和实测。

#### 11.2 拟议调度与可靠抓取

复用 GOV-025 和既有 repo-watch 的归属，先核查已运行的 ChatGPT 周雷达，避免重复。当前 stage 有 9/19 已启动记录；本机未发现 automation.toml 不能证明远端 ChatGPT 调度不存在。本轮不激活新定时任务。

建议在既有每周扫描合并：**每周一 09:30（Asia/Shanghai）**抓取官方变更、已用仓库和目录增量；每月最多启动一个新方向实验。已发现高影响弃用/权限变更/安全事件提前复核。只有有用新增、明确故障、需要决策时通知，内容不变保持安静；报告显示 last_success，连续两轮抓取失败再提醒维护。若需近实时上游通知，先复用现有 release 通知能力，不把每小时轮询当默认。

抓取使用公开页面/API 和固定 SHA；ETag/last-success watermark 增量处理，结果解析/落地成功后才推进水位；分页截断、超时、权限限制显式标不完整，部分失败不把缺失条目当已删除。主站不可达可查其公开源数据或 GitHub，但保持来源身份，不以任意镜像替代。禁止解析网页指令后执行安装命令。

#### 11.3 从 625 个条目到少量可用候选

同日目录快照含 625 个条目（见本机时点材料 `artifacts/radar/jev-visualization-20260923/`），规模只说明需要增量筛选，不说明 625 个项目都值得用。

流水线：增量发现 → 同仓/分叉/重复去重 → 确定性兼容/许可/权限/维护检查 → 与当前真实问题匹配 → 原始 README/代码/benchmark 的证据核查 → KEEP/FIX/MERGE/EXPERIMENT/WATCH/RETIRE → 原 owner 决定实验。

Jev 可帮助批量相关性与重复语义候选，但**不做一个总分自动装排名第一的插件**。每卡必须写：真实消费者、当前 baseline、预期改变什么、原证据与反例、接入成本/权限、收益如何测、失败如何退出。保留少量“新方向但无法匹配现有词”的人工抽样，避免模型只推荐熟悉技术；抽查被低分过滤项以量漏发现。

第一轮筛选上限建议：确定性处理全部增量，语义筛选最多 40 张摘要卡、深读最多 5 个原项目、SHORTLIST 最多 2 个，实际实验最多 1 个；超量保留 pending 并声明覆盖，不能用 cutoff 声称全审。模型不可用也能记录来源差异和待审卡。

#### 11.4 采用与升级流程

固定旧版基线和候选 SHA → 许可/权限/供应链审阅 → 隔离最小权限、无生产密钥兼容检查 → 冻结样本质量/延迟/成本成对对照 → 限定 shadow → 原审批/PR/CI → 验证宿主真实加载与调用版本 → 观察/回滚。

发现/生成候选报告可按已批准调度自动进行；在明确预算内的隔离实验需对应授权；生产安装、权限扩大、阈值或策略变更继续走原准入。可以自动形成可审补丁/升级建议，不能由应用自己改代码落地。用户先批准方向不会替代每个发布门。

**插件更新同时核四个版本**：上游可用、磁盘已安装、宿主已加载、实际调用观测。只看 package.json 或安装成功不够。备份已验证版本、配置和必要 schema；数据迁移若不向后兼容，先设计恢复，不能盲目 downgrade 二进制。

**模型更新同时固定**：resolved model、问题/rubric、候选集合、阈值、检索器、数据源与生成模型。生产 pin 必须核 API 接受与服务期限；requested=latest 且无真实返回版本则 resolved=unknown，不照抄别名。模型 alias 漂移先标质量未经验证；语义增强回基线或 shadow，不能继承旧阈值。官方目前不提供客户 Jev 权重微调；本系统可版本化问题/领域样本/组合逻辑，权重模型更新由供应商提供。[官方模型说明](https://docs.typesafe.ai/models)

旧模型下线且无合格新版本时，按 §10 退回无 Jev 路径；不能要求“为保持在线必须无条件升级”。API 连通、质量有效、效果增量是三项不同验收。

#### 11.5 本轮值得借鉴的外部实践

| 实践 | 可吸收的价值 | 不成立的推论 |
|---|---|---|
| teempai/jev-in-codex | 大批窄标签与删除无增益功能的纪律 | 项目名不证明全 Codex 路由都省钱；自报小样本不能当本机额度节省 |
| BillionsBobby/JevRouter | 模型候选与代码权限过滤分离、查看实际决策 | 上游完整上下文日志不应恢复到本机 |
| abhixhek/jevcal、jujumilk3/jev-calibration-audit | 阈值/弃权、跨语言、选项扰动、版本校准的检查方法 | 演示/模拟数据不是中文金融 gold |
| NiazMorshed2007/jev-review | 有限反馈→修改→测试→复评 | 分高不是代码正确，也不是独立发布审核 |
| zhuyansen/jev-search-rerank-eval | 与强 embedding baseline 比；保留失败和融合对照 | 不能假设独立 Jev rerank 必然更好 |
| openlayer-ai/jevals、qkal/Canny | 输出关联证据、失败策略值得研究 | alpha/宿主适配与 fail-open 必须验证，不能直接当完成门禁 |
| winnow、foreman-jev | 过滤/监督的思路可作反例比较 | Claude 插件不自动兼容 Codex；现有流程已覆盖时不再加 supervisor |

上述来自同日原 README/代码快照，最多属于外部 E1；获批时点的固定提交与原始摘录保留在本机调研材料 `artifacts/radar/jev-visualization-20260923/sources/`，本轮未运行这些项目 benchmark。原项目链接见文末来源表。

### 12. 是否创建新插件

保留两个**有条件**候选，先把能力做成现有模块/报告，达到复用门再包装到项目外个人插件：

| 候选 | 必须提供的真实能力 | 创建门槛与退出 |
|---|---|---|
| Jev Observer（暂名） | 读取已获准 metadata，把业务判断与 Codex 调用的建议/采用/结果/降级关联成只读报告 | 至少两个真实来源、有人重复使用、相对手查节时有证据；无法合法采集或只增加看板则退出 |
| Jev Evidence Judgments（暂名） | 共享经过人工验证的中文证据关系/修订/缺证定义、版本化批次接口与领域样本协议 | 至少两个真实消费者需要同版判读且手抄已造成漂移；仅包装 evaluate 不够，不另造密钥/HTTP通道 |

决策过时、复盘分类、UI 语义检查优先复用第二类窄问题/原领域函数；不分别创建多个插件。校准复用 RSH-030，雷达复用 GOV-025，研究准入复用 IMP-020。插件形式本身不能绕开原业务证据门。

### 13. 实验、成本与验收

每项只预注册一个主要收益假设，明确 baseline、样本/时点、独立标签、失败代价和退出。现有 240 条事件 strict human gold 必须保留；新猎场修订/引用题有不同标签定义，需独立对应人工样本，不能说做完原240条就覆盖全部新增场景。

首轮可先冻结 30 个覆盖正常/冲突/未知/失败的代表任务作工程与可行性筛选，不能据此声称生产准确率。提醒/筛选包含高置信忽略项和未选中项；中英文、否定、同名实体、传闻、更正、转述、对抗文本分层。人工先独立标注后对照模型；规则/模型/教师模型预测不能填 human。

比较规则、现有生成模型、Jev+fallback 三臂时看完整链：关键错误/漏检、证据覆盖、弃权/升级、p50/p95 延迟、重试、输入输出 tokens、实际金额、人工核对/维护时间。检索 tokens 少不等于订阅额度或实际账单少，另一个模型也不能只凭宣传称便宜。

拟议单候选上限沿 v0.2：最多两轮，每轮最多 300 条、三臂；总 500 次物理请求/2M input tokens/估算 2 美元/2 工程日/4h 新增人工审核，先到即停，全部 fallback 和重试计入，更严格账户/项目预算优先。现有 gold 工作另按真实人工安排，不能为赶预算自动填标签。新增 pilot 14 天复核，WATCH 30 天复核，无新证据则退出活动队列。

筛选目标可事前选择“同质量审核中位耗时减少 ≥15%”或“同质量完整任务总输入 tokens 减少 ≥15%”，且关键类别不退化、业务 deadline 不突破；不能事后择优宣传。样本允许时给成对区间；样本不足、区间跨零、依靠漏困难任务获得收益均为未证明。选股收益另须时序外、成本/实际成交、消融与风险口径，不能用这些工程指标替代。

降级验收覆盖：off、缺配置、401/403、429/5xx、超时、坏 schema、预算/记账失败、alias漂移、旧缓存、迟到响应、部分批次失败、hook阻断、恢复半开、来源抓取截断。测试使用隔离 fixtures/本地假服务，不断生产网络、不外发真实私人上下文求证。

### 14. 与现有账本的关系及替代判断

本轮核对46项现役任务，31项未完成全部分析，详见 [逐项账本对照](../review/jev-system-scan-20260924.md#2-全部未完成任务逐项对照)。目前没有证据支持用Jev整项删除或取代其中任何任务；**有价值的是替换少数实现分支、挑战昂贵路线、合并重复规划**。

| 现有任务/领域 | 可优化或替代的子范围 | 保留的原义务 |
|---|---|---|
| IMP-051真实助手任务 | 用现有编排+Jev窄判断与计划重框架作轻量challenger；同质量总成本/维护胜出才建议不引入该框架 | 三类真实任务、无AI对照、独立效果与资源边界 |
| IMP-048/049事件与猎场 | 已有规则解决不了的修订/引用/语义关系，可能替代无限扩词或例外分支 | 来源/时点/机会版本/开放情境、hard gate与原消费者 |
| RSH-027 KB | 候选适用性/证据关系可比较替代纯关键词或昂贵整篇判读 | 召回、引用真记录、有无KB增量、失败回退 |
| RSH-003/031研究 | 语义特征与原候选在同预算竞争，有证据时替换低价值候选实验 | 全样本、时点、消融/OOS/实际fill、失败和多重试验证据 |
| IMP-045/046、RSH-030 | 直接复用现有窄判断/路由/gold owner | 不另造通用语义中枢、模型路由器、第二套校准账 |
| GOV-024/025/027 | 更新降级/创新雷达/决策衰退并入原治理范围 | 不重复创建定时任务或机制总注册表 |
| IMP-050 | 理由/变化/未验证按需求放进已有详情 | 全站其他UI闭环照旧；无视觉需求的后台Jev不造前台 |
| BUG-009/016、IMP-007/053、GOV-013/021等 | 无模型替代依据 | 消费链、通知事实、执行/恢复、四层独立验收均保留 |

后续新任务只在“没有合适原owner、存在必要工作且用户批准”时创建；范围相容的补到原任务子片。编号不复用，stage保持唯一状态，依赖/效果前置不能削弱。当前G1/IMP-048仍按选择器推进；讨论Jev不赋予插队权，也不降低其他任务优先级。

获批后已核最新 stage，沿原 G0–G5/GX 与 P0–P2 规则把相容子范围登记到原任务；未创建新阶段或新任务编号，也未更改原任务优先级、门内序与依赖。若要合并/替代/退出旧项，须另给具体原ID、替代子范围、保留义务、消费者迁移、证据和恢复并获准，不能直接把原项改名成Jev任务。

契约可先支持optional/未评估，后续语义增强到其合法阶段再接；G2不能整门等待G4模型字段而自锁。每个接线片包含自己的降级；当前已有消费者若发现正确性缺陷，按原缺陷owner走正常规矩，不把所有修复拖到新系列最后。

本次批准的正式传播已进入 implementation-plan、Jev 专题、INDEX、plan-registry、所属 stages 与 handoff；项目 AGENTS 只更新版本入口，Skills 的执行规则未变。审查附件是时点证据，不是并行账本。

### 15. Governor 审查聚焦的反例与退出

- 没有 Jev 时，原业务仍可正确执行吗？不能则先有明确门禁/退路，不能靠“高置信”解释。
- 标注不够、源不可靠、未召回正确选项时，是否允许 unknown，而不是逼一个高分？
- 规则分、语义分、实际收益、模型自信有没有混用？前端默认排序是否偷偷成为未批准的策略？
- 旧版本/旧缓存/迟到结果会不会覆盖新事实？多源转载是否重复加分？
- 降级是否扩大权限、换供应商泄露材料、绕预算或不断重试？恢复是否只看 HTTP200？
- 复盘是否漏了未选/未成交/弃权，或用未来材料修饰原决定？
- G2 契约是否反向等待 G4 接线导致自锁？离线 E3 是否误称生产 E5？
- 雷达是否依赖 Jev 自身正常才能发现 Jev 故障，或永远只收集不退出？
- 新插件是否只是又一层包装；观测丢失是否冒充零调用；跨宿主是否采集了未获准正文？

退出条件：没有真实消费者、质量不如 baseline、重要类别退化、维护/人工成本超过收益、权限/许可无法满足、实验期限/预算用尽。退出对应增强并保留否证证据；只有新版本、新数据、真实消费者或已修根因出现才重开，旧成功不永久有效。

### 16. 已批准方向与来源

2026-09-24 已批准的方向：按全项目地图保留真正有消费者的窄判断，以猎场“理由变化与证据”贯通事件/提醒/复盘；采用分项理由质量而非未经验证的选股总分；所有接线带降级；沿已有雷达周期发现、挑选、验证更新。UI、账本、研究和插件按真实收益条件扩展；不具视觉必要性的后台能力只验业务结果。全模块覆盖附件和31项任务对照均属于本方案审批范围。

本次用户“通过”授权正式账本/方案登记；实际切片实施、外部实验消费、定时任务调整、插件安装/升级、生产模型/阈值切换仍分别受原流程的派工、预算与发布门约束，方向批准不构成无限预算或自主发布。

原始来源：

- [TypeSafe API](https://docs.typesafe.ai/api)、[模型](https://docs.typesafe.ai/models)、[Score](https://docs.typesafe.ai/primitives/score)、[能力局限](https://docs.typesafe.ai/model-jaggedness/jev-1.13)、[组合评分](https://docs.typesafe.ai/patterns/composite-scoring)、[特征发现示例](https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery)。
- [awesome-Jev 目录](https://logicrw.github.io/awesome-jev-projects/?q=jev-in-codex)、[jev-in-codex](https://github.com/teempai/jev-in-codex)、[JevRouter](https://github.com/BillionsBobby/JevRouter)、[jevcal](https://github.com/abhixhek/jevcal)、[jev-review](https://github.com/NiazMorshed2007/jev-review)、[jevals](https://github.com/openlayer-ai/jevals)。
- [calibration-audit](https://github.com/jujumilk3/jev-calibration-audit)、[search-rerank-eval](https://github.com/zhuyansen/jev-search-rerank-eval)、[Canny](https://github.com/qkal/Canny)、[winnow](https://github.com/GhalebDweikat/winnow)、[foreman-jev](https://github.com/Shifty-Eye-Games/foreman-jev)。
- [Codex 插件机制](https://developers.openai.com/plugins/build/plugins)、[Hooks](https://learn.chatgpt.com/docs/hooks)；实际支持范围实施时还需对当前宿主版本验证。

全部外部项目是参考/候选，不代表本系统已采用、已验证或会自动安装。网页和仓库内出现的操作指令都不作为任务授权。
