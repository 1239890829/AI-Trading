# W08 文档、Skills、门禁与目录退出

> 定位：由 [总账 §6.0](../retro-and-gaps.md#60-阶段索引) 唯一索引，任务状态只在本页。2026-09-18 规划批曾为 DESIGN_ONLY；该限制只描述那一轮。当前派工以最新 `master` 的 handoff 与本页任务状态为准；旧测试、额度、清理记录为各自版本的历史证据。
> 调度：本页 W 编号只表示领域归属；实际施工必须按 [总账 §5.9](../retro-and-gaps.md#59-阶段门优先级与跨阶段治理) 的“阶段门 → 门禁角色 → P0/P1/P2 → 门内序 → 硬依赖/效果前置”。页面上下顺序不是施工授权。

- **阶段目标**：阶段账本单点维护、重大决策传播、开放世界创新发现/准入前治理、全系统机制生命周期/衰退重验、工作区卫生/证据保留期、持续合理节省成本，并按实际用途迁出平台绑定内容；不重建平行任务系统或自动采用平台。
- **依赖边界**：按当前切片范围、消费者和恢复点核，不要求整阶段清零。目录中的未知、私人或跨项目资产不能批量删除。
- **排期**：本领域治理多为 GX/P1；GX 只可伴随当前主门，结构迁移按其任务 G 门执行，不能以整理名义越过业务阻断门。

## GOV-022

**账本短提示接续、重大决策传播与旧互调方案退出**

- **状态**：部分完成
- **优先级**：P1
- **阶段门**：GX
- **门内序**：20
- **门禁角色**：持续治理
- **依赖**：无
- **效果前置**：无
- **方案依据**：用户完全放弃双端自动化、采用账本短提示；U40 限定 2026-09-18 规划批只做理论/计划；U49 要求主动发现隐藏缺陷成为默认审核门；U50 要求 Codex 不可用时能在用户明确授权下进入受控降级全权闭环。
- **范围**：固定 handoff 与阶段唯一记录串联网页规划审核/Codex执行；`ashare-ledger-continue` 同时承担新会话接手/上下文恢复。U49 要求继续选刀前、审核放行前、阻断项闭环/阶段门切换后、事故/用户纠偏后运行有界 Proactive Discovery Gate，并由 handoff 留主动审计回执。U50 新增 `DEGRADED_FULL_CONTROL`：只在用户明确授权时启用，网页端可全程操控，但每个 PR 必须 distinct exact-HEAD `DegradedRelease` + required CI + release_check + post-merge CI，不伪装独立 Review。重大模型、工具链、架构、产品语义、协作或治理决定按 plan-registry §1.1 传播到全部受影响权威面。旧 App Server 派工、浏览器唤醒、自动审核回执与 Bridge 控制不属于候选建设路线。
- **验收**：已退役原型无活动消费者悬挂引用且可恢复；新会话能只凭最新 master 的固定读取链恢复方案/Jev/账本/下一动作；重大决策有“已更新/不适用+理由”的传播核对；每轮 `Preflight` + 当前模式 release receipt 能回答“谁在何时扫了什么、发现/未发现什么、是否改变门/验收/下一刀”：正常模式为独立 `Review`，降级模式为明确作者全权的 `DegradedRelease`；两者不得互相冒充，且缺 U49/U50 传播面的明显漂移能被 doc-health 判红。真实双端使用未发生不标整项完成。
- **证据**：历史26a0c44到9d439c5批检查过跟踪源、原项目scripts/.github、LaunchAgents与进程命令；通过safe-trash成对移除旧脚本及44项专属测试、核SHA并做一次脚本恢复再退出。对应retirement.json及Git保留；不是本次新查，也不宣称整台机器不存在未知外部引用。
  - 2026-09-19 v9.4 治理切片 PR #41 新增传播契约与 doc-health R 守卫；首次主动扫当前版本指针即额外抓出 `summary/pick-signal-chain.md`、`summary/review-governance.md` 两处残留 v9.3，证明人工记忆不足以防漏，并已纳入同一守卫面。
  - 2026-09-20 用户追问“为什么不说就不能主动发现”触发治理复盘：确认原流程虽然写有反证/持续演进，但没有把隐藏缺陷扫描设成每轮强制触发，因此同日升级为 v9.10/U49，并同步 Governor v1.2.0、continue/handoff Skills、协作规范、AGENTS、plan-registry、INDEX、handoff 与 doc-health 传播守卫。
  - 同轮 U49 Preflight 又确认 `release_check.py` 只校验 GitHub reviews/threads/CI，不校验项目级网页 Review 回执本身。PR #63 exact-HEAD Review 已在合并前存在且 post-merge master CI 全绿，因此不是该 PR 的放行事故；但流程缺口真实存在。后续治理切片为 `release_check.py` 增加 PR Conversation exact-PR/exact-HEAD Review 回执校验与正反测试，并明确它只做流程一致性、不宣称 GitHub 身份隔离。
  - 2026-09-20 用户进一步指出 Codex 无额度后网页临时代执行会被“作者不得自产 Review”永久自锁，并明确授权“降级后由网页全程操控”。据此升级 v9.11/U50：正常双角色保留；降级模式以 `DegradedRelease` 代替不存在的独立 Review，但 required CI、release_check、U49 作者反证、post-merge CI 与分支清理不降级。
  - 2026-09-21 多窗口审计新增两项执行纪律：① `待条件` 任务必须在时间/事件条件出现时重新计算阶段门，BUG-020 已在真实交易日漏过一次条件窗口；② 同一 task/sub-slice 在降级全权模式下仍只允许一个 active worktree/PR（single-writer），替代分支确定后先提炼独有差异再关闭/清理，避免 IMP-020 #88/#90/#91/#92 式并发竞争。
- **处置依据**：人工触发已代替自动发送需求；继续开发互调增加权限/状态/维护成本。退出不等于自动链成功，废弃子目标不得重新派工。
- **下一步**：U50 已有真实 PR 验证；持续验证 U49/U50 的发现质量、误报与成本。新增治理守卫重点不是再加审批，而是**条件激活 + single-writer + merge 后清理/运行态接收**：每次 continue 先把 `待条件` 的外部条件重算；同一子片禁止并发活动分支；merge 后把 branch/worktree/runtime status 分开核对。GX 不借治理跨门。
- **恢复**：26a0c44和项目回收记录保存旧文件；恢复只作调查且不覆盖目标，不恢复旧自治目标或撤销金融/CI保护。
- **分工**：正常模式网页方案/账本/独立审核，Codex执行；`DEGRADED_FULL_CONTROL` 激活时网页全权闭环，用户只需明确进入/退出降级或提出重大取舍。Bridge个人App/扩展/对话未卸载删除，不影响本流程。

## GOV-024

**TypeSafe / Jev 治理、额度度量与回退策略**

- **状态**：部分完成
- **优先级**：P1
- **阶段门**：GX
- **门内序**：30
- **门禁角色**：持续治理
- **依赖**：无
- **效果前置**：无
- **方案依据**：docs/ai/jev-integration.md；2026-09-19 官方 Use Case Map 与本仓真实 smoke。
- **范围**：Jev 只做 bounded 语义判断、验证与条件能力路由；确定性金融规则、权限、撮合、风控与真实执行仍由代码负责。统一 HTTP/key 入口为 backend/app/core/jev_client.py，测试默认禁真实触网。
- **验收**：不新增第二套凭据/endpoint；隐私输入失败关闭；真实调用只在能改变下一步或减少更贵模型成本时保留；全局 model/effort 自动切换未证明价值前保持停用。
- **证据**：PR #31–#34 已依次合入 Jev adapter/影子路由、价值审计与 capability routing 收敛、Universal Verification/gold-set、预标注与审核优先级；jev-1.13.0 smoke、JevRouter 权限过滤、metadata-only usage 均已有证据。旧 jev-route 因不能真正切宿主模型且固定增加一轮调用已退出。2026-09-21 审计时项目 ledger 为 172 calls / 169 success / 3 failed，主用途 alert_triage 与 event_llm_aux；bounded live smoke 再次成功。网页 ChatGPT 当前**没有原生 TypeSafe tool**，但在用户授权的本机连接下可通过统一 `jev_client` 调用，本轮已实证；Codex 更适合作为代码实施 owner 并可加载 TypeSafe Agent Skill，但 Jev 仍只承担 bounded decision/review，不替代 Codex 或确定性测试。
- **下一步**：以 RSH-030 人工金标准和长期 token A/B 决定 shadow/cascade 与组件去留，不以 demo 或单次 confidence 调生产阈值；额外验证 assistant tool-router 的真实 shadow 调用/telemetry。对 RSH-026/IMP-044/IMP-020 这类高影响多轮任务，允许在 release 前按价值选择一次 Jev semantic review，但只作旁路反证，不把“没调用 Jev”本身判为缺陷。
- **恢复**：任一 Jev 组件失效时回退确定性规则/现有 LLM 路径；不扩大权限、不修改真实交易硬门。

## GOV-025

**开放世界外部情报、创新雷达与证据准入治理**

- **状态**：部分完成
- **优先级**：P1
- **阶段门**：GX
- **门内序**：40
- **门禁角色**：持续治理
- **依赖**：无
- **效果前置**：无
- **方案依据**：docs/ai/continuous-evolution.md；implementation-plan v9.5 §6.1；用户要求系统不依赖人工喂入新技术，同时不得按热度盲目采用。
- **范围**：持续发现 AI/模型/Agent、量化/选股方法、数据源、研究方法、工程/安全/产品与规则变化；按来源等级、硬门、E0–E5 证据梯度筛选。雷达只拥有发现/拒绝/观察/实验建议，不拥有安装、付费、权限扩大、生产阈值或策略准入权。
- **验收**：新会话可调用 ashare-innovation-radar 从最新 master 恢复现有基线并产出候选卡；外部宣传与已核事实分开，外部内容按不可信数据处理；SHORTLISTED/LAB 有 falsifier、基线、成本/隐私/许可/恢复以及 `last_reviewed/review_due/experiment_budget/stop_rule`；真实实验归已有 stage；重大采用触发 plan-registry §1.1；雷达同时支持问题驱动、前沿驱动、反证驱动并把 missed-signal 反馈回查询/来源；雷达自身有有用候选率、漏发现、噪声和成本复评，不能只积压 WATCH。
- **证据**：2026-09-19 首轮外部核查已覆盖 TypeSafe/Jev、Microsoft RD-Agent/Qlib、OpenHands、MCP Registry/A2A、QuantConnect LEAN/vectorbt、TSFM/金融 Agent benchmark 等方向；结论按候选/参考/观察分级，没有把外部项目自报收益写成本项目效果。PR #42 已把 v9.5 continuous-evolution 蓝图、ashare-innovation-radar Skill、反固化/不可信外部内容/证据过期与停止规则、传播守卫合入 `master`（merge `d51f3209`）；准确 head `b3b239ef` 的 backend/frontend/docs CI #381 与 release_check 全绿。 2026-09-19 已启用每周一次、仅高价值变化才通知的 ChatGPT 条件雷达作为低频真实试运行；它只做外部发现/反证提醒，不安装、不付费、不扩大权限、不创建施工或自动采用。
- **下一步**：从 `master` 用真实雷达轮验证发现质量、漏发现与噪声；优先挑能挑战当前真实瓶颈且成本低的少量 SHORTLISTED 做 E2/E3，不为填清单实施。先以低频、只通知高价值变化的外部监测积累证据；多轮证明净收益后再决定是否提高频率或增加自动实验。
- **恢复**：停用雷达或降低频率不影响生产；原始扫描留忽略 artifacts，未准入候选不得改变运行配置、策略、权限或真实外发。

## GOV-026

**工作区卫生、证据压缩与资产生命周期门禁**

- **状态**：部分完成
- **优先级**：P1
- **阶段门**：GX
- **门内序**：45
- **门禁角色**：持续治理
- **依赖**：无
- **效果前置**：无
- **方案依据**：U46；用户要求已完成/过时方案在精华提炼后退出，本项目临时克隆、缓存、测试目录和完成轮次大产物不得长期占空间。
- **范围**：统一治理五类资产：① EPHEMERAL 当轮临时克隆/worktree/pytest basetemp/一次性探针；② REGENERABLE_CACHE 如构建缓存与解释器缓存；③ COMPACT_EVIDENCE 日志/XML/JSON/manifest/delivery 等可长期追溯的小证据；④ RECOVERY 仅在相关变更未稳定前保留的恢复副本；⑤ LIVE_OR_EXTERNAL 业务数据、用户/跨项目仓库、运行中/脏工作树、插件管理器拥有或所有权不明资产。文档退休仍按 kb/07：精华和未做项已迁、活引用清零、无活动依赖、Git/PR 可恢复、删除后文档门禁通过。 文档物理落位同时纳入治理：`docs/` 根只保留 INDEX/handoff/总账/实施计划/计划注册/协作协议 6 个控制面；现役正文进入已登记领域目录，时点/搁置件进入 archive，禁止“已登记但仍平铺根目录”。
- **验收**：独立 `scripts/workspace-hygiene.py` 已落地并接入 CI/交接，扫描项目树及根/子项目 `.gitignore`；应用专属目录/隐藏规则判红，依赖环境剪枝，配正反测试。每轮 handoff 仍先把完整测试目录压缩成“紧凑证据 + 必要恢复入口”再清理可再生目录；未知/跨项目/运行中/脏工作树/业务数据默认 fail-safe 保留。tracked/独有内容走 safe-trash；白名单可再生项可直接清理。 `doc-health` O2 另校验 docs 根目录白名单和顶层分类白名单，配“合法分类 / 根目录越界 / 未知目录”三类自证测试；B2 按引用文件自身目录解析 Markdown 相对链接，阻断分类迁移后的层级断链；INDEX 编目闭包仍由 O 项独立校验。
- **证据**：2026-09-20 网页侧只读盘点发现主仓约 10GiB，其中 artifacts/runs 约 3.2GiB；现役跟踪文档没有指向嵌套 pytest 沙箱，顶层任务目录另有日志/XML/JSON/manifest/delivery 等紧凑证据。确认主仓无活动进程后，同轮实际清理 62 个 artifacts/runs/*/pytest* 沙箱约 3.119GiB、可再生 apps/web/.next 约 1.1GiB，以及不进入 node_modules/backend venv 的项目解释器/测试小缓存约 13MiB；artifacts/runs 降至约 56MiB，主仓约 10GiB→5.8GiB。/private/tmp 中确认无活动进程引用的旧 v9.4/v9.5 验证克隆与项目专属 pytest/Jev 临时目录另清理约 1.47GiB。约 818MiB node_modules、约 691MiB 后端 venv 属昂贵依赖环境而保留；8 个既有未跟踪运行/复盘证据、跨项目仓库、活动开发仓、业务 data/parquet、平台退出恢复包、插件管理器目录均未动。 随后最终一致性复核把其中 4 份 `docs/daily-review` / `docs/evolution` 的 09-17/09-18 时间序列证据通过 PR #50 纳入 Git；剩余 4 个 backend/data 运行 JSON 继续按本机运行数据保留、不进仓库。 同日文档结构治理将 docs 根 Markdown 从 36 份收口到 6 份控制面：26 份现役正文分别进入 system/data/product/strategy/ai/review/research，4 份时点/搁置件降入 archive；全仓显式旧路径清零，主仓相关回归 222 passed，嵌套 hithink-finance 文档契约 15 passed，并通过真实失败修正验证了嵌套项目不能按主仓字符串机械迁移。
- **下一步**：持续治理：每轮交接运行 workspace-hygiene + doc-health；若命中应用专属目录/隐藏规则、大型临时资产、docs 根目录回堆或未知分类目录，当轮修复。2026-09-21 审计发现该纪律在 RSH-026/IMP-020 高频切片后退化：残留多份 22–840MiB worktree/临时仓和关闭 PR 分支。当前轮已提炼 promotion-v2 契约缺口并实际清理约 **2.0GiB** 已合并/已替代 worktree/临时克隆 + **4.3GiB** 过期 RSH-026 SQLite 一致性副本/备份 + 0B 假库/小型 Python cache；只保留真实 `data/ashare.db`、运行 JSON、依赖环境及唯一未合入的 39MiB `54b7e0c` RECOVERY。9/21 evolution 日志入 Git。长期要求 merge/post-merge 后有机械 cleanup check，而不是等用户再次提醒。它保持 GX 伴随角色，不改变 G0 主任务门序。
- **恢复**：tracked 文档/规则从 Git/PR 恢复；恢复资产在相关变更合并并完成一次准确版本复核前不得清；本机业务数据与外部资产不进入自动删除面。门禁误报时先停自动清理，只保留 report/check 模式，不扩大删除白名单。
- **分工**：网页负责保留/退出语义、账本与审计；执行侧维护/运行 hygiene gate 与清理证据；用户无需逐轮提醒“删缓存”，只有所有权/跨项目/唯一恢复证据不确定时才需要人工裁决。

## GOV-027

**全系统机制生命周期、衰退重验与知识/成本反馈**

- **状态**：待执行
- **优先级**：P1
- **阶段门**：GX
- **门内序**：47
- **门禁角色**：持续治理
- **依赖**：无
- **效果前置**：无
- **方案依据**：U48；implementation-plan v9.11 §6.2；continuous-evolution §5.1/§10；factor-lifecycle-governance 与现有 strategy/KB/Jev owner 体系。
- **范围**：不新建第二套机制总注册表；定义跨域最小生命周期契约，并把字段/审计落回原 owner：策略/战法、猎场情境、因子/过滤器/排序器、KB 结论与消费规则、Jev/LLM 语义能力、能力/模型路由、数据源优先级和关键治理自动化。至少覆盖用途/消费者、机制/版本、Champion/基线、证据与数据窗口、适用/不适用域、反例/falsifier、last_reviewed/review_due/revisit_trigger、衰退信号、成本/延迟/维护/隐私、challenger/shadow、退出/回滚/复活条件；专业指标继续归原领域，不造一个万能总分。
- **验收**：① 能从原登记册/运行证据回答“为什么存在、服务谁、当前证据多强、何时重验、何时降级/退役/复活”；② 猎场/策略/因子使用 point-in-time 全分母、OOS/forward/shadow 与可成交成本证据，知识使用能区分独立增量/重复背景/反例，Jev/路由同时看 human gold/anchor recall、升级率、token/延迟/费用和最终任务质量；③ 发现衰退先区分市场/数据/实现/样本/消费者/机制，不允许 Agent/Jev 自动改生产阈值自救；④ 已完成任务仍可关闭，只有 review/revisit trigger 成立才生成新切片，active backlog 不因“持续迭代”无限增长；⑤ 无证据显示有增量时允许保持现状或退役机制，不以机制数量衡量系统能力。
- **证据**：当前已有局部基础：factor-lifecycle-governance 已定义因子入库/使用/出库/衰减，strategy-registry/RSH-026/IMP-020 提供策略与效果证据，RSH-027 负责知识正文/引用增量，RSH-030 与 Jev usage/gold 负责语义能力质量和成本，IMP-049/053 负责猎场 opportunity/reference/shadow 真实性；这些仍是各自 owner，不因本项登记自动变为已实现的统一运行机制。2026-09-20 又将多轮项目治理方法蒸馏为项目中性 `skills/living-system-governor/`：它只提供 Context Snapshot、KEEP/FIX/MERGE/EXPERIMENT/WATCH/RETIRE、反证/生命周期/成本审计等上层协议，不拥有 stage 状态、生产准入或自动晋级权，因此其入库不把 GOV-027 标成已完成。 同日 v1.2.0 在既有自我进化基础上新增 U49 主动缺陷发现门；Skill 自身继续纳入生命周期：持续观察后续用户长期要求/重复纠偏，但只在稳定复现、明确长期授权或真实复盘证明缺口时蒸馏，并区分 Core/领域扩展/经验反例；一次性要求不固化，新增前先合并/删除重复，实质变化以版本 + Git/PR + 后续行为证据证明。
- **下一步**：本项作为 GX 伴随治理，不改变当前主门。2026-09-21 本轮已用 Governor 做第一次跨域真实审计并产出 `docs/review/chatgpt-multiwindow-audit-20260921.md`：结论是现有 owner 足够，不建总注册表；优先把 condition activation、runtime/master drift、Jev human-gold/assistant usage、IMP-053 fill identity 与 worktree lifecycle 回写原 owner。后续按领域把衰退/知识贡献/成本 A/B 接到既有报告；任何生产降权、阈值变化或策略晋级仍回对应 owner task 与原证据门。
- **恢复**：若统一 metadata/report 增加复杂度却没有改变审核/退役/成本决策，删除跨域聚合层，保留原领域生命周期；历史证据和退役结论不因回滚被抹除。

## GOV-019

**阶段账本与交接单点维护**

- **状态**：已完成
- **优先级**：P1
- **阶段门**：GX
- **门内序**：90
- **门禁角色**：持续治理
- **依赖**：无
- **效果前置**：无
- **方案依据**：原账本改造授权、主方案§11–§12；本项建立时为 v9.3，当前入口以 implementation-plan 最新版本为准。
- **范围**：复用十阶段/旧号去向/当前方案入口；需求、派工、执行事实、审查与恢复各归其位，不重新迁移已建主体。
- **验收**：原123个ID有唯一可核去向；阶段全可达、任务状态/依赖合法、完成有证据、退出有理由，无平行账本。历史编号数量不等于当前任务完成率。
- **证据**：原 Codex 建主体，ChatGPT 于 2026-09-18 补旧号、空交接和终态核查；PR #39 集成进一步修复 fork 后任务 ID 碰撞，冻结 123 个历史源 ID，JEV 治理改用 GOV-024，阶段 P/Q 双向守卫均为 0。集成 head 3e657c6 上 doc-health、public_repo_scan、git diff --check 全绿，PR #39 GitHub CI run #340 的 docs/backend/frontend 三项 required check 全绿。2026-09-19 合并后复核又发现 handoff/协作 Skill 仍把已删除累计功能分支写成“当前固定入口”、部分文档把 9/18 DESIGN_ONLY 写成当前模式；post-merge reconciliation（PR #40）改为 `master` 共享入口并新增回归守卫，作为本项单点维护的后续纠偏。
- **下一步**：本项闭环；今后新增任务只在所属阶段页单点登记，handoff 仅记录当前现场。若再出现编号碰撞、幽灵依赖、并行状态表或退役功能分支重新成为“当前入口”，由现有 P/Q 与协作入口回归守卫阻断并当轮修复。
- **恢复**：固定版本和原文恢复，运行数据与未知平台资产不受本次文案影响。
- **实施步骤**：原号集合不变；当前入口只指方案/阶段；过时勿重开规则按证据复核，安全限制独立；长日志留原持久证据目录，handoff只定位当前模式/工作区/任务。

## GOV-018

**平台目录退出与内容提炼**

- **状态**：已完成
- **优先级**：P1
- **阶段门**：G4
- **门内序**：90
- **门禁角色**：非阻断
- **依赖**：无
- **效果前置**：无
- **方案依据**：主方案§12.4、U46 与用户 2026-09-20 明确要求项目内退出所有应用专属目录。
- **范围**：旧平台内容按用途提炼到项目中性的 skills/scripts/docs/artifacts；第三方整仓只保留已提炼知识，不整包改名。
- **验收**：最终恢复包逐 payload 校验；所有活消费者改到中性路径；旧平台目录无运行消费者且物理不存在；应用专属目录由 workspace-hygiene 本地+CI 阻断复活。
- **证据**：最终恢复包 3,249 payload 全量回读通过，archive SHA-256 `ed57f245ee440bd6b56607b5466ce605cb1d430a6862ac08fb227059cf373324`。18 个项目 Skill、4 个 TDX 探针及独有研究证据完成提炼；UZI/Serenity 本体不迁，方法/经验由 KB 承接。2026-09-20 主工作区确认无进程引用后，`.workbuddy` 与 `.workbuddy-ai` 已物理删除。PR #48 首个准确候选 `86cb841aa1efda2782b2735a7a7baa9f742374dc` 的 GitHub CI run `35481601773` 已确认 backend / frontend / docs 三项 required check 全绿，docs job 同时通过 public-repo scan、workspace-hygiene 与 doc-health。
- **下一步**：本项闭环；后续不恢复旧平台入口。若应用专属目录或隐藏规则再次出现，由 GOV-026/workspace-hygiene 当轮阻断和清理，不重开旧迁移工程。
- **恢复**：需要历史取证时只读 `docs/archive/platform-directory-migration-20260920.md`、Git 历史和本机最终恢复包；恢复到隔离目录，不恢复为项目现役结构。
- **实施步骤**：已完成“清单/恢复点 → 提炼 → 消费者改写 → 守卫 → 物理删除 → 独立验证”的闭环。

## IMP-043

**持续 CI 成本与测试隔离**

- **状态**：部分完成
- **优先级**：P1
- **阶段门**：GX
- **门内序**：50
- **门禁角色**：持续治理
- **依赖**：无
- **效果前置**：无
- **方案依据**：用户持续节省、重点后端CI；主方案§11.3/W08。
- **范围**：每个获准批复核依赖安装/测试/构建/返工与云端触发成本，检查旧优化退化；本次仅完善方法，不做业务计时试验。
- **验收**：有效断言和业务覆盖不减；同条件对照含失败分母、时效和机制。能复用证据时说明准确输入版本及范围；不能把旧测算新测、用跳CI/公开仓库/付费/削弱保护求快。
- **证据**：PR #24/#25/#29及后续本地隔离有历史成果；旧余额60/预留40分钟只是当时快照，不是当前预算。真实余额和工作流触发条件须在未来实际发布前只读核验，不为测量反复触发CI。
- **下一步**：正式实施片先定向收敛，再按实际变更范围核完整检查；纯规划只验资料，未来发布仍满足准确SHA的审核及完整CI。2026-09-21 审计新增“本机 runtime dependency drift”检查：CI fresh install 全绿不代表长期运行环境同步；当前 Next server=v15.5.25，而 package manifest 要求 ^16.3.3，`npm ls` 大量 unmet。下一次受控停服部署时执行 `npm ci` 重建并记录实际版本；Python `pip check` 当前无 broken requirements。npm registry mirror 不支持 audit endpoint，故本轮不能声称 npm audit=0。
- **恢复**：回退优化时保留有效检查，费用/仓库可见性/权限不变。
- **退出与覆盖**：9d439c5减少的44项只属于已退役互调原型，业务测试未删除；安全性移除应用内宿主测试不等于云端测试削减。不宣传退役专属测试为业务覆盖或CI速度提升。
- **历史教训去向**：61bb122的测试注册顺序问题、57a8bd2及前基点的本地日历依赖曾由干净检出暴露；保留原准确SHA/日志和修复证据，今后继续查运行环境/顺序依赖，不把这些长日志复制进每轮交接。

## 已交付基线与持续规则

PR #23/#28/#30的分用途迁移/恢复、#24/#25/#29等成本隔离成果保留，原详细数值与来源可从7f9f4a71c3c16f4fee1a1c728727eb64c7096769恢复。旧编号去向见 [历史处置表](../archive/ledger-transition-20260917.md)。
稳定且无新证据不重复改写治理文档；每轮核本轮范围、真实变更、验证方法和剩余，已完成只留必要结论。纯规划收口不延伸成执行全部工程账本，下一业务片始终需明确派工。
