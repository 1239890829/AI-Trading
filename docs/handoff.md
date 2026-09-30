# 当前交接：BUG-009 晨报今日计划真实消费链

> 唯一状态在 [W05/BUG-009](stages/w05-agents.md#bug-009)。上一轮 IMP-032 已由 PR #185/#186 发布，RSH-031 第一版由 PR #182 合并；两者的前向效果仍归原 owner，不因本轮改写。

## 1. 权威入口与授权

读取链：AGENTS → INDEX → 本 handoff → 总方案/相关蓝图 → 总账 §5.9/§6.0 → stage。用户已确认全域方案 v1.3、实施方案 v9.13/U52–U55，累计要求 U01–U55。Jev 现役蓝图在 docs/ai/jev-integration.md，长期治理在 docs/ai/continuous-evolution.md。

**U50 降级授权回执仍有效**：用户 2026-09-24 允许本机 Codex 在 `DEGRADED_FULL_CONTROL` 承担规划、U49 作者反证、实施、自审、PR/CI、发布、合并及清理，直到明确撤销。准确 HEAD 的 `DegradedRelease` 必须明示作者自审，不能冒充独立 Review；完整本地门禁、三项 required CI、release_check、合并后 CI 和分支清理不降低。授权不覆盖付费源/模型、真实通知、券商、部署或生产库。

本轮在 `master@f8e103d614a78cb1a20217eb04e7f7891247c485` 运行 selector，G0–G4 无 actionable blocker，按最低普通非阻断门领取 G2/BUG-009 唯一主任务；无硬依赖或效果前置。同门无其它竞争项。实施分支 `codex/bug009-morning-plan`。

## 2. 本轮实现与 U49 作者反证

原 `_daily_plan` 两路导入不存在的模块且被 `suppress(Exception)` 吞没，晨报仍输出看似正常的空计划；页面从未消费 `daily_plan`。现在从现行复盘/改进项/议程存储按上一交易日读取，三路分别标 `available/empty/error`。行动项只取仍关联当前报告的未完成行，排除未来，按最终结果最多 3 条；晨报保持无 LLM 规则拼装与来源日期，页面展示同一只读投影。旧版简报缺来源状态时提示不可用；晨报 HTTP 404 与读取失败分开提示。

**U49 主动审计回执 / 作者反证**：核了死导入、孤儿改进项、跨日污染、损坏 JSON、缺报告、部分来源失败、旧版简报、API 404/非 404 与页面消费者；没有新增计划写入、业务动作或模型调用。仍需按准确 HEAD 与 CI 对最终差异自审，不能称独立审核。传播核对：W05 与本 handoff 已更新；细功能审计 X30/X32 已指向 BUG-009，无需改对象归属；总方案、INDEX、AGENTS/Skills 与 Jev 蓝图没有新增长期规则或入口，不适用。

本机现有 `data/ashare.db` 以只读连接核 2026-09-23：复盘、未完成项、议程均可读，行动项最终返回 3 条。该观察不等于生产版本已加载或前向效果验收。隔离专项后端与前端通过；完整门禁、PR 与发布回执以本轮最终结果补记。首次本机全量 pytest 的 5 项无关失败来自忽略的 `backend/data/trade_calendar.json` 停在 2026-09-24，使合成 marketdb 被当前日期新鲜度闸门判陈旧；另 1 项是本任务修掉死导入后旧测试豁免应撤销，已修。保持闸门不变，改在不携带运行数据的干净检出全量复验。

## 3. 发布与后续

本轮尚在发布收口，完成准确 PR/HEAD `DegradedRelease`、三项 required CI、`release_check.py`、合并后 master CI 和分支清理后才可称任务完成。未获部署授权，不加载生产服务。新前向反例回 W05/BUG-009 原 owner；下一轮必须重新运行 selector，只领取一个主任务。

- **当前主门**：G2
- **主切片首选**：BUG-009
- **同门竞争项**：无
