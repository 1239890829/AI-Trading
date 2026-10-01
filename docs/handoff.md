# 当前交接：IMP-053 猎场动态机会影子执行

> 唯一状态在[W03/IMP-053](stages/w03-execution.md#imp-053)；合同见[猎场§9](product/hunting-decision-design.md#9-imp-053-首个执行场景与效果证据2026-10-01)，逐动作见[细功能§20](product/feature-closure-audit.md#20-imp-053-逐动作与失败分母2026-10-01)。总方案v9.13/U01–U55；Jev入口ai/jev-integration.md；开放世界入口ai/continuous-evolution.md。

## 1. 现场、模式与门序

2026-10-01用户“继续”；起点干净master `728391d492a2f36228bf6ebc0d0d50486104123d`，前任务IMP-054/PR #197与master CI36728691328 success已核，旧分支清理。核IMP-006/049已完成、daily_picks active与用户定稿buy_point现役main自动消费者后，修正IMP-053陈旧的工程等待状态；唯一G2阻断任务已连续完成。分支codex/imp053-hunting-shadow，代码 `8ef3cc979e6ff4c147d518ed01a43bf2aafbf40e`；准确发布事实归本轮PR/DegradedRelease，未称已部署。完成后重算普通门为空；G5/GX不自动领取，RSH-031/未来效果和human gold不被本项销账。运行条件与硬依赖不放宽，未扩大策略准入。

**U50 降级授权回执**：用户2026-09-24本机Codex DEGRADED_FULL_CONTROL授权持续有效，直到撤销。作者=发布操作者，U49作者反证不冒充独立Review；exact-HEAD DegradedRelease/完整本地门禁/required CI/release_check/post-merge/清理不降低。部署搁置，无生产重启/迁移/真实通知/券商/付费或模型权重准入。

## 2. 实施与最近验收

独立hunting_shadow后台默认关闭，既有买点归档同版与当前行情/区间/会话/日历/风控重检，订单/资金/尝试原子提交；挂单成交前再次核风控及报价身份。参考与first_seen/trigger、重检及PaperOrder.filled_price分开。标准100万元/单票10%含费用/2名额含挂单、60秒限价有效期，过期取消退款；已确认后续交易日合法退出，跌停/停牌/未知日历保留未退出。重复版本及已开同日episode不增仓。纯读GET与H04支持指定日/最近有动作日、关闭后的历史、失败/未知/未成熟及真实完整结果；不泄露main资本，不以参考价制造成交。readiness复算执行DB、五版本、订单/费用及完整成熟分母；existing规则无Challenger/实验/压力成本绑定，仍blocked。

干净检出 `d0539cd` 后端4590 passed/82 skipped（157.24s）、pyflakes通过；后续只改前台文案/固定日期测试及协调记录，后端源码一致。新增模块引起import-lint参数化skip增加，不当覆盖增强。前端84文件767项默认/UTC各通过（46.93s/46.99s）、tsc/eslint通过、Next16.3.3生产构建8.54s；开发服务先停，构建生成next-env已恢复。首轮后端漏示例配置1 failed/4588 passed，修同步而非断言；首轮前端固定快照夹具隐式依赖运行当天1 failed/766 passed，改为Date-only的2026-09-30T10:00:00+08:00，不冻结定时器，保留断言，双时区复验。失败和最终日志均保留。

真实浏览器只使用隔离合成夹具，正常/未退出/成熟扣费/未启用历史/空/失败重试及键盘展开通过；569 GET、零写请求，非实源行情或投资效果。事务并发、买/卖后崩溃回滚、挂单退款/风控与身份、日历/限价/T+1、三scope隔离、旧表迁移保留和重封假fill拒绝有真引擎证据。

**U49 主动审计回执 / 作者 Preflight 与反证**：核自锁/已满足条件被冻结、scope风险旁路、引用与动作顺序、原子回执资金、pending期限/恢复、同股去重/unknown、失效版本、T+1与当前时钟、只读副作用、陈旧指针传播。摘要格式与消费者一致，未知执行状态禁止效果汇总。Jev baseline/最终两次，previousEvaluation原样传递；正确性7.5→8.5、可靠性7.7→8.8、测试7.7→8.9、可观测性6.4→7.8，无reported regression。其低级rubric提示不代替代码诊断或独立审核；模型版本/tokens/费用未知，不当金融正确率。CI余额API403/未知，不启用付费；只计划一次PR批与必要master批，耗时归发布回执。

## 3. 传播与恢复

总方案、猎场、产品、细功能（32后台声明）、INDEX、plan-registry、W03与本页同步；AGENTS/Skills/Jev/开放世界沿原边界，无新模型/权限/策略准入规则。关闭开关暂停整个本scope执行，挂单和未退出成交保留，恢复后先核期限与当前退出资格；旧main/daily-shadow事实不删，schema降级须审查保留方案，全库灾备仍GOV-013。临时检出/pytest目录在结束且无进程/无脏树后清理，最小日志/截图/回执与Next生成文件恢复副本保留在忽略artifacts。本轮唯一IMP-053，不自动开展第二个业务任务；下轮仍从最新master重算selector。
