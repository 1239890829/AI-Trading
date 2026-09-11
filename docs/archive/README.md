# docs/archive

一次性过程/评审文档的归档（2026-09-01 文档收口，评审 D 系列）。

这里的文档记录当时的方案、评审结论与实测数据，**不再随代码演进更新**；
现行权威口径一律以下列活文档为准：

- 总览与阶段状态：`docs/PROJECT-MASTER.md`、`AGENTS.md`
- API/WS 契约：`docs/api.md`、`docs/websocket.md`
- 数据源口径：`docs/data-sources.md`
- 架构决策：`docs/architecture.md`（架构盘点已归档：`archive/architecture-redesign.md`）
- 联动设计：`summary/architecture-design.md`（原件 `linkage-design.md` 已删除，精华并入该汇总）
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

## 2026-09-10 追加归档（文档治理首批）

| 归档文档 | 性质 | 归档依据 |
|---|---|---|
| architecture-redesign.md | 08-31 模块盘点与重构方案 | **执行状态已清零**（文档头 09-01 标注），并入现役 architecture.md |
| plan-review.md | 08-31 全盘计划复盘与整合清单 | 已被 09-02/09-08/09-09 各轮审计取代 |
| orderbook-source-evaluation.md | 五档盘口数据源评估 | 结论（ths 无五档）已进 INDEX §2 数据源口径 |
| picks-stability-sweep.md | 参数敏感性快照（08-31 区间） | 一次性过程产物，参数已定案 |

> 本次同时**删除**：`PROJECT-SUMMARY-20260909.md`（零引用 + 与 PROJECT-MASTER 重叠；git 可回滚）。
> 治理规则见 `docs/kb/07-doc-curation.md`（§6 三级处置）。

## 2026-09-10 追加归档（文档整合 B1：选股策略 + 因子体系）

| 归档文档 | 性质 | 精华去向 |
|---|---|---|
| stock-picking-system-2026-09-02.md | 选股 2.0 设计与落地计划 | `summary/stock-strategy.md` §1 |
| picks-intraday-fusion-assessment.md | 盘中融合评估（09-04） | 同上 §2 |
| picks-take-profit-design.md | 止盈档位设计 | 同上 §3 |
| stock-picking-backtest-2026-09-02.md / -200d.md | 网格回测报告 ×2 | 同上 §4（含"样本过小"结论） |
| halt-check-risk-analysis.md | 熔断风险评估 | 同上 §6 |
| baida-trend-to-dragon-20260909.md | 百大案例 → 妖股模型 | 已入 KB-STOCK-23/24，汇总留指针 |
| factor-ic-review-20260908.md | tech_score IC 复核 | `summary/factor-system.md` §3 |
| factor-library-design.md | 因子库建设方案 | 同上 §1/§5 + factor-lifecycle-governance |

## 2026-09-10 追加归档（文档整合 B2/B3）

| 批次 | 归档文档 | 精华去向 |
|---|---|---|
| B2 | longhu.md / nfp-ashare-validation.md / linkage-design.md | `summary/data-market.md` §1-2、`summary/architecture-design.md` §1（longhu 口径并入 data-dictionary） |
| B3 | ai-agent-console-plan / ai-brain-plan / evolution-brain-plan / strategy-evolution-plan / trading-star-merge-plan / trading-agent-positioning | `summary/ai-evolution.md` §1-5 |
| B3 | system-review-2026-09-02 / system-audit-20260908 / system-review-20260909 / review-strategy-update / console-three-modules-review / board-fund-page-audit / hunting-review | `summary/review-governance.md` §1-5 |

## 2026-09-10 删除记录（文档整合 B5，用户指令：精华已提炼即删）

**删除 25 份**（原件精华全部已在 `docs/summary/` 六份汇总中）：
- 选股策略类 7 → `summary/stock-strategy.md`
- 因子类 2 → `summary/factor-system.md`
- 数据源类 2 → `summary/data-market.md`
- 架构设计类 1 → `summary/architecture-design.md`
- AI 与进化类 6 → `summary/ai-evolution.md`
- 复盘与治理类 7 → `summary/review-governance.md`

**恢复方式**（需要追溯原件细节时）：
```bash
# tracked 的 20 份（原路径在 docs/ 根，git mv 前的位置）
git restore --source=HEAD -- docs/<name>.md
# 未跟踪的 5 份（baida / trading-star-merge / trading-agent-positioning / console-three-modules / board-fund-page-audit）
ls /tmp/docs-deleted-backup-20260910/
```
> 说明：删除 ≠ 丢失——**git 历史是最终保险**。判据修正：「被索引登记」≠「被依赖」，索引指针可同步清理，不构成保留理由。
