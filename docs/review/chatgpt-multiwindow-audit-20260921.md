# ChatGPT 多窗口执行审计（2026-09-15—2026-09-21）

> 定位：一次性证据报告，不是第二账本。任务状态、优先级与下一步仍只写所属 stage；本报告只记录跨窗口事实、反证和 KEEP/FIX/WATCH/RETIRE 判断。
> 审计方法：Living System Governor v1.2.0；证据顺序为真实运行/运行库 > 隔离实测 > 代码/测试 > Git/PR/CI > 文档/对话说明。

## 1. 覆盖边界与可追溯性

- 当前 ChatGPT 侧无法枚举 9/15—9/21 **全部已归档会话标题**；9/20—9/21 的部分项目窗口可由项目上下文恢复，其余窗口以 PR/commit/CI/账本/本机运行证据重建。故本文不把 Git 批次伪称为逐聊天窗口全文。
- 版本事实完整：以 2026-09-15 PR #1 的 base `d3b758b8` 为对比基线，到 2026-09-21 `master@6c895dd9`，仓库前进 **552 commits / 483 files / +56,398 -7,676**。
- 已核 PR 时间线覆盖 #1—#94；其中 9/19 的 Jev 主批为 #31—#34，9/20—21 的 RSH-026 主批为 #69—#85，9/21 的 IMP-020 主批为 #86—#94。

## 2. 多轮工作按阶段复核

| 阶段/时间 | 主要工作 | 事实判断 | Governor 决策 |
|---|---|---|---|
| 9/15 审计与安全基线 | 分支/CI、Agent 自动执行收权、鉴权、因子窗口、架构解耦 | 方向正确，解决了真实 P0/P1；不是为重构而重构。分支规则随后又被 master-only/PR 保护替代，旧 develop 语义已退休 | KEEP 已落地结果；RETIRE 旧 develop/历史分支心智 |
| 9/16—17 机会学习/数据/通知/UI | RSH-026 第一二批、迁移目标库 bug、选股提醒、数据身份、通知恢复 | 大部分与“事实身份/失败可见/可成交成本/不冒充收益”主方向一致；实测抓出 schema 分叉和成本接线盲区，价值明确 | KEEP；避免把阶段性实现写成长期最终态 |
| 9/19 Jev + GitHub 治理 | PR #31—34 Jev shadow/路由/verifier/gold；#35/#37/#38 安全保护 | Jev 不是 PPT：有 adapter、影子接线、真实 API smoke；但 human gold 仍 0/240，故 cascade 未开是正确克制 | KEEP shadow；EXPERIMENT human gold；WATCH assistant router 实际使用 |
| 9/19 计划/协作/演进治理 | PR #39—46 master 事实源、传播守卫、创新雷达、阶段门 | 显著减少“旧分支=当前事实”的歧义；但 handoff 后来仍逐轮堆长审计回执，说明“handoff 只留当前现场”纪律执行不彻底 | KEEP 单点事实源；FIX handoff 历史膨胀 |
| 9/20 猎场/生命周期/技能 | PR #47—59，猎场双轨、工作区卫生、Living System Governor/U49 | 与用户“动态、可参与、持续衰退重验、主动发现”方向一致；Governor skill 有真实价值，U49 多次抓到隐藏缺陷 | KEEP；GOV-027 仍未真正运行化，不能把 skill 入库当完成 |
| 9/20 IMP-006/IMP-044/U50 | 执行事实身份、durable outbox、DegradedRelease | 因 Codex 无额度引入受控网页全权链是合理降级；没有伪装独立 reviewer。代价是单作者多角色 + 多 worktree 并发，后续出现重复 PR/分支竞争 | KEEP 降级能力；FIX single-writer/cleanup discipline |
| 9/20—21 RSH-026 #69—85 | 全漏斗 denominator、D1/D3/D5、MFE/MAE、legacy recovery、公司行动、revision、数据质量、原子 run、fallback、notification header | 核心工作高价值且符合原设计：它修的是样本身份/可复算证据，不应交给 Jev 替代。**过度之处在执行形态**：17 个 PR/大量临时 worktree，切片与清理不同步 | KEEP 核心；MERGE 今后相邻同根子片；RETIRE 残留工作树 |
| 9/21 IMP-020 #86—94 | purge/embargo、35bps、trial-family/Bonferroni、overlap、PIT/digest/history、ablation、Champion/Challenger、experiment/readiness | 研究准入从“文字/布尔自证”升级为结构化证据，方向正确；重复 PR #88/#90/#91/#92 暴露并发执行卫生问题。最终 readiness 安全但执行身份契约仍可加强 | KEEP 主机制；FIX IMP-053 actual-fill identity contract |

