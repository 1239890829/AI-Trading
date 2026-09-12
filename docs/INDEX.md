# 文档索引（INDEX）

> 定位：**docs 唯一入口**。查东西先来这里；写新文档必须在此登记。
> 维护约定：**已完成方案的精华提炼进 `summary/` 或 `retro-and-gaps.md` §六后，原件即删除**（规范见 `kb/07-doc-curation.md` §3.2）；过时/被取代的移入 `archive/`。
> 最后整理：2026-09-12（**KB 系统性整理第一轮 + 文档缺陷修复 + 控制台面板分层**）：①**8 处 `full.md` 死锚**清除（6 份现役正文 + 本表 + README）——该文件**从未存在**，而 B/F 检查项都扫不到裸名，故新增 **F4 废弃锚名检查**（上线即实测出全部 8 处，注入验证通过）；②`retro-and-gaps.md` **完成记账下沉**（原 §一/§二/§三/§五 的 54 行已完成明细 → §一 里程碑 + 指针，明细在逐日日志；511 → 453 行），并补记 2 项**原本无出口的待决项**（P2-30 盘口 L2 / P2-31 外部付费源）；③**控制台「知识库」面板分层折叠**：79 份平铺 → `canonical`(11) / `current`(33) / `history`+`timeline`(35 折起)，`kb/07` §1 补「治理分层 ↔ 面板呈现」对照表；④门禁数字实测回填（后端 2619 项 / 171 文件、前端 429 项 / 52 文件）。详见 `.workbuddy/memory/2026-09-12.md`。
> 上次整理：2026-09-10（**全量重盘 + 执行到底**：40+ 待办归集唯一总账；8 份已完成方案文档删除；**15 处状态偏差更正**——9 低估完成度 / 3 高估完成度 / 1 断言未验证 / **1 需求口径本身不成立** / 另 1 死链清理；**P0-1 止盈 tracker** 落地；**P1-26 后端测试 17m27s→5m55s**；**P1-27 eslint 25→0 warn**；**P1-1/P1-3 板块资金 watcher 规则 + 助手工具**；**P1-19 炸板率双源收尾**；**P1-4 自选行 / P1-5 题材卡资金徽标**（东财 f62 口径，与合力口径并列不混算）；文档体检查缺——根文档 25 份 100% 登记、长表外移 784 行、死链 0）。

## 0. 使用地图（我要查 X → 去 Y）

