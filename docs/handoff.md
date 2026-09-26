# 当前交接：G1／IMP-040 整项能力核验收尾

## 1. 权威入口与批准范围

用户已批准全域方案 v1.3（含 v1.0–v1.2），仓库实施方案 v9.13／U52–U55；
累计用户要求范围为 U01–U55。
规划 PR #172 已合并，批准与历史拒绝见 [确认记录](review/system-plan-v13-approval-20260926.md)。
读取链：最新 master → AGENTS → INDEX → 本 handoff → 总方案/相关蓝图 →
`docs/ai/jev-integration.md`（Jev 现役蓝图）→
`docs/ai/continuous-evolution.md`（开放世界持续演进入口）→
retro-and-gaps §5.9/§6.0 → 所属 stage。任务状态、优先级与门序仅归 stage。

B 五任务主页为首选比较候选，C 四入口须对照；JEV 对自选/持仓一级拆分弃权保留。
50 子功能、18 域、16 组件族、73 源、30 后台声明是设计映射，不是固定上限或运行通过。
界面融合不合并资金、收益口径、数据事实或权限；后台先验触发、单写者、幂等、
持久结果、失败/取消/恢复、权限与维护入口。

## 2. 临时协作授权与本轮选择

**U50 降级授权回执**：2026-09-24 用户明确授权本机 Codex 在 `DEGRADED_FULL_CONTROL` 下接续实施、
U49 作者反证、自审、PR/CI、发布、合并与清理，直到明确撤销。
作者发布回执只能是绑定准确 HEAD 的 `DegradedRelease`，不得称独立 Review；
本地门禁、三项 required CI、`release_check.py`、合并后 master CI 与分支清理不降级。
授权不扩到模型费用、真实通知、券商、部署或生产数据库。

**U49 主动审计回执**：本轮 Preflight 从最新 master/PR #173/selector 核范围、依赖与
旧拒绝；作者 Review 核部分失败/休市回退、同根伪独立、财报字段混用、权限未知、
历史完成状态和测试固化，不以作者自审冒充独立审核。准确 HEAD 的复查结果见
本轮 PR 的 DegradedRelease。

2026-09-26 用户要求以后**整项任务连同所有切片同轮完成**。
本轮在 master `fb2dae38d6e460564b09b7f6105dc022535ac7e3` 上重算 selector：
static/effective 均为 G1／IMP-040，依赖 BUG-020 已完成；PR #173 的跌停日期
API 修复已合并，本轮接续其余入口和完整能力取舍，不重复那次切片。

## 3. IMP-040 交付与边界

源能力与消费者证据见 `docs/data/data-sources.md` §8.10 和
`docs/data/data-source-comparison.md` §13。09-24 跌停 THS/东财各 13、共同 11：
THS 独有 ST 2，东财独有北交所 2，故现役东财不切流；09-25 休市东财会静默回退
09-24 池。市场 API、助手和扩展交叉校验共用交易日历日期闸；助手连跌天数字段修正，
akshare 东财包装不再标为独立来源。涨停原因 09-24 THS 51/51、东财 0/52。

茅台/平安银行 2026 中报 THS 财务指标与东财各 2/2 可取，但茅台营业收入与
营业总收入同比口径不同，不能替换评分字段；现役东财报告期请求上限已兑现，
`report_date` 与 `notice_date` 分离展示。THS 估值和全市场快照维持候选；
三个 THS 接入面的预算、health、恢复与未知许可分列。未证明再分发许可、
盘中长期时效、全市场完整性或策略效果；未来采用/效果验证仍在原 owner。

本轮代码、文档与适用门禁以 `codex/imp040-complete` 的 PR 准确 HEAD 回执及
GitHub 合并记录为准。本机最终后端全量 4378 passed / 80 skipped、pyflakes 通过；
前端 tsc、eslint、两时区各 719 tests、生产构建通过；doc-health、workspace-hygiene、
public-repo scan、diff check 通过。Jev Review 首次不可达，复试获得辅助质量评分，
最低观测维度 7.2/10（置信度 0.62）；它不代替测试、独立审核或发布门。
以上本地实测不代替远端 required CI。
下一项只从合并后的最新 master 重算 selector，不在本轮直接开始 RSH-031。

- **当前主门**：G1
- **主切片首选**：RSH-031
