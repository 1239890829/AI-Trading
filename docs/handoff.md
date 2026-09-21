# 当前交接：DEGRADED_FULL_CONTROL / G3-RSH-026 Cross-day Cumulative MFE/MAE

> 定位：当前运行模式、最新已合并证据、唯一在制纵切与安全边界；任务唯一状态仍以所属 stage 为准。

**当前模式：`DEGRADED_FULL_CONTROL`（用户明确授权，持续到用户明确退出/恢复 Codex）。** RSH-026 的 PR #69/#70/#71/#72/#74/#75 已闭环；PR #75 merge=`a96c3d0086872cf8f5bc71f5f19c8792007cbcf3`，post-merge CI run `35550792738`（#517）backend/frontend/docs 全绿，功能分支已删除。当前仍在 **G3 / RSH-026**，唯一在制纵切为 D1/D3/D5 **cross-day cumulative MFE/MAE**；施工分支 `chatgpt/rsh026-crossday-excursion` 基于 `master@a96c3d00`。本片只从已有完整 D0 决策后 Tencent 1m path 的新样本继续累积 future qfq 日线 high/low；历史旧部署没有 path 字段，禁止事后用日线伪造 D0 路径。不改策略阈值、生产权重、真实交易权限或 shadow fill 语义。


## 1. 固定入口与范围

仓库 `1239890829/AI-Trading`；共享事实固定从 `master` 读取：`AGENTS.md` → `docs/INDEX.md` / `docs/retro-and-gaps.md` → `docs/handoff.md` → 对应 stage。功能分支只是施工载体，合并后可删除，不能再充当固定入口。2026-09-19 本次审计起点为 `master@2b89c0c077b9f0978f4a510217cbecf3976ece8f`（PR #39）；该起点远端仅保留 `master` 且无 open PR。PR #4 与 PR #36 均已 closed/unmerged，旧 `develop` 及累计功能分支已删除；历史事实从 Git/PR 追溯，不再写成当前入口。
2026-09-18 规划复核基点为 `7f9f4a71c3c16f4fee1a1c728727eb64c7096769`。原40条需求、21条Codex来源、37条KB用途、三专题和已有业务成果全部继承；该规划批与后续 2026-09-19 工程/治理合并分开记账。

## 2. 累计规划要求去向（2026-09-18 基线 + 后续增量）

下表以 2026-09-18 规划批为基线，并吸收后续 U41–U50 的新增长期取舍；它是累计规划验收索引，不是第二套任务进度表。各项“覆盖”表示设计与验收方法有实际落点，不表示已经实现或实证有效。

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

- **当前主门**：G3
- **主切片首选**：RSH-026
- **当前现场**：IMP-044 已由 PR #67 合并且 post-merge CI 全绿；G1 无可行动阻断项。G2/IMP-049 仍为 `待条件`，故按总账 §5.9 跳过不可行动 blocker，当前主门为 G3/RSH-026。
- **门内阻断顺序**：RSH-026 → IMP-020；本轮只领取 RSH-026 一个纵切。RSH-030 为同门非阻断，RSH-031 仍受效果前置限制。
- G0 的 BUG-028 / BUG-026 已完成，BUG-020 为 `待条件`；G1 的 BUG-029 / IMP-006 / IMP-044 均在本候选合入后完成；G2 的 IMP-049 为 `待条件`。因此 post-merge 下一唯一首选为 G3/RSH-026，不因 RSH-031、新 UI、Agent 或其它研究任务“更有趣”跳序。
- BUG-020 的 current-value 接纳门现区分 source event time / received time、缺失/拒绝/合法空集与身份歧义；旧可信值不会被晚到/非法观测覆盖。2026-09-20 周日已用本片代码显式加载主仓部署 `.env`，成功构建 `chain(ths→tencent→eastmoney→sina)` 并完成有界只读休市探针：最近交易日 2026-09-18、6/6 指数覆盖、拒绝数 0，600519 实际由腾讯返回且 source time 为 2026-09-18，Hub 明确标 `stale/market_closed`。尚未完成真实交易时段完整会话验收，故不得标已完成或宣称长期盘中 SLA。
- BUG-029 现以 `ever_sealed/current_sealed/snapshot_state/version` 为唯一 current-state 契约；开板只恢复“进入评估”的资格，不自动获得成交/通知/模拟执行许可。2026-09-18 真实跨源样本已证明涨停池成员可多次开板/回封；旧 v1 归档保持原语义，v2 才使用新状态重放。
- RSH-031 为 `G1/P1/非阻断/门内序70`，即使同处 G1 也排在阻断项之后；且 `效果前置=RSH-026, IMP-020, RSH-030` 未满足前不得宣称龙头战法有效或接生产权重。
- U47 不制造跨门例外：IMP-006 已把 `reference_entry → executable_snapshot → PaperOrder fill` 三种价格身份和同一 decision/version 接到通知、模拟仓与页面；IMP-044 本 PR 只证明通知可靠性工程闭环，不证明买点效果。G2/IMP-049 当前仍 `待条件`，所以 post-merge 阶段门计算到 G3/RSH-026；未来 IMP-049 条件转可行动时仍需重新按最低门算法计算，reference 与实际 shadow fill 永久分名、分母和收益口径。
- U48 同样不制造跨门例外：W08/GOV-027 为 `GX/P1/持续治理/门内序47`，只可作为不冲突的伴随切片；它先复用 factor/strategy/KB/Jev/opportunity 各自 owner 的既有证据，定义最小生命周期/衰退/成本反馈契约，不建第二总注册表。项目级上层治理入口新增 `skills/living-system-governor/SKILL.md`，用于跨模块方案、重大重构和机制生命周期复核；该 Skill 只提供证据/反证/KEEP-FIX-MERGE-EXPERIMENT-WATCH-RETIRE 决策协议，不拥有派工、生产晋级或阶段门修改权。 v1.2.0 在自我进化基础上进一步加入 U49 主动缺陷发现门；v9.11/U50 又补受控降级全权闭环，避免 Codex 不可用时角色门自锁：后续长期要求/重复纠偏先作为方法论候选，只有形成稳定可复用增量才版本化蒸馏；一次性要求不污染 Core，是否落实以 Git/PR 与后续行为核验，不靠聊天窗口记忆。任何生产降权、阈值变化、策略/Jev 晋级仍回原 owner task 与证据门。
- U49 不改变阶段门算法，但改变每轮默认动作：继续选刀前、审核放行前、阻断项闭环/换门后、事故/用户纠偏后都要运行有界主动缺陷发现门并写回执；同根 P0/P1 新发现可改变当前验收，跨域发现回原 owner，不自动扩权施工。
- U50 也不改变阶段门算法，只改变“谁可以完成当前切片”：正常模式仍是 Web Review + Codex；当前 `DEGRADED_FULL_CONTROL` 下网页端按同一阶段门全程操控，每个 PR 必须重新写 exact-HEAD `DegradedRelease`，不能复用一次用户授权跳过逐 PR 发布证据。
- GX 治理只可作为不冲突的伴随切片。2026-09-20 `GOV-018` 已闭环：`.workbuddy` / `.workbuddy-ai` 已物理删除，有价值内容进入项目中性 `skills/scripts/docs/artifacts`，第三方 UZI/Serenity 本体不再复制；PR #48 已合入 `master`（merge `b6b5ba0`，最终 required CI run `35481812431` backend/frontend/docs 全绿）。`GOV-026` 已落地 `scripts/workspace-hygiene.py` 并接 CI/交接；docs 根已收口为 6 个控制面。以上治理不改变阶段门算法；IMP-044 随本 PR 完成后，BUG-020 与 IMP-049 都因 `待条件` 排除，阶段门按账本计算到 G3/RSH-026。

