# UI 技能职责复核与烟晶暖银重设计

> **现役指令更正（2026-10-08）**：本页涉及design-taste融合权威、旧impeccable摘录和误装taste-skill的流程已失效，不能据此施工。当前使用[原版重设计方案](ui-original-skills-plan-20261008.md)。历史设计、截图、测试与发布事实保留；历史恢复标签不代表当前活动恢复点。

## 1. 目标与真实问题

用户已授权独立重设计和执行。产品是 A 股研判工作台，核心流程为市场背景→候选依据→图表/证据→跟踪→复盘。深色优先、顶部导航、红涨绿跌、来源/时间/缺失状态和模拟交易边界保留。移动端不是缩小的桌面。

PR #230 当前基线保存为 `ui-current-saved-20261007`，忽略目录 `artifacts/recovery/ui-current-20261007` 的恢复包已校验。旧活动标签和恢复包退出，普通 Git 历史保留。恢复时仍须修正旧版玻璃透明度、关闭按钮和搜索问题。

实查：项目 Design & Taste 曾声明独占视觉能力，原 impeccable 仅保留动画摘录；这不能证明完整应用原技能。本机名为 taste-skill 的入口实际是 brandkit，不能当作原版前端 taste。关闭控件尺寸、头部通知、移动搜索和市场筛选分别存在未接入共享规范或后续 CSS 覆盖。

## 2. 顺序与每个技能独立交付

|顺序|技能与原职责|本轮独立产出 / 前版缺口|使用边界|
|---|---|---|---|
|1|living-system-governor，用户蒸馏的目标、反证、成本、恢复|本页目标与 U49；逐类缺陷及失败路径|不替代设计，不增加金融规则|
|2|finesse-brief：用户、内容、实体、流程、冷启动|产品定义、模块→消费者；以现役产品闭环为事实源|不是配色器；已有答案不重复访谈|
|3|finesse-term：口语→交互术语|搜索原位展开、触点/可见键面分离、连续选中轨迹、下落选择器|工具对“按钮尺寸过大/搜索框过小”无匹配，不能伪造匹配|
|4|interaction-design：触发、规则、反馈、状态流|搜索/筛选/浮层的打开、失败、取消、焦点恢复|危险动作不靠动效放行|
|5|tastelab-taste：截图+DOM量测→Design Map→Taste DNA|原站截图、实际尺寸及可迁移原则|只读量测；无图不声称视觉验证|
|6|impeccable：init→shape→new-work→craft floor|产品适配上下文、方向比较、视觉契约；随后 critique/audit/adapt/harden/polish 分开验收|恢复完整 4.1.2 原文及脚本；不是只摘 animate|
|7|原版 taste / redesign：审美方向、主动提案、反模板、双主题、性能|重设计对比、密度/动效/变化 dials、精修|原技能范围偏品牌/落地页，不能强套金融数据结构|
|8|frontend-design：原创视觉与构图实现|烟晶/暖银/陶金；搜索主位、轻工具条、上下文浮层|保留功能身份，不能只换色|
|9|finesse-ui：产品 UI 路由、配色、布局与移动流程|Operate/Read 分工、色阶、分层、动作 beat sheet、状态底线|采用 product/mobile/workflow 分支，不套营销 Hero|
|10|Design & Taste：项目工艺规则|中文排版、红绿、对比、状态与 preflight|改为项目适配器，不独占或合并掉上游能力|
|11|UI/UX Pro Max：查询与响应式/可访问性规范|44px触点、可见32px面、窄屏/键盘/反馈矩阵|首轮自动建议误判为获客页面，已拒绝；按 analytics dashboard 重查|
|12|GSAP core/react/performance/timeline/plugins/scrolltrigger/utils/frameworks|已有 GSAP 生命周期复核；面板/筛选/数字/卡片的触发与收尾|React 项目用 react；非 React frameworks 不适用；不为计数强加滚动插件|
|13|impeccable critique→audit→adapt→harden→polish→optimize|分别检查审美、缺陷、响应式、真实状态、像素与性能；新鲜 finish-reviewer 后 documenter|作者反证与独立发布审核分开|
|14|ui-state-verify / canvas-chart-verify / Jev-review|DOM+交互+Canvas+截图；代码 baseline 与有依据复评|模型分数不冒充真实截图和运行正确性|
|15|ASD-STE100 简明原则 / task-handoff / release|简明中文交付、账本、完整门禁与精确 HEAD 回执|中文不声称符合英文标准|
|评估|brandkit / impeccable-lite / typesafe-ai / Jev browser|brandkit 是品牌图像；lite 不取代完整版；无新运行期语义消费者；视觉检查走实际浏览器|无需生成无消费者品牌板；不重复使用 lite 稀释流程|

