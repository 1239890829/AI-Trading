# 当前交接：IMP-050 全站任务视图与维护分离

> 唯一任务状态在[W07/IMP-050](stages/w07-simplification.md#imp-050)；产品实现与B/C对照见[产品闭环§11](product/product-closure-design.md#11-imp-050-全站视图落地与设计对照2026-09-30)，子功能/后台承接见[细功能审计§18](product/feature-closure-audit.md#18-imp-050-ui工程逐项验收与继承边界2026-09-30)。

权威入口：[实施方案](implementation-plan.md)v9.13、[产品闭环](product/product-closure-design.md)、[细功能覆盖](product/feature-closure-audit.md)、[Jev蓝图](ai/jev-integration.md)与[持续演进](ai/continuous-evolution.md)。累计U01–U55。

## 1. 现场、模式与门序

2026-09-30用户“继续”，干净master@aada09d0af5db8f04690dbcb607139d22a2a4e5f，IMP-049/PR #195已合并且master CI success；runtime selector领取唯一G2/IMP-050（非阻断/P1/门内序60）。分支codex/imp050-task-workspace，工程提交7299fb4；协调记录另提交。精确最终HEAD/PR、三项required CI、release_check、合并及清理事实以本任务DegradedRelease/发布回执为准，本记录不抢先宣布发布成功。

**U50 降级授权回执**：用户2026-09-24的本机Codex DEGRADED_FULL_CONTROL继续有效，直到明确撤销。作者U49反证与准确HEAD DegradedRelease，不声称独立Review；完整本地门禁、required CI、release_check、post-merge CI、分支清理不降低。部署仍搁置；无生产库写入、真实券商、真实通知、付费或模型/阈值准入。

## 2. 实施与本地验证

- B五任务：市场全景、机会发现、自选跟踪、持仓与模拟、复盘研究；维护独立次入口，旧路由兼容。H02发现/依据与等待/跟踪、follow/pin/compare同版侧栏；H03/H04四scope、main完整委托、daily未启用与hunting未准入分态；手工记录不称券商核验。H05复盘只读，处置在维护，不以采纳率宣称收益。
- 五项运营命令迁原agent维护，仍走原后台owner/开关/鉴权/预算/幂等/持久恢复，页面读取不触发生成。API权限不因area改变，普通用户自选/手工记账/模拟命令保留。原安全重置不回普通表单，参考价不冒充fill。
- 异步报告/日期/板块/资金身份与局部失败、URL中文/参数/返回、IME旧结果与加自选回执、知识小屏长文、Overlay顶层Esc/inert/圈定/恢复（含SVG）、助手键盘/焦点、分屏键盘与手机自然滚动分别修补。真实Canvas继续用原坐标和数据身份。

**完整本地门禁**：干净检出7299fb4，后端PYTHONPATH清空、隔离库/网络/文件，4564 passed/81 skipped（134.64s），pyflakes通过；前端83文件761测试默认/UTC通过（46.50s/47.93s）、tsc/eslint通过；Next16.3.3构建通过（编译546ms/类型5.0s），dev停止后构建。第一次构建仅因临时ui-acceptance删除后旧dev类型引用而失败，清理本任务开发缓存再构建通过；不改断言或门禁。早期定向失败和修复日志保留，不算完整绿色；最终相关新增回归纳入761测试。

**真实浏览器**：本机隔离合成夹具，明确非行情。正常/合法空/错误/固定版本、四scope、八位日期、只读复盘身份、通知/证券SVG详情焦点、助手键盘、桌面亮暗/手机390px长文及Canvas；原型A/B/C各12任务准确落点，24/24/27步，仅作者脚本不是人类盲测。正文宽358px，无横溢；原Canvas测与实屏分开。两批1899请求，173次原只读risk/check-order（夹具403），其余GET，无生成/成交/通知/chat POST。原生window.confirm让IAB工具超时，实屏取消回执未取得，单测取消/单飞/错误路径通过；工具限制不当业务成功。相关确认框请求人工点取消，所有隔离服务已停止。

**性能与未验**：开发同夹具150帧P50 A/B均16.7ms，P95 18.3/18.5ms、CLS 0.04367/0.01252；heap 24.96/39.20MB不可作生产内存收益结论，CPU/长期内存/真人认知未验证，不宣称全局提速。当前系统reduced-motion=false，CSS/JS减弱分支保留，工具无媒体仿真，未称实屏已验。后置动效IMP-054及生产运行验收另有原owner。

**Jev辅助**：baseline与最终复评共两次，previousEvaluation原样传递；正确性7.1→7.5、可靠性7.3→7.9，全部comparison判unchanged、无显著改进/退化。新增覆盖使test/security等维度可评但置信有限；rubric提示非根因，不为追分扩建。代码行为和门禁是主证据；模型版本/tokens/费用未知，研发评分不填股票Jev贡献或独立Review。

**U49 主动审计回执 / 作者 Preflight 与反证**：核权限/双事实源/scope/先后部分失败/GET写者/未知与去重/动态日期与URL/已退出能力/金融提示与伪实体/焦点/文档指针/测试是否固化坏行为。实屏发现SVG回焦与手工核验标题已修正并复验；所有新UI消费者有用途与原owner，不按导航命名授予后台权限。

## 3. 传播、恢复与交付边界

实施方案、产品闭环、猎场专题、细功能50项/18域/16组件族及新两组件、INDEX、plan-registry、W07和本页已更新。AGENTS/Skills/Jev模型/预算/撮合/协作机制不适用新规则：沿已批范围和原接口，无新权限、库或另一登记册。73份历史源码映射不是73逐行重审，30后台声明不是30生产健康任务。X20/X49/X50仍WATCH，研究前向效果、human gold、hunting-shadow真成交仍原任务，本轮不伪销账。

临时8007/3007/3008停止，原型/观测器和Next生成文件恢复性移出，旧dev缓存清理；本任务临时验证树/pytest沙箱在证据缩成日志/XML后清理，基线依赖副本因依赖环境保留保护进入恢复资产。截图/辅助结果/合成请求日志只保留忽略artifacts，不提交私人数据。workspace hygiene、doc-health、public scan按准确树复核通过；协调文档守卫初次2个回执标题缺失已补，最终201 passed（4.52s），发布结果另回填PR回执。生产未重启，合并不等生产加载。

本轮连续完成IMP-050内部切片并按授权发布，不自动领取第二主任务。下一次用户继续重算运行条件与门序；当前任务的长期用户反馈属于已有产品/运行复盘，不新造待条件自锁。

IMP-054的既有依赖与静态基线条件已由本次工程满足，仅将原状态回填待执行，不改范围/优先级/门序，不在本轮开工。下一用户继续再运行selector和换门U49反证。

- **当前主门**：G4
- **主切片首选**：IMP-054
- **当前门候选顺位**：IMP-054
