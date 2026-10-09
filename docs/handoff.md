# 当前交接：IMP-088 原地旁览与首次加入事实

> 总方案v9.13；累计U01–U55；任务状态只归W07/IMP-088。现役Jev蓝图[jev-integration](ai/jev-integration.md)，设计权威见[原版技能方案](product/ui-original-skills-plan-20261008.md)。

## 1. 现场、授权与版本

- **当前主门**：G2
- **本轮主任务**：IMP-088
- **主切片首选**：IMP-088
- **顺位**：G2非阻断/P1/门内序112；运行selector与静态均为G2/IMP-088，无另一可行动主任务。
- **基点与分支**：已同步origin/master的2649709bc469b3d11b38234ca7916595c15826c6；codex/contextual-workspace。依赖IMP-087已通过PR247/248发布，原分支清理，不重复实施。
- **U50 降级授权回执**：2026-09-24 Codex DEGRADED_FULL_CONTROL明确长期授权未撤销。本轮用户明确授权六项工作台/选股交互优化；作者=发布操作者，非独立Review。最终仍需准确HEAD DegradedRelease、完整本地门禁、required CI、release_check、match-head合并与post-merge核验。
- **范围**：指数默认收起；影子启用用途与运行条件；未来首次入选事实；今日盘面四卡；全域原地查看消费者；加载/缓存/关闭/焦点/滚动体验。评分、模型、门槛、资金、撮合与风控不因UI接线改变，真实券商继续禁止。
- **运行**：3000已加载本轮前端production build `-OJIz0_LF5kBwlCOnqZe2`；8000仍运行IMP-087旧后端，本轮后端代码尚未加载，不能把新前端或隔离夹具当后端运行通过。只读preflight记录每日影子配置已启用、机会影子配置关闭，本轮未修改开关。隔离UI样本明确标非真实行情，不写生产、不启用影子、不强制生成、不调用模型或外发取得验收。
- **恢复**：现役ui-current-saved-20261008及忽略恢复包保留；本项无需数据库schema迁移。旧复盘迁移备份和后续数据不能盲目覆盖。

## 2. U49 主动审计回执 / 作者Preflight与反证

开工先核原owner、当前SHA、账本和真实消费者。首次加入实际记录钟与源报价钟分开，原价不冒充成交；旧未知不倒填。最高连板由完整涨停池身份给出，并列先选；题材/断层保存点击批次，不冒充全市场总数。原地查看日期/筛选隔离，主导航和实际任务切换保留；链接修饰点击保留。关闭停止读取，旧回包不覆盖新对象，嵌套详情不悄悄切被挡住的背景。账户旁览不初始化/结算，配置启用不等于运行或成交，未成熟结果不报胜率。

原版Impeccable按Operate/clarify/animate/optimize职责，interaction-design按联动/状态，ui-state-verify按实际组件与小屏验收。TypeSafe已评估，本项确定性时间/身份/风控不交语义模型。不是整版重设计，不宣称全部历史设计技能完整执行。多代理关注面核验仍是作者自检，最终DegradedRelease明确身份。

## 3. 本轮证据与未验证范围

- 源码锁定批次的完整后端4918 passed、83 skipped、1 warning，401.49s；警告为Starlette TestClient使用httpx的弃用提示。前端109文件973项默认/UTC分别通过，191.53s/164.87s；定向244/122/18及客户端账户4项为先前局部证据，不与全量相加。
- 同批tsc、eslint、Next生产构建、pyflakes、doc-health、workspace-hygiene及公开仓库扫描已通过；构建ID为`-OJIz0_LF5kBwlCOnqZe2`。构建生成的next-env源码变更已restore，不提交；本次文档事实回填后还须再核文档/卫生/公开扫描。证据位于`artifacts/runs/contextual-workspace-20261009`，不以旧绿色代替本轮门禁。
- 首次加入selection-entry-v1；盘中PIT证据版本pit-evidence-v4.selection-entry，原策略版本未变。记录首次实际容量内rank归档时间，非更早源钟。
- Jev主体baseline/final均返回HTTP451/isError=true，无有效评分或previousEvaluation。保留工具缺口，确定性门禁不降低。
- 浏览器已验1280×800/600、320×640与390×844隔离合成输入：首入选依据、原涨停池、完整最高身份/Canvas、同批题材/断层、账户四scope、指数默认/展开收起、通知保留与嵌套Esc/回焦；页面根无X/Y溢出，表格横滚留在内部。几何/截图/限制见browser-acceptance.json；不冒充39源码族全部实屏或真实手机。CI/发布与新后端实际运行仍待取得，未预写通过。源数据时效、全系统SLO与策略效果仍归原owner及IMP-019，本项工程不证明上涨概率或成交收益。

## 4. 传播与接续

实施方案、产品闭环、猎场设计、细功能审计§42、数据源7.2及plan-registry已传播；无新文档和第二状态源。INDEX/AGENTS/协作Skills/Jev蓝图不适用变更，原权威不变。连续完成同一IMP-088任务的内部切片与发布；不自动领取第二业务任务。开放世界长期候选准入继续按[持续演进](ai/continuous-evolution.md)，不以本轮工程结果晋级策略。

- **加载证据**：同一路由首文档直接script引用原始字节合计1,377,532→1,047,658（约减少23.9%）；本机HTML读取44.65→53.17ms不构成加载更快证据。惰性旁览与独立资源解除候选被辅助请求阻塞，未承诺全系统SLO。见loading-before/after.json。
