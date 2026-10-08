# 当前交接：B+C选股代表闭环已补，全功能迁移未完成

> 总方案v9.13；U01–U55；状态仅W07/IMP-082；现役提案product/ui-original-skills-plan-20261008.md；Jev蓝图docs/ai/jev-integration.md。

外部演进入口docs/ai/continuous-evolution.md；候选不自动准入。

## 1. 现场、模式与范围

用户2026-10-08审阅原版方案后明确“开始吧”，随后拒绝全部旧九稿并要求最多三套全新可操作方案。开工时fetch成功，基点master/origin/master=d0972819775c7161daca1c54379dd13540c82b51；分支codex/ui-direction-selection；selector未发现其他actionable任务，明确派工登记IMP-082。

- **当前主门**：G2
- **本轮主任务**：IMP-082
- **主切片首选**：IMP-082

**U50 降级授权回执**：2026-09-24 Codex DEGRADED_FULL_CONTROL未撤销；作者=发布操作者，非独立Review。准确HEAD DegradedRelease、required三job、release_check、合并后CI与清理不降低。

用户已明确选择“第二和第三套的结合”，即本批B+C，不再等待三选。用户追问全功能并强调选股后，确认前稿仅工作台/市场/提醒、缺核心选股完整流程；现已补选股入口和合成代表闭环，未迁入真实API/业务。IMP-082继续进行中，结合稿仍为隔离prototype，全生产迁移未完成。本轮原生产源码未改、未删除，3000/8000、凭据、交易权限和恢复点不变。缺失功能必须逐子项迁移验收后才能替换旧版，不能因换UI丢功能。

## 2. U49 主动审计回执 / 作者Preflight与有界反证

原版finesse-brief、frontend-design、Interaction Design、finesse-ui分别产出，不用单一融合摘要替代原职责。源码核对发现：工作台本地刷新时刻不是行情源时间；OpportunityView历史triggered且actionable=False不是现在可执行；通知读取窗口数不能叫全天分母；默认600519不是用户关注事实；任务取消请求不是实际停止。各项进入定义/状态契约，业务修复仍属后续获准实现。

原版context/seed/question根解析与live不同，artifacts无嵌套Git的live会找到父仓Git。故本阶段只运行已核试件cwd的原生context/seed/question，主仓采用明确宿主适配，不声称完整native live。首次拟查看context帮助但引擎未将--help视为帮助，实际又输出一次主仓context；此为记录上的额外只读运行，不再重复。试件context根与产品投影另存证。

## 3. 当前产物与边界

旧批次`artifacts/runs/ui-direction-selection-20261008`中的九稿、暂选与shape候选已被用户全部拒绝，退出当前权威，仅留历史证据。方案§8明确标历史撤回，§8.5旧九稿/comp流程被最新指令覆盖，不回到旧问板要求确认。产品实体、字段/writer/cadence事实可复用，旧视觉不能融合、换皮或自动恢复锁定。

