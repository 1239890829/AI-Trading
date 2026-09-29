# W02 通知可靠性与偏好

> 定位：最终融合方案的阶段任务文档；由 [总账 §6.0](../retro-and-gaps.md#60-阶段索引) 唯一索引。任务状态只在本页更新。
> 调度：本页 W 编号只表示领域归属；实际施工必须按 [总账 §5.9](../retro-and-gaps.md#59-阶段门优先级与跨阶段治理) 的“阶段门 → 门禁角色 → P0/P1/P2 → 门内序 → 硬依赖/效果前置”。页面上下顺序不是施工授权。

- **阶段目标**：持久化发送意图、真实回执、恢复和可解释的不提醒终态。
- **依赖边界**：W00；渠道能力核验。不要求依赖阶段整体清零，按对应接口/证据切片判断。
- **排期**：本领域含 P0 核心正确性任务；实际主切片顺序只按总账 §5.9 的阶段门治理计算。

## IMP-044

**Outbox、类型化回执与中断恢复**

- **状态**：已完成
- **优先级**：P0
- **阶段门**：G1
- **门内序**：30
- **门禁角色**：阻断
- **依赖**：无
- **效果前置**：无
- **方案依据**：主方案 §5.6–§5.8、W02
- **范围**：规则告警首片已持久化；补 bool 通道的拒绝/未知边界、消息标识及仍直发的机会卡路径。
- **验收**：事件与意图同事务；竞争领取、发送前后崩溃、DB 失败、超期/取消均有终态；unknown 不盲目重发，受理不等送达。
- **证据**：PR #29 修空回执真实性；PR #30 交付规则 Outbox/attempt/CAS 和恢复演练；其余入口仍开放。2026-09-20 接手核读又确认：`AlertEngine` 的规则告警已走同库 Outbox，并把 bool=False 保守落为 `unknown`；但 `Notifier.send` / `FeishuNotifier.send_interactive` 仍只返回 bool，无法区分平台明确拒绝与回执未知。买点链虽先落 `AlertEvent`，随后仍在 `buy_point.check_and_dispatch` 直接调用 `send_interactive()`，因此不具备 Outbox 的崩溃恢复/过期/取消事实；现有规则 Outbox 的 `_delivery_block` 又只理解 price/change 规则，不能直接拿来复核 `picks_buy_point`，所以本片必须复用存储与租约机制、扩展意图语义，而不是把买点硬塞进现有价格规则判定。 2026-09-20 U49 Preflight 在 `master@6cbe746750d02bd387ead71d5d85cec50445e8e5` 再沿真实链复核并新增三项同根事实：① `dispatch_alert()` 当前先 `append_alert()` 写晨报 JSON 去重，再做 `record_sighting/maybe_open`，最后才 `AlertEvent.record_trigger()`；因此 brief 已写而 DB/outbox 失败时，下一拍会被“当日已提醒”永久吞掉，且 paper/watch ledger 可能先于通知事实存在。② `picks_buy_point_channels` 默认只有 `in_app,log`，但后续 `send_interactive()` 无条件尝试飞书，配置不是外发权限的唯一事实源；迁到 Outbox 时必须把飞书是否启用收回同一 channels/rule 权威，不能继续保留旁路。③ 现有 `_delivery_block()` 对 `picks_buy_point` 会因 `_extract_value()` 返回 `None` 直接得到 `condition_no_longer_met`，所以必须按 intent kind 分支复核，不能只把卡片塞进旧规则 Outbox。
- **实施候选**：2026-09-20 用户因 Codex 暂无额度，明确授权网页当前会话临时代执行本片。候选分支 `chatgpt/imp044-outbox` 从 `master@aca43e408a9555da43de9e7f42bb10361c92b6e9` 开工，PR #67 已打开。已实现：① `DeliveryResult(accepted|explicit_rejected|unknown)`，旧 bool API 只保留兼容；② `AlertEvent.dedup_key` nullable unique + migration `f8c1d4e7a2b6`，旧事件保持 NULL；③ `record_trigger_once` 将 AlertEvent + Feishu intent 同事务 create-once，并把 `enqueue_feishu()` 主动 flush 与 commit 纳入同一并发唯一键恢复边界；④ buy-point 每票 card 先进入 `snapshot.card`，`picks_buy_point_channels` 成为唯一外发权限，默认 `in_app,log,feishu`，主链移除 direct `send_interactive`；⑤ Outbox payload 仅增加 `intent.kind/trade_date/decision_id/version`，发送前专用 recheck 同时核 event execution_ref 与最新归档 exact decision/version/ready state；⑥ explicit_rejected→`permanent_failed`，未知回执/网络/5xx→`unknown` 且不自动重发；⑦保留旧买点卡 0.5s 发送 pacing，并在等待后重新执行 begin_send expiry/lease 门；⑧保留数据健康 direct-card 的 token 拒绝失效语义。最终业务代码树本地全量 backend `4039 passed / 80 skipped`，全仓 pyflakes 退出 0。
- **下一步**：本任务随 PR #67 合并进入主干即工程闭环；当前 U50 `DEGRADED_FULL_CONTROL` 下由网页作者对最终 exact HEAD 运行 U49 反证并写 `DegradedRelease`，required CI + `release_check.py` 全绿后合并，随后必须核 post-merge master CI 并删除功能分支。合并后按总账重新选刀，不在本 PR 内迁其它事件来源。
- **恢复**：停投递器保留记录；应用回滚保留迁移与尝试历史，不清空 Outbox。买点迁移采用“单一路径切换”，不得同时保留 direct-card 与 outbox 两条外发造成双发。
- **实施步骤**：①在 `notifiers/base.py` 增加最小类型化回执契约，推荐 `DeliveryResult(outcome=accepted|explicit_rejected|unknown, reason=...)`；旧 `send()` 继续返回 bool 作为兼容层，Feishu/Outbox 使用类型化结果。`feishu.py` 仅把明确整数零码判 accepted；明确平台非零拒绝判 explicit_rejected；网络/超时/5xx/非 JSON/畸形或冲突回执判 unknown。②复用 `NotificationOutbox/Attempt`、CAS lease 与现有 event 关联，不新建第二张买点队列表；outbox payload 增加最小 `intent.kind=picks_buy_point` 与 `decision_id/version`，不新增第二份完整执行快照。③把“每票每天至多一推”的权威去重从 brief JSON 移到持久化 DB：优先在 `AlertEvent` 增加 nullable unique `dedup_key`（旧事件为 NULL），buy-point 用稳定的 `trade_date + symbol + kind` 哈希；仓储提供 create-once/返回 existing 的原子语义。晨报 alerts 退为 event 创建成功后的 best-effort 派生展示，brief 写失败不得撤销 event/outbox，也不得让下一拍重复外发。④ `dispatch_alert` 对 buy-point 先解析 rule/channel/target 并原子创建 `AlertEvent + Outbox`；创建成功后再做 `append_alert`、watch ledger、paper consumer；重复 dedup_key 直接返回“已有事实”，不得再次 paper/open 或发消息。⑤ buy-point card 在持久化前构建并放 `snapshot.card`；`picks_buy_point_channels` 成为唯一渠道权威。为保持当前实际产品语义（现代码无视 channels 仍会直发飞书），默认值应收敛为 `in_app,log,feishu`；未配置 Feishu target 时 outbox/pre-send 明确 suppressed，不旁路直发。⑥规则告警继续走现有 price/change `_delivery_block`；`intent.kind=picks_buy_point` 走专用 recheck：rule/channels 与 target 未变、仍在发送窗口、event/card/execution_ref 完整，`latest_notification_execution(trade_date)[symbol]` 的 `decision_id/version` 与 intent 一致且归档 decision 仍为 eligible/passed，执行 snapshot 仍为 ready/未超有效期；任一失效→suppressed。不得把 `picks_buy_point` 交给 `_extract_value()`。⑦发送端根据 DeliveryResult：accepted→accepted；explicit_rejected→permanent_failed（保留平台拒绝 reason）；unknown→unknown 且不自动重发。`send_started` 后进程丢失继续由 lease reconcile 归 unknown。⑧覆盖 DB commit 失败、brief 写失败、重复拍、并发 create-once、claim 竞争、send-start 后进程丢失、明确拒绝、unknown、超期、渠道撤销/目标变化、decision/version 变化、快照过期，并断言同一 dedup_key 最多一个 AlertEvent/Feishu intent/一次 paper consumer。旧 outbox/attempt/AlertEvent append-only 保留。
- **发布前置**：类型兼容/规则回归可先做；真实外发需现有用户授权范围，恢复与回放默认不外发。
- **补充验收**：队列等待期间偏好撤销、决策失效和参数版本变化；发前检查必须绑定所发载荷版本，界定发送已开始后的不可撤销边界。渠道不支持幂等/查询时保留unknown，禁止用重试次数证明恰好一次。 **顺序反例必须新增**：① brief 写成功后 DB 失败不得造成“永不再尝试”；实现后该顺序应不再存在；② DB/event/outbox 成功但 brief 写失败，发送仍可继续且下一拍不可创建第二 event/intent；③相同 buy-point 在并发/重启后最多一个 durable dedup 事实；④关闭 `feishu` channel 后不得存在任何 direct-card 网络 IO；⑤ `picks_buy_point` pending intent 不能被通用 price/change `_delivery_block` 误杀。
- **产品衔接**：接收同decision/version的状态变化而非重新选股；开板/失效/风险按用户价值和授权渠道处理。普通前台移出规则/通道调试，用户行动提示可见；LLM降噪不能拦确定性风险；同意图不得多渠道重复建事实。


## IMP-032

**其余事件来源与渠道偏好**

- **状态**：已完成
- **优先级**：P1
- **阶段门**：G2
- **门内序**：40
- **门禁角色**：非阻断
- **依赖**：IMP-044
- **效果前置**：无
- **方案依据**：主方案 §5.6–§5.8、W02
- **范围**：开板、模拟持仓、风控和日报按各自 kind 与现有 push_policy 接入可追溯事件；不一律套 CRITICAL。
- **验收**：每来源有事件/决定 ID 与合法终态；取消、更正和静默可追溯；不扩大外推面，不把资讯浏览变推送。
- **证据**：PR #185 / 实施提交 `4fb8f600e85dfff89debc63bc895daf3f71baf3f`：2026-09-29 `codex/imp032-event-delivery` 从最新 master 领取，开板、模拟开/卖及持仓监护、真实持仓止损、复盘报告按独立 kind 接现有 AlertEvent/Outbox；来源 ID/版本/原 asof/记录时点、晨报投影 pending/completed、模拟成交补建不重下单、报告更正与历史不补推均留痕。真实止损用自选报价或新鲜全市场快照复核，缺数待重核、条件消失/偏好撤销压制；当日收盘后生成的 REPORT 可在盘后发，SILENT 不外发，unknown 不自动重发。隔离测试含来源去重、投影失败恢复、撤销、非自选持仓、盘后报告、更正和收盘前旧报告；最终本地全量后端 4465 passed/81 skipped、pyflakes，前端 tsc/eslint、两时区各 733 passed、Next build、文档/卫生/公开仓扫描通过。Jev 作者辅助复评 correctness 7.1→7.9、reliability 7.6→8.2；不作独立审核或金融效果证据。
- **下一步**：PR #185 已按 `DegradedRelease` + required CI + `release_check.py` 合并为 `ac884b7b29e4be247d5cfb2db1deb222e25b596f`；合并后 master CI [run 36583856071](https://github.com/1239890829/AI-Trading/actions/runs/36583856071) 三项通过，功能分支已删除。生产未加载，真实渠道受理与前向样本待运行观察，出现新反例回 W02 重开。RSH-031 长期研究不由本任务替代。
- **恢复**：撤回新接线但保留审计；已外发内容不可用代码回退抹除。
- **U52/U55子范围（P10/P15）**：其他来源按kind接既有Outbox，保留IMP-044已完成成果；新增消费失败与补建/重试/过期进度可观测。事件持久不等所有下游完成；参考补建保留原asof与补建时点，交易过期不补历史。偏好撤销、不同scope、unknown发送、业务静默和真实失败分别验；共享消息是投影不复制事实。

## BUG-016

**不提醒原因、通知状态与呈现一致**

- **状态**：已完成
- **优先级**：P1
- **阶段门**：G2
- **门内序**：30
- **门禁角色**：非阻断
- **依赖**：IMP-044
- **效果前置**：无
- **方案依据**：主方案 §5.8、§14 通知清晰度
- **范围**：复用既有通知中心，区分无机会/硬门拒绝/链路异常/待处理/受理未知；吸收 BUG-015 的判读文案冲突。
- **验收**：从用户入口能查事实、理由、时效、失效条件和状态；拒绝/弃权可解释，未读和渠道状态不混用。
- **证据**：PR #20 与既有通知链已补入口/空态。2026-09-29 实施提交 `2bb4e98` 接现有 AlertEvent/Outbox 与逐股决定归档，补源/触发时间/失效条件/渠道受理状态及按需诊断，清除水位与服务端同步状态不再混说；纠正“无归档=未运行”“已通过却空=必然故障”“清 storage 可恢复”等过度断言。后端全量 4456 passed/81 skipped；前端默认与 UTC 各 733 passed，tsc/eslint/pyflakes/Next build/文档/卫生/公开仓扫描通过。工程隔离验收未加载生产、不代表真实通道送达或前向收益。
- **下一步**：本任务按准确 PR/HEAD 的 required CI、`DegradedRelease`、release_check 与合并后 CI 收口；之后若生产出现新反例，回原 owner 重开。改变机会策略、渠道偏好或其余来源扩面仍需单列差异与授权（IMP-032）。
- **恢复**：回退呈现或适配，不伪造机会、不抹掉失败记录。
- **实施步骤**：①复用现有通知中心和个股详情；②区分未发现、硬门拒绝、数据未知、链路失败、静默与已受理；③给时间/失效/来源和查看依据；④把点击/已读与渠道受理分开；⑤用同一组样例验证用户能解释为何没提醒，不以提醒数量提升当成功。
- **呈现边界**：用户主视图显示原触发事实、必要原因和时效；技术形状计数折叠。铃铛、已读/清除、时段、资讯浏览与机会、行情/判读双入口已按同一响应范围复核。清除水位服务端确认后持久，失败则明示当前页暂存；不删用户原事件或审计记录。
- **U52/U55子范围（P15）**：原事件/决定版本解释未提醒、静默、偏好、过期、失败与unknown；缺归档不推断“未运行”。站内未读/清除独立于外部受理，受理不称送达；部分来源失败与轮询旧结果均显式标出。其余来源、跨渠道偏好扩面归 IMP-032，不在本任务私自改变实际发送策略。

## 已交付基线

- PR #29（BUG-023）：只承认明确飞书受理码。
- PR #30（BUG-024）：冷却按北京 naive 落库解释；Outbox 首片含隔离崩溃/恢复证据，不代表全部入口完成。

旧编号、退出理由和原文恢复入口见 [历史处置表](../archive/ledger-transition-20260917.md)。本节只留仍支撑本阶段的成果，不保存逐轮长日志。