## 3. 关键偏差与不足（按优先级）

### P0 — 条件任务没有在条件出现时重新抢占阶段门

`BUG-020` 明文要求“下一次可实际观察的 A 股交易会话”到来后，G0 必须重新优先。2026-09-21 本机产生 205-symbol minute scan、833 recorded / 814 settled 记录和当日 LHB 归档，证明真实交易日窗口已出现；当天任务窗口却继续 RSH-026/IMP-020，未做要求的盘前→上午→午间→下午→收盘 + restart 完整会话验收。

判断：不是 RSH-026/IMP-020 内容本身偏离，而是**静态 `待条件` 没有时间/事件激活机制**，阶段门重算漏掉了机会窗口。修复应落 BUG-020 + GOV-022/GOV-027，不靠人工记住。

### P1 — “没有真实 SQLite 运行库”曾是事实判断错误

PR #69 明确写过“remote Mac 没有可验证长期生产运行 DB”；后续 RSH-026 自己又定位并迁移了长期 `data/ashare.db`。本次现场复核：`data/ashare.db` 310,886,400 bytes、39 tables、`integrity_check=ok`，且运行中的 uvicorn 正持有该文件；`backend/data/ashare.db` 与 `backend/ashare.db` 才是 0B 空文件。

判断：**隔离原则是对的，数据库身份判断错了**。正确规则应是“测试写入永不碰真实库；审计/统计允许对真实库只读或一致性副本取证”，而不是“因为测试在 memory DB，所以真实 DB 不存在/不可核”。

### P1 — 当前本机运行态与 Git master 脱节

审计时远端 master=`6c895dd9`，主工作区/运行 backend 仍为 `f3556ad5`，落后 9 commits；uvicorn 自 17:10 启动且持有真实 DB。Next server 自 9/19 运行，版本为 **15.5.25**，而当前 package manifest 要求 **Next ^16.3.3**。`npm ls --depth=0` 报大量 unmet dependency；CI 因 fresh install 仍全绿。

判断：已完成/已合并不等于本机 runtime 已生效。需要“merge truth / deployed truth / runtime dependency truth”三分；受控停服务→同步 master→迁移/`npm ci`→重启→health/scheduler/DB 校验应成为运行态接收步骤。

### P1 — IMP-020 最终 readiness 缺少部分同版执行身份验收

最终 `strategy_readiness` 正确做到：research 不能伪造 fill；只接受 `source_owner=IMP-053` + `execution_scope=hunting_shadow`；匹配 strategy/experiment/candidate/cost/exit policy/closed fills；最多 `ready_for_human_review`。

但被淘汰的 promotion-v2 草案模块曾包含更严格且合理的**消费侧校验**：`strategy_version / feature_version / execution_version / cost_model_version / exit_rule_version`、`paper_order_filled_price`、cost/slippage included、`opportunities = filled+rejected+no_fill+expired+pending`、`pending=0`、`exited=filled`、net mean/median/win rate。

判断：不要复活 research-side fill builder；把这些字段写进 **IMP-053 的 execution-owned evidence contract**，由 IMP-020 readiness 消费。否则“同版成本/退出/成交净收益”仍有文字强于机器契约的差距。

### P1 — 多 worktree/重复 PR 造成真实执行噪声

审计前仍有 10 个注册 IMP-020 worktree，另有 RSH-026/IMP-044 等未注册 `/private/tmp` 仓库；磁盘占用主要包括 `imp044` 840MB、`rsh026-path` 646MB，以及多份 22–57MB worktree。远端还留 #88/#89/#90/#92 等关闭 PR 的功能分支。

同一 IMP-020 hardening 在 #88/#90/#91/#92 之间多次竞争、关闭、重开、重放，聊天里也出现“同一路径被另一执行链改回”的事实。判断：这是**执行卫生/单写者问题**，不是研究设计本身需要四套分支。今后同一 task/sub-slice 同时只允许 1 个 active worktree/PR；替代线一旦确定，先保留差异摘要再清理。

### P1 — Jev 真实可用，但价值闭环仍未完成

