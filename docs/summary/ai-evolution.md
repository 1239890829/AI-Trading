# AI 智能体与进化汇总（AI & Evolution Summary）

> 产品展示修订：进化、参数、任务与运营告警控制退出普通交易前台，后台已有审计、权限、停止与恢复保留；真实风险/结果仍可见。网页统筹/审核与Codex实施依 ../collaboration-workflow.md，只靠账本短提示，不再建设模型互调工具链。

> 当前实施与权限修订见 [实施校准 v9.12](../implementation-plan.md)，状态唯一归账本 §6.0；本页历史“自动转正/事后回滚”描述不构成新的线上参数授权，保留背景并由 IMP-052 核当前消费者。
> **定位**：AI 控制台 / 自主进化 / 策略进化 / 仓库整合 / LLM 微调 的**已落地结论与待办**汇总。
> **Jev 当前边界不在本页维护**：TypeSafe/Jev 的 bounded semantic verify、条件 capability routing、human-gold/额度实证与回退统一见 [Jev 蓝图](../ai/jev-integration.md)；本页历史 LLM/进化记录不能覆盖该现役规则。
> **长期外部创新不在本页维护**：新模型/Agent/量化方法/工具的主动发现、筛选和重开规则见 [开放世界持续演进蓝图](../ai/continuous-evolution.md)；本页“自主进化”历史实现不能被解释成自动采用外部候选。
> **现役不在此**：`../system/llm-gateway-probe.md`（网关探针）、`../review/review-agent.md`（复盘模块说明）。
> **整理**：2026-09-10。

---

## 1. 定位与命名（原 AI 控制台）

- 名称：**交易智能体 · 自主进化体**（前端 5 处文案已落地：导航 / 页标题 / 悬浮球上下文 / 返回 / 加载）。
- 结构：四个 tab（任务中心 / 复盘 / 提醒与告警 / 参数配置）+ 权限分级 **L0–L3**。
- 红线：不真实下单 / 不伪造实盘 / 凭据只在 `backend/.env` / 撮合规则硬拦截。

## 2. 自主执行机制（进化议程，受限自治默认开启）

```
每日 15:45 → 收集十路证据 → LLM 裁决（严格 JSON，≤3 项）
  → 受限自治按证据与权限分级执行（紧急停机时只输出建议议程）：
     A 类 参数建议（入影子，保存结构/影响评估，未核准不得转正）
     B 类 改文档 / 写 KB
     C 类 代码提案（明确文件 → LLM diff → 路径授权 → git apply --check →
                  补丁文本归档与审计；不应用、不执行、不建分支或提交）
  → 后置守护：回归门禁 + 劣化回滚 + 红线清单 + 预算上限
```

**C 类纯提案契约（2026-09-18，IMP-052）**：
执行器只产出 `artifacts/evolution-patches/` 下的文本补丁与审计，不应用补丁，不创建工作树、分支或提交，不执行宿主 pytest/pyflakes/回放，更不合并或推送。旧隔离分支产物仍按历史读取，不能代表现版行为。
新条目为 `proposed`（待审提案），返回 `merged=False`、`review_required=True`、`code_applied=False`、`gate_ran=False`。任务生成成功仅表示补丁归档成功，既不是测试通过，也不是代码已落地。历史 C 类 `executed` 在界面标待复核，新旧 C 条目均不得自动把复盘改进项写成 applied。

