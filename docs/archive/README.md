# docs/archive

一次性过程/评审文档的归档（2026-09-01 文档收口，评审 D 系列）。

这里的文档记录当时的方案、评审结论与实测数据，**不再随代码演进更新**；
现行权威口径一律以下列活文档为准：

- 总览与阶段状态：`docs/PROJECT-MASTER.md`、`AGENTS.md`
- API/WS 契约：`docs/api.md`、`docs/websocket.md`
- 数据源口径：`docs/data-sources.md`
- 架构决策：`docs/architecture.md`、`docs/architecture-redesign.md`
- 联动设计：`docs/linkage-design.md`、`docs/architecture-linkage-plan-2026-09-01.md`
- 待办池：`docs/retro-and-gaps.md`

| 归档文档 | 性质 |
|---|---|
| full-project-review-2026-09-01.md | 全项目系统性评审（P0/P1 已执行） |
| system-review-2026-09-01.md | 功能与性能评审（四类 19 项，第 1/2 批已执行） |
| theme-attribution-review-2026-09-01.md | 题材归属比对与涨停归因核实（修复已落地） |
| theme-audit-2026-09-01.md / .json | 题材审计明细（59688 条修正清单；同目录 `theme_audit.py` 可重跑再生） |

归档脚本（一次性工具，恢复使用前需修正相对路径计算）：

| 归档脚本 | 性质 |
|---|---|
| akquant_lab.py | akquant 通路验证（P0 结论已留档 `backend/requirements.txt` akquant 注释；P2 回测化时恢复） |
| patch_eval_rolling.py | 2026-09-07 首批因子报告 rolling 字段后处理（evaluate_factor 已内置，仅历史报告迁移兜底） |
| theme_audit.py | 题材概念全量审计与修复（2026-09-01 一次性；产物见上表 theme-audit 明细） |
| verify_akquant.py / verify_ta.py | akquant/ta 指标精度实测（结论留档 requirements.txt 注释） |