现场配置：`jev_enabled=true`；alert/event/assistant tool router 默认 shadow，assistant semantic verify off。项目 usage ledger 审计时已有 **172 calls / 169 ok / 3 failed**，其中 alert_triage 168、event_llm_aux 4；约 128,276 input / 7,653 output tokens，平均延迟约 856ms。另有 global usage 2 次（task_router、PR41 governance review）。本轮 live bounded smoke 再次成功，返回 `jev-1.13.0`、约 837ms。

结论：Jev“确实能用、确实用过”；但 RSH-030 human gold 仍 0/240，不能宣称 accuracy/cascade/token savings。assistant tool router 虽配置 shadow，项目 usage ledger 未见该 purpose，属于“接线存在、实际运行使用未证实”。

### P2 — Jev semantic review 没有系统进入高影响多轮任务

RSH-026 核心是数值/数据库/时间/原子性，**不应该**让 Jev 决定；不用 Jev 是正确的。可优化点是在 RSH-026、IMP-044、IMP-020 这类大批次 release 前，按需增加一次 bounded `jev-review`/semantic contract review 作为第三类反证。它只能辅助发现语义遗漏，不能替代真实 DB、pytest、CI、Codex/ChatGPT 代码审阅。

网页 ChatGPT 本身没有原生 TypeSafe/Jev tool；但在当前授权环境可以通过 Remote Desktop Commander 调本机 `jev_client`/API，本轮已经实测。因此“网页端无法使用”只对**原生工具层**成立，对“操作上完全不能用”不成立。Codex 更适合作为代码实施 owner，并可使用 TypeSafe Agent Skill；Jev 本身不是 Codex 替代品。

### P1 — 一份未合入能力被藏在旧 RSH-026 临时仓

`/private/tmp/AI-Trading-rsh026-zero-reasons` 的唯一 commit `54b7e0c` 不在 master，新增 `theme_gate_counts`，能区分题材未集中、缺官方容器、行情全不可用、容器内无可参与候选。它不是 RSH-026 结果标签核心，而是 IMP-049 的候选可解释性。审计后不删除该 39MB RECOVERY，先在 IMP-049 账本登记；其它已被主线/PR 覆盖的旧 worktree 可退休。

### P2 — handoff 历史回执膨胀

GOV-019 定义 handoff 只记录当前现场，但当前 handoff 仍保留大量 8.x/10.x 历史实施回执。它们有取证价值，但长期堆积让新窗口读取成本增加，也加剧“当前 vs 历史”混淆。应保留当前快照 + 指向本报告/PR/阶段页，历史长回执逐步归档；不是删除独有证据。

## 4. 隔离机制专项判断

### KEEP
- `tests/conftest.py` 把 `ASHARE_DATABASE_URL` 设为 `sqlite:///:memory:`，同时把 review/prediction/position-plan 等模块级输出路径重定向沙箱。
- `test_data_path_isolation.py` 维护路径清单、扫描 CWD 相对路径并带自测。
- 这些机制来自真实污染事故：测试曾把报告、gap、position plan 等写入真实 `data/`，故不是防卫过度。

### FIX
- 测试隔离与真实只读取证必须分开：测试写路径 fail-closed；审计脚本可显式 `mode=ro` 或复制一致性副本。
- 0B `backend/data/ashare.db` / `backend/ashare.db` 没有消费者，却容易让人工/脚本误判，应 RETIRE。
- 不应再把“memory test DB”推导成“没有真实运行 DB”。

## 5. 内存、缓存、依赖与临时资产

- 当前 8GB Mac 的 `memory_pressure -Q` 显示约 **69% free**；没有证据证明此刻处于持续内存泄漏。
- 单进程主要 RSS：Chrome renderer 约 **1.5GB**（当时最大）、uvicorn 约 **193MB**、ToDesk Session 约 **193MB**、Desktop Commander 两个 Node 约 **190MB 合计**。worktree 主要占磁盘，不是 1.5GB RSS 的来源。
- `apps/web/node_modules` 约 **818MB**，但当前已失配，不能当“无用垃圾”直接删：Next server 正在用旧环境。应在停服同步时 `npm ci` 重建。
- Python `pip check`：无 broken requirements。`npm audit` 因当前 npm registry mirror 不实现 audit endpoint 而无法得到有效安全结论，不能把空结果写成 0 漏洞。
- `backend/tests/__pycache__` 约 11MB、`.pytest_cache` 约 360KB，属可再生缓存；可清但收益小。
- 9/21 的 `backend/data/lhb`、`minute_decisions`、`position_plans` 是实时运行证据，保留；`docs/evolution/2026-09-21.md` 与既有 9/17/18 同类，本轮纳入 Git。
- 本轮实际 RETIRE：约 2.0GiB 已合并/被 supersede 的 worktree/临时克隆，约 4.3GiB 过期 RSH-026 SQLite 副本/备份，两个 0B 假 `ashare.db`，以及小型 `__pycache__/.pytest_cache`；本地主分支只剩 master + 当前 audit branch。唯一 `54b7e0c` RECOVERY 保留。

