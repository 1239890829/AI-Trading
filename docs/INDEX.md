# 文档索引（INDEX）

> 定位：**docs 唯一入口**。查东西先来这里；写新文档必须在此登记。
> 维护约定：状态在所属阶段单点更新，成果提炼与恢复按 `kb/07-doc-curation.md` §3.2；新建/迁移同步本表，未知资产不删除。
> 当前入口（2026-09-20）：从最新 `master` 的 `AGENTS.md` → 本索引；`retro-and-gaps.md` §5.9 是执行门序治理、§6.0 是 W00–W09 领域索引，`stages/` 存任务唯一状态与调度元数据，`handoff.md` 仅存当前主门/现场。 `implementation-plan.md` 是 v9.12 规划修订，不单独定义当前执行模式；允许动作以最新 `handoff.md` + 所属 stage 为准。分工与逐轮审核见 `collaboration-workflow.md`；旧平台目录已退休，历史迁移证据归 `archive/platform-directory-migration-20260920.md`。

## 0.0 书库编目（编号 / 层 / 领域 / 用途）

> **为什么有这张表**（用户 2026-09-13：「每份文档都应有其编号、分类与领域用途，并做好持续维护」）：
> 判据「**可定位**」是「一份好文档」第一条——**找不到 = 等于不存在**。编号用于**指认**，不用于排序。
> 分层（L1/L2/L3）与收敛标准见 **`kb/11-doc-catalog.md`**；整理流程见 `kb/07-doc-curation.md`。
>
> **编号规则**：`领域前缀-序号`。**前缀变更 = 换领域**（罕见）；序号只在同领域内追加。
> **编号一经分配不再回收**（与 KB-ID「永不删」同原则），文档删除后编号标记为「已退役」。

