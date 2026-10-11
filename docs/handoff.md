# 当前交接：IMP-091 Jev退出已生效

> 总方案v9.13；累计U01–U55；唯一状态归[W07/IMP-091](stages/w07-simplification.md#imp-091)。现役语义蓝图[semantic-decisions](ai/semantic-decisions.md)。本轮仅同任务文档收口，不自动领取第二业务任务。

## 1. 现场、授权与最新验收

- **当前主门**：G2
- **主切片首选**：无（IMP-091已完成；本轮仅同任务文档闭环）
- **U50 降级授权回执**：2026-09-24 DEGRADED_FULL_CONTROL未撤销；2026-10-11用户明确删除不可用Jev，并确认项目接入＋本机插件和skill一起清理。作者=发布操作者，不称独立Review。
- **代码发布**：[PR #255](https://github.com/1239890829/AI-Trading/pull/255)，准确HEAD 32db3192afa9d3afc0eb21d04ab2961e3ad46d53、合并0e485dae4028ad2d03625223cf1cb6f30d448ec5；DegradedRelease、required CI38123486372三job及release_check通过。post-master CI38123799161三job completed/success；原codex/retire-jev本地/远端已删除。本轮文档闭环限定handoff与W07事实回填，不改业务代码；对应PR回执归档发布事实，不形成新施工派工。
- **完整门禁**：后端4854 passed/81 skipped/1 StarletteDeprecationWarning（164.38s），pyflakes通过；隔离前端tsc/eslint、默认及UTC各112文件1012 passed（69.51s/71.17s），Next正式构建exit0（15.39s）。doc-health/卫生/公开扫描通过。165消费者、88文档、12卫生/配置、最终108消费者为局部子集，不与全量相加。专属JeV路径/测试退出导致通过数减少，不称覆盖增强。CI时长另有元数据；账户余额接口不可读，写未知，未开启付费。同任务文档收口另核97项文档/卫生及10项Markdown渲染通过，不与主体全量相加；首回填漏2个机器传播标记已恢复，原守卫未修改。
- **运行**：8000新PID52476从已发布主代码0e485dae无reload加载；启动前agent/outbox/attempt/model usage在途均0，正常TERM旧实例。3000 PID8157/build sW3ONH-epZJoX5SroTTpF保留，前端业务源码本项未改。providers无jev字段；原claude_cli自然探针16:10:12+08:00 ok/2770ms/probes1/alerts0，工作台与代理health 200；无新增持久ping会话。行情health仍degraded，不冒充新鲜数据。
- **本机清理**：Jev Review/Guard官方卸载；专属MCP/四skill/CLI/Keychain凭据与注入/环境hook/两LaunchAgent/8777专用桥已备份后退出。最终无专属进程，含复核发现的2个context MCP已正常终止；Codex无关配置解析一致，生成模型设置原hash不变。旧源码/配置/使用量仅在私有恢复tar，0600/根0700，业务库/人工gold/UI恢复包未删除。
- **证据根**：artifacts/runs/retire-jev-20261011；source-manifest-final、commit-scope-verified、global-verify-final、global-recovery/archive-index、runtime-start/acceptance、release-check和CI JSON绑定上述事实。早期dead env与恢复目录门禁失败已修正，根目录误运行pytest collection另有保留；最终正确backend目录全量绿色，断言/鉴权/门禁未放宽。

## 2. U49 主动审计回执 / 作者Preflight与反证

检查消费者/fallback、工具依赖/执行白名单、取消/真实线程drain、事件CAS/全批失败、人工标签/预测身份、目录卫生、全局配置/模型漂移、权威指针和未授权付费。available_tools迁唯一注册表且原函数逐字一致。Jev影子/级联/外部核验/研究预标注及专属评分义务退出；grounding、规则去重、预算、通用人工gold/rationale工具及历史provider事实保留。不能将退出辅助评分描述成退出工程审核。

总方案/INDEX/plan-registry/semantic-decisions、AGENTS、现役Skills（Governor1.4.1）、stage及机器传播守卫已更新；旧蓝图只在archive，GOV-024退出，父业务/人工gold任务不误取消。交易/风控/数据口径与界面不适用，因为未改其规则/模型/资金。代码CI、运行加载和文档闭环各自绑定准确版本。

## 3. 替代方案与下一步

当前运行使用规则→已有生成模型→明确失败降级；无Jev后台等待或凭据读取。OpenAI Decisions官方指南/参考已核：公开beta、POST /v1/decisions、当前gpt-6-luna、predicate/choice/score、基础输入0.10美元/百万token。账户/地区/网关/费用上限及目标域质量未知，厂商速度说法不作本项目收益；没有自动配置或调用。旧概率阈值不能直接继承，更不能当上涨概率/胜率。

按[开放世界持续演进](ai/continuous-evolution.md)保持候选/费用/准入边界。本项已实施、发布、运行验收；本轮只发布同任务文档回填。后续候选走既有消费者、人工gold/同基线/LAB与费用授权，不为了替代名字另造SDK/配置/注册表。常规继续重跑selector，只按现役stage选择；不得重复退出、恢复旧强制调用或领取未经授权的第二任务。
