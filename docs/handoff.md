# 当前交接：全新三案可操作UI比较与修复验收

> 总方案v9.13；U01–U55；状态仅W07/IMP-082；现役提案product/ui-original-skills-plan-20261008.md；Jev蓝图docs/ai/jev-integration.md。

外部演进入口docs/ai/continuous-evolution.md；候选不自动准入。

## 1. 现场、模式与范围

用户2026-10-08审阅原版方案后明确“开始吧”，随后拒绝全部旧九稿并要求最多三套全新可操作方案。开工时fetch成功，基点master/origin/master=d0972819775c7161daca1c54379dd13540c82b51；分支codex/ui-direction-selection；selector未发现其他actionable任务，明确派工登记IMP-082。

- **当前主门**：G2
- **本轮主任务**：IMP-082
- **主切片首选**：IMP-082

**U50 降级授权回执**：2026-09-24 Codex DEGRADED_FULL_CONTROL未撤销；作者=发布操作者，非独立Review。准确HEAD DegradedRelease、required三job、release_check、合并后CI与清理不降低。

本阶段执行定义、参考研究和全新三案隔离HTML候选，真实修复验收进行中；IMP-082等待用户三选，不自行选定。保留当前UI恢复点，不修改生产产品代码/提醒后端、3000/8000、凭据或交易权限。产品用途、顶导、深色和模拟边界不重问。

## 2. U49 主动审计回执 / 作者Preflight与有界反证

原版finesse-brief、frontend-design、Interaction Design、finesse-ui分别产出，不用单一融合摘要替代原职责。源码核对发现：工作台本地刷新时刻不是行情源时间；OpportunityView历史triggered且actionable=False不是现在可执行；通知读取窗口数不能叫全天分母；默认600519不是用户关注事实；任务取消请求不是实际停止。各项进入定义/状态契约，业务修复仍属后续获准实现。

原版context/seed/question根解析与live不同，artifacts无嵌套Git的live会找到父仓Git。故本阶段只运行已核试件cwd的原生context/seed/question，主仓采用明确宿主适配，不声称完整native live。首次拟查看context帮助但引擎未将--help视为帮助，实际又输出一次主仓context；此为记录上的额外只读运行，不再重复。试件context根与产品投影另存证。

## 3. 当前产物与边界

旧批次`artifacts/runs/ui-direction-selection-20261008`中的九稿、暂选与shape候选已被用户全部拒绝，退出当前权威，仅留历史证据。方案§8明确标历史撤回，§8.5旧九稿/comp流程被最新指令覆盖，不回到旧问板要求确认。产品实体、字段/writer/cadence事实可复用，旧视觉不能融合、换皮或自动恢复锁定。

当前批次`artifacts/runs/ui-three-refined-20261008`有三套独立可操作HTML：A“从一个对象开始”、B“图表与核对记录”、C“观测室”，每套均有工作台/市场/提醒三个代表面。入口[三案比较](http://127.0.0.1:51066/index.html)，[来源与技能覆盖](http://127.0.0.1:51066/coverage.html)；只有三项直接预览，无iframe嵌套。真实自托管Noto Sans SC+Geist、语义控件与Canvas，全部数值/事件/提醒为隔离合成示例，非实盘。候选尚未选定，不落生产DESIGN/方向契约。

33项技能/条件分支、30项明确来源、40条跨面要求、18组细功能、95条原始效果+12条整改继续作为全库存逐项评估，4项待辨识另保留缺地址状态。每项需真实消费者、独立产物、采用层次与未采用原因；不是全部强装。三代表面不替代全域闭包。frontend-design两遍、IxD状态/恢复、finesse product/密度/dataviz/mobile、Taste九组与Impeccable craft-floor分别留在三份design，不合并成风格摘要；原版/人工适配/未执行边界见方案§9。

先提交`holistic-critique.md`的任务/结构/行为判断，再读`detector-review.md`合并检测。holistic对A/B为非作者源码审阅、C为作者自审，后补部分真实截图，仍是DEGRADED；detector对A为作者自查、B/C为非作者源码检查。原版detect各案实际exit2，原始warning为A26/B71/C69，共166；是有发现而不是clean，不得据数量排名或称通过。扫描绑定的旧字节与当前修复字节分开，未执行的overlay/browser部分如实保留。

TasteLab远端浏览连续超时，本地IAB已恢复；完整外站截图+extract、量测及Design Map/Taste DNA仍partial。部分原站HTML200或本地可截图不能关闭完整外站提炼缺口，也不能声称全部浏览工具不可用。真实Canvas、对象证据、动作反馈、空态恢复、键盘焦点、窄屏触达/小字和关闭全族已按代表面复验。全部7个下拉与5个复选框换真实Radix；7菜单×320/1440共14次实际展开在触发器下方且不重叠，含dialog内层级/键盘/重置。旧手机隐藏span规则误伤B新组件也已修正。

## 4. 验证与恢复

保留`ui-current-saved-20261008`→0377b8414be70710bd4fff67818974601d5910da和`artifacts/recovery/ui-current-20261008/source.tar.gz`。本轮重新fetch后HEAD/origin/master仍为d0972819775c7161daca1c54379dd13540c82b51（PR #240合并基点）；不据此保证未来远端不变。旧审计不代替本批视觉、行为或性能验收。

**本轮代表面验收**：`final-acceptance.json`绑定最终字节、24个表面/视口观察和真实截图；工作台覆盖320/390/1440/1024短屏，市场/提醒覆盖桌面与手机。A图表0高、A市场根溢出、B根滚动及反馈/同对象/恢复缺口已修后复验，手机图表最低230px。搜索、切股、周期/MA/Tabs、通知、删除撤销、空清单恢复及同源12点表有实际交互证据。系统减少动态/透明、屏幕阅读器、全设备/性能和完整ultramotion WebGL未实测；原版detect的166条历史warning仍不记全部关闭。共享runtime及Radix桥接各有Jev baseline/复评，非独立产品验收。

本轮10个适用文档守卫已运行201 passed，doc-health/卫生/公开扫描通过；最终回填后的同组复核另存`doc-validation-final.json`。此前194 passed不作本轮证据。文档不触发Jev代码评分；无新增付费/外发授权。完成必要修复验收后交用户在三案中选择，再确认对应具体定义；IMP-082不结项，不扩生产，不自动领取第二任务。后续生产实施、完整门禁与准确HEAD发布另按原门序执行。
