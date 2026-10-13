# 当前交接：IMP-019 系统质量与短阅读整改

> 总方案v9.13；累计U01–U55。阅读与WS整改[W07/IMP-093](stages/w07-simplification.md#imp-093)已由PR #270发布并运行；直接上游单位反例按[W00/BUG-020](stages/w00-foundation.md#bug-020)既有同根回归条款止险，不启动第二个产品优化任务。

现役语义判断依[semantic-decisions](ai/semantic-decisions.md)；外部能力依[开放世界持续演进](ai/continuous-evolution.md)筛选。

## 1. 现场、授权与最新验收

- **当前主门**：G4
- **主切片首选**：IMP-019
- **候选顺位与执行边界**：IMP-019；此前清理/读链/指数纠偏已由PR #272/273发布并加载。本轮用户明确扩展系统根因、DeepSeek、依赖积压、选股、原因/详情/通知及指数对齐。
- **基点与分支**：origin/master `e4356ef63f3f6b5b804d0f057cbaec1119c9b8fb`，`codex/system-quality-fix`；主树两份未跟踪evolution保留。
- **获准范围**：完整系统根因读查与已复现修补：归因主线索/neutral journal、详情驱动抽屉/右列Tab/做T退出、通知短阅读/正常BJ时钟/指数对齐，以及六依赖PR的兼容处置。保持原模型、源序、预算、时效、完整性、选股公式及通知资格；不恢复Jev/Decisions/KEV/REA。
- **实际运行基线**：2026-10-13 20:27已激活PR #273/master e4356ef6：8000单实例PID83948、3000生产构建fOC5Y95YZY2NBhJiZBsft/PID84873。PR及post-master三job均成功、功能分支清理；本轮质量整改尚未加载，新准确HEAD发布和运行切换另录。
- **证据状态**：20:51盘后纯读providers/health/metrics，指数完整覆盖且方法累计332进入/332返回/0fail，但不是新窗HTTP分母或新版效果。当前最终本地后端5278 passed/81 skipped/112.06s；前端默认/UTC各1510通过/134文件（98.11/101.23s），tsc、ESLint（0error/2既有warning）、Next16.3.8生产构建通过。真实组件桌面/390/320已验；准确HEAD CI/运行加载尚待发布回执。[报告§14](system/runtime-performance.md#14-2026-10-13-系统质量归因与阅读整批整改)保留各处根因/改法和未测条件。
- **U50 降级授权回执**：2026-09-24 DEGRADED_FULL_CONTROL未撤销；作者自审和协作反证不冒充独立网页Review。每个PR准确HEAD另有DegradedRelease。

## 2. U49 主动审计回执 / Preflight

原版Impeccable按Operate/clarify/harden核信息、状态和触屏；Leon原版redesign按Scan→Diagnose→Fix核既有产品，不强套营销页；Interaction Design核焦点、退出、抽屉、不冒泡和阅读连续性。Governor区分工程、真实运行与前向效果，不用Stars或新依赖代替收益证据。

先核现役e435基线、纯读health/provider/metrics/库记录；不追加生产选股、模型、消息、WS或回补。明确LIMIT前有效资料/公司关系、来源与版本绑定、neutral理由留痕、窄否定反例及未知时间；右侧Tabs/抽屉保持图表空间，通知完整条件一次可读。退役T专属读写和导航，旧数据/迁移及通用日内功能保留。六旧PR全部为依赖，旧CI不当新准入；failed Tailwind4不盲合，采用当前消费者兼容patch并保留braces残余。

第一轮协作反证补齐foreign输入版本、纯价量/天气驱动边界、过期/传闻/观点挤占、触屏节选提示、非主neutral单条可见性、普通时间及右Tab旧display覆盖。关闭已复现反例不代表全量财经判读准确。工程完整门失败先改新时区fixture，保留原权威守卫，不贴豁免。作者及协作者检查不是独立网页Review，准确HEAD另走DegradedRelease。

全批计划与长证据在忽略artifacts/runs/system-quality-20261013，现役根因及各位置见[报告§14](system/runtime-performance.md#14-2026-10-13-系统质量归因与阅读整批整改)。实际组件Canvas/桌面/320/390与全门/发布/运行分层取证。IMP-019真实新版交易窗、分段尾延迟/源龄/锁线程/设备trace与人工gold不伪写完成；当天下午错过且临时跟进已停用。

## 3. 验收与恢复

代码提交f73a9651：完整后端5227通过/81跳过/478.61s，pyflakes通过；前端默认与UTC各1428通过/132文件（112.40/171.55s），tsc、ESLint（既有1 warning/0 error）、Next16.3.3生产构建通过。文档守卫180项、卫生、公开扫描已通过；最终回填另复验文档门。原版设计反证的跨行连线、嵌套原因及长回复菜单问题均关闭；不是独立网页Review。PR #270准确HEAD发布及正式运行取证已完成，见§5。构建pjXxp4V2Z0WlEgJ4WGHPj的隔离3105接现役8000：30秒握手/子协议/snapshot/pong正常、6个stale帧、18标的、无中途断线，无凭据403。收盘数据仍stale，不掩盖源质量，也不证明未来交易窗无卡顿。正式应用PC/390原因弹层核完整原句、来源、交易日与374px容器；隔离真实公开新闻PC/320/390与助手展示夹具分开标注。3000已完成PR #270构建切换；该PR后端无代码改动，单位纠偏另按§5有条件激活。保留旧构建与PR恢复点，按本次Git差异回退阅读表现；源数据、已有归档、选股与消息硬门保留。不恢复REA/Jev/Decisions/KEV。既有INDEX和产品闭环§45沿本任务更新，无新业务页面/模型/数据源/权限，不产生新的重大准入决定。

## 4. 交易时段跟进边界

本机官方交易日历覆盖2026-10-13，盘前不能当连续交易证据。用户授权下建立仅当天09:35/13:35两次的线程跟进，每窗先冻结实际运行版本/负载，再只读观察5分钟；本机应用与前后端需在线。参数/脚本哈希与现场证据在忽略的`artifacts/runs/live-performance-20261013`，状态仍归W06；第二次后停用，不产生永久ping。

现有health/metrics/caches/providers/schedulers读面已盘前实发5次200，没有触发采集/模型；交易窗collector盘前拒绝且0请求。使用Hub批报价不主动回源/新增订阅；同股分钟/日K有界读取。保留额外观测请求、失败和unknown。滑动1h分位/count不能直接差分成窗口分母；缓存factory不等于供应商HTTP；图表meta属Hub，日K日期不是秒级源钟。StockDetail冷读可能自动调用摘要模型，不为测试额外激活。没有浏览器性能trace就不宣称刷新无卡顿。确认问题后再按完整工程/发布门最小修复，不改TTL/轮频/freshness、选股或通知规则。

## 5. 本轮运行反例的事实止险

PR #270已精确HEAD发布、post-master三job成功；3000已加载pjXxp4V2Z0WlEgJ4WGHPj，后端94616未重启。正式3000/本机LAN HTTP200，WS45秒三次pong/快照及源状态帧，无中途断线，无凭据403；收盘stale保留，不证明盘中SLO。

15:49:56 THS429→EM揭露旧ZT价格/100错：官方15:53:16 qdate20261013的002343 p7360应7.36，原输出73.60；THS/腾讯缓存一致7.36。代码与错误说明/测试同步修正/1000；来源工具输出摘录及实际provider failover回归先红2项，修后46项和完整门通过，准确HEAD发布/单实例后端激活待验；摘录不是完整upstream raw，不伪造rawhash。现有UI/WS保留，不回填旧归档或把行业补成原因；EM缺原因与67/70池差明确unknown。

PR #271/075f49d0首次CI单项助手sources为空，其他5228 passed/81 skipped；本地同HEAD助手4worker56项通过。只读反例确证base id(stocks)缓存不保留名单引用，空名单地址被复用可漏识别新名单；CI未记录id，无法锁定唯一因果，不能冒称纯环境波动。改真实缓存身份并保留原断言，新HEAD另跑完整门与CI；旧075f49d0发布回执不得用于新版本。

缓存身份新增3项先红；修后连原助手59项通过，最终完整5232 passed/81 skipped/187.63s，前端默认/UTC各1428（171.13/153.30s）及构建通过。单份真实名单缓存用is核验，新名单退休旧名单；无O(n)内容副本。只读团队反证无新增阻塞，新HEAD另走准确CI/DegradedRelease/post-master与后端激活，不将旧回执转借。


## 6. 当前实施的反证与传播

根因来自本轮冻结的真实日志与隔离反例：整轮gather丢正常域、启动重复、旧图无错误出口、当日题材参照漏刷新、历史数据过度轮询和消息条件失真。修补按原owner完成，不引入独立事实库/架构平台。完整后端首轮2失败已保留：真实字段fixture与长观察TTL登记缺失；修复守卫而非豁免，完整复验另录。正常域合批抖动与新鲜low质量健康漏报也经反证整改。作者/协作者检查不是独立网页Review，当前PR须新准确HEAD DegradedRelease。

重大决策传播：system/runtime-performance、product/limit-up-attribution、ai/semantic-decisions、data/data-sources、W06与handoff同步既有方案；INDEX已有条目可达，无新增文档/业务页面。总方案v9.13/AGENTS/现役Skills无需改准入规则，理由是既有任务内工程纠错，未引入新模型/服务/评分/权限或重研究隔离。独立语义效果、人工作真值与真实交易性能仍不由工程测试替代。

当天上午实际5分钟证据保留，下午错过；自动跟进已PAUSED，盘后collect拒绝且0请求。没有新永久ping或历史回补。下一实盘验收须新运行版本和真实交易状态；不为测量主动生成选股、发/已读通知、调用冷摘要模型或模拟交易。


## 7. 本批验收包

后端首次完整2失败的原日志保留；补真实schema fixture与长观察TTL登记后完整5263 passed/81 skipped/109.80s，4个既有warnings。前端最后默认/UTC各133文件1465项通过，tsc、ESLint（0error/2个既有warning）、生产构建通过；文档守卫、卫生和公开扫描全部通过。实际MinuteChart绘制十个Canvas，指数叠加可见；读取失败后保留画面、重读清告警；默认完整原文/限制与链接可读。桌面1280、390、320无横向页面溢出，320短节点标题经容器查询展开图标/标题布局后保持可读。数据是隔离合成夹具、connect-src none，非实盘、真机FPS或原文事实验证。Impeccable context→optimize/harden→mechanical detect→实际状态/窄屏验收沿原技能职责，不强套营销样式。

恢复仅回退此PR源码及运行构建；进程内原因观察非持久历史，重启后缺失仍unknown。长期语义效果、源切换覆盖和开盘/断源浏览器SLO需同版本真实观察，不用有限行为测试造可用率或提速倍率。已有Dependencies PR仍独立维护，不混入本批源稳定性修补。

## 8. 本批冻结、验收与恢复

基点e4356ef6，本轮已授权工程整改完成。最终后端5278通过/81跳过/112.06s、pyflakes；前端默认/UTC各1510通过/134文件（98.11/101.23s）、tsc、ESLint（2个既有warning/0error）、Next16.3.8构建。首轮BE新测试自建时区失败、首轮FE两处旧ISO断言及漏分类展示失败均保留；复用BJ_TZ、正常展示时钟与恢复消息分类后完整复验，不放宽守卫。最终文档守卫/卫生/公开扫描另核，exactHEAD发布与激活单独回填运行回执，不能预写成功。

实际组件使用合成数据/CSP connect-src none，驱动开关图表Canvas尺寸未变，图表实际绘制；右Tab旧flex覆盖经真实夹具改为五列两行。320/390无横向页面溢出，320通知286px宽/188.5px高，原因全文和后置否定一点击可达，关闭回原焦点。指数名/价格/涨幅/已收盘左轴一致。实际source时点、质量和失败保留；不是手机设备FPS、实盘时效或语义正确率。

新neutral journal/来源主线索不改变选股评分或通知资格。旧方向潜在消费差异未见今日实际输出污染；两条未来标签写入钟来源未知，不删业务数据、不用全库总数证明效果。独立全市场趋势发现源仍需LAB增量证据。六旧PR按新兼容版本处置，official audit14→7 high，braces dev/build链尚无同链官方修复；production依赖子集0，现有Node/jsdom engine警告及实际CI另录。无新服务、模型、源/轮频/TTL/预算/权限或永久跟进。

恢复仅本批Git差异和切换前正式构建；旧T表/迁移/档案及用户两份evolution原件保留。发布后核新单实例、准确版本、纯读取端点和真实源状态；不为验收触发选股/通知/摘要模型/新WS。
