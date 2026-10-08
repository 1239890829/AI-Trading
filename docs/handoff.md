# 当前交接：B+C正式前端工程验收完成，PR #241发布收尾

> 总方案v9.13；U01–U55；任务状态仅W07/IMP-082；现役方案[原版技能与UI方案](product/ui-original-skills-plan-20261008.md) §9.7；Jev蓝图[jev-integration](ai/jev-integration.md)。外部候选按[持续演进](ai/continuous-evolution.md)核验，不自动准入。

## 1. 现场、授权与版本

- **当前主门**：G2
- **本轮主任务**：IMP-082
- **主切片首选**：IMP-082

**U50 降级授权回执**：2026-09-24 Codex DEGRADED_FULL_CONTROL授权未撤销；作者=发布操作者，非独立Review。准确HEAD DegradedRelease与完整发布门继续执行。

- **当前主门 / 唯一主任务**：G2 / IMP-082。用户选择第二、第三套结合，并明确“菜单名称可以再想想，然后就开工吧”，已授权正式React实施。
- **菜单**：工作台 / 选股 / 市场 / 复盘。自选和持仓归工作台；跟踪记录归选股；研究、参数与维护经全局工具进入。原路由key、证券/日期/四账户scope与返回来源保留。
- **工程状态**：正式前端已实现并完成本轮工程验收。stage的“已完成”指实现与已披露范围内的验收；准确HEAD发布、合并后CI和清理仍是本轮必做交付，实际结果单点追加[PR #241](https://github.com/1239890829/AI-Trading/pull/241)，不预写未来成功，也不为写入未来merge SHA再造一次PR。
- **版本**：分支`codex/ui-direction-selection`；正式源码提交`c157f179cbf7710386025800c6384008e12baa7b`；当前协调文档提交在其后。fresh fetch所核`origin/master`=`d0972819775c7161daca1c54379dd13540c82b51`，发布前须再核。旧prototype及旧PR标题不能代表本轮生产范围。
- **协作模式**：DEGRADED_FULL_CONTROL；作者=发布操作者，不能称独立Review。required三job、exact-HEAD DegradedRelease、release_check、match-head合并、post-merge CI和分支清理均保留。

本轮只改变前端呈现、交互与失败反馈。后端规则、数据源、策略阈值、模拟撮合、账户和维护权限不扩张；不涉及真实资金或券商。

## 2. U49 主动审计回执 / 作者Preflight与有界反证

| 范围 | 已实现与已取得证据 | 验收边界 |
|---|---|---|
| 工作台 | 顶部真实自选/系统候选带；原分组、模拟持仓、手工记录、每日/盘中候选消费者保留；缺报价不抹名单；原图表/七项核对视角与深链；320/390/1280/1440、短屏及Canvas实测。 | 写命令有隔离行为测试；未在真实账户执行写命令求验收。 |
| 选股 | 发现候选 / 持续观察 / 验证复盘组织原十项能力；风险先于候选，原真实API、参考跟踪和模拟入口保留；合法空与失败分开；日期/对象/返回上下文有测试。 | 界面工程不证明选股持续性、收益或RSH-031效果。 |
| 市场与盘面 | 八视角、指数身份、资金/事件/梯队/涨跌停/龙虎榜消费者保留；主表/事件分层与容器滚动。最后隐藏caption越界以滚动容器建立包含块修复，390px根scrollHeight由1124回到844。 | 当前行情与历史日期口径继续区分；该修复未隐藏或删除读屏标题。 |
| 复盘与维护 | 阅读宽度约78ch，手机日期/摘要同轴；研究和受控维护经工具分组；提醒宽抽屉、定制选择/渠道、pending/错误/可取消确认与有界长表。 | 本轮未改变后端去重、推送或权限；读取失败不能写成无触发。 |
| 共享界面 | IBM Plex Sans Latin+完整中文系统fallback；石墨/淡硫、实体读数面与外围材质；完整手机搜索、紧凑关闭/按钮、ARIA Tabs、菜单位于触发器下方。 | 真实手机软键盘、读屏与全设备性能尚未完整实测；不宣称完成光学折射。 |

源码反证修复非成交视角错误追加逐笔、提醒原生选择/确认遗漏、嵌套菜单Esc关闭父抽屉和全局事件虚假证券链接；规则回退显示“规则提醒 · AI不可用”。兼容代理`production-verdict.md`限定F-01–05全部resolved、disposition ship，非项目独立Review。另新增真实resize回归：713→1440→390保持助手停靠，左右/自由位置与存储契约均验证；浏览器右侧位置691→1418→368确认。最后caption修复仅加滚动容器position:relative，已重新构建并实屏验证。

## 3. 技能、来源与历史证据

原版pbakaus/impeccable按Operate、craft-floor和阶段审阅执行；Leonxlnx/taste-skill按既有项目九组Scan→Diagnose→Fix执行；frontend-design、Interaction Design等各保留职责，退出的融合入口不恢复。仓库禁止应用私有权威目录，产物投影到现有方案及忽略artifacts，属于宿主适配，非原生全流程宣称。

全库存33技能/条件分支、30明确来源、40跨面要求、18细功能组、95原始效果+12整改分别裁定；评估不等于全部接入，4项地址未辨识不猜测替代。TasteLab外站完整截图/DOM量测及Design Map/Taste DNA仍partial。ultramotion为视频动效模板，不是可直接替换的实时金融光学组件；本轮未完成其完整WebGL验证。

旧九稿已撤回；隔离A/B/C与B+C试件只作比较/迁移基线，其合成数据、旧ship/检测/截图不代替本轮React证据。

## 4. 本轮工程验收

证据根目录：`artifacts/runs/ui-bc-production-20261008`。`production-acceptance.json`绑定正式源码、构建与截图哈希；`gallery.html`区分最终release图和较早补充图。

- **前端**：默认99文件856 passed，201.25s；UTC99文件856 passed，250.24s。当前TypeScript、ESLint、生产build通过。测试覆盖最终JS/TS；最后CSS-only caption定位修复重新build并实屏核验，没有调整测试/门槛。
- **后端**：完整4654 passed / 83 skipped / 1 warning，471.80s；pyflakes通过。首批100fail来自本机.env shared鉴权，进程级local/空token隔离；中间3项文档守卫暴露机器/任务必填字段遗漏，恢复后完整复验通过，未改.env、断言或阈值。
- **实屏**：320/390/1280/1440及短屏代表路径有DOM/行为/真实Canvas记录。最终四主页面各1440/390截图、助手resize及caption复验见browser-release-observations；手机绘区296×268+68px轴，桌面1010×242+68px轴。长表、菜单位置、嵌套Esc、关闭和搜索另有先前同源码范围记录。截图不代替真实手机、软键盘、读屏、所有系统偏好或全设备性能。
- **审阅**：Impeccable当前27文件detector=[]/exit0；历史warning保留。共享Jev最终correctness8.2/testQuality7.8/compatibility8.4，全部比较方向unchanged，regressions空；各切片另有回执。分数与机械扫描不代替独立验收或发布许可。
- **文档/发布**：最终协调文档相关守卫186 passed（10.95s），doc-health通过；卫生与public scan在精确暂存后再核。PR required CI、exact-HEAD回执、合并及post-merge实际结果归PR #241，不在发生前写通过。

## 5. 运行、恢复与交付下一步

3000运行最终Next production start，监听局域网并读取原真实API；token仅从既有backend/.env注入服务进程内存，未写入前端文件。工作台、真实代理接口和局域网选股HTTP200。8000原后端未重启；休市/最近交易日/组合日期在界面保留，真实来源不代表盘中实时。prototype合成数值未进入生产。

唯一恢复点：tag `ui-current-saved-20261008`→`0377b8414be70710bd4fff67818974601d5910da`；`artifacts/recovery/ui-current-20261008/source.tar.gz` SHA-256=`f1e89ee0244b128b884b9ab7ed6f2aaecb2450ab917ae8df87bcd62ffed93`。只恢复呈现源码，不覆盖业务数据或复活退出技能；旧版玻璃过透问题须随恢复说明保留。

完成最终协调文档门禁后，一次push至PR #241，按当前模式完成准确HEAD发布、post-merge和清理；发布事实追加同一PR。不自动领取第二业务任务，部署仍搁置，不把工程完成当选股效果结论。
