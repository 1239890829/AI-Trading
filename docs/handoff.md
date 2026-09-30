# 当前交接：IMP-049 统一机会契约与 UI 前置

> 唯一任务状态在 [W04/IMP-049](stages/w04-research.md#imp-049)，契约与消费者边界见 [猎场 §4.2](product/hunting-decision-design.md#42-统一机会证据读模型-v1imp-0492026-09-30)。全站改版归 [W07/IMP-050](stages/w07-simplification.md#imp-050)，不能用本片替代。

权威入口：[实施方案](implementation-plan.md) v9.13、[产品闭环](product/product-closure-design.md)、[细功能覆盖](product/feature-closure-audit.md)、[Jev 蓝图](ai/jev-integration.md)与[持续演进](ai/continuous-evolution.md)。累计 U01–U55。

## 1. 现场、模式与门序

2026-09-30 从干净 master@0eb141a28194ba169b5b5595b8b92c62584ce4ad 同步远端；用户核对已批 UI 全域方案后明确“继续任务”。四项硬依赖均完成，IMP-049/050 原“等门序轮到”构成候选自锁，已回原 stage 修正。本轮只领取 G2/IMP-049（阻断/P1/门内序10）；IMP-049 工程完成后静态 selector 首选为 IMP-050；本轮先完成 IMP-049 准确版本发布，不开第二任务。selector 算法未改，不把自然语言条件变成自动授权。

**U50 降级授权回执**：用户 2026-09-24 的本机 Codex DEGRADED_FULL_CONTROL 仍有效，直到明确撤销。作者做 U49 反证及准确 HEAD 的 DegradedRelease，不声称独立 Review。完整本地门禁、三项 required CI、release_check、post-merge CI 和分支清理仍强制；不扩大部署、生产库写入、真实通知、券商、付费或模型/阈值准入。

## 2. 实施与验证

分支 codex/imp049-opportunity-contract；工程提交 5655f18，最终补正 fec9ea3。协调文档另提交；准确发布版本以 PR/DegradedRelease 回执为准。

- GET /api/picks/opportunities 复用原决定运行头/快照和 RSH-031 观察，保留同股多假设、未知 scenario、通知 owner 决策版本、进入条件、KB/原文/反证/缺项及回放引用。研究观察无交易 decision/version，历史只读卡片不取得当前动作资格；参考价不是 fill。
- 最新零行运行清除旧已入选行；not_collected / collected_empty / unavailable 分开。零候选原因按题材计数，不改变召回门槛或股票分母。54b7e0c 当前 Git 不可解析，未盲恢复旧提交。
- GET 冷热装配不归档样本/台账，原后台 tick 唯一写者；冻结同版价格/风险字段，intraday-top 不重拿更新报价覆盖。归档成功而台账失败仍不推进 cursor，重试不增样本；来源日/时刻不换成晚消费时刻。
- 真实消费者在既有台账折叠入口，打开才挂载；日期读取/迟到旧回包/错误隐藏旧成功/零参考价缺失/原始依据展开。不实现全站布局或新策略，不提供买入按钮。

**U49 主动审计回执 / 作者 Preflight 与反证**：核调度自锁、GET 单写者、缓存冷暖与共享对象、A/B 快照拼接、真实归档与部分写入恢复、零候选/缺数/未知、材料变化与仅时间刷新版本、同股多路径、晚到回包、空/坏 JSON/非有限参考价、引用和原执行 owner、已有鉴权/影子与准入边界。已纠正旧测试期望 GET 写样本及路由风险二次读；自检不是独立审核。

**已验证**：定向原机会/缓存/学习及新契约134 passed，追加非有限旧事实后新契约13 passed；tsc及新组件3项定向通过。真实 React 组件在隔离合成服务验证正常/合法空/失败、日期真实键盘变更、Return 展开与可见焦点；截图/request log 只在忽略 artifacts。外层通知/助手 fixture 503 保留，不称完整实源猎场验收。临时8007/3007和tab已关闭，生成文件/临时路由恢复性移出。最终干净检出后端4564 passed/81 skipped（132.75s）、pyflakes通过；初次回执名称缺失和未用模型导入已修正，不改门禁。最终前端79文件742测试默认/UTC通过（45.90s/46.22s），tsc/eslint/Next16.3.3构建通过（编译1.41s/类型1.40s）。完整后端三批（初次失败+两次修复复验）、前端两批成功及一次JSX闭合遗漏的快速tsc失败均保留日志，针对具体缺口重验；本地耗时不是产品收益。文档/selector/卫生/公开仓行为守卫109 passed（3.57s），doc-health/public scan/hygiene通过。准确发布结果归PR回执。

Jev三次有效辅助调用（baseline→复评→交付版），正确性6.4→8.2、可靠性6.5→8.3，最终微小分值变化均判unchanged；不为追分改结构；辅助评分不代表正确率或独立审阅，模型版本/tokens/费用未知。原始辅助结果/model元数据只存忽略artifacts，Jev结果不冒充效果或独立审核。GitHub Actions billing读取403，余额未知，不启用付费；当前CI触发仍为master/main/develop push与PR，门禁未削弱。

## 3. 传播与边界

W04/W07单点状态、总方案、猎场专题§4.2、细功能P07/P08、INDEX、plan-registry及本页已同步。只是已批准契约的纵切与原写者修补，不变更总体导航、Jev模型/调用/效果准入、撮合、协作或权限；AGENTS/Skills/其它机制登记册不适用新规则，原规范继续有效。新代码不另建研究/交易库。尚未采集的 lurk/relay/RPS 路径不被 GET 重扫或冒充覆盖；持续研究和后续新准入情境由原 owner 扩展。

上一轮 IMP-019 PR #194 已合并0eb141a并完成 PR/master CI 和分支清理；真实运行条件仍待补。部署继续搁置，合并不表示生产加载；未重启生产或接真实行情/费用/通知求测试绿色。RSH-031 前向效果、RSH-030 人工gold和 IMP-053 真实影子成交各自验收。

- **当前主门**：G2
- **主切片首选**：IMP-050
- **当前门候选顺位**：IMP-050