| 编号 | 文档 | 层 | 领域 | 用途 / 维护触发 |
|---|---|---|---|---|
| **AG-01** | `AGENTS.md`（根） | L3 | 工程治理 | 作业边界、门禁命令与发布纪律；测试实测只在 handoff |
| **AG-02** | `README.md`（根） | L3 | 对外 | 项目简介 + 指针式入口。维护：结构性变化 |
| **KW-00** | `CONTEXT.md`（根） | L2 | 术语 | **领域词汇表（Ubiquitous Language）唯一权威**；只放术语、不放实现。维护：grilling 会话即时更新 |
| **AG-03** | `INDEX.md` | L3 | 编目 | **文档总入口**（本表即编目）。维护：新增/删除文档时 |
| **AG-04** | `retro-and-gaps.md` | L2 | 账本 | §5.9 G0–G5/GX 阶段门、任务调度治理；§6.0 索引 W00–W09 领域归属；不复制任务状态 |
| **AG-05** | `plan-registry.md` | L3 | 计划 | 历史计划去向、文档权责与重大决策传播契约；旧状态不作当前指令 |
| **AG-06** | `archive/PROJECT-MASTER.md` | L1 | 历史总览 | 08-29 技术基线，只读追溯；当前总览从 INDEX / handoff / system 分类进入 |
| **AG-07** | `handoff.md` | L2 | 交接 | 当前工作区、实际运行版本、最近门禁与交付；不另建任务记录 |
| **AG-08** | `archive/platform-directory-migration-20260920.md` | L1 | 工程治理历史 | 旧平台目录迁移/恢复/最终退休证据；`GOV-018` 已完成，禁止恢复为现役入口 |
| **AG-09** | `stages/` | L2 | 阶段任务 | W00–W09，各 ID 仅一份状态/验收/证据；P/Q 校验 |
| **AG-10** | `implementation-plan.md` | L2 | 当前实施修订 | v9.12：living plan；继承 G0–G5/GX、U48 生命周期、U49 主动缺陷发现门与 U50 降级全权闭环，并新增 U51 JEV 可观测/命名/降级契约；状态仍归阶段 |
| **AG-11** | `collaboration-workflow.md` | L2 | 协作审核 | 账本驱动；网页统筹审核、Codex执行，用户一句话触发，`master` 共享入口与原型退出 |
| **AG-12** | `product/product-closure-design.md` | L2 | 产品闭环 | 全模块用途、前后台分工、业务/研究/工程链和确认流程图；非已上线报告 |
| **AG-13** | `product/feature-closure-audit.md` | L2 | 细功能审计 | 大小动作/接口/后台任务的覆盖、源码发现与未验边界；任务状态仍归阶段 |
| **AG-14** | `ai/continuous-evolution.md` | L2 | 持续演进 | 外部模型/工具/量化方法/数据/工程创新的发现、证据梯度与准入前治理，并规定已采用内部机制的阶段性有效/衰退重验；状态归 GOV-025/GOV-027/真实 owner stage |
| **MD-08** | `product/hunting-decision-design.md` | L2 | 选股与呈现 | 开放情境、KB37项映射、候选/时机/反证、猎场UI及分层验收 |
| **MD-09** | `research/limit-up-dragon-research.md` | L2 | 选股研究 | 历史涨停/强连板/龙头形成：全样本+失败对照、点时证据、Jev语义MapReduce、旧→新盲测及向猎场回流 |
| **KW-00..11** | `kb/00-INDEX.md` … `kb/11-doc-catalog.md` | L2/L3 | 知识 | 见下方「KB 知识库」分表 |
| **SM-01..08** | `summary/stock-strategy` · `factor-system` · `data-market` · `architecture-design` · `ai-evolution` · `review-governance` · `system-final-blueprint` · `pick-signal-chain` | L2 | 主题汇总 | **查主题先看这里**；`system-final-blueprint` 是任务四后目标架构与验收总纲；**`pick-signal-chain` = 选股提醒链路（`dispatch_alert` 扇出 / 双家族分裂 / 断点清单 G1–G5）**。维护：主题结论更新 |
| **DT-01** | `data/data-source-comparison.md` | L2 | 数据源 | 四源实测对比与选型。**改数据源前必读，改完回填** |
| **DT-02** | `data/data-sources.md` | L2 | 数据源 | 接入策略：主源 → 备源 → 降级链 |
| **DT-03** | `archive/external-data-source-survey-2026-09-11.md` | L1 | 调研 | 外部付费源调研。§0–§7 = 官方公开信息（零实测，📎）；**§8 = 2026-09-16 复评（本机实测）**：FTShare 免费档实测 + 1000 次/天预算精算 + 四家价格/覆盖横向对比 + **升级决策 = 不升级**。**已拍板不接入付费档** ⇒ 只读留痕 |
| **MD-01** | `strategy/theme-sentiment-methodology.md` | L2 | 方法论 | A 股热点题材与情绪分析 v1 |
| **MD-02** | `strategy/sentiment.md` | L2 | 方法论 | 情绪指标清单 + 阶段判定 + **历史误判案例库** |
| **MD-03** | `strategy/theme-prediction.md` | L2 | 方法论 | 新题材预判 |
| **MD-04** | `strategy/factor-lifecycle-governance.md` | L2 | 治理 | 因子全生命周期管理 |
| **MD-05** | `strategy/factor-candidates.md` | L2 | 登记册 | 候选因子登记（原 `app/factors/candidates.py` **已迁出删除**，纯文档） |
| **MD-06** | `strategy/backtest-rules.md` | L2 | 禁令 | 回测强制禁令（代码级校验） |
| **MD-07** | `strategy/risk-management.md` | L2 | 风控 | 风险拦截位置与规则。⚠️ 旧稿曾把「预检」写成「强制拦截」（**文档高估**），已更正 |
| **FN-01** | `strategy/strategy-registry.md` | L2 | 登记册 | **策略级**生命周期（核心辨析：**因子 ≠ 策略**） |
| **FN-02** | `system/architecture.md` | L2 | 架构 | 数据流与分层设计 |
| **FN-03** | `system/deployment.md` | L2 | 部署 | 部署与运维（3000/8000 纪律） |
| **FN-04** | `system/api.md` | L2 | API | API 契约（⚠️ 端点计数滞后，**以 `/openapi.json` 为权威**） |
| **FN-05** | `system/websocket.md` | L2 | WS | WS 协议（**必须直连后端，不走 Next 代理**） |
| **FN-06** | `system/mcp.md` | L2 | MCP | MCP 工具体系清单 |
| **FN-07** | `system/data-dictionary.md` | L2 | 数据 | 数据对象审计字段约定（source/quality） |
| **FN-08** | `archive/picks-replay-baseline.md` | L1 | 快照 | 精选 60 日回放基线（08-31 评审指名保留）。**时点快照，只读** |
| **FN-09** | `system/llm-gateway-probe.md` | L2 | 运维 | LLM 网关健康探针（`claude_cli` 别名监控） |
| **FN-10** | `ai/jev-integration.md` | L2 | AI/Agent 架构 | Jev 现役蓝图：bounded semantic verify、条件 capability routing、gold-set/usage 证据与回退边界；shadow 的冻结候选集不是猎场全局 taxonomy；任务状态归 GOV-024 / IMP-045 / IMP-046 / RSH-030，长期生命周期受 GOV-027 审计。**JEV 专项唯一入口**：阶段路线见 §18、输出契约见 §32（2026-09-23 收敛，原 `plans/jev/*` 与 `handoff/codex/JEV-CODEX-HANDOFF-001.md` 已下线，见 artifacts/trash） |
| **EX-01** | `archive/live-trading-guosen-plan.md` | L1 | 搁置历史 | 国信 miniQMT 实盘蓝图。**用户已搁置**（不接受 Windows 依赖），恢复条件见文档头 |
| **RV-01** | `review/review-agent.md` | L2 | 复盘 | 盘后复盘 Agent 架构 |
| **RV-02** | `review/daily-review-sop.md` | L2 | 复盘 | 每日复盘 SOP（怎么判） |
| **RV-03** | `review/daily-review-checklist.md` | L2 | 复盘 | 每日复盘执行清单（逐项勾） |
| **RV-04** | `review/chatgpt-multiwindow-audit-20260921.md` | L2 | 审计 | 9/15–9/21 ChatGPT 多窗口执行、Jev、隔离、运行态与工作区审计（一次性证据，不是第二账本） |
| **RV-05** | `review/bug020-production-session-20260922.md` | L2 | 审计 | BUG-020 2026-09-22 生产完整交易会话验收证据（一次性时点证据） |
| **RV-06** | `review/imp052-promotion-approval-20260922.md` | L2 | 审计 | IMP-052 参数晋级独立批准、证据绑定、原子消费与 U49 反证证据 |
| **RV-07** | `review/imp052-budget-cancel-20260922.md` | L2 | 审计 | IMP-052 统一 usage/token、跨进程配额、输入输出预算、取消传播与部署日回填证据 |
| **DR-01** | `daily-review/` | L1 | 存档 | 逐日复盘报告（YYYY-MM-DD.md） |
| **DR-02** | `repo-watch/` | L1 | 存档 | 仓库周期性跟踪周报 |
| **DR-03** | `evolution/` | L1 | 存档 | 进化议程每日执行日志 |
| **DR-04** | `push-templates/` | L2 | 模板 | 飞书推送卡片模板（v2 版式定稿） |
| **AR-01** | `archive/` | L1 | 历史 | 保留期间**只读**；结论已吸收且无活引用/活动依赖、Git 可恢复时可按 kb/07 §6 整件退休，不把 archive 当永久仓库 |
| **WB-01** | `artifacts/logs/` | L1 | 日志 | 只存过程证据；稳定结论提炼进 docs，日志从不承担权威入口 |
| **WB-02** | `artifacts/reports/` | L1 | 报告 | 本地报告；可复用结论提炼进现有 docs，私有快照不入 Git |
| **WB-03** | `artifacts/` | L1 | 证据 | 调研快照、补丁、恢复副本均忽略；长期只留紧凑证据/必要恢复点，大型测试沙箱与过期恢复副本按 GOV-026 收口 |
| **WB-04** | `skills/` | L3 | 技能 | 项目中性技能唯一目录；含接续/交接/复盘/创新雷达、`living-system-governor` 跨模块长期治理，以及迁出的数据源、CI、文档对账、策略实证、测试隔离、UI 验收等项目 Skill；禁止应用私有技能根 |
| **WB-05** | `artifacts/trash/` | L1 | 回收站 | `scripts/safe-trash.sh`，逐条恢复记录与哈希；拒绝项目外路径和恢复覆盖；不是永久仓库，退出条件由 GOV-026 管理 |

