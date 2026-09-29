# 当前交接：BUG-016 通知解释与状态一致性

> 唯一任务状态在 [W02/BUG-016](stages/w02-notifications.md#bug-016)，读侧合同见[信号链 §11](summary/pick-signal-chain.md#11-通知解释与浏览状态合同2026-09-29bug-016)。RSH-031 第一版已由 PR #182 合并，持续效果验证仍按 W04 条件等待。

## 1. 权威入口与现行授权

读取链：AGENTS → INDEX → 本 handoff → 总方案/相关蓝图 → 总账 §5.9/§6.0 → stage。用户已确认全域方案 v1.3、实施方案 v9.13/U52–U55，累计用户要求范围 U01–U55；Jev 现役蓝图在 docs/ai/jev-integration.md，长期治理在 docs/ai/continuous-evolution.md。

**U50 降级授权回执**：用户 2026-09-24 明确允许本机 Codex 在 `DEGRADED_FULL_CONTROL` 承担规划、U49 作者反证、实施、自审、PR/CI、发布、合并及清理，直到明确撤销。准确 HEAD 的 `DegradedRelease` 必须明示作者自审，不能冒充独立 Review；完整本地门禁、三项 required CI、release_check、合并后 CI 和分支清理不降低。授权不覆盖付费源/模型、真实通知、券商、部署或生产库。

本轮从 `master@d03d39e79815a1d8cbbd9957659e594dc06b34a6`（PR #183）领取 G2/BUG-016 唯一主任务，实施分支 `codex/notification-state-clarity`，业务提交 `2bb4e98`。selector 在本轮收口后给出 G2/IMP-032、BUG-009 顺位；这是下一轮候选，不代表本轮已领取第二个任务。

本轮最终本地实测（Python 3.11.12 / Node 24.14.0，后端/前端全量串行）：后端 **4456 passed / 81 skipped / 1 既有 Starlette 警告**（135.40 秒），全量 pyflakes 通过；前端 tsc/eslint、**77 文件 / 733 tests**（默认 43.24 秒、UTC 42.86 秒）及 Next 生产构建通过。文档、工作区卫生、公开仓扫描通过。Jev 作者辅助复评 correctness 7.7→8.1、reliability 7.6→7.9；分数不是独立审核或金融效果证据。

## 2. 本轮交付与 U49 作者反证

**U49 主动审计回执 / 作者反证**：原空态把无归档说成链路未跑，把“通过/去重但列表空”径直说成故障；清除 tooltip 称清浏览器缓存可恢复，与服务端权威水位冲突。通知来源失败时独立诊断可能掩盖故障，刷新失败仍显示旧未读数；外部受理与站内已读未区分。逐股拒绝原因缺原决定 ID/版本，且旧文档仍误称仅买点进入通知中心。

本轮复用既有 AlertEvent、Outbox 和 notification 决定归档：个股通知带来源、触发时刻与条件失效说明；飞书 pending/accepted/unknown/expired/suppressed/failed 分开，accepted 只称渠道受理，附时效与必要原因。站内已读、清除和外部受理互不代替；服务端同步失败明示当前页暂存。按需只读诊断即使列表非空也可看逐股原决定/版本，缺记录标未知。来源/刷新失败不把空数组或旧列表冒充当下无机会。triage ignore 只称“建议降噪”，保留原事件，不私改推送口径。

传播核对：W02、细功能审计、信号链、INDEX 与本 handoff 已同步；总方案/Jev/AGENTS/Skills 无长期规则或权限变更，不适用。后端与前端同版本更新前，旧客户端可忽略新增字段；新按需诊断入口需后端新版本。

## 3. 验收与当前运行边界

本地隔离测试不等于生产运行或真实飞书送达。本轮未加载生产、未迁移生产库、未外发真实提醒。合并后的前向通知样本可按原事件/决定版本复盘；新反例回 W02 owner 复核，改变策略阈值或渠道偏好应单列。PR 的准确 HEAD `DegradedRelease`、required CI、release_check、合并后 master CI 与分支清理以 GitHub 本轮回执为准。

- **当前主门**：G2
- **主切片首选**：IMP-032
- **当前门候选顺位**：IMP-032 → BUG-009

以上为 BUG-016 工程收口后的候选，不代表已领取；下轮按新授权重算。
