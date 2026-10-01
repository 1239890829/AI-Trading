# 当前交接：IMP-053 猎场动态机会影子执行

> 唯一任务状态在[W03/IMP-053](stages/w03-execution.md#imp-053)；合同在[猎场§9](product/hunting-decision-design.md#9-imp-053-首个执行场景与效果证据2026-10-01)，逐动作在[细功能§20](product/feature-closure-audit.md#20-imp-053-逐动作与失败分母2026-10-01)。现役总方案v9.13/U01–U55；Jev入口ai/jev-integration.md；开放世界入口ai/continuous-evolution.md。

## 1. 现场、模式与门序

- **当前主门**：G2
- **主切片首选**：IMP-053

2026-10-01用户“继续”，干净master728391d492a2f36228bf6ebc0d0d50486104123d，IMP-054/PR #197及master CI36728691328已核success，原分支已清理。初始selector普通门为空；核IMP-006/049已完成、daily_picks active与用户定稿buy_point现役main自动消费者，原工程条件满足，W03待条件冻结已修正，重算唯一G2/IMP-053（P1、阻断、门内序15）。不是新策略准入，不把intraday_watch参考active或RSH-031观察当买点。

**U50 降级授权回执**：用户2026-09-24本机Codex DEGRADED_FULL_CONTROL授权继续有效；作者=发布操作者，作者U49自审不冒充独立Review，准确HEAD DegradedRelease/完整本地门禁/required CI/release_check/post-merge/清理不降低。部署搁置；无生产重启/迁移/真实通知/券商/付费或模型权重准入。

## 2. 实施与验收边界

独立hunting_shadow后台默认关闭；归档同版和当前行情/区间/会话/日历/风控重检，订单/资金/尝试原子提交；通知按日去重不冻结交易版本。参考与first_seen/trigger、重检与PaperOrder.filled_price分开。标准100万元/10%含费用/2名额含挂单，60秒限价有效期，过期取消退款；已确认后续交易日合法退出，跌停/停牌/未知日历保留未退出。重复版本、已开同日episode均不增仓。

纯读GET/H04账户入口展示真实失败/未成熟/历史，关闭开关仍可读；readiness机械复算执行DB、五版本、订单/费用、完整成熟分母。首片existing规则无Challenger/实验/压力成本绑定，仍blocked，不制造RSH-031收益。工程和真实运行/成熟效果分别验收。

**U49 主动审计回执 / 作者 Preflight**：反证覆盖自锁、scope风险旁路、引用与动作顺序、原子回执/资金、pending与期限、同股去重、未知、失效版本、T+1和恢复；两侧账户/对账闭集已同步。分批本地结果与准确发布事实在本任务回执回填，未完成门禁不称交付。

## 3. 传播与恢复

实施方案、猎场、产品、细功能（32声明）、INDEX、plan-registry、W03与本页同步；AGENTS/Skills/Jev/开放世界沿原边界，无新模型/权限/策略准入规则。原影子和用户main数据保留。回退停本scope新动作、保留PaperOrder/失败尝试及历史；不以Git回退撤销已成事实，schema降级须审查保留方案；全库灾备归GOV-013。本轮唯一IMP-053，不自动领取第二主任务，完成后重算selector。