| 层 | 判据与边界 |
|---|---|
| 开关 | 默认关闭；开启仅允许文本提案，不开放任何宿主执行路径 |
| 文件与权限 | 只读明确的既有 backend/app Python 文件；原白名单、禁改、受保护目录、路径/符号链接校验保留；读上下文前就拒绝不合格路径 |
| diff 核验 | 解析全部文件头，拒绝未声明文件、穿越、改名、复制及增删；git apply --check 只核适用性，不实际应用 |
| 基点 | 提案绑定完整 HEAD 与文件摘要；生成期间变动则暂缓，不覆盖人工改动 |
| 成本 | 每日 C 提案 1 次 + 自主 LLM 8 次 + 自主改进 3 次均由 SQLite numbered slot 跨进程原子预留；失败/started unknown 不退款，部署日旧尝试 backfill；业务 LLM/Jev 进入统一 metadata usage 查询面但不偷占自治额度 |
| 留痕 | 生成前建任务和审计，缺diff、异常、归档失败和基点漂移均回填结果；不把静态通过标成测试全绿 |
| 实施 | 由获准开发者在 codex/* 分支实施并完整验证，经独立审阅和准确版本CI后交付；应用内提案不能自行升级为执行 |

工作树仅隔离Git文件版本，并非操作系统安全隔离。即便禁止修改测试，测试导入修改后的 app 也会执行代码，因此本版移除整个应用内宿主执行路径，而不是再加一个开关。IMP-052 已把参数 promotion authority 与模型/任务预算、跨进程取消分别收进持久事实：started unknown 不按 0 退款，取消请求不等于取消完成，wall-time 超时也不冒充用户取消。

- 运行默认：`ASHARE_AGENT_AUTONOMY_ENABLED=1`、`ASHARE_AGENT_CODE_CHANGE_ENABLED=0`。
  前者使A类影子评估、B类记录及既有实验回滚运行；不再自动转正；紧急停机时设为 `0`。C 类仍要求代码开关显式开启，
  且只产出待审 patch，不应用、不执行、不提交、不合并、不推送。
  （变量名以 `.env.example` 为准。**勘误 2026-09-14**：本文档与代码注释此前写作少 `_ENABLED` 的短名，
  按该名设置**根本不生效**——短名现已兼容但会告警，见 KB-ENG-76。）
- 停机语义：关闭时议程照常生成（降级为建议清单）；代码修改关闭时只允许建议、议程或 patch 预览，
  **不得修改工作区、提交或合并**。调度器的影子评估/既有实验回滚也随自治开关停止；
  元评估周报是显式例外（只产出可读 artifact，不改系统行为）。
- 十路证据含：复盘改进项（含 `repeat_pending` 重复未落实检测）、信号健康、告警统计、台账归因、KB 健康、数据健康、`framework_backlog`（06 框架演化日志）等。
- 设计要点：低风险自治默认运行，代码能力独立 opt-in；关闭自治时系统保持只读建议模式。

### 2.1 参数影子晋级边界（2026-09-18）

权重漂移温和仅是结构检查，离线 `supports` 仍只是探索证据；调度只保存候选与 shadow evidence，不取得批准权。模型 evidence 内的 `approved/reviewer` 字段仍不构成批准，通用 `apply_change` 继续只允许合法人工 draft，不能应用 shadow、rejected 或 AI 来源草稿。

2026-09-22 的 IMP-052 参数晋级纵切新增**独立于普通写权限的 promotion-operator 批准链**，不是 evaluator 自批平台：先读取候选/当前运行基线/影子证据的 exact digest review package；批准写入口必须同时经过普通写鉴权和专用 `ASHARE_AGENT_PROMOTION_TOKEN`，后者默认空即关闭、不得与普通 API token 共用。专用凭据判定由 `core/auth.py` 单点拥有，**service 创建/撤销批准时也必须显式提供并通过**，HTTP dependency 只是取 header 的适配层，不能成为唯一授权边界。批准还必须绑定仓库内真实 review/research/artifact 文件与当前 SHA-256，最长 24h、可撤销、一次性消费；消费时再次核候选/基线/影子证据/效果文件，任何漂移或本地 shadow 已为负面/不足即 fail-closed。candidate CAS、approval consume、live baseline CAS、参数写入和既有 30 日后置实验在同一事务落库；后置基线拿不到就整笔不生效。已有 owner-aware rollback 继续保留。A类入影子仍不回写复盘 `applied`；只有上述 promotion-operator 批准链真正消费后才形成 applied 事实。2026-09-22 最后一纵切又以 `agent_resource_usage` 收口统一 usage/token metadata、跨进程 quota 与 cancellation：自主模型/任务按北京日原子 slot，普通业务 LLM/Jev 只记 telemetry；provider 未返回 token 时 `usage_known=false`，自主 scope 遇 started unknown 当日停止继续消耗。

### 2.2 模型预算与取消边界（2026-09-22）

`agent_resource_usage` 是 Agent 资源预算/usage 的持久事实：自主 LLM 默认 8/day、自主改进 3/day、C提案 1/day；输入 250k chars、输出 150k chars、model timeout 180s、retry≤2、task wall-time 600s 为默认高水位。只有 never-started stale reservation 可回收，started 后未知用量保留占用。`GET /agent/resource-usage` 仅暴露 metadata，不含 prompt/messages/Jev state/questions/凭据。Jev 保留既有 metrics/JSONL，同时 production lifespan 镜像 metadata 到统一表。

Task 取消先落 `cancel_requested_at`；跨 worker 时调用方保持 running/queued +「取消中」，owner 观察到 intent 并真正停止后才写 `canceled`。服务重启对账把“已有取消请求的残留”收成 canceled，其余残留收成 failed/Interrupted；timeout 单独为 failed/TaskTimeout。

## 3. 复盘闭环（含最后一公里）

- 链路：06 七阶段复盘 → `action_items` → 议程 → 裁决执行 → `applied` 回写。
- **补上的断点**：`suggest_methodology_changes()` 此前只展示无消费方（"看得到改不了"）→ `app/services/agent_params.py` 补齐「建议 → 变更单 → 生效 → 回滚」，白名单 + 值域钳制 + 证据门槛（不足只允许 draft）+ 运行时覆盖层（免重启）+ before 记录可回滚。
- 纪律：**该模块不自动改任何参数**；生效来自人工确认或显式 API。

## 4. 策略进化（strategy-evolution）P0 落地

| P0 项 | 承载 |
|---|---|
| 信号健康度监控 | `picks/signal_health.py`（滚动 20 次胜率/期望 + CUSUM 漂移） |
| 筹码分布引擎 | `market/chip.py`（CYQ 衰减模拟：获利盘/集中度/密集峰）+ `GET /chip` |
| 复盘策略健康维度 | `review/strategy_health.py` |
| 相位对账 | `sentiment/reconcile.py` |

**P2 延后（按设计，依赖样本积累 ≥200 或满季度）**：HMM regime / 游资席位画像 / meta-labeling 数据版 / walk-forward 门禁 / 回测成本建模 / 新闻情绪特征入模。
> ⚠️ **状态不在此（2026-09-14 `GOV-002`）**：各项**状态与触发条件一律见账本 §6.0**
> （整组随 `RSH-018` 维持**观察**：`RSH-016` HMM regime / 席位画像 / meta-labeling，其余见 §6.3 触发条件表）。
> 本节只说明"为什么按设计延后"，不承担进度断言。

## 5. 仓库整合结论（trading 分组 + 新仓库评估）

- **trading 分组 22 仓**：14 仓已有历史深评结论（直接引用不重评），真新增 8 仓；**无替换级候选**——换血重点回到系统内部（因子 0 消费 / 形态薄弱 / 记忆效应排序）。
- **akquant（0.3.58）建议引入**（回测 + 指标引擎）；**talib 一律指定 `backend="rust"`**（Python 后端有 NaN→0 头部污染）。
- 「AI 能否替代定时任务」的结论：**不是替代，是三层分工**（数据枢纽 / 分析决策 / 自主执行）。
- 教训：**大分组评估三步**——先查 archive + 台账 diff → 只评真新增 → 浅评即可。

## 6. 自主化路径（research-autonomous-agent）

**结论：分层而非单选**——
| 层 | 方案 |
|---|---|
| 触发层 | ~~launchd + `claude -p`（headless）~~ ❌ **2026-09-12 裁定 B 不采用**（见下） |
| 能力层 | 补一个 MCP server（多步工具循环：查数 → 读 KB → 跑脚本 → 写结论） |
| 治理层 | KB 复盘框架 + 现有推送矩阵 |

差异点：15:45 议程是**固定管线**（十路 → 一次裁决），非自由探索。

**触发层裁定（2026-09-12，B 方案）**：`launchd + claude -p` 选型**已推翻**。实测 `com.ashare.review`
自 09-09 安装后 `runs=4` **全部 exit 126**、零产出——项目位于 `~/Desktop`（macOS **TCC 保护目录**），
launchd 启动的进程无该目录访问授权。**改用后端已有常驻调度承担**：`review-scheduler`
（交易日 15:30 → `data/review/reports/YYYYMMDD.json`，实测 09-02~09-11 **连续 9 个交易日准点产出**）
+ `picks-intraday-review`（15:35）。**不新增独立机制**——KB-ENG-62 的普适结论。
ad-hoc headless 的真实成本样本（供未来评估）：**$1.40 / 38 轮 / 8.2 分钟**。
拆除明细与教训：`kb/09-verification-pitfalls.md` KB-ENG-62、`retro-and-gaps.md` §6.10。

## 7. LLM 微调路径（三层，守零新增付费依赖）

| 层 | 内容 | 状态 |
|---|---|---|
| 层 1 | pending 事件交给 GLM 结构化判定（走 analyzers 纪律 + `llm_aux` 标记不伪装） | ✅ 已落地（`events/llm_aux.py` + 20 分钟盘中轮询，默认开关） |
| 层 2 | 情绪因子批量打分（若过池内 IC） | 状态见账本 §6.0（**等窗**：需算力/成本确认，属零新增付费依赖纪律的待拍板项） |
| 层 3 | **`event_card` 判定沉淀自标注数据集**（事件×方向×依据×买点五因素×T+1/T+5） | ✅ 脚本已交付（`scripts/export_event_labels.py`） |

**核心洞见**：我们的 `event_card` 判定结果本身就是"强模型/规则标注 → 蒸馏小模型"路径里最缺的高质量标签数据。
**近期不实际训练**（无 GPU，训练即新增付费依赖）。

---

## 原始文档指针

| 原始文档 | 处置 |
|---|---|
| ai-agent-console-plan.md / ai-brain-plan.md / evolution-brain-plan.md | 已归档（全落地） |
| strategy-evolution-plan.md | 已归档（P0 落地，见本文 §4；**P2 延后项状态 → 账本 §6.0**，不在本文维护） |
| trading-star-merge-plan-20260909.md / trading-agent-positioning-20260909.md | 已归档 |
| research-autonomous-agent-20260909.md | **已删除**（未落地方案 → 账本 §6.0：`RSH-018` 观察档） |
| llm-finetune-research-20260909.md | **已删除**（层 2 → 账本 §6.0 等窗；层 1/3 已落地见本文 §7） |