当前批次`artifacts/runs/ui-three-refined-20261008`保留比较基线A“从一个对象开始”、B“图表与核对记录”、C“观测室”，每套均有工作台/市场/提醒三个代表面；现入口以BC为主，原A/B/C折叠保留，[来源与技能覆盖](http://127.0.0.1:51066/coverage.html)继续保留。原A/B/C及共享资产哈希不变，Geist/Noto使用原共享字体。全部数值/事件/提醒为隔离合成示例，非实盘。

`bc.html/css/js`、`bc-contract.md`、`bc-design.md`及功能核对已形成，最终字节绑定`bc-acceptance.json`；契约由`product_workbench_spec`生成。采用B主体+C关注带及石墨/淡硫风格，顶部关注带替代B底部自选表，只保留一个自选管理入口和同一选中证券状态。[BC合并试件](http://192.168.110.169:51067/bc.html)仅限同一Wi-Fi的LAN，HTTP200/私有路径404见`bc-http-verification.json`，不代表真实手机已验。各原版技能分别应用，不恢复退役融合skill权威；10个原始来源绑定哈希不变。

选股是真实生产核心，不能用市场过滤替代。一级“选股”入口及合成候选→依据/等待/失效/质量→同股图表→明确加入本地关注的代表闭环已补。`bc-feature-parity.md`已核原生产子功能、动态猎场、深链、持仓模拟、复盘与后台；基线绑定整改前稿，§10为当前增量。真实API/业务仍未迁入，不得把入口或合成闭环写成全功能迁移已完成。

33项技能/条件分支、30项明确来源、40条跨面要求、18组细功能、95条原始效果+12条整改继续作为全库存逐项评估，4项待辨识另保留缺地址状态。每项需真实消费者、独立产物、采用层次与未采用原因；不是全部强装。三代表面不替代全域闭包。frontend-design两遍、IxD状态/恢复、finesse product/密度/dataviz/mobile、Taste九组与Impeccable craft-floor分别留在三份design，不合并成风格摘要；原版/人工适配/未执行边界见方案§9。

先提交`holistic-critique.md`的任务/结构/行为判断，再读`detector-review.md`合并检测。holistic对A/B为非作者源码审阅、C为作者自审，后补部分真实截图，仍是DEGRADED；detector对A为作者自查、B/C为非作者源码检查。原版detect各案实际exit2，原始warning为A26/B71/C69，共166；是有发现而不是clean，不得据数量排名或称通过。扫描绑定的旧字节与当前修复字节分开，未执行的overlay/browser部分如实保留。

TasteLab远端浏览连续超时，本地IAB已恢复；完整外站截图+extract、量测及Design Map/Taste DNA仍partial。部分原站HTML200或本地可截图不能关闭完整外站提炼缺口，也不能声称全部浏览工具不可用。合并前三案的真实Canvas、对象证据、动作反馈、空态恢复、键盘焦点、窄屏触达/小字和关闭全族已按代表面复验；当时7个下拉与5个复选框换真实Radix，7菜单×320/1440共14次展开在触发器下方且不重叠。旧手机隐藏span规则误伤B新组件也已修正。上述数量为旧三案历史，不是BC控件分母。

## 4. 验证与恢复

保留`ui-current-saved-20261008`→0377b8414be70710bd4fff67818974601d5910da和`artifacts/recovery/ui-current-20261008/source.tar.gz`。本轮fresh fetch成功，origin/master仍为d0972819775c7161daca1c54379dd13540c82b51（PR #240合并基点）；不据此保证未来远端不变。当前复用codex/ui-direction-selection，PR #241为draft，回填基点HEAD=5f0f9b8b88d5aef86c764b28cac5ff5984d66dd6；不表示BC成果已提交或发布。旧审计不代替新字节的视觉、行为或性能验收。

**合并前三案代表面验收**：`final-acceptance.json`绑定当时字节、24个表面/视口观察和真实截图；工作台覆盖320/390/1440/1024短屏，市场/提醒覆盖桌面与手机。A图表0高、A市场根溢出、B根滚动及反馈/同对象/恢复缺口已修后复验，手机图表最低230px。搜索、切股、周期/MA/Tabs、通知、删除撤销、空清单恢复及同源12点表有实际交互证据。系统减少动态/透明、屏幕阅读器、全设备/性能和完整ultramotion WebGL未实测；原版detect的166条历史warning仍不记全部关闭。共享runtime及Radix桥接各有Jev baseline/复评，非独立产品验收。这些记录不证明BC新字节已通过。

**BC前稿有限实测**：`bc-browser-observations.json`有8个尺寸/表面观察，覆盖1440/390的工作台/市场/提醒及320/1024×600工作台，根尺寸均等viewport。当时实际7个Canvas，桌面绘区939×441，390/320手机分别350×260、284×260，短屏650×279，字体loaded；前稿3 Select/3 Checkbox，6次PC/手机Radix菜单在触发器下约6px且不覆盖。PC通知宽900/关闭32，390手机抽屉关闭44。搜索切股不加关注、删除原槽撤销/清空恢复/明确加关注/市场已关注筛选、周期MA/12行同源表/Tabs键盘/来源就地展开/提醒空态重置/通知关闭回焦有实际操作记录；不覆盖新增选股或最终修改字节，尚不能写成最终`bc-acceptance.json`验收。

**选股实测及最终确认**：`bc-hunting-browser-observations.json`保留8条增量观测/3条新菜单记录，含初批与修后确认。PC1440/390/320根无溢出；宏和603256切到同股图表/依据不加关注(count5)，返回后明确加入才变6，防重复且回焦。10分组全可达，5组合成流程/5组显式生产未接入；盘中失效只有博云1条，每日精选失效合法空并可恢复金安1条。现4 Select/3 Checkbox，第四Select在PC/390触发器下约6px，修后手机再确认不覆盖；390/320图350×260、284×260，带返回入口的最终桌面939×439。手机状态4em挤字已改为328/262px宽、19.8px高的一行；BC专属19字补充字体3×7304B，fonts=loaded，原共享未改。桌面截图1417×900与DOM1440×900的余白截取差异已披露，完整字形覆盖未独立量测。

**BC前稿审阅与局限**：原版detect单次exit2、12条原始warning见`bc-detector.json`，不是clean，也不据旧三案166条排名或全部关闭。五部分兼容`bc-finish-review.md`及bc.js Jev correctness7.4 unchanged只证明各自受检范围，不代替补选股后的实测，不冒称原生角色或独立生产审核。真实手机、系统偏好/屏幕阅读器/全设备性能、完整外站TasteLab、完整ultramotion WebGL和全域闭包仍未关闭。

**本轮增量审阅**：新单次`bc-hunting-detector.json`仍exit2/12 warnings，不是clean；兼容新代理五部分`bc-hunting-finish-review.md`为ship，仅限选股入口/合成代表闭环/未接入说明，不是全功能、生产或原生完整流程放行。新bc.js Jev final correctness7.5(confidence0.37)/reliability7.5，documentation唯一improved、regressions空；补CSS/字体后JS未变，不重复评分，不称独立产品验收。

合并前201 passed见`doc-validation-final.json`，只覆盖当时文档字节；此前194 passed也不作本轮证据。本次文档门禁记录为`bc-doc-validation.json`，绑定三文档运行前后哈希，不以文档门禁替代产品实测。用户选择和隔离选股代表闭环已明确，IMP-082继续进行中。全生产功能/真实API业务尚未迁入，原业务代码未删，不能替代现用系统；缺失功能必须逐子项迁移验收后才能替换旧版。生产迁移仍另待确认，不重问三选，不宣称全部技能或全部功能完成，不自动领取第二任务；文档不触发Jev代码评分，无新增付费/外发授权。
