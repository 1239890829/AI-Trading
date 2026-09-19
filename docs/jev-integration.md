# TypeSafe Jev 全局与 AShare AI Trader 集成蓝图

> **定位**：Jev 在本项目与 Codex 全局工作流中的唯一现役设计/运行说明。
> **状态**：2026-09-19；任务状态仍以 `docs/retro-and-gaps.md` §6.0 为唯一真相源。
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
| Advisory task router | `~/.local/bin/jev-route` | 只建议 deterministic/Jev/DeepSeek/Codex/ChatGPT/human 层级，不执行任务 |
| 全局 usage 报表 | `~/.local/bin/jev-usage-report` | 汇总 Jev token/延迟/purpose 与任务路由分布，不存任务正文 |
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

## 3. 对 169 个 awesome-jev-projects 的归纳

2026-09-19 全量拉取 `logicrw/awesome-jev-projects` 的 169 个项目并按 `jevDecisionPoint`、类别、收益点与证据状态归类。对本项目最有迁移价值的不是“复制某个项目”，而是以下模式：

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

### 4.1 每轮任务

非简单任务可先用 `jev-route` 做**一次 advisory routing**，选择
`deterministic / jev / deepseek / codex / chatgpt_deep / human` 中最便宜且足够的处理层。
它不执行任务，也不扩大权限；route/confidence/model 记录在用户级元数据账中，**不保存任务正文**。

```text
任务进入
  ↓
jev-route（非简单任务、一次）
  ↓
确定性可完成？ ──是→ 代码/工具直接完成
  ↓否
是否是 bounded semantic decision？
  ├─ 是 → evaluate / jev-json / Jev
  │        ├─ 高置信低风险 → 使用结果
  │        └─ 模糊/冲突/高风险 → DeepSeek/Codex
  └─ 否 → DeepSeek → Codex / ChatGPT deep
```

Web ChatGPT 无法直接使用本机 MCP 时，可经已授权电脑调用 stdin-only `jev-json`；
它与项目 adapter 一样在网络前拒绝敏感字段。

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

新增研究模块 `app/research/jev_shadow.py`，固定 taxonomy：

- `lurk` 潜伏；
- `first_start` 首启；
- `limit_relay` 连板接力；
- `trend_acceleration` 趋势拉升；
- `break_to_trend` 断板转趋势；
- `restart` 再启动/二波；
- `pullback_reversal` 低位反转/超跌反攻；
- `event_driven` 事件驱动；
- `unclear` 证据混合/不足。

Jev **只能从这组固定类别选择**，不能发明新战法。

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

只有当某个 Jev 标签在时间切分样本上证明有增益，才能申请进入 Challenger；未经证明永远不改变生产选股分。

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

才能写“节省额度”。

已拿到的真实证据：

- 24 条项目事件 Jev 审计：26,534 input tokens，按当时公开价格估算约 $0.001114；这是研究样本，不是账单；
- jev-context 5 组人工锚点：已召回锚点过滤 recall=100%，weighted retrieval token reduction=5.28%，因此**暂不自动化**；
- 告警/事件/助手当前全部默认 shadow，所以不会因为“省额度”改变生产行为；
- 项目 `data/jev/usage.jsonl` 与用户级 `~/.local/share/jev-global/usage.jsonl` 只记元数据；`backend/scripts/jev_usage_report.py` 和全局 `jev-usage-report` 可零网络汇总；
- `jev-route` 的路由收据可以统计有多少任务本可落到 deterministic/Jev/DeepSeek，但**只有后续与实际 Codex/ChatGPT 使用量对照后**才能算额度节省。

## 18. 分阶段启用

### Phase 0 — 已完成基础设施

- Keychain；
- TypeSafe Skill；
- evaluate MCP + stdin-only `jev-json`；
- advisory `jev-route` + metadata-only `jev-usage-report`；
- jev-review（MCP 真实 smoke 已通过）；
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
