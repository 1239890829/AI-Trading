# 当前交接：U50 降级全权模式已授权，先收口机制再闭环 IMP-044

**当前模式：`DEGRADED_FULL_CONTROL`（用户于 2026-09-20 明确授权，持续到用户明确退出/恢复 Codex）。** 当前 master 仍为 `aca43e408a9555da43de9e7f42bb10361c92b6e9`；先合入 v9.11/U50 降级发布机制，再把当前 G1/P0 IMP-044 PR #67 同步到新 master，并由网页端完成 exact-HEAD U49 作者反证、`DegradedRelease`、required CI、`release_check.py`、merge、post-merge CI 与分支删除。正常双角色并未废弃：Codex 恢复后用户可明确退出降级，届时重新要求独立网页 `Review`。降级模式只替代“独立 reviewer 必须存在”这一角色条件，不降低阶段门、安全红线、完整 CI、敏感信息/范围、latest master、post-merge 或分支清理要求。BUG-020 仍为 G0 `待条件`，所以当前最低可行动阻断门仍是 G1；在 IMP-044 闭环前不领取 IMP-032/BUG-016/IMP-049/053。


## 1. 固定入口与范围

仓库 `1239890829/AI-Trading`；共享事实固定从 `master` 读取：`AGENTS.md` → `docs/INDEX.md` / `docs/retro-and-gaps.md` → `docs/handoff.md` → 对应 stage。功能分支只是施工载体，合并后可删除，不能再充当固定入口。2026-09-19 本次审计起点为 `master@2b89c0c077b9f0978f4a510217cbecf3976ece8f`（PR #39）；该起点远端仅保留 `master` 且无 open PR。PR #4 与 PR #36 均已 closed/unmerged，旧 `develop` 及累计功能分支已删除；历史事实从 Git/PR 追溯，不再写成当前入口。
2026-09-18 规划复核基点为 `7f9f4a71c3c16f4fee1a1c728727eb64c7096769`。原40条需求、21条Codex来源、37条KB用途、三专题和已有业务成果全部继承；该规划批与后续 2026-09-19 工程/治理合并分开记账。

## 2. 累计规划要求去向（2026-09-18 基线 + 后续增量）

下表以 2026-09-18 规划批为基线，并吸收后续 U41–U49 的新增长期取舍；它是累计规划验收索引，不是第二套任务进度表。各项“覆盖”表示设计与验收方法有实际落点，不表示已经实现或实证有效。

| 用户要求 | 已核定的规划内容 | 唯一主要落点 |
|---|---|---|
| 全功能统筹且小功能不可忽略 | 各模块用途/输入/拥有者/消费者/反馈；按钮、输入、日期、筛选、计数、提示、跳转、取消和后台动作均按最小契约覆盖 | product-closure-design §3–§5；feature-closure-audit；W07 |
| 后台做业务，普通前台清楚易用 | 后端拥有判定/规则/费用/风险/标签/实验，普通前台无运营配置/预警调试/进化控制；必要风险、用户正常操作、表现与本地草稿按用途保留 | product-closure-design §2/§5；W07；W09 |
| 选股提前发现、进入时机和成因 | 首次观察/触发/reference/actionable/实际 shadow fill 分开，盘中随新事实另版重评；先验事实/时间/竞争解释/失效并存；金健只作未定区间示例，不作抓涨或成交保证 | hunting-decision-design §1/§4/§4.1/§7/§8；W03/IMP-053 |
| 不限少数战法/形态，充分用知识 | 驱动/结构/角色/环境/时点/执行域/成熟度开放组合；37条KB及候选登记有用途，未知情境不硬归类，负结果与准入区分 | hunting-decision-design §2/§3/§5；plan-registry §3/§4 |
| 猎场重新设计但不偏离UI风格，其他板块同理 | 沿原组件/字号/亮暗/配色；猎场按机会/跟踪/影子/复盘职责分区，页面锁屏+内部滚动；Drawer/Modal/Popover 按上下文语义选择，动效有目的且后置；工作台、市场、图表、消息、记录和复盘均有独立目标 | product-closure-design §2/§4/§5；hunting-decision-design §6；W07/IMP-050/054 |
| 历史要求叠加、同义去重、新要求保留 | U01–U50及21条原消息ordinal保持；冲突在语义处裁定，不以最新局部问句抹去主任务，历史成果不重做 | implementation-plan §8/§8.1；plan-registry §1–§3 |
| 全面论证后主动补缺、融合、调整 | 现状/最小修补/复用/替代比较，收益/成本/风险/恢复与反证齐备才增删重排；允许不改、拒绝或补证，不机械领下一行 | implementation-plan §6/§7；collaboration-workflow §4 |
| 废弃旧协作自动化，改用账本短提示 | 互调/自动唤醒/自动回执/Bridge依赖退出，历史原型已退役；网页规划审核、Codex执行、用户只触发读取 | collaboration-workflow §1–§3/§7；W08/GOV-022 |
| 更新关联文档、清理无用重复并可追溯 | 当前资料各有职责，旧矛盾集中裁定；清理的是旧施工承诺/重复日志，不删除独有知识、历史证据或业务调度 | plan-registry；W08；Git父版本 |
| 完整流程图供确认，只做本次规划 | 产品图包含市场/独立记录与机会的关联，开发图区分DESIGN_ONLY和明确实施；本轮不执行BUG/研究/组件试验 | product-closure-design §8；implementation-plan §0/§9 |

