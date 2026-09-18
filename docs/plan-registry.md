# 历史计划去向与现行入口

> 定位：只保存历史计划的去向，历史状态不作为当前施工指令。当前最终融合方案的阶段与任务统一从 [总账 §6.0](retro-and-gaps.md#60-阶段索引) 进入，最新交付看 [handoff](handoff.md)。

## 当前管理规则

2026-09-17 用户要求按最终方案重新评估旧项，允许有依据地改进机制、流程、设计与架构；具体准入与状态规则以总账为准。阶段页是同一账本的组成，不是多份并行计划。既有功能先查代码与合并记录，低价值/不适合项可以合并或退出，不盲目照历史清单开发。

完成后保留必要结论、PR/commit、验收与限制；旧正文由 Git 或已验证恢复点保存。不再一律禁止新文档或要求完成即删；有明确用途才新建，未知资产不删除。详细内容处置见 `docs/kb/07-doc-curation.md` §3.2，旧编号去向见 `docs/archive/ledger-transition-20260917.md`。

## 历史计划去向（原登记快照）

下面的旧节号只供恢复当时版本，不代表现行账本位置；归档也不意味着验收完成。原始方案或归档中的未实施设想，必须重新通过当前准入才可施工。

| # | 原计划文档 | 定稿 | 落地状态 | 精华去向 |
|---|---|---|---|---|
| 1 | ai-agent-console-plan.md（321 行） | 09-08 | 🟢 全落地 | `summary/ai-evolution.md` §1–3；断点已闭合（`services/agent_params.py` 补「建议→变更单→生效→回滚」） |
| 2 | ai-brain-plan.md（149 行） | 09-08 | 🟢 主体落地 | `summary/ai-evolution.md` |
| 3 | evolution-brain-plan.md（193 行） | 09-08 | 🟢 全落地 | `summary/ai-evolution.md` §2（议程 A/B/C + 30 日劣化回滚 + C 类提案边界（原称沙箱，worktree 不构成 OS 隔离）） |
| 4 | strategy-evolution-plan.md（135 行） | 09-09 | 🟢 P0 全落地 / P2 延后 | `summary/ai-evolution.md` §4；延后项 → `retro` §6.3 P2-7 |
| 5 | trading-star-merge-plan-20260909.md（79 行） | 09-09 | 🟢 P0–P2 已执行 | `summary/ai-evolution.md` §5 |
| 6 | trading-agent-positioning-20260909.md | 09-09 | 🟢 已落地 | `summary/ai-evolution.md` §1（命名） |
| 7 | live-trading-guosen-plan.md（71 行） | 09-07 | ⚫ 搁置（用户决策） | **原位保留**（未来蓝图，恢复条件见文档头） |
| 8 | plan-review.md（270 行） | 08-31 | ⚫ 已归档 | `archive/plan-review.md` |
| 9 | fund-flow-redesign.md / hotspot-pipeline-design.md / news-event-module-redesign-20260909.md | 09-07~09 | 🟡 部分实施 | 已实施部分 → `summary/architecture-design.md`；**未做项 → `retro` §6.2/6.3** |

## 关联

- 文档总入口：`docs/INDEX.md`。
- 历史归档：`docs/archive/README.md`。
- 真实券商方案仅是已搁置的历史蓝图，不构成执行授权。
