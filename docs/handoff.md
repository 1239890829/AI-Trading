# 当前交接：榜单密度整改本地验收完成，待准确HEAD发布

> 总方案v9.13；U01–U55；任务状态仅W07/IMP-084；现役方案[原版技能与UI方案](product/ui-original-skills-plan-20261008.md) §9.9；Jev蓝图[jev-integration](ai/jev-integration.md)。外部候选按[持续演进](ai/continuous-evolution.md)核验，不自动准入。

## 1. 现场、授权与版本

- **当前主门**：G2
- **本轮主任务**：IMP-084
- **主切片首选**：IMP-084

**U50 降级授权回执**：2026-09-24 Codex DEGRADED_FULL_CONTROL授权未撤销；作者=发布操作者，非独立Review。准确HEAD DegradedRelease与完整发布门继续执行。

- **当前主门 / 唯一主任务**：G2 / IMP-084。用户2026-10-09直接要求排查题材梯队、人气榜、飙升榜及各页面/组件内部遮挡，短字段不无故换行，必要横滚及时可发现；收起指数保留上证、科创50和创业板，开关完整；成交额完整显示，不保留微型滚动条。本轮为现有前端可读性与比例整改，依赖IMP-083已满足，不重复旧任务。
- **保留项**：工作台 / 选股 / 市场 / 复盘四主菜单、现B+C配色/字体/材质、顶部导航、紧凑控件、原业务字段和动作。研究、参数与维护继续经工具入口进入；原URL/key、证券/日期/账户scope、来源/时间/质量及返回来源保留。
- **版本**：从已发布主干`dc43c1dbafa89806693113e765206f85db7f78c4`开始；本轮分支`codex/ranking-density-layout`。此前[PR #242](https://github.com/1239890829/AI-Trading/pull/242)准确HEAD `4d50183b0c667bc0cbd75ef2dd0f52ae7b17f92d`已match-head合并；post-merge运行37868845814三job completed/success，原功能分支已清理，见[PR最终发布回执](https://github.com/1239890829/AI-Trading/pull/242#issuecomment-6072294482)及上一批publication-receipt.json。旧handoff“待PR发布”已过期，本轮予以更正。
- **施工边界**：只改前端宽高分配、列宽、换行、响应式、折叠和滚动入口；不改后端规则、数据源、通知/去重/推送、策略、账户、模拟撮合、权限或模型。不以隐藏功能/字段、缩字或裁切内容换取无滚动条。

## 2. U49 主动审计回执 / 作者Preflight与有界反证

开工前已核master基线、分支和原消费者，IMP-084未被占用，门内序108。源码中指数折叠与整组显隐关联；榜单沿横向排列并依赖overflow-x-auto；工作台手机成交摘要限高且overflow:auto。结合用户实屏反馈，需复现父子裁切、短字段最小宽度、榜单末项/表格末列和开关受挤问题。上述为待核原因，不能直接写成修复或实屏通过。

| 范围 | 本轮动作与反证 | 证据责任与成功判据 |
|---|---|---|
| 题材梯队 / 人气榜 / 飙升榜 | 调整面板比例、列宽与排列，短板数/角色/数值不挤换行；所有证券和换手后的字段/操作保留。 | 市场/题材切片提供桌面与320/390px截图、全项/末列实际到达、字段与动作覆盖。必要横滚由表自身承担，hover/focus入口与触屏能力分别验证。 |
| 工作台指数 / 披露开关 | 收起仍显示上证/科创50/创业板，展开恢复完整集；完整保留身份、源时间和质量；开关不被裁切。 | 工作台切片提供收起→展开→再收起及窄屏证据，含有效空/失败/陈旧状态，不为缺数据生成替代数值。 |
| 市场及工作台成交额 | 完整读数/单位/状态有适当空间，不使用微型滚动面。 | 对应切片检查桌面、手机和短屏，无小型滚动条；长数字不会挤掉单位或旁侧动作。 |
| 全页同类组件 | 按页面→共享组件→字段核短标签、数值、长名称/说明、列头、tooltip/菜单和操作，记录FIX/KEEP及理由。 | 有界同类审计逐消费者列证据，保持外壳与叶子scroll owner；末行/末列、焦点和恢复可达，不凭overflow:hidden消除问题。 |

同类反证包括min-width/min-height:0、flex shrink、Grid最小内容列、表格固定列、粘性头部、父子overflow冲突、数据迟到/空错陈旧、快速切视角与折叠后图表resize。短字段与长文按语义分别处理，不全局套nowrap；触屏不依赖hover。无新语义消费者，不接入新的Jev生产调用。

## 3. 技能职责与实施顺序

Impeccable原版Operate的layout/adapt和craft-floor负责可读性、溢出与控件细节，沿holistic→diagnose→fix→confirmation；Taste原版redesign-existing-projects独立按Scan→Diagnose→Fix检查比例、密度和组件关系，不将dashboard套入营销主技能。UI/UX Pro Max提供表格/nowrap/响应式建议；Interaction Design核披露、焦点和滚动反馈；ui-state-verify负责状态、实际浏览器与Canvas验证。退役融合入口不恢复，技能各自的判断与结果保留。

顺序：①复现并冻结字段/动作/scroll owner与尺寸基线；②工作台固定指数/开关/摘要和市场题材榜单/末列/成交额；③按同类消费者排查修补其他表面；④桌面与320/390px、短屏的实际操作及状态反证；⑤原版复查、适用完整门禁和Jev复评；⑥准确HEAD DegradedRelease、required CI、release_check和match-head合并；⑦post-merge CI、运行绑定与分支清理。作者反证与分数不冒充独立Review。

## 4. 本轮证据与验收进度

本轮实施与已披露范围的本地验收已完成，准确HEAD发布尚未收口；源码风险、实屏观察、工程门与发布事实分别记录，不预写PR/CI成功。

- **后端完整门禁**：本轮4654 passed / 83 skipped / 1 warning，1020.45s；pyflakes exit0。前端TypeScript/ESLint exit0。早期指数9 passed（3s）、Panel/evolution/review28 passed（16.32s）、根协调theme/index/limitup/longhu共4文件27 passed（42.38s）为局部检查，可能重叠，不相加为全量覆盖。
- **前端失败与重验**：工作台定向首次7项中6 passed、1项5000ms timeout（整批37.46s），并非断言失败；停止dev且后端结束后的低负载串行重跑7 passed（11.95s），未改阈值。全量首轮99文件860测试中858 passed、2 failed：真实QualityBadge被`q &&`门控，已改`q?.quality`直接渲染；markdown corpus耗时15193ms超过15000ms阈值。相关3文件27测试低负载重验全通过（10.22s），未改测试/阈值。最终默认全量99文件860 passed（217.52s）、UTC全量99文件860 passed（219.33s），分别见frontend-vitest-retry.log、frontend-vitest-utc.log；末次tsc/eslint exit0（frontend-tsc-final.log、frontend-eslint-final.log），生产build exit0（frontend-build-release.log），3000已正常start。workspace-hygiene、doc-health与public_repo_scan均已取得exit0（*-final.log）；本次日志/属性名文档修正后继续复核，准确HEAD发布仍待完成。
- **实现与作者反证**：排名列3ch/#1000、跨列EntryChecklist继承nowrap已修正；长工作台错误提示改渐进披露，避免挤压列表。手机指数展开由45%父区约束改自身88–160px滚动，收起保留三项；进一步修正mobile max-height cascade与账户grid高优先级冲突，clamp只施加data-collapsed="false"（展开态），账户mobile context改flex/首行auto。题材横滚面的Y overscroll改auto，允许父级继续到末行。
- **实源浏览器观察**：CUA实际核320×665工作台，收起三指数、完整披露按钮与完整成交摘要；展开后实际滚到科创/沪深/中证末行。市场手机读数改compact grid与双列内联六项统计后，已用正式生产在390×665和1280×600实源核完整读数、无读数内部滚动条，截图market-390-short-production.png、market-1280-short-production.png。390px验收时仅将原有悬浮助手气泡移至顶部，避免遮挡画面；未ack或改动通知。
- **合成夹具浏览器观察**：独立fixture显著标记合成，使用真实ThemesTab/ThemeCard/EntryChecklist/IndexCards与完整生产CSS，无后端token；修正壳层bc-tape-page后重新验收。320/390px榜单实际竖滚到第10项，#1000长名称可换行；1280×800九列全部可见，榜单末项与梯队末行可达；320px键盘Right到介入条件末列并展开长条件，桌面展开条件完整。390px缺科创fixture明确显示未返回；390×665账户fixture收起/展开均保留指数与两阅读区高度。截图归`artifacts/runs/ui-density-20261009/screenshots`，不把合成内容冒充实源或真实手机。
- **滚动入口实测**：Chrome兼容修正保留标准scrollbar-color:auto并恢复webkit 8px轨道；CUA在150%桌面缩放实际click/focus显示滚动条、drag到末列、外点失焦后隐藏，分别留图。纯mousemove hover没有独立操作，不写hover和真实触屏均已验证；CSS适用路径与实际动作证据分开。
- **代码审阅缺口**：首轮Jev Review调用返回HTTP 451 unavailable，未产生baseline或复评分数。本轮不写Jev通过、不沿用IMP-083旧分数、不冒充独立Review；按AGENTS §5披露工具缺口，常规完整门禁和CI继续执行。

最近已完成的IMP-083仅作对照：后端4654 passed / 83 skipped / 1 warning、pyflakes exit0；前端默认/UTC各99文件857 passed、tsc/eslint/build exit0；PR #242准确HEAD发布与post-merge三job通过。不能把该历史绿色用于本轮新范围。

验收区分页面根、工作区外壳、分栏、叶子榜单/表/阅读面与短读数卡。结构/AX、真实Canvas、实际滚动、实源与测试夹具分别报告；只截图不能证明末行/末列可达，CSS声明不能证明触屏/键盘通过。本轮上述320/390px为桌面Chrome窄窗，不是真实手机；客户端精确几何、软键盘/读屏、触摸drag与全设备性能仍未完整验证，不能以夹具或桌面键盘操作推定。

## 5. 运行、恢复与传播核对

本轮最终build exit0后3000已恢复正式生产；8000保留原后端进程，未重复调度。local HTTP与health均200；发布时继续核实际源码/构建与准确HEAD绑定。token沿用既有backend/.env，只注入服务进程内存，不输出凭据、不写前端或文档。

既有恢复点保持：tag `ui-current-saved-20261008`→`0377b8414be70710bd4fff67818974601d5910da`；`artifacts/recovery/ui-current-20261008/source.tar.gz` SHA-256=`f1e89ee0244b128b884b9ab7ed6f2aaecb2450ab917ae8df87bcd62ffed93`。本轮未要求替换备份，不新增/删除恢复资产；恢复只涉及呈现源码，不覆盖业务数据或复活退出技能，旧版玻璃过透问题随说明保留。

传播核对：本handoff、W07/IMP-084与原版UI方案§9.9已更新；总方案/产品能力/细功能归属/INDEX/AGENTS/协作Skills/模型与数据源蓝图不适用，因为没有新页面、业务语义、工具链、权限、治理或新文档入口。实施与已披露范围验收完成，卫生/doc-health/公开扫描已取得exit0，下一步对末次修正精确暂存并复核，提交PR并完成准确HEAD DegradedRelease、required CI/release_check、match-head合并、post-merge CI、运行绑定与分支清理；不预写成功、不自动领取第二业务任务，部署仍搁置。Jev HTTP 451及设备/交互验证限制随发布记录保留。