### 0.0.1 物理目录分类（2026-09-20）

`docs/` 根目录只允许 6 个跨域控制面：`INDEX.md`、`handoff.md`、`retro-and-gaps.md`、`implementation-plan.md`、`plan-registry.md`、`collaboration-workflow.md`。领域正文不得继续平铺根目录。

| 目录 | 职责 |
|---|---|
| `system/` | 架构、API、WebSocket、MCP、部署、数据契约、运行探针 |
| `data/` | 当前数据源接入、能力对比与选型 |
| `product/` | 产品闭环、细功能审计、猎场/呈现设计 |
| `strategy/` | 策略、因子、回测、风控、情绪与题材方法论 |
| `ai/` | Jev、Agent 能力与开放世界持续演进 |
| `review/` | 复盘 Agent、SOP、执行清单 |
| `research/` | 仍需实证/盲测的专题研究蓝图 |
| `kb/` / `summary/` / `stages/` | 知识唯一权威 / 主题汇总 / 任务唯一状态 |
| `archive/` | 只读历史、时点快照、搁置方案；不得承担当前权威 |
| `daily-review/` / `evolution/` / `repo-watch/` / `push-templates/` | 时间序列与模板 |

新增 Markdown 必须先判断能否并入已有文档；确需新建时先选上述分类并登记 §0.0。`doc-health` 的 O2 门禁同时阻止新的根目录正文和未登记顶层分类目录。

### KB 知识库分表（`docs/kb/`，**全序列唯一登记处 = `00-INDEX.md`**）

| 编号 | 册 | 领域 | 条目数 | 说明 |
|---|---|---|---|---|
| **KW-00** | `00-INDEX.md` | 总索引 | — | **全序列唯一登记处**；引用只写 `[[KB-XXX-NN]]`，查条目不必知道册名 |
| **KW-01** | `01-stock-picking.md` | 选股 KB-STOCK | 37 | ⚠️ 案例层 01~06 已全标 `📎`；**收敛候选**（可蒸馏为 1~2 条案例库入口） |
| **KW-02** | `02-trading-lessons.md` | 交易教训 KB-TRADE | 13 | — |
| **KW-03** | `03-engineering.md` | KB-ENG **应用与设计层** | 24 | — |
| **KW-04** | `04-decisions.md` | 决策 KB-DEC | 23 | — |
| **KW-05** | `05-repo-tracker.md` | repo 候选追踪台账 | 表驱动 | `continuous-evolution` 的 repo 子集；Watch/Scout 分离，Stars 非硬门 |
| **KW-06** | `06-review-framework.md` | 复盘框架 v1.0 | 框架 | **复盘任务先读它** |
| **KW-07** | `07-doc-curation.md` | 文档治理**流程** | 规范 | 分类/归属/配额/淘汰的操作流程 |
| **KW-08** | `08-tooling-pitfalls.md` | KB-ENG **工具操作**速查 | 15 | 每条 3~5 行 |
| **KW-09** | `09-verification-pitfalls.md` | KB-ENG **验证层** | 33 | 测试·门禁·CI·防线有效性 |
| **KW-10** | `10-data-contract-pitfalls.md` | KB-ENG **数据契约层** | 14 | 写入·去重·传输·时间·质量门（09-14 自 09 册迁入 `KB-ENG-82`） |
| **KW-11** | `11-doc-catalog.md` | 文档治理**判据** | 规范 | 「好文档」七条判据 + KB 收敛标准 + 卡帕西三层映射 |

**合计**：KB 条目 **159 条 / 4111 行**（2026-09-14 复测；原 09-13 基线为 `139 / 3123`，差额来自 09-14 新增与迁册）。
> ⚠️ **本表是快照，不是权威**——引用前**必须复测**（`### KB-` 计数 + `wc -l`）。
> 逐册结论与三个超线项（`07` 框架层超线 / `09` 已评估判定不拆 / `01` 待蒸馏）见 `kb/11-doc-catalog.md` **§3.5**，
> **详情报一份**、此处不重复（`kb/11` 为唯一出口，见 [[KB-ENG-85]]）。收敛触发与三底线见 `kb/11` §3。

---

## 0. 主题路由（我要做什么 → 去读什么）

