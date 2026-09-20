# W03 执行快照一致性

> 定位：最终融合方案的阶段任务文档；由 [总账 §6.0](../retro-and-gaps.md#60-阶段索引) 唯一索引。任务状态只在本页更新。
> 调度：本页 W 编号只表示领域归属；实际施工必须按 [总账 §5.9](../retro-and-gaps.md#59-阶段门优先级与跨阶段治理) 的“阶段门 → 门禁角色 → P0/P1/P2 → 门内序 → 硬依赖/效果前置”。页面上下顺序不是施工授权。

- **阶段目标**：页面、提醒、模拟动作共用可追溯执行事实，生成态保持可回放。
- **依赖边界**：W00；外推切换依赖 W02。不要求依赖阶段整体清零，按对应接口/证据切片判断。
- **排期**：本领域含 P0 核心正确性任务；实际主切片顺序只按总账 §5.9 的阶段门治理计算。

## BUG-029

**曾封板与当前开板的候选可达性一致**

- **状态**：已完成
- **优先级**：P0
- **阶段门**：G1
- **门内序**：10
- **门禁角色**：阻断
- **依赖**：无
- **效果前置**：无
- **方案依据**：用户要求断板走趋势/再接力；KB-STOCK-21；v9.3机会状态契约。
- **范围**：backend/app/picks/tradability.py、intraday_opportunity.py、pre_limit_radar.py、api/routes/picks_intraday.py、services/picks_pipeline.py、opportunity_learning.py及实际前端/归档消费者；统一“曾封板身份”与“当前封板状态”，不把全部涨停股变可成交。
- **验收**：保留日内曾封板身份，同时按有效实时状态重评；真正开板可进入评估而非自动获准，仍封死/缺时点报价/权限不足拒绝或 unknown；开板→回封→再开板的版本和去重一致，主屏/参考区/盘后组合/离线重放不自相矛盾。
- **证据**：PR #55 / 代码提交 `be3ccd1`。修前 7 类定点反例真实判红：`linkage_candidates` 在读 current 快照前按涨停池成员永久拒绝；`sealed_no_entry → board_reopen` 因 registered filter 不可达；机会缓存不含快照版本/状态；主候选与 reference 可重复；无可信时点仍可形成冲突判断。修后统一 `ever_sealed + current_sealed + snapshot_state + version`：只有 `ready + as_of` 能证明曾封板股当前已开板；current 仍封拒绝，缺时点/陈旧为 unknown，权限/涨幅/成交额硬门保持；机会缓存键加入 snapshot state/version；`board_reopen` 按 `(trade_date,symbol)` 去重；完整涨停身份从未裁剪的 theme board ladder 提取，不从 UI 配额反推。
- **真实源证据**：2026-09-18 东财涨停池 78 只中 49 只有 `break_count>0` / 开板再封证据。国芳集团 `601086`：`break_count=9`、首封 `09:53:17`、末封 `13:12:02`；腾讯 5 分钟线按前收 14.60、10% 涨停价 16.06 复核：09:55 到 16.06，10:00 低至 15.97/收 16.03（已开板），10:05–10:10 再到 16.06，午后亦出现低于封板价后再封。证明“在当日涨停池”只是 ever-sealed 身份，不能永久等同 current sealed。
- **证据版本**：机会归档从 `stock-opportunity-funnel-v1 / pit-evidence-v1` 升到 `v2`；旧 v1 行 append-only 不改写，legacy `sealed_pool=True` 仍按旧语义 replay 为 rejected；v2 以 `ever_sealed/current_sealed/snapshot_state/version` 重放，开板、回封、unknown 均可复算。未改 `LINKAGE_MIN_PCT`、成交额、临板区或涨停阈值等策略数字。通用数据契约沉淀为 `KB-ENG-117`。
- **验证**：相关后端套件与 `pyflakes app tests` 全绿；全后端 **4090 tests collected**、完整 pytest exit 0；前端全量 **693/693**、TypeScript、ESLint 全绿；干净 `npm ci` 后 Next.js 16.3.3 production build 通过；`PickCard` 最新文案定点 35/35 通过。
- **下一步**：按阶段门重算，G1 下一可行动阻断项为 **IMP-006**，之后 IMP-044。BUG-020 仍为 G0 `待条件`；其交易会话条件一旦变为可行动，必须重新抢占 G1。
- **恢复**：保留旧 v1 归档、ever-sealed 身份和 current-state 版本；若回退本片实现，不得恢复“涨停池成员=当前永久封板”“首封历史=全天不可买”或“开板=自动成交资格”。原始涨停池来源仍不直接授权，开板后必须重新通过 current 快照、权限、联动与流动性门。

## IMP-006

**统一执行快照与证据重放**

- **状态**：待交付
- **优先级**：P0
- **阶段门**：G1
- **门内序**：20
- **门禁角色**：阻断
- **依赖**：无
- **效果前置**：无
- **方案依据**：主方案 F01、§5、W03；U47 / hunting-decision-design §4.1。
- **范围**：复用 `OpportunityDecisionSnapshot`、现有 buy-point gate、AlertEvent 与 PaperTradingEngine；不新建第二事实库。页面/提醒/模拟动作引用同一当时事实，生成态和历史 decision 仍 append-only。
- **验收**：统一 snapshot/decision/version；年龄、参数与迟滞可解释；`reference_entry`（组合生成/首见参考）与 `executable_snapshot`（动作时重检）及 `PaperOrder.filled_price`（真实模拟成交）永久分名。动作时行情非 `ready` 必须 fail-closed，只留参考/拒绝证据；页面 GET 不生成新样本。
- **证据**：PR #60 的准确被审 HEAD `87b099540a4b6c3d54c56a9d57293dc1963ee450` 已于 2026-09-20 合入 `master`，merge commit `fa0185412db5c0d3f47445081da822f121cceff1`；独立网页审核回执绑定同一 HEAD（PR comment `5749077515`），结论 `APPROVED / MERGE_IF_GATES_PASS`。GitHub 原生 self-approve 因连接账号同时为 PR 作者被平台拒绝，不作为单账号仓库的必要门禁；exact-head `release_check.py`、required CI run `35502888440` 三 job 全绿且无阻断 thread 后完成合并。主体实现建立 `execution-facts-v1`：同日同场景同股 `decision_id` 稳定，`decision_version` 只由会改变判定的物质事实生成，通知发送/去重或单纯采样时间刷新不伪造新交易版本；动作时快照携 source/as_of/received_at/freshness，`stale/degraded/unavailable/unknown` 从命中降为明确拒绝；乱序晚到旧 notification snapshot 仍 append-only 保留供回放，当前读视图只按 `as_of` 选择较新版本。复核又发现“动作先发生、权威快照后落库”的孤儿引用窗口，限定整改 `d46d3ee` 已改为**完整 decision 先落库，归档失败则当拍提醒/自动模拟动作 fail-closed**；dispatch 状态继续由 AlertEvent/通道事实拥有，不反写交易 decision，`eligible` 仍进入结果标签链。
- **消费者闭环**：完整事实唯一留在 `OpportunityDecisionSnapshot.evidence`；AlertEvent、watch ledger、position plan 只存 `decision_id/version` 与必要价格身份引用，避免复制第二事实源；自动模拟仓沿同一引用下单，真实成交仍只认 order/fill；`GET /api/picks/today` 只读挂最新执行版本，PickCard 同屏分开显示“参考价 / 执行快照”，并对陈旧/降级/不可用显式标注“不是成交”。
- **验证**：主体候选此前完成完整后端 `4015 passed, 80 skipped`、全量 `pyflakes app tests scripts`；前端 `PickCard` 37/37、TypeScript、ESLint、默认时区与 `TZ=UTC` 两轮 `73 files / 695 tests`、Next.js 16.3.3 production build 均全绿；`git diff --check`、`public_repo_scan.py`、`workspace-hygiene.py`、`doc-health.py` 通过。PR #60 曾暴露一处治理测试把“当前必须 READY”写成测试前提，`488594f` 已改为测试内自行构造 READY 反例；随后准确 HEAD `9840499` 的 required CI run `35502367565` 三 job 全绿。复核新增的 P0 顺序修正 `d46d3ee` 又通过 opportunity-learning / buy-point / watcher / position-loop 定向回归与相关 pyflakes；其合入 PR 后必须重新以**最新 HEAD** 跑 required CI，旧 `9840499` 绿灯不自动继承。未改变选股阈值、仓位参数、真实券商/权限或策略收益口径。
- **送审边界**：独立网页审核与合并前 exact-head 门禁已闭环；当前只剩合并后 `master` CI run `35503775542` 的发布后验证。该 run 未完成前不把本项状态提前改成 `已完成`，也不把后续 IMP-044 的实施结果倒写到本项。
- **下一步**：等待合并后 `master` CI run `35503775542` 完成；若 backend/frontend/docs 全绿，则将本项标 `已完成` 并从最新 `master` 重算阶段门。当前已核 BUG-020 仍为真实交易时段 `待条件`，所以验证通过后的 G1 下一阻断项是 **IMP-044**；若 BUG-020 外部条件先转为可行动，G0 重新抢占。
- **恢复**：回退本片可停止新 execution contract/read-view，同时保留旧 `OpportunityDecisionSnapshot`、AlertEvent、paper order/position plan 与历史 v1/v2 证据；不得回退为“参考价=成交价”、允许 stale 行情执行或用通知发送状态制造交易版本。
- **开工前置**：已满足；本片不等待 BUG-020/IMP-044 全任务完成。
- **发布前置**：相关 BUG-020 数据字段代码契约已复用；BUG-020 的真实交易时段生产会话仍按原任务 `待条件`，不被本片冒充完成。任何外发可靠性切换仍受 IMP-044 约束。
- **v9.3/v9.8契约**：统一 opportunity/decision 版本及 first_seen/trigger/asof/失效；`first_seen` 不覆盖，盘中新物质事实形成可追溯 decision version，旧判定仍可重放。GET 只读、业务判定由后台拥有；页面打开不能生成研究样本、收费推理或动作。


## IMP-053

**猎场动态机会自动影子执行与双轨验证**

- **状态**：待条件
- **优先级**：P1
- **阶段门**：G2
- **门内序**：15
- **门禁角色**：阻断
- **依赖**：IMP-006, IMP-049
- **效果前置**：RSH-026, IMP-020
- **方案依据**：U47；hunting-decision-design §4/§6/§8；product-closure-design；现有 `watch_ledger.py`、`shadow.py` 与 `PaperTradingEngine`。
- **范围**：复用现有撮合、T+1、整手、费用、涨跌停、停牌与资金约束，为“猎场已达到可执行条件”的 decision/version 建独立 hunting-shadow scope；不污染用户手工模拟账户，也不把现有“每日精选→次日开盘” shadow 偷换语义。观察/等待/被拒绝对象只进参考轨，不因出现于猎场就自动占用模拟资金。
- **验收**：①参考轨保留 first_seen/trigger/reference_price，执行轨另记 submit/fill/reject/no_fill/expired/exit、成交价、费用、滑点与持有规则；两轨统计和 UI 名称不可混用；②动作前以 IMP-006 同版 snapshot 重检，早盘等待/拒绝可在后续新 decision version 条件成立后首次提交，开盘状态不冻结全天；③同一 decision/version 幂等，重复轮询不增仓/增样本，新一轮独立机会必须有新版本/episode；④资金/仓位采用可复算、版本化的标准化协议，不连接真实券商；⑤拒单/未成交/过期也进入分母，成交收益只能按真实 shadow fill 与合法退出计算；⑥回放时只用当时可见信息，未来低点、后续涨停和盘后原因不得倒填。
- **证据**：当前 `watch_ledger` 以首见价对照收盘，能验证“当时发现后价格怎样”，但不是成交；当前 `picks-shadow` 主要在每日精选定稿后的下一交易日晨窗按开盘价进入。两者均不能代表“猎场每个盘中可执行买点已自动模拟成交”，因此本项是明确缺口，不据此声称策略已有增益。
- **下一步**：满足以下条件再实施：IMP-006 与 IMP-049 已完成，并能用同一 opportunity/decision version 区分 reference、actionable 与动作前重检事实。条件满足后只选一个已准入、可动作的猎场场景做最小纵切，对照 reference→recheck→shadow order→fill/no-fill→exit→review 全链，再扩到其它情境。UI 只消费同版事实，不先造漂亮胜率。
- **研究边界**：工程链路完成不等于买点有效；胜率、净收益、早发现与“更低位置后涨停”的效果主张必须等待 RSH-026/IMP-020 的点时全分母、成本与 OOS/前向证据。
- **恢复**：停 hunting-shadow 新动作即可；保留 reference 记录、订单/拒单/成交审计和旧 daily-picks shadow。回退不得把参考价重命名为成交价，也不得删除失败/未成交分母。


## IMP-007

**模拟动作预检与恢复一致性**

- **状态**：待执行
- **优先级**：P1
- **阶段门**：G2
- **门内序**：20
- **门禁角色**：非阻断
- **依赖**：IMP-006
- **效果前置**：无
- **方案依据**：主方案 §5.7、§14 成交/组合/恢复
- **范围**：核模拟草稿、提交前重检、幂等与恢复；复用现有撮合/风控，不接真实券商。
- **验收**：T+1、整手、费用、停牌、涨跌停、资金和 scope 硬拒绝不退化；重复/超期动作不产生额外成交。
- **证据**：既有 paper 引擎与对账器已有硬约束；新增草稿 UI 或机制须先复现现有路径的缺口。
- **下一步**：用真实模拟动作链找过期预检或重复提交窗口，只有确凿缺口才新增机制。
- **恢复**：停新动作入口保留订单审计，按模拟账本对账恢复。
- **小动作闭环**：模拟价格输入/使用现价/改数量/预检/提交/撤单/重置分别核权限、身份和反馈；费用预估改读后端同版结果，不保留前端独立费率公式。手动券商成交记账不是自动下单，成交日期、费用、撤销与修正链单独检验；不在交易前台保留破坏性重置调试。


## IMP-048

**新闻事件来源谱系、修订与影响证据闭环**

- **状态**：待执行
- **优先级**：P1
- **阶段门**：G1
- **门内序**：40
- **门禁角色**：非阻断
- **依赖**：无
- **效果前置**：无
- **方案依据**：主方案 §5.3/§5.7/§7.3；数据源附件快讯断档与多标的关联；实施校准 v9.1 §7
- **范围**：backend/app/models/event.py、backend/app/events/store.py、extract.py、impact.py、chains.py、verify.py、ranking.py 与 backend/app/news/ 下实际采集/水位消费者；复用现有事件模型和查询入口。
- **验收**：同题多源、正文更正、晚到/撤回、一事多股均可追溯；首发/当时可见/接收时间不同字段；历史决策绑定当时版本，不被后来解释污染；LLM 归因标为待验证假设，不直接改变股票分数。
- **证据**：4e7bfcb E1：EventStore.add_event 对同指纹只补缺 url/summary；EventCard/EventDirection 有来源与方向但未展示修订关系。需核其它消费者，不能将局部缺字段等同全仓完全无版本机制。
- **下一步**：Codex 先复现“同标题原文已更正却仍读旧内容”及一事多源样本，判断现有资产可复用范围，再做最小增量。
- **实施步骤**：①明确 raw observation、事件归并、解释版本三种身份；②来源/修订追加、保留原文引用与哈希，不能依标题去重丢原始证据；③绑定多标的方向、依据、置信/未知及有效期；④按 available_at 做回放；⑤快讯水位与断档补采幂等；⑥将变化原因接入现有详情/复盘，发送仍须 IMP-044 的渠道策略。
- **发布前置**：仅观察/查询的切片不等待通知平台全完成；外发必须复用可靠投递与既有用户偏好；策略入模另经 RSH-026/IMP-020。
- **恢复**：追加式迁移先扩展/对照/切换，旧事件可读，撤回/更正不删除历史；不新建图数据库或第二套新闻平台。
- **分工**：ChatGPT 已界定缺口与消费者范围；Codex 完成复现、接口/存储设计、迁移及跨模块回归。
- **因果边界**：事实/竞争假设/反证分栏；消息首发、系统可见和价格启动先后明确。新闻标题、原文、标的池、主题跳转、L1/类别/标签与排序逐项验证；未找到新闻不等于无催化，涨后解释不算提前发现。


## 已交付基线

- 既有 execution gate、decision ledger、页面读时重检和 RSH-026 决策归档作为基线；不重复建立。

旧编号、退出理由和原文恢复入口见 [历史处置表](../archive/ledger-transition-20260917.md)。本节只留仍支撑本阶段的成果，不保存逐轮长日志。
