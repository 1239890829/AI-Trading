# 当前交接：IMP-091 不可用Jev退出

> 总方案v9.13；累计U01–U55；状态单点归[W07/IMP-091](stages/w07-simplification.md#imp-091)。现役语义蓝图[semantic-decisions](ai/semantic-decisions.md)。前项IMP-090已发布/加载，历史证据从原stage、PR #253/#254及Git追溯。

## 1. 现场、授权与版本

- **当前主门**：G2
- **主切片首选**：IMP-091
- **U50 降级授权回执**：用户2026-10-11明确删除不可用Jev并核实Decisions；2026-09-24 DEGRADED_FULL_CONTROL长期授权未撤销，作者=发布操作者，不称独立审核。
- **基点与范围**：最新master 8d6b9de2ffd9e2deee58e2227861c3e5bf3c667f，分支codex/retire-jev；仅本项代码/测试/专属资产及权威传播。前项PR #254三job与post-master CI38121735989三job均completed/success，原分支本地/远端清理完成。
- **运行**：8000后端PID41443仍为前项发布源码，3000前端PID8157/build sW3ONH-epZJoX5SroTTpF保留；本项尚未发布/加载。数据库/模型配置/UI恢复副本未改。
- **本轮完整门禁**：后端4854 passed/81 skipped/1 warning（164.38s），pyflakes通过；前端隔离副本tsc/eslint、默认与UTC各112文件1012 passed（69.51s/71.17s），Next正式构建exit0（15.39s）。doc-health/卫生/公开扫描通过；局部消费者165、文档88与卫生/配置12不与全量相加。隔离前端不覆盖运行中3000的构建。准确HEAD CI/发布/加载分别核验。

## 2. U49 主动审计回执 / 作者Preflight

Jev Review已连续HTTP451，现役shadow调用也不应成为取行情/业务/发布的阻断依赖。按用户要求退出TypeSafe运行接入；保留原规则/LLM、时效去重、单事件并发/真实thread drain、预算、目录/版本CAS、助手只读执行白名单/真实依赖、grounding以及人工gold评估。不能将旧工具路由连同其依赖硬门删除；依赖筛选迁回唯一工具注册表。旧人工标注/业务数据、历史provider与发布证据不删。

Decisions官方指南和API参考已读；公开beta、predicate/choice/score有typed答案。与旧schema并不等价，旧概率阈值不能直接继承；基础输入价格0.10美元/百万token。只有文档证据，账户、费用、延迟/质量增量未验证，作为候选而非新模型配置。用户已明确确认项目接入＋本机插件和skill一起清理。Jev Review/Guard官方卸载成功；专属MCP、四个skill、CLI、环境注入/LaunchAgent、Keychain服务及调用规则已备份后退出，生成模型配置按hash确认未变。历史服务使用量/路由记录移入私有恢复包，未删除人工gold/业务数据。

## 3. 验证、传播与发布

证据根artifacts/runs/retire-jev-20261011。最终后端4854 passed/81 skipped/1 StarletteDeprecationWarning（164.38s），pyflakes通过；消费者行为165 passed与文档88 passed为局部证据，不与全量相加。前端隔离完整门禁已通过（§1）；准确HEAD release receipt/运行验收尚待。首全量暴露示例配置残留，次全量暴露恢复包内应用目录，已清残留并封装私有归档；另一次错误从根目录运行pytest产生collection错误，正确backend目录重跑绿色。未放宽断言、跳过门禁或改生产鉴权。通过数下降来自退役专属路径与测试，通用行为/标签工具保留。

传播核对：总方案、语义专题、INDEX、plan-registry、AGENTS、现役Skills、stage、handoff与机器传播守卫已更新；旧Jev蓝图迁archive，旧Jev子范围退出，GOV-024已退出，通用父任务不误取消。交易/风控/数据/界面不适用，因为未改其规则/模型/资金。历史实测保留，不将Jev未通过评分改为通过；本项不再要求调用已退役评分服务，工程反证/完整门禁/独立Review或DegradedRelease/CI/release_check保持。

按[持续演进](ai/continuous-evolution.md)给未来候选账户/费用/LAB/gold/Shadow/退出边界，不自动安装付费或改生产阈值。安全退出按Git和受控safe-trash可恢复；重新调用原服务仍须有新的准入授权。
