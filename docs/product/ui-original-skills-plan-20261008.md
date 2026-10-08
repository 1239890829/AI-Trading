# 原版设计技能：全域UI方案与逐项审计

> 摘要：本版补齐33项技能及条件分支、24项Impeccable命令、30项清晰来源、40条跨切面要求、18组细功能覆盖和107项候选裁定。§1–7及附录是方案审计基线；§8为已撤回的历史九稿，§9为当前三套隔离HTML候选。安装、来源阅读、方案覆盖和产品验收分别记录。

## 1. 审计结论与范围

**上一份方案不够完整。** 原包安装正确，不代表职责已发挥；名称入表，不代表参考被用于具体设计。此次按原文、历史要求、当前消费者三路复核，修订如下：

|原方案缺口|本版处理|完成证据属于哪一层|
|---|---|---|
|多项技能合成一句，原有步骤和独立产物被压缩|逐技能写输入、步骤、独立产物、下游、完成判据；Impeccable命令另逐项裁定|方案覆盖，不是命令执行回执|
|工具适配目录容易被误认作完成原版init|明确正式PRODUCT确认与解析路径、选案、方向契约和实现的先后；前轮适配目录只作实验|原版安装/context/seed已发生；init和选案尚未完成|
|34个参考中含身份不清的名称；存在安装状态陈旧描述|删除4项不明确资源；30项清晰来源逐一写具体消费者及取舍，移除错误状态|来源阅读、视觉研究、源码和运行四层分别记录|
|107项清单与41入口被误读成全域分母|107项仅为效果/功能候选；另外建立跨切面要求和路由→动作→共享组件→状态→视口的证据树|静态盘点不是行为或视觉验收|
|原版默认与金融事实、用户偏好冲突没有逐项裁定|保留原文；逐条写裁定、理由和影响，不自造新的融合Skill|项目约束与明确用户要求优先|
|组合建议停留在“以后比较”|每个核心功能明确结构、原语、动效、材质、关联、退出和性能边界；有条件方案给出通过/失败分支|最终皮肤仍由原版选案与实屏验证决定|
|完整矩阵只在忽略目录，仓库读者看不到|全部技能、资源、107项及细功能覆盖写进本文附录；本地图册只是同文渲染|可追溯共享方案，不另建账本|