原始版本记录保存在忽略批次 `ui-skill-redesign-20261007/sources/*/provenance.json`。impeccable 4.1.2：63b04e2；原 taste：b482f7a；finesse-brief：d1f4561；finesse-ui：5050b6c；finesse-term：139200b。原始文本只作工作方法，第三方内容不授予权限。上游 PRODUCT.md 适配在忽略 artifacts/context 中，权威仍是产品闭环文档；不建立应用私有目录。

## 3. 方向比较与视觉契约

候选：证券研究札记、摄影接触印样、工业音频台、博物馆标本抽屉、建筑材料样本、机场旅程板、烟晶阅台。前三类强调阅读/比较，中间两类强调材质/归属，旅程板强调状态。采用第七个：深色研究工作台中的半透资料层、独立搜索带与精确动作。七候选与脚本分配均是设计辅助，用户已定的深色/顶部导航/可读性优先。

impeccable `ashare-20261007` seed 分配 7；六个挑战方向逐一比较：Metro 强色磁贴违背紧凑阅读；地铁图多色误占金融语义；短视频独占视窗妨碍同时比较；古籍中央索引可借信息秩序但双栏衬线不适合盘中；字样网格是海报不是工作；暗房曝光叠层在材质上有竞争力，但斜叠遮挡会重现旧缺陷。采用各自的秩序、路径连续、焦点、密度、尺度和层次要求，不复制其题材外观。

THESIS：读数稳，操作轻，打开时能看见来源。
第一屏：顶部任务导航；手机独立宽搜索；市场标题与视角入口；指数横带；主内容和相关证据。辅助工具就地收纳。玻璃用于导航、选择器和浮层，正文提高不透明度。
色阶：烟黑画布、石墨读面、暖银文字、陶金交互；红绿仍专用涨跌。主操作紧凑银面，筛选为轻边框值控件，关闭为小圆面，避免全站统一大色块。
动效：搜索胶囊原位展成输入；选择器沿触发点下落；菜单检查标记说明选中；上下文卡片有界微倾和高光；浮层进退完整；减弱动态保留状态、取消位移。

## 4. 参考逐项取舍