当前处于 `DEGRADED_FULL_CONTROL`：PR #67 已完成 merge + post-merge CI + 分支清理，本轮已正式领取 G3/RSH-026。只做当前 outcome/denominator 纵切；完成或阻塞后再重新计算，不能在同一 PR 内继续扫 IMP-020 或其它任务。

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
- **当前动作**：U50、IMP-044 与 RSH-026/PR #69/#70/#71/#72/#74/#75、账本 PR #73 均已闭环；#75 merge=`a96c3d00`、post-merge CI #517 全绿且分支已清理。当前在 G3/RSH-026 执行 D1/D3/D5 cross-day cumulative MFE/MAE 纵切，分支 `chatgpt/rsh026-crossday-excursion`。本轮只做该纵切；每个后续 PR 仍需新的 exact-HEAD `DegradedRelease`。

## 8.2 U49 主动审计回执（RSH-026 Preflight）

- **阶段**：`Preflight`；对象是 G3/RSH-026 当前纵切。
- **基点**：当前子片为 `master@eed8214ffdf914dd06fbeba7d150e37dbcd140a1`；本节同时保留 PR #69/#70 之前已发现并持续适用的 RSH-026 反例。U50 降级模式已生效。
- **扫描范围**：`build_intraday_records/build_notification_records → archive_records → OpportunityDecisionSnapshot/OutcomeLabel → pending_symbols → review_intraday 收盘回填 → learning_summary/opportunity_scorecard → API/tests/migration`。
- **已证实 P1-1 选择性分母**：旧 `archive_records` 只为 `ranked/eligible/notified/suppressed` 建 outcome；rejected/unknown 只留 snapshot，导致 `label_coverage=1.0` 仍可能漏掉全漏斗失败样本。
- **已证实 P1-2 过早可成交主张**：pending outcome 未评估前默认 `fill_state=ok`，会把“尚未判定”静默写成“可成交”。
- **已证实 P1-3 跨版本不可自愈**：旧 snapshot 已存在但 outcome 缺失时，同 run 重放直接跳过，无法补齐历史分母。
- **本轮新增 P1-4 交易日错位风险**：D1/D3/D5 若按自然日 `+1/+3/+5` 会跨周末/节假日错标；目标日必须来自 `trade_calendar`，日历未覆盖未来时 fail-closed，后续再补。
- **本轮新增 P1-5 收益身份风险**：D1/D3/D5 虽满足 T+1 时间约束，但本表 reference_price 不是实际 shadow fill；跨日 `net_return_pct` 只能叫“成本调整 reference 代理”，`realizable_return=false`。
- **本轮新增 P1-6 请求面风险**：为补全失败分母不能把 deferred/rejected 全量加入盘后实时 K 线请求；主链只用 `pending_outcome_targets(..., lookback_days=30)` 找 selected/actionable 的到期/逾期 horizon，并继续并入 D0 pending + watch ledger，失败分母留显式离线回填。
- **本轮新增 P1-7 漏跑恢复风险**：只处理“今天到期”会让某日调度/数据源失败永久留下 pending。当前主链有界回看最近 30 个自然日内 selected future-horizon target，并为每个 symbol 只取一次日 K 后按目标交易日补标；超过窗口不自动扩大抓取面，保留 pending 供后续离线专项回补。
- **本轮新增 P1-8 D0 前视风险**：不能用当日日线 high/low 算“决策后”MFE/MAE，因为日线包含决策前极值。当前只接受严格晚于 `snapshot.as_of` 的腾讯 1m 完整 bar，并保守排除决策当分钟。
- **本轮新增 P1-9 分钟跨源时区风险**：腾讯 1m 会把北京时间转 UTC；东财当前分钟 normalizer 把北京时间字符串直接标 UTC，两者 timestamp 语义不一致。当前 D0 路径只认 `source=tencent`，拿不到就 pending，禁止静默 fallback。
- **本轮新增 P1-10 炸板漏计风险**：只看最终涨停池会把“盘中封板后炸板”误标 `not_hit`。ever-hit-limit 使用最终涨停池 ∪ 炸板池；只有两池都成功且两边都缺席才可记 `not_hit`，任一池失败时缺席保持 `unknown`。
- **本轮新增 P1-11 重复外呼风险**：`path_state=unknown` 既可能是旧版本未采，也可能是当前版本 terminal unknown。用 `path_version` 区分：旧 unknown 可自愈，当前版本因 reference 缺失等已判 unknown 后不再每天重复请求分钟线。
- **本轮新增 P1-12 分钟序列截断风险**：腾讯 `m1` 是滚动上限窗口，拿到若干合法决策后 bar 不等于拿到完整 D0 路径；若目标交易日最后合法 bar 尚未覆盖 15:00，路径保持 `pending`，MFE/MAE 不落值，禁止把盘中/截断前缀冒充全天结果。真实活跃股探针 `600519`、`000001` 已确认完整交易日终端 bar 为 15:00；`600001` 空结果属于不合适的历史/非活跃探针样本，不作为源不可用证据。
- **本轮新增 P1-13 路径分母串线风险**：`scorecard.path_metrics` 不得借用“D0 收盘标签已成功”的样本集；分钟路径与收盘价是独立结果事实。路径统计按 selected 的 `symbol×trade_date` 最早决策独立去重，即使 close label 仍 pending，只要完整 1m 路径已成熟就计入 path evaluable/coverage，反之亦然。
- **本轮新增 P1-14 单次盘后回填风险**：`review.trigger=schedule` 会在 outcome/path IO 前持久化，15:35 首次分钟外呼若瞬时失败，整轮 review 的幂等会阻止再次进入；而 1m 为滚动窗口，跨日恢复不可靠。调度器现只对**仍 pending 的 selected D0 path**做同日晚盘有界重试：首轮后每 15 分钟一个 slot，最多 60 分钟（15:50/16:05/16:20/16:35），不重开整轮 review、不请求 deferred/rejected，terminal unknown 也不重试。
- **本轮新增 P1-15 路径版本混算风险**：既然路径派生事实带 `path_version`，coverage/MFE/MAE/time_to_limit 消费侧必须只统计当前 `PATH_VERSION`；旧版 labeled 仍保留在审计状态/版本分布中，但不得静默混入当前均值。当前 schema 仍是一条 horizon outcome 上挂一组 path 字段，因此未来若需要“同 snapshot 多版路径同时在线重算”，应另立 append-only path outcome 子表，而不是覆写旧 labeled 证据。
- **本轮新增 P1-16 午休提前量膨胀风险**：自然钟差会把 11:29→13:01 误报为 92 分钟“可行动提前量”。当前 `time_to_limit_minutes` 明确定义为 A 股 09:30–11:30、13:00–15:00 **连续交易分钟**，午休不计；反例测试固定 11:29→13:01 = 2.0 分钟。
- **本轮新增 P1-17 首板时间可评估分母风险**：ever-hit 与“能计算 time_to_limit”不是同一件事，炸板源可能只证明触板但缺首封时间。scorecard 现同时给出 `limit_states`、`limit_membership_evaluable`、`ever_limit_hits` 与 `post_decision_timed_hits`；平均 `time_to_limit_minutes` 只在明确 post-decision 且首封时间可用的子集上计算，不能把 `hit_time_unknown` 静默丢掉后宣称全部命中都有提前量。
- **本轮新增 P1-18 空池证据边界**：CompositeProvider 的通用 pool 路由把 `[]` 当失败并继续切源，所有源都空则抛错；因此当前 D0 路径不会把“某源静默空响应”误当成全市场真实 0 而制造假 `not_hit`，但真实 0 炸板/0 涨停日也会保守保持 `unknown`。本片不把全仓 pool 改为 allow-empty：东财 ZT/ZB 与 THS ZB 当前尚未像跌停池一样完整校验业务 `rc/tc/pool`，先接受空值会扩大假成功风险；若后续要提高零池覆盖，应先把各源完整响应契约补齐，再做“优先非空、全健康源确认空才接受空”的专用路由。
- **本轮新增 P1-19 路径 coverage 丢分母风险**：若从 `OutcomeLabel JOIN Snapshot` 生成 path denominator，某个 selected snapshot 一旦 outcome 行根本没挂上，它会同时从分子和分母消失，覆盖率被系统性抬高；若同股后续刷新有 outcome，还可能用更晚决策替代最早决策。当前 scorecard 先从 immutable selected snapshots 按 `symbol×trade_date` 取**最早 selected 决策**定义分母，再按该 exact snapshot 找 outcome；新增 `outcome_attached` 显式暴露挂接缺口，缺 outcome 时 denominator 仍在、coverage=0，不允许“缺得越多看起来越完整”。learning summary 的 selected path denominator 同样改从 snapshots 构造。
- **高风险反例已裁定**：不能简单把所有 rejected/unknown 加进 `pending_symbols`，否则盘后 `review_intraday` 会把实时逐股外部 close 请求从 selected 集合扩大到全漏斗。当前方案用 `deferred/not_actionable` 分流，实时消费者维持原请求面，离线显式回填再补市场结果。
- **真实长期库已定位（2026-09-21）**：旧运行工作区的 `data/ashare.db` 为 173MB，运行工作区落后远端 master 51 个提交，故**禁止直接在原库上试新代码**。只读盘点得 156,108 snapshot / 63 D0 outcome；63 条恰好覆盖全部 selected/actionable 快照，156,045 条缺失均来自 rejected/unknown 全漏斗失败对照。其配套 marketdb 为 378MB、10,298,838 行、覆盖至 2026-09-18。
- **历史恢复副本验证**：先用 SQLite backup 复制原库，再在副本从 Alembic `d2e4a6b8c0f1` 升到当前 head `a6e2c9f4b7d1`，`integrity_check=ok` 且原行数不变；恢复工具在副本插入 156,045 个缺失 D0 identity、修正 15 个 legacy 未评估 `pending+fill_state=ok`，并仅用本地 marketdb exact-date close 离线回填。最终 156,108 snapshot / 156,108 D0 outcome、缺 identity=0；9/16–9/18 marketdb symbol coverage 分别 99.7%/100%/100%，缺 reference 的失败样本显式变 `unknown`，缺 marketdb close 的少量样本保留 pending。副本 `integrity_check=ok`；二次 `--apply` 只读计划确认无可写 close 后直接 `noop=true / backup=null`，没有再次复制 173MB 备份。
- **安全边界**：真实运行库尚未写入；正式执行必须先部署/迁移当前代码、停应用/调度、自动 SQLite backup，再运行显式 `--apply`。恢复只补不存在的 `(snapshot_id,d0_close)`，不改 decision snapshot、不覆盖既有 labeled outcome，不访问外部行情源。
- **本地发布前证据（RSH-026 D0 path 子片）**：最终实现态 backend 全量 `4066 passed / 80 skipped`，`pyflakes app tests scripts` 通过；frontend `tsc`、Vitest `73 files / 695 tests`、ESLint、Next production build 通过；`public_repo_scan`、`workspace-hygiene`、`doc-health` 均通过。最终合并仍以 PR exact-HEAD GitHub CI + DegradedRelease receipt 为准，本地结果不替代远端门禁。
- **非目标**：当前子片实现 D0 决策后 MFE/MAE 与首次封板/time_to_limit，但不把 reference 路径冒充实际 shadow fill P&L，不做跨日累计 MFE/MAE、不做 deferred 全量实时分钟回补、不改策略阈值、生产权重、交易权限或真实交易。

