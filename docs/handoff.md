# 当前交接：榜单密度整改追加降级提醒与退潮展示验收

> 总方案v9.13；U01–U55；任务状态仅W07/IMP-084；现役方案[原版技能与UI方案](product/ui-original-skills-plan-20261008.md) §9.9；Jev蓝图[jev-integration](ai/jev-integration.md)。外部候选按[持续演进](ai/continuous-evolution.md)核验，不自动准入。

## 1. 现场、授权与版本

- **当前主门**：G2
- **本轮主任务**：IMP-084
- **主切片首选**：IMP-084

**U50 降级授权回执**：2026-09-24 Codex DEGRADED_FULL_CONTROL授权未撤销；作者=发布操作者，非独立Review。准确HEAD DegradedRelease与完整发布门继续执行。

- **当前主门 / 唯一主任务**：G2 / IMP-084。用户2026-10-09直接要求排查题材梯队、人气榜、飙升榜及各页面/组件内部遮挡，短字段不无故换行，必要横滚及时可发现；收起指数保留上证、科创50和创业板，开关完整；成交额完整显示，不保留微型滚动条。发布前又明确追加AI判读不可用/助手反复提醒与市场退潮容器完整展示，继续归同一任务，依赖IMP-083已满足，不重复旧任务。
- **保留项**：工作台 / 选股 / 市场 / 复盘四主菜单、现B+C配色/字体/材质、顶部导航、紧凑控件、原业务字段和动作。研究、参数与维护继续经工具入口进入；原URL/key、证券/日期/账户scope、来源/时间/质量及返回来源保留。
- **版本**：从已发布主干`dc43c1dbafa89806693113e765206f85db7f78c4`开始；本轮分支`codex/ranking-density-layout`。此前[PR #242](https://github.com/1239890829/AI-Trading/pull/242)准确HEAD `4d50183b0c667bc0cbd75ef2dd0f52ae7b17f92d`已match-head合并；post-merge运行37868845814三job completed/success，原功能分支已清理，见[PR最终发布回执](https://github.com/1239890829/AI-Trading/pull/242#issuecomment-6072294482)及上一批publication-receipt.json。旧handoff“待PR发布”已过期，本轮予以更正。
- **施工边界**：原前端宽高分配、列宽、换行、响应式、折叠与滚动入口继续实施；追加判读降级原因、提醒显示去重/确认/读取失败和退潮呈现。用户进一步明确告警AI判读“可以无限制使用，但是一定要筛选过后的，不要盲目使用”：仅告警判读独立alert_triage_llm scope，daily=None不限日次数、0停用、正数有限；其他autonomy_llm每日8次保持。持久receipt、输入/输出、timeout、并发与unknown usage硬门继续执行，不切模型，不改分类准入、交易风控、账户/模拟撮合、鉴权或外部通知；不回填历史具体原因，也不缩字/裁切业务内容。

用户随后明确临板距离为“距实际涨停价还需上涨多少%”：按同一报价中源提供的实际涨停价与当前价做纯算术(实际涨停价/当前价−1)×100，保留price/source/as_of，不按代码段、昨收或涨幅猜限价。仅身份匹配、quality=high、具名非mock来源、带时区源时间为北京当日且非未来/年龄≤120秒时可展示；缺限价、身份、时间或质量不满足即unknown，界面为待核对。先按原规则筛出本轮真正提醒候选，复用hub/cache，再按30秒≤50个miss有界补取，2秒结束等待但单一后台任务真实drain前不排队。原runway_pct仍是判定线百分点差，原筛选/评分/排序不变；存量“距封板Xpct”仅前端标旧口径百分点，不回写历史或假换算。

入选快照与距离基准报价分别展示并追溯：入选快照价10.72、后续距离报价10.80、实际涨停价11.00时，距离为1.85%，不把10.72误写为该报价现价，也不混算成2.61%。提醒保留快照涨幅/版本时点及距离报价现价/限价/来源/源时间；原entry_price、trigger_value仍为入选快照10.72，不被补取报价改写。缺报价只显示待核对，不借入选价填补。

## 2. U49 主动审计回执 / 作者Preflight与有界反证

开工前已核master基线、分支和原消费者，IMP-084未被占用，门内序108。源码中指数折叠与整组显隐关联；榜单沿横向排列并依赖overflow-x-auto；工作台手机成交摘要限高且overflow:auto。结合用户实屏反馈，需复现父子裁切、短字段最小宽度、榜单末项/表格末列和开关受挤问题。上述为待核原因，不能直接写成修复或实屏通过。

| 范围 | 本轮动作与反证 | 证据责任与成功判据 |
|---|---|---|
| 题材梯队 / 人气榜 / 飙升榜 | 调整面板比例、列宽与排列，短板数/角色/数值不挤换行；所有证券和换手后的字段/操作保留。 | 市场/题材切片提供桌面与320/390px截图、全项/末列实际到达、字段与动作覆盖。必要横滚由表自身承担，hover/focus入口与触屏能力分别验证。 |
| 工作台指数 / 披露开关 | 收起仍显示上证/科创50/创业板，展开恢复完整集；完整保留身份、源时间和质量；开关不被裁切。 | 工作台切片提供收起→展开→再收起及窄屏证据，含有效空/失败/陈旧状态，不为缺数据生成替代数值。 |
| 市场及工作台成交额 | 完整读数/单位/状态有适当空间，不使用微型滚动面。 | 对应切片检查桌面、手机和短屏，无小型滚动条；长数字不会挤掉单位或旁侧动作。 |
| 追加：判读降级 / 助手提醒 | 后端保留具体原因，告警判读使用独立daily=None/0/正数scope；前端按记录去重显示，确认失败可见，读取失败不伪装为清空。 | 主切片负责alert_triage预筛/关系/事件变化，预算切片负责scope与receipt硬门，提醒切片负责显示/确认/恢复；覆盖过期/重复/已知故障、ignore不延长静默、0/有限/None、其他自治隔离、ack=false及30秒重放。 |
| 追加：市场退潮 | 退潮正文改auto行高并去除内部scroll，长依据复用ModalShell；以真实可用空间完整展示。 | 市场切片负责桌面/手机/短屏、长依据/有效空/错陈旧和焦点/Esc实屏；不得以隐藏文字或外壳竖滚替代。 |
| 全页同类组件 | 按页面→共享组件→字段核短标签、数值、长名称/说明、列头、tooltip/菜单和操作，记录FIX/KEEP及理由。 | 有界同类审计逐消费者列证据，保持外壳与叶子scroll owner；末行/末列、焦点和恢复可达，不凭overflow:hidden消除问题。 |

同类反证包括min-width/min-height:0、flex shrink、Grid最小内容列、表格固定列、粘性头部、父子overflow冲突、数据迟到/空错陈旧、快速切视角与折叠后图表resize。短字段与长文按语义分别处理，不全局套nowrap；触屏不依赖hover。无新语义消费者，不接入新的Jev生产调用。

追加前只读核真实DB：当日triage.llm有8条成功调用，拆分前共享autonomy_llm每日8次硬预算已用尽；其后49条llm_fallback notify为旧记录，不由当前改动回填新具体原因。追加前只读观察模型探针ok、last_ok10:42；该探针状态与告警预算耗尽分别记录，不写网关持续故障。Jev shadow HTTP 451不改变baseline。前端每30秒重放未ack记录、查看不ack、ack=false被忽略以及读取失败清空需独立修复。新授权要求先确定性预筛过期、完全重复和已知系统故障，补齐持仓/自选关系、事件种类及变化再调用；规则ignore不得延长静默窗口。新增效果未验收前不写已通过。

## 3. 技能职责与实施顺序

Impeccable原版Operate的layout/adapt和craft-floor负责可读性、溢出与控件细节，沿holistic→diagnose→fix→confirmation；Taste原版redesign-existing-projects独立按Scan→Diagnose→Fix检查比例、密度和组件关系，不将dashboard套入营销主技能。UI/UX Pro Max提供表格/nowrap/响应式建议；Interaction Design核披露、焦点和滚动反馈；ui-state-verify负责状态、实际浏览器与Canvas验证。退役融合入口不恢复，技能各自的判断与结果保留。

顺序：①复现并冻结字段/动作/scroll owner与尺寸基线；②工作台固定指数/开关/摘要和市场题材榜单/末列/成交额；③按同类消费者排查修补其他表面；④追加判读预筛/关系/变化与独立告警日次数scope、提醒读状态和退潮展示，核其他自治8次、持久usage硬门、模型/风控/外部通知保持；⑤桌面与320/390px、短屏的实际操作及状态反证；⑥原版复查、适用完整门禁和Jev复评；⑦准确HEAD DegradedRelease、required CI、release_check和match-head合并；⑧post-merge CI、运行绑定与分支清理。作者反证与分数不冒充独立Review。

## 4. 本轮证据与验收进度

初始布局切片与追加实现均已完成代码和适用工程门禁，本地恢复提交8b079419尚未发布。以下初始全量与原实屏保留为历史基线；追加后最终全量、最终构建和已取得的实屏另列。桌面短屏追加CSS后的工程门禁与最终实屏均已完成，准确HEAD发布尚未收口；不预写PR/CI成功。

- **后端完整门禁**：本轮4654 passed / 83 skipped / 1 warning，1020.45s；pyflakes exit0。前端TypeScript/ESLint exit0。早期指数9 passed（3s）、Panel/evolution/review28 passed（16.32s）、根协调theme/index/limitup/longhu共4文件27 passed（42.38s）为局部检查，可能重叠，不相加为全量覆盖。
- **前端失败与重验**：工作台定向首次7项中6 passed、1项5000ms timeout（整批37.46s），并非断言失败；停止dev且后端结束后的低负载串行重跑7 passed（11.95s），未改阈值。全量首轮99文件860测试中858 passed、2 failed：真实QualityBadge被`q &&`门控，已改`q?.quality`直接渲染；markdown corpus耗时15193ms超过15000ms阈值。相关3文件27测试低负载重验全通过（10.22s），未改测试/阈值。最终默认全量99文件860 passed（217.52s）、UTC全量99文件860 passed（219.33s），分别见frontend-vitest-retry.log、frontend-vitest-utc.log；末次tsc/eslint exit0（frontend-tsc-final.log、frontend-eslint-final.log），生产build exit0（frontend-build-release.log），3000已正常start。workspace-hygiene、doc-health与public_repo_scan均已取得exit0（*-final.log）；本次日志/属性名文档修正后继续复核，准确HEAD发布仍待完成。
- **实现与作者反证**：排名列3ch/#1000、跨列EntryChecklist继承nowrap已修正；长工作台错误提示改渐进披露，避免挤压列表。手机指数展开由45%父区约束改自身88–160px滚动，收起保留三项；进一步修正mobile max-height cascade与账户grid高优先级冲突，clamp只施加data-collapsed="false"（展开态），账户mobile context改flex/首行auto。题材横滚面的Y overscroll改auto，允许父级继续到末行。
- **实源浏览器观察**：CUA实际核320×665工作台，收起三指数、完整披露按钮与完整成交摘要；展开后实际滚到科创/沪深/中证末行。市场手机读数改compact grid与双列内联六项统计后，已用正式生产在390×665和1280×600实源核完整读数、无读数内部滚动条，截图market-390-short-production.png、market-1280-short-production.png。390px验收时仅将原有悬浮助手气泡移至顶部，避免遮挡画面；未ack或改动通知。
- **合成夹具浏览器观察**：独立fixture显著标记合成，使用真实ThemesTab/ThemeCard/EntryChecklist/IndexCards与完整生产CSS，无后端token；修正壳层bc-tape-page后重新验收。320/390px榜单实际竖滚到第10项，#1000长名称可换行；1280×800九列全部可见，榜单末项与梯队末行可达；320px键盘Right到介入条件末列并展开长条件，桌面展开条件完整。390px缺科创fixture明确显示未返回；390×665账户fixture收起/展开均保留指数与两阅读区高度。截图归`artifacts/runs/ui-density-20261009/screenshots`，不把合成内容冒充实源或真实手机。
- **滚动入口实测**：Chrome兼容修正保留标准scrollbar-color:auto并恢复webkit 8px轨道；CUA在150%桌面缩放实际click/focus显示滚动条、drag到末列、外点失焦后隐藏，分别留图。纯mousemove hover没有独立操作，不写hover和真实触屏均已验证；CSS适用路径与实际动作证据分开。
- **代码审阅缺口**：首轮与最终市场修复、886项全量之后的Jev Review调用均返回HTTP 451 unavailable，未产生baseline或复评分数。本轮不写Jev通过、不沿用IMP-083旧分数、不冒充独立Review；按AGENTS §5披露工具缺口，常规完整门禁和CI继续执行。

- **追加实现已落地，发布与剩余实屏仍在进行**：告警判读独立`alert_triage_llm`默认不限日次数，仍持久预留UUID与reserved→started→finish回执；有限次数原子slot、同日旧null/legacy triage计数及原自主8次均保留。自主/告警任一started模型用量未知会阻止两个scope继续预留/启动，Jev telemetry未知不误作该阻断；旧日未启动模型预留以`reservation_expired`拒绝并由调用方释放，已启动/终态幂等保留。任务中心明确区分自主模型与告警次数，旧snapshot缺字段不补造0或不限。
- **追加判读与收尾**：规则预筛后，同一进程的`(session_factory,event_id)`共享singleflight结果，调用者取消不重启同事件判读；已有判读落库后复用。降级原因区分次数用尽、用量未知、跨日预留、未配置、通道/额度/超时/格式故障及输入输出/重试资源门，不回填旧记录。CLI按适配器实际120秒预留并记录120000ms，HTTP判读30秒；取消等待真实worker收尾后记终态。进化议程保留模型单次120秒与外层150秒deadline，取消/重复取消/timeout先drain再finish，晚到usage如实保留，超时结果不采用。
- **新增定向证据**：`test_agent_budget.py`与`test_evolution.py`合计69 passed（19.27s）；`test_agent_tasks.py`33 passed（4.19s），7个直接改动文件pyflakes与diff检查通过。覆盖无限11次持久回执、0/有限/跨日、未知用量/已预留启动/telemetry、真实阻塞线程取消与timeout收尾、历史预算缺字段；摘要旧码7项判红、议程启动阻断旧码误记failed已复现。以上是定向行为证据，不相加为全量或金融效果证明。
- **追加后最终完整门禁**：后端4767 passed / 83 skipped / 1 warning（333.66s），见`backend-frozen.log`、`backend-frozen.xml`；pyflakes exit0（`backend-pyflakes-frozen.log`），隔离测试进程local鉴权环境不改变生产配置。桌面短屏CSS追加后前端默认100文件886 passed（153.12s）、UTC100文件886 passed（156.75s），见`frontend-vitest-release.log`、`frontend-vitest-utc-release.log`；本批tsc/eslint与生产build exit0，见`frontend-tsc-release.log`、`frontend-eslint-release.log`、`frontend-build-release.log`。此前默认886（144.88s）/UTC886（166.41s，*-final.log）及885（153.57s，frontend-vitest-additions.log）分别保留为桌面CSS追加前、手机布局末次修复前基线。51个源码/测试/配置文件已与最终`final-source-manifest.json`及运行构建绑定；后端未在桌面CSS补丁后变化。320/390px市场分支已取得实屏，桌面短屏最终实屏和构建运行绑定已完成，准确HEAD CI/发布待完成；Jev Review 451缺口保留，不以工程门替代实屏或效果验收。

- **最终前端反例与修复**：390×665市场三行布局中的指数minmax(0,1fr)被成交/情绪auto读数挤至几px，手机改为四个相邻横向阅读面：指数、成交+情绪、涨停榜、事件；桌面保留原三行，metrics wrapper为display:contents。真实MarketPage+Modal测试先复现情绪获取失败后关闭、恢复却自动重开；loadSlow在sentRes为null时清basisOpen，恢复后须用户重新点击。该61行行为测试GREEN 1 passed，已纳入桌面短屏CSS追加前100文件886项全量与生产构建。最终生产390×665实屏已核六个指数完整、成交与情绪独立阅读面完整，并实际用键盘横向到达；截图为market-final-390-indices-production.jpg与market-final-390-metrics-production.jpg。320×665生产已核指数、成交/情绪完整及长依据完整，见market-final-320-indices-production.jpg、market-final-320-metrics-production.jpg、market-final-320-evidence-production.jpg。一张早期图保留指数详情旧接口报错，刷新回市场后错误数为0；不据此宣称指数详情接口已修复。1280×600桌面实屏另发现指数区仅剩标题，追加仅min-width:768px/max-height:760px生效的page/context/environment/breadth padding与gap紧凑规则；追加后默认/UTC各886及本批tsc/eslint/build已完成，桌面最终实屏与新构建运行已由根任务完成。不把源码、定向测试或键盘动作冒充触屏验证。

**有界反证残余**：现有指数详情对sz399006仍使用stock-only消费者，themes返回400，company/financial/news返回502；本轮观察已记录，市场刷新恢复不表示该详情残余已修复。本项未扩成第二任务、未编造新编号或owner，也未宣称全域全功能通过。

最近已完成的IMP-083仅作对照：后端4654 passed / 83 skipped / 1 warning、pyflakes exit0；前端默认/UTC各99文件857 passed、tsc/eslint/build exit0；PR #242准确HEAD发布与post-merge三job通过。不能把该历史绿色用于本轮新范围。

验收区分页面根、工作区外壳、分栏、叶子榜单/表/阅读面与短读数卡。结构/AX、真实Canvas、实际滚动、实源与测试夹具分别报告；只截图不能证明末行/末列可达，CSS声明不能证明触屏/键盘通过。本轮上述320/390px为桌面Chrome窄窗，不是真实手机；客户端精确几何、软键盘/读屏、触摸drag与全设备性能仍未完整验证，不能以夹具或桌面键盘操作推定。

## 5. 运行、恢复与传播核对

追加后后端已正常重启；只读health/budget/providers均HTTP200，triage_unlimited=true、triage_budget=null、triage_exhausted=false，自主模型8/8保持。后台自身health probe在12:10:40+08:00为ok、model=deepseek-v4-flash；根任务仅读取状态，未强制判读或ack，见runtime-backend-updated.json。前端进程83220的Next production start、3000市场页面及代理/后端health只读HTTP200是桌面短屏CSS追加前运行证据；390/320px市场实屏当时已加载。追加后仍需新生产build、服务启动与构建ID/源码哈希/准确HEAD运行绑定，由根任务发布回执补齐；不将运行成功写为发布或金融效果通过。token沿用既有backend/.env，只注入服务进程内存，不输出凭据、不写前端或文档。

既有恢复点保持：tag `ui-current-saved-20261008`→`0377b8414be70710bd4fff67818974601d5910da`；`artifacts/recovery/ui-current-20261008/source.tar.gz` SHA-256=`f1e89ee0244b128b884b9ab7ed6f2aaecb2450ab917ae8df87bcd62ffed93`。本轮未要求替换备份，不新增/删除恢复资产；恢复只涉及呈现源码，不覆盖业务数据或复活退出技能，旧版玻璃过透问题随说明保留。

传播核对：本handoff、W07/IMP-084、原版UI方案§9.9、implementation-plan §4/§7与ai/jev-integration §5.2/§25.2已同步独立告警日次数授权。plan-registry §1.1与INDEX只读核过，现有authority分工及AG-10/MD-12/FN-10分别指向总方案、原版UI方案和Jev专题，没有新文档、路径、领域或入口变化，不另堆指针。产品能力/导航/数据源/AGENTS/协作Skills不适用，因为不新增页面、模型/风控/权限、外部通知或协作规则。追加前卫生/doc-health/公开扫描exit0仅作基线；追加后最终工程门禁与已披露390px实屏/服务运行分别取得，协调文档门禁及准确HEAD发布链仍待收口。不预写成功、不另领业务任务，部署仍搁置；Jev 451与设备限制保留。

**发布前最终实屏与运行**：最终短屏追加后生产复验已完成：1280×600首对指数价格/涨跌幅/成交額完整，Tab可实际滚到沪深300/中证1000末项；成交额、六统计、情绪/十日历史与长依据弹窗均完整，Esc关闭回焦。证据为market-release-1280x600-production.jpg、market-release-1280x600-indices-last-production.jpg、market-release-1280x600-evidence-production.jpg；市场320/390分支不受该min-width:768px限定补丁影响，保留上述手机实屏。临时device override与DevTools均关闭，专用验收窗口已关闭。最终前端production start进程91072、后端75041，构建ID R7POJW0iM7jbhje4Z_mCY；51项源哈希无漂移，market/代理health/后端health及LAN工作台只读HTTP200，见runtime-frontend-release.json。准确HEAD发布尚未执行，不把运行验收当CI或发布成功。