本轮全产品入口： [产品闭环](product/product-closure-design.md) → [细功能审计](product/feature-closure-audit.md) 与 [猎场决策](product/hunting-decision-design.md) → [实施方案](implementation-plan.md) → [阶段任务](retro-and-gaps.md#60-阶段索引)。用户追加局部要求不缩小这个范围。


> **本节的职责**：回答「**做某件事，该按什么顺序读哪些文档**」。
> **分工互斥**（不得互相承载内容）：`kb/00-INDEX.md` 回答「某条**知识**是什么」（KB-ID）·
> `retro-and-gaps.md` **§6.0** 回答「**还有什么没做**」（唯一任务清单）· `CONTEXT.md` 回答「某个**词**什么意思」。
> **读写协议**（先索引后跳转 / 写入判据 / 反固化条款）见 `kb/07-doc-curation.md` §4.4；
> 会话短入口是 `AGENTS.md` 的项目入口块（纯指针，≤3000 字符），不另建 MEMORY 权威文件。
>
> **维护触发**（本节自身也会漂移，故有明确时点）：① 被引用的文档**改名 / 归档 / 删除** ⇒
> **当轮**改本节对应行；② 出现新的高频任务类型 ⇒ 在 §0.1 加一行主题（**不是加长已有行的描述**）；
> ③ 改完跑 `python3 scripts/doc-health.py`——**N 项**捕获短入口/非 docs 指针，**B 项**捕获显式 `docs/**.md` 死链，**B2 项**按引用文件自身目录解析 Markdown 相对链接，防目录迁移后“文件名没变但层级已断”。
> ⚠️ **本节只放指针与技术红线，不放内容**：某主题的细节变多 ⇒ **扩它指向的那份文档**。

### 0.1 按主题（先读 → 再读 → 红线）

> **只在该主题**「再读」**确有需要时才深入**——未命中即跳过。**减少单次读取量**是本表存在的理由。

| 主题 | 先读（入口，必读） | 再读（需深入时） | 红线（最常咬人） |
|---|---|---|---|
| **T1 起栈与环境** | `AGENTS.md` §1 快速启动 | `system/deployment.md` · `archive/PROJECT-MASTER.md` | `--reload` **禁用**（与 SQLite 锁组合挂死）；端口固定 **3000/8000**；仅同工作区共享构建目录时先停止对应 dev，独立工作区不得误停原服务 |
| **T2 门禁与测试** | `AGENTS.md` §1 门禁命令块 | `kb/09-verification-pitfalls.md` · `kb/03-engineering.md` | 必带 `--basetemp`、**勿叠 `-q`**；涉时区断言**必须 `TZ=UTC` 复跑**；vitest 加 `--maxWorkers=1`；**必须整仓跑** |
| **T3 数据源与行情口径** | `data/data-source-comparison.md` → `data/data-sources.md` | `system/data-dictionary.md` · `system/websocket.md` | 时效按数据类型、交易日历与来源可见时间判定；盘前/休市可用上一有效交易日，盘中旧数据不能冒充当前；不以一律“今天”或固定TTL替代业务语义 |
| **T4 选股与策略** | `product/hunting-decision-design.md` → `kb/00-INDEX.md` 选股表 | 历史涨停/龙头研究 `research/limit-up-dragon-research.md` · `summary/stock-strategy.md` · `strategy/strategy-registry.md` · `strategy/factor-lifecycle-governance.md` + `strategy/factor-candidates.md` | **示例 ≠ 规范**；赢家复盘≠预测能力；策略/因子落地**必须先过点时全分母、OOS/前向和成本实证**；回测禁令见 `strategy/backtest-rules.md` |
| **T5 复盘与治理** | 复盘 `kb/06-review-framework.md`；执行治理 `retro-and-gaps.md` §5.9/§6.0 | `plan-registry.md` §1.1/§1.3 · `collaboration-workflow.md` · `handoff.md` · `kb/07` · `kb/11` | W只管归属、G0–G5管门序、P0–P2管门内重要性；硬依赖未完成不得跨门；任务只在 stage 单点维护 |
| **T6 前端与 UI** | `summary/architecture-design.md`（§1 跨页面联动设计） | `system/architecture.md` · `kb/03-engineering.md` | **验收以实际渲染为准**（agent-browser 文本通道）；**新增页面/板块需先论证**；**详情弹窗化**（个股/指数在任何页面就地弹窗，不跳工作台）见 [[KB-ENG-92]] |
| **T7 外部工具与技能** | `ai/continuous-evolution.md` → `skills/ashare-innovation-radar/SKILL.md` | `ai/jev-integration.md` · 本表 **WB-04** · `system/llm-gateway-probe.md` | 外部热度只产候选；先许可/隐私/费用/权限硬门与证据梯度，未经 owner stage 不安装/准入 |
| **T8 决策与"为什么当初这么定"** | `kb/04-decisions.md`（KB-DEC） | `archive/ledger-transition-20260917.md` · `implementation-plan.md` | 既有决定可依新证据和最新授权复核；保留取代关系与依据，不据历史标题锁死设计 |
| **T9 提醒与通知链路** | `summary/pick-signal-chain.md`（**先读它**：定位/触发/流向/断点） | `services/alert_triage.py`（判读闸门）· `api/routes/notifications.py`（通知收口）· `picks/watcher.py::dispatch_alert`（唯一汇聚点）· `services/push_policy.py` | **收敛口径时必须回扫自称该口径的注释**（`IMP-028` 遗留 5 处过期断言，见该文 §G4）；**判读闸门现状只作用于悬浮球**（§G1）；改通知来源须同步 §3.1 规则清单 |
| **T10 跨模块长期治理 / 重大重构** | `skills/living-system-governor/SKILL.md` → `implementation-plan.md` §6/§6.2 → `retro-and-gaps.md` §5.9 | `ai/continuous-evolution.md` · `plan-registry.md` · W08/GOV-027 · 对应领域登记册/专题 | 先恢复真实上下文，运行主动缺陷发现门，再比较 KEEP/FIX/MERGE/EXPERIMENT/WATCH/RETIRE；Skill 只提供上层治理协议，不自创任务状态、不越阶段门、不替代领域 owner |

### 0.2 任务动线（**按序**读——顺序错会先读一堆无关的）

| 我要做的事 | 动线（按序） |
|---|---|
| **修一个 bug** | `AGENTS.md` §1 起栈 → 复现 → 症状反查（§0.3）→ 相关 `kb/0X` 条目 → 改 → 门禁（T2）→ 账本登记 |
| **加一个功能** | `AGENTS.md` §0 红线 → `summary/architecture-design.md` §1（**先论证是否需要新页面**）→ `system/architecture.md` → 实现 → 门禁 → 按 `kb/07` §3.2 处置方案文档 |
| **改数据源 / 数据口径** | `data/data-source-comparison.md` → `data/data-sources.md` → **改完回填对比文档** → 门禁（含 `TZ=UTC` 复跑） |
| **做一次复盘** | `kb/06-review-framework.md`（**强制先读**）→ `review/daily-review-sop.md` → `review/daily-review-checklist.md` → 产物存档 |
| **写 / 整理文档** | `kb/07-doc-curation.md`（流程）→ `kb/11-doc-catalog.md`（判据）→ 改动 → `python3 scripts/doc-health.py` |
| **登记或收口任务** | `retro-and-gaps.md` §6.0 → 所属阶段唯一任务条目 → 实测/证据与状态更新 → handoff 当前现场 → doc-health P/Q |
| **验证策略/因子是否有效** | `strategy/strategy-registry.md`（是否已测过）→ `strategy/backtest-rules.md`（禁令）→ 实证 → 结论**必须带失效条件** |
| **调研外部仓库/工具** | `skills/ashare-innovation-radar/SKILL.md` → `ai/continuous-evolution.md` → 原始来源核查 → 值得保留再进 `kb/05-repo-tracker.md`；真实实验回对应 stage |
| **做跨模块方案、重大重构或机制生命周期复核** | `skills/living-system-governor/SKILL.md` → 当前总方案/总账 → 对应 owner 文档/登记册 → 形成 KEEP/FIX/MERGE/EXPERIMENT/WATCH/RETIRE 决策 → 只把真正需要实施的最小切片写回 stage |

### 0.3 症状反查（**踩过的坑** → 只记得"当时踩过一次"时用它）

> 完整条目表在 `kb/00-INDEX.md`；本表只收**症状可辨识**的那些。

| 症状 | 去哪条 |
|---|---|
| shell 检索返空，或依赖不可移植的 BRE `\|` | `kb/03-engineering.md` KB-ENG-04（多分支用 `rg` / `grep -E`；“不存在”结论须第二工具复核，详见 `kb/08`） |
| 同文件多处 Edit 并行改，**只生效最后一处** | `kb/08-tooling-pitfalls.md` KB-ENG-01 |
| 测试开头**成簇 `E`**（不是 `F`） | `kb/09` KB-ENG-53：先怀疑**环境**（缺 `--basetemp`），不要先怀疑代码 |
| **本地全绿、CI 红** | `kb/09` KB-ENG-57（时区/大文件/顺序）· KB-ENG-70（判定面 ≠ CI 检出）· KB-ENG-111（**结构代理失衡**：被部分跟踪的 gitignored 顶层，前提变了代理不报警） |
| 守卫**注入了却不报红** | `kb/09` KB-ENG-65（三种假绿形态）· KB-ENG-66（只改注释不算）· KB-ENG-81（两侧同判据 = 双向自洽的假绿） |
| 测试**只在特定日历日变红** | `kb/09` KB-ENG-56：真实运行日驱动 ⇒ 有注入参数就必须用 |
| 门禁**全绿但新文件根本没被扫到** | `kb/09` KB-ENG-60（扫描面写窄） |
| 门禁**一上线就红满天**、随后被整体无视 | `kb/09` KB-ENG-58（**过载 = 被忽略**） |
| 下游过滤后**条目全丢** | `kb/10-data-contract-pitfalls.md` KB-ENG-22（写入侧须落齐下游依赖字段） |
| 队列消费**旧条目被永久挤出** | `kb/10` KB-ENG-23（先过滤再 limit） |
| 时间戳**跨格式/时区比较错判** | `kb/10` KB-ENG-50（一律先转 epoch） |
| 时间呈现**报错量级**（如「期间收益」当天收益用） | `kb/09` KB-ENG-78（量纲必须进字段名与标签） |
| 中间层块级导致 **overflow 全失效** | `kb/03` KB-ENG-16（flex 高度链逐层接力） |
| **「服务在跑」但跑的不是新代码** | `AGENTS.md` §5 + T1：复验须用能区分新旧代码的探针 |

### 0.4 既有决定与复核边界

历史决定不自动产生施工义务，也不阻止有据修正。禁止真实券商/真实自动下单、不新增付费或权限、未知资产不删除等仍按当前授权边界执行；结构、交互、策略假设与文档规则可按实施校准复核。安全/恢复低频能力不能据低使用量删除。

旧 P0/P1/P2 子编号和 §6.5b 等是历史定位，不能当现行任务指针。恢复当时文字查固定 Git 版本；当前任务从阶段总账进入。已交付实现先核消费者和残余缺口，不因旧文标题重新开发。

### 0.5 硬约束速查（最常用的事实 → **唯一权威处**）

| 约束 | 唯一权威 |
|---|---|
| **北京时间** | `backend/app/core/bjtime.py`：`beijing_now[_naive]()` / `beijing_today()`；**禁止 `date.today()`**；绝不发不带标记的 naive UTC 串 |
| **三态 > 二态** | `unknown` 显式「未判定」、缺失 `--`；文案单点 `push_cards.tri_text` / `apps/web/lib/format.ts` 的 `triText` |
| **`current_time` 不可信** | 一律 `date "+%Y-%m-%d %H:%M:%S %Z"` 实测（KB-TRADE-01） |
| **统计与对账** | 比较同一 Git 跟踪面、版本、环境与实际测试 ID；路径含中文时用 NUL 分隔读取。passed/skipped 分开，差额逐项解释，不将历史“恒差”写成永久事实 |
| **产物落点** | 按用途进项目 `skills/`、`scripts/`、`docs/`、忽略的 `artifacts/`；用户全局目录不属本任务授权（`kb/11` §4.1） |
| **删除** | `scripts/safe-trash.sh <路径> --reason "..."`；**禁 `rm`**（`kb/07` §6.2） |
| **文档体检** | `python3 scripts/doc-health.py`——**每个功能点收尾必跑**，有问题 `exit 1` |
| **报告交付** | `scripts/reports/md-report-html.py` 渲染（**收尾最后一步才渲染**）+ `scripts/reports/md-html-parity.py` 对账 |

### 0.6 具体问题速查（比主题更细的一步跳）

| 想知道… | 看 |
|---|---|
| **用户教过的选股规则/历史教训/拍板过的决策** | **`kb/00-INDEX.md`（知识库，KB-ID 引用制）** |
| **盘后复盘怎么自动跑（不依赖会话）** | **后端常驻调度承担**：`review-scheduler`（交易日 **15:30**，`ASHARE_REVIEW_SCHEDULER_ENABLED`）+ `picks-intraday-review`（15:35），日程可见 `GET /api/system/schedulers`，产物 `data/review/reports/YYYYMMDD.json`。⚠️ **独立 launchd 通道已拆除**（2026-09-12，裁定 B）：`~/Desktop` 属 macOS TCC 保护目录，launchd 进程无权访问 ⇒ `runs=4` 全 exit 126、**零产出**；教训见 `kb/09-verification-pitfalls.md` KB-ENG-62 |
| **怎么执行一次复盘（LLM SOP）** | **`kb/06-review-framework.md`（七阶段复盘框架 v1.0，复盘任务先读它）** |
| 趋势转龙头连板的成因实例 | `summary/stock-strategy.md`（百大集团六维剖析+规律提炼，KB-STOCK-23/24 实证） |
| 新闻/事件模块排查与改造 | `summary/architecture-design.md` §4 + `kb/03-engineering.md`（根因/刷新方案/判定状态机/弹窗改造；剩余待办见 `retro-and-gaps.md` P0-2~P0-5） |
| 系统全貌/技术栈/模块清单 | `archive/PROJECT-MASTER.md`（08-29 基线）+ 本 INDEX |
| 全面审查结论/重构执行计划 | `summary/review-governance.md`（09-08 四路深扫：死代码/孤立端点/闪现根因/猎场融合方案/P0-P2 计划） |
| 任务四后历史架构与当前实施修订 | `summary/system-final-blueprint.md`（历史基线）→ `implementation-plan.md`（当前修订）→ 阶段任务 |
| AI 助手怎么升级成"大脑+执行层" | `summary/ai-evolution.md`（09-08：权限分级 L0-L3/四道拦截/任务中心+复盘+告警+参数+日志模块设计/研究页下线方案） |
| AI 助手更名论证（已定稿「交易智能体」） | `summary/ai-evolution.md`（09-09 P2-1：已是"进化体"的能力盘点+命名三选项+拍板记录「交易智能体·自主进化体」） |
| LLM 微调/训练资料与结合方式 | `summary/ai-evolution.md` §7（09-09：FinGPT/FinGLM/RD-Agent 资料清单+A 股实证基准+三层结合路径，近期零新增付费依赖；层 2 待算力 → 总账 P2-8） |
| 控制台三模块梳理（任务中心/参数/告警） | `summary/review-governance.md`（09-10：三模块定位+实证数据+告警运作逻辑白话版+规则评估与优化建议） |
| 板块资金页核对 + 瀑布流改造 | `summary/review-governance.md`（09-10：东财 vs 同花顺板块体系差异实证（非 bug）+ 列表→卡片瀑布流+滚动分页改造记录） |
| 自主迭代与参数晋级的当前边界 | `implementation-plan.md` §6；W05/IMP-052 与 W04/IMP-020；`summary/ai-evolution.md` 仅提供历史设计与已有实现背景 |
| 数据源怎么选、备源顺序、**哪些源没被用满** | `data/data-source-comparison.md`（实测对比）→ `data/data-sources.md`（接入策略 + **§8 使用度审计**） |
| 某个 API 端点的参数 | `system/api.md`（⚠️ 端点计数停在 09-01/92 个，以 `/openapi.json` 为权威） |
| 每天怎么跑复盘 | `review/daily-review-sop.md` → `review/daily-review-checklist.md` → `daily-review/`（逐日存档） |
| 情绪/题材方法论 | `strategy/theme-sentiment-methodology.md` / `strategy/sentiment.md` / `strategy/theme-prediction.md` |
| 因子库是什么、怎么用 | `summary/factor-system.md` → `strategy/factor-lifecycle-governance.md` |
| 实盘历史方案为何搁置 | `archive/live-trading-guosen-plan.md`（历史参考，禁止据此执行真实券商接入） |
| 两个新仓库评估 / 自动化精简 / 助手大脑化 | `summary/ai-evolution.md`（09-08；**主体已落地**，状态见 `plan-registry.md`） |
| 策略进化（信号健康 / 筹码引擎 / 复盘闭环） | `summary/ai-evolution.md`（09-08；**P0 全落地**：`backend/app/picks/signal_health.py` + `backend/app/market/chip.py` + `backend/app/review/strategy_health.py`；P2 按设计延后） |
| trading 分组仓库值不值得用 | `kb/05-repo-tracker.md`（A/B 证据分级 + 台账 diff） |
| trading 分组新增 star 怎么处理 | `summary/ai-evolution.md`（09-09 diff：22 仓去重已评后真新增 8 仓；告警时区修复+KB 补录+分级计划） |

## 1. 总纲与运维

| 文档 | 摘要 |
|---|---|
| **kb/** | **知识库（09-09 建库，唯一权威）**：`kb/00-INDEX.md` 总索引 → 选股知识 KB-STOCK / 交易教训 KB-TRADE / 工程教训 KB-ENG（**按子类分四册**：`03-engineering.md` 应用与设计 / `09-verification-pitfalls.md` 验证层 / `10-data-contract-pitfalls.md` 数据契约 / `08-tooling-pitfalls.md` 工具操作速查）/ 决策 KB-DEC；沉淀红线=对话中当轮入库；**文档治理流程见 `kb/07-doc-curation.md`（v1.6）**，**判据标准（「好文档」七条判据 / KB 收敛标准 / 卡帕西三层映射 / 非 docs 面治理）见 `kb/11-doc-catalog.md`**；体检一键跑 `python3 scripts/doc-health.py`。**状态语义：示例/题材案例一律 `📎`，不得与 `✅ 已落地` 混用**（KB-DEC-019） |
| **`skills/`、`scripts/`、`artifacts/`** | 项目中性唯一落点：技能 / 复用工具 / 本地证据与恢复资产；应用专属目录已退出并由 `scripts/workspace-hygiene.py` 阻止复活，历史迁移见 `archive/platform-directory-migration-20260920.md` |
| **plan-registry.md** | 历史计划去向；当前任务从 `retro-and-gaps.md` §6.0 进入所属阶段，按真实价值准入 |
| **retro-and-gaps.md** | 阶段总账：只维护领域目标、优先级及链接；任务在 stages 单点，现场在 handoff，当前修订在 implementation-plan |
| **strategy/strategy-registry.md** | **策略级登记册（09-10 建）**：5 条策略键（`daily_picks` / `intraday_watch` / `pullback_reversal` / `triple_volume` / `two_thirty_five`）｜§0 **因子≠策略**辨析 ｜§2 逐条量化证据 + 样本环境 ｜§3 处置台账 ｜§4 衰减监控机制（P1-37/38/39）。**监控端点 `GET /api/picks/strategy-health`** |
| **summary/** | **主题汇总目录（8 份）**：`stock-strategy`（选股策略）/ `factor-system`（因子体系）/ `data-market`（数据源行情）/ `architecture-design`（架构设计）/ `ai-evolution`（AI 进化）/ `review-governance`（复盘治理）/ `system-final-blueprint`（任务四后目标架构与验收总纲）——查主题先看这里 |
| archive/PROJECT-MASTER.md | 08-29 历史技术基线（只读；当前入口见 INDEX / handoff） |
| system/architecture.md | 数据流与分层设计 |
| architecture-redesign.md | 08-31 模块盘点与重构方案（实测驱动）· **已归档**（优先级清单已全部清零）→ `archive/architecture-redesign.md` |
| system/deployment.md | 部署与运维（本地开发 8000/3000 纪律） |
| system/api.md | REST API 按域检索手册（端点/路径计数以 /openapi.json 为权威） |
| system/websocket.md | WS 协议（必须直连后端，不走 Next 代理） |
| system/mcp.md | MCP 工具体系清单 |
| system/data-dictionary.md | 数据对象审计字段约定（source/quality） |

## 2. 数据源

| 文档 | 摘要 |
|---|---|
| data/data-source-comparison.md | 四源实测对比与选型（改数据源前必读；改完回填） |
| data/data-sources.md | 接入策略：主源→备源→降级链；**§8 全量清单与使用度审计（2026-09-16）**：fuyao 59 端点 × 已接 23 · TDX 20 数据类方法 × 已用 3 · 零调用/仅测试/单点清单 · **可替代 5 处（首推逐笔改走 TDX）** · 推翻 3 条既有结论 |
| archive/external-data-source-survey-2026-09-11.md | **外部付费源调研**（Tushare / FTShare / KlineShare / QuantDash + PTrade）：价格档位 + 能力矩阵 + 与本项目对比。§0–§7 ⚠️ **均为官方公开信息，零实测**；核心结论：**四家均无 L2（最高五档），且两家与我们同源（东财/新浪/ths 二次聚合）**。**§8 = 2026-09-16 复评（本机实测）**：FTShare 免费档实测（v1 通 / v2v3v4 全 403）、**涨停池为本仓 ths 的严格超集（交集 32/32）但无涨停原因**、1000 次/天预算精算（≈191/日，余量 5×）、**四家横向对比 ⇒ 无任何付费源可覆盖免费档 161 项，且 FTShare 免费档本身 ¥0**；**决策 = 不升级付费档**；**§8.10 = 付费档横向排序**（KlineShare 权限矩阵从 `public/v1/catalog` 实测解出；**FTShare 付费档真实价格 19:33 到手** ⇒ **最优单档 = FTShare 基础版 ¥159/月（¥1,908/年）**，**单项最省 = Tushare 研报库 ¥500/年**，**最差 = FTShare 专业版 ¥799/月（零增量且最贵）**）。🔶 未决建议见总账 `retro-and-gaps.md` P2-31 |
| orderbook-source-evaluation.md | 五档盘口数据源评估（08-29/30，ths 无五档结论）· **已归档** → `archive/orderbook-source-evaluation.md` |

## 3. 方法论与功能设计（现役）

| 文档 | 摘要 |
|---|---|
| strategy/theme-sentiment-methodology.md | A 股热点题材与情绪分析方法论 v1 |
| strategy/sentiment.md | 情绪指标清单与阶段判定（§5.5）+ **历史误判案例库**（非交易日回退自指 bug 全链条 + 九条误判链 + 17 项优化清单状态） |
| strategy/theme-prediction.md | 新题材预判方法论 |
| research/limit-up-dragon-research.md | 历史涨停/强连板/龙头全样本研究蓝图：点时样本、失败对照、Jev语义特征、walk-forward/盲测与猎场回流 |
| review-methodology-20260910.md | 外部复盘视角「五层提问框架」 · **已删除**（净化并入 `kb/06-review-framework.md` **附录 A**，仍标非权威、不替代 KB） |
| kb/07-doc-curation.md | **文档整理方法论**（09-10）：分层模型/归属三问/精华五要素/计划压缩归档/双索引串联/合并拆分/三级淘汰/试运行条款 + 本仓库现状体检与首批整理候选。整理文档类任务先读。 |
| longhu.md | 龙虎榜模块设计（口径与席位） · **已删除**（精华见 `summary/data-market.md`） |
| fund-flow-redesign.md | 大盘→板块→个股三级资金流重构（09-07） · **已删除**（P0 已实施；P1/P2 待办见 `retro-and-gaps.md` §6.2/6.3） |
| linkage-design.md | 跨页面联动系统设计 · **已删除**（精华见 `summary/architecture-design.md`） |
| stock-picking-system-2026-09-02.md | 选股 2.0 设计与落地计划 · **已删除**（精华见 `summary/stock-strategy.md`） |
| stock-picking-backtest-2026-09-02.md | 选股 2.0 网格回测（120 交易日） · **已删除**（精华见 `summary/stock-strategy.md`） |
| stock-picking-backtest-2026-09-02-200d.md | 同上，200 交易日扩展窗 · **已删除**（精华见 `summary/stock-strategy.md`） |
| picks-intraday-fusion-assessment.md | 精选×盘中跟踪融合可行性评估 · **已删除**（精华见 `summary/stock-strategy.md`） |
| archive/picks-replay-baseline.md | 精选 60 日回放基线快照（**结论版 116 行**：稳定性四策略对照 / 组合轨迹 / 梯队阶段分布）｜逐日明细（784 行表）已外移 `data/picks/replay-baseline-detail-20260831.md` |
| picks-stability-sweep.md | 精选参数敏感性快照（08-31 区间）· **已归档** → `archive/picks-stability-sweep.md` |
| picks-take-profit-design.md | P1 冲高止盈提醒设计（待拍板） · **已删除**（精华见 `summary/stock-strategy.md`） |
| factor-library-design.md | 因子库建设方案（唯一口径/评估准入） · **已删除**（精华见 `summary/factor-system.md`） |
| strategy/factor-lifecycle-governance.md | 因子全生命周期管理制度 |
| strategy/backtest-rules.md | 回测强制禁令（代码级校验） |
| strategy/risk-management.md | 风险拦截位置与规则 |
| halt-check-risk-analysis.md | 停牌核查/异动对情绪的传导设计 · **已删除**（精华见 `summary/stock-strategy.md`） |
| review/review-agent.md | 盘后复盘 Agent 架构说明 |
| review-strategy-update-2026-09-02.md | 复盘策略增量迭代方案 · **已删除**（精华见 `summary/review-governance.md`） |
| review/daily-review-sop.md | 每日复盘 SOP（怎么判） |
| review/daily-review-checklist.md | 每日复盘执行清单（逐项勾） |
| theme-ladder-20260907 | 已删除（HTML 时点报告，结论在 daily-review/2026-09-07） |
| hotspot-pipeline-design.md | 热点消息捕获→传导→映射建设方案（09-07） · **已删除**（P0 快讯流已实施 c84d382；P1/P2 待办见 `retro-and-gaps.md` §6.2/6.3） |
| nfp-ashare-validation.md | 非农意外差→A 股适用性验证报告 · **已删除**（精华见 `summary/data-market.md`） |
| system/llm-gateway-probe.md | LLM 网关健康探针（claude_cli 别名监控） |
| strategy/factor-candidates.md | 候选因子登记册（原 `app/factors/candidates.py` **已迁出删除**，纯文档；治理见 factor-lifecycle-governance） |
| factor-ic-review-20260908.md | tech_score 权重 IC 复核（500 只等步抽样 × 近 60 交易日，T+5 Spearman） · **已删除**（精华见 `summary/factor-system.md`） |

## 4. 本轮调研（2026-09-07）

| 文档 | 摘要 |
|---|---|
| repo-deep-research-20260907.md | trading 分组 17 仓深度调研 + 功能盘点 + 孤立功能清单 · **已删除**（结论与台账 diff 见 `kb/05-repo-tracker.md`） |
| archive/live-trading-guosen-plan.md | 国信 miniQMT 实盘接入：门槛/分步/风控红线/灰度序列（**已搁置 09-07，恢复条件见文档头**） |

## 5. 健康检查与复盘存档

| 文档/目录 | 摘要 |
|---|---|
| system-review-2026-09-02.md | 09-02 全量体检与优化方案 · **已删除**（精华见 `summary/review-governance.md`） |
| plan-review.md | 08-31 全盘计划复盘与整合清单 · **已归档**（已被 09 系列审计取代）→ `archive/plan-review.md` |
| **retro-and-gaps.md** | 阶段总账：只维护领域目标、优先级及链接；任务在 stages 单点，现场在 handoff，当前修订在 implementation-plan |
| system-review-20260909.md | 09-09 系统全面审查（基线含猎场批次 A+B、进化 P0/P1） · **已删除**（精华见 `summary/review-governance.md`） |
| hunting-review-20260909.md | 09-09 猎场实盘台账复盘（37 只 × 涨停池 34 家逐笔对照） · **已删除**（精华见 `summary/review-governance.md`） |
| daily-review/ | 逐日复盘报告（YYYY-MM-DD.md） |
| repo-watch/ | 仓库周期性跟踪周报 |
| evolution/ | 进化议程每日执行日志（YYYY-MM-DD.md） |
| push-templates/ | 飞书推送卡片模板（v2 版式定稿） |

## 6. archive/（只读历史）

> 已完成/被取代的时点性文档。**结论已吸收进现役文档或代码，引用前先确认未过时。**

- 09-03 star 审计系列 ×6（github-repo-audit-*、github-llm-agent-audit、ths-repo-gap-analysis）→ 已被 `kb/05-repo-tracker.md` 台账取代
- github-stars-trading-analysis.md（09-03 收敛表）→ 同上
- realtime-broker-feasibility-2026-09-01.md → 已被 `archive/live-trading-guosen-plan.md` 取代
- architecture-linkage-plan-2026-09-01.md → **已删除**（计划已执行/被现役产品与架构方案吸收；精确原文从 Git 恢复）
- minute-chart-plan / assistant-optimization-plan / ui-redesign-plan → 计划已执行进代码，但仍有活引用，迁移引用后再按 kb/07 §6 退休
- 更早：full-project-review / system-review / theme-audit（09-01 批次）