## 8.3 U49 主动审计回执（RSH-026 Legacy D0 Backfill）

- **阶段/基点**：`Preflight + production-copy validation`；基于 `master@7df3f2867208a7515bbb9796688dbf1589b99434`（PR #71 已合并且 post-merge CI #507 全绿）。
- **真实库发现**：旧部署工作区仍落后远端 master 51 个提交；其 173MB SQLite revision=`d2e4a6b8c0f1`，共有 156,108 snapshot / 63 D0 outcome。63 条 outcome 与 63 条 selected/actionable snapshot 一一覆盖；缺失 156,045 条均是 rejected/unknown 失败对照，因此 selected 胜率分母没有再丢 156k，真正缺口是全漏斗漏选/误杀分母。
- **P1-20 历史自愈不足**：PR #69 的 exact-run replay 只能在旧 run 再次被重放时补 identity，无法自动修复已经沉睡的 156,045 条历史 snapshot。当前新增显式离线 `backfill_missing_outcome_identities()` 与 CLI；默认 dry-run，写入必须 `--apply`。
- **P1-21 版本/生产隔离**：真实库 schema 落后当前 head 两个 migration，禁止用最新 ORM 直接碰原库。只读 dry-run 可在旧 revision 盘点；写入前必须先升级 schema。已对 SQLite 一致性副本验证 `d2e4→f8c1→a6e2`，`integrity_check=ok`，156,108 snapshot / 63 outcome 原值均保留。
- **P1-22 legacy `fill_state=ok` 歧义**：旧 ORM 默认曾把未评估 pending 写成 `ok`，但“已经评估可成交、只是收盘价缺失”也合法为 pending+ok。恢复只修 `state=pending + labeled_at=NULL + fill_state=ok + reason=等待收盘价` 的旧初始化态；已评估 reason 不得二次降回 pending。
- **P1-23 备份放大**：173MB 库的无变化二次 apply 若仍强制备份会持续堆磁盘。CLI 现先做只读计划；只有缺 identity、需修 legacy fill，或本地 marketdb 确有可写收盘价时才创建一致性备份；no-op apply 输出 `backup=null`。
- **P1-25 future-horizon 自动放大**：真实副本只补 D0 就从 173MB 增至 267MB；若盘后调度继续对 156,108 条历史 snapshot 自动建 D1/D3/D5 full-funnel identity，理论最多再新增 468,324 行，且会随日历成熟逐日触发。当前生产 EOD 改为 `ensure_outcome_horizons(..., include_deferred=False)`，在线只建 selected/actionable future identity；rejected/unknown 的跨日失败对照必须显式离线研究。真实副本 9/16–9/18 共 154,106 source snapshot 的规模验证中，selected-only 仅处理 61 snapshot，三个 horizon 合计新增 183 行。
- **P1-26 巨型 `IN (...)` 崩溃风险**：旧 `ensure_outcome_horizons` 把同日全部 snapshot_id 展开进一个 SQL `IN`；真实 2026-09-18 有 81,672 条 snapshot，可能超过 SQLite bind-variable 上限。existing horizon 查询现改为按 `trade_date` join，不再展开 snapshot ID 列表；full-funnel 离线模式也沿用该安全查询。
- **P1-27 selected-only ORM 放大**：初版虽不再写 deferred future identity、也不再使用巨型 `IN`，但仍先加载同日全部 snapshot 再在 Python 筛选；真实 2026-09-18 是 81,672→8，no-op 探针约 1.82s。当前改为 SQL `COUNT(*)` 保留 source denominator + SQL selected predicate，仅 materialize 8 条 actionable snapshot，同行为复测约 0.57s；回归测试锁定 selected-only 模式只加载 selected snapshot 对象。full-funnel 显式离线模式仍允许全量读取，但不会进入生产 EOD。
- **真实只读 dry-run**：原库未修改即可报告 missing D0 identity=156,045、selected missing=0、legacy pending-fill default=15；marketdb 对 2026-09-16/17/18 的待恢复 symbol 覆盖分别为 1007/1010、256/256、162/162。
- **一致性副本 apply**：插入 156,045 条 deferred/not_actionable identity，修正 15 条旧初始化 fill；随后用本地 exact-date marketdb 回填，最终 D0 identity=156,108/156,108、missing=0，状态为 `labeled=150,903 / unknown=5,192 / deferred=12 / pending=1`。`unknown` 主要是决策 reference 缺失；12 条 deferred 与 1 条 selected pending 均因本地 marketdb 无目标日收盘而保留未标，不造 0、不外呼。二次 apply `inserted=0 / repaired=0` 且 CLI 判定 `noop=true / backup=null`。
- **P1-24 版本空分母语义**：这 156,108 条历史全为 `stock-opportunity-funnel-v1 / pit-evidence-v1`；默认 current-v2 scorecard 对它们应是“没有匹配版本样本”，不能把 `complete=false` 配成普通 `insufficient_sample`。scorecard 新增 `funnel_denominator.state=empty|incomplete|complete`，空版本返回 `no_matching_denominator`；按真实 v1/v1 重算，9/08、9/16、9/17、9/18 的 opportunity label coverage 分别为 50.00% / 95.20% / 98.44% / 95.06%，四日均保持 `incomplete_denominator`，没有把历史补数冒充成策略效果结论。
- **运行边界**：真实运行库仍保持原样；其运行代码也落后当前 master，不能只迁 DB/灌新 denominator 而让旧消费者继续运行。实际部署恢复必须在应用/调度停止、代码与 schema 同步后执行，并保留工具生成的备份。
- **发布结论**：PR #72 已以 exact HEAD `49d94e9c34675f3f47a019f0d8b9533b0830688f` 通过 `DegradedRelease` 与 `release_check.py`，merge=`726fdeb0242cd4a42ffdc5599c8c4fc006972331`；post-merge CI run `35546496882` 的 backend/frontend/docs 全部 `success`，功能分支已删除。