## 6. 已完成任务“效果是否达成”的判定

| 任务 | 工程完成 | 运行/效果完成 | 判断 |
|---|---:|---:|---|
| GOV-012 GitHub protection/security | 是 | 是（平台门禁可查） | KEEP |
| GOV-019 账本单点 | 是 | 大体是；handoff 历史膨胀仍需治理 | KEEP + FIX |
| GOV-026 workspace hygiene | 部分 | 9/20 清理有效；9/21 又积累 >2GB tmp/worktree，说明“每轮清理”未持续执行 | FIX 持续机制 |
| Jev PR #31—34 / GOV-024 | 接线是 | shadow 有真实调用；human gold/assistant usage 未闭环 | KEEP + EXPERIMENT |
| RSH-026 | 是 | 数据/证据底座大量真实运行验证；不代表策略有效 | KEEP；执行碎片化需改 |
| IMP-044 | 是 | durable delivery 工程证据充分；不证明买点效果 | KEEP |
| IMP-020 | 工程机制是 | 当前 Candidate B readiness=blocked，正确反映“不足以晋级”；IMP-053 同版执行证据契约还要加强 | KEEP + FIX consumer contract |
| Living System Governor | skill/流程已入库 | 本次审计实际使用；但 GOV-027 运行化仍待执行 | KEEP + EXPERIMENT |

## 7. 与 9/15 之前系统的全方位差异

### 之前
- 关键事实更依赖单模块/当前状态，失败分母、来源时间、历史版本与执行身份容易混在一起。
- Agent/代码执行权限、通知发送、Git 主干与 public-repo 安全边界较松。
- 研究结果常有“样本内/当前快照/参考价”被误读成可执行效果的风险。
- Jev 不存在于项目主链；协作/账本/生命周期治理较弱。

### 现在
- **工程规模**：从 9/15 基线到 9/21 master 累计 552 commits、483 files changed。
- **事实真实性**：source time/current value、point-in-time、失败/有效空集、全漏斗 denominator、append-only revision、return/fill identity 被系统化。
- **研究准入**：RSH-026 建完整机会学习证据底座；IMP-020 加 purge/embargo、35bps、trial-family/Bonferroni、overlap、PIT/digest/history、ablation、Champion/Challenger、experiment/readiness。
- **执行与通知**：reference/executable/fill 永久分名；buy-point durable outbox、typed delivery result、原子去重与 pre-send recheck 落地。
- **安全与治理**：branch protection、secret/privacy scan、required CI、release_check、U49 proactive discovery、U50 controlled degraded full-control、master 单点账本与 Living System Governor 建立。
- **Jev**：从无到统一 adapter + alert/event/assistant/verifier/research shadow；有真实 usage，但仍停在“可用/影子已用，价值未完全实证”。
- **代价**：流程和证据面明显更强，同时出现 PR/worktree 过碎、handoff 膨胀、runtime 与 master/依赖不同步的运维债。下一阶段优化重点应是**减少中间态数量、加强运行态接收与条件自动激活**，而不是继续堆新机制。

## 8. 结论

总体方向没有发生根本偏离：这几天最主要的变化是把系统从“功能很多但部分事实/证据边界松”推向“事实身份、失败分母、研究/执行/发布边界可追溯”。RSH-026/IMP-020 的核心并非过度设计；它们修复了真实的 denominator、PIT、成本、重叠、身份和覆盖问题。

真正需要纠正的是执行层：① `待条件` 没自动激活导致 BUG-020 错过 9/21 会话；② 曾错误判断无真实 SQLite；③ 同一切片多 worktree/多 PR 并发；④ 合并态与本机运行态脱节；⑤ Jev 的价值评估/assistant shadow 使用仍未闭环；⑥ IMP-053 需要承接更完整 actual-fill 同版身份契约。

这些问题都可在现有 owner 内修，不需要再建一套总架构。
