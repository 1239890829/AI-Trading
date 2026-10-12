# 当前交接：IMP-019 冷读复用、时效与无感刷新

> 总方案v9.13；累计U01–U55；状态只归[W06/IMP-019](stages/w06-performance.md#imp-019)。2026-10-13用户续派剩余性能项，不恢复REA/Jev/Decisions/KEV，不改选股/消息规则。

现役语义蓝图[semantic-decisions](ai/semantic-decisions.md)；外部能力依[开放世界持续演进](ai/continuous-evolution.md)筛选。

## 1. 现场、授权与最新验收

- **当前主门**：G4
- **主切片首选**：IMP-019
- **调度**：ledger运行选择一致，不另领业务任务。
- **基点**：已运行PR #266/master e5b0f77b30521c1d6b77868fda770ffa4eff0e60；最新fetch后建codex/quote-read-performance，独立worktree施工，不开第二后端调度。原未跟踪evolution记录保留。
- **范围**：21候选逐项处置，10族实施、11项保留或测量。源日期/年龄、历史完整/失败恢复、股票/板块/新闻/新浪/turnover冷读共享、精选DB所有权、题材冷同步竞态、自选取消/写后/人工刷新/失败保留。
- **不变条件**：成功TTL/行情轮频/预算/源优先级、选股/消息资格、去重/推送/撮合硬门不变；部分历史/失败不扩为成功缓存。manual立即校准，源时间不改成命中时刻。
- **门禁**：后端5209 passed / 81 skipped / 187.02s、pyflakes通过。前端默认/UTC各1324 passed（123文件，84.07/162.11s），tsc/ESLint零错误（仅原生图片夹具1 warning），生产build通过，编译13.6s/类型17.2s不当总耗时；文档合同191 passed，doc-health/hygiene/public scan通过。首次handoff精简丢失6项机器入口，修回原约定后3个合同失败复绿，未放宽守卫。定向真实组件及源时间/冷读/历史恢复反例判红再精确恢复。
- **实测限度**：8并发冷读8→1；精选120ms等待ticker最大150.16→5.69ms；两窗口三题材来源6→3、冲突3→0，输出一致但总延迟未改善。真实组件/CSS20Hz桌面/390px夹具零50ms长任务、17 Canvas保留，无横向溢出；非真机/实盘SLO。
- **U50 降级授权回执**：2026-09-24 DEGRADED_FULL_CONTROL未撤销，作者=发布操作者；准确HEAD DegradedRelease、本地/CI/release_check/postmerge/运行生效分开核，不称独立网页Review。

## 2. U49 主动审计回执 / Preflight 作者反证与传播

核冷读/最后退订/旧finally、写前后代际、manual立即新读、失败不当真空、副本、先截断集合、日期/采集年龄、五字段/原子写/部分失败/同期存档、GET副作用、Session取消worker自然drain、题材失败和关闭生命周期。根反证发现新校准失败误清membership，修复并注回判红；协作子代理交叉核查不是独立网页Review。

events覆盖未过滤过期override是既有资格风险，保留事件owner候选，不在性能片静默改入选。report §10、W06、INDEX传播；AGENTS/skill/总方案/权限与产品语义不变，无新增业务页面/模型/数据源/生产依赖，无需新重大方案准入。

## 3. 发布、恢复与剩余证据

本段记录发布前施工与完整本地门禁；准确PR/HEAD发布结果从本批DegradedRelease、release_check与GitHub CI核对，服务实际版本从本机artifacts/runs/quote-read-performance-20261013/runtime-activation.json核对，不由本地工程通过推定。每项差异可Git独立回退，切换前保存现有构建，后端no-reload且单实例；隔离测试没有重启8000/3000。

[报告§10](system/runtime-performance.md#10-2026-10-13-剩余候选逐项验证与读链优化)逐项列位置、原因、处置与不变条件。IMP-019长期SLO仍部分完成：真实交易尾延迟/源龄/错误attempt分母、生产DB规模/锁等待、真机/GPU/研究竞争需实际窗口；不重复已修点，不为形式优化扩大重构。