## 3. 2026-09-18 规划复核发现并修正的残余

①W07原整体依赖IMP-049与“非猎场可独立”正文不一致，改为按消费者列接口前置；不降低任何安全/发布要求，也不因去依赖而授予实施。②W09仍只写U01–U39，补齐U40规划限制；纠正“全链同ID”及“只有首帧可本地”的过强句，按各对象及隐私用途保留独立身份/本地表现。③W08多个历史“本轮”预算、门禁、清理描述混在当前安排，收敛为绑定版本的历史基线与持续规则，明确当前无新业务派工。
以上是跨文档语义收尾，不是重写所有理论。无需重做已经完整的知识映射、原40条需求或业务代码；不把这些文案修正计成业务功能已完成。

## 4. 2026-09-18 规划批验证与未执行事项

本次核对固定基点文件、要求去向、角色/模式、依赖语义、ID/状态保留和Git差异；只改本文件及W07/W08/W09四份已有Markdown，无新增文件/业务任务，也无代码、配置、CI、模型、服务、部署或App卸载。
仅做文本/结构及共享版本核查，不运行本机doc-health、业务测试、React/Canvas、回测或Codex往返；不复用旧3851/151数字作为本轮成绩。实际全仓逐行/运行/效果未验内容仍保留原任务，不能把“图已确认”写成实装成功。
旧26a0c44/9d439c5/29eae6f/7f9f4a及证据路径照原记录保留；本机最后delivery.json曾超时，不能补造存在。原知识八文件的批量注释未重试，其当前用法已由plan-registry集中裁定，不再作为独立编辑欠债。

## 5. 当前接续规则

2026-09-18 规划批已完成理论/产品/大小功能/知识使用/文档治理层面的逐项去向；其 DESIGN_ONLY 边界只描述该历史规划批，不再作为全项目“当前模式”。计划中的实际开发、原始数据/候选实证和运行验收仍按各 stage 的真实状态继续。
Codex收到短提示后先从最新 `master` 重读 AGENTS、handoff、协作规范、总账 §5.9 和对应 stage。网页派工与用户调用 `ashare-ledger-continue` 的“继续任务/继续”使用同一算法：**最低可行动阻断 G 门 → 门禁角色 → P0/P1/P2 → 门内序 → 硬依赖**；`效果前置` 只限制效果主张/晋级，不得被忽略。一次“继续”只授权一个主切片，Codex不得连续扫账本或自行跨门。
文字版完整图以product-closure-design §8为准；展示图片只是解释副本，不是状态源。图必须包含后续获准实施的方向，不得宣称已经自动执行或真实券商下单，不出现普通前台配置/调试中心或后台固定分类上限。

## 6. 2026-09-19 分支收敛结果

PR #39 已把累计协作功能栈合入 `master`（审计起点 merge commit `2b89c0c`）。`codex/plan-led-backlog`、`codex/code-proposal-only`、`codex/research-admission-integrity`、`codex/shadow-review-required` 均由最终累计分支覆盖并在合并后删除；最终累计分支自身也已删除。任务 ID 冲突已按冻结规则收口：历史治理任务保留 GOV-019，Jev 治理使用 GOV-024，Jev IMP-045 / IMP-046 与 RSH-030 保留，累计分支原 provisional IMP 编号迁为 IMP-051 / IMP-052。
旧 PR #4 / `develop` 已 closed/unmerged 并删除，不再作为待合并项；Dependabot PR #36 也已 closed/unmerged，其实际依赖安全修复由 PR #38 统一完成。当前工程接续不得从这些退役分支恢复“当前状态”。

