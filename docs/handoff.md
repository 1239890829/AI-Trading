# 当前交接：IMP-032 其余来源事件与投递

> 唯一状态在 [W02/IMP-032](stages/w02-notifications.md#imp-032)，当前行为合同见[信号链 §12](summary/pick-signal-chain.md#12-其余来源事件与投递合同2026-09-29imp-032)。RSH-031 第一版已由 PR #182 合并，持续效果验证仍按 W04 条件等待。

## 1. 权威入口与授权

读取链：AGENTS → INDEX → 本 handoff → 总方案/相关蓝图 → 总账 §5.9/§6.0 → stage。用户已确认全域方案 v1.3、实施方案 v9.13/U52–U55，累计要求 U01–U55。Jev 现役蓝图在 docs/ai/jev-integration.md，长期治理在 docs/ai/continuous-evolution.md。

**U50 降级授权回执仍有效**：用户 2026-09-24 允许本机 Codex 在 `DEGRADED_FULL_CONTROL` 承担规划、U49 作者反证、实施、自审、PR/CI、发布、合并及清理，直到明确撤销。准确 HEAD 的 `DegradedRelease` 必须明示作者自审，不能冒充独立 Review；完整本地门禁、三项 required CI、release_check、合并后 CI 和分支清理不降低。授权不覆盖付费源/模型、真实通知、券商、部署或生产库。

本轮在 `master@f91d2990aa45a659e9f3db65a4af35aaa91877e7` 运行 selector，领取 G2/IMP-032 唯一主任务，实施分支 `codex/imp032-event-delivery`。BUG-009 是本任务发布收口后的下一轮候选，不代表本轮领取第二项。

## 2. 本轮实现与 U49 作者反证

家族 B 的开板、模拟开/卖和持仓监护过去直接写晨报；真实持仓止损从离场引擎直开 Feishu 线程；报告有 REPORT 策略却无持久事件/意图。现在来源先写带 ID/版本/原 asof/记录时点的 `AlertEvent`，晨报为派生投影并留 `pending/completed` 回执；模拟成交按订单 ID 补建，不重复下单。真实止损为 CRITICAL，发送前重核规则、渠道、持仓和新鲜价格，非自选持仓可用现有全市场快照；报告为 REPORT，盘后投递，更正版本压制旧待发，历史补建不追发。其它状态按 SILENT 留痕。站内铃铛只纳入具名开板与真实持仓风险，资讯浏览和模拟动作不变成外推。

**U49 主动审计回执 / 作者反证**：补了未持仓/已恢复、规则撤销、非自选无报价、报告更正、投影失败、成交后 plan 写失败及发送结果未知窗口。来源重核不可用时只在过期前待重试，不标送达；发送已开始而结果未知不自动重发。仍须以生产前向样本核实际受理，不能用隔离测试证明送达。

本地实测（Python 3.11.12 / Node 24.14.0，后端/前端全量串行）：后端最终 **4465 passed / 81 skipped / 1 既有 Starlette 警告**（197.52 秒），全量 pyflakes 通过。中间一次全量因本 handoff 漏写累计要求范围 `U01–U55` 触发文档守卫红，补回后最终全量通过；没有放宽断言。前端 tsc/eslint、**77 文件 / 733 tests**（默认与 UTC 各一轮）及 Next 生产构建通过。文档、工作区卫生、公开仓扫描通过。Jev 作者辅助复评 correctness 7.1→7.9、reliability 7.6→8.2；分数不是独立审核或金融效果证据。

传播核对：W02、信号链、细功能审计、INDEX 与本 handoff 同步。总方案、Jev 蓝图、AGENTS/Skills 没有长期权限或方法变更，不适用。后端和前端需同版本运行；本轮未加载生产、未迁移生产库、未外发真实提醒。

## 3. 发布与后续

本轮仍须准确 PR/HEAD 的 `DegradedRelease`、required CI、release_check、合并后 master CI 与分支清理。合并后前向通知样本按源 ID/版本、投影状态与 Outbox 终态复盘；新反例回 W02 owner，不自动改机会阈值或渠道策略。下一轮正式开工须重跑 selector。

- **当前主门**：G2
- **主切片首选**：IMP-032
- **当前门候选顺位**：IMP-032 → BUG-009