## 8.4 U49 主动审计回执（RSH-026 Corporate-action / Suspension Basis）

- **阶段/基点**：`Preflight + candidate implementation`；基于 `master@4fbde4557bc5dbc0d9438343ed4640028be1fd3a`（PR #73 已合并，post-merge CI #513 全绿）。
- **P1-28 raw/qfq 尺度串线**：旧 outcome 用盘中 raw reference 直接除 qfq 日收；遇除权除息会把机械价格跳变写成收益。当前新增 `PRICE_BASIS_VERSION=qfq-ref-v1.raw-anchor`：以决策日 `qfq_close/raw_close` 把 raw reference 映射到 qfq 基准，再与目标日 qfq close 比较；原始 `reference_price` 永久保留。真实 marketdb 的 603444 在 2026-09-16 raw=373.49/qfq=363.49、09-17 qfq=352.06；新口径约 -3.14%，不会使用 raw→qfq 的错误跌幅。
- **P1-29 跨源伪复权因子**：qfq 与 raw 若来自不同 vendor，微小口径差会被比值放大成“复权因子”。生产 EOD 对 outcome symbol 的两条腿都走同一 Composite 入口，并要求 bar `source` 完全一致；failover 后来源不一致则不生成 basis、标签保持 pending。THS `adjust=forward/none` 与东财 `fqt=1/0` 分开有参数级回归。
- **P1-30 旧标签版本混算/覆写风险**：迁移前 labeled 行没有 price-basis 版本，不能把它们按新公式静默解释；同时 `(snapshot_id,horizon)` 唯一约束不允许在同表追加第二版。scorecard/current denominator 只统计当前 `PRICE_BASIS_VERSION`，旧 `legacy_unversioned` 仅保留审计。若后续确需把旧历史重算纳入当前指标，必须另建 append-only outcome revision 子表，禁止覆写旧 labeled 数值。
- **P1-31 历史停牌与数据缺口不可同形**：仓库 `trading_status` 能判当前/最近状态，但不足以证明任意历史 horizon 缺 bar 的原因。当前只接受精确 `target_date` close；目标日无 bar 不借前后日期、不造 0，保持 pending 可重试。没有独立历史停复牌事件证据前不把“缺 bar”硬标成 suspended。
- **迁移副本验证**：旧长期库一致性副本从 `d2e4a6b8c0f1` 直接升级到候选 head `c7f3e1a9b4d2`，`integrity_check=ok`，156,108 snapshot / 63 原 outcome 行数不变；63 条旧 outcome 的 `price_basis_version` 全为空，4 个新 basis 列齐全，证明迁移没有伪造历史版本。
- **离线恢复口径**：legacy recovery 改用 marketdb 同日 `daily_k` raw 与 `daily_k_adj.close_adj` 成对计算 factor；缺任一腿即保持未标，不访问外部行情源。
- **当前验证**：corporate-action、exact-date suspension、same-source、provider 参数、legacy exclusion、migration/provider/review/backfill 定向测试已通过；最终 exact HEAD `925db24618c4443105d18bd522be5013dfc185d1` 本地 backend `4083 passed / 80 skipped`，PR #74 CI #514 全绿，`release_check` 通过；merge=`d8d5d35c418c518f3f67b00bf7a62faac4e34534`，post-merge CI #515 全绿，功能分支已删除。
- **非目标**：本片不重写旧 labeled 历史、不把 pending 缺 bar 猜成停牌、不实现 actual shadow fill、不做跨日累计 MFE/MAE、不改选股/风控/仓位参数。