当前平台事实以 W00/GOV-012 为准：`master` branch protection、Secret Scanning、Push Protection、required public repo scan 已启用；依赖安全修复已由 PR #38 合并。Jev 现役设计与实证入口为 [jev-integration.md](ai/jev-integration.md)，阶段状态只在对应 W04/W05/W08 任务页维护。

本次合并后事实指针与账本关联收口由 PR #40 承载；不为记录 PR 自身再追加会改变被审版本的自指提交。

## 7. v9.11 当前阶段门快照

本节只记录当前现场，不维护第二份 backlog；权威算法在总账 §5.9，任务元数据在所属 stage。

- **当前主门**：G1
- **主切片首选**：IMP-044
- **当前现场**：IMP-006 已合并且 post-merge master CI 全绿，已退出当前候选计算；允许按本节范围领取 IMP-044 一个切片。
- **门内阻断顺序**：IMP-044；IMP-006 已完成。
- G0 的 BUG-028 / BUG-026 已完成；BUG-020 为 `待条件`。G1 的 BUG-029 与 IMP-006 均已完成并退出候选计算；当前按门序领取 IMP-044，不因 RSH-031、新 UI、Agent 或研究任务“更有价值”跳过。
- BUG-020 的 current-value 接纳门现区分 source event time / received time、缺失/拒绝/合法空集与身份歧义；旧可信值不会被晚到/非法观测覆盖。2026-09-20 周日已用本片代码显式加载主仓部署 `.env`，成功构建 `chain(ths→tencent→eastmoney→sina)` 并完成有界只读休市探针：最近交易日 2026-09-18、6/6 指数覆盖、拒绝数 0，600519 实际由腾讯返回且 source time 为 2026-09-18，Hub 明确标 `stale/market_closed`。尚未完成真实交易时段完整会话验收，故不得标已完成或宣称长期盘中 SLA。
- BUG-029 现以 `ever_sealed/current_sealed/snapshot_state/version` 为唯一 current-state 契约；开板只恢复“进入评估”的资格，不自动获得成交/通知/模拟执行许可。2026-09-18 真实跨源样本已证明涨停池成员可多次开板/回封；旧 v1 归档保持原语义，v2 才使用新状态重放。
- RSH-031 为 `G1/P1/非阻断/门内序70`，即使同处 G1 也排在阻断项之后；且 `效果前置=RSH-026, IMP-020, RSH-030` 未满足前不得宣称龙头战法有效或接生产权重。
- U47 不制造跨门例外：IMP-006 已把 `reference_entry → executable_snapshot → PaperOrder fill` 三种价格身份和同一 decision/version 接到通知、模拟仓与页面；它的合并只证明工程事实契约收口，不证明买点效果。post-merge CI 通过后仍先做 G1/IMP-044，再进入 G2 的 IMP-049 → IMP-053；reference 与实际 shadow fill 永久分名、分母和收益口径。
- U48 同样不制造跨门例外：W08/GOV-027 为 `GX/P1/持续治理/门内序47`，只可作为不冲突的伴随切片；它先复用 factor/strategy/KB/Jev/opportunity 各自 owner 的既有证据，定义最小生命周期/衰退/成本反馈契约，不建第二总注册表。项目级上层治理入口新增 `skills/living-system-governor/SKILL.md`，用于跨模块方案、重大重构和机制生命周期复核；该 Skill 只提供证据/反证/KEEP-FIX-MERGE-EXPERIMENT-WATCH-RETIRE 决策协议，不拥有派工、生产晋级或阶段门修改权。 v1.2.0 在自我进化基础上进一步加入 U49 主动缺陷发现门；v9.11/U50 又补受控降级全权闭环，避免 Codex 不可用时角色门自锁：后续长期要求/重复纠偏先作为方法论候选，只有形成稳定可复用增量才版本化蒸馏；一次性要求不污染 Core，是否落实以 Git/PR 与后续行为核验，不靠聊天窗口记忆。任何生产降权、阈值变化、策略/Jev 晋级仍回原 owner task 与证据门。
- U49 不改变阶段门算法，但改变每轮默认动作：继续选刀前、审核放行前、阻断项闭环/换门后、事故/用户纠偏后都要运行有界主动缺陷发现门并写回执；同根 P0/P1 新发现可改变当前验收，跨域发现回原 owner，不自动扩权施工。
- U50 也不改变阶段门算法，只改变“谁可以完成当前切片”：正常模式仍是 Web Review + Codex；当前 `DEGRADED_FULL_CONTROL` 下网页端按同一阶段门全程操控，每个 PR 必须重新写 exact-HEAD `DegradedRelease`，不能复用一次用户授权跳过逐 PR 发布证据。
- GX 治理只可作为不冲突的伴随切片。2026-09-20 `GOV-018` 已闭环：`.workbuddy` / `.workbuddy-ai` 已物理删除，有价值内容进入项目中性 `skills/scripts/docs/artifacts`，第三方 UZI/Serenity 本体不再复制；PR #48 已合入 `master`（merge `b6b5ba0`，最终 required CI run `35481812431` backend/frontend/docs 全绿）。`GOV-026` 已落地 `scripts/workspace-hygiene.py` 并接 CI/交接；本轮又把 docs 根从 36 份 Markdown 收口为 6 个控制面，领域正文进入 system/data/product/strategy/ai/review/research，`doc-health` O2 阻断根目录回堆与未知分类；最终一致性复核 PR #50 又把 09-17/09-18 四份复盘/进化时间序列补入主干，剩余 4 个 backend/data JSON 明确保留为本机运行证据。以上治理变更不改变阶段门算法；BUG-028 / BUG-026 已闭环，BUG-020 因生产盘中外部条件转 `待条件`，故 G0 当前无可行动阻断项，阶段门现计算到 G1；IMP-006 已完成，G1 当前阻断位已转到 IMP-044。

