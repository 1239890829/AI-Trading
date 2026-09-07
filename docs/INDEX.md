# 文档索引（INDEX）

> 定位：**docs 唯一入口**。查东西先来这里；写新文档必须在此登记。
> 维护约定：文档只进不删正文，但过时/已完成/被取代的移入 `archive/`；每个条目一句话摘要，改完文档顺手更新。
> 最后整理：2026-09-07（深度调研轮，知识库重构）。

## 0. 使用地图（我要查 X → 去 Y）

| 想知道… | 看 |
|---|---|
| 系统全貌/技术栈/模块清单 | `PROJECT-MASTER.md`（08-29 基线）+ 本 INDEX |
| 数据源怎么选、备源顺序 | `data-source-comparison.md`（实测对比）→ `data-sources.md`（接入策略） |
| 某个 API 端点的参数 | `api.md`（⚠️ 端点计数停在 09-01/92 个，以 `/openapi.json` 为权威） |
| 每天怎么跑复盘 | `daily-review-sop.md` → `daily-review-checklist.md` → `daily-review/`（逐日存档） |
| 情绪/题材方法论 | `theme-sentiment-methodology.md` / `sentiment.md` / `theme-prediction.md` |
| 因子库是什么、怎么用 | `factor-library-design.md` → `factor-lifecycle-governance.md` |
| 怎么接实盘（国信） | `live-trading-guosen-plan.md` |
| trading 分组仓库值不值得用 | `repo-deep-research-20260907.md`（A/B 证据分级） |

## 1. 总纲与运维

| 文档 | 摘要 |
|---|---|
| PROJECT-MASTER.md | 全项目唯一总览（08-29 基线，细节以代码为准） |
| architecture.md | 数据流与分层设计（full.md §2.2） |
| architecture-redesign.md | 08-31 模块盘点与重构方案（实测驱动） |
| deployment.md | 部署与运维（本地开发 8000/3000 纪律） |
| api.md | REST API 92 端点（09-01 快照，计数已滞后） |
| websocket.md | WS 协议（必须直连后端，不走 Next 代理） |
| mcp.md | MCP 工具体系清单 |
| data-dictionary.md | 数据对象审计字段约定（source/quality） |

## 2. 数据源

| 文档 | 摘要 |
|---|---|
| data-source-comparison.md | 四源实测对比与选型（改数据源前必读；改完回填） |
| data-sources.md | 接入策略：主源→备源→降级链 |
| orderbook-source-evaluation.md | 五档盘口数据源评估（08-29/30，ths 无五档结论） |

## 3. 方法论与功能设计（现役）

| 文档 | 摘要 |
|---|---|
| theme-sentiment-methodology.md | A 股热点题材与情绪分析方法论 v1 |
| sentiment.md | 情绪指标清单与阶段判定（§5.5） |
| sentiment-phase-review.md | 情绪周期判定调研+纠错+优化清单 |
| theme-prediction.md | 新题材预判方法论 |
| longhu.md | 龙虎榜模块设计（口径与席位） |
| fund-flow-redesign.md | 大盘→板块→个股三级资金流重构（09-07） |
| linkage-design.md | 跨页面联动系统设计 |
| stock-picking-system-2026-09-02.md | 选股 2.0 设计与落地计划 |
| stock-picking-backtest-2026-09-02.md | 选股 2.0 网格回测（120 交易日） |
| stock-picking-backtest-2026-09-02-200d.md | 同上，200 交易日扩展窗 |
| picks-intraday-fusion-assessment.md | 精选×盘中跟踪融合可行性评估 |
| picks-replay-baseline.md | 精选 60 日回放基线快照 |
| picks-stability-sweep.md | 精选参数敏感性快照 |
| picks-take-profit-design.md | P1 冲高止盈提醒设计（待拍板） |
| factor-library-design.md | 因子库建设方案（唯一口径/评估准入） |
| factor-lifecycle-governance.md | 因子全生命周期管理制度 |
| backtest-rules.md | 回测强制禁令（代码级校验） |
| risk-management.md | 风险拦截位置与规则 |
| halt-check-risk-analysis.md | 停牌核查/异动对情绪的传导设计 |
| review-agent.md | 盘后复盘 Agent 架构说明 |
| review-strategy-update-2026-09-02.md | 复盘策略增量迭代方案 |
| daily-review-sop.md | 每日复盘 SOP（怎么判） |
| daily-review-checklist.md | 每日复盘执行清单（逐项勾） |
| theme-ladder-20260907 | 已删除（HTML 时点报告，结论在 daily-review/2026-09-07） |
| hotspot-pipeline-design.md | 热点消息捕获→传导→映射建设方案（09-07，未实施） |
| nfp-ashare-validation.md | 非农意外差→A 股适用性验证报告 |
| llm-gateway-probe.md | LLM 网关健康探针（claude_cli 别名监控） |

## 4. 本轮调研（2026-09-07）

| 文档 | 摘要 |
|---|---|
| repo-deep-research-20260907.md | trading 分组 17 仓深度调研 + 功能盘点 + 孤立功能清单 + P0/P1/P2 计划 |
| live-trading-guosen-plan.md | 国信 miniQMT 实盘接入：门槛/分步/风控红线/灰度序列 |

## 5. 健康检查与复盘存档

| 文档/目录 | 摘要 |
|---|---|
| system-review-2026-09-02.md | 09-02 全量体检与优化方案 |
| plan-review.md | 08-31 全盘计划复盘与整合清单 |
| retro-and-gaps.md | 08-29 项目欠缺/布局/技术债盘点（部分已销账，见文内注记） |
| daily-review/ | 逐日复盘报告（YYYY-MM-DD.md） |
| repo-watch/ | 仓库周期性跟踪周报 |
| push-templates/ | 飞书推送卡片模板（v2 版式定稿） |

## 6. archive/（只读历史）

> 已完成/被取代的时点性文档。**结论已吸收进现役文档或代码，引用前先确认未过时。**

- 09-03 star 审计系列 ×6（github-repo-audit-*、github-llm-agent-audit、ths-repo-gap-analysis）→ 已被 `repo-deep-research-20260907.md` 取代
- github-stars-trading-analysis.md（09-03 收敛表）→ 同上
- realtime-broker-feasibility-2026-09-01.md → 已被 `live-trading-guosen-plan.md` 取代
- architecture-linkage-plan / minute-chart-plan / assistant-optimization-plan / ui-redesign-plan → 计划已执行进代码
- 更早：full-project-review / system-review / theme-audit（09-01 批次）
