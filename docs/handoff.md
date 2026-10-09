# 当前交接：IMP-087 三模块时点与依据闭环

> 总方案 v9.13；累计 U01–U55；唯一任务状态归 W03/IMP-087。Jev 现役蓝图 [jev-integration](ai/jev-integration.md)；UI 现役入口 [原版技能与UI方案](product/ui-original-skills-plan-20261008.md)。历史绿色不冒充本轮通过。

## 1. 现场、授权与版本

- **当前主门**：无（G0–G4当前无可行动项）
- **本轮主任务**：IMP-087
- **主切片首选**：无
- **基点与分支**：2026-10-09 已同步 `origin/master` 的 `b3bca78177b18fe0c3c5e210ede6d4db831a8b77`；实施分支 `codex/selection-evidence-closure` 已清理。本次同任务文档分支 `codex/selection-evidence-close` 从最新 `origin/master` 的 `f44e24b5868cd4bdb3dfbab112d6c0369a3cdd97` 创建。主体代码提交 `51264720d6379cab6be9eb3104734045b64e526e`；最后消费者说明提交 `7731434555ac2bcdf47d2e1645734880837ab0a2`。实施发布 HEAD `245db8c25e4ff3db643b9c8a78edb7b3e78d6d5e`，已通过精确回执与三项CI。现为同任务的发布事实文档收口，业务源码不再修改。
- **已发布依赖**：IMP-086 的 [PR #246](https://github.com/1239890829/AI-Trading/pull/246) 合并为上述基点，post-merge CI `37906036497` 的 backend/frontend/docs 均成功，原分支已清理。
- **U50 降级授权回执**：2026-09-24 Codex `DEGRADED_FULL_CONTROL` 长期授权未撤销；本轮用户在三模块现状核查后要求“开始吧”。作者等于发布操作者，非独立 Review。精确 HEAD 的 DegradedRelease、本地完整门禁、required CI、release_check 与 post-merge 核验保持。
- **边界**：原选股评分权重、阈值、模型、资金、撮合与风控保持。复用原页面、复盘表、AlertEvent/Outbox；不增加推荐入口。趋势/独强战法准入、前向研究与全系统 SLO 仍由原研究 owner 和 IMP-019 维护。
- **运行**：8000 后端 PID `35226`，无 reload；3000 前端 PID `45504`，绑定 `0.0.0.0`，production build `JUkR7hL9_QIirwDcpKo0c`。服务端按原配置传递代理 token，不暴露到浏览器。最终消费者说明已构建并正常重启。隔离 UI 服务3001/8011已正常停止，真实3000/8000保留。
- **真实读取边界**：通知代理 GET 200、当前 count=0，风险核对 `unknown`、读取最近50条。新启动后快照尚未就绪，health=`degraded`；不把盘后缺数据称为有效实盘，也不触发补造选股、模型或外发通知取得验收。
- **恢复与迁移**：2026-10-09 正常升级 `e2c6a8f4b9d1 → f2a7c9e4b6d8`；旧复盘 56 条全部保留。原表新增可空上下文与版本，同代际幂等、新代际追加。迁移前 SQLite 备份在忽略产物，SHA-256 `4da665e70bcf0752d93765c41ea8aa8722e4d306935376a40c3351559e20eb8a`。降级阻断删除新版本证据；恢复不得盲目覆盖后续写入。既有 `ui-current-saved-20261008` 与原 UI 恢复包保留。

## 2. U49 主动审计回执 / 作者 Preflight 与反证

开工核当日生成、源时间、归档、复盘、通知与风险的真实消费者。未来日期或全天低点不能证明生成后的可用信息或成交；缺项的50分占位不是已测中性；同股多个题材不能占多个正常名额。消息原事实与当前行情分开，源观察钟与首次记录钟分开。

隔离反例覆盖未来/紧凑日期、旧 NULL 上下文、同日新版本、窗口缺失、区间交集、当前版本先选后限量、主题重复、归档版本碰撞、每日依据丢失、迟到消息、并发首次已读、旧回包、坏行情消耗冷却、持仓缺成本、健康 unknown 盲重发和部分失败回滚。原买点/交易消费者与原 Outbox 不绕过。多个实现关注面代理交叉核验，但不冒充独立发布审核。

技能分别按原职责使用：ledger-continue 与 task-handoff 管登记/接续/事实；living-system-governor 管现状、最小修补与退出边界；interaction-design 管单出口、反馈与状态；原版 Impeccable 管明确表达、失败态与窄屏加固。TypeSafe 适用性已核，确定性时间/身份/风控不交语义模型。未开展整版重设计，也不宣称所有视觉技能已完整执行。

最后实际 UI 验收复现完整判读的64位版本摘要溢出：320px 正文宽270px、scrollWidth435px。增加长词断行后270/270，390px为340/340，关闭按钮36×36。原消息 date+version 链接到该版本暂无复盘，不能回落当前结果。每日/盘中同股保留一个主卡，资金缺项显示缺项且没有资金评分 meter。角色统计改为同版同窗可信价格观察，盘中卡改用每日原生成时点说明；两项反例先判红后修复。抽屉分时段说明限定入选消息源钟，其他消息沿事件钟。

## 3. 本轮证据与未验证范围

- 后端冻结主体：完整 pytest **4886 passed / 83 skipped**（403.24s），1条原 Starlette 弃用警告；`pyflakes app tests scripts` 通过。原参数化 import-lint 跳过保持，不计作新增覆盖。
- 前端含角色/生成说明的冻结版：TypeScript、全量 ESLint 通过；默认与 UTC 两轮均 **100 files / 916 passed**（150.61s/139.72s）。最后通知时钟说明仅一处文案变更，再验27项通知消费者、TypeScript/全量ESLint及最终 Next production build 8静态页通过；不把前一轮全量冒充文案后的重新全量。准确最终 PR HEAD 仍必须跑完整三项 CI。
- 迁移现场：备份后正常停止旧服务；升级后表头版本和56条旧记录核对一致。未删除历史或改写旧 NULL 为可信结果。重启前 pending/leased Outbox、queued/running Agent 均为0；既有待人工确认记录保留。
- 实际浏览器：隔离样本明确标“非真实行情”，用于320/390/1280px的证据、缺项、版本链接和布局；不写入生产。最终构建核原判读正文断行、资金缺项、同股一张卡、原版本复盘入口及两处更正文案。320px根宽320/scrollWidth320；正文270/270，390px正文340/340；1280px每日详情670/670。关闭36×36，资金缺项无meter。屏幕截图与完整日志只进忽略产物。
- Jev 主体 baseline、主体复评与最终消费者修正复评均返回 HTTP 451、isError=true，无评分或有效 previousEvaluation。保留工具缺口，不宣称模型评分通过；确定性门禁、作者反证和精确发布流程不降低。
- CI成本记录：实施PR与post-merge各一次attempt，无重跑；backend job 3m35s/3m31s（pytest169.54s/172.86s），frontend job均2m27s，docs14s/13s。安装/步骤详情留CI紧凑日志；账户可用Actions余额未知，未启用付费，不据两轮波动宣称节省。
- 本轮工程不证明策略上涨概率、成交收益或全系统 SLO。未强制生成真实候选/模型/外发；历史未绑定复盘、缺源和 unknown 保留真实边界。
- 实施发布：[PR #247](https://github.com/1239890829/AI-Trading/pull/247) 的 DegradedRelease `6079004111`、PR CI `37917179673`三项completed/success、release_check与match-head合并已完成；合并SHA `f44e24b5868cd4bdb3dfbab112d6c0369a3cdd97`，post-merge CI `37917786755`三项completed/success。源分支已本地删除，远程由平台合并后自动删除且已核不存在。文档守卫149项通过（11.67s），doc-health/workspace-hygiene/公共仓扫描通过；本次同任务文档收口仍走独立准确HEAD回执与required CI，不复用实施PR的绿灯。

## 4. 传播与后续接续

传播核对：实施方案、产品闭环、猎场设计、细功能审计§41、数据源契约、W02接口和 W03 已更新。任务状态只在 W03；W02不复制状态。无新文档、模型、权限或治理入口，INDEX/AGENTS/协作 Skills/Jev 蓝图不适用变更；既有入口保持。原设计技能权威不更改。

本轮只收口 IMP-087。实施发布后selector静态/运行结果均无G0–G4可行动项；待条件研究与全系统效果不被冒充完成。下一轮按 [ledger-continue](../skills/ashare-ledger-continue/SKILL.md) 从最新 master 重算运行条件，领取唯一合法主任务；不因为本轮仍有未来观察条件而继续全仓循环。长期候选准入归 [持续演进](ai/continuous-evolution.md)。已结束的34个本任务pytest临时目录清理约1.18GB，保留紧凑证据、迁移备份和原UI恢复包；运行服务与其他worktree未清理。