当前处于 `READY_FOR_NEXT_PLANNED_SLICE`：下一主切片为 IMP-044，仅做 W02 已界定的“类型化回执 + 买点 Outbox”最小纵切；不得顺手迁移全部事件来源。没有网页登记的 `CROSS_GATE_EXCEPTION` 就不能跨门。

## 8. Jev、工具链与协作流当前基线

- Jev 只保留 bounded semantic verify、条件 capability routing、metadata-only usage 与研究/审核辅助；确定性金融规则、权限、撮合、风控和真实执行不得委托给 Jev。
- BillionsBobby/JevRouter 只采用经过固定版本校验的内核与 privacy-safe wrapper；旧 `jev-route` 已退出活动链。OpenRouter 当前明确不接入，也不使用聊天中出现过的旧 Key。
- Universal Verification 仍为 off/shadow；RSH-030 的 240 条固定队列已经 Jev 预标注，但 human gold 仍为 0/240。未完成人工独立标注前，不启用 cascade、不调生产阈值、不宣称准确率或额度节省。
- 协作固定为“`master` 账本事实源 + 用户短提示”，但执行角色由模式决定：正常模式网页规划/独立审核、Codex执行；`DEGRADED_FULL_CONTROL` 网页全程操控。Bridge、自动互调均不是必需依赖。功能分支只作短期施工载体，合并确认后立即删除。
- v9.4 引入 Jev 长期系统分层与 `plan-registry.md` §1.1 重大决策传播契约；后续版本继续继承该规则。新模型/工具链/架构/协作决定若只更新专题蓝图而漏总方案、INDEX、stage 或接手入口，视为治理缺陷。
- v9.5 把“当前方案不是永久终局”制度化：`ai/continuous-evolution.md` + `ashare-innovation-radar` 主动发现外部新模型/工具/量化方法/数据与反证；只产生 WATCH/SHORTLISTED/LAB 建议，任何真实采用仍回原 stage、证据门、PR/审核/CI。
- v9.6 已新增 RSH-031：历史涨停/强连板/空间板/弱市穿越/题材梯队与异常板块拉升采用全量事件+失败对照+point-in-time+旧→新盲测；Jev 只做 bounded MapReduce/rerank/verification，效果准入继续由 RSH-026/IMP-020，猎场证据接线只走 IMP-049。v9.8/U47 进一步规定可交易真实性由 IMP-053 hunting-shadow 验证，首见/reference 不冒充成交或净收益。
- v9.9/U48 规定 Jev/策略/因子/KB/路由等已采用机制仍需持续证明增量：固定实验候选集不再冒充猎场全局 taxonomy，历史有效不等于永久有效；衰退、知识贡献和额度节省必须用对应领域真实反馈复核。RSH-030 human gold 仍未完成，因此本轮只是治理登记，不产生准确率/额度节省结论。