## 8.5 U49 主动审计回执（RSH-026 Append-only Outcome Revisions）

- **阶段/基点**：`Preflight + production-copy validation`；基于 `master@d8d5d35c418c518f3f67b00bf7a62faac4e34534`（PR #74 merge 后 CI #515 全绿）。
- **P1-32 旧 terminal label 无法原位“升级”**：base 表唯一键为 `(snapshot_id,horizon)`；覆写会毁掉历史，硬塞第二条又违反唯一性。新增 `OpportunityOutcomeRevision`，以 `base_outcome_id + revision_version` 唯一，base outcome / snapshot / path 字段永久不改。
- **P1-33 revision 覆盖误伤 path 风险**：current scorecard/summary 只在内存 effective view 覆盖 close-return 字段；MFE/MAE/limit/time-to-limit 仍从 base outcome 继承。回归测试锁定 revision 后 path evaluable/MFE/MAE 不丢。
- **P1-34 dry-run/apply recovery surface 分叉**：初版 dry-run 能发现 legacy labeled，但 apply 只请求 pending symbols，会出现“预检看到、执行漏掉”。当前两段统一为 pending/deferred ∪ legacy revision candidates；真实副本因此成功追加 48 条 revision。
- **P1-35 cost version 不能继续只藏在 reason 文本**：revision 新增结构化 `cost_model_version`，effective scorecard 优先读结构化字段，legacy base 才回退 reason 兼容；migration parity 覆盖该列。
- **P1-36 跨日 revision 误用单日 close 风险**：`backfill_outcome_revisions` 默认只允许 D0；D1/D3/D5 必须显式传 horizons 与各自 target-date close map，离线 D0 工具不自动重算跨日结果。
- **真实长期库最终副本**：从原库重新复制，`d2e4→f8c1→a6e2→c7f3→d9e4`，`integrity_check=ok`；156,108 snapshot / 63 原 base outcome 保留。apply 后 D0 identity=156,108/156,108，新增 revision=48，revision backlog=0。
- **base 不变机械证据**：从原始长期库只读取 48 条 terminal labeled 的全部原始 base 列，再按同一 48 个 id 与恢复后副本逐列比较，结果 `matched=48 / identical=True`；revision 只追加派生结果，不覆写旧 base。
- **读侧恢复结果**：2026-09-16 v1/v1 scorecard 从 legacy exclusion 的 current sample=0 恢复到 28 个独立 selected symbol-day sample，应用 current revision=48；但全漏斗仍 `incomplete_denominator`，因此继续不产出策略效果结论。
- **幂等**：第二次 apply 返回 `noop=true / backup=null`；缺 marketdb 的少量 pending 仍保留，不重复生成 revision。
- **发布结论**：PR #75 exact HEAD `57e2e186b5f449e7f0f4625226f8ac89757cdf2e` 本地 JUnit `4170 tests / 0 failures / 0 errors / 80 skipped`（即 4090 passed），全仓 pyflakes 与 repo/docs 门全绿；required CI #516 与 `release_check` 通过，merge=`a96c3d0086872cf8f5bc71f5f19c8792007cbcf3`，post-merge CI #517 backend/frontend/docs 全绿，功能分支已清理。
- **非目标**：不改旧 base labeled、不自动重算 D1/D3/D5、不复制 path 证据、不实现 actual shadow fill、不做跨日累计 MFE/MAE、不改策略/风控/仓位参数。

