# 大小功能闭环审计与覆盖边界

**U52–U55 已批准（2026-09-26）**：v1.3旧新归属与本次基点2adaaee映射见§11–§14，产品定义只由product-closure-design拥有，任务状态只归stage。前文26a0c44/182路由/29调度是历史扫描，不是当前运行证明；IMP-048工程已经完成，不由旧疑点重开。

定位：按可观察功能审查用途、输入、动作、反馈和退出，不是新账本。上游：[产品闭环](product-closure-design.md)、[实施方案](../implementation-plan.md)；状态/排期唯一归 [总账 §6.0](../retro-and-gaps.md#60-阶段索引) 下GOV-020及对应任务。
基点26a0c44。本批成功的结构盘点含267个backend/app Python文件、2429个函数/类定义、182个路由装饰器、29处调度注册；前端跟踪源清单133个TS/TSX文件。数量是静态覆盖分母，不是已读懂或已验收数量；单个装饰器也不等于部署后唯一有效路由。
TypeScript AST交互清单生成调用被安全检查拒绝，未产生成功结果，不能据此声称全部按钮已穷举。下表来自实际已读组件及调用点；未读细分、动态注册/运行状态仍须逐片补足，不用这些表代替测试。

## 1. 粒度与记录要求

每个可观察行为分别审查：入口/触发→输入及身份→实际调用与副作用→数据/下游→成功/空/错误/取消反馈→持久化与恢复。按钮、筛选、排序、标签、计数、日期、弹窗、快捷键、后台任务及无UI服务同样纳入，不只写“市场模块已审”。
同一组件的不同动作可分条，纯装饰不强造选股用途；无风险的展示工具仍须有可用性/无障碍证据。业务判定在后台，CSS、排版、图坐标和纯交互保留前端，不机械迁移造成高频网络往返。
本表“源码已读”仅证明观察到该实现；行为、真实数据、运行版本、策略效果分别验。所有未通过项接既有任务，非每个小动作造新ID；同族修复后扫描同类消费者，不能漏旁路。

## 2. 工作台与个股详情

| 细功能 | 源码/上下游 | 检查与设计裁定 | 任务 |
|---|---|---|---|
| 搜索输入、防抖、IME、Enter/Escape | search-box → searchSymbols → detail | 已读；旧关键词结果Enter、中文组合、迟到回包需行为验 | IMP-050 |
| 搜索结果快速加自选 | quickAdd → addToWatchlist → watchlist-sync | 已读；失败目前只console.warn，用户需真实失败反馈，不误报成功 | IMP-050 |
| 自选增删/排序/切股/最近标的 | workbench、watchlist接口/同步 | 入口/部分调用已读；逐动作验持久/跨窗/重复/未知改动 | IMP-050、GOV-013 |
| K线/分时/资金图与右栏切换 | stock-detail及charts | 已读主装配；页签不串证券/日期，指数不发个股无效请求 | BUG-022 |
| 技术评估及依据展开 | technical-analysis → stock-detail | 源装配已读；业务分析应消费后端同版依据，非仅前端重算 | IMP-006 |
| 均价/成本/事件/成交点与图例 | chart props与event-markers | 装配已读；不同价格身份、新闻时间/复权、真实Canvas单验 | BUG-022 |
| 拖宽/收起/恢复、焦点/滚动 | stock-detail、localStorage | 已读；存储失败/键盘/小屏/切股清理和事件解绑需验 | IMP-050 |
| 加自选、题材chip、关联事件 | add、ThemeChips、StockEvents | 已读；静默失败、跨页统一对象、主题与多事件关系需验 | IMP-050、IMP-048 |
| 历史回放播放/暂停/退出/进度 | ReplayChart、持仓marks | 入口已读，回放内部非全验；不得泄露未来数据 | IMP-020、BUG-022 |
| 盘口/逐笔/涨速/板块排行 | BookTrades/Speed/BoardRank与REST | 装配已读；内部消费者需逐项核时间/单位/刷新和空态 | BUG-020、IMP-050 |
| 公司/财报/公告/新闻 | Profile/Info与digest | 装配已读；发布日期与财报期、修订、旧字段复用及来源错误需验 | BUG-020、IMP-048 |
| 模拟草稿价格、使用现价、方向/数量 | TradeForm → risk precheck | 源码已读；编辑价不被行情冲掉、预检签名、pending按钮语义分别验 | IMP-007 |
| 费用预估、提交/挂单/撤单 | TradeForm/TradePanel → paper | 已读表单；前端独立费率公式应移至后端同版结果，不放松执行硬门 | IMP-007 |
| 重置模拟账户 | handleResetAccount → resetPaperAccount | 源已读；破坏性调试动作后移，鉴权/恢复不能只靠confirm | IMP-007、IMP-050 |
| 真实成交记账、日期/费用输入 | RealPositionPanel → real_position | 源已读；UTC默认日期、输入合法性、切股草稿单验；只记账非真实下单 | BUG-020、GOV-013 |
| 手动修正/删除/清仓/盈亏汇总 | RealPositionPanel → trades/overrides | 源已读；修正可追、删除不抹证据，缺价格不当零盈亏 | GOV-013、IMP-050 |

## 3. 盘面、市场与事件

| 细功能 | 源码/上下游 | 检查与设计裁定 | 任务 |
|---|---|---|---|
| 题材日期/排序/连板与家数筛选 | ThemesTab → getThemes | 源已读；参数回显/URL/重复请求/失配旧响应分别验 | IMP-050、BUG-020 |
| 聚焦题材、显示全部、官方别名 | ThemesTab/ThemeCard → themes | 源已读；筛选与原主题/多关联保持，不把无梯队说无机会 | IMP-049、IMP-050 |
| 竞价标杆/人气/飙升/资金强度 | ThemesTab辅助数据 | 源已读；历史日期不能混进当前热榜和资金证据；降级可辨 | BUG-020、IMP-049 |
| 断板股、涨停/跌停切页与详情 | tape组件、pool接口 | 源已读；断板不等退潮终结，股票落点一致 | BUG-029、IMP-050 |
| 涨跌停日期、空池与成员高亮 | LimitUp/LimitDown | 源已读；空结果取首条日期会丢查询日期，需固定行为反例 | BUG-020、IMP-050 |
| 原因全文、封单/换手/炸板计数 | pool列表与提供方 | 源已读；缺失不变0，原文/推断及当时口径分开 | BUG-020、IMP-048 |
| 龙虎榜日榜/三日榜、日期及披露态 | LonghuTab → getLonghu | 源已读；不同区间不相加；数据商时间不是交易所普适规则 | BUG-020 |
| 题材资金迁徙 | LonghuTrail | 源已读；等分归属非真实拆单，历史查日与近N日不混 | IMP-006、IMP-050 |
| 市场指数/宽度/主题跳转 | market/page、IndexCards | 入口/部分读；须逐点核代码/基准/数量及去向 | BUG-020、BUG-022 |
| 成交额同刻比较/全天预估/历史日切换 | FundTab | 源已读；估算非终值、时区与异步结果绑定选择日期 | BUG-020、IMP-050 |
| 五档净额/资金历史/机构游资聚合 | FundTab | 源已读；缺席位不当0，个股/板块/日榜语义不可混 | BUG-020、IMP-006 |
| 板块种类/时间窗/排序/多条件过滤 | BoardFlow | 源已读；切换请求身份/失败状态/仅今日字段适用性待验 | IMP-050、IMP-019 |
| 下钻分钟/日度/成员排行与关闭 | BoardFlowDrawer | 源已读；不同board需重置/请求键，关闭停止轮询，迟到不串板块 | IMP-050、IMP-019 |
| 云图全市场/自选/行业下钻/返回 | HeatmapTab → getHeatmap/watchlist | 源已读；focusGroup旧对象与自选只加载一次的刷新一致性需验 | IMP-050 |
| 云图hover/点击/聚合格/面积/图例 | HeatmapCell及layout | 源已读；缺数颜色、键盘/小格替代、源时间和本机取数时间分开 | BUG-020、IMP-050 |
| 事件相关/时间/影响排序，类别/标签/L1 | EventsTab → impact | 源已读；切排序旧回包、筛选分母、重复事实去重需行为验 | IMP-048、IMP-050 |
| 新闻原文、无URL详情、方向/股票链接 | EventsTab/NewsModal/event-view | 入口已读；外链安全、来源版本、未核事实标记及统一落点 | IMP-048、IMP-050 |
| 标的池展开、记忆龙头 | StockPools、memory_leaders | 使用处已读；经营关联/时间衰减，别名不算事实，嵌套源待验 | IMP-048、RSH-027 |

## 4. 猎场、通知、助手与复盘

| 细功能 | 源码/上下游 | 检查与设计裁定 | 任务 |
|---|---|---|---|
| 盘前/盘中名单、去重、空态、状态条件 | hunting/page全文、PickCard适配 | 已读主要契约；开放情境而非四类enum；盘中随新事实另版重评，开盘状态不冻结全天；不因整页正常掩盖局部失败 | IMP-049、IMP-050、IMP-053 |
| 卡片价格/进入区间/止损/退出/持仓标记 | PickCard、风险字段、position labels | 已读契约；reference/actionable/fill 价格身份必须分开，后端同版决定、缺失/权限逐字段验；参考价不冒充模拟成交 | IMP-006、IMP-053、IMP-007 |
| 筛选/主题深链/展开、盘后增强/潜伏 | hunting、PostMarketEnhance | 入口已读；小功能不靠放在折叠区获得有效性 | BUG-028、IMP-050 |
| 生成/刷新组合、简报、单拍、对照/复盘 | hunting act与对应POST | 源已读；运营/重算动作后移，后台继续产出真实结果 | IMP-050、IMP-006 |
| 铃铛/未读数、单条已读/全部已读 | NotificationBell → read-state | 源已读；本地/服务端水位、跨时区、多窗口并发单验 | BUG-016 |
| 清除与恢复 | clearBefore持久化与tooltip | 源已读；“清storage恢复”文案不符服务端权威，不能删记录求一致 | BUG-016 |
| 时段切换、资讯/机会切换、分页 | NotificationDrawer/EventFeed | 源已读；浏览不计通知未读，分页与总数/筛选同分母 | BUG-016、IMP-050 |
| 通知行体/行情/判读、错误/空态 | NotificationRow/EmptyState | 源已读；用户影响说明保留，内部规则计数/诊断后移 | BUG-016、IMP-050 |
| 助手打开/发送/取消/重试/历史/链接 | floating-assistant与assistant API | 仅入口/结构定位；正文和运行行为待核，不宣称已审 | IMP-051、IMP-052 |
| 复盘选择日、生成/读取、评价与改进 | ReviewTab与review API/service | 入口/既有流程已核；结果读取留前台、运营执行后移 | BUG-009、RSH-026、IMP-050 |
| 进化/任务/参数/健康/仓库/KB浏览 | agent/page及各tab | 页面入口已读，内部并非全验；后台保留有效能力/鉴权，用户主导航退出调试 | IMP-052、IMP-050 |
| 公共 Modal/Drawer/Popover、键盘、焦点、返回/主题切换 | modal-shell、notification/board drawers、nav、routing、UI hooks | 导航与主要 overlay 已读；按上下文/阻断性/空间需求选择，不机械全改 Drawer；页面锁屏+内部滚动、焦点恢复、reduced-motion 与空态逐片核 | IMP-050、IMP-054、GOV-021 |

## 5. 无前台功能也逐项审

对182个路由装饰器清单逐一补有效挂载/权限/消费者，不以“有API”为闭环。29处调度注册逐项核开关、唯一拥有者、输入、输出、失败/重试/停止/恢复；风险、行情、事件、研究与成本控制都保留各自目的，不能因取消协作互调而删除业务调度。
后端细分按实际调用追踪：身份/日历/价格规则→Provider路由与主备→缓存/限流/退避→Hub/WS/快照→事件去重修订与抽取→环境/角色/候选/评分/门控→通知意图/发送回执→模拟/手工记账→标签/研究→KB引用与模型权限。每条输入输出都检查单位、时间、缺数、版本、消费者和失败终态。
隐性功能包括配置默认值、缓存失效、数据迁移、清理/备份/恢复、任务取消、历史导入、CLI、重启加载、日志脱敏、预算/限流及兼容路由；列入GOV-020审查分母。结构清单不是逐行审阅证据，未核项逐批登记，不以“工具类无需审”排除。

## 6. 已发现的问题怎样推进

本次两项函数级反例已单列BUG-028/029；其余小功能源码风险优先并入BUG-020、BUG-016、IMP-007、IMP-050等原任务。先写可失败的行为测试再确认影响，不把静态疑点数量当生产事故数。
尤其以下不能遗漏：快速加自选失败不可见；旧搜索结果Enter；空池失去查询日期；云图行业聚焦与自选列表不随新数据更新；板块资金/历史日切换旧回包；手工成交默认UTC日期；前端费率/技术评估独立业务逻辑；通知清除恢复文案与服务端状态不符。
这些分别影响操作反馈、身份/日期、同步和解释可信度，不只是“优化UI”。每项修复沿同类组件回扫并保留失败路径，不能只修猎场。

## 7. 完成定义与剩余边界

整体完成不得用一个大模块打勾代替细功能：所有当前入口/动作/后台注册均需去向；每个有影响的改动有正例、失败/空/取消、相邻消费者与恢复证据。未知资产和低频安全逻辑不因低点击删除。
本轮已读组件和库文字支持设计与问题定位，尚未逐一点击真实界面、调用全部API、验证后台29任务或重跑全部历史研究；这些仍是明确未验收范围，不宣称本次全仓终审完成。
证据在原项目artifacts/runs/product-loop-redesign-20260918-190452：backend-structure.json、lurk-prefix-probe.json、reopen-candidate-probe.json、retirement.json。页面与业务源码不在本批改造范围；Codex根据用户确认后的优先级分片实施。
文档版本变动时只对相关覆盖条目更新证据，不每轮重新复制整个清单；源码增加新入口/动作时必须补覆盖。构建/静态绿不替代真实渲染，示例/夹具不替代真实行情，不知道不等于无问题。

## 8. 非猎场补审的具体结论与验证路径

ReviewTab全文补读后，补入三条：切日期的detail没有请求身份绑定，快速切换可能挂错报告；失败返回null仍显示“详情加载中”；“已实施”按钮/采纳率是管理事实，不是净收益证明，应迁后台并绑定实现证据。LinkedSymbols把任意六位数字变证券链接，金额/日期片段需实体识别而非直接跳转。对应BUG-009、IMP-050、BUG-020，先按真实组件做失败夹具再改。
useResource全文确认：仅其返回快照有key隔离；usePollingFetch的调用方自行setState不受此保护。不能因公共hook已修就宣布板块抽屉、搜索、资金历史日和复盘页均无竞态；各消费方须以实际参数、回包身份、卸载及取消分别验。网络取消是否引入按成本与风险决定，不为统一重写全站。
assistant-sessions全文确认：它是本地有限历史缓存，多标签页后写覆盖、配额不足裁剪旧会话和写失败无用户回执是既有取舍，不是已完成跨设备可靠存储。系统决策/任务事实必须在后台；私人聊天是否长期入后台需要明确用途/隐私/保留期，不能借后台化默默上传。保留interrupted状态，不把恢复文本当未完成任务可自动续跑。
新增源码疑点只证明可能路径，不冒称生产事故。最小复現、同类消费者扫描、行为/权限/负例、实源与运行版本逐层补证；实际未读函数和未验证交互继续留在覆盖分母，不因本表更新而销账。

## 9. 后台29处调度注册逐项归属

下表是结构盘点到具体用途/消费者/验收的映射，不代表29项已运行验证。原始注册行和调用保存在backend-structure.json；实现者逐条读实际函数、开关和调度条件，外发/计费试验先核授权。

| 注册键 | 用途与下游 | 必验重点/任务 |
|---|---|---|
| alert-triage | 告警解释与通知判读 | 失败不假终态、风险不被降噪吞；IMP-044/046 |
| evolution-agenda | 证据整理和改进建议 | 不改代码/私自晋级；IMP-052 |
| review-scheduler | 盘后报告与反馈 | 交易日/版本/生成幂等；BUG-009 |
| quote-poller | 主行情与Hub | 真实源时间/单拥有者/重连；BUG-020 |
| market-snapshot | 固定市场快照 | 原子写、重复/缺数；GOV-013 |
| picks-autogen | 盘前名单 | 后台拥有生成，页面不重复触发；IMP-006 |
| data-health-sentinel | 数据可用性与质量 | 失败和合法空集分开；BUG-020 |
| pre-limit-radar | 临板观察 | 资格、再开板/迟到与时点；BUG-029 |
| llm-aux-judge | 事件辅助解释 | 输入来源/预算/失败降级；RSH-027/IMP-052 |
| position-monitor | 持仓风险跟踪 | 持仓/观察分开、旧价不伪执行；IMP-007 |
| paper-matcher | 模拟撮合 | T+1/权限/涨限/费用/幂等；手工 paper、daily-picks shadow、hunting-shadow scope 分离；IMP-053/007 |
| alert-quotes-feeder | 告警行情输入 | 单位/身份、无效价拒绝；IMP-044 |
| alert-engine | 条件告警 | 重复/撤回/过期、配置后台化；IMP-044/050 |
| risk-refresher | 风险状态与约束 | 陈旧/缺状态失败关闭；IMP-007 |
| event-collector | 事实/公告收集 | 去重/修订/发布时间与接收时间；IMP-048 |
| metric-history-backfill | 指标历史与校准 | 来源/可见时间，回补不污染先验；BUG-020 |
| premarket-brief | 盘前假设与观察 | 日期与版本冻结，解释不等于批准；IMP-006 |
| picks-watcher | 盘中观察与失效 | 同决定版本和持久回执；IMP-006/044 |
| picks-buy-point | 条件进入判断 | 时机/失效/新鲜度，不保证成交；IMP-049 |
| board-surge | 板块异动到观察 | 规模/成分/资金口径，不自动当个股买点；IMP-049 |
| lhb-archive | 龙虎榜历史证据 | 日榜/多日榜、披露时点/源质量；BUG-020 |
| picks-intraday-review | 盘中结果标签 | 机会/轮次/周期/未成交分母；BUG-026/RSH-026 |
| ths-reason-sentinel | 涨停原因覆盖核查 | 覆盖不等正确，不重复计转引；IMP-048 |
| sentiment-monitor | 环境状态变化 | 热度/接力/集中分开，迟到不倒填；IMP-049 |
| picks-shadow | 当前每日精选影子执行 | 现实现主要是定稿后次日晨窗 shadow，不得冒充猎场盘中每次 actionable 已成交；研究效果归 IMP-020，猎场独立自动 shadow 归 IMP-053 |
| marketdb-sync | 历史行情入库 | 复权/日期/证券及一致恢复；BUG-020/GOV-013 |
| factor-eval | 因子证据评估 | 隔离重研究、失败预算/样本外；RSH-003/IMP-019 |
| news-flash | 快讯到事件 | 单条事实多源转引去重，无效来源不晋级；IMP-048 |
| llm-gateway-probe | 模型服务健康 | 探针不假装业务有效，不反复计费；IMP-052 |

## 10. 路由与数据面不遗漏的覆盖分母

26份带路由声明的源文件全部归入：market_quotes/stock/board/pools/longhu/themes/sentiment/flow、theme_catalog、news/events/ext_data、picks/picks_intraday、paper/real_position/risk/watchlist、notifications/alert、assistant/agent/review/backtest、health及websocket。182个装饰器要核实际挂载、参数、鉴权、读写副作用和消费者，不能把同文件某一路通过推及所有端点。
后台267个文件/2429个定义是结构快照，嵌套/私有函数不是2429个业务功能；以可观察动作/输出/保护作用划分验收项，辅助函数随真正消费者覆盖，不制造形式化任务数量。前端133份TS/TSX清单同理，测试配置/声明文件不算用户功能。
后续新增入口、动作、模型工具或后台注册必须补此覆盖映射和原任务验收；旧模型/缓存/迁移/认证等低频保护不能因未出现在主导航而被省略。全景覆盖负责不漏，具体反例负责验证深度，两者缺一不可。

## 11. 已批准50行子功能归属（U55）

X编号仅用于批准来源与设计覆盖，不是任务编号；历史证据在原稿，具体状态在stage。名称为B候选，C可以通过对照取代，不把本表当已部署菜单。

| 来源 | 子功能 | 旧入口 | 目标归属 | 处置 | 原责任 | 闭环/退出条件 |
|---|---|---|---|---|---|---|
| X01|指数/市场广度|市场/工作台|H01/共享摘要|MERGE呈现，KEEP事实|IMP-040/IMP-050|同源只读，不另算广度 |
| X02|市场总览|市场|H01概览|MOVE/重设计|IMP-050|环境不是第二机会榜 |
| X03|题材/梯队/成员|盘面/详情|H01题材|MOVE/规则KEEP|IMP-049/050；RSH-031|日期角色分开，跳转不授权 |
| X04|涨停/跌停/断板|盘面|H01涨跌停|MOVE|IMP-040/050|曾封板/当前状态不混；跌停按请求日验证，THS/东财的 ST/BJ 覆盖差异不能静默合并 |
| X05|龙虎榜/席位|盘面/详情|H01/共享详情|MOVE/待定位|IMP-040/050|日榜/多日榜/披露时点独立 |
| X06|资金/同刻成交额|市场/详情|H01/共享详情|MERGE重复读视图|IMP-040/050|估算/终值/单位/分母分名 |
| X07|云图/热力图|市场|H01子视图|KEEP/重设计|IMP-050|筛选/返回/键盘可达 |
| X08|快讯/公告/事件|市场/消息/详情|H01事件/共享投影|MERGE呈现|IMP-048成果；IMP-050|未推荐仍可读，转引不重复计证 |
| X09|原文修订/撤回/差异|事件弹层|H01/共享证据|EXTEND呈现|IMP-048成果；IMP-050|保旧版本，不倒填 |
| X10|宏观/隔夜/气候|简报/市场|H01上下文|MOVE/按需|IMP-040/050|低频不挤主屏，不证因果 |
| X11|盘前精选|猎场|H02|MOVE/保节奏|IMP-049/050|日期/参考不混盘中 |
| X12|盘中联动机会|猎场|H02|重设计|IMP-049/050|同版条件，空错陈旧分开 |
| X13|潜伏/接力/趋势/再启动|猎场/知识|H02情境|KEEP开放，准入后扩展|IMP-049；RSH-003/031|无分类上限，不由菜单准入 |
| X14|未归类/缺证线索|既有规划|H02线索|EXTEND候选|IMP-049|有事实，无假买点 |
| X15|等待/触发/失效|卡片/详情|H02/共享详情|EXTEND|IMP-049/050|新事实另版，不自动发单 |
| X16|为何未选/未提醒|分散记录|H02/消息|EXTEND只读|IMP-049；BUG-016/IMP-032|无记录写未知 |
| X17|参考轨/首见|猎场|H02，H05评价|KEEP/分名|IMP-049|观察不是fill，刷新不扩分母 |
| X18|加/取消自选|搜索/工作台/详情|H03/共享命令|KEEP/统一回执|IMP-050|明确用户操作，取消不平仓 |
| X19|自选排序/分组|工作台|H03|KEEP/按需EXTEND|IMP-050|并发恢复，不擅自重排 |
| X20|保存筛选/观察理由|部分既有/候选|H03|WATCH|IMP-050；持久字段另评|频繁复用任务成立才扩展 |
| X21|最近标的/连续看股|工作台/详情|H03/共享详情|KEEP/联动|IMP-050|选中不等收藏，不串时点 |
| X22|K线/分时/盘口/财报|多种详情|共享证券工作区|MERGE装配|IMP-050；BUG-022成果|真实Canvas，指数个股分离 |
| X23|手工记录/修订|个股详情|H04手工记录|MOVE|IMP-050；GOV-013|非券商验证，不覆盖旧事实 |
| X24|持仓/风险|工作台/详情|H04，H03摘要|MOVE/共享|IMP-007/050|不合本金/收益口径 |
| X25|手工模拟/预检/撤单|详情表单|H04/同命令快捷入口|KEEP/统一|IMP-007|后端费率风险，草稿隔离 |
| X26|每日精选shadow|后台/研究|H04独立scope|KEEP|IMP-020消费|保留次日晨窗语义 |
| X27|hunting-shadow|待条件方案|H04机会影子|EXTEND按原前置|IMP-053；IMP-049|真实fill/no_fill/reject/退出 |
| X28|订单/未成交原因|分散结果|H04|EXTEND证据|IMP-007/053|unknown先核，不补历史fill |
| X29|模拟重置/危险维护|普通界面|系统维护|BACKEND/MOVE|IMP-007/050|先鉴权备份恢复再迁出按钮 |
| X30|日度复盘/对照|Agent/猎场|H05，局部摘要|MERGE完整呈现|BUG-009/IMP-050|日期对象不串，合法空可辨 |
| X31|失败/漏选/弃权/未成交|分散统计|H05|EXTEND聚合|RSH-026/IMP-020；IMP-050|全分母/成熟度/scope分开 |
| X32|次日条件计划|复盘/简报|H05，H03投影|FIX/MOVE|BUG-009|一份计划版本，不双写 |
| X33|验证/否决方法成果|研究/策略/Agent|H05|KEEP/只读组织|RSH-003/031|否决不因换名复活 |
| X34|知识正文/搜索/反证|Agent/助手|H05/上下文|KEEP/验证正文|RSH-027；IMP-050|索引合法不等支持 |
| X35|批量实验/标注管理|Agent/脚本|受控研究操作|BACKEND/MOVE|RSH-030/003/031；IMP-052边界|先预算授权，human不由模型填 |
| X36|参数/晋级/回滚|Agent|系统维护|MOVE，安全KEEP|IMP-052成果；GOV-027|独立批准与证据绑定不变 |
| X37|议程/任务/取消|Agent|系统维护/就地影响|MOVE|IMP-025/052|提案不是部署，取消真终态 |
| X38|仓库/依赖/模型更新|Agent/治理|系统维护|MOVE/有限雷达|GOV-024/025/027|不自动安装升级跨门 |
| X39|消息/风险/已读清除|铃铛/通知|共享消息|KEEP/同事实投影|BUG-016/IMP-032|清除不删审计，故障不藏后台 |
| X40|助手/停止/引用/历史|浮动助手|共享上下文助手|KEEP/有据重设计|IMP-046/051/052|私聊草稿不默默上传 |
| X41|JEV参与/判断/采纳/结果|U51/v1.2|对象凭证/H05/维护|EXTEND实际记录|原owner；IMP-045/050|设计共审不是股票贡献 |
| X42|搜索/别名/旧深链|导航/路由|共享搜索导航|EXTEND兼容|IMP-050|名称变，身份权限不变 |
| X43|主题/密度/布局/焦点|前端偏好|共享用户偏好|KEEP/设计对照|IMP-050/054|不机械后台化，稳定导航 |
| X44|源时间/缺口/能力可用|分散提示|就地状态/维护|EXTEND|IMP-040/050|空池/错误/未启用分开；财报报告期与公告日分示，未知许可/源时点不写成已验证 |
| X45|采集/快照/WS/调度|后台|后台/用户事实影响|KEEP/业务水位验证|IMP-040/019|声明数不是健康数 |
| X46|生成组合/简报/单拍/复盘|五个运营按钮|后台/受控兜底|BACKEND/条件迁出|IMP-049/BUG-009/050协调|先验承接，刷新只读结果 |
| X47|备份/迁移/恢复/审计|运维|系统维护|KEEP|GOV-013/026；OPS-003|先核外发与水位，不抹数据 |
| X48|重复完整详情/统计/榜单|多页|权威服务/只读投影|MERGE→条件RETIRE|IMP-005/050|不同语义不强合并 |
| X49|独立事件主模块|候选|暂H01子域|WATCH|IMP-050/048|持续高频任务/查找收益才升级 |
| X50|独立风险/研究主模块|候选|H04/H05/受控域|WATCH|原owner；GOV-022协调|不先造空页，权限/维护可解释 |

## 12. 十八审计域的产品归属

业务事实owner不随产品聚合改变；M不是固定菜单。

| 审计域 | 已批准比较归属 |
|---|---|
| M01 | 共享导航/对象工作区 |
| M02 | H03自选；H04账户动作（B/C待验） |
| M03 | H01市场 |
| M04 | H01题材盘面 |
| M05 | H01事件/共享证据 |
| M06 | H02机会；H04订单；H05跨日评价 |
| M07 | 共享证券详情 |
| M08 | 共享消息/后台投递 |
| M09 | H04模拟（scope独立） |
| M10 | H04手工记录（非券商验证） |
| M11 | H05复盘/计划；H03只读投影 |
| M12 | H05知识/共享片段；受控编辑 |
| M13 | 共享上下文助手 |
| M14 | H05研究结果；受控实验 |
| M15 | 系统维护/影响就地 |
| M16 | 后台数据/调度；就地质量 |
| M17 | 维护/存储恢复 |
| M18 | 原治理/账本/Skills |

## 13. 当前组件源覆盖

基点 `2adaaee2a5586f25dd1faeb4a3111f616c92104c`；对批准的73份组件覆盖按当前跟踪路径重新映射。结构归属不等每个回调/Canvas已实际验证；每个新入口仍按原小动作契约补覆盖。全站C01–C16定义只读产品文档§10。

| 相对组件路径 | 审计域 | 组件族 | 目标职责 |
|---|---|---|---|
| `agent/evolution-tab.tsx` | M15/M12/M14 | C03/C06/C08/C16 | 受控维护；知识/研究结果按H05只读投影 |
| `agent/kb-browser-tab.tsx` | M15/M12/M14 | C03/C06/C08/C16 | 受控维护；知识/研究结果按H05只读投影 |
| `agent/markdown-view.tsx` | M15/M12/M14 | C03/C06/C08/C16 | 受控维护；知识/研究结果按H05只读投影 |
| `agent/params-tab.tsx` | M15/M12/M14 | C03/C06/C08/C16 | 受控维护；知识/研究结果按H05只读投影 |
| `agent/repo-tracker-tab.tsx` | M15/M12/M14 | C03/C06/C08/C16 | 受控维护；知识/研究结果按H05只读投影 |
| `agent/strategy-health-tab.tsx` | M15/M12/M14 | C03/C06/C08/C16 | 受控维护；知识/研究结果按H05只读投影 |
| `agent/task-center.tsx` | M15/M12/M14 | C03/C06/C08/C16 | 受控维护；知识/研究结果按H05只读投影 |
| `assistant/assistant-mark.tsx` | M13 | C02/C03/C08/C09/C15 | 共享助手，明确对象/引用/取消/隐私 |
| `assistant/floating-assistant.tsx` | M13 | C02/C03/C08/C09/C15 | 共享助手，明确对象/引用/取消/隐私 |
| `assistant/rich-text.tsx` | M13 | C02/C03/C08/C09/C15 | 共享助手，明确对象/引用/取消/隐私 |
| `concept-detail-modal.tsx` | M04 | C04/C08 | 题材关系原文 |
| `detail/board-rank-panel.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/book-trades-view.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/capital-flow-panel.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/detail-modal.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/flow-chart.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/info-panel.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/minute-decision-panel.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/profile-panel.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/quote-strip.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/real-position-panel.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/speed-panel.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/stock-events.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/suspended-badge.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/symbol-detail-context.ts` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/symbol-detail-modal.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/theme-chips.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `detail/trade-panel.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `entry-checklist.tsx` | M06/M09 | C07/C11 | 条件/动作权限独立 |
| `event-panel.tsx` | M05 | C03/C08/C09 | H01事件证据 |
| `hunting/intraday-sections.tsx` | M06/M11 | C03/C07/C08/C09 | H02主机会，H05评价；H04动作引用 |
| `hunting/pick-sections.tsx` | M06/M11 | C03/C07/C08/C09 | H02主机会，H05评价；H04动作引用 |
| `hunting/post-market-enhance.tsx` | M06/M11 | C03/C07/C08/C09 | H02主机会，H05评价；H04动作引用 |
| `hunting/stats-bar.tsx` | M06/M11 | C03/C07/C08/C09 | H02主机会，H05评价；H04动作引用 |
| `hunting/watch-ledger-panel.tsx` | M06/M11 | C03/C07/C08/C09 | H02主机会，H05评价；H04动作引用 |
| `index-cards.tsx` | M03 | C03/C07/C10 | 指数身份/参考日 |
| `kline-chart-pro.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `market/board-flow.tsx` | M03 | C02/C03/C06/C08/C10 | H01合浏览不合口径 |
| `market/events-tab.tsx` | M05 | C02/C03/C06/C08/C10 | H01合浏览不合口径 |
| `market/flow-intraday-chart.tsx` | M03 | C02/C03/C06/C08/C10 | H01合浏览不合口径 |
| `market/fund-tab.tsx` | M03 | C02/C03/C06/C08/C10 | H01合浏览不合口径 |
| `market/heatmap-tab.tsx` | M03 | C02/C03/C06/C08/C10 | H01合浏览不合口径 |
| `masonry-columns.tsx` | M01/M06 | C13 | 不锁死瀑布流 |
| `minute-chart.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `nav-bar.tsx` | M01 | C01/C02 | B/C导航与旧链接 |
| `news-modal.tsx` | M05 | C04/C08/C15 | 原文版本/链接/更正 |
| `notifications/event-feed.tsx` | M08 | C03/C04/C08/C12 | 消息同对象，水位/外发/unknown分开 |
| `notifications/notification-drawer.tsx` | M08 | C03/C04/C08/C12 | 消息同对象，水位/外发/unknown分开 |
| `panel.tsx` | M01/全域 | C03/C13 | 布局原语按任务 |
| `picks/card-entries.tsx` | M06/M11 | C03/C07/C08/C09 | H02主机会，H05评价；H04动作引用 |
| `picks/card-shell.tsx` | M06/M11 | C03/C07/C08/C09 | H02主机会，H05评价；H04动作引用 |
| `picks/pick-card.tsx` | M06/M11 | C03/C07/C08/C09 | H02主机会，H05评价；H04动作引用 |
| `picks/pick-detail-modal.tsx` | M06/M11 | C03/C07/C08/C09 | H02主机会，H05评价；H04动作引用 |
| `price-flash.tsx` | M16/全域 | C12/C14 | 数据意义不依赖动效 |
| `quality-badge.tsx` | M16/全域 | C03/C12 | 源时间/质量，不等健康 |
| `replay-chart.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `research/alerts-tab.tsx` | M08/M15 | C03/C06/C08 | H05阅读与受控维护分权 |
| `research/review-tab.tsx` | M11 | C03/C06/C08 | H05阅读与受控维护分权 |
| `search-box.tsx` | M02/全域 | C05 | 实体/IME/失败回执 |
| `sparkline.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `stock-detail.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `stock-link.tsx` | M07/M09/M10 | C02/C04/C08/C10/C11 | 共享证券工作区，账务命令主H04 |
| `tape/limit-down-tab.tsx` | M04 | C02/C03/C06/C08 | H01题材/涨跌停/披露；日期与分母独立 |
| `tape/limit-up-tab.tsx` | M04 | C02/C03/C06/C08 | H01题材/涨跌停/披露；日期与分母独立 |
| `tape/longhu-tab.tsx` | M04 | C02/C03/C06/C08 | H01题材/涨跌停/披露；日期与分母独立 |
| `tape/themes-tab.tsx` | M04 | C02/C03/C06/C08 | H01题材/涨跌停/披露；日期与分母独立 |
| `theme-card.tsx` | M04/M06 | C04/C07/C08 | 多角色/来源与H01→H02 |
| `trade-form.tsx` | M09 | C11 | H04后端预检/费用/草稿 |
| `ui/incremental-sentinel.tsx` | M01/全域 | C03/C04/C12/C14 | 公共原语逐消费者验；非统一样式即完成 |
| `ui/jump-link.tsx` | M01/全域 | C03/C04/C12/C14 | 公共原语逐消费者验；非统一样式即完成 |
| `ui/loading.tsx` | M01/全域 | C03/C04/C12/C14 | 公共原语逐消费者验；非统一样式即完成 |
| `ui/modal-shell.tsx` | M01/全域 | C03/C04/C12/C14 | 公共原语逐消费者验；非统一样式即完成 |
| `ui/panel-boundary.tsx` | M01/全域 | C03/C04/C12/C14 | 公共原语逐消费者验；非统一样式即完成 |

## 14. 后台声明的目标消费者

当前 `backend/app/bootstrap/schedulers.py` AST共30条声明；这是注册与设计映射，不是开启/存活/业务进展证明。每条均验原开关、单拥有者、触发/输入、版本/水位、失败/取消/恢复、预算与消费者。后台接管先于退出按钮；无页面进展与GET无副作用另验。

| 声明 | 源行 | 目标消费者 |
|---|---|---|
| `alert-triage` | 143 | 共享消息解释 |
| `evolution-agenda` | 150 | 受控议程，H05结果 |
| `review-scheduler` | 163 | H05复盘 |
| `quote-poller` | 176 | H01/H03/详情 |
| `market-snapshot` | 179 | 市场事实消费者 |
| `picks-autogen` | 190 | H02盘前结果 |
| `opportunity-evidence` | 206 | H02决定/H05证据 |
| `data-health-sentinel` | 218 | 就地影响/维护 |
| `pre-limit-radar` | 228 | H02观察/已授权消息 |
| `llm-aux-judge` | 235 | 事件辅助解释；仅实际Jev参与才有Jev凭证 |
| `position-monitor` | 241 | H04风险 |
| `paper-matcher` | 248 | H04订单/成交 |
| `alert-quotes-feeder` | 255 | 提醒输入 |
| `alert-engine` | 259 | 共享消息/风险 |
| `risk-refresher` | 266 | H04/H02约束 |
| `event-collector` | 291 | H01事件/H02证据 |
| `metric-history-backfill` | 298 | H01历史/研究 |
| `premarket-brief` | 312 | H02计划/H01上下文 |
| `picks-watcher` | 328 | H02条件/参考/消息 |
| `picks-buy-point` | 339 | H02决定/动作重检 |
| `board-surge` | 353 | H01变化/H02线索 |
| `lhb-archive` | 364 | H01龙虎榜/H05历史 |
| `picks-intraday-review` | 375 | H05结果标签 |
| `ths-reason-sentinel` | 392 | 事件覆盖/维护 |
| `sentiment-monitor` | 403 | H01环境 |
| `picks-shadow` | 414 | H04每日精选影子，非hunting-shadow |
| `marketdb-sync` | 428 | 历史/H05研究 |
| `factor-eval` | 446 | 受控研究/H05结果 |
| `news-flash` | 463 | H01事件 |
| `llm-gateway-probe` | 474 | 维护健康，不证明业务效果 |