| 想知道… | 看 |
|---|---|
| **用户教过的选股规则/历史教训/拍板过的决策** | **`kb/00-INDEX.md`（知识库，KB-ID 引用制）** |
| **盘后复盘怎么自动跑（不依赖会话）** | **后端常驻调度承担**：`review-scheduler`（交易日 **15:30**，`ASHARE_REVIEW_SCHEDULER_ENABLED`）+ `picks-intraday-review`（15:35），日程可见 `GET /api/system/schedulers`，产物 `data/review/reports/YYYYMMDD.json`。⚠️ **独立 launchd 通道已拆除**（2026-09-12，裁定 B）：`~/Desktop` 属 macOS TCC 保护目录，launchd 进程无权访问 ⇒ `runs=4` 全 exit 126、**零产出**；教训见 `kb/09-verification-pitfalls.md` KB-ENG-62 |
| **怎么执行一次复盘（LLM SOP）** | **`kb/06-review-framework.md`（七阶段复盘框架 v1.0，复盘任务先读它）** |
| 趋势转龙头连板的成因实例 | `summary/stock-strategy.md`（百大集团六维剖析+规律提炼，KB-STOCK-23/24 实证） |
| 新闻/事件模块排查与改造 | `summary/architecture-design.md` §4 + `kb/03-engineering.md`（根因/刷新方案/判定状态机/弹窗改造；剩余待办见 `retro-and-gaps.md` P0-2~P0-5） |
| 系统全貌/技术栈/模块清单 | `PROJECT-MASTER.md`（08-29 基线）+ 本 INDEX |
| 全面审查结论/重构执行计划 | `summary/review-governance.md`（09-08 四路深扫：死代码/孤立端点/闪现根因/猎场融合方案/P0-P2 计划） |
| AI 助手怎么升级成"大脑+执行层" | `summary/ai-evolution.md`（09-08：权限分级 L0-L3/四道拦截/任务中心+复盘+告警+参数+日志模块设计/研究页下线方案） |
| AI 助手更名论证（已定稿「交易智能体」） | `summary/ai-evolution.md`（09-09 P2-1：已是"进化体"的能力盘点+命名三选项+拍板记录「交易智能体·自主进化体」） |
| LLM 微调/训练资料与结合方式 | `summary/ai-evolution.md` §7（09-09：FinGPT/FinGLM/RD-Agent 资料清单+A 股实证基准+三层结合路径，近期零新增付费依赖；层 2 待算力 → 总账 P2-8） |
| 控制台三模块梳理（任务中心/参数/告警） | `summary/review-governance.md`（09-10：三模块定位+实证数据+告警运作逻辑白话版+规则评估与优化建议） |
| 板块资金页核对 + 瀑布流改造 | `summary/review-governance.md`（09-10：东财 vs 同花顺板块体系差异实证（非 bug）+ 列表→卡片瀑布流+滚动分页改造记录） |
| AI 大脑自主进化（v2，取代人工确认模型） | `summary/ai-evolution.md`（09-08：每日进化议程/盘后自动闭环三类执行/后置验证自动回滚/方向校验/红线与停机开关/10 项延伸能力） |
| 数据源怎么选、备源顺序 | `data-source-comparison.md`（实测对比）→ `data-sources.md`（接入策略） |
| 某个 API 端点的参数 | `api.md`（⚠️ 端点计数停在 09-01/92 个，以 `/openapi.json` 为权威） |
| 每天怎么跑复盘 | `daily-review-sop.md` → `daily-review-checklist.md` → `daily-review/`（逐日存档） |
| 情绪/题材方法论 | `theme-sentiment-methodology.md` / `sentiment.md` / `theme-prediction.md` |
| 因子库是什么、怎么用 | `summary/factor-system.md` → `factor-lifecycle-governance.md` |
| 怎么接实盘（国信） | `live-trading-guosen-plan.md` |
| 两个新仓库评估 / 自动化精简 / 助手大脑化 | `summary/ai-evolution.md`（09-08；**主体已落地**，状态见 `plan-registry.md`） |
| 策略进化（信号健康 / 筹码引擎 / 复盘闭环） | `summary/ai-evolution.md`（09-08；**P0 全落地**：`picks/signal_health.py` + `market/chip.py` + `review/strategy_health.py`；P2 按设计延后） |
| trading 分组仓库值不值得用 | `kb/05-repo-tracker.md`（A/B 证据分级 + 台账 diff） |
| trading 分组新增 star 怎么处理 | `summary/ai-evolution.md`（09-09 diff：22 仓去重已评后真新增 8 仓；告警时区修复+KB 补录+分级计划） |

## 1. 总纲与运维