## 8.6 U49 主动审计回执（RSH-026 Cross-day Cumulative MFE/MAE）

- **阶段/基点**：`Preflight + candidate implementation`；基于 `master@a96c3d0086872cf8f5bc71f5f19c8792007cbcf3`（PR #75 merge 后 CI #517 全绿）。
- **P1-37 D0 前视 + raw/qfq 尺度串线**：决策日整根 daily high/low 含决策前极值，不能拿来拼路径；D0 只接受已由当前 `PATH_VERSION` 证明完整的决策后 Tencent 1m extrema。future daily high/low 是 qfq，直接与 D0 raw 极值合并又会把公司行动制造成虚假 MFE/MAE；最终 `CROSS_DAY_PATH_VERSION=xday-v1.d0m1+qfq1d.raw-anchor` 将 future qfq extrema 按决策日同源 qfq/raw factor 映射回 raw-reference 等价尺度，再与 D0 raw extrema 合并，`path_high/low` 跨 horizon 始终保持同一价格尺度。
- **P1-38 中间交易日缺 bar 不能静默跳过**：D3/D5 若少任一预期市场交易日，路径保持 `pending`，不按剩余日线硬算；本层不能证明是停牌还是数据缺口，故不猜、不借邻日。
- **P1-39 horizon target 身份错位**：D1/D3/D5 的 `target_date` 必须分别是决策日后的第 1/3/5 个市场交易日；实际 offset 不一致时直接 terminal `unknown`，禁止把自然日或错误交易日结果写进正确 horizon。
- **P1-40 close/path 成熟度独立**：future close label 与 cumulative path 是两类独立结果事实。即使 close 仍 pending，只要 D0 path + 预期 future qfq high/low 已完整，cross-day path 可以 labeled；反之 close 已 labeled 也不能替代缺失的 path bar。scorecard 为每个 horizon 使用独立 path denominator。
- **P1-41 请求面放大风险**：跨日 path retry 只查询 selected/actionable future rows，30 天有界；与 pending close symbols 合并后复用同一次 qfq 日线请求，同时产 close/high/low，不把 rejected/deferred 全漏斗扩进实时 EOD 请求。
- **P1-42 历史回填不可得**：真实长期库仍停在 `d2e4a6b8c0f1`，其 `opportunity_outcome_label` 根本没有任何 `path_*` / `mfe_pct` / `mae_pct` 列；配套 378MB marketdb 的 `daily_k_adj` 也只有 `close_adj`，没有复权 high/low。没有历史 D0 决策后分钟证据与可信 qfq high/low，就不能用今天的 raw 日线倒推历史 cross-day path；本片只从具备完整 current D0 path + 可验证同源 qfq OHLC 的新样本在线积累。
- **P1-43 schema 宽度风险**：现有 `path_version` 列宽只有 32；初始描述性版本串会在严格数据库上有截断风险。最终固定 `CROSS_DAY_PATH_VERSION=xday-v1.d0m1+qfq1d.raw-anchor`，并用测试机械锁定 `len(version) <= 32`，不依赖 SQLite 对 VARCHAR 长度的宽松行为。
- **P1-44 新 lane 反向阻断旧 D0 风险**：cross-day 是附加结果事实，不能因其 provider/写回异常让 #71 已闭环的 D0 path 一并丢失。EOD 现在先完成 `collect_d0_path_outcomes`，再在独立 try/except 中处理 cross-day；回归测试让 cross-day 人工抛错并断言 D0 仍执行且返回。
- **P1-45 日 K 日期时区错位**：provider 的日 K `ts` 可能是带时区时间；直接 `.date()` 会把 UTC 23:30 归到前一自然日。`_daily_bar_facts` 统一先 `to_beijing_naive(ts)` 再取交易日，回归锁定 UTC 23:30 → 北京次日。
- **同源与完整性**：future qfq daily source 必须与 price-basis 的 qfq source 相同；跨 provider 路径保持 pending。D0 path 必须是当前 `PATH_VERSION` 且完整覆盖收盘，否则 cross-day 不定稿；D0 terminal unknown 会传播为 cross-day terminal unknown。
- **scorecard/summary**：D0 仍用 `PATH_VERSION`；D1/D3/D5 使用 `CROSS_DAY_PATH_VERSION`，报告 denominator / outcome_attached / evaluable / coverage / avg MFE / avg MAE；first-limit/time-to-limit 仍明确只属于 D0。learning summary 的每个 horizon 额外暴露 path version/state/selected coverage，不能把 close coverage 代替 path coverage。
- **生产 EOD 接线**：`pending_cross_day_path_targets` 与 pending close 分开维护；同一 symbol 的一次 qfq daily fetch 通过 `_daily_bar_facts` 同时供 close 与 high/low，raw daily 只用于 basis。交易日历不可用时连 cross-day backlog 都不读取，也不把其 symbol 扩进额外 provider 请求面；daily bar 日期统一按北京时区解释。
- **当前验证**：纯函数、DB 写回、retry surface、scorecard 独立 denominator、公司行动、缺中间交易日、跨 provider、错误 horizon、目标日之后极值不泄漏、D0 terminal unknown、日 K 北京时区归属、EOD 编排与 cross-day 失败不阻断 D0 的回归均已通过。真实长期库及现有 RSH-026 副本的 current D0 path labeled 均为 0，无法形成可信真实历史 cross-day 样本；本地 marketdb 又无 qfq high/low。本轮对东财在线 qfq/raw 日 K 的实探还遇到 `RemoteProtocolError` 断连，因此只证明实现/契约和 fail-closed 边界，不声称真实线上 OHLC 覆盖已验证。最终 exact-head 全量 backend / repo gates / CI 仍是发布前门禁。
- **非目标**：不伪造旧历史 path、不把停牌与数据缺口强行二分、不把 reference path 冒充 shadow-fill P&L、不实现 actual fill/entry-capture、不改策略/风控/仓位参数。

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
- **允许下一动作**：PR #67 已同步 U50 主干；网页端在降级模式下对准确 HEAD 做 U49 作者反证并生成 `DegradedRelease`，required CI/release_check 全绿后直接合并、核 post-merge CI、删除分支。不得并行领取 IMP-032/BUG-016/IMP-049/053。


