# 当前交接：IMP-089 事件状态与涨停原因闭环

> 总方案v9.13；累计U01–U55；任务状态单点归[W07/IMP-089](stages/w07-simplification.md#imp-089)。现役Jev蓝图[jev-integration](ai/jev-integration.md)。历史现场由Git追溯。

## 1. 现场、授权与版本

- **本轮主任务**：IMP-089，主体已发布并真实加载；当前仅回填同任务闭环文档，不领取第二业务任务。接续前重新运行ledger-runtime-selection。
- **版本与分支**：主代码HEAD `8e86a706d33d5305bd9bff08033393dd539ff9da`；[PR #251](https://github.com/1239890829/AI-Trading/pull/251)合并为`56023b716cb4cfc2429e604136157f20541df52f`。当前文档分支codex/event-attribution-close从此最新master创建。
- **U50 降级授权回执**：2026-10-10用户明确要求修复事件待判、原文对齐、涨停池/个股详情/涨跌与梯队及同类原因缺失。2026-09-24长期DEGRADED_FULL_CONTROL未撤销；作者=发布操作者，不能称独立Review。
- **运行**：3000前端PID78495/build `sW3ONH-epZJoX5SroTTpF`；8000后端PID84510，无reload，从已发布源码启动。55份业务/测试源哈希与验收无漂移；health=degraded、休市stale如实保留。现役UI恢复副本及业务数据保留。

## 2. U49 主动审计回执 / 作者Preflight与反证

开工100条事件89 pending/11 judged；规则0可能是方向未明，不统一称AI故障或排队。辅助尝试不证明当前版本已判定；同题材未应用模型假设只绑定既有解释版本，不覆盖规则依据、不回填历史、不升级模型权威。原版Impeccable scoped clarify/polish、interaction-design与ui-state-verify分别执行；确定性日期、身份、缓存和权威硬门不委托语义模型，团队反证仍为作者自检。

默认10-09涨停池72条有原因；旧页面传休市10-10会收到旧69条无原因且伪标当天。已修默认最近交易日、显式日期日历/错日拒绝、缓存副本与同类消费者漏字段/漏展示。长原因用独立读层、正文内部滚动，不撑高主图；键盘进入滚动正文、Esc/关闭回焦，内容/身份切换后旧阅读请求不复活。目录尚未初始化、成分缺失显示降级，不伪造零只股票。

## 3. 最近验收、发布与真实运行

- **工程**：完整后端4941 passed/83 skipped/1 warning（StarletteDeprecationWarning，549.06s）；前端112文件1012项默认/UTC通过（199.40s/158.30s）。tsc/eslint/pyflakes/Next生产构建/doc-health/卫生/公开扫描通过。173项文档守卫补充验证，不与全量相加。证据根`artifacts/runs/event-reasons-20261010`；失败批保留，测试记录钟与SQLAlchemy默认回调顺序问题只在测试作用域修正，原断言及生产日期闸不放宽。
- **交互**：隔离夹具验桌面、1280×600短桌面、320/390，根无页面溢出，长原因读层/原文居中/焦点可达；短桌面图128px+28px时间轴，上下文144px内部滚动。生产构建390×844读层374×319，旧误名记录已改为实际1280×720。夹具与真实运行分开，不声称手机硬件测试。
- **准确发布**：[DegradedRelease](https://github.com/1239890829/AI-Trading/pull/251#issuecomment-6092251751)绑定主HEAD，verdict APPROVED / MERGE_IF_GATES_PASS；[PR CI38013764443](https://github.com/1239890829/AI-Trading/actions/runs/38013764443) attempt1三job completed/success、release_check通过，match-head合并于2026-10-10T01:42:11Z。上述merge的[master CI38014193299](https://github.com/1239890829/AI-Trading/actions/runs/38014193299)三job completed/success。当前文档提交仍按自己的准确HEAD发布，不提前声称其CI已通过。
- **真实读取**：2026-10-10T01:43:58Z经3000代理，默认池200/交易日2026-10-09/72条/72条同花顺原因、所有行日期一致；休市10-10查询422。100事件12 judged/87 pending/1 expired，说明字段存在，不强制消除真实未明。涨停列表及600825详情同源同日原因可见；实源桌面Canvas246px+28px、手机288px+28px，根1280×720与390×844无溢出。见runtime-probe.json、runtime-release.json及real截图。
- **运行安全与清理**：重启前只读queued/running=0、outbox pending/leased/未完成attempt=0、usage reserved/started未终态=0；7条needs_confirm和2条历史unknown保留。旧后端正常TERM，新实例无reload；没有强制模型/批补历史/启用影子/发送通知取得验收。主代码本地分支已删，远端ls-remote为空。隔离3001/8011正常停止且监听已消失，viewport已reset，真实3000/8000及恢复包保留。
- **辅助与来源限制**：Jev baseline/final两次HTTP451，没有分数或有效previousEvaluation，不称评分通过。数据源归因不等已核实新闻因果，备用源无原因明确缺失；DTO/日历闸不证明上游原始载荷日期真实性。全系统SLO、未来收益、真实手机硬件不由本轮工程通过证明。

开放世界长期候选准入继续按[持续演进](ai/continuous-evolution.md)；本轮确定性修复不构成模型或策略效果晋级。