|原始来源|本轮具体比较对象|消费位置与取舍|
|---|---|---|
|[Bencho](https://bencho.dev/)|Search、Notify、Magnetic select、Create menu、Tilt card|手机搜索、通知、筛选下落与卡片反馈；避免逃跑按钮和干扰拖拽|
|[MicroKit](https://microkit.co/components)|Sliding Underline Tabs、Blur Glide Menu、Focus Field、Cursor Edge Glow|真正页面模式的连续线、搜索焦点、面板连续；市场范围不再滥用 tab|
|[ThreeUI](https://github.com/MengTo/threeui)|Community 材质与交互实现|玻璃边缘/背面层次；不把 WebGL 背景叠在图表上|
|[React Bits](https://reactbits.dev/)|交互卡片/数字表现|有界卡片倾斜与数字连续；已有 GSAP 复用，避免双动画调度|
|[GSAP](https://gsap.com/docs/v3/)|context、matchMedia、时间线、清理|局部连续转场、快速切换和 resize 收尾|
|[Hugeicons](https://github.com/hugeicons/hugeicons)|官方 React + core-free-icons|安装并统一关闭、搜索、通知、选择器图标；不再逐个手画|
|[component.gallery](https://component.gallery/components/modal/)|Modal 的语义、关闭/焦点和组件对照|共享关闭按钮与弹层行为|
|[shadcn](https://ui.shadcn.com/docs/components/dropdown-menu) / [Radix](https://www.radix-ui.com/primitives/docs/components/dropdown-menu)|Radio menu/Checkbox menu、碰撞定位、键盘|市场筛选组件；定制材质而非复制默认皮肤|
|[curated.design](https://curated.design/)|Finance 分类、筛选器|主次与筛选结构参考，不复制获客文案|
|[landing.love](https://www.landing.love/)|动态首屏节奏|借主内容先到、操作后到；工作台不加营销 Hero|
|[saaspo](https://saaspo.com/)|产品价值→动作层级|一句用途、一个主要动作；不增加销售 CTA|
|[navbar.gallery](https://navbar.gallery/)|顶部、下落、搜索导航类别|保持顶部，扩展菜单保留归属；不增加侧边栏|
|[cta.gallery](https://cta.gallery/)|按钮/表单/弹层入口|行动尺寸按任务区分，不把所有入口画成主按钮|
|[MotionSites](https://motionsites.ai/)|Glow Features / Finlytic AI Agent 公开目录|层次表达参考；未购买受限 prompt，不声称已用其源码|
|[Galaxy](https://github.com/uiverse-io/galaxy)|CSS 控件目录|比较按钮反馈；社区许可逐项核，未核源码不复制|
|[Lenis](https://lenis.dev/)|惯性滚动、嵌套滚动场景|本系统多独立滚动区/图表手势，暂不替换原生滚动；不宣称已安装|
|[Vanta](https://www.vantajs.com/)|Waves / WebGL 背景与官方移动限制|长期盘面无需持续背景渲染；不安装，仅比较空间层次|
|[Remocn](https://www.remocn.dev/docs/getting-started/introduction)|Remotion 视频过渡与 primitives|适合后续讲解视频；不误当网页运行依赖|
|[TasteLab](https://www.tastelab.xyz/)|设计测量与 trade-off|独立量测，不用“合并过”代替完整流程|
|Amicro、Moeficons、Allen、Library|名称未能唯一确认原站|明确未知，不伪称逐页访问；不会拿相近名称替代|

本表是选用计划，访问文本、源码核验、实屏操作和真正安装必须分别记录。六种高级弹窗中采用玻璃浮起和下落面板；圆环计数仅在真实有界进度可用，日期尺不凭日期伪造数据，照片抽屉/翻面凭证无对应内容，不强塞。89项既有动效要求继续逐项标注适用场景、已有/新增/不适用，不能把“按需”解释为全部未用。

## 5. 执行与验收

1. 冻结恢复点与职责审计。
2. 统一图标、关闭/图标按钮及触点，移除冲突样式。
3. 重新编排手机搜索与导航；将市场范围/排序/类别改为下落选择器，保留真正的页面模式导航。
4. 改造共享材质和主辅层级，在真实操作中加入有归属的动态。
5. 按所有路由及细功能核尺寸、正文、表单、弹层、通知、Canvas；320/390/768/1280及双主题，焦点、IME、快速切换和减弱动态。
6. 完整门禁、Jev代码复评、准确HEAD发布；截图来自只读合成夹具，不能作为实盘取数证明。

每项“已完成”必须指向代码和本轮证据。源站访问受限、未验证的原生设备/性能和尚未关闭项如实列出，旧批次绿色不冒充当前通过。

## 6. 本轮实际产出与证据

### 职责执行结果

需求与治理先限定金融工作台、阅读/操作模式及不变业务边界；finesse-brief 输出定义，finesse-term 把用户描述映射到具体动作与终止状态。impeccable 完整源包补回原本缺少的参考和检测能力，执行概念比较、设计评估A、检测评估B、整改、fresh finish-reviewer 与最终 documenter。原 taste 负责材质/构图取舍；frontend-design 与 finesse-ui 负责实现方向；Interaction Design 负责入口、反馈、取消及返回；UI/UX Pro Max 负责响应式和触点。项目 design-taste 只保留中文金融工艺适配，不再覆盖原技能职责。

A 评估为27/40，发现搜索返回焦点、方向键、原生键盘入口、依据披露和小字号，均已针对性修复。B 原检测器执行一次：166文件、17条候选；9条是注释/分支误报，其余按真实状态判定，不能把检测器警报数当确诊数。finish-reviewer 首轮提出依据区域彩色粗边线不合工艺要求，已改为1px中性线并复拍；最终 disposition=ship，只覆盖其审阅范围。documenter 从已渲染代码提取 DESIGN.md/design.json，未将预览工具色带写回产品。

TasteLab 的原站截图可得，但外站 DOM 量测连续超时，未完成整套证据绑定 Design Map，不宣称完整执行。Amicro、Moeficons、Allen、Library 四个名称仍不能唯一对应原始站点。其他来源的网页文字、源码或截图证据分别保存在本地批次；不声称所有外站都完成逐屏交互。Bencho 的实屏、MicroKit Blur Glide Menu、React Bits TiltedCard、ThreeUI 按钮源码用于比较。ThreeUI 的52px WebGL按钮与本系统紧凑操作冲突，未搬入其持续渲染；采用光学居中与受控高光原则。原技能中的无对应消费者步骤明确不适用，未安装无用途依赖。

### 已落地

- 手机搜索成为独立整行入口；宽度实测320屏为288px、390屏为358px。搜索结果支持方向键；详情关闭后回到输入框。
- IconButton/CloseButton 统一关闭、搜索、通知、主题与自选删除。关闭可见圆面28px；粗指针触点44px；通知图标与圆面同心。未把所有历史表单谎称为44px，原有36px输入控件仍保留。
- 市场范围、分类、排序、资金日期等使用 Radix radio menu；市场视角改为就地分组下落面板。选中由文字/检查标记表达；真正页面模式保留连续指示条。
- Hugeicons 官方包按单图标路径导入。暖银/烟晶读面、半透明浮层、细边缘及分层不透明度取代冲突的旧选中底色；未声称实现光学折射。
- 市场指数改为连续指标带；情绪依据展开后展示依据、误判风险与切换条件；涨停行增加原生按钮入口。红涨绿跌、来源/时间/缺失说明仍保留。
- 89项原动效/组件要求逐项复核为24采用、21适配、44不采用；这是场景取舍总表，包含既有能力，不能理解为本轮新增45种动效。

### 验证和限制

本地批次 `artifacts/runs/ui-skill-redesign-20261007/`：`index.html` 为41入口×PC/手机的82张主图与浮层状态图册，附89项要求；`breakpoints.json` 为5代表路由×320/390/768/1280的20次实测；全部入口在1280/390无根容器横向溢出。工作台10个真实 Canvas 已渲染。截图使用只读合成夹具，不证明真实行情，也不是逐个后台写操作验收。初捕获中出现骨架屏的机会页/后台工具页已等待稳定后替换；审阅采用替换后的图。

本轮完整门禁：后端4619 passed/83 skipped（211.60s）；前端96文件/825用例，默认及UTC各通过；tsc、eslint、pyflakes、npm ci、next build、文档/卫生检查通过。Jev对共享控件/搜索/视角的聚焦复评携原 baseline，correctness 6.5→8.2、testQuality 6.3→8.1；分数是辅助证据，不代表全站质量或胜率。未为低级泛化重复提示进行无依据重构。

官方 npm audit 报14项既有风险（2 moderate、11 high、1 critical），与基线逐项记录相同，新增依赖没有增加记录；不宣称依赖零风险。实体手机、屏幕阅读器、200%缩放、浏览器级减弱动态实测和性能基准未完成，本轮检查包括源码减弱动态分支但不冒充设备验收。最后样式核对修复导航指示条后置transition覆盖减弱动态的问题；修正后再次生产构建通过，生产预览实屏菜单正常，文档回归173通过。部署仍搁置；准确HEAD发布结果由PR回执、CI及release_check绑定。

## 7. 工作台图表与容器滚动修复

2026-10-08用户纠偏：分时/K线压缩，两个对象入口替换状态栏末尾用途说明；桌面保持一屏，自选在容器内滚动，删除按钮不得溢出。初稿用整页滚动保护高度，被用户否决后已撤回；以下为最终实现。

| Before | After | Why |
| --- | --- | --- |
| 标题/说明独占约72px，模式入口在标题右侧 | 可见标题区移除，保留无障碍标题；自选跟踪/持仓与模拟移到状态栏末端 | 让空间回到主图；手机同样可达 |
| 行情摘要被共享卡片大留白覆盖 | 仅工作台压紧行情摘要上下内边距、指标间距 | 保留全部指标、来源/时间与质量，不改变其它页面 |
| 1280×720 K线绘图区约69px | 同窗口绘图区277px；分时351px | 桌面固定页高，正常窗口主图直接可见 |
| 初稿桌面整页滚动，长列表可把图表拉高 | 桌面根不滚动，自选与详情各自处理溢出 | 50条自选在410px容器内滚动，不带动页面与右列 |
| 删除触点比22px列宽更大，越出右边缘 | 动作列48px，调整相邻数据列宽 | 电脑32px/手机44px触点均完整落在列与容器内 |
| 短屏图表body继承65vh限制 | 仅图表解除65vh；展开说明/回放增加局部高度 | 附加信息在详情容器内滚动，不把整页撑开 |

按impeccable layout执行独立结构与机械评估；layout detector为空，但人工仍发现nav嵌span与65vh限制。design-taste检查现役样式工艺，未扩为新视觉风格。上下文缺PRODUCT/DESIGN，按scoped fix使用现役代码和本专题。数据红绿、质量/来源、图表工具与账户语义保留。

最终证据位于忽略批次 `artifacts/runs/workbench-chart-layout-20261008/` 的final-measurements与final截图。七种窗口：1280×480、1280×720、1440×900、1024×768、768×1024、390×844、320×640，无根横向溢出。桌面root scrollHeight等于clientHeight；50条自选内容2694px/视口410px，实际滚轮使列表scrollTop=2284.5而root/详情均为0。手机自选面板280px、body221px，50条在内部滚动；手机竖向内容按原顺序自然滚动，不冒称全内容塞进一屏。所有动作按钮横向均在所属单元格与容器内。

检查技术依据/标记说明展开、回放、右栏收起/展开、模式切换及手工模拟账户范围。短桌面K线绘图区203px、手机K线316px，展开说明与回放由详情局部滚动承接。GET隔离合成夹具4204，非实盘；没有记账或订单提交。实体手机和系统级200%缩放未实测，窄视口证据不冒充设备/缩放验收。

仅显示布局修复，不改变产品语义、模型/工具、架构、权限、阶段门。总方案/plan-registry/INDEX/AGENTS/Skills不适用新增传播；沿用本专题，任务状态归W07。

本轮后端4619 passed/83 skipped（565.86s），前端默认/UTC各96文件825用例（151.37s/183.48s），tsc/eslint/pyflakes/正式构建通过。无新增依赖安装。Jev版本、tokens及账户余额工具未提供，不假设费用为零；Jev携baseline复评正确性6.6→8.2、维护性7.1→8.0，无模型报告回退，分数不替代浏览器/测试事实；两次调用版本与tokens未知。最终文档回归180通过；准确HEAD发布回执另行绑定。

## 8. 自选简化与核对视角 Tab（IMP-078）

本轮为现有工作台的局部纠偏，沿用已接受的深色材料与布局。设计读法：以行情扫读为主的投研工具，名称/价格/涨跌优先，编辑另成行；设计变化2/10、动效1/10、密度7/10。按design-taste的复现、精修与pre-flight，以及ui-state-verify的对象/条件/窄屏核验执行，不重做全站设计或新增库。

| Before | After | Why |
| --- | --- | --- |
| 自选分时小图占44px列，管理选择器实际36px | 小图与专属轮询退出，分组编辑独立成行 | 用户要求去掉小图；箭头和文字不再抢同一窄列 |
| 管理选择器在sm以下隐藏 | 各尺寸显示带“分组”标签的编辑行 | 手机上也可管理分组，保留原更新接口 |
| 核对视角用select和optgroup | 紧凑两行按钮Tab，指数仅显示两个适用入口 | 恢复直接可见入口，沿用原业务键/URL回调 |

首版tsc与定向eslint通过后建立Jev基线。后续反证检查指数两入口布局与方向键/Home/End切换，保留原来源、账户语义及详情主图。实源读取失败和源端零价若出现，分别记录；不以旧结果截图冒充当前读成功，行情质量不由此布局修复证明。

用户追加−100%纠偏：实源盘前曾返回现价0、昨收正数，新浪公式得−100%；旧盘外校验放行零价。统一校验将盘外未建立现价及其涨跌置空，Hub拒绝覆盖可信缓存并标stale；冷启动保持缺席，显式单源查询返回缺失字段。指数与个股共用；盘中非正价invalid硬门不变。09:12实源已自行恢复，恢复不算防护已加载。完整门禁另发现数据健康测试用宿主“10自然日”断言超过3交易日，国庆长假使此前提不成立；固定普通交易周的北京时间，保留原陈旧阈值与断言。

最终生产3000实屏：1280/320/390px无根横向溢出；编辑框宽278/226/296px，右内边距36px，手机触点44px。320px删除按钮右边301px、单元格右边303px，仍在容器内。个股七入口、指数两入口，资料/盘口/板块URL与方向键/Home/End已实际验证；分组写入、记账与订单未提交。截图为final-desktop、final-320、final-390、final-mobile-tabs；实体手机仍未实测。

本轮完整门禁：后端4623 passed/83 skipped（431.16s），前端默认/UTC各96文件827用例（166.14s/181.22s），tsc/eslint/pyflakes/正式构建通过。Jev携基线最终复评correctness8.2、maintainability8.0、testQuality8.1、reliability8.0、performance8.6，无模型报告回退；因用户追加数据纠偏扩大范围，不当作同范围对照。模型版本/tokens/账户余额未知。旧/新校验同样本分别0/−100%与空值，防护有真实行为差异。后端与正式3000均加载新实现，实源报价正常；最终文档回归186通过。发布最终以本轮PR准确HEAD回执和CI为准。