## 10. IMP-044 实施候选（降级全权作者证据）

- **作者/授权**：用户已明确激活 U50 `DEGRADED_FULL_CONTROL`；本网页会话同时承担实现与发布操作者，必须做 U49 作者反证并使用 distinct `DegradedRelease`，不得把它命名成独立 `Review`。
- **实施基点**：`master@aca43e408a9555da43de9e7f42bb10361c92b6e9`；分支 `chatgpt/imp044-outbox`。
- **工程事实**：AlertEvent 新增 nullable unique `dedup_key` 与 migration `f8c1d4e7a2b6`；buy-point 使用 `trade_date|kind|symbol` 稳定哈希并通过 `record_trigger_once` 与 Feishu Outbox intent 同事务 create-once。card 与 execution_ref 落同一 AlertEvent snapshot，Outbox 只保存最小 intent identity；brief/watch-ledger/paper 都后置为派生消费者。
- **渠道/回执**：`DeliveryResult` 分 `accepted / explicit_rejected / unknown`；明确平台拒绝落 `permanent_failed`，网络/5xx/畸形/冲突回执落 `unknown` 且不自动重发。旧 `send()` / `send_interactive()` 只作 bool 兼容包装，买点主链不再 direct Feishu。配置 `picks_buy_point_channels` 为唯一外发权限，默认 `in_app,log,feishu` 保留历史实际产品行为。
- **发前复核**：`intent.kind=picks_buy_point` 不走 price/change `_extract_value`；专用 recheck 要求 event card/execution_ref 完整、event 自身 execution snapshot 为 ready、最新归档同 symbol 的 exact decision_id/version 仍一致且 eligible/passed、最新 executable snapshot 仍 ready，同时复用 rule/channels/target/交易窗口与 Outbox expiry 门。
- **作者级反证已抓并修复**：① 并发 create-once 的 unique 冲突可在 `enqueue_feishu()` 为取得 event.id 主动 `flush()` 时发生，已把 flush/outbox/commit 放进同一 `IntegrityError` 恢复边界，且只有同 dedup_key 确已存在才按重复收敛；② buy-point 从旧 direct-card 迁到 Outbox 后会丢掉原 0.5s 飞书频控间隔，已在 Outbox 买点发送侧恢复 pacing，并让 `begin_send()` 在等待后重新校验 lease/expiry；③ `send_interactive()` 仍被数据健康异常卡使用，类型化重构不能丢 P2P 拒绝后的 token cache invalidation，已恢复并补回归；④ event 自身 execution_ref 非 ready 即使最新归档仍 ready 也必须 suppressed；⑤ channels 移除 feishu 后断言零 Outbox/零 direct IO；⑥迁移测试使用 raw SQL 构造真实旧 schema 历史行，避免新 ORM 反向污染旧迁移场景。
- **当前验证**：最终业务代码树已从零完成全量 backend：`4039 passed / 80 skipped`，随后全仓 `pyflakes app tests scripts` 通过；最新聚焦 Feishu/Outbox/buy-point/watcher/migration 回归亦全绿，并覆盖并发 create-once、事件+Outbox 原子回滚、brief 失败后 durable intent 保留、渠道关闭零 direct IO、event/decision stale、typed reject/unknown、Outbox pacing 与 direct-card token invalidation。`public_repo_scan.py`、`workspace-hygiene.py`、`doc-health.py`、`git diff --check` 已通过；本次文档证据校正后再重跑 docs 门。该证据只证明作者候选通过本地门，不替代独立 U49 Review / PR required CI。
- **降级发布边界**：本段是作者执行事实与反证输入，不是独立 `Review`。PR #67 只有在 exact HEAD 的 `DegradedRelease`（`User authorization=EXPLICIT`、非空原因、同 HEAD verdict）+ required CI + latest master + 无阻塞 thread/status 全部满足时，`release_check.py` 才允许 `degraded_full_control` 放行；普通作者自检评论仍不能放行。