| 文档 | 摘要 |
|---|---|
| **kb/** | **知识库（09-09 建库，唯一权威）**：`kb/00-INDEX.md` 总索引 → 选股知识 KB-STOCK / 交易教训 KB-TRADE / 工程教训 KB-ENG（**按子类分四册**：`03-engineering.md` 应用与设计 / `09-verification-pitfalls.md` 验证层 / `10-data-contract-pitfalls.md` 数据契约 / `08-tooling-pitfalls.md` 工具操作速查）/ 决策 KB-DEC；沉淀红线=对话中当轮入库；**文档治理流程见 `kb/07-doc-curation.md`（v1.6）**，**判据标准（「好文档」七条判据 / KB 收敛标准 / 卡帕西三层映射 / 非 docs 面治理）见 `kb/11-doc-catalog.md`**；体检一键跑 `python3 scripts/doc-health.py`。**状态语义：示例/题材案例一律 `📎`，不得与 `✅ 已落地` 混用**（KB-DEC-019） |
| **`.workbuddy/`（L1 原始源，非 docs 面）** | 用户 2026-09-13 定：**唯一主目录**。四个目录**各有分工**：`memory/`（逐日日志 append-only + `MEMORY.md` 长期约定，**权威**）· `reports/`（审查/复盘/性能报告，时点快照）· `artifacts/`（调研快照与变更概览，**2026-09-13 已把根部散落的 8 份 `overview-*.md` 与无主 png 归位至此**）· `trash/`（回收站，`safe-trash.sh` 唯一入口，含 `MANIFEST.md`）。⚠️ **`.workbuddy-ai/memory/` 只是平台会话注入位，内容为指针**（权威在 `.workbuddy/memory/`）。分层依据见 `kb/11-doc-catalog.md` §2/§4 |
| **plan-registry.md** | **计划文档登记表**：历史计划去向表 + 文档处理规范（**不再新建计划文档**，待办一律进 `retro-and-gaps.md` §六） |
| **retro-and-gaps.md** | **唯一待办总账**：§一 = 交付里程碑（已完成明细**只留指针**，正文在 `.workbuddy/memory/` 逐日日志）｜**§六 = 全量待办（P0/P1/P2，逐项代码核实）**｜§七 = 文档×状态偏差更正｜§八 = 计划文档处置 |
| **strategy-registry.md** | **策略级登记册（09-10 建）**：5 条策略键（`daily_picks` / `intraday_watch` / `pullback_reversal` / `triple_volume` / `two_thirty_five`）｜§0 **因子≠策略**辨析 ｜§2 逐条量化证据 + 样本环境 ｜§3 处置台账 ｜§4 衰减监控机制（P1-37/38/39）。**监控端点 `GET /api/picks/strategy-health`** |
| **summary/** | **主题汇总目录（09-10 建，6 份齐）**：`stock-strategy`（选股策略）/ `factor-system`（因子体系）/ `data-market`（数据源行情）/ `architecture-design`（架构设计）/ `ai-evolution`（AI 进化）/ `review-governance`（复盘治理）——原文档已归档，查主题先看这里 |
| PROJECT-MASTER.md | 全项目唯一总览（08-29 基线，细节以代码为准） |
| architecture.md | 数据流与分层设计 |
| architecture-redesign.md | 08-31 模块盘点与重构方案（实测驱动）· **已归档**（优先级清单已全部清零）→ `archive/architecture-redesign.md` |
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
| external-data-source-survey-2026-09-11.md | **外部付费源调研**（Tushare / FTShare / KlineShare / QuantDash + PTrade）：价格档位 + 能力矩阵 + 与本项目对比。⚠️ **均为官方公开信息，零实测**；核心结论：**四家均无 L2（最高五档），且两家与我们同源（东财/新浪/ths 二次聚合）**；🔶 **唯一未决建议 = KlineShare 旗舰版作 ths 打板备源（须先验「涨停原因」字段）已登记为总账 `retro-and-gaps.md` P2-31**（此前该待决项只存在于本文档内，无出口） |
| orderbook-source-evaluation.md | 五档盘口数据源评估（08-29/30，ths 无五档结论）· **已归档** → `archive/orderbook-source-evaluation.md` |

## 3. 方法论与功能设计（现役）

| 文档 | 摘要 |
|---|---|
| theme-sentiment-methodology.md | A 股热点题材与情绪分析方法论 v1 |
| sentiment.md | 情绪指标清单与阶段判定（§5.5）+ **历史误判案例库**（非交易日回退自指 bug 全链条 + 九条误判链 + 17 项优化清单状态） |
| theme-prediction.md | 新题材预判方法论 |
| review-methodology-20260910.md | 外部复盘视角「五层提问框架」 · **已删除**（净化并入 `kb/06-review-framework.md` **附录 A**，仍标非权威、不替代 KB） |
| kb/07-doc-curation.md | **文档整理方法论**（09-10）：分层模型/归属三问/精华五要素/计划压缩归档/双索引串联/合并拆分/三级淘汰/试运行条款 + 本仓库现状体检与首批整理候选。整理文档类任务先读。 |
| longhu.md | 龙虎榜模块设计（口径与席位） · **已删除**（精华见 `summary/data-market.md`） |
| fund-flow-redesign.md | 大盘→板块→个股三级资金流重构（09-07） · **已删除**（P0 已实施；P1/P2 待办见 `retro-and-gaps.md` §6.2/6.3） |
| linkage-design.md | 跨页面联动系统设计 · **已删除**（精华见 `summary/architecture-design.md`） |
| stock-picking-system-2026-09-02.md | 选股 2.0 设计与落地计划 · **已删除**（精华见 `summary/stock-strategy.md`） |
| stock-picking-backtest-2026-09-02.md | 选股 2.0 网格回测（120 交易日） · **已删除**（精华见 `summary/stock-strategy.md`） |
| stock-picking-backtest-2026-09-02-200d.md | 同上，200 交易日扩展窗 · **已删除**（精华见 `summary/stock-strategy.md`） |
| picks-intraday-fusion-assessment.md | 精选×盘中跟踪融合可行性评估 · **已删除**（精华见 `summary/stock-strategy.md`） |
| picks-replay-baseline.md | 精选 60 日回放基线快照（**结论版 116 行**：稳定性四策略对照 / 组合轨迹 / 梯队阶段分布）｜逐日明细（784 行表）已外移 `data/picks/replay-baseline-detail-20260831.md` |
| picks-stability-sweep.md | 精选参数敏感性快照（08-31 区间）· **已归档** → `archive/picks-stability-sweep.md` |
| picks-take-profit-design.md | P1 冲高止盈提醒设计（待拍板） · **已删除**（精华见 `summary/stock-strategy.md`） |
| factor-library-design.md | 因子库建设方案（唯一口径/评估准入） · **已删除**（精华见 `summary/factor-system.md`） |
| factor-lifecycle-governance.md | 因子全生命周期管理制度 |
| backtest-rules.md | 回测强制禁令（代码级校验） |
| risk-management.md | 风险拦截位置与规则 |
| halt-check-risk-analysis.md | 停牌核查/异动对情绪的传导设计 · **已删除**（精华见 `summary/stock-strategy.md`） |
| review-agent.md | 盘后复盘 Agent 架构说明 |
| review-strategy-update-2026-09-02.md | 复盘策略增量迭代方案 · **已删除**（精华见 `summary/review-governance.md`） |
| daily-review-sop.md | 每日复盘 SOP（怎么判） |
| daily-review-checklist.md | 每日复盘执行清单（逐项勾） |
| theme-ladder-20260907 | 已删除（HTML 时点报告，结论在 daily-review/2026-09-07） |
| hotspot-pipeline-design.md | 热点消息捕获→传导→映射建设方案（09-07） · **已删除**（P0 快讯流已实施 c84d382；P1/P2 待办见 `retro-and-gaps.md` §6.2/6.3） |
| nfp-ashare-validation.md | 非农意外差→A 股适用性验证报告 · **已删除**（精华见 `summary/data-market.md`） |
| llm-gateway-probe.md | LLM 网关健康探针（claude_cli 别名监控） |
| factor-candidates.md | 候选因子登记册（原 `app/factors/candidates.py` 迁出，纯文档；治理见 factor-lifecycle-governance） |
| factor-ic-review-20260908.md | tech_score 权重 IC 复核（500 只等步抽样 × 近 60 交易日，T+5 Spearman） · **已删除**（精华见 `summary/factor-system.md`） |

## 4. 本轮调研（2026-09-07）

| 文档 | 摘要 |
|---|---|
| repo-deep-research-20260907.md | trading 分组 17 仓深度调研 + 功能盘点 + 孤立功能清单 · **已删除**（结论与台账 diff 见 `kb/05-repo-tracker.md`） |
| live-trading-guosen-plan.md | 国信 miniQMT 实盘接入：门槛/分步/风控红线/灰度序列（**已搁置 09-07，恢复条件见文档头**） |

## 5. 健康检查与复盘存档

| 文档/目录 | 摘要 |
|---|---|
| system-review-2026-09-02.md | 09-02 全量体检与优化方案 · **已删除**（精华见 `summary/review-governance.md`） |
| plan-review.md | 08-31 全盘计划复盘与整合清单 · **已归档**（已被 09 系列审计取代）→ `archive/plan-review.md` |
| retro-and-gaps.md | **唯一待办总账**（08-29 立项，2026-09-12 收敛）：§一 历史里程碑（只留指针）｜§六 待办明细｜§七 偏差更正｜§八 计划处置 |
| system-review-20260909.md | 09-09 系统全面审查（基线含猎场批次 A+B、进化 P0/P1） · **已删除**（精华见 `summary/review-governance.md`） |
| hunting-review-20260909.md | 09-09 猎场实盘台账复盘（37 只 × 涨停池 34 家逐笔对照） · **已删除**（精华见 `summary/review-governance.md`） |
| daily-review/ | 逐日复盘报告（YYYY-MM-DD.md） |
| repo-watch/ | 仓库周期性跟踪周报 |
| evolution/ | 进化议程每日执行日志（YYYY-MM-DD.md） |
| push-templates/ | 飞书推送卡片模板（v2 版式定稿） |

## 6. archive/（只读历史）

> 已完成/被取代的时点性文档。**结论已吸收进现役文档或代码，引用前先确认未过时。**

- 09-03 star 审计系列 ×6（github-repo-audit-*、github-llm-agent-audit、ths-repo-gap-analysis）→ 已被 `kb/05-repo-tracker.md` 台账取代
- github-stars-trading-analysis.md（09-03 收敛表）→ 同上
- realtime-broker-feasibility-2026-09-01.md → 已被 `live-trading-guosen-plan.md` 取代
- architecture-linkage-plan / minute-chart-plan / assistant-optimization-plan / ui-redesign-plan → 计划已执行进代码
- 更早：full-project-review / system-review / theme-audit（09-01 批次）