§1–7及附录记录IMP-081的方案审计基线，**不代表产品UI、提醒业务、运行服务或交易规则已修改**。用户随后“开始吧”授权的产品定义与历史选案见§8；最新用户已拒绝全部旧九稿，当前全新三案见§9。实施仍按具体批准点推进。状态仅归W07对应阶段项；本文统一归集至[账本 §6.0](../retro-and-gaps.md#60-阶段索引)，不承担第二任务排序。

产品目标：中文A股研究者在盘中及时感知新变化，核对依据、跟随强势机会、管理关注和模拟操作；盘后复盘改进。机会必须有理由、源时间、质量、失效条件和未知项。首板、连板、强趋势、独立公司事件分别可用，不能强制所有机会都有后排梯队，也不能把历史赢家当成预测结果。

## 2. 原版来源与冲突裁定

|原版入口|固定来源|已经核实|职责边界|
|---|---|---|---|
|Impeccable 4.5.0|[pbakaus/impeccable](https://github.com/pbakaus/impeccable/tree/778c8a7b71ccd5bfe3ca6ac68c15d9d872d0f87d/.agents/skills/impeccable)|前轮62文件逐Git blob一致，engine 0.1.11；context及concept-seed有实跑证据|Operate主导工作台；Read主导长证据；init/shape/new-work、评价、修整、适配、性能和收尾各有职责|
|design-taste-frontend|[Leonxlnx/taste-skill主技能](https://github.com/Leonxlnx/taste-skill/blob/b482f7a970abb98c4108d4a9f761e458c64cefc8/skills/taste-skill/SKILL.md)|前轮完整1206行、1文件hash一致；上游实验v2|原文排除dashboard、密集产品UI、表格等；没有适用展示消费者就记不适用，不另造营销页|
|redesign-existing-projects|[同仓redesign](https://github.com/Leonxlnx/taste-skill/blob/b482f7a970abb98c4108d4a9f761e458c64cefc8/skills/redesign-skill/SKILL.md)|前轮完整178行、1文件hash一致|现有应用Scan→Diagnose→Fix，保留框架与业务；原文九组审查分别执行|

原文保持不改；旧融合、lite、摘录及误装brandkit入口已退出，不从历史或记忆恢复权威。TasteLab是另一项独立技能，不能与Leonxlnx主技能混称。

|冲突|裁定与具体结果|
|---|---|
|redesign建议渐进升级；用户要求全新风格与组件搭配|替换表现与构图，保留Next/React、接口、对象、业务规则；旧外观作反例，不作为新皮肤基础|
|redesign建议单一强调色；用户拒绝单调|建立有层级的主色、辅助色与金融语义色；不任意多彩，也不把全站压成一块灰|
|原文建议“有机数字”、随机日期、上下文品牌或占位图|金融数字、日期、来源、公司与能力一律真实；展示夹具必须隔离且标合成，正式UI不能为美观改事实|
|“少对齐/错位/大图”与用户反复反馈未对齐|数据列、标题工具行、控件基线严格对齐；错位只用于非密集对象构图，主图是实际图表；不能乱叠或跨容器|
|玻璃拟态与用户要求液态玻璃|blur/透明/高光只算基础层；选用方案须证明局部折射/形变及明确降级。行情文字、Canvas及风险提示不被扭曲|
|Taste主技能排除密集UI；要求充分使用Taste|应用其同仓redesign的完整审查；主技能仅在真实适用表面使用。强套排除域会损害能力与任务|
|“所有动效/组件都用上”与“按需、不适合不用”|全部逐项评估并给消费者或排除理由；同义效果共用一个实现；没有收益不安装、不伪造消费者|
|原版有界审查与“仔细全部检查”|完整范围分批覆盖，作者一批检查加一次确认；收尾交新鲜上下文reviewer。开放缺陷明确处理，不能借次数上限宣称通过|

## 3. 执行顺序与原版入口

顺序是依赖关系，不是把所有技能串成一个大提示。各技能保留原始步骤和独立产物，下一阶段读取产物，不改写上阶段职责。

1. **恢复事实和覆盖范围**：现役产品文档、源码、路由、控件消费者、最新Git与当前可恢复版本。Governor比较保留/修补/合并/替换/退出；后台接管需证明触发、单写者、持久结果、权限和失败恢复。
2. **定义任务**：finesse-brief写用户、首屏任务、真实内容和功能归属；finesse-term翻译交互画面；Interaction Design写任务流、状态、反馈与回退。三项各有产物，不能以一份brief替代全部。
3. **现状和参考诊断**：Taste redesign的Scan/Diagnose完整覆盖原文九组；UI/UX Pro Max按原技能检索；TasteLab对真正入选的具体参考页完成截图、DOM量测、Design Map与Taste DNA。目录浏览只能算候选发现。
4. **原版确认和选案**：按下述init→shape/new-work完成产品确认和方向选择。frontend-design独立给出构图、4–6色、字体角色和同质化反证；finesse-ui完成其设计规格。必要试件可独立验证材料，不能提前替代被选方案。
5. **确认后建立方向契约和实现**：此时才读craft-floor；选定tokens与组件规格，落实交互、材质、动效和失败路径；提醒正确性先于视觉上线。GSAP各技能按具体消费者使用。
6. **全域检查和修复**：critique/audit/adapt/harden/optimize各有独立发现与验收；typeset/layout/colorize/clarify等针对原因整改，polish在最终阶段。真实DOM、行为、Canvas及源数据分别验证。
7. **原版收尾与发布**：新鲜上下文的原版finish reviewer裁定；修复后由原版documenter记录实际产出；适用代码门禁/Jev复评、exact-HEAD DegradedRelease、required CI、合并后CI及运行版本分别验收。

### 3.1 必须补齐的原版确认入口

前轮context未给target，未发现嵌套web源码；这不能证明项目没有UI。前轮artifact中的PRODUCT适配记录与seed仅为工具实验，**不能算原仓init完成或用户选案**，88adca6b也不是批准记录。

正式选案需按原版解析到真实应用范围，核对PRODUCT路径。init先读取已有产品文档，只确认实质空缺；不重问已明确的顶部导航、深色、用途、模拟交易等事实。需至少一次真实确认后，按原版schema记录产品事实和明确未决项，引用既有权威文档；不复制任务状态，也不把颜色/组件写进产品事实。

存在imagegen能力；原版要求单独说明comp-first与code-first的权衡。只有用户选择才持久化buildPath，未答不写默认；当次依原版有图能力默认走comp-first并明示，不把沉默当同意。

new-work的替换世界流程保留：七个有依据方向、至少三个来源家族→原版seed与挑战方向比较→按用户辨识/产品清晰两轴给裁定及明确提升→等完整度的选案页、QUALITY BAR与必要效果图→记录选择。胜出/竞争/拒绝方向、保守出口与重新抽取机制按原文呈现，不能只给一份预选主案。

shape在选案后给可确认brief并停在方向契约之前；批准后才写契约和进入实现。此前“深海图册蓝银”降为方向候选，不是先定色盘再补原版手续。

### 3.2 产物位置与原文保护

原技能全局安装，保持固定源与hash。原版产品/视觉记录的用途分别是已确认产品事实与已建视觉事实；既有product文档仍拥有业务定义，stage仍拥有状态。工具截图、选案payload、引擎状态、生成资源和检查长日志只进入忽略的artifacts批次，不创建应用私有源码目录，也不修改原技能来适配项目。

本轮查到上游真实支持`IMPECCABLE_CONTEXT_DIR`，但它仅在项目/仓库都缺产品或设计文件时重定位读取。surface、live及buildPath仍有固定项目路径，不能把这个环境变量宣传成完整状态重定位。

执行采用明确两层：①需要原版原生选案/comp/build工具时，在忽略artifacts内建立固定基点、无凭据、隔离数据的独立UI试件工程，从试件cwd启动并核context返回的projectRoot/repoRoot与live的appRoot/sessionRoot，完成真实确认记录后按原文运行，工具私有状态只存在已核试件；②真实仓库按同一选定契约修改产品，用已确认事实投影和原版参考执行各职责；context环境变量仅在上游条件满足时使用，不能强制覆盖既有记录，原版只读detect扫实际修改文件，实际应用重新验收。试件中的原生通过不冒充主仓通过；主仓流程标“保留原版职责的宿主适配”，不称完整native。

试件到主仓的交接必须绑定方向选择/确认brief、原版方向契约、选定comp或code-led首屏承诺、region-map、spec与build-state、组件/资源路径清单、源代码diff及准确SHA。comp-led按原版阶段门推进，区域测量和素材先于细部；主仓用同像素尺寸截图重新作区域对比与行为验证，不能复用试件截图关闭实际应用门。生成素材保留准确prompt sidecar与嵌入记录，外来素材保留原始来源/许可；废弃素材随本批退出，不能留下孤立资源。最终DESIGN与sidecar镜像必须指向主仓真实token/组件和值，原版documenter试件产物在主仓复核后才成为有效设计事实。

产品投影只记录确认事实、固定基点和权威文档指针；不自创业务事实或第二账本。选案页和截图公开给用户的内容不含凭据/私人持仓。原版finish reviewer/documenter只有宿主确实加载其定义时才称原版agent；否则用明确披露的兼容执行者按原输入包和输出契约完成职责，不能冒认身份。这个路径保留确认、比较、检查和收尾能力，同时不改项目卫生规则或原文。

## 4. 功能与组件的具体组合

### 4.1 新布局的任务结构

|表面|布局与核心任务|前后台和关联|
|---|---|---|
|工作台|紧凑顶部导航/账户状态；搜索独立；自选对象表、主图、核对内容按主次布置；宽屏可调宽，窄屏切到明确内容窗|自选无小分时；核对保持真正Tabs；切股/日期同步同对象，固定和对比明确保持范围|
|市场全景|连续指数信息带、一个主分析区、就地筛选栏和可展开证据；资金/题材/事件共用对象与日期落点|范围/排序用Select或多选过滤；切换真正内容面才用Tabs；不为少tab混淆不同数据口径|
|机会与跟踪|同股一张主记录，理由、变化、等待、失效内联展开；图表与证据是主辅而非等宽装饰卡|首板/连板/趋势/独立消息按情境标识，不强加梯队；研究假设不冒充胜率|
|研究与复盘|对象/时间索引、阅读面、同尺度证据比较；长文和表格各有可达滚动|来源、反例、适用窗、未验证结果完整；历史K线/回测保留|
|通知与提醒管理|铃铛预览、消息归组、宽管理工作面、单条理由详情；列表与详情各自滚动|AI失败走已筛选规则提醒；同事件修订只展示一次；实时/历史/未处理有清晰口径|
|维护与设置|现有维护区按任务收纳概况与工具，参数/策略健康等不挤主界面；就地编辑或按需面板|后台承接验证后才撤前台入口；管理、普通用户和模拟风险权限不合并|
|手机|整行搜索；对象/主图/核对有明确焦点；必要详情用半屏→全屏同一抽屉，保留返回锚点|软键盘、安全区、横屏、短屏；应用壳不制造双滚动，内容在指定容器内可达|

### 4.2 控件契约与组合决策

以下是实现结构的明确首选；色值、光学强度和视觉样式须经选案实屏确定。候选未过兼容/许可/收益验证就走表中的基线，不让界面等待某个外库。

|组合ID与消费者|组件/参考→材质与动效→状态|联动、退出与验证|
|---|---|---|
|C01 顶部导航/任务菜单|语义nav与按钮＋Navbar Gallery结构研究＋Bencho下落菜单；局部液态边缘，选中块连续移动|仅主任务少量入口；键盘/触摸打开，hover为增强；一个菜单打开，关闭回原焦点；无侧边栏|
|C02 证券搜索/命令面板|Combobox交互、MicroKit/Bencho原位展开；桌面图标变输入、手机整行输入；轻液态外沿，内容实体底|自动聚焦、IME、防抖、旧请求隔离；证券与命令分组，Enter不执行旧结果；Esc回入口；命令不越过权限|
|C03 市场范围/排序/多条件|Select/Radio/Checkbox＋Radix或经核许可的shadcn原语；Bencho Select反馈＋GSAP Flip筛选重排|值选择不是伪Tabs；活动条件摘要、清除和真实计数；原地重排不重载同批数据，保留焦点|
|C04 核对Tabs/MA按钮/跟随固定对比|Tabs仅管理内容面；SlidingUnderlineTabs原则统一指示；MA及模式为Button/aria-pressed|选中面来自同一色义且跨页面一致；无超大底块；标签不挤换行；按模式维持对象/日期/版本身份|
|C05 分时/K线/云图|现有lightweight-charts/真实Canvas；磁吸游标和精确值同步；绘图区实体稳定|主图占据主区，宽度随容器；分别量绘图区/坐标/工具高度；触控与页面滚动不互抢；无虚构插值和数值滚动误读|
|C06 机会/证据/研究条目|内联Disclosure优先；深证据用同来源共享元素展开；React Bits TiltedCard仅非密集封面候选|展开其余内容自然让位；保留来源、对象、日期、返回位置；层叠同方向且在容器内；有长按预览的等价按钮|
|C07 详情抽屉/宽管理弹窗|按阻断程度选择Dialog、Sheet或Popover；Bencho/MicroKit运动研究；半屏/全屏连续圆角；实体阅读底＋液态外沿|桌面宽规格、手机可用宽；位移或速度关闭、吸附锚点、嵌套背景后退；Esc/焦点/锁滚动、脏草稿退出均验|
|C08 提醒与铃铛|统一Hugeicons小可见面/完整命中区；新计数低频滚动、状态文字自然替换；无条件循环光效不采用|规则/AI来源可辨；刷新/重连不复弹，升级才新提醒；计数分母/北京日和已读持久状态一致；图标光学居中|
|C09 操作按钮与写动作|ThreeUI已核免费Dark Pill/Dark Glass及Galaxy作为局部表面比选；Glassy Split仅作视觉研究；同位置pending→成功/失败/unknown；CSS/GSAP分工|关闭/删除/普通/危险/提交全族同时改；不靠超大按钮造层级；后端确认才成功，未知给查询；危险动作保留确定性硬门|
|C10 数据表/长列表|语义table/列表＋可缩Grid/Flex＋Radix ScrollArea候选；密集行实体底，长列表按实测虚拟化|表格内容min-width与独立横向viewport；标题/行操作不被切；可键盘/触摸/拖动滚动；筛后补足有效提醒不饿死后排|
|C11 多选/排序/比较|同批对象layout重排、选中数量与操作条联动；拖拽显式把手＋键盘替代；最多一套引擎拥有transform|选择不会触发跳股；取消恢复；排序持久化真实反馈；比较同尺度/同窗口，禁用误导性两图营销滑块|
|C12 冷启动/失败/帮助|Impeccable onboard/clarify/harden；component.gallery状态语义；保留任务所需最短指引|有效空、没数据、错误、陈旧和未授权分别可读；一屏知道下一步；不强造新导览页面或占位“AI分析”|

C01–C12是本节组合编号，附录要求用R、资源用REF、表面用S区分，不是任务编号。C01–C12是同一视觉系统下的组件家族，不是十二种皮肤。一个元素的transform/opacity仅有一个动画owner；一个组件可覆盖多个同义需求ID。Tooltip只补充说明，关键理由/风险/操作名不能依赖hover。

### 4.3 材质、字体与动效规范

色盘需同时解决深色质感、银色阅读、多色层级和红涨绿跌。每个候选写4–6个核心色值及各自角色，再做真实内容对比；蓝银只是候选之一。主区、背景、浮层区分明度与色域；选中与危险/降级/行情涨跌互不混用。浅色若仍有消费者则复验，深色是主要验收。

液态试件同时比较CSS基线、React Bits GlassSurface的SVG位移、ThreeUI已公开材质；ultramotion提供光学/形变研究。React Bits当前许可为MIT附Commons Clause，复用前核本项目分发适配；其GlassSurface源码对Safari/Firefox回落CSS，不能承诺这两个浏览器具有SVG背景折射。ThreeUI部分高级案例只可参考，不默认已获免费源码。文本与折射层分离，背景移动验证像素变化，WebGL内部纹理不冒充底层DOM折射；Safari/Chromium/Firefox分别记录可用、降级和性能。读数与正文不靠极低alpha透背景，减少透明模式使用高对比实体底。

中文字体先核本地/系统实际字形与加载，数字tabular-nums；标题、正文、标签各有字阶/行高/字重，长原因全文可展开。小控件桌面候选28–32px，主动作32–36px，关闭可见面24–28px；手机命中区至少44px且位于容器内。不能靠溢出的伪元素补触点。最后尺寸按320px、真实窗口和长中文测量确定。

运动按任务分层：按压80–140ms、控件切换160–240ms、展开220–360ms为初始候选，弹簧以阻尼/速度试件定，不把时长当达标证据。风险、价格和输入响应即时；入场仅初次/实质新增，轮询不重复动画；结束必须减速、吸附、回收或切状态。减少动态直接终态，光学层暂停不影响业务；关闭/卸载清理observer、rAF、timer与GSAPcontext。

### 4.4 提醒、滚动与回放的必修行为

提醒链：现有事件事实→确定性候选筛选→新变化/关联/风险→提醒意图→AI可用则解释，不可用标规则回退→按事件修订投递及展示。筛选/去重/升级与AI解释分离；不同证券/题材/规则类型/版本不碰撞；ignore不封死后续风险升级；刷新/多轮询/重连同态不重复。unknown、stale和无效数据与有效信号分开，不能用全局静音或一味延长冷却代替修正。

通知管理复用同一事实与修订，明确事件时间、判读时间、AI/规则、筛选状态、待处理/已确认/失败；北京日计数使用完整分母，不把最近30条说成今日。筛后有界补足有效结果，错误可恢复。

滚动树：应用壳固定→任务内容窗→列表/证据/表格→弹层，每区指定唯一owner和高度/宽度约束。可收缩节点min-width/min-height为0；表格内容宽度驱动横条，窄屏/短屏保留图表底线，不能用全局overflow:hidden掩盖裁剪。自动隐藏原生滚动条的平台增加可见控件或边界提示，验实际操作，不承诺强迫系统永显原生横条。

取消历史回放仅退出Replay UI、状态和独有消费者；历史K线/研究/回测/共享行情API及模拟请求replayed幂等字段继续保留。自选无分时小图，管理层不遮字/溢出；核对视角Tabs、MA按钮语义、头部有效工作台入口保持用户已确认功能。

## 5. 验收、证据和停止规则

### 5.1 覆盖与截图

本文附录D是子功能起点，实际分母由当前源码/调用点/动态入口共同扩展。每条记录：路由与入口→真实动作/副作用→对象/日期/版本/账户→共享原语→来源/时间/单位→状态→视口→截图/DOM/行为/Canvas→缺陷与复验。共享按钮通过不代表每个容器里的按钮通过。

批次覆盖320/390/768/1024/1280/1440px、用户实际窗口、桌面短屏、手机横屏、200%缩放；同类控件全消费者扫描。每模块/每子功能的默认图及有意义展开/菜单/错误等另图；拖拽、手势、FLIP、中断与返回另有短录屏或逐帧证据，图册可按功能筛选，逐张打开确认。截图标记版本、尺寸、对象/数据时点和真实/隔离夹具；恢复服务真实状态后再验一组，不把演示数据当线上数据。

状态最小集合：loading、ready、有效空、部分空、error、stale、unknown、乱序、取消/重试、快速切换、重复加载、权限失败；写动作另验pending、成功、明确失败、结果未知及查询恢复。键盘/触摸/鼠标、中文IME、焦点进出、Esc、浏览器返回、软键盘、安全区、减少动态/透明分别覆盖。

### 5.2 原版检查不相互代替

critique评任务/主次/同质化，并按原版先交付报告，再保存snapshot/trend及具体问题收尾，主仓兼容产物镜像进artifacts；audit评语义/可访问性/适配/技术；adapt检尺寸输入方式；harden检长内容/空错/权限/国际化边界；optimize检运行成本；polish检最终细节。DOM证明角色/焦点，行为证明流程，Canvas证明实际绘图，数值对同版响应，截图证明可见布局；任何一种都不能替代其他证据。

作者完成一批PC/手机检查→集中修复→最多一次确认；new-work构建收尾的通用机械detect按原版运行一次。critique的独立B检测，以及真实缺陷触发的typeset/layout等scope检测，按各自原文独立执行和必要整改复验；通用收尾的次数不能压掉这些职责。原版finish reviewer必须使用无继承历史的新鲜上下文和实际输入包；其身份/加载方式须核工具能力，不能把普通审计agent或同线程自检冒称原版review。recapture/rebuild/fix/ship按原文处置，未解决表不写通过；用户新证据优先触发重新审查。

documenter最后按真实代码和已确认方向写token-bearing DESIGN与原版sidecar；不能提前把本方案当已建系统。路径适配与原版工具差异如实记录。两批没有新增有效证据就复审方法；对缺陷不无限换检索词、反复微调或降低标准求绿。

### 5.3 性能与发布

同机、同窗、同数据比较基线/候选，记录bundle增量、长任务、交互延迟、图表拖动、滚动、滤镜面积、内存、隐藏页与卸载后资源。LCP<2.5s、INP<200ms、CLS<0.1为待测目标；实验室近似不冒充真实用户Core Web Vitals。功能与视觉清晰优先，未证明收益的Lenis/Vanta/重WebGL不进入行情主路径。

适用本地门禁后，非简单代码才进入Jev复评；纯文档不评分。DegradedRelease明确作者=发布操作者，不能冒充独立Review。准确HEAD/最新master/required三job/release_check/PR合并后CI按原门禁；代码入库、运行加载、实源及效果分别说明。

## 6. 分阶段交付与恢复

|交付顺序|交付物|退出条件|
|---|---|---|
|交付1 方案覆盖修订|本文技能/资源/跨切面/107候选/细功能附录；清晰来源与冲突裁定|完整映射、相关文档守卫、准确版本发布；不宣称产品已改|
|交付2 产品确认与方向选择|原版init记录、shape任务、new-work候选/挑战/QUALITY BAR/必要效果图与用户选择|原版产物路径兼容明确；确认brief后停，UI批准才建方向契约|
|交付3 提醒正确性|同一事实链的规则回退、筛选/去重/升级、展示修订和管理口径|AI失效有效规则可提醒；同态不复弹，新态/跨股/跨日/重连/刷新边界正确|
|交付4 组件与三代表面|新tokens和全族控件；工作台/市场/通知PC手机；材质性能试件|主图、搜索、关闭、选中、宽弹层、滚动可测，组件来源/许可/成本明确|
|交付5 全部消费者迁移|附录D逐动作、所有共享入口、后台收纳、回放退出|没有遗漏/孤立旧皮肤；真实功能/权限/状态/上下文/返回保持|
|交付6 全域原版收尾|独立评价产物、缺陷修复、真实截图图册、finish disposition、documenter产物|严重缺陷关闭；限制明确获准；文档事实对应实际产出|
|交付7 发布与运行核实|准确HEAD门禁/CI/合并/清理/运行版本|各证据层独立通过，未验领域如实标明|

恢复点仍为ui-current-saved-20261008与独立source包；当前产品UI不受本轮文档修订影响，旧活动保存版不恢复。技能恢复包只作取证，不自动恢复旧融合权威。IMP-081只完成方案覆盖修订及发布，当时未执行原版选案、UI/提醒实施、兼容/性能和新截图；历史选案见§8，当前全新三案与验收边界见§9。

传播：总方案、plan-registry、INDEX、W07、handoff与细功能审计指向本版完整附录；产品与猎场的对象/机会/权限语义不变，其已有指针继续有效。AGENTS/原版技能/协作/AI准入不改规则；不为文档补全产生新生产消费者。

## 7. 本轮实际取证与未执行项

本节记录IMP-081基线；IMP-082历史选案见§8，当前进展见§9。任务统一归集至账本 §6.0 下W07对应阶段项；本文不维护任务状态。

实际完成：三路只读原文/消费者/来源审计；原仓与清晰资源逐项刷新；原版引擎上下文源码、React Bits、ThreeUI、MicroKit等固定源码核对；补当前7个路由文件/85份非测试组件的静态范围；修订技能/资源/需求/子功能映射。原版安装hash和context/seed实跑为前轮已核事实，本轮没有重复宣称安装或init通过。

UI/UX Pro Max本轮实际运行系统方向、图标语义、焦点检索及一次窄化重试。首轮误命中营销Showcase、浅色绿CTA和通用Inter，明确拒绝；重试命中Financial Dashboard、Landing N/A。只采用适用的数据密度、焦点和可访问名称要求，不新增Phosphor图标家族，不将AAA增强指标误称AA硬门。这说明检索输出需审查，不能以“技能运行了”认定建议适用。

未执行：用户方向选择、正式init确认、产品UI/提醒代码、全部案例视觉/DOM提炼、浏览器/手机材质运行与性能、新产品截图、原版finish review。本文是经审计补全的实施方案，不是完成这些步骤的证明。

以下附录是方案正文的一部分。清晰资源30项有逐项裁定；删除的4项不再留活动资源行，旧身份只保留忽略审计证据。不清晰名称不以同名网站替代。每一项“默认纳入”都是待实施决定，不等于已经生效。
## 8. 历史九稿记录（已全部撤回）

**当前不生效。** 用户最新明确拒绝此前全部九稿，要求从空白重新制作最多三案。本节保留历史证据；九稿的名称、构图、暂选、配色、签名动作、shape候选定义及comp后续流程均退出当前选案权威，不得用于融合、换皮或恢复旧锁定。当前工作只以§9为准。历史产物保留不等于仍可选择。

### 8.1 已执行与未执行分开

用户2026-10-08“开始吧”批准推进已审方案。开工基点为PR #240合并的d0972819775c7161daca1c54379dd13540c82b51。批次为`artifacts/runs/ui-direction-selection-20261008`。本节是产品定义与选案记录，不是实现契约或DESIGN。

|职责|本阶段实际产出|边界与下一阶段|
|---|---|---|
|finesse-brief|原版完整schema：产品/用户任务、8组逻辑实体、4任务通道及3共享/受控能力、L1–L3、6组首屏字段的writer/cadence/冷启/失败|使用完整原仓按流程人工执行；未全局安装，不称native命令；实体不冒充已存在数据库表|
|Interaction Design|任务路径、反馈、错误/未知、回焦和跨模块对象连续性|规范不是实际交互通过|
|frontend-design与finesse-ui|7个有文化来源的完整方向，至少3个来源家族；各有主次结构、字体、配色、手机、退化与反例|独立产物没有被单个融合摘要替代；色值对比只是实体草案计算|
|finesse-term|12项用户原话→可观察行为→真实消费者→状态/PC/手机/无障碍/验收探针|10条词库零命中如实保留；1条错峰语境误配拒绝；不编造术语|
|原版Impeccable|真实试件context、确认PRODUCT投影、7候选seed、全部6挑战者逐轴裁定、原生问板、4张同surface视觉稿、真实方向选择|本轮seed=2eb94603；选择后返回shape，未进入build-phase、契约或产品代码|
|TasteLab|Bencho真实DOM/AX可读；截图与完整只读extract标准量测未完成|状态partial；不称已完成Design Map/Taste DNA；后续构建前用可工作的采集路径补证|
|Taste原版主技能/redesign、UI/UX Pro Max、动画/性能与系统审计|本阶段沿用附录已核原文职责和消费者约束，不伪称全部阶段已执行|密集工作台不强套Taste主技能排除域；redesign完整审查、craft floor、适配、性能、finish/documenter在其对应阶段执行|

原版工具的context/seed/question与live根解析不同。试件在忽略artifacts内，无嵌套Git；真实context确认其projectRoot/repoRoot，seed与question状态留在试件。live会找到父仓Git，故本阶段不用live，不声称完整native live。主仓后续实施采用明确宿主适配；原版完整asset-producer角色没有在宿主暴露，两个方向稿由按同输入/产物契约的兼容执行者制作，根代理逐图复核，不冒称已加载原角色。

### 8.2 真实选择与生成稿边界

先写7个有序候选，随后原版seed分配D3“折页地图”，没有重抽。六个挑战者按用户辨识/产品清晰度分别判断；“格序”保留为竞争方案，另外五个不采用外观，但分别捐献独立时点、近处来源/反例、紧凑层级、单一焦点和即时反馈纪律。裁定是设计判断，不是用户测试分数。

原生问板最初提供“折页地图”“夜行索引”“格序”和熟悉同类产品出口的完整选案图，保留五个拒绝理由和重抽入口。真实`serve-question --wait`曾返回`optionId=assigned`、`buildPath=comp`，无steer。用户随后明确要求“先把前面的所有方案都设计出来给我看，我再决定，不止是折页地图”；因此该选择改为暂选，全部7个原创方向及2个对照一起呈现后再决定。历史选择及其被新指令替代的关系另存`selection-receipt.json`；代理没有POST答案、点击选项或制造用户授权。

生成稿全部显式标“隔离设计样本·非实盘”。首个折页稿错误保留永久侧栏，已在选择收集前按同方向重做；折屏首稿错误保留永久右栏，后以内置imagegen编辑删除，均不是重抽世界。最终9图保留工具原始尺寸（8图1586×992，折屏1585×992），未裁切或缩放，exact prompt嵌入并回读一致，sidecar均`approved:false`。并行资产执行者与根代理分别目视，图像只证明设计构图可见，不证明DOM、Canvas、响应式或业务行为。

生成器添加的日期、其他证券价格、量能/传闻叙述均为虚构，不能当作市场事实或带入产品。MA复选框/MA10选态和底部四列证据仍是生成缺陷；实现以本节任务定义、真实数据与后续获准构图规范为准。方向选择不等于三构图批准，不能提前将sidecar设为approved。

“格序”的原站board/hero图片实际HTTP 403，未绕过、未目视；其稿按原版文字规则生成，不称像素参考复刻。TasteLab量测缺口及该原图缺口保留，不用营销截图或估算量测补齐。

### 8.3 九套方案的明确差异

|方案|首屏结构与辨识点|主要用途与代价|手机适配原则|
|---|---|---|---|
|D1 夜行索引|稳定自选/主图/证据三窗；银绿导视定位串联同对象|频繁扫股、随时核对；长证据不能挤窄主图|自选先行选对象，主图独立内容窗，依据同源展开|
|D2 跨页|约62%主图+38%连续案卷；烟梅护页边，引用就地展开；自选在底部|长依据与图对读；底部表需收纳以保图高|图与案卷分别内容窗，同引用半屏到全屏|
|D3 折页地图|横向自选索引、近全宽主图、底部可扩展证据折边；石油墨与银绿|图表优先；不能把所有功能塞进一个底部抽屉|整行搜索、横滚自选、主图底线、证据半屏/全屏|
|D4 目录卡|全宽长记录列表，当前行原位展开图+依据；鸢尾索引边|逐条筛选与观察线索；直接盯一只股需保留搜索直达|短记录→原行详情，主图先满宽、依据在下|
|D5 图版并读|默认单图，显式对比才两幅等高图与下方差异表；冷钢双片边|两个标的或时段的真实比较；不可比时不强联动|A/B内容窗切换，不压两幅窄图；差异表内横滚|
|D6 折屏|中央全宽工作面+关注/依据/模拟三个互斥收纳槽；葡萄墨与青玉内缘|低频功能收纳、工作面变形；高频工具应可固定展开|一组展开、短标题入口、保持足够触点，不依赖扇形手势|
|D7 年鉴页签|局部可知时间索引+主图+连续证据页；烟橄榄与淡钢时间字|来源/修订/后续观察定位；不允许变成历史回放|时间索引收成可展开时间条，源时间保留，无播放控制|
|格序 对照|严格行列基线，蓝钢色面与选中坐标关联|密集读数清楚；过多格线会显生硬|主图优先，表格在容器横滚，文字不缩成不可读|
|常规 对照|熟悉三窗证券终端，黑/银/冷蓝；同等完成度|作为任务可发现性基准；辨识度普通|内容窗与宽阅读面，沿用同一数据/状态约束|

9套均可完整放大。图册是只读设计产物，不能“采用”即修改生产；没有把生成图内文案或绘制控件当作已验证真实组件。此前4稿明确复用，新增5稿有各自prompt与回执，不把复用冒称新生成。

### 8.4 七部分候选shape brief

下列定义记录此前暂选的“折页地图”，用于解释完整定义应达到的颗粒度；用户已重新打开全部方向比较，故不是锁定方向或已批准契约。最终选择后须相应更新第3/6项，不把折页拓扑强套给其他候选。

1. **任务与用户。** 中文A股研究者在盘中快速切股、看到新变化、核对依据与等待条件；盘后以同一对象、时段和证据版本复盘。工作台为Operate，长证据为Read。首屏应回答“看哪只、发生什么、依据在哪、缺什么”，不提供确定性上涨结论。
2. **结果与证据。** 主动作是选择对象→读真实主图与源时间/质量→检查依据→决定保持关注或进入获准模拟流程。source time、请求刷新、北京日窗口、历史投影分别呈现；历史triggered且actionable=False不能变成当前可执行机会。提醒数量不能用读取窗口冒充全天分母。成功以真实任务行为及证据验证，不以生成图美观替代。
3. **选定方向。** “折页地图”：行情主图近全宽，核对上下文从底部折边展开，横向自选索引替代永久侧栏。石油墨底、银绿定位、少量砂铜辅色；红涨绿跌独立保留。唯一主导动作是底部证据面的连续展开/归位；局部液态折边表达材料，价格、坐标、正文不折射/倾斜。不是装饰地图或把K线卷起来。
4. **范围与边界。** 最终覆盖附录D的18组细功能；先做工作台/搜索/图表/核对/通知/自选等共享组件代表流，再迁移市场、机会、研究、模拟和受控维护。复用业务对象、接口、框架与权限，视觉和组件搭配重新设计。规则fallback/去重由后端实现与实证，不在前端模拟；退出历史Replay UI、播放态及独有消费者，保留历史K线、研究回测、共享API与replayed幂等字段。真实券商、实盘下单、付费数据与部署不在范围。
5. **状态与范围。** 覆盖冷启、loading、ready、合法空、partial、stale、error、unknown、乱序、禁用/权限、cancel_requested、实际canceled、retry与恢复。数据长度按真实消费者取得，不编造实体字段上限；无来源写未知。连续切股/日期/版本不得串图或串证据；先显示准确目标数据再做次级反馈，不把插值价格当行情。
6. **布局与交互。** 桌面顺序：顶导→宽搜索/对象和来源条→横向自选→主图/量能→底部核对Tabs。主图有可验证宽高底线，短屏时次要正文内滚动；证据过长进入同对象全幅阅读，不持续挤压图。手机搜索占可用整行，自选索引与指标容器横滚，主图保持可读，证据在同面半屏/全屏吸附；软键盘/安全区重测。核对是真Tabs，MA是独立pressed按钮，市场筛选不是Tabs。关闭/通知图标居中，紧凑可见面与足够触点分开，长理由完整可达。每层只有明确滚动owner，超宽表格有可操作横滚提示。
7. **约束与待定。** Next/React、中文、来源/质量、tabular-nums、鉴权与模拟硬门继续保留。液态仅局部光学层，文字实体底；减少动态/透明及低性能环境退化可操作。构图批准后才定组件几何、精确材质和性能预算；不得用未浏览的参考或未经许可的素材补事实。按320/390px、桌面与短屏验证DOM/行为/真实Canvas，动效用录屏，性能按同机同数据对照。完整参考/组件裁定继续绑定附录而不是一口气安装全部依赖。

### 8.5 历史比较流程（被最新指令覆盖）

此前“交九稿再决定→形成对应brief→同世界三构图comp批准”的流程已被用户最新“旧九稿全部拒绝、最多三套全新可操作方案”覆盖。暂选与待确认shape均失效；本节不再要求用户回到旧问板、旧折页定义或旧comp步骤确认。

原版产品事实、方向确认、未批准不落方向契约的职责仍保留。当前以§9三案比较、实际修复验收及真实用户三选推进；本地HTML候选不是生产实现授权。3000/8000与恢复点不因候选页面变化而改变。

## 9. 当前全新三套可操作HTML候选

### 9.1 最新指令、范围与入口

用户已明确撤回全部旧九稿，要求按完整原始要求和各技能职责从空白重做，最多三案。当前批次为`artifacts/runs/ui-three-refined-20261008`，没有将旧九稿图片、方向名称或布局作为新设计输入。三案互相独立构思，不融合旧稿；IMP-082继续进行中，等待用户在完成必要修复验收后比较三案。

本地[三案比较入口](http://127.0.0.1:51066/index.html)只列A/B/C三个直接页面，没有iframe嵌套；[来源与技能覆盖](http://127.0.0.1:51066/coverage.html)可展开查阅实际消费者、采用层次与未采用原因。端口51066仅用于隔离试件，不代表部署。候选名称以本批设计记录为准：

|候选|本批代表结构|可操作代表面|
|---|---|---|
|A 从一个对象开始|宽主图与右侧纯自选，下方宽幅核对阅读带|工作台、市场、提醒|
|B 图表与核对记录|主图与右侧连续核对同屏，纯自选表在主图下方|工作台、市场、提醒|
|C 观测室|顶部连续关注带，宽主图与证据档案同一视野|工作台、市场、提醒|

真实自托管Noto Sans SC与Geist、语义控件、真实Lightweight Charts Canvas和共享GSAP为本批可运行基础。报价20px桌面/18px手机是本批工艺上限，不冒称用户逐字给出的数字。所有价格、事件、统计和提醒均标合成设计示例、非实盘；交互只作用本地候选，不证明真实行情、规则回退或通知去重已修复。未选择、未批准，不落生产DESIGN或方向契约。

### 9.2 全库存逐项评估，不全部强装

评估分母继续保留**33项技能及条件分支、30项明确资源、40条跨面要求、18组细功能、13组95条原始效果加12条整改（合计107）**。原附录逐项登记和本批`resource-recheck.md`保持可追溯；4个待辨识历史名称另保留缺地址状态，不猜同名资源或纳入活动实现。

每项记录原始职责/来源→真实消费者→本批产物或组件→采用/条件采用/不适用/证据不足→未采用原因→待验收项。共同效果可由一个实现承接，没有消费者或收益证据就不安装；来源读取不等于复制许可、实际运行或验收通过。`coverage.html`汇总33项技能、30项来源与4项待辨识；40/18/95+12的全域实施映射仍以原附录及后续真实消费者验收为准，不能以三代表面替代全域闭包。

本批采用工作台product分支；营销Hero、巨型报价、常驻侧导、自选分时小图、持续WebGL背景和无收益惯性滚动没有当前消费者。真实业务、账户、权限与后台规则保持原owner，不为了“用全”技能扩任务、扩依赖或强搬营销布局。

### 9.3 各技能独立产物和适配边界

三案分别保存`a-design.md`、`b-design.md`、`c-design.md`，不把全部技能合成风格摘要。frontend-design两遍分别留构思、字体/颜色角色和反默认修订；Interaction Design留任务流、十态、反馈/恢复及键盘触摸等价契约；finesse-ui留Design Read、dials、product密度、dataviz、mobile、motion与preflight；原版Taste redesign九组逐组自查；Impeccable craft-floor独立保留工艺和真实状态检查。

C另留finesse-brief实体/页层/writer/cadence定义与finesse-term无新触发的边界。Taste主技能的密集UI排除仍生效；redesign的渐进默认被用户本轮全新重做授权覆盖，但金融事实和九组审查不弱化。finesse固定原仓为人工读取应用，未全局安装；Impeccable图像comp在本轮明确适配为真实HTML候选，不冒称原生七方向/选案/finish/documenter全流程已完成。未加载原版专用角色时，只称兼容执行者。

### 9.4 先holistic、后detector；真实修复验收进行中

`holistic-critique.md`先完成任务、主次、信息负担和行为真实性的判断，之后才合并detector证据，避免原始命中数先决定审美。该审阅A/B为非作者源码判断，C为作者自审；后补读取部分主执行者提供的实际截图，不是本人全套键盘、触摸或浏览器量测。状态为DEGRADED，不冒称原版双隔离完整critique/audit或finish review。

原版Impeccable CLI针对三个HTML各实跑一次，engine-probe为0.1.11、exit0；每案扫描前后html/css/js哈希稳定。`detector-raw/run.json`与`summary.json`核得：

|候选|detect实际exit|原始warning数|解释|
|---|---:|---:|---|
|A|2|26|成功完成检测且有发现，不是clean|
|B|2|71|成功完成检测且有发现，不是clean|
|C|2|69|成功完成检测且有发现，不是clean|

166条均为原始warning，不是166个独立根因，不是项目P0/P1等级，也不能按26/71/69排名或称某案通过。`detector-review.md`对小功能字、padding、根裁剪、指定字体、字阶与width动画分别核对：小字号是实际风险；根固定/指定Geist不能为清警告盲目撤销；性能需实测。检测者A是作者自查，B/C为非作者源码检查；其browser visibility、overlay与console部分未执行，不能冒称完整Assessment B。

主执行者正按发现修复并验收真实Canvas高度、同对象证据、动作反馈真实性、空自选恢复、删除/已读后的焦点、移动触达/字号及关闭全族。作者语法、HTML、静态对比度、本地DOM测试和finesse regex只证明各自范围；它们不抵消原版detect的实际warnings，也不替代新字节上的浏览器复验。本批代表面的必要修复已完成实际复验，范围与未验证项见§9.5；不据此关闭全域实施门。

### 9.5 TasteLab、最终验证与恢复

TasteLab部分原站HTML/源码可读不等于视觉提炼完成。远端浏览连续超时；本地IAB已恢复，可用于本地三案真实预览，但完整外站截图+只读extract、量测、Design Map/Taste DNA仍为partial。不能把远端超时写成所有浏览能力不可用，也不能用估算色值/尺寸补齐extract。

**代表面最终验证**：`artifacts/runs/ui-three-refined-20261008/final-acceptance.json`绑定实际文件哈希与截图。三案工作台在1440×900、390×844、320×740、1024×600有真实Canvas量测；另核桌面/手机市场与提醒，共24个表面/视口观察。曾发现A手机图表0高、B根滚动与A市场根溢出，均修复后复测；图表手机最低230px。真实操作核搜索空态/Enter选股、周期/MA、Tabs键盘、通知关闭和焦点、删除撤销、空清单恢复、关注筛选及B同对象失效/对比。三案同源最近12点数据表均有实际入口。

用户再次纠偏后，全部7个Select和5个Checkbox改为真实Radix组件，隔离固定版本分别2.3.8/1.3.12，未修改生产依赖。下拉采用popper而非item-aligned，优先bottom、6px间隔、12px箭头内收，必要时碰撞避让；Portal在原dialog内或body。7个菜单在320与1440宽共14次实际展开均在触发器下方且不重叠；真实键盘、Esc保留父dialog、重置与消费数据同步通过。另修复旧手机隐藏span规则误伤B新组件。版本、MIT许可、桥接/失败恢复、bundle体积见`controls-dependency-receipt.json`；共享运行时及组件各完成Jev baseline与带previousEvaluation复评，分数只作辅助。

减少动态/透明、强制颜色与资源失败恢复只完成源码/本地验证，未完成全部系统偏好、屏幕阅读器、设备/性能实测；液态光学使用局部SVG位移与fallback，未证明等同原ultramotion完整WebGL表现。原版detect未在新字节上重新判clean，166条历史warning不自动作全部关闭。文档门禁在最终文本上另核，不以历史194 passed替代。

恢复点仍为`ui-current-saved-20261008`（0377b8414be70710bd4fff67818974601d5910da）与`artifacts/recovery/ui-current-20261008/source.tar.gz`，不变。本阶段不改生产UI/提醒后端、3000/8000、凭据、交易权限或部署。待三案必要修复验收后交用户三选，再绑定所选方案的具体定义与方向确认；候选代码和文档发布都不扩成生产实施授权。

### 9.6 用户已选择B+C：选股代表闭环已补，全功能迁移未完成

用户2026-10-08已明确选择“第二和第三套的结合”，即本批B“图表与核对记录”与C“观测室”的组合。该选择覆盖§9.1、§9.5中“尚未选择/等待三选”的阶段性记录，不再等待三选；旧九稿仍全部退出当前权威。

`bc.html`、`bc.css`、`bc.js`、`bc-contract.md`、`bc-design.md`及功能保真核对已形成，最终文件哈希绑定`bc-acceptance.json`，仍是隔离合并原型。契约由`product_workbench_spec`生成，固定**B的图表与连续核对主体+C的顶部关注带、石墨与淡硫风格**。顶部关注带替代B底部自选表，只保留一个自选管理入口和同一选中证券状态；10个原始来源绑定文件哈希不变，Geist/Noto原共享资产未改。frontend-design、Interaction Design、finesse及固定原仓pbakaus/impeccable、Leonxlnx/taste-skill分别按原职责应用并留产物，不能恢复已退役的融合skill权威。

用户追问是否保留全部功能，并强调选股最重要。核对确认前稿只有工作台/市场/提醒三个代表面，**缺少核心选股完整流程，不能称为全功能版**。选股是真实生产核心，不能用市场过滤替代。现已补一级“选股”入口及合成候选→依据/等待/失效/质量→同股图表→明确加入本地关注的代表闭环；真实API和业务消费者仍未迁入。`bc-feature-parity.md`已核原生产子功能、动态猎场、深链、持仓模拟、复盘与后台，基线段绑定整改前稿，§10单列当前增量，未接入项不作完整。本轮原生产源码未改、未删除；缺失功能必须逐子项迁移验收并确认覆盖后，才能替换旧版，不能因换UI丢功能。

当前复用`codex/ui-direction-selection`分支；PR #241仍为draft，回填基点HEAD为`5f0f9b8b88d5aef86c764b28cac5ff5984d66dd6`；本轮fresh fetch成功，origin/master仍为`d0972819775c7161daca1c54379dd13540c82b51`。入口以BC为主，原A/B/C折叠保留。[BC合并试件](http://192.168.110.169:51067/bc.html)本机HTTP可访问，仅供同一Wi-Fi下的LAN访问，不记为真实手机已验。全部内容继续为合成设计示例、非实盘，生产3000/8000不动。

**BC前稿有限实测**：`bc-browser-observations.json`记录8个尺寸/表面观察：1440×900及390×844的工作台/市场/提醒，另有320×740、1024×600工作台；根尺寸均等于viewport。当时实际7个Canvas，绘图区桌面939×441、390手机350×260、320手机284×260、短屏650×279，字体loaded；前稿3个Radix Select和3个Checkbox，6次PC/手机菜单在触发器下约6px且不覆盖；PC通知宽900px、关闭32px，390手机抽屉关闭44px。这些观察不覆盖新增选股或最终修改字节。

**选股实测及最终一次确认**：`bc-hunting-browser-observations.json`保留8条增量观测、3条新菜单记录，含初批与修后确认；PC1440、390与320根无溢出。宏和603256进入同股图表/依据时关注数仍5，返回选股后明确加入才变6，防重复并回焦。10分组全部可达：5组合成流程、5组显式生产未接入；盘中失效筛选仅1条博云，每日精选失效筛选合法空，恢复后1条金安。当前4 Select/3 Checkbox，新第四Select在PC/390触发器下约6px，修后手机再确认仍不覆盖。390/320绘区350×260、284×260，带返回入口的最终桌面为939×439，不能混用前稿无返回的441px高度。

手机状态4em挤字和缺19字已作一次有限修复：390/320状态宽328/262px、高约19.8px，均为一行；BC专属19字自托管字体为3×7304B，原共享未改，浏览器fonts=loaded。字体二进制完整字形覆盖未独立量测。桌面截图为1417×900、DOM viewport为1440×900，右侧余白截取差异已披露，不把它们当作完全同尺寸截图。

前稿实际操作有搜索切股不加关注、删除原槽撤销/清空恢复/明确加关注/市场已关注筛选、周期/MA、12行同源表、Tabs键盘、来源就地展开、提醒空态重置与通知关闭回焦。前稿原版detect单次exit2、12条原始warning见`bc-detector.json`，不是clean；不与旧三案166条排名或视作全部关闭。五部分兼容`bc-finish-review.md`及前稿`bc.js` Jev correctness7.4 unchanged只证明各自受检范围，不代替补选股后的实测或独立生产发布审核。

本轮新增单次detector为`bc-hunting-detector.json`，exit2、仍12条warning，不是clean。新兼容代理的五部分`bc-hunting-finish-review.md`为disposition ship，仅限选股入口、合成代表闭环及未接入说明可交付，不代表全功能、生产发布或原生完整流程。新`bc.js` Jev final correctness7.5/confidence0.37、reliability7.5，documentation为唯一improved、regressions空；CSS/字体修复后JS未改，不重复评分，分数不是独立产品验收。

§9.4、§9.5的24个观察、14次菜单展开及201项文档守卫保留为合并前三案/文档字节历史。本轮最终回执为`bc-acceptance.json`，本次文档门禁另记`bc-doc-validation.json`并绑定三文档运行前后哈希；LAN HTTP200/私有路径404见`bc-http-verification.json`，仅本机访问证据。用户选择已完成，选股入口和合成代表闭环已补，IMP-082继续进行中；完整真实API/业务、持久台账、持仓模拟、复盘及全生产功能尚未迁入，原业务代码未删，不能替代现用系统。真实手机、系统偏好/屏幕阅读器/全设备性能、完整外站TasteLab、完整ultramotion WebGL及全域闭包仍未关闭。生产迁移另待确认并逐子项验收，不重问三选，不宣称全部技能或全部功能完成。

## 附录A：逐技能原文职责与完整适用流程

本附录包含修订前20项问题的处置、33项技能及条件分支、24项Impeccable命令，以及原生路径与宿主适配的具体边界。A–G字母表示本附录的工作阶段，正文§6的“交付1–7”表示成果批次，两者不是任务编号。

### 结论

前案修正了两个原仓、Taste v2实验状态和dashboard排除，这是正确的。**但前案还不是完整的可执行技能流程。** 核心缺口为职责压缩、确认顺序、状态路径、独立评价/收尾产物及规则冲突。完整发挥应指“适用的原有能力得到完整输入、步骤、产物和门”，不等于把每个命令运行一遍。

下表20项问题针对修订前方案，前案行号只用于追溯；对应修订已纳入本版正文、完整流程和附录，不是本版仍有20项未解决问题。原始只读审计记录与机器可读记录保留在本机批次；本附录是并入共享方案的修订稿。技能/网站是否已运行与计划职责分开，原始技能规则未修改。

### 1. 确认缺口与直接修订

|ID|级别|问题与原文证据|前案位置|具体改法|
|---|---|---|---|---|
|SK-01|P1|**init上下文与原生状态路径尚未裁决**；`全局技能/impeccable/reference/init.md:7-9,56-108`；`upstream-impeccable/crates/context/src/context.rs:143-175,341-347`；`upstream-impeccable/crates/context/src/surface_briefs.rs:11-12`；`upstream-impeccable/crates/live/src/roots.rs:294-307,349-353`；`upstream-impeccable/crates/context/src/serve_question.rs:308-326,944`；隔离context-workspace仅是事实适配草稿；不能证明真实repo完成init。IMPECCABLE_CONTEXT_DIR真实存在，但只改产品/设计读取且只在project/repo均缺PRODUCT/DESIGN时生效；surface/questions/build/live/sidecar仍有固定.impeccable路径。|`docs/product/ui-original-skills-plan-20261008.md:19,38,57`|写明两条执行路径：真实repo使用已确认的忽略产物投影+原生env读取（先核实际解析），其余产物人工按原结构生成于artifacts，明确非full-native；完整native只在无凭据、固定版本、实际UI试件工程运行，核返回根均在试件后才开始。不得造--state-dir，不能把--target当状态重定位。|
|SK-02|P1|**shape选案与确认顺序不完整**；`全局技能/impeccable/reference/shape.md:39,43-59`；`全局技能/impeccable/reference/new-work.md:20,37-59,73-91`；当前先建议蓝银主案，承认未选案；但把原版世界/方向选择、必要同等完整度comp和brief确认都延后，不能称已按原版完成方案设计。|`docs/product/ui-original-skills-plan-20261008.md:38,55-61,95,142`|保留已知用户约束；执行真实世界/方向比较并呈现原生问板或有证据的fallback，shape返回含所选方向、范围、状态、布局、未知的确认brief；此刻停止，不写六块方向contract或产品代码。后续批准才持久化contract并构建。|
|SK-03|P1|**comp/code分支及首屏门未成为退出条件**；`全局技能/impeccable/reference/init.md:112-116`；`全局技能/impeccable/reference/new-work.md:45-59,89-120`；图像工具在当前harness存在，不能把生成效果图降为任意可跳过步骤；亦不能默认记录用户已选code。|`docs/product/ui-original-skills-plan-20261008.md:61,142-147`|单独记录buildPath选择；未答不得写持久默认。comp路线保留方向card同精度comp、prompt sidecar、三构图批准、spec/state机及首屏实屏diff；code路线记录FIRST VIEWPORT及标志性交互的等价雄心并由finish核验。|
|SK-04|P1|**Impeccable完整命令被一段职责摘要压缩**；`全局技能/impeccable/SKILL.md:41-86`；`全局技能/impeccable/reference/audit.md:1-9`；`全局技能/impeccable/reference/extract.md:7-9`；`全局技能/impeccable/reference/overdrive.md:12-20`；只列命令名，缺触发、产物、边界；容易把critique/audit混成一轮、把polish当重设计、把overdrive未确认就实现或全部命令机械运行。|`docs/product/ui-original-skills-plan-20261008.md:46,51,146`|附逐命令触发/独立产物/门/下游矩阵（本报告已提供），明确必经、条件、排除；保留read-only audit与两份独立critique。没有消费者就不运行。|
|SK-05|P1|**critique独立性、报告交付和收尾缺门**；`全局技能/impeccable/reference/critique.md:3-16,31-63,65-108,131,185,199-267`；没有规定独立UX评估A/AI痕迹检测B、非完整时DEGRADED、评分分母、聊天先交付、snapshot/trend和基于发现的提问；只留“分别检查”会再次被合并。|`docs/product/ui-original-skills-plan-20261008.md:46,146`|A/B使用隔离上下文与各自新鲜tab；A完成前detector不进入主评估。保留CLI/overlay/cleanup真实回执；先向用户输出完整报告，再存snapshot/read trend。≥3 Priority Issues时按原文问具体优先级/意图/范围，已有直接授权仍按高层规则避免重复许可；少于3记录Questions skipped及计数。|
|SK-06|P1|**finish/documenter身份与状态出口尚不可执行**；`全局技能/impeccable/reference/new-work.md:136-152`；`全局技能/impeccable/reference/document.md:43-75,251-255,342-346`；泛称finish reviewer/documenter，未规定新鲜无fork上下文、完整packet、recapture/rebuild/ship/fix处分和DESIGN的真实token+sidecar；当前harness无已验证的原生agent类型加载接口。|`docs/product/ui-original-skills-plan-20261008.md:146,163`|在独立子代理启动后读取原角色原文，按原packet执行并披露兼容代理身份，不能仅取名就称原生角色。先valid capture；fix有界一批，rebuild重审；新世界由documenter从实际建成系统抽取DESIGN与扩展sidecar，仅artifacts投影，不第二产品authority。|
|SK-07|P1|**finesse-brief核心数据底座被缩减**；`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-brief/skills/finesse-brief/SKILL.md:33-56,68-69,353-354,381-410,435-498`；只写目标用户/首屏/功能输入输出，丢掉既有定义修订、CADENCE/INPUT/DEPTH、主体和实体关系、L1-L3页面树、每句hook的数据来源、首日空态与断线态、dead-console检查。|`docs/product/ui-original-skills-plan-20261008.md:34`|基于现有产品闭环做revision，保留已排除/延后项；完整Workbench Spec覆盖主体/实体关系/页树/触发/数据字段/写者/更新频率/冷启动/失败/断线/下一步和实现交接；逐模块复用后端真实writer，不再重新问已知事实。|
|SK-08|P1|**finesse-term被错误放成一次性阶段**；`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-term/skills/finesse-term/SKILL.md:18-30,40-64,68-94`；原版是每轮描述的说法层，不能一次翻译完毕；每条术语必须命中用户原话和本地词库trigger。移动端成对陷阱未映射。|`docs/product/ui-original-skills-plan-20261008.md:35`|贯穿需求/修改/验收；保留原话分句，实际调用term.mjs或读取data触发词，不编造词典。记录bottom-fixed↔safe-area、overlay↔scroll-lock、input↔keyboard、image↔aspect、fixed-bars↔scroll-owner；最多向用户显示1–3必要术语，其余进映射。|
|SK-09|P1|**finesse-ui产品分支与完整流程未展开**；`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-ui/skills/finesse-ui/SKILL.md:28-56,128-154,156-213,306-321,336-349,476-510`；`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-ui/skills/finesse-ui/references/product-ui.md:13-20,24-38,64-75,100-144`；一行“视觉规则”没有按product/workflow/AI-console/component分路，缺product-palettes、实际例子、独立Design Read、三dial、分歧轴、四beats、状态预览、mobile-floor、preflight、build memory。|`docs/product/ui-original-skills-plan-20261008.md:41`|按本报告分层矩阵保留全部对应作用；各reference在其阶段阅读。product base先选中性ramp和语义色；真实提交任务叠workflow，真实异步agent叠ai-console；单控件后续精修走component而非全页apparatus。样式方向与Impeccable可共用一次用户选择，但原始产物/门不合并消失。|
|SK-10|P1|**finesse原包未安装却没有调用状态边界**；`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-brief/skills/finesse-brief/SKILL.md:1-17`；`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-term/skills/finesse-term/SKILL.md:1-12`；`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-ui/skills/finesse-ui/SKILL.md:1-24`；两个全局skill根未发现finesse-*安装；存在前轮原仓完整快照，不能从登记名推断本轮可调用原生命令。|`artifacts/runs/ui-original-skills-20261008/skills.json:02-03,09`|状态改为“原仓快照可读，当前未安装；本轮按原文人工流程审计/设计；需native时按获准installer完整安装并核版本”。原文/脚本位置明确，不创建误导性的替身入口。|
|SK-11|P1|**redesign中随机化真实数据建议必须明确否决**；`全局技能/redesign-existing-projects/SKILL.md:77-87,147-169`；原文建议organic/randomized numbers、inventbrands、randomizedates以显真实。项目禁止mock冒充实盘，方案没有明确冲突裁决；也不能把字体优先级放在提醒正确性之前。|`docs/product/ui-original-skills-plan-20261008.md:27,39`|仅在明确隔离并标注的设计fixture可用示例；股票、行情、日期、新闻、来源、账户、收益和进度都从真实事实或缺失状态取值。正确性/可靠提醒先行；保留其完整审查，不把审查数量伪装十个固定类别（原入口现有九个标题）。|
|SK-12|P2|**Taste v2适用排除已正确，但还需原流程边界**；`全局技能/design-taste-frontend/SKILL.md:8-9,13-36,43-104,783-833,896-979`；`artifacts/runs/ui-original-skills-20261008/sources/taste-skill-README.md:105,129-130`；v2实验和dashboard排除已正确；需防止把主技能设计dials/营销preflight借独立“anti-slop”名义重新强套到工作台。|`docs/product/ui-original-skills-plan-20261008.md:23,40,55`|真实工作台记录主技能不适用；如未来出现有真实消费者的展示页，独立执行brief inference→Design Read→dials→官方system map→redesign protocol→Preflight；当前不新造营销页。redesign-existing-projects另包照常使用。|
|SK-13|P2|**frontend-design独立构图产物被合并**；`全局技能/frontend-design/SKILL.md:13-36,47-59`；原文要求4–6named颜色hex、typeface角色、1句意图+ASCII比较构图、自查generic再修订；当前与finesse-ui共享一行没有独立交付。|`docs/product/ui-original-skills-plan-20261008.md:41`|给frontend-design独立输入和输出：任务相关构图对比、tokens草案、字体角色、一个可辨识机制、语义/布局约束；作为选案/组件规格输入。Operate首屏不是获客Hero。|
|SK-14|P2|**Interaction Design五维、十态及loops/modes缺失**；`全局技能/interaction-design/SKILL.md:47-61,358-426,483-516,543-555`；只有状态图/触摸/键盘，缺Words/Visual/Physical/Time/Behavior五维、全十态、Trigger→Rules→Feedback→Loops&Modes、认知/错误恢复和基于任务的可用性验证。|`docs/product/ui-original-skills-plan-20261008.md:36`|逐高价值任务输出全路径图、五维分析、十态表、微交互四部分、时序、回退/恢复/草稿/返回锚点；高频精确读数不追动画，非关键状态变化可有界过渡。|
|SK-15|P2|**UI/UX Pro Max不能仅做最后复核**；`全局技能/ui-ux-pro-max/SKILL.md:49-81,88-104,134-172`；全域新视觉需先--design-system，并按query contract/实际stack/目标UX搜索；只在末尾复核丢掉设计智能职责。--persist默认第二design-system文件树亦未裁决。|`docs/product/ui-original-skills-plan-20261008.md:42`|保留真实已跑搜索和拒绝记录；系统方向阶段先搜索，组件阶段按outcome→stack窄搜，结果不匹配一次窄化再fallback。默认不--persist；输出候选进ignored artifacts，正式设计仍归既有权威。|
|SK-16|P2|**TasteLab四阶段和可核度量不足**；`全局技能/tastelab-taste/SKILL.md:15-34,74-180,233-311`；仅截图+DOM→tokens/DNA，缺1440×900/分段截图约束、20类measure、5–8pattern、四步分析与至少一项Restraint、JSON/禁空泛质量门。|`docs/product/ui-original-skills-plan-20261008.md:37`|只对入选的明确原站按Measure→Pattern→Taste→Observer依次产出；每域md/json、截图和readonly DOM证据。截图缺失/DOM timeout就partial，不猜尺寸色值或称完整提炼。|
|SK-17|P1|**状态/图表核验的对象绑定与乱序边界被泛化**；`skills/ui-state-verify/SKILL.md:18-22,32-47,65-69`；`skills/canvas-chart-verify/SKILL.md:12-14,22-25,48-57,89,95-136`；列状态不等于核同对象/日期/版本/账户/source/returnanchor；缺follow/pin/compare、迟到响应、draft/scroll/focus恢复、主题实例/geometry/slots/idempotence，以及fixture实际envelope与unroute→route→fetch probe。|`docs/product/ui-original-skills-plan-20261008.md:47,149-151`|按对象×状态×输入×viewport建立证据格。Canvas分DOM、行为、实例、pixel、同版数值五层；深浅深主题不重建实例、不改变几何/金融数值；实源与fixture分开；每次注入先探针确认消费契约，finally清理。|
|SK-18|P2|**governor缺功能组合的具体决策输出**；`skills/living-system-governor/SKILL.md:14-17,21-31`；用途表不足以支持移模块/藏后台/合页面；未明确导航/业务owner/componentview/ledger四边界与KEEP/FIX/MERGE/EXPERIMENT/WATCH/RETIRE逐消费者决策。|`docs/product/ui-original-skills-plan-20261008.md:33,72`|复用既有登记册逐项记录owner、真实consumer、writer、触发/幂等、失败重试/恢复、权限/预算、成本收益/反例、退出；隐藏前端不等于后台承接；不新建第二总registry。|
|SK-19|P2|**通用规则相互冲突未逐项裁决**；`全局技能/impeccable/reference/operate.md:41-52`；`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-ui/skills/finesse-ui/references/product-ui.md:46-58,64-75,92`；`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-ui/skills/finesse-ui/references/mobile-floor.md:13-27`；`全局技能/redesign-existing-projects/SKILL.md:147-154`；`全局技能/ui-ux-pro-max/SKILL.md:22-28`；禁止全页编排vs入场stagger/从0计数；topnavvs默认sidebar；无横滚vs用户要求表格容器横滚； compact控件vs44touch；clip/hide根溢出vs不得掩盖缺陷，尚无逐条可执行裁决。|`docs/product/ui-original-skills-plan-20261008.md:26,79,91,109`|新增冲突表：真实金融最终值即时；低频摘要有界滚动；topnav按user；局部table有唯一horizontalowner；可见小控件命中44并在容器内；修布局根因后才做外层clip保护；所有状态仍可访问。|
|SK-20|P2|**逐技能独立产物还需要绑定消费者与未跑状态**；`全局技能/impeccable/SKILL.md:10-13,17-26`；`全局技能/ui-ux-pro-max/SKILL.md:61-63`；27行静态登记多为同一“后续执行”，不足以证明已发挥作用；完成定义必须是当前需求下可取得证据，不能要求无消费者的命令全部执行。|`artifacts/runs/ui-original-skills-20261008/skills.json:all`|每技能/条件分支列输入、具体步骤、独立产物、退出门、下游、excluded/未跑/人工兼容/原生等状态。全部评估不等于全部安装/命令跑遍。当前报告是审计与规划，未修UI。|

### 2. 技能与条件分支的独立输入、步骤、产物、门和下游

以下是可直接并入最终方案的执行矩阵。未注明已跑的均是计划，不能称已完成。finesse三项目前是已获取的原仓快照，不是已安装的全局native技能。

#### 1. living-system-governor — A及全过程

- 原文：`skills/living-system-governor/SKILL.md:14-31`。
- 输入：用户完整要求；现有产品闭环/消费者/owner与已知事故。
- 完整步骤：确认目标/消费者→比较现状、最小修补、替代→逐四边界裁决KEEP/FIX/MERGE/EXPERIMENT/WATCH/RETIRE→限定证据/成本/恢复→U49反证。
- 独立产物：决策卡：对象/owner/consumer/writer/基线/方案/反例/成本/恢复/证据取得。
- 退出门：前台简化不丢业务闭环，后台承接经证据证明；未知有边界。
- 下游：brief修订、功能组合、准入与发布。
- 边界/不适用：不替代阶段账本/权限/风险/项目发布门；不第二总registry。
- 状态：`planned_not_executed`。

#### 2. finesse-brief — A-B定义

- 原文：`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-brief/skills/finesse-brief/SKILL.md:33-56,435-498`。
- 输入：已有产品定义与真实接口/实体/权限/角色/工作周期，当前要求。
- 完整步骤：先read-only修订已有定义并保留exclude/defer→CADENCE/INPUT/DEPTH→主体/实体/关系→L1-L3页树→identity/hook/data-floor/modules/seams顺序→数据底座→cold-start/disconnect/error→交接。
- 独立产物：完整Workbench Spec：每句首屏claim绑定field/writer/cadence/首日空态/断线fallback；页树、实体图、模块责任及实现方说明。
- 退出门：dead-console blacklist过关；没有虚构数据、没有未承接的空开关；现实writer清楚。
- 下游：PRODUCT事实确认、IxD、页面组合、finesse-ui。
- 边界/不适用：当前未全局安装；原仓快照人工完整阅读执行，不称已native调用。原版.web无push假设不能覆盖真实backend scheduler/notification。
- 状态：`planned_not_executed`。

#### 3. finesse-term — 每轮UI描述持续

- 原文：`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-term/skills/finesse-term/SKILL.md:18-30,40-94`。
- 输入：用户原话分句与目标web/mobile surface。
- 完整步骤：按原话运行term.mjs find或读取data触发→命中记录→paired traps→翻成可观察行为→结果先行/最多1–3必要术语。
- 独立产物：原话/命中trigger/词库条目/语义/触发/状态/退出/PC-phone差异表。
- 退出门：每术语有真实命中证据；没有命中正常，不编词库；不自行挑功能/美学。
- 下游：IxD状态与组件规格/后续修改验收。
- 边界/不适用：不是一次性前置阶段；不向用户倾倒术语；未安装状态明确。
- 状态：`planned_not_executed`。

#### 4. interaction-design — B任务流至G行为验收

- 原文：`全局技能/interaction-design/SKILL.md:47-61,358-426,483-555`。
- 输入：Workbench Spec、对象/事件/账户边界、误操作与恢复条件。
- 完整步骤：5维→Norman/Tognazzini适用项→端到端entry/decision/success/recovery/resume→默认/hover/focus/pressed/loading/disabled/error/success/empty/skeleton十态→Trigger/Rules/Feedback/Loops&Modes→时序/easing→非显然交互原型→任务测试。
- 独立产物：任务流图、十态表、微交互契约、恢复/草稿/返回规则、键盘/触摸等价路径。
- 退出门：正常/失败/未知/中断均可完成或恢复；反馈及时；服务器确认前不伪造金融完成。
- 下游：方向、组件规格、实现、状态测试。
- 边界/不适用：高置信optimistic普通低风险才可用；不把语义模型/动效当权限或执行确认。
- 状态：`planned_not_executed`。

#### 5. TasteLab / tastelab-taste — C原站提炼

- 原文：`全局技能/tastelab-taste/SKILL.md:15-34,74-180,233-311`。
- 输入：明确原站URL、截图许可、已选参考用途。
- 完整步骤：现有cua public fresh tab→1440×900 screenshot/全页或分段→完整只读extract脚本与DOM量测→Measure20类→Pattern5–8→Taste4步含Restraint→Observer JSON/Design Map。
- 独立产物：每域md/json、截图、computed尺寸色值、Pattern/Evidence/Goal、Trigger/Decision/Reason/Evidence、迁移取舍。
- 退出门：真实截图与DOM齐备；JSON可parse、两节标题与禁空泛检查；不足partial。
- 下游：组件/色彩/布局比较，原版方向素材。
- 边界/不适用：不把senlindesign TasteLab当Leonxlnx Taste v2；不装第二浏览器；不写app私有状态目录。
- 状态：`planned_not_executed`。

#### 6. Impeccable context/init — A-B事实

- 原文：`全局技能/impeccable/SKILL.md:17-21;reference/init.md:7-9,19-54,56-118`。
- 输入：实际web target、canonical产品文档、已确认用户事实。
- 完整步骤：cwd/target context一次→核projectRoot/contextDir与缺口→按现有事实扫描/最多必要提问→真实一轮确认→PRODUCT schema写confirmed/open→单独buildPath问题→可选live配置。
- 独立产物：已确认PRODUCT投影、来源/owner/digest/未知；context根与buildPath选择回执。
- 退出门：init完成必须在实际解析PRODUCT路径有确认记录；草稿packet不替代。
- 下游：shape/new-work与判断产品真相。
- 边界/不适用：真实repo env只读重定位有限；固定state路径不能原生跑时声明兼容；不得重复context或虚构flags。
- 状态：`planned_not_executed`。

#### 7. Impeccable shape/new-work — D方向与确认

- 原文：`全局技能/impeccable/reference/shape.md:11-59;reference/new-work.md:20-91`。
- 输入：confirmedPRODUCT、Operate/Read mode、原站证据、incumbent与完整要求。
- 完整步骤：跳过已知问法但确认任务→适用surface世界→7方向≥3families→原版方向seed→challenger比对/raised→问板与comp/code分支→所选方向写回shape brief→停；批准后才6块contract及实现。
- 独立产物：候选card/board/comp+sidecar、原版brief七部分的全部字段、用户选择；批准后direction contract。
- 退出门：shape确认前不contract/不code；seed不是批准；comp/code不能静默降级。
- 下游：frontend/finesse/UIUX最终规格与实施。
- 边界/不适用：Operate/Read规范控制文化素材，不能变营销/科幻仪器；原生状态路径按两层裁定。
- 状态：`planned_not_executed`。

#### 8. redesign-existing-projects — B现状审查，E实施

- 原文：`全局技能/redesign-existing-projects/SKILL.md:8-130,159-178`。
- 输入：当前真实框架/样式/渲染/组件、历史重复问题。
- 完整步骤：Scan→逐Typography/ColorSurfaces/Layout/InteractivityStates/Content/ComponentPatterns/Iconography/CodeQuality/StrategicOmissions Diagnose→依据用户全换表现而保持stack/contracts→Fix→再核同类。
- 独立产物：逐现象位置/严重度/证据/根因/建议/同族影响；扫描原标题九组，而非机械固定十组。
- 退出门：不更改业务事实/交易规则；所有可见操作有完整状态；动效/布局不退化。
- 下游：选案、共享组件族、全域迁移与复查。
- 边界/不适用：禁止原文organic/randomized dates/numbers/brands用于真实生产；正确性优先于原通用字体先行建议。
- 状态：`planned_not_executed`。

#### 9. design-taste-frontend v2 experimental — 仅真实适用展示表面

- 原文：`全局技能/design-taste-frontend/SKILL.md:8-9,13-104,783-833,896-979`。
- 输入：有真实消费者的landing/portfolio/showcase brief。
- 完整步骤：scope判定→brief inference→Design Read→独立DESIGN_VARIANCE/MOTION_INTENSITY/VISUAL_DENSITY→system map/已有stack→redesign→完整Preflight。
- 独立产物：适用性记录；如适用则独立Design Read/dials/system choice/preflight。
- 退出门：当前密集工作台排除；未来适用表面才可交付其全流程。
- 下游：适用展示表面。
- 边界/不适用：当前不适用：dashboard/data tables/dense admin/multistep UI；不为用skill造营销页。
- 状态：`scope_reviewed_excluded_on_current_workbench`。

#### 10. frontend-design — D构图/视觉规则

- 原文：`全局技能/frontend-design/SKILL.md:13-36,47-59`。
- 输入：真实任务、内容层级、候选world与现有约束。
- 完整步骤：给具体视角→4–6named hex tokens/type roles→1句意图+ASCII构图比较→generic self-check改→build后critique。
- 独立产物：独立构图对比、token草案、字形角色、记忆点和修订理由。
- 退出门：构图从主任务出发；主次足够明确；不套营销Hero；同类generic被指出并改。
- 下游：shape选案及finesse完整规格。
- 边界/不适用：不将所有设计职责合并为finesse一行；中文/金融数字选择实屏验证。
- 状态：`planned_not_executed`。

#### 11. finesse-ui主流程 — D-E视觉系统

- 原文：`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-ui/skills/finesse-ui/SKILL.md:28-56,128-213,306-349,476-510`。
- 输入：Workbench Spec、参考Design Maps、当前rotation证据、已知用户偏好。
- 完整步骤：先scope→product register→Design Read(坐标/You will see/Images/两异议/rotation)→SOUL/SPECTACLE/DENSITY→产品palette与base→实际任务叠分支→motion路线/四beats→skeleton→anti-cheap/mobile/preflight→建成后日志。
- 独立产物：原版Design Read/dials、五轴差异、四beats与静止形态、各分支规格、建成memory。
- 退出门：选择需用户可否决；已有确认不重复；真实native工具与素材是否可用明确；产物不进入私有authority。
- 下游：组件族/实现/全域验收。
- 边界/不适用：未安装，仅完整原快照人工流程；.finesse/.workbench状态按项目规则转为ignored派生证据且披露非原生。
- 状态：`planned_not_executed`。

#### 12. finesse-ui product base + palettes + examples — D-E所有工作台基础

- 原文：`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-ui/skills/finesse-ui/SKILL.md:37-40,128-136;references/product-ui.md:13-38,64-75,100-144`。
- 输入：真实数据密度、涨跌语义、用户深色偏好、中文阅读与图表需求。
- 完整步骤：先product-palettes中性ramp+功能accent+semantic分离→阅读/操作模式→最贴近example真实源码→shell和密度→dataviz问题/表类型→完整组件族与状态。
- 独立产物：中性/semantic/action/focus/selection tokens、shell/密度/图表/组件inventory/移动语义。
- 退出门：红涨绿跌；topnav；普通文本不折射；图表plot有实际高度宽度；unknown不伪造。
- 下游：workflow/AI组件层、tokens、全域迁移。
- 边界/不适用：不用brand grain/vignette/giant hero；不让例子的sidebar或绿涨红跌覆盖用户/项目；从0金融计数/表格禁止横滚规则不照搬。
- 状态：`planned_not_executed`。

#### 13. finesse-ui workflow layer — D-E真实提交/配置流程

- 原文：`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-ui/skills/finesse-ui/references/workflow-ui.md:1-180`。
- 输入：存在实际draft/commit/权限/错误/后置确认的操作。
- 完整步骤：workflow shell→numbered sections/radio-card→live preview→pre-submit check→derived totals→draft/commit/undo→workflow preflight。
- 独立产物：操作步骤、预览/总计真值、提交前核对、dirty草稿、服务端结果/失败恢复。
- 退出门：权限及金融提交硬门保留；未证实cancel只能pending；配置不靠藏UI假后台化。
- 下游：参数/维护/模拟操作等真实消费者。
- 边界/不适用：普通搜索过滤不强加workflow；没有form example就按reference不强套dashboard截图。
- 状态：`planned_not_executed`。

#### 14. finesse-ui AI-console layer — D-E真实异步AI任务

- 原文：`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-ui/skills/finesse-ui/references/ai-console.md:30-51,67-196,220-292`。
- 输入：真实agent task ID、排队/运行/结果/取消/费用/审批权限接口。
- 完整步骤：三时态→run stream→九态→resident stop→in-stream approval→cost/context receipts→artifact→phone shell→trust preflight。
- 独立产物：任务状态语义、run stream、停止/审批/恢复、费用未知/确认receipt、结果来源。
- 退出门：实际API与权限能力匹配；终止不假kill后台；不得用漂亮百分比假progress。
- 下游：仅真实agent任务视图。
- 边界/不适用：AI解释静态卡/规则提醒不因AI字样变agent console；不虚造新增能力。
- 状态：`planned_not_executed`。

#### 15. finesse-ui component-scope — E后续单控件精修

- 原文：`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-ui/skills/finesse-ui/references/component-scope.md:11-58,58-132`。
- 输入：单控件缺陷/既有tokens与原版scope触发。
- 完整步骤：两信号判scope→保留register/craft/tokens→跳page skeleton/hero/rotation→八态→实现+可视preview→handoff。
- 独立产物：component实现及预览两文件（若现有storybook则复用）；八态实屏/输入行为。
- 退出门：对所有同族消费者检索；小修不重新滚全页方向；视觉/keyboard/disabled/loading/error等真实可见。
- 下游：共享Button/Dialog/Select等微修。
- 边界/不适用：不能用单控件修复重新换全页世界；不新增无消费者demo产品入口。
- 状态：`planned_not_executed`。

#### 16. finesse-ui divergence/motion/theming/mobile/preflight — D至G跨层规则

- 原文：`artifacts/runs/ui-skill-redesign-20261007/sources/finesse-ui/skills/finesse-ui/SKILL.md:47-56,476-510;references/divergence.md:11-35,58-81,131-141,207-254;references/motion.md:18-62,158-254;references/preflight.md:7-186;references/mobile-floor.md:13-125`。
- 输入：rotation/现有变体、目标交互及设备、主题真实消费者。
- 完整步骤：product bounded五轴→必要三真实visual不同轴→motion效果/路线六选一/十家族/4beats/仍态→主题role map若可切换→mobile320/375/414/768→promise/render首先preflight→记录build。
- 独立产物：差异轴、effect-route-budget与still/reduced表、主题角色、六mobile缺陷检查、preflight实证与buildreceipt。
- 退出门：不得仅自评；Promise kept和renders先；关键数值即时；CSS/JS reduced一致；RM/透明fallback能用。
- 下游：实现及finish packet。
- 边界/不适用：phone-only H5才fixed frame/notch专门路径；现有响应web不强390框；root clip不代替修越界；只有改造后的建成版本可写完成memory。
- 状态：`planned_not_executed`。

#### 17. UI/UX Pro Max — C-D比选，E-G检查

- 原文：`全局技能/ui-ux-pro-max/SKILL.md:49-104,134-172`。
- 输入：实际React/Next stack、单一查询意图、用户深色金融工作台场景。
- 完整步骤：system级--design-system→检查fit→一次窄化retry→outcome targeted-domain→actual-stack→adopt/reject→fullprioritized UX checks。
- 独立产物：搜索参数/原始结果/采纳拒绝理由；contrast/touch/layout/forms/nav/charts查验。
- 退出门：结果不是事实或默认胜者；不匹配明确拒绝；不--persist创建第二authority。
- 下游：方向/组件/性能与可用性。
- 边界/不适用：本轮已跑3搜索+1重试，由root保留原产物；不重复运行。拒绝Feature-Rich Showcase/浅色绿CTA/Inter；保留Financial Dashboard适用条目。
- 状态：`root_current_run_searches_completed_partial_adoption`。

#### 18. ultramotion / liquid-glass — C参考→E隔离材质试件

- 原文：`artifacts/runs/ui-plan-audit-20261008/reference-sources/sunsiyuan--ultramotion/skills/liquid-glass/SKILL.md:3-17,23-29,33-69,71-98`。
- 输入：固定原仓scene/score源码、真实web材质需求。
- 完整步骤：读视频场景原文→观察pop/stretch/merge/lens层A/B→复制隔离试件→与CSS/SVG/网页组件同尺寸对照→实际browser背景移动/像素位移→RM/实底降级。
- 独立产物：网页材质试件、真实refraction或近似标签、性能/兼容/失败对照；若视频另有render/contactsheet。
- 退出门：没有视频生成需求不移植渲染/声轨流程到React生产；字层与折射分离；背景DOM与内部shader纹理差异说明。
- 下游：选中导航/search/drawer材质。
- 边界/不适用：当前不是全局installed skill；是原版视频风格参考，不是React组件库；不自动声音/持续背景/全页折射。
- 状态：`planned_not_executed`。

#### 19. GSAP core — E实现及F性能

- 原文：`全局技能/gsap-skills/skills/gsap-core/SKILL.md`。
- 输入：已确认微交互状态与duration/ease预算。
- 完整步骤：to/from/fromTo/set语义→overwrite与打断→matchMedia响应/RM→终态。
- 独立产物：每动画trigger/start/end/cancel/overwrite/media契约。
- 退出门：default可见；快速重复不排队过期动画；reduced到仍可理解终态。
- 下游：真实组件实现及cleanup/perf验收。
- 边界/不适用：不为简单CSS引库；无消费者不用API。
- 状态：`planned_conditional`。

#### 20. GSAP React — E实现及F性能

- 原文：`全局技能/gsap-skills/skills/gsap-react/SKILL.md`。
- 输入：实际Next client边界与真实refs。
- 完整步骤：useGSAP/ref scope→contextSafe deferred callback→event cleanup→revertOnUpdate按依赖→SSR安全。
- 独立产物：卸载/重路由/StrictMode与callback清理证据。
- 退出门：无卸载后更新、无跨容器selector、SSR不执行动画。
- 下游：真实组件实现及cleanup/perf验收。
- 边界/不适用：不把全部组件改client；动态指针不state重render。
- 状态：`planned_conditional`。

#### 21. GSAP timeline — E实现及F性能

- 原文：`全局技能/gsap-skills/skills/gsap-timeline/SKILL.md`。
- 输入：加载/成功/结果、开关/收回时序。
- 完整步骤：label与relative position编排→pause/reverse/seek按当前状态→取消旧序列。
- 独立产物：同位置提交/原位展开sequence、entry/exit时序。
- 退出门：没有视觉上成功先于服务器结果；打断后终态正确。
- 下游：真实组件实现及cleanup/perf验收。
- 边界/不适用：不依赖任意delay链或加载美化假等待。
- 状态：`planned_conditional`。

#### 22. GSAP plugins — E实现及F性能

- 原文：`全局技能/gsap-skills/skills/gsap-plugins/SKILL.md`。
- 输入：真实重排/手势/路径消费者。
- 完整步骤：核版本/plugin登记→Flip前测量与DOM后状态→Draggable/Observer仅真实手势→cleanup。
- 独立产物：plugin→consumer→效益→cost/fallback/退出表；同节点同identity重排。
- 退出门：列表→网格/筛选保语义与身份；touch不抢scroll；许可/包成本真实。
- 下游：真实组件实现及cleanup/perf验收。
- 边界/不适用：不是所有433行API都用；ScrollSmoother不接管嵌套桌面壳。
- 状态：`planned_conditional`。

#### 23. GSAP utils — E实现及F性能

- 原文：`全局技能/gsap-skills/skills/gsap-utils/SKILL.md`。
- 输入：几何/drag/吸附区间。
- 完整步骤：clamp/mapRange/normalize/snap/wrap按真实范围→零尺寸/越界/速度边界。
- 独立产物：映射函数边界与gesture anchor表。
- 退出门：输入输出明确；time/金融数值/风控仍确定性业务代码。
- 下游：真实组件实现及cleanup/perf验收。
- 边界/不适用：不能把random用于行情/日期/事实。
- 状态：`planned_conditional`。

#### 24. GSAP performance — E实现及F性能

- 原文：`全局技能/gsap-skills/skills/gsap-performance/SKILL.md`。
- 输入：同机/窗/数据/动画中和静止baseline。
- 完整步骤：transform/opacity优先→batch read/write→quickTo频更新→可见范围/virtualize→killhidden/unmount→measure。
- 独立产物：帧/longtask/layout/memory/interaction before-after。
- 退出门：有限临时will-change；无持续重渲染；图表操作不被overlay拖慢。
- 下游：真实组件实现及cleanup/perf验收。
- 边界/不适用：不承诺未测60fps；滤镜/大blur必须按实测预算。
- 状态：`planned_conditional`。

#### 25. GSAP ScrollTrigger — E实现及F性能

- 原文：`全局技能/gsap-skills/skills/gsap-scrolltrigger/SKILL.md`。
- 输入：有真实需求的唯一容器scrollowner。
- 完整步骤：scroller明示→resize/content变化refresh→matchMedia/cleanup→避免pin冲突。
- 独立产物：scrollowner、progress绑定、refresh/kill测证。
- 退出门：只滚目标容器；没有双惯性、键盘/触摸陷阱。
- 下游：真实组件实现及cleanup/perf验收。
- 边界/不适用：无真实滚动叙事不采用；Lenis不统一接管多容器。
- 状态：`planned_conditional`。

#### 26. GSAP frameworks — E实现及F性能

- 原文：`全局技能/gsap-skills/skills/gsap-frameworks/SKILL.md`。
- 输入：项目实际framework。
- 完整步骤：确认React/Next，选择React skill。
- 独立产物：不适用回执。
- 退出门：不将Vue/Svelte生命周期套React。
- 下游：真实组件实现及cleanup/perf验收。
- 边界/不适用：当前排除；未来真实Vue/Svelte消费者才启用。
- 状态：`excluded_current_react_project`。

#### 27. ui-state-verify — E-F逐状态

- 原文：`skills/ui-state-verify/SKILL.md:18-22,32-47,65-69`。
- 输入：当前路由/对象ID/日期/版本/账户/source/returnanchor与stub契约。
- 完整步骤：unroute旧→route新→fetch probe actual envelope→loading/ready/valid&partialempty/error/stale/unknown→乱序/retry/cancel→follow/pin/compare→draft/scroll/focusrestore→keyboard/IME/zoom/CSS+JSRM→finally清理。
- 独立产物：DOM/行为/截图/请求顺序/对象版本绑定证据格。
- 退出门：不以页面总览替子功能；局部失败不清整页；重复/迟到不会回退身份。
- 下游：technical audit/fresh finish review。
- 边界/不适用：fixture只证行为边界，实源真实性另证。
- 状态：`planned_not_executed`。

#### 28. canvas-chart-verify — E-F图表

- 原文：`skills/canvas-chart-verify/SKILL.md:12-14,22-25,48-57,89,95-136`。
- 输入：真实chart instance、canvas layers/DPR、同对象同版源数据。
- 完整步骤：DOMlayout→行为/focus→实例与sequence→像素真画→同版金融数值→theme实例/geometry/color slots/dark-light-dark→latepayload/stale/follow-pin-compare→fixture actual envelope probe。
- 独立产物：五层证据、绘图区实际高宽、主/overlay canvas、theme稳定性、源数值对账。
- 退出门：pixel仅证绘制非事实准确；单位与复权基准不混；history无未来数据。
- 下游：finish/performance/图册。
- 边界/不适用：不靠DOM文本或fetch裸数据绕app解析；不可自动装第二browser。
- 状态：`planned_not_executed`。

#### 29. U49主动缺陷发现 — A/E/F/G有界反证

- 原文：`AGENTS.md:§3；skills/living-system-governor/SKILL.md:31`。
- 输入：当前切片及直接上下游、已重复用户缺陷。
- 完整步骤：同类关闭/删除/通知/搜索/tab/shared family→自锁/双源/顺序部分失败/幂等unknown/权限failopen/动态冻结/坏测试/旧指针→分级回owner→修复验证。
- 独立产物：发现/证据/根因/同族消费者/整改/未覆风险回执。
- 退出门：不是只改点名处；不是无限全仓研究；阶段切换按范围扩展。
- 下游：后续合法任务/发布receipt。
- 边界/不适用：作者自检不能叫独立review。
- 状态：`planned_not_executed`。

#### 30. ASD-STE100 — 贯穿说明与微文案

- 原文：`全局技能/asd-ste100/SKILL.md:3,13-30,32-36`。
- 输入：高误读成本英文或中文项目表达约定。
- 完整步骤：英文选Strict/STE-flavored并声明→一句一主要动作/术语一致；中文借简明原则不称英文标准认证。
- 独立产物：用户结论先行短摘要+具体依据/未知/恢复；必要英文无歧义串。
- 退出门：不删关键条件和未知；不把设计品牌声调改成技术维护文。
- 下游：用户方案/错误/状态/交付。
- 边界/不适用：不为品牌创意文案强套STE；中文不是ASD-STE100正式符合性。
- 状态：`planned_not_executed`。

#### 31. TypeSafe/Jev — 按消费者考虑

- 原文：`全局技能/typesafe-ai/SKILL.md:26-44,55-60,95-149`。
- 输入：既有获准语义adapter/预算与真实consumer。
- 完整步骤：先适用性→读取live文档索引；实际集成再读对应API/cookbook→仅bounded语义Choice/Noul/Score→unknown/escalation→独立gold验证。
- 独立产物：采用/不采用说明；如使用则state/question/model/cost/latency/uncertainty/consumer证据。
- 退出门：不把概率当正确率/涨率/权限；金融硬规则留代码；当前规划不新增生产调用。
- 下游：既有受控语义模块或完全不调用。
- 边界/不适用：无真实consumer不制造调用；确定性像素/时间/数值/风控不用模型。
- 状态：`planned_not_executed`。

#### 32. Jev Review — E实现后及G交付

- 原文：`全局插件/cache/plugins-cli/jev-review/0.1.1/skills/jev-review/SKILL.md:29-67,86-99`。
- 输入：经过验证的coherent非简单代码slice、当前准确diff与必要上下文。
- 完整步骤：implement→validate→baseline→弱维真实假设→最小有据改进→validate→previousEvaluation原样rescore→停于无正当改进。
- 独立产物：绑定准确diff baseline/final分数/置信/实测/可用性缺口。
- 退出门：final对应交付版本；分数不替代功能/发布门；不追分架构化。
- 下游：交付作者证据。
- 边界/不适用：纯规划/格式不触发；不可用如实记缺口，不假评分。
- 状态：`planned_not_executed`。

#### 33. Jev Browser — C研究/F行为验收条件

- 原文：`全局技能/jev-browser/SKILL.md:8-23,32-42`。
- 输入：实际观测DOM/ARIA与本机获准runtime。
- 完整步骤：适用性判断→从最新observed actions选→未知/低置信升级→最终独立页面验证。
- 独立产物：浏览方法选择、目标达成证据与缺口、成本实测。
- 退出门：DONE非验收；secret/private data不外发；Canvas/复杂popup回正常CUA。
- 下游：原站浏览与适用DOM流程。
- 边界/不适用：不是像素视觉裁判，不为省额度强用，不真实券商操作。
- 状态：`planned_not_executed`。

### 3. Impeccable全部原版命令的用途与门

共24项（含已弃用craft别名）；不是24个都必须执行。doctor/hooks/pin等支持入口另列，不能为了“全部”改变环境。

|命令|真正触发|独立产物|边界/门|下游及本次取舍|原文|
|---|---|---|---|---|---|
|craft|旧别名请求|等价new-work路由回执|已弃用，不作额外能力/额外执行次数|new-work；`deprecated_alias_do_not_schedule`|`全局技能/impeccable/reference/new-work.md:3-20`|
|init|缺确认PRODUCT/产品事实实质变化|已确认schema1 PRODUCT+open未知+来源|真实一轮答案/批准；resolvedpath完成；buildPath另问；不写visual rules|shape/new-work；`required_when_PRODUCT_missing`|`全局技能/impeccable/reference/init.md:7-9,27-31,56-118`|
|shape|要求先方案或未形成任务brief|job/audience/mode/outcome-proof/selecteddirection/scope/states/layout/constraints的confirmed brief|通过new-work完成所选方向后返回；用户确认；停在contract/code前|批准后的new-work；`required_current_plan_confirmation`|`全局技能/impeccable/reference/shape.md:11-59`|
|document|建成新世界/现DESIGN陈旧/需基线记录|从真实实现抽tokens的DESIGN frontmatter+八节正文+扩展sidecar|既有DESIGN不静默覆盖；newworld在finish写；seed无虚构tokens；状态路径披露|future design/detector/live；`required_finish_new_world`|`全局技能/impeccable/reference/document.md:43-75,251-255,342-346,350-383`|
|extract|真实重复token/primitive需设计系统化|组件/token/文档迁移计划+实际提取|先现DS，缺DS先确认位置；不抽一次性/无consumer/过度通用|复用组件族；`conditional_repetition_evidence`|`全局技能/impeccable/reference/extract.md:7-24,36-69`|
|critique|稳定表面需UX/设计诊断|独立A Nielsen&journey&personas +B detector，综合优先级、runnotes、snapshot/trend、用户问题|两独立评估；技术失败DEGRADED；报告先聊天；>=3 priority按原文具体问；actual denominator|修复命令/方案/优先级；`required_baseline_and_stable_redesign`|`全局技能/impeccable/reference/critique.md:3-16,31-108,131,199-267`|
|audit|需技术可测质量审查|A11y/Performance/Theming/Responsive/ImplementationIntegrity五维0–4=20的只读报告|不修代码；实测位置/严重度/建议；不当设计critique|原因对应的整改；`required_technical_assessment`|`全局技能/impeccable/reference/audit.md:1-9,58-113,132-137`|
|polish|方向已成且需最终工艺精修|真实路径问题、最小根因token/primitive修正与复验|不是redesign；全路径不是一角；只能关闭它实际读到且确已解决snapshot|finish；`required_after_world_stable`|`全局技能/impeccable/reference/polish.md:3-20,37-49,71-105`|
|bolder|现有world过弱且scope允许|受限amplification及邻区/事实保留证据|open方向轮说bolder应reroll register；不靠堆效换新primitive|同世界表现；`conditional_no_default`|`全局技能/impeccable/reference/bolder.md:3-9,22-31`|
|quieter|当前噪音过多/层次冲突|减accent/卡框/动作同时保辨识|不删业务；generic neverelastic不覆盖用户有据物理反馈|视觉密度与稳定性；`conditional_noise_evidence`|`全局技能/impeccable/reference/quieter.md:7-9,75,90-97`|
|distill|信息/IA/操作无价值复杂|IA/视觉/布局/交互/内容/代码各需删除合并的理由+替代入口|简化障碍不删重要能力；复杂domain不能过度简化；下游consumer存在|功能组合与导航；`conditional_real_complexity`|`全局技能/impeccable/reference/distill.md:26,43-109`|
|harden|长文本/边界/失败/手势易断|CJK/RTL/数量/empty/error/network/validation/a11y/快速操作/中断矩阵|实机/仿真分开；所有边界不破滚动/功能；不模拟成功|状态验收；`required_boundaries`|`全局技能/impeccable/reference/harden.md:7-14,328-343`|
|onboard|初用/空态无法获得firstvalue|contextual firstvalue/empty WhatWhyHow/下一步与dismiss持久语义|可跳过不重复；不无故强tour；TTV不经真实用户不称提升|空态与首次使用；`conditional_first_use_or_empty_gap`|`全局技能/impeccable/reference/onboard.md:3,37-48,168,211,225-234`|
|animate|动作/状态/continuity需motion|motionthesis focal/continuity/feedback/budget+effect duration/still/RM/cleanup|Operate不用page-load编排；不能延风险/假数值；hidden stop；exit自然；一元素一owner|真实交互实现；`required_selected_feedback_only`|`全局技能/impeccable/reference/animate.md:9-10,27-48,56-77`|
|colorize|色彩role/contrast/semantics问题|canvas/text/action/focus/selection/border/semantic/data palette及contrast记录|新identity走new-work；保红涨绿跌；4.5正文3大字/控件，非颜色唯一编码|tokens与实屏；`required_new_palette_and_contrast`|`全局技能/impeccable/reference/colorize.md:3,23,27-65`|
|typeset|排版缺陷为实际原因|独立typeassessment+detect --scope type；role/scale/measure/perf证据|computed font与语言验证；200%font；CJK/金融tabular；不为跑命令重测已足证内容|type tokens及修正；`conditional_or_required_typography_new_system`|`全局技能/impeccable/reference/typeset.md:8,13-30,36-68`|
|layout|布局主次/拓扑/密度问题|独立layoutassessment+detect --scope layout；spatial thesis/responsive/extremes|variation非目标；readingorder、滚动/触点、containment；所有检查逐证据|任务布局与容器规范；`required_root_layout_cause`|`全局技能/impeccable/reference/layout.md:8,13-43,49-72`|
|delight|关键结果有真实情感/可用收益|单delight thesis、频率/场景/keyboard-touch预算|普通点击不庆祝；不fakeprogress不延等待；重复使用不扰读数|有限signature feedback；`conditional_real_user_value`|`全局技能/impeccable/reference/delight.md:23-25,40,59-68`|
|overdrive|一个核心效果值得较高工程成本|3–4可行方向、成本/兼容/降级选择与真实原型|用户选后code；一个核心moment；progressive enhancement；sound optin；预算/设备/removaltests|有据core material试件；`conditional_requires_specific_choice`|`全局技能/impeccable/reference/overdrive.md:12-24,86,116-120`|
|clarify|copy使对象/动作/状态不可理解|整路径microcopy、term glossary、errorcause/nextaction|事实/法律/域词改动先有依据；unknown不能编cause；长文/zoom/accessiblename|说明/错误/提醒可读性；`required_current_rule_AI_unavailable_copy`|`全局技能/impeccable/reference/clarify.md:3-18,53,78-89`|
|adapt|新设备/输入/viewport/context|结构重排及输入/安全区/键盘/实机或emulation证据|移动web仍web；不强phone-only框；resize非gesture证明；缺硬件实证声明|PC/phone全域；`required_mobile_and_PC`|`全局技能/impeccable/reference/adapt.md:3-5,179-195,213-243,304-314`|
|optimize|实测瓶颈或新motion性能风险|CWV/bundle/runtime/network before-after与functionalcheck|先baseline；同条件；有限滤镜可保若收益预算证实；不宣称未测speedup|性能准入；`required_measure_new_material`|`全局技能/impeccable/reference/optimize.md:1-20,112,247-256`|
|live|局部variant需浏览器实际试验|rootsmanifest/session/identity/scoped preview/accept-discard/recovery清理|本地checkout；不生产注入不弱CSP；variants pending不当已实现；source state固定native仅isolatedworkspace|generate或手动变体接受；`conditional_local_trial_only`|`全局技能/impeccable/reference/live.md:3-21,62,307-323`|
|generate|用户指定局部元素方向/数量的live变体|1–8variants+choice/accept/discard与清理回执|这条fastlane不得init/document；原生answer不能猜；不kill/restartdevserver；unknown direction一问|被明确选择的局部code change；`conditional_not_whole_redesign_route`|`全局技能/impeccable/reference/generate.md:3-13,34-37,72-99`|

支持入口：doctor仅真实诊断/明确请求，不自动修漂移；hooks仅用户明确要求；pin/unpin仅明确用户行为。所有检测结果都是辅助线索，不替代目视/行为/金融事实。

### 4. 原生状态路径裁定

#### 4.1 原文与源码支持的事实

- `--target`真实支持，绑定实现目标/工作区；不是状态目录flag。证据：`target_args.rs:8-49`。
- `IMPECCABLE_CONTEXT_DIR`真实支持，但仅在project/repo均无PRODUCT及DESIGN时作为两类文件读取fallback。证据：`context.rs:143-175,341-347`。
- surface brief仍写`<project_root>/.impeccable/surfaces`；question/roll/mocks/build仍由cwd生成；live写`<app_root>/.impeccable/live`；buildPath从project/repo `.impeccable/config[.local].json`读取；document sidecar仍固定`.impeccable/design.json`。
- 因此“env+--target已完整重定位所有状态”是错误的。原版未发现已验证的全状态重定位参数，不得编造。源码均从固定SHA获取并逐Git blob核验，位于本报告`upstream-impeccable/`。

#### 4.2 真实项目路径（兼容人工流程，明确非full-native）

1. 把确认记录放入`artifacts/runs/<run>/confirmed-context/PRODUCT.md`。保留原schema字段；记录confirmed/open、canonical事实/章节指针、版本/hash、owner与“本文件是派生投影，来源冲突以canonical为准”。产品事实确认需真实用户答案/批准轮，现有context-workspace草稿不满足。
2. 在实际repo cwd按原生支持的env读取，target为`apps/web`，只在会话需要时执行一次context。若local PRODUCT/DESIGN存在，env可能被忽略，必须核返回路径，不能强制宣称覆盖。
3. 用户问`apps/web/PRODUCT.md`是否可行：可以成为自然解析的确认记录，但须源文档规则允许，并保持派生事实身份；它仍不能解决任何固定state路径冲突。当前更简单的是已支持env的ignored投影。
4. 原版shape/方向contract/spec/critique/finish/documenter的字段和判断步骤不缩减，实际写入ignored artifacts。报告先聊天、snapshot/trend/问题收尾等能力保留。明确记录路径/运行时兼容适配，不能称原生state机已完整执行。
5. 原版只读detect可继续做辅助检查并把stdout存artifacts；不调用写配置、自动doctor修复或source live注入。实际native助手角色未验证时使用新鲜无fork兼容代理，启动后读原角色和完整packet，披露身份。

可核原生读取形式（占位run需由执行者替换；本审计没有重新执行）：

```bash
IMPECCABLE_CONTEXT_DIR="$PWD/artifacts/runs/<run>/confirmed-context" "$HOME/.codex/skills/impeccable/scripts/impeccable" context --target apps/web
```

#### 4.3 完整native试件路径

1. 固定版本建立`artifacts/runs/<run>/native-ui-trial`中的真实UI试件工程，使用真实package root；不带生产.env/keys、真实账户库、broker/backend调度。设计fixture清晰标示。它是ignored产物，不是产品权威，不建立嵌套managed Git仓。
2. 从试件cwd执行原版；先核context的projectRoot/contextDir/repoRoot和live roots的appRoot/sessionRoot不会写出试件。源码的root解析不等于运行验收；若返回不合格，停止native写操作，退回明确人工流程，不能假称隔离。
3. 满足后可在试件内完整使用原生`.impeccable`问板/build-phase/live/sidecar；源原文不改。试件通过只证明试件，真实repo仍需行为/Canvas/性能/发布门。
4. fresh finish-reviewer/documenter需实际harness角色加载证据；现工具只有普通spawn_agent时，兼容代理严格保留原角色原文、完整packet、disposition和独立性，但不声称原生agent身份。

### 5. 冲突逐项裁定

|冲突|执行裁定|
|---|---|
|User full redesign vs original targeted redesign|Replace presentation and component combination, preserve actual framework/API/identity/business contracts; Scan and Diagnose remain complete.|
|User top navigation vs product example sidebar|Top navigation wins; use original product reference alternative shell, not invented sidebar requirement.|
|User liquid material vs Taste v2 dashboard exclusion|Keep main Taste v2 excluded on dense operation surfaces; material research proceeds through applicable Impeccable/product skills, never claim partial use equals main full use.|
|Operate no page-load choreography vs product counters/staggers|Do not animate financial facts from zero or redraw all rows on poll. Low-frequency true summaries and user-triggered continuity may use bounded state transitions.|
|User spring feedback vs generic neverelastic|Use purpose-driven small settle on selected reversible transitions, with still/reduced form, not bounce everywhere.|
|Original organic/messy/randomized facts vs project data truth|Never invent production symbols, dates, prices, news, returns, counts, brands or progress. Clearly labelled isolated design fixtures only.|
|No horizontal scroll default vs user container horizontal scroll|Prohibit accidental page horizontal overflow; keep semantic tables in a single local horizontal viewport, visible scroll affordance and keyboard/touch path.|
|Compact visible buttons vs 44px touch|Separate visible face and actual contained hit area; no overlap, off-container pseudo hit zones or truncated text.|
|Root clip recommendation vs no hiding layout defects|Repair min-width/min-height/grid tracks, overlay portals and scroll ownership first. Root clip only as last protective boundary, not fix evidence.|
|Multiple libraries vs single coherent design system|One semantic component family and token authority; external effects are scoped consumers with licence/cost/lifecycle evidence, not mandatory every package installed.|
|Original private state paths vs neutral source tree|Limited context env is supported, full state relocation is not. Native confined to verified ignored trial; real repo manual-compatible flow disclosed.|
|Latin typography/showcase recommendations vs Chinese financial UI|Use actual CJK metrics and tabular numeric roles; default marketing Inter/display scale is not automatic choice.|
|AI optimistic/success/stop interaction vs backend truth|Server-confirmed financial results and real cooperative cancel state; show pending/unknown instead of pretending canceled/completed.|
|Skill generic prompts vs existing user answers|Reuse confirmed facts and authorization; only ask new material unanswered choices. Preserve original output/gates rather than repeat questionnaires.|

### 6. 建议最终阶段顺序

1. **A 价值与现状**：governor完整决策；finesse-brief修订；原版redesign Scan/Diagnose；当前事实与目标消费者；U49有界反证。
2. **B 事实与交互**：确认真实PRODUCT投影；finesse-term贯穿；Interaction Design完整五维/十态/任务路径；功能组合先证后端承接。
3. **C 参考与比选**：明确网站逐项评估；入选案例TasteLab四阶段；UI/UX system与窄查候选；motion/material隔离证据。
4. **D 选案与方案门**：Impeccable7方向/seed/challenger/问板/comp-code选择；frontend独立构图；finesse完整Design Read/dials/五轴/四beats/product分支输入；共用一次可审选案保留各自产物；shape确认并停。
5. **E 批准后实施**：6块contract；comp-led state/spec/firstviewport门或code-led等价约束；写UI前craft-floor；真实product/workflow/agent/component分支；有界GSAP与材质、全部页面/子功能。
6. **F 实屏与反证**：状态/Canvas五层、PC/mobile/短屏/输入/主题/RM；critique独立A/B；technical audit；按原因layout/typeset/colorize等；harden/adapt/polish/optimize有界修正；不无限同窗循环。
7. **G 收尾与发布**：新鲜finish packet与原处分；documenter从建成系统抽token/sidecar；Jev最终准确代码评估；项目适用门禁/精确HEAD release/运行版/截图；限制获准。

范围备注：这次只交付方案审计。真实UI/提醒修复、完整方向问板、comp批准、所有浏览器/设备像素与性能均未由本审计执行。


## 附录B：30项清晰来源与实际用法

每行都区分采用、仅参考、条件试件或不采用；浏览目录、读源码、视觉提炼和运行验收互不替代。资源编号REF仅在本方案追溯，不是任务或新注册表。

|编号/来源|具体案例与选择|功能与组合|证据与许可边界|采用验收/退出|
|---|---|---|---|---|
|REF01 [ultramotion](https://github.com/sunsiyuan/ultramotion)|skills/liquid-glass/SKILL.md；scene.html；采用视觉/形变原理；不接视频运行时|顶部搜索、操作弹层、浮起证据预览；暗色不透明阅读底 + 局部液态控制层；触发点扩张、收回同源；正文不被折射|本轮原仓及固定源码0c56a227已读；未运行视频或网页材质；MIT；字体另有OFL|液态效果需有边缘/背景位移与连续形变证据；截图同时验证文本清晰；禁止把blur当真折射|
|REF02 [原版 Taste](https://github.com/Leonxlnx/taste-skill)|主技能 + redesign-existing-projects；主技能按原文排除密集工作台；redesign审计现有应用|现有应用审查；仅真实展示型表面可用主技能；分别完成原版的Scan→Diagnose→Fix与其适用审查，不能融合成几条反AI规则|本轮原仓重访；已安装固定b482f7a9；原版入口由技能审计另核；按固定原仓包许可；不混brandkit或TasteLab|逐项原版职责、输入、输出与反例；不得虚构日期/交易/业绩数据来制造自然感|
|REF03 [impeccable](https://github.com/pbakaus/impeccable)|init/shape/new-work/Operate/Read/检查命令；原版流程主干；未来执行不写成现在完成|全部重设计表面；工作台Operate、证据阅读Read；产品事实→shape→七方向比较与视觉板→选择→方向契约→craft-floor→实现→检查→document|本轮原仓重访；62文件已原版安装与engine核验，不再保留缺支持文件旧结论；Apache-2.0；按包中第三方条款|所有适用命令有触发/产物/验收；选择和实际截图未完成时不可写全流程已执行|
|REF04 [finesse-brief](https://github.com/mouse-lin/finesse-brief)|skills/finesse-brief；System轨Workbench Spec；独立需求架构阶段|股票/事件为业务主对象的多模块工作台；定位、用户、每日主任务→首屏/页面层级/数据模型/次日回访；交给视觉技能|本轮原仓重访；固定d1f45618完整快照；两全局技能根未安装；MIT|输出完整System Workbench Spec；复用已知事实，只追问实质缺口；不得假写已安装|
|REF05 [finesse-ui](https://github.com/mouse-lin/finesse-skill)|product + READ/OPERATE + mobile-floor + workflow-ui；独立视觉/组件/密度方案；不缩成配色器|实时工作台、审核/维护流程、手机H5适配；先读brief与Design Read，再SOUL/SPECTACLE/DENSITY、配色家族、组件/图表/表单/状态；手机是响应式产品，不伪装phone-only壳|本轮原仓重访；固定5050b6c7完整快照；未全局安装；MIT；样例依赖另核|原版product、workflow、dataviz、chart-crafting、h5/mobile、motion、audit/detector各有消费者和产物；不照搬brand hero|
|REF06 [finesse-term](https://github.com/mouse-lin/finesse-term)|128词条；say/name/explain/check/align/list；跨阶段语义对齐；不负责决策或美观|用户大白话需求→组件状态/交互契约；原话触发→术语库检索→触发/状态/几何/反馈/回收；安全区配吸底、滚动锁配弹层、键盘配输入|本轮原仓重访；固定139200b8词库和脚本存在；未全局安装；MIT|检索命中可追溯；没有命中就写未收录；输出先交付结果，术语最多1–3个，不机械给每行教学|
|REF07 [TasteLab / tastelab-taste](https://www.tastelab.xyz)|本地技能原仓senlindesign/taste-skill；四阶段Design Map/Taste DNA；采用研究方法；站点失败不当可用案例|实际入选公开参考站的独立设计系统提炼；截图+只读DOM→Measure→Pattern→Taste→Observer；解释转用取舍；与Leon原版Taste分开|本轮www/非www均获取失败；本地完整技能存在，未做新截图/DOM；方法技能与站点素材许可分开；不复制站点私有资产|至少一张真实截图+domData；四阶段全产物；缺测量不能写完成提炼，不等待站点首页恢复|
|REF08 [ThreeUI Community](https://github.com/MengTo/threeui)|Community source、catalog、public/community-sync-report.json；固定源码对比；按组件取用，避免整库全导入|材质试件、工具按钮；与React Bits/CSS原生相同背景/尺寸/状态比较；只选有免费完整实现且收益成立者|本轮固定tree68802d54及report/按钮源码已读；未运行组件；应用/Community代码MIT，字体OFL，远端预览不随仓分发|选定组件必须存在Community公开源码；记录依赖/资源路径/构建体积/清理；Pro/Beta无授权不采用|
|REF09 [ThreeUI Advanced Glass Material](https://threeui.com/hero/advanced-glass-material)|two-bounce refraction、edge dispersion、studio reflections；材质对照，当前不复制实现|局部液态材质技术对照，不作为整屏背景；只借边缘色散/折射对比，不把全屏水场/Orbit搬进实时工作区|原页说明可读但预览Loading；固定Community报告未列该组件，免费实现未取得；尚未证明公开实现许可/可得性|进入原型仅在取得合法实现后；未取得时用已核源码的React Bits或项目独立实现；不宣称已目视|
|REF10 [ThreeUI Glassy Split](https://threeui.com/buttons/star-portal/glassy-split)|玻璃分裂创建菜单；语义/连续性参考；当前不复制实现|添加自选/打开快捷动作；按钮本体扩成面板→选项→同源收回；形变时标签保持不变形；合Bencho Create menu与Radix行为|原页说明重访；Community报告未列glassy-split；ShaderButtons公开类型只有6种，不含该项；未取得该变体的公开实现/许可|免费完整实现未核时不列已采用组件；键盘/触摸均可开合，不强依赖WebGL|
|REF11 [ThreeUI Dark Glass](https://threeui.com/buttons/rectangle-buttons/dark-pill)|RectangleButtons dark-pill，DOM+CSS；优先紧凑工具按钮试件|依据/等待的跟随、固定条件、对比；通用次级动作；炭黑面+银色边缘+单次浅高光+0.96按压；业务主色由新方案决定|固定68802d54报告包含dark-pill；对应RectangleButtons源码已读；未视觉运行；Community MIT；保留代码notice|只借材质与按压，重做尺寸/中文长度/禁用/加载；不把按钮扩大；不是背后真折射|
|REF12 [Galaxy](https://github.com/uiverse-io/galaxy)|CSS按钮/Toggle/loader公开目录；已读取0x-Sarthak、3bdel3ziz-T、0xnihilism三个源码；本轮原样候选均不直接采用；保留逐项对比结果|通用按钮/开关/等待反馈对照；对比ThreeUI暗按钮和项目基础按钮；只保留适用的按压/焦点细节，不能按示例硬套紫色CTA/2秒点击回弹|本轮原仓及固定adbd2add三源码已读；未运行；原仓UI代码MIT；所读开关内嵌Font Awesome图标需另保留该资产许可；不混为全包MIT|按钮18px字/3px边/2秒回弹，开关无可读名称且引Font Awesome，loader240–280px/高频闪烁均不适合；不能写已用2–3个|
|REF13 [MicroKit UI](https://microkit.co/components)|Blur Glide Menu、Sliding Underline Tabs、Scrub Number Field、Focus Field；采用适用机制，先做源码级补强|核对视角Tabs、移动搜索、维护字段、分层快捷菜单；下划线滑移+Radix Tabs键盘；Blur Glide在层级菜单；数字scrub保留点击输入，交易字段仍整手硬门|本轮目录49项与3详情重访；固定d41259c5三组件源码已读；Focus详情获取失败；MIT；copy-paste/shadcn registry，不是npm整库|原例Tabs10px/248px/缺键盘关联不可原样搬；图标换Hugeicons；motion-reduce和中文长文均验|
|REF14 [Bencho](https://bencho.dev)|Search、Notify、Create menu、Inline confirm、Reorder list、Tilt card；七交互分类；用精确消费者比选；未知源码许可不复制|手机搜索、消息确认、加自选、可撤销删除、列表重排、证据预览；Search原位展开+自动焦点；Notify状态同位更新；Create menu同源展开；Reorder稳定槽位；Tilt仅预览|本轮目录与七分类清晰；旧批次有截图，本轮未玩遍/未核源码许可；本轮没有取得明确复制许可和版本；行为研究可用，源码引入前核|Hover均有focus/tap等价，drag有键盘替代；轮询不重播；“七类全用”不是验收目标|
|REF15 [React Bits](https://reactbits.dev/components/glass-surface)|GlassSurface、TiltedCard TS-CSS；局部液态层/预览首选试件；按源码导入|顶部控制层、搜索/证据浮层、独立证据预览卡；GlassSurface仅做背景表面，文字独立清晰；TiltedCard只悬停预览，行情表不倾斜；GSAP和本组件不重复控制同一transform|原站JS无文本；固定63a008de GlassSurface/TiltedCard/LICENSE源码已读；MIT + Commons Clause；应用内使用可，禁止组件本身转售/转授权/分发，保留notice|GlassSurface源码明确Safari/Firefox退CSS路径；不能承诺全浏览器背后真折射；真实Safari/Chrome截图+性能对照|
|REF16 [Lenis](https://github.com/darkroomengineering/lenis)|指定wrapper/content、生命周期、prevent/nested scroll；仅长证据阅读容器做受控对比；未证明收益前保留原生|研究详情/复盘长阅读容器；惯性仅作用阅读容器；图表、表格、自选、抽屉仍原生；与GSAP ticker只有一个时钟和卸载清理|本轮原仓重访；固定bc152f90身份明确；未新增依赖；MIT|同内容PC/手机读文对照；键盘/选择/嵌套/触摸/reduced motion通过；达不到收益退出|
|REF17 [GSAP](https://gsap.com/docs/v3/)|Core、React useGSAP、timeline、Flip、matchMedia、cleanup；已有依赖，担当共用编排底座|筛选/列表网格重排、抽屉/菜单形变、提交反馈、页面连续转场；Flip保留卡片身份/焦点；timeline串联触发→进行→完成/失败→归位；只transform/opacity优先|本轮官方文档重访；项目有真实消费者；新编排未实现；按安装版本GSAP Standard License及原仓条款核，不笼统写MIT|useGSAP/context revert；取消/重试/卸载/StrictMode无残留；直播数字更新不重播整个列表|
|REF18 [Vanta](https://www.vantajs.com/)|Waves等持续WebGL背景；评估后本轮不接入|无实时工作区合适消费者；真实行情需要稳定背景；已选择局部液态层，不增加常驻GPU动画|本轮原站与固定f8b35190身份已核；不宣称实测更耗电的具体数值；MIT；依赖Three/p5按所选模板另核|不安装无消费者依赖；未来真实展示页单独有预算和退出条件才重开|
|REF19 [Remocn](https://remocn.dev/)|Typography/Shaders/Transitions/Animated Icons/UI Primitives；原站当前自称327组件；已查明是产品演示视频工具；不接实时UI运行时|若需要后续演示视频可另列交付，当前无消费者；只以视频时序的开头/进行/结束对照方案，不把视频帧组件当交互React控件|本轮根站可读，/docs路径获取失败；不是此前笼统“获取失败”；根站宣称MIT；实际源码/包未核，当前不复制|更正“上千UI动效”旧前提；没有演示视频需求就不安装，不为全部用而新增视频任务|
|REF20 [curated.design](https://curated.design/)|Askpalette https://curated.design/sites/s/84571/；Superset /sites/s/31358/；案例筛选；视觉提炼需后续实截图/DOM|市场全景组合、研究页主次、桌面/手机搜索入口；从公开案例提炼主角比例/辅助操作，不复制页面；本方案7/3只是假设，测量前不声称案例采用该比例|本轮目录与2详情可读，未完成新截图/DOM；案例截图/素材属各所有者；只提炼设计原理|入选案例必须有真实截图、DOM与取舍记录；仅目录标题不能证明好看或已充分研究|
|REF21 [landing.love](https://www.landing.love/categories/finance/)|Tempo https://www.landing.love/sites/tempo-2/；原站tempo.xyz；金融信息层级参考；营销结构限定范围|首次引导、任务首屏说明、长研究证据分节；价值一句话→当前任务→清晰动作；实时工作台保留行情主角，不加长滚动Hero|本轮金融分类/Tempo详情已读；视频和原站新视觉未验证；截图归各作者；只参考层级|对比声明/来源是否容易读；不借用伙伴logo/营销事实，不把展示网页当金融仪表盘设计证据|
|REF22 [saaspo.com](https://saaspo.com/sections/linear-features-section)|Linear Features Section；Dark Mode/Bento目录；身份/具体案例已明确；直页403，本轮不作为已完成提炼证据|相邻功能融合的待观察案例；只保留暗色功能组候选；改用已可读且测量完成的参考作实施依据，恢复后可替换|本轮搜索索引能定位上述原站案例；根站与详情直接403；不是截图已读；案例素材许可未核；不复制|不等待恢复、不绕访问限制；无截图DOM前不写其配色/尺寸/动效为实测|
|REF23 [navbar.gallery](https://www.navbar.gallery/navbar/customer-io)|Customer.io Mega Menu；Outerbase /navbar/outerbase；Supaste仅Static；顶部导航与通栏分组采用合适案例；纠正Supaste误配|桌面顶导航、移动搜索/菜单；同一个通栏面板承载分组，切换内容保持打开；手机点击同源全屏/折叠；保留头导，无侧导|本轮三原站详情与Mega Menu说明重访；新截图/原站DOM尚未提炼；截图版权归各作者；不复制资产|tab/focus/点击/外部关闭/ESC同等；hover不是唯一入口；菜单不压正文/越界；Supaste无下拉不可当通栏案例|
|REF24 [cta.gallery](https://www.cta.gallery/cta/get-ripe-copy)|原站Numa详情：Light/Ecommerce/Call-to-Buy；只作为不适合本系统的反例记录；按钮行动层级取规范|主/次/危险操作层级；审阅Numa后拒绝买入式大CTA/浅色商品布局；采用紧凑动作、状态同位回执|本轮目录与Numa详情已读；不是深色金融工具范例；图库截图各版权所有；不复制|所有按钮按目的/风险/频次定级；不能宣称已搬Numa风格；金融风险提示和证据入口不被转化CTA遮盖|
|REF25 [Hugeicons](https://github.com/hugeicons/hugeicons)|@hugeicons/react + @hugeicons/core-free-icons Stroke Rounded；复用当前官方包，统一图标系统|通知、关闭、删除、搜索、菜单、MA/布局操作；16–18px可视图标与统一线宽，点击区独立44px；用几何光学居中，不让SVG贴边|原站主页读取失败；官方旧仓明确Deprecated并链接本monorepo，官方新仓重访；项目现依赖需精确核对；renderer与free pack各按安装包许可核；Pro图标不使用|不用已废弃hugeicons-react；关闭/通知/删除全部同类普查；accessible name、focus、载入位置不跳|
|REF26 [component.gallery](https://component.gallery/components/modal/)|Modal组件语义与各设计系统链接；作为跨系统组件规范对照|Dialog/Popover/Tooltip/Drawer/Toast/Command选择；先按是否阻断任务/是否上下文/是否短信息决定组件，再设计材质；不全用模态弹窗|本轮Modal官方汇编可读；不是所有外链系统逐个研究完成；规范引用；外链代码许可分开|焦点圈定、恢复触发器、ESC/外点、标题关联、遮罩滚动锁与层级；单纯截图不等于通过|
|REF27 [MotionSites.ai](https://motionsites.ai/?prompt=aethera-hero)|公开目录Glow Features/Finlytic AI Agent/Aethera Studio；只做公开视觉候选目录，不使用未取得prompt|候选方向视觉板，非实际功能代码；与finesse/impeccable方向比较；没有实图/DOM就不能从名称推导光晕/流程|本轮query页恢复可读；根站失败；未取得付费prompt或完整源码；All rights reserved；没有复制授权证明，不购买|不声称已用premium prompt；若公开预览不可稳定测量则从入选实现参考退出，保留已审覆盖记录|
|REF28 [shadcn/ui](https://ui.shadcn.com/docs/components/radix/dialog)|明确Radix变体Dialog/Command/Popover/Toggle/ScrollArea/Tabs；可访问行为参考/现组件底座；外观全新|全域弹层、搜索命令面板、图表MA按钮、核对Tabs；行为层复用Radix现契约，液态材质/尺寸/布局自行建立；筛选不用伪Tabs，核对视角保留真Tabs|本轮官方Radix Dialog路径已读；不是已安装完整shadcn库；MIT；具体复制模板notice保留|不默默切换Base UI；Tabs键盘、Toggle aria-pressed、Dialog焦点与错误状态验收|
|REF29 [Radix ScrollArea](https://www.radix-ui.com/primitives/docs/components/scroll-area)|Root/Viewport/Scrollbar orientation=horizontal/vertical/Thumb/Corner；溢出时可见轨道候选，与原生对照|自选列表、宽表、提醒管理、维护记录、证据长文；指定每个容器单一滚动owner，横纵按实际溢出渲染；外围min-height:0/min-width:0，弹层内部滚动|本轮官方文档可读；新组件未安装/实测；MIT|有溢出必须可见可操作轨道；拖拽/键盘/触摸/焦点项滚入；移动页面不产生二重纵向滚动|
|REF30 [MDN scrollbar-gutter](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/scrollbar-gutter)|overlay与classic scrollbar说明；工程验收依据|全部容器与宽表；gutter解决classic预留间距，不能强制overlay系统显示横条；需要显式轨道时选ScrollArea|本轮原文重访；MDN文档按其内容许可引用；只说明行为不复制大段|scrollWidth>clientWidth真实发生时核track与拖动；不能以overflow:auto字符串或gutter存在判通过|

## 附录C：40条跨切面用户要求

这些要求与效果候选分别追溯；R编号用于本文引用，不产生第二账本。原有业务目标和全部金融硬门仍由原owner维护。

|编号/方面|明确要求|消费者|可核验判据|
|---|---|---|---|
|R01 产品定位|实时A股研究/事件跟随/证据核对/复盘/模拟；重点发现已走强及续强机会，不虚构预知起爆|全域导航、机会、研究、证券详情|每屏有主任务/输入/输出/下一动作；无营销Hero替代工作区|
|R02 新方案独立|旧UI仅作对照/恢复；新布局、组合、材质、色彩要重想|三个代表屏→全域迁移|方向选择记录、对照图、组件谱系；不能只换颜色/blur|
|R03 顶部导航|保留顶部主导航；四任务定位清楚；维护按职责收纳|nav-bar、task-browser、command-palette|无侧边主导航/重复底Dock；键盘/手机均可达|
|R04 技能原版与分工|每个技能按原版范围、输入、流程、产出和验收独立执行|规划→设计→实现→检查→收尾|原文来源/版本及适用裁定；不称所有命令强跑等于全发挥|
|R05 外站身份|明确URL的参考逐一核身份、组件用途；不清晰地址从活动方案删除|资源矩阵及组件选择|仅已知站点留活动列表；已采用项有具体组件/作用/许可/版本/来源证据|
|R06 组件搭配|每个组件按任务和空间选择，不用同样卡片/Tab展示所有功能|全域控件与数据阅读面|任务→组件→反馈→动效→返回→退化组合契约，非库名称清单|
|R07 深色与配色|深色优先，黑银基底有明确互补点缀和层次；涨跌/风险颜色独立|所有页面/弹层/图表/选择态|正文/次文/焦点/disabled对比度与色盲辨识；用户对照认可新搭配|
|R08 液态材质|要求液态光学近似，不以透明blur冒称折射；之前过透问题记入恢复说明|顶栏、搜索、轻菜单、抽屉外沿|真实背景移动像素证据、文字不失真、减少透明实体退化、浏览器兼容|
|R09 字体与中文|中文长名称、金额、日期、状态、按钮字不挤断；数值tabular-nums|所有控件/表格/读面/tooltip|320/390及200%缩放、长串/IME；不以隐藏内容和全局nowrap掩盖问题|
|R10 控件尺寸全族|所有关闭/删除/通知/新增/刷新/提交/选择/Tab逐族扫描|IconButton、原生button、links、select及所有调用方|可见面紧凑、手机命中区44px且不遮邻项；关闭按钮也验；光学居中|
|R11 手机搜索|整行足够宽且有明确入口/展开归属，不挤在通知旁角落|SearchBox/CommandPalette/任务菜单搜索|320px键盘/安全区/结果层、不裁输入与确认；自动聚焦/回焦/错误重试|
|R12 Tab与筛选语义|核对视角是真Tabs；市场视角/范围/排序按导航或值选择语义；MA为pressed按钮|SelectionRail/MarketLensPicker/FilterMenu/stock-detail/K线|全部选中底面同族、没有突兀色块；方向键/URL/aria正确|
|R13 图表主导|分时/K线不被工具挤扁；宽度用满主区；真实Canvas、磁吸和图例完整|minute-chart、kline-chart-pro、flow-chart、独立详情|测绘图区而非外框；短屏/左右调宽/放大/切股/切日期/缺失/指数单验|
|R14 自选纯列表|自选不放分时小图；管理下拉不遮文字，删除不溢出|工作台自选行/分组工具/管理态|超长证券名/数值/分组名/删除触点；内部纵滚而非整页拉长|
|R15 滚动归属|应用壳稳定，各工作区/正文/列表/表格/弹层唯一owner|全域grid/flex/工作区/弹层|无意外页面级双滚；横向内容超宽可滚、可见边界提示/控制、键盘触摸拖动|
|R16 弹层合理性|宽内容应宽Dialog/Sheet，轻选择Popover；不把所有内容塞窄抽屉|提醒管理/参数/新闻/证券/证据/确认|视口内宽高、内滚动、Esc/点外/关闭按语义、单焦点栈和返回|
|R17 边界与层级|堆叠同方向，底角完整，卡片间距与邻区净距明确|维护收纳、证据叠牌、候选卡、全域浮层|动态中包围盒不越界、不贴邻容器、不挡关闭/字；手机另定排法|
|R18 按钮基线与比例|标题/动作/右栏对齐，主任务比次信息更突出|工作台右栏、市场/机会、工具栏、通知管理|真实测量baseline、min-width/min-height收缩、长内容/短屏；可调宽度保留|
|R19 后台与前台|配置/生成/策略维护归受控域；结果/风险/用户命令仍可前台|任务/参数/策略健康/生产命令/复盘读取|迁出前证明后台触发、单写者、持久结果、失败/取消恢复和原权限；不删除业务能力|
|R20 AI失败规则提醒|AI不可用先规则筛选，明示规则身份/质量，不未经筛选全弹|alert-triage→通知→浮泡|有效规则仍提醒、unknown/stale可辨；失败不漏必要风险；规则解释不假称AI|
|R21 提醒新颖度与重复|同态不反复，新事件/穿越/风险升级可提醒；跨股、无股主题不互抑制|后端意图/展示revision、通知轮询|刷新/断线重连/多窗/多轮询/跨日/ignore后升级反例；不靠全局静音|
|R22 提醒管理完整|宽比例、完整理由、真实今日分母、排序/筛选/启停/删除回执|AlertsTab/通知诊断/规则表|长理由/长规则名/缺字段/0与未知；横滚/分页/触摸；错误不丢草稿|
|R23 通知事实|未读/已读/清除与原事件、外发渠道分开；铃铛居中|NotificationBell/Drawer/EventFeed/diagnosis|服务端同步态/失败/跨窗水位/清除后回读；资讯不冒充提醒未读|
|R24 取消历史回放|取消唯一回放UI和播放状态，历史行情/研究/回测保留|stock-detail/replay-chart/调用图|入口消失且无孤立唯一组件；历史K线/深链/共享API/幂等字段未误删|
|R25 真实数据|真实源时间/质量/缺失可见，mock/缓存不能冒充实盘；零值和缺数分开|全域行情/表格/图表/提醒/助手|实源与夹具分证，加载运行SHA/端口明确；指数/个股能力不混，不再-100%哨兵错误|
|R26 跨模块对象|证券/题材/事件/日期/版本/返回来源统一，近N日与当日口径不同|市场→详情→机会→工作台→通知→研究|跳转/后退/刷新/深链/快速切换、旧请求迟到不串对象日期；历史不混今日|
|R27 机会类型开放|首板/连板/强趋势/独立驱动等按事实区分，无梯队不自动剔除|机会卡/依据与等待/研究样本|共性/反例/失效可读，不把已知赢家当未来胜率、不因菜单封情境数量|
|R28 微交互状态对齐|加载/完成/失败/取消各有真实反馈，终态连续且可中断|共享按钮/表单/重排/抽屉/助手|快速反向/重复点击/卸载/慢网/取消/unknown；不等待动画执行硬门|
|R29 动效适用与去重|107项逐项默认/有条件/不采用，同类一次实现|effect-decisions与组合契约|每项有消费者/PC手机/退出；无业务不造页面；无常驻花哨行情背景|
|R30 触摸/键盘无障碍|无hover独占功能，手势有按钮/键盘替代；真实可聚焦语义|tooltip/图表/拖拽/长按/抽屉/表单|Tab顺序/方向键/Esc/回焦/屏读/触点；原生滚动优先；提示手机可点击查看|
|R31 性能与流畅|减少重绘、长任务、GPU常驻和动画冲突，真实数据即时|动画/大列表/Canvas/浮层/WS|同机窗数据基线测bundle、INP、长任务、拖图；清理事件/RAF/轮询；不宣称未测改进|
|R32 资源比较与依赖|自写/最小原语/外库按真实消费者比较，不以拒装或全装作质量|GSAP/ReactBits/ThreeUI/Galaxy/Lenis/Vanta等|效果、许可、维护、体积、生命周期、手机兼容逐项；最终选择及替代原因|
|R33 全页面子功能截图|每个模块和子功能主状态可见，并记录关键弹层/动态|路由→动作→状态→视口证据图册|逐张打开确认，截图带路由/SHA/尺寸/状态/数据范围；动画另录屏不靠静态证明|
|R34 状态全域|loading/ready/合法空/部分空/error/stale/unknown/乱序/权限/重试/取消|每个可观察动作、其上下游|逐项声明适用/不适用并给理由，不能默认一张ready截图覆盖全部|
|R35 恢复与保存|保留当前UI版本，删除此前活动保存；恢复警告透明问题|当前tag/source包/恢复说明|哈希可核、无业务数据覆盖、不恢复退休融合skill；截图证据不随清理丢失|
|R36 规划与验收|先可审方案和原版方向选择，再实施、批检、收尾与发布|技能独立产出/U49/准确HEAD CI|本轮方案不冒称UI已改；不无限精修、两批无新证据换方法；用户主观审美另确认|
|R37 私人偏好与草稿|主题/密度/布局/自选排序/私人助手草稿合理前端保存|共享偏好/助手/表单|存储失败/刷新/跨窗/配额不吞反馈；不机械后台化默默上传私人聊天|
|R38 金融与权限硬门|模拟/手记/shadow范围独立，不接真实券商；价格/费率/风险后端拥有|账户范围/TradeForm/风险/任务维护|布局/动效不绕权限/撮合/预算；错误/拒绝/未成交是第一等结果|
|R39 内容完整与事实层次|事实/推断/偏向/未知/失效条件分层，tooltip不藏唯一依据|新闻原文/证据/机会/研究/通知|来源/版本/发布时间/接收时间可辨，外链安全，缺原文不编造|
|R40 反复问题主动扩查|用户点名问题沿共享族全部回扫，覆盖相邻状态与上下游|关闭/通知/Tab/按钮/搜索/滚动全站|缺陷→共享原语→消费者名单→整改证据；不只修被点名位置|

## 附录D：18组页面与细功能覆盖

基点42407f93：7个page.tsx包括5任务路由及2重定向；85份非测试组件TSX。下表是已识别行为的覆盖起点，不是18组已通过或穷尽未来新功能。路径均相对apps/web；非app/lib条目默认在components内。每行须按动作进一步形成一行一状态的验收记录，共享原语的全部调用方进入同族回扫。

|编号/表面|当前源码入口|必须逐动作展开|关联要求|所需证据|
|---|---|---|---|---|
|S01 共享应用壳与导航|nav-bar.tsx；ui/task-browser.tsx；ui/command-palette.tsx；search-box.tsx|顶部任务导航、下落分组、命令打开/过滤/选择/关闭、搜索展开/IME/乱序/Enter/Escape/快速加自选、主题偏好、跳到工作区|R03；R09；R10；R11；R12；R15；R26；R30|搜索结果/无结果/错误重试；任务菜单、命令面板、浅深色图标与焦点|
|S02 工作台自选与管理|app/workbench/page.tsx；lib/watchlist-sync.ts|读取/切股/加入/移除/分组创建改名删除/成员移动/排序/管理模式/最近标的/返回来源/动态精选与跟踪只读分组|R10；R14；R15；R17；R18；R26；R37|空自选/长行/分组菜单/删除/失败/跨窗同步/内部滚动；无分时小图|
|S03 共享证券图表|stock-detail.tsx；minute-chart.tsx；kline-chart-pro.tsx；detail/flow-chart.tsx|分时/K线/资金、MA按钮/均价/成本/事件与成交点图例、周期/日期/技术依据、游标/缩放/拖动、左右调宽收起恢复、取消回放|R12；R13；R15；R18；R24；R25；R26|三Canvas真实截图；小窗/短屏/缩放/切股/源缺失/迟到；走势图层不被材质扭曲|
|S04 证券核对视角及指数能力|detail/book-trades-view.tsx；detail/profile-panel.tsx；detail/info-panel.tsx；detail/speed-panel.tsx；detail/board-rank-panel.tsx；detail/minute-decision-panel.tsx|盘口/逐笔/资料财报/公告资讯/盘中条件；指数仅涨速/板块；真正Tabs切换/键盘/URL；技术依据展开|R12；R13；R16；R25；R26；R39|七个股视角及两个指数视角；深链/无效参数/unsupported能力/真实时间单位与缺数|
|S05 账户与金融命令|detail/account-scope-panel.tsx；trade-form.tsx；detail/trade-panel.tsx；detail/real-position-panel.tsx|manual/paper/daily/hunting范围、订单/未成交/拒绝原因、草稿方向数量价格/现价、费用/风险预检、提交/挂单/撤单、手记新增修正删除、危险重置去向|R10；R16；R19；R25；R34；R37；R38|四scope读态、表单pending/error/reject/unknown、草稿切股不串、手机键盘；不触真实下单|
|S06 市场概览与环境|app/market/page.tsx；index-cards.tsx|指数/宽度/情绪/成交额、主视角选择、列表排序/成员下钻、来源质量与指数详情|R07；R12；R15；R18；R25；R26|概览PC手机、宽表横滚、数据部分失败与合法空；指数不发个股无效接口|
|S07 资金流与板块下钻|market/fund-tab.tsx；market/board-flow.tsx；market/flow-intraday-chart.tsx|同刻比较/全天估算/历史日、净额分档/机构游资、板块种类时间窗排序多筛选、分钟/日度/成员drawer关闭|R12；R13；R15；R16；R25；R26|历史/当前估算分示；换板块/日期竞态、抽屉关闭停止轮询、宽列横滚|
|S08 市场云图|market/heatmap-tab.tsx|全市场/自选/行业、下钻返回、hover/click/键盘小格替代、面积颜色图例/缺数/自选刷新|R07；R12；R15；R25；R26；R30|主Canvas/布局真实渲染、焦点/触摸替代、空自选/缺数/切源；不只看DOM文本|
|S09 题材与涨跌停生态|tape/themes-tab.tsx；tape/limit-up-tab.tsx；tape/limit-down-tab.tsx；theme-card.tsx；concept-detail-modal.tsx|日期/排序/连板与家数筛选、聚焦全部/别名、梯队成员/原因全文/换手封单炸板、断板/涨跌停切页和个股下钻|R12；R15；R16；R25；R26；R27|查询日绑定空池、source覆盖差异、历史不混今日热榜、独立强势无梯队不被误判|
|S10 龙虎榜与迁徙|tape/longhu-tab.tsx|日榜/三日榜/披露状态、席位/个股下钻、题材资金迁徙与近N日语义|R12；R15；R25；R26；R39|不同区间不相加、披露未知、成员缺失/横表/日期快切/详情返回|
|S11 事件新闻与证据|market/events-tab.tsx；event-panel.tsx；news-modal.tsx；detail/stock-events.tsx；detail/theme-chips.tsx|排序/类别标签筛选/分页、原文/无URL详情/修订撤回、方向/标的池/关联实体、证据详情共享转场、外链|R07；R15；R16；R25；R26；R39|长文/长来源/缺原文/转引去重；短预览与长Read面分工，手机返回锚点|
|S12 机会发现与跟踪|app/hunting/page.tsx；hunting/pick-sections.tsx；hunting/intraday-sections.tsx；picks/pick-card.tsx；ui/candidate-collection.tsx；hunting/opportunity-evidence-panel.tsx；hunting/watch-ledger-panel.tsx|盘前/盘中/盘后/潜伏、筛选/列表网格、首板连板趋势独立驱动、依据/等待/失效/首见/参考轨、跟随固定条件对比、证据台及返回|R10；R12；R15；R17；R18；R26；R27；R38|同股一记录/稳定key、新修订/部分失败；参考不当fill，所有旧深链兼容，按钮不大不换字|
|S13 通知与规则展示|notifications/notification-drawer.tsx；notifications/event-feed.tsx；assistant/floating-assistant.tsx|铃铛/计数、资讯机会/时段/分页、已读/全部/清除/刷新、逐股诊断、规则/AI身份、浮泡ack/新变化展示|R10；R15；R16；R20；R21；R23；R25；R26|无41独立入口但必须截图；重复轮询/重连/跨窗/今日分母/失败未知/铃铛居中|
|S14 提醒管理|research/alerts-tab.tsx；app/agent/page.tsx|规则创建/编辑条件范围阈值冷却与标的、保存/启停/删除、规则列表/命中/判读/筛选与宽管理面|R09；R10；R15；R16；R19；R20；R21；R22|长规则/长理由、宽列/横滚/手机输入键盘、失败保草稿、规则已筛/未筛/回退身份|
|S15 助手全流程|assistant/floating-assistant.tsx；assistant/rich-text.tsx；assistant/assistant-mark.tsx|打开/最小化/拖动、发送/streaming/停止真实drain/重试、引用与实体链接、新会话/历史切换删除/私人草稿恢复|R10；R15；R16；R25；R26；R28；R30；R37；R39|无41独立入口但全流程需证据；滚动贴底/用户上翻、长代码表格、无引用/断网/unknown、不假取消|
|S16 复盘研究与知识|research/review-tab.tsx；hunting/leader-research-panel.tsx；agent/kb-browser-tab.tsx；agent/markdown-view.tsx；agent/strategy-health-tab.tsx|按日读取复盘/结果/反例/失败漏选/次日条件、样本与否决方法、KB搜索正文引用/反证、方法健康只读、证据比较/日期回今天|R01；R15；R19；R25；R26；R27；R39|Read长文/宽表/缺产物、日期乱序/失败不永加载，否决不复活，不以已采纳当盈利|
|S17 系统维护收纳|app/agent/page.tsx；agent/production-operations.tsx；agent/task-center.tsx；agent/params-tab.tsx；agent/evolution-tab.tsx；agent/repo-tracker-tab.tsx；ui/workspace-deck.tsx|生产概况、任务/提交/预算/取消回执、迭代/实验/提案、参数晋级回滚、策略健康运营、仓库候选同步；按组就地展开|R10；R15；R16；R17；R18；R19；R28；R38|所有子工具读/编辑/失败/取消/权限态；设置跟踪不贴下容器、上下圆角完整、原批准链不变|
|S18 共享原语/路由/故障|ui/modal-shell.tsx；ui/icon-button.tsx；ui/filter-menu.tsx；ui/selection-rail.tsx；ui/control-hint.tsx；ui/spatial-surface.tsx；detail/symbol-detail-modal.tsx；app/error.tsx；app/global-error.tsx；app/loading.tsx；lib/routing.ts|Dialog/Drawer/Popover/Tooltip/Select/Tabs、关闭/层级/焦点/回退、空错加载、首页和stock重定向、旧URL解析/返回、Resize/卸载/减少动态|R10；R12；R15；R16；R26；R28；R30；R34；R40|每原语及直接全部消费者；重定向非法symbol、共享详情不串数据，非模态背景仍可操作|

## 附录E：107项效果与功能逐项裁定

55项默认纳入、28项有条件、24项不采用。默认不是已实现；有条件项只在真实消费者、兼容/许可、收益和可访问终态通过后采用，失败走本文控件基线。不采用项不加依赖或新业务。重复ID共用一个状态机，绝不叠加执行。

|ID/要求|裁定与真实消费者|具体行为或排除理由|PC/手机与组合终态|
|---|---|---|---|
|N01 搜索框展开|默认；顶部证券搜索|按钮原位展开为输入；输入光标主动定位；不改变当前页面；Escape清结果再回入口|PC从真实入口就地展开；手机输入整行/弹层安全区内，键盘可达。|
|N02 加号原位展开面板|默认；搜索结果快速加自选/自选管理加号|从真实加号展开上下文操作面；关闭回同入口；不创建第二个无用FAB|PC从真实入口就地展开；手机输入整行/弹层安全区内，键盘可达。|
|N03 提交加载→完成→结果|默认；规则保存/受控任务提交/模拟交易|固定按钮框内依次呈现等待、服务端确认结果、可重试错误；未知不变成功|PC/手机同位回执；禁用/失败/unknown可读，布局不跳。|
|N04 菜单/播放图标变形|默认；手机任务菜单开关|同一按钮的三横线连续变关闭；历史回放取消后不新增播放入口|PC顶部通栏/键盘；手机点击任务菜单；URL与返回来源一致。|
|N05 指示条拉伸后收回|默认；核对视角与真正内容Tabs|细指示线向目标拉伸再回收；焦点与aria-selected始终指真实页签|PC方向键与内容关联；手机紧凑且可达；滤镜不是Tabs。|
|N06 按钮变数量步进器|不采用；模拟数量字段|保留明确数值输入及整手步进；按钮归零消失会隐藏数量语义，不能改撮合规则|PC/手机均不进入本次实现。|
|N07 列表就地展开详情|默认；提醒行/事件行/机会摘要依据|当前条目内展开短依据，其余条目顺移；保留源条目、滚动锚点和日期|PC短依据内联；手机顺势下移，长文入内容窗。|
|N08 列表切网格原地重排|默认；机会卡片/列表|同一对象稳定key重排；不卸载详情草稿、不重发生成命令|PC保对象/焦点自然重排；手机单列或网格按实际可读宽度切换。|
|N09 滚动顶栏收起/返回展开|有条件；复盘正文次级工具栏|仅长阅读容器滚动时收起次工具；搜索输入/风险提示/主导航保持可达|PC各区有界滚动；手机内容窗/软键盘/安全区，无双滚。|
|N10 按钮起点铺满全屏菜单|有条件；手机任务浏览器|窄屏菜单从入口扩成可用全屏；键盘打开或减弱动效时直接到完整终态|PC顶部通栏/键盘；手机点击任务菜单；URL与返回来源一致。|
|N11 封面飞出放大翻面|有条件；可图像化的证据来源预览|仅真实证据封面到详情可试飞出翻面；不得翻走唯一风险/来源正文；无封面不造图片|PC证据并览；手机同源抽屉/阅读面，恢复来源锚点。|
|N12 长按序列确认|有条件；规则删除/账户重置的确认面|可中止按住进度只作附加确认方式；等价键盘按钮/权限/幂等和备份要求保留|PC把手/键盘替代；手机点按管理/完整触点，失败复位。|
|N13 旋钮转盘|不采用；精确数量、价格、风险阈值|旋钮降低中文输入和精确录入效率；当前不承担参数/金额的唯一输入|PC/手机均不进入本次实现。|
|N14 前后对比分割滑块|有条件；同对象同轴同尺度的两份真实证据图|只有可比较源图存在才试分割滑块；日期/来源双标；不能用不同窗口图暗示因果|PC证据并览；手机同源抽屉/阅读面，恢复来源锚点。|
|N15 加号弧形快捷菜单|不采用；主导航/模拟交易|弧形隐藏菜单增加发现和误触成本；主任务和金融命令保持明确可见入口|PC/手机均不进入本次实现。|
|N16 滑动 80% 确认|有条件；可撤销的提醒归档/清除确认|滑动达阈值后发原命令；未到回弹；同处提供按钮和撤销，不替代服务端确认|PC把手/键盘替代；手机点按管理/完整触点，失败复位。|
|N17 长按浮起预览|有条件；手机机会短预览|长按浮起仅是点按详情的补充；纵向移动取消长按交还滚动|PC证据并览；手机同源抽屉/阅读面，恢复来源锚点。|
|N18 拖到分组吸入|有条件；已有自选分组间移动|拖近真实分组时提示目标，松手提交原分组更新；失败复位；原Select移动保留|PC把手/键盘替代；手机点按管理/完整触点，失败复位。|
|N19 分段阅读进度|不采用；新闻/研究证据阅读|不把连续正文改自动播放图集，不以阅读时间伪造完成进度|PC/手机均不进入本次实现。|
|N20 中心轮播同步背景/标题|不采用；实时行情/核心结论|中心轮播会隐藏同时比较的信息，环境换色会扰乱金融语义|PC/手机均不进入本次实现。|
|N21 液态玻璃导航|默认；顶栏/搜索展开层/轻选择器外沿|网页液态近似集中低面积入口；字层不扭曲；真实背景移动检验位移，实体退化可读|PC局部光学层；手机按浏览器能力实体退化，字层不折射。|
|N22 封面取色环境光|不采用；行情与事件正文|当前缺真实封面消费者；不从涨跌图取氛围色覆盖风险语义，不造封面|PC/手机均不进入本次实现。|
|N23 景深分层滚动|不采用；实时内容滚动|不对行情、证据正文做分层视差与持续模糊；模态背景退后由N63独立承担|PC/手机均不进入本次实现。|
|N24 圆柱卷收列表|不采用；提醒/订单列表|圆柱卷收牺牲行列可扫读、边缘内容及键盘定位|PC/手机均不进入本次实现。|
|N25 液滴粘连拖拽|不采用；未读徽标|液滴拖拽没有明确新任务收益且容易误清未读；用显式已读/清除命令|PC/手机均不进入本次实现。|
|N26 点击/加载/成功边缘反馈|默认；所有异步操作按钮|按下短边缘反馈、等待有界循环、成功终态三者区分；关闭后动画停止|PC/手机同位回执；禁用/失败/unknown可读，布局不跳。|
|N27 文字与数字状态过渡|默认；低频未读数/任务计数/状态文案|新旧计数竖向切换且播真实整数；实时价格直接显示目标数，不插值造数|PC/手机只过渡真实低频整数；屏读与失败口径完整。|
|N28 标题→内容→动作节奏|默认；Read模式复盘/研究阅读面的首次进入|只在Read读面首次进入或实质新增时短交错标题、摘要与次操作；Operate工作台不做页面入场编排；数据到达不等动画|PC/手机可见小批一次入场；减少动态直接终态。|
|N29 卡片前后空间差|有条件；两至三份证据预览|同方向浅纵深，选中前移；容器预留包围盒，不能压住相邻正文|PC最多2–3真实对象同向预览；手机窄屏单卡/清晰并列，若没真实对象不做。|
|N30 动效自然结束|默认；全部采用的动画|定义成功、取消、关闭、重入和中断终态；快速反向时从当前状态继续|PC/手机快速反向、中断、取消与卸载均回到明确终态。|
|N31 按钮生长式弹出|默认；搜索/加自选/轻筛选/证据入口|面板从实际入口共享容器展开，反向归位；找不到源时使用淡化退化|PC从真实入口就地展开；手机输入整行/弹层安全区内，键盘可达。|
|N32 按钮收圆旋转再撑开|有条件；非金融危险的受控保存按钮|真实pending可由按钮收圆再恢复；错误/未知保留可读结果；不改变布局宽度|PC/手机同位回执；禁用/失败/unknown可读，布局不跳。|
|N33 轮播按拖距与速度翻页|有条件；少量非实时证据预览|距离或速度决定翻页，提供上一份/下一份按钮；不同来源始终可辨|PC证据并览；手机同源抽屉/阅读面，恢复来源锚点。|
|N34 同批列表→网格语义重排|默认；机会集合列表/网格|与N08同一实现；标题/指标/动作在各终态保留，FLIP不重建对象|PC保对象/焦点自然重排；手机单列或网格按实际可读宽度切换。|
|N35 筛选退出/移位/进入三态|默认；机会筛选/市场列表过滤|退出淡出、余项位置过渡、进入短显；结果数量和筛选范围即时更新；无结果可恢复|PC保对象/焦点自然重排；手机单列或网格按实际可读宽度切换。|
|N36 触点倾斜与厚度高光|有条件；非密集证据预览封面|有界3D倾斜与高光跟指针；触摸仅短按预览且竖移取消；图表与表格不倾斜|PC有界指针倾斜；手机纵移取消短按效果，优先原生滚动。|
|N37 400ms 悬浮操作/拖出取消|有条件；手机机会卡片长按操作|与N17共用长按状态机；400ms后才浮起，拖出取消；点击与键盘菜单始终可用|PC证据并览；手机同源抽屉/阅读面，恢复来源锚点。|
|N38 多选整页态变|有条件；自选管理批量选择|进入管理后局部勾选圈/计数/操作条统一出现；退出可恢复焦点；不缩小全文数据|PC把手/键盘替代；手机点按管理/完整触点，失败复位。|
|N39 规律错位|默认；证据/研究主次构图|布局轴允许规律错位；同族操作和表格仍对齐；不用随机偏移伪装层次|PC主辅比例可调；手机按任务顺序重排，信息完整。|
|N40 主图主导|默认；工作台K线/分时|真实绘图区主导，工具紧凑；分别测Canvas宽高、坐标和右栏，短屏次说明内部滚动|PC/手机测真实绘图区；磁吸已有点，缩放拖动不影响精确值。|
|N41 非对称主次|默认；市场/工作台/机会主区|主任务大比例、辅助上下文小比例且可调；移动端按任务顺序堆叠不强行七三|PC主辅比例可调；手机按任务顺序重排，信息完整。|
|N42 有意义遮挡层叠|默认；工具层/抽屉/证据预览|通过有意义遮挡表达前后；关闭/文字/邻区边界不被挡；固定层级与焦点栈|PC非模态/模态分清；手机仅合法焦点层/内滚动，保持完整关闭触点。|
|N43 留白与少框|默认；全域数据阅读面|减少重复卡片边框，按内容组留白；稠密表格保留必要信息而非人为删字段|PC主辅比例可调；手机按任务顺序重排，信息完整。|
|N44 扇形折叠/展开同布局|有条件；两至三份可比较证据预览|与E08/N55共享一个折叠比较器；展开后回到清晰并列，手机窄屏改分页/单卡|PC最多2–3真实对象同向预览；手机窄屏单卡/清晰并列，若没真实对象不做。|
|N45 滚轮衰减后吸附|不采用；日期/精确数字选择|当前用明确日历及数值输入；不为惯性效果增加难精确停稳的滚轮|PC/手机均不进入本次实现。|
|N46 弹窗按位移/速度拖回|默认；手机证据/通知/管理抽屉|仅手柄判断位移或关闭速度；内容保持原生滚动；关闭焦点回真实入口|PC按内容用宽面板；手机手柄拖、速度/位移、半屏/全屏吸附和键盘重测。|
|N47 归档弧线飞入角标|有条件；提醒已读/归档反馈|服务端确认后才能飞入计数；失败不改变已读数；减弱动效直接更新|PC/手机只过渡真实低频整数；屏读与失败口径完整。|
|N48 大标题同进度折叠|有条件；长复盘阅读标题|字体/位置/底色绑定同一容器进度；保持文档标题语义；不压缩图表高度|PC各区有界滚动；手机内容窗/软键盘/安全区，无双滚。|
|N49 拖排槽位 + 其余让位|有条件；自选手动排序|拖项跟指针、其余让位、槽位提交；键盘上移/下移与存储失败复位；轮询不能擅自重排|PC把手/键盘替代；手机点按管理/完整触点，失败复位。|
|N50 小图→大图起始测量|默认；真实证据预览到同对象详情|源条目到同对象详情保持连续；有真实缩略图才使用小图到大图，测量源和目标；没有可见源或跨窗口则退化，不造封面|PC证据并览；手机同源抽屉/阅读面，恢复来源锚点。|
|N51 手写采样与平滑|不采用；无手写业务|不新增笔记采样/平滑依赖和空页面|PC/手机均不进入本次实现。|
|N52 未读拖走液态根部|不采用；未读徽标|与N25同一不采用裁定；明确已读/清除与服务器事实一致|PC/手机均不进入本次实现。|
|N53 新页入、旧页退暗|默认；任务路由切换|新任务到位时旧层轻退，不保留旧报价冒充新事实；URL身份/返回来源保留|PC顶部通栏/键盘；手机点击任务菜单；URL与返回来源一致。|
|N54 双行反向图带|不采用；实时市场与资料列表|双行反向图带没有当前消费者，持续位移影响比较和性能|PC/手机均不进入本次实现。|
|N55 扇形牌阵翻面/摊墙|有条件；两至三份证据比较|与N44/E08同一比较器；扇形只作入口，阅读终态并列不挡内容|PC最多2–3真实对象同向预览；手机窄屏单卡/清晰并列，若没真实对象不做。|
|N56 圆柱卡带 + 字距响应|不采用；行情/证据集合|圆柱卡带与字距联动影响金融文本识别，没有当前操作收益|PC/手机均不进入本次实现。|
|N57 环形画廊摊归档墙|不采用；行情/证据集合|环形画廊摊墙不适合稠密数据，无实际画廊消费者|PC/手机均不进入本次实现。|
|N58 扭环带|不采用；行情/证据集合|扭环带需要正反展示且增加GPU/可读成本，无当前消费者|PC/手机均不进入本次实现。|
|N59 Z 轴纵深隧道|不采用；行情/证据集合|纵深隧道将原生滚动改深度叙事，无当前必要消费者|PC/手机均不进入本次实现。|
|N60 按入口方向底部抽屉|默认；手机通知/证据/管理面|入口方向到底部抽屉；桌面宽侧阅读面或内容宽Dialog；安全区内布局|PC按内容用宽面板；手机手柄拖、速度/位移、半屏/全屏吸附和键盘重测。|
|N61 半屏保留背景内容|默认；手机证据摘要|半屏时保留底层来源位置；背景不可误点，长正文可扩展不压成窄条|PC按内容用宽面板；手机手柄拖、速度/位移、半屏/全屏吸附和键盘重测。|
|N62 全屏同面板展开|默认；手机长证据/提醒管理|同一面板扩全屏，圆角/抓手连续变化；保持草稿、请求身份和滚动位置|PC按内容用宽面板；手机手柄拖、速度/位移、半屏/全屏吸附和键盘重测。|
|N63 背景模糊缩小退后|默认；阻断性模态背景|背景轻退后/压暗，模糊有性能与减少透明退化；非模态旁览不禁背景|PC非模态/模态分清；手机仅合法焦点层/内滚动，保持完整关闭触点。|
|N64 两段式弹性出现|有条件；只读证据抽屉入场|轻掀起再落位只用于新开只读层；危险确认、报价内容直接稳定出现|PC按内容用宽面板；手机手柄拖、速度/位移、半屏/全屏吸附和键盘重测。|
|N65 位移 + 速度关闭|默认；手机抽屉拖关|与N46同一状态机；小位移高速度可关，慢拖不足返回合法锚点|PC按内容用宽面板；手机手柄拖、速度/位移、半屏/全屏吸附和键盘重测。|
|N66 拖扩展后吸附档位|默认；手机可扩展证据面|半屏/全屏离散锚点按速度和位置吸附；内容滚动与拖扩展隔离|PC按内容用宽面板；手机手柄拖、速度/位移、半屏/全屏吸附和键盘重测。|
|N67 嵌套抽屉父层退后|有条件；证据中的必要二级确认|优先同面替换/面包屑，只有阻断性确认才二层；单焦点栈、Esc逐层、父面后退|PC非模态/模态分清；手机仅合法焦点层/内滚动，保持完整关闭触点。|
|N68 抽屉长成页面|有条件；长证据进入阅读工作区|同一对象面板扩为阅读面；返回恢复原入口/锚点；不卸载重开|PC按内容用宽面板；手机手柄拖、速度/位移、半屏/全屏吸附和键盘重测。|
|N69 弹窗→成功提示连续收尾|默认；规则保存/任务结果|结果确认后原面连续收尾；未完成任务保留pending/unknown而不是画勾|PC/手机同位回执；禁用/失败/unknown可读，布局不跳。|
|E01 滑动高亮块|默认；真正Tabs与顶部当前页指示|所选色盘的细轨迹及轻底面共享移动；色值在原版选案后确定；不用大块选中底色；与N05同控件族|PC方向键与内容关联；手机紧凑且可达；滤镜不是Tabs。|
|E02 就地展开子菜单|默认；维护工具分组|带箭头原位展开子项，单组展开且语义expanded；不新增侧边主导航|PC顶部通栏/键盘；手机点击任务菜单；URL与返回来源一致。|
|E03 顶部横排 + 下落通栏面板|默认；顶部任务导航|顶部按任务组织下落面；PC鼠标与键盘、手机点击都能进出，跨项不中断|PC顶部通栏/键盘；手机点击任务菜单；URL与返回来源一致。|
|E04 底部停靠栏|不采用；主导航|用户指定顶部导航；不加底部主Dock与顶部重复。若需手机批量操作条由N38承担|PC/手机均不进入本次实现。|
|E05 命令面板|默认；全局命令面板|快捷键/按钮入口、宽输入、过滤匹配、方向键/Enter/Escape；只列获准可达动作|PC从真实入口就地展开；手机输入整行/弹层安全区内，键盘可达。|
|E06 状态点 + 滚动徽标|默认；通知未读/任务状态|真实数字/状态点，读取失败标上次值；已读确认后才收徽标；无休止呼吸不用于稳定状态|PC/手机只过渡真实低频整数；屏读与失败口径完整。|
|E07 Depth Stack Spread|有条件；两至三份证据摘要|与N29同向浅叠牌；展开前后在包围盒内；密集列表不叠牌|PC最多2–3真实对象同向预览；手机窄屏单卡/清晰并列，若没真实对象不做。|
|E08 Fan Spread|有条件；证据比较入口|与N44/N55共用扇形比较器；展开终态无遮挡，窄屏退化单卡/并列滚动|PC最多2–3真实对象同向预览；手机窄屏单卡/清晰并列，若没真实对象不做。|
|E09 Cylindrical Layout|不采用；行情/证据集合|圆柱排布影响同时扫读，没有当前画廊消费者|PC/手机均不进入本次实现。|
|E10 Spherical Layout|不采用；数据集合|球面缺直接任务收益、手机与键盘成本高|PC/手机均不进入本次实现。|
|E11 Helix Layout|不采用；时间序列|螺旋不能代替按真实时间轴排序，无法清晰比较|PC/手机均不进入本次实现。|
|E12 Wave Grid|不采用；数据墙|持续波浪扰动报价与表格位置；不在后台稳定状态制造动画|PC/手机均不进入本次实现。|
|E13 3D 倾斜光影|有条件；非密集证据封面|与N36同一倾斜材质组件；鼠标移开阻尼回正，减少动态禁用|PC有界指针倾斜；手机纵移取消短按效果，优先原生滚动。|
|E14 流体胶囊形变|默认；证券搜索/轻筛选入口|胶囊共享容器连续扩展；文本层不拉伸，内容先就位后显；与N01/N31同一次动画|PC从真实入口就地展开；手机输入整行/弹层安全区内，键盘可达。|
|E15 共享元素无缝展开|默认；机会/事件到同对象依据|共同对象和来源身份贯穿展开/返回；与N50同转场，不嵌套两套引擎|PC证据并览；手机同源抽屉/阅读面，恢复来源锚点。|
|E16 磁吸图表游标 + 数值滚动|默认；K线/分时游标|磁吸真实已有数据点、即时精确读数；缺失分钟不造点；手机手势不拦页面返回|PC/手机测真实绘图区；磁吸已有点，缩放拖动不影响精确值。|
|E17 阻尼弹性底部抽屉|默认；手机底部抽屉|与N46/N60–N66同一抽屉状态机；键盘打开后锚点重测且动作可见|PC按内容用宽面板；手机手柄拖、速度/位移、半屏/全屏吸附和键盘重测。|
|E18 动态弥散光晕边框|有条件；助手真实streaming/pending区域|状态驱动有界微边框和光晕；失败/取消/完成停止；不可用不继续假装处理中|PC/手机同位回执；禁用/失败/unknown可读，布局不跳。|
|E19 物理弹簧交错流|默认；Read模式首次阅读面或实质新增的短摘要|与N28同一短交错；只动画可见小批，Operate无页面入场编排，轮询稳定更新不重播|PC/手机可见小批一次入场；减少动态直接终态。|
|E20 0.96 弹性按压|默认；全域可点击操作|可见面短压缩且命中区不移动；图标居中，真实操作不等待回弹，危险语义独立|PC紧凑可见面；手机44px命中区固定在容器内，按压不改变热区。|
|G01 玻璃面板浮起|默认；通知/轻证据摘要外沿|与N21材质同族；实体正文+液态边沿浮起，实际位移/仅blur退化分别记录|PC局部光学层；手机按浏览器能力实体退化，字层不折射。|
|G02 圆环计数弹出|有条件；未读计数入口到通知摘要|圆环只能表达真实数量/明确进度分母，不能伪造AI完成百分比；无分母不用环|PC/手机只过渡真实低频整数；屏读与失败口径完整。|
|G03 刻度尺扫到今天|有条件；复盘返回今天|刻度扫到实际北京今日，休市/无报告仍显示真实空态；正常日历与键盘选择保留|PC/手机日历与键盘可用；实际北京日期和合法空态准确。|
|G04 照片抽屉贴合|不采用；照片业务|没有照片抽屉消费者；不新增图片内容凑效果|PC/手机均不进入本次实现。|
|G05 点击翻面凭证|不采用；模拟成交/审计回执|关键成交状态/费用/拒绝原因须同时可读；翻面会隐藏必需事实，改原位展开|PC/手机均不进入本次实现。|
|G06 下拉展开面板|默认；通知预览/轻筛选|按钮下落面板碰撞避让、完整文字；宽管理面使用宽Dialog/Sheet不塞小Popover|PC从真实入口就地展开；手机输入整行/弹层安全区内，键盘可达。|
|F01 AI不可用仍按规则筛选提醒|默认；后端规则→提醒意图|确定性规则先筛选和处理新变化/风险，AI只作可选解释；不可用标签明确|PC/手机消费同一意图/revision；刷新重连不反复展示。|
|F02 重复弹出与状态变化提醒|默认；提醒意图/通知/浮泡|事件+规则+对象+修订身份去重；刷新/重连不重弹，同态忽略但升级可再提醒|PC/手机消费同一意图/revision；刷新重连不反复展示。|
|F03 提醒管理布局与完整内容|默认；提醒管理|完整理由/筛选态/AI或规则/时间/质量/真实北京日分母；左右比例和宽度按内容|PC宽列表+详情；手机可用宽/内滚动，完整理由与真实计数。|
|F04 容器横/纵滚动及可见横条|默认；全部工作区/表格/弹层|逐层登记唯一滚动owner；超宽自动横滚并有可操作提示；不隐藏溢出掩盖错误|PC各区有界滚动；手机内容窗/软键盘/安全区，无双滚。|
|F05 弹窗按内容加宽|默认；全部Dialog/Sheet/Popover|依据内容选宽规格；手机安全区全可用宽，正文内部滚动，关闭触点不越界|PC非模态/模态分清；手机仅合法焦点层/内滚动，保持完整关闭触点。|
|F06 明确UI复查标准|默认；全部子功能和状态|路由→动作→组件→状态→视口证据树；DOM/行为/Canvas各自有证据|PC/手机逐动作状态证据；动态需录屏，Canvas独立。|
|F07 重分配页面比例|默认；工作台/市场/机会/研究/维护|测量主次比例和短屏，主要任务优先；同级工具/标题基线统一|PC主辅比例可调；手机按任务顺序重排，信息完整。|
|F08 取消历史回放功能|默认；历史回放UI|取消回放按钮、播放态和唯一组件；保留历史K线/共享历史API/研究和幂等replayed|PC/手机入口一致退出；共享历史行情/研究/幂等继续工作。|
|F09 MA5/MA10等统一按钮|默认；K线MA5/MA10等指标|真正button+aria-pressed；统一紧凑选中/未选/disabled/focus样式，不伪装内容Tabs|PC/手机均为Button/aria-pressed；Tab聚焦、Enter/Space切换指标，不套内容Tabs的方向键与面板关联。|
|F10 全新风格与组件组合|默认；全域新视觉和组件组合|旧版作为恢复/反例；选定新方向后迁全域，不只换玻璃颜色和保留旧信息堆叠|PC与手机同时呈代表组合；用户选案后才迁全域。|
|F11 保留当前版，退出旧保存版|默认；现有UI恢复副本|只保留当前活动UI恢复点，删除此前活动保存版；恢复时记透明度问题且不复活旧融合技能|PC/手机恢复同一UI基点；不覆盖业务数据或复活旧技能。|
|F12 每个skill独立职责完整发挥|默认；各原版技能职责|各自输入/流程/产出/验收独立记录；不适用明确理由，不以全部命令运行作目标|PC/手机职责与适用边界各有证据；不适用项写明确理由。|

同义合并示例：N08/N34同一列表网格重排；N17/N37同一长按预览；N36/E13同一微倾试件；N44/N55/E08同一扇形比较器；N46/N65/E17同一抽屉；N25/N52共同不采用。
