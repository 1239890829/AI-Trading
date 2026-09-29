# 当前交接：RSH-031 有界研究结项

> RSH-031 唯一状态在 [W04](stages/w04-research.md#rsh-031)，最终证据、负结果及重开门在[研究蓝图 §32](research/limit-up-dragon-research.md#32-rsh-031-最终研究裁决与停止条件2026-09-29)。当前无新增涨停/龙头特征准入；旧 §25/28 的未来污染排名仍由 §30 取代。

## 1. 权威入口与现行授权

读取链：最新 master → AGENTS → INDEX → 本 handoff → 总方案/相关蓝图 → [docs/ai/jev-integration.md](ai/jev-integration.md)（Jev 现役蓝图）→ [docs/ai/continuous-evolution.md](ai/continuous-evolution.md)（开放世界持续演进入口）→ 总账 §5.9/§6.0 → 所属 stage。用户已确认全域方案 v1.3、实施方案 v9.13/U52–U55；累计用户要求范围为 U01–U55。规划 PR #172 已合并，[确认记录](review/system-plan-v13-approval-20260926.md)只证明方案批准，不证明业务效果。

**U50 降级授权回执**：用户 2026-09-24 明确允许本机 Codex 在 `DEGRADED_FULL_CONTROL` 下承担规划、U49 作者反证、实施、自审、PR/CI、发布、合并及清理，直到明确撤销。每个 PR 必须有准确 HEAD 的 `DegradedRelease`，明示作者自审，不冒充独立 Review；本地门禁、三项 required CI、`release_check.py`、合并后 master CI 和分支清理不降级。授权不覆盖付费模型/数据、真实通知、券商、部署或生产数据库。

2026-09-29 用户明确要求本轮结束 RSH-031，并审查过严验收及免费替代。最新主干基点为 PR #176 合并提交 `eca8238c77a45c62e847a7432e7380d2bd4bf58c`。当前结项仅修订研究决策和文档指针；不改生产策略、数据源优先级或交易行为。

本轮本地门禁（顺序运行、无额外负载；默认 3000/8000 端口无监听服务，未运行部署验收）：后端最终 **4408 passed / 80 skipped**、pyflakes 通过；前端 tsc/eslint、默认及 UTC 各 **719 passed**、`next build` 通过；doc-health、workspace-hygiene、public_repo_scan 通过。初次后端全量因 handoff 遗漏 `U01–U55` 导致 1 项文档守卫失败，补正后定向及全量均通过；该失败不算作最终通过证据。

## 2. RSH-031 本轮结论与 U49 反证

PR #176 已交付修正后的 `rsh031_asof_audit.py` 和来源审计；本轮又核两份归档 `report.json` 的 SHA 与蓝图 §30 一致。九月和原 220 日固定等权相对早盘涨幅的 Top20 命中分别为 28/14（各 340 槽）与 299/331（各 4,400 槽）；两窗已被观察、方向不一致，不能宣称稳定增量或新盲测。免费新闻短窗仅是供应商抓取时刻假设下的敏感性。

**U49 主动审计回执 / Preflight**：原任务把完整层级机制、历史文本/题材修订、独立 holdout、成本/可成交、Jev 人工 gold 和未来 shadow fill 捆成单一完成门，造成数据可得性自锁。研究蓝图 §32 以冻结分母、无未来筛选、简单基线和明确的否决/弃权收口；效果与交易硬门继续只约束未来具体候选。免费源可供价格/触板探索，却未证明历史逐成员首次公开/修订、点时全文或反事实成交。新来源仅在改变裁决时重开，不再重复下载/扫描同一窗口。

本轮关闭不声称任何龙头机制已被完整证实或否证；没有新特征进入 IMP-049。未来确有候选时，RSH-026/IMP-020 负责效果准入，RSH-030 负责独立人工语义评价，IMP-053 负责实际 shadow fill。既有研究资产与负结果留项目忽略的 `artifacts/runs/rsh031-free-source-audit-20260927/`，不提交受限原始行或新闻正文。

## 3. 当前门序与交付边界

`python3 scripts/ledger-runtime-selection.py --json` 在本轮 W04 状态更新后返回 static/effective `G2/IMP-007`，同门候选 IMP-007 → BUG-016 → IMP-032 → BUG-009。RSH-031 已结项；本轮不自动启动第二个业务任务。下一轮从最新 master 重跑选择器。准确 HEAD 本地门禁、DegradedRelease、required CI、release_check、合并后 master CI 和功能分支清理以本轮 PR 回执为准，不能由历史 PR #176 的通过代替。

- **当前主门**：G2
- **主切片首选**：IMP-007
