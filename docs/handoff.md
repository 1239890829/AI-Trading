# 当前交接：IMP-090 健康探针会话与重复检查收敛

> 总方案v9.13；累计U01–U55；任务状态单点归[W07/IMP-090](stages/w07-simplification.md#imp-090)。既有IMP-089保持已完成，历史发布与验收由对应stage/Git追溯。现役Jev蓝图[jev-integration](ai/jev-integration.md)。

## 1. 现场、授权与版本

- **当前主门**：无；IMP-090主体已收口，selector须在下一轮重算。
- **主切片首选**：无；本轮只发布同任务闭环文档。
- **U50 降级授权回执**：2026-10-11用户明确开始解决ping刷屏；2026-09-24长期DEGRADED_FULL_CONTROL未撤销，作者=发布操作者，不能称独立Review。
- **版本**：主体[PR #253](https://github.com/1239890829/AI-Trading/pull/253)准确HEAD71263d30b5ad1accbae9452d357bd0299c14b8b4，主代码9b489fc4，已合并为27bcf7d5ebc1b50400f73a103e96773082a4c2e1。当前codex/quiet-probe-close从此最新master创建，只回填同任务事实。
- **现场**：8000后端PID41443已从发布源码无reload加载；3000前端PID8157/build sW3ONH-epZJoX5SroTTpF保持运行。四份源码/测试manifest无漂移；业务数据库、模型配置及UI恢复包不改。

## 2. U49 主动审计回执 / 作者Preflight与反证

半小时全天探针发送ping；同步CLI保存会话，Codex外部导入变为任务。无需关闭故障检测，现有单轮同步调用没有resume消费者，适用官方no-session-persistence参数。历史backend-cwd、单次ping、外部导入、无后续业务对话的652条已通过官方任务工具归档，保留源日志；只读库确认全部archived，近期50条无ping。普通交互式会话与流式助手不改。

健康观察只保留模型/CLI身份、时间与成功/失败，最多16个纯元数据项、线程锁及读取副本。原时间TTL不因轮询延长；新失败失效旧成功，切目标失效缓存；force、原失败分类/阈值/冷却保留。探针自身不伪装业务观察，语义正确性不是输送成功，HTTP/流式/跨进程不冒充覆盖。测试只替换模块导入时钟，不冻结事件循环。只读providers不触发付费调用。

## 3. 本轮验证与证据

证据根artifacts/runs/quiet-llm-probe-20261011。最终后端4947 passed/83 skipped/1 StarletteDeprecationWarning（175.24s），pyflakes通过；定向初版55项通过后新增旁路故障保持原结果/错误用例。离线假CLI实际子进程对照旧调用保存、新调用不保存且OK回复不变，不触网或使用生产模型。辅助记录故障可复现覆盖业务成功，已加入旁路保护并重新全量验证。

首次全量受本机shared/token配置影响，测试进程显式local/空token复验，不修改生产鉴权或断言；第二批2项文档治理字段失败补齐后重验，当前doc-health/卫生/公开扫描通过，失败批保留不冒充通过。前端在隔离同源副本/公开配置样例/同版本依赖下验证，不改运行中生产.next；tsc/eslint通过，默认/UTC各112文件1012项通过（71.59s/70.53s），Next16.3.3生产构建通过。首次副本漏带已跟踪的公开.env样例而触发守卫，补样例后复验，不放宽断言、不复制私有配置。

Jev baseline/final均HTTP451，无分数或有效previousEvaluation，不称评分通过。加载前再次只读确认在途任务、通知待发/未完成尝试及未终态模型用量均0。正常TERM旧8000后加载新源码；自然启动探针15:19:35+08:00 ok/4372ms/probes1/alerts0，未新建或修改项目CLI会话JSONL；未归档backend ping=0。providers/3000工作台/代理health均200，市场health degraded保留，不冒充全数据新鲜。元数据不出提示词/回复/凭据，不改变业务判断或模型配置。

传播核对：现有system/llm-gateway-probe、原owner stage及handoff已更新；INDEX原文档入口不变，无新文件；总方案、AGENTS、Skills与Jev蓝图不适用，因为不引入新模型/路由/权限/产品语义或治理门。仅收敛既有探针会话与重复调用，运行效果和费用不由单测代替。

## 4. 发布与恢复

主体[DegradedRelease](https://github.com/1239890829/AI-Trading/pull/253#issuecomment-6106571343)、CI38120636274 attempt1三job、release_check与match-head合并通过，post-merge CI38120933217三job completed/success；真实运行另核如上。主体本地/远端分支已清，隔离前端源/依赖/构建副本已删除，紧凑证据与真实服务保留；本轮文档回填守卫195 passed（4.68s）、doc-health/卫生/公开扫描通过，源码manifest无漂移；文档候选仍按自身准确HEAD发布，不预报其CI通过。项目拥有的本轮pytest basetemp已清，失败批日志/XML与运行证据保留。不领取第二业务任务。按本项Git差异恢复，聊天可以取消归档，源日志/业务数据/UI恢复副本保留；不回滚数据库或修改模型配置。

开放世界候选继续按[持续演进](ai/continuous-evolution.md)准入；本轮是确定性运行修复，不改变Jev/策略效果准入或模型路由。