## 8.1 U50 降级授权回执

- **Mode**：`DEGRADED_FULL_CONTROL`。
- **User authorization**：`EXPLICIT`；用户原意为“以后加上降级，降级之后由网页全程操控”。
- **Degraded reason**：当前 Codex 额度不可用，正常双角色无法持续完成实施→独立审核→发布闭环；旧 exact-HEAD Review 门因此形成自锁。
- **有效期**：持续到用户明确说 Codex 已恢复/退出降级；不是单 PR 临时口令。但每个 PR 的发布仍必须重新生成 exact-HEAD `DegradedRelease`，不能复用上一 PR 回执。
- **不降低项**：G0–G5/GX 阶段门、U49 主动反证、branch protection、required CI、latest master、CHANGES_REQUESTED/thread、public-repo scan、workspace/doc-health、敏感信息/范围、post-merge CI、删除功能分支。
- **当前动作**：先合入 U50 机制本身；随后只闭环 PR #67/IMP-044，不并行领取下一业务切片。

## 9. U49 主动审计回执（IMP-044 Preflight）

- **阶段**：`Preflight`；对象是下一主切片 IMP-044，不是成果 Review。
- **基点**：`master@6cbe746750d02bd387ead71d5d85cec50445e8e5`；PR #63/U49 与 PR #64/release-receipt gate 均已合并且 post-merge CI 成功。
- **扫描范围**：`buy_point.check_and_dispatch → watcher.dispatch_alert → morning_brief.append_alert → AlertEvent/AlertRule → NotificationOutbox/Attempt → AlertEngine._delivery_block/_deliver_pending → NotifierRegistry/FeishuNotifier → tests/config`，并回看 IMP-006 的 `latest_notification_execution` 决策事实。
- **已证实 P0-1 顺序/去重窗口**：`dispatch_alert` 当前先写 brief JSON 去重，再做 watch ledger/paper，最后才创建 AlertEvent；brief 成功但 DB/event 失败会让下一拍永久 dedup，且派生消费者可能先于通知事实存在。
- **已证实 P0-2 渠道双事实源**：`picks_buy_point_channels` 默认 `in_app,log`，但 `check_and_dispatch` 后续无条件直接 `send_interactive`；因此 channels 不能真正关闭/控制 Feishu。迁移后 channels/rule 必须成为唯一外发权限，保留既有实际产品语义时默认收敛为 `in_app,log,feishu`，不得保留 direct-card 旁路。
- **已证实 P0-3 回执语义缺失**：Notifier/Feishu 仍只返回 bool；明确平台拒绝与网络/超时/畸形回执被压成同一 False。IMP-044 必须产出 accepted / explicit_rejected / unknown，旧 bool 只留兼容。
- **已证实 P0-4 Outbox 判据不兼容**：现有 `_delivery_block` 仅理解 price/change；`picks_buy_point` 会被 `_extract_value=None` 误判 `condition_no_longer_met`。必须用 `intent.kind=picks_buy_point` 专用 recheck，并绑定 latest decision_id/version、执行 snapshot freshness、渠道/target 与有效期。
- **新增顺序裁定**：`决策归档 → AlertEvent + durable dedup + Feishu intent 同事务 → brief/watch-ledger/paper 派生消费者 → 异步发送`。每日去重权威迁到 DB；优先给 AlertEvent 增 nullable unique dedup_key，以 `trade_date+symbol+kind` 稳定哈希实现 create-once。brief 只做 best-effort 展示，不再拥有发送资格。
- **非目标**：不在这一刀迁开板/风控/日报等其它来源；不解决 IMP-049/053 的盘中 gate/buy_range 动态重评；不改策略阈值、仓位或真实交易边界。
- **允许下一动作**：当前 IMP-044 已有 PR #67；U50 机制合入后网页端在降级模式下同步 #67 到最新 master，对准确 HEAD 做 U49 作者反证并生成 `DegradedRelease`，required CI/release_check 全绿后直接合并、核 post-merge CI、删除分支。不得并行领取 IMP-032/BUG-016/IMP-049/053。

