# 当前交接：任务与对象连续性的全域 UI 重设计

> 总方案v9.13；累计U01–U55；设计证据product/ui-audit-20261003§25；状态只在W07；Jev入口ai/jev-integration.md。

## 1. 现场、模式与范围

用户已授权执行重设计计划修订2。IMP-068分支codex/ui-continuous-workspaces，base bfadc38e（PR #220已合并）。当前版保存在ui-current-saved-20261004，恢复时须修正玻璃过透。

- **当前主门**：G2
- **本轮主任务**：IMP-068
- **主切片首选**：IMP-068

**U50 降级授权回执**：2026-09-24本机Codex DEGRADED_FULL_CONTROL未撤销。作者=发布操作者，不是独立Review。准确HEAD DegradedRelease、完整本地门禁、required CI和release_check继续强制。

## 2. 实施与验证

**U49 主动审计回执 / 作者Preflight与反证**：开工前核主干与远端一致、无用户脏改动、selector无活动阻断；功能映射继承原50项但不以路由数冒称子功能通过。重点核命令/已读不假成功、搜索IME与旧回包、动画不阻塞输入、列表切视图不重取业务、抽屉层级与焦点、玻璃叠层对比度、图表全宽与真实Canvas。新要求按真实消费者取舍，不新增生产模型/资金/调度权限。

**代码提交**：`c1a13f066cda1ec06cac04523c4f54158b86874f`。候选发布HEAD包含随后协调文档提交，必须重新绑定回执和CI。

- 后端最终全量：4619 passed / 83 skipped；pyflakes通过。前端默认及UTC各92文件/809 passed，tsc/eslint/Next16.3.3正式build通过。
- 41个正式入口分别1280/390截图，另核320/768/1440与专项状态，共100张；X01–X50消费者映射及92生产TSX登记。相应入口无页面横向溢出。亮暗各6面alpha合成正文扫描0发现；图表Canvas实际占满容器。
- 独立复现并修复输入焦点优先级、窄屏状态断行/通知触点、部分指数响应空格、浅色未知角色对比度及旧测试日历/时钟依赖。搜索迟到回包、IME、命令Arrow/Esc、列表保状态均有行为测试。
- Jev基线及复评保留本地；正确性6.9→7.8，低置信泛化建议没有具体反例，不追分。它不是独立审核或业务收益证明。
- 只读合成API预览4202、截图图册4203保留；API夹具4204拒写。未启动生产后端/模型/调度/通知/交易；实体手机、200%浏览器缩放、OS减弱偏好、手机GPU和真实业务收益未验。
- 协调文档198项回归、公共扫描、workspace hygiene及doc-health通过；发布条件继续以准确PR/HEAD的DegradedRelease、required CI与release_check为准。日志/截图/源码hash留在忽略批次ui-continuous-workspaces-20261004。

## 3. 边界与恢复

原四任务与既有路由/API/后台owner保持。原恢复标签和hash已复核；旧Prismatic/Nocturne保存标签退出，本地包可恢复回收，不改历史Git事实。原版及新界面证据分开保存；本轮作者验收不冒充独立Review。

外部持续演进入口：docs/ai/continuous-evolution.md；本轮参考只产生设计取舍，不自动准入依赖。
