# 当前交接：GOV-013离线恢复验收

> 唯一状态在[W09/GOV-013](stages/w09-acceptance.md#gov-013)；命令、范围及hold合同见[离线恢复](system/deployment.md#离线备份与隔离恢复)，细功能见feature-closure-audit §16。总方案v9.13/U01–U55；Jev入口ai/jev-integration.md；开放世界入口ai/continuous-evolution.md。前轮RSH-031/PR #199已合并为`800106157955a0a36b91494f5f5394e2c5cf1aa4`，活动隔离副本固定`2038ff8e28d00599b44022c03da266812ef812cf`，不重复开发或清掉运行资产。

## 1. 现场、模式与范围

2026-10-02用户询问剩余任务后，在明确建议GOV-013隔离备份/恢复/对账后要求继续。起点干净master`800106157955a0a36b91494f5f5394e2c5cf1aa4`，fetch后相同；selector普通门仍为空。本轮只针对已交付存储/迁移/业务消费者做G5验收及必要恢复安全修补，不自动跳到GX或第二业务任务。

**U50 降级授权回执**：2026-09-24本机Codex DEGRADED_FULL_CONTROL持续有效，直到撤销。作者自审不冒充独立Review；准确HEAD DegradedRelease、完整本地门禁/required CI/release_check/post-merge/清理不降低。部署仍搁置，无生产升级/恢复启动/通知外发、真实券商、策略权重或付费准入。

分支`codex/gov013-recovery-audit`；核心恢复代码`cc7391009658d64f8046f55be5564776a91a1ec5`，最终代码/测试`32b85d615cebce93b14773d5e9c8b228c8aede6e`补原版本恢复、downgrade拒绝及节假日T+1反例，最终发布以PR准确HEAD为准。生产数据只读；真实恢复资产与私有manifest/逐表hash/对账留忽略的artifacts/runs/gov013-recovery-20261002，不向Jev/GitHub外发。RSH-031仍是独立有限采样进程，deadline 2026-10-22 16:00+08:00；本轮不改变它的待条件状态或补造交易样本。

## 2. 实施与证据

离线CLI只处理明示两棵数据树，SQLite backup归并WAL，其余文件hash；停写确认、源变化、8GiB/100000文件/SQLite120秒限额、软链/路径逃逸、目标覆盖/重叠、损坏/缺项/重复项拒绝。恢复hold先于复制，get_engine在迁移/服务/调度前拒绝标记、缓存engine及DB软链别名；显式离线目标迁移不是应用放行。保留旧sending/pending及lease，不假定渠道已受理，不自动重放旧任务。

真实11300文件/4649342884字节，backup27.99秒，verify后的restore复制/核对14.01秒。恢复前45表schema/事实一致，隔离升级a4e8c2d9f6b1→e2c6a8f4b9d1后51表；原业务行hash不变、外键违规0、paper异常0，11173份Parquet元数据及DuckDB三表可读。watchlist旧nullable/default和sentiment_history旧server default为兼容DDL差异，当前无NULL/范围违规，消费者显式赋值/默认处理；不为DDL相同改生产。原库/日历未写，副本未启动服务。生产RPO/RTO与渠道恢复归OPS-003，不以14.01秒替代端到端恢复时间。

新增持久文件测试覆盖真实消费者：自选增删、通知水位不回退、手工流水修正/删除、main/shadow/hunting_shadow隔离撤单及账本对账、参数生效/回滚、原版本备份恢复、保留计划downgrade硬拒。首次新增测试调用save_state入参顺序错误；后续误用被硬门禁止的downgrade造旧库，再因原始SQL漏ORM默认quality失败，均按真实接口/由旧revision正向建夹具修正，未放宽断言或迁移保护。

最终干净Git检出32b85d6后端4619 passed/83 skipped（130.71s），pyflakes通过；新增19项针对性通过。83比前轮82多一项core/recovery装配/其它层import-lint参数跳过，不算覆盖增强。前端84文件767项默认/UTC通过（45.69/45.74s）、tsc/eslint及Next16.3.3生产构建通过，next-env已恢复；最终协调/发布/selector/卫生文档守卫233项通过，doc-health/public scan/workspace hygiene通过。前两次完整代码批4617/4618通过用于新增回滚/T+1真实缺口，最终结果不复用旧计数。Jev baseline及两次携带previousEvaluation的复评：正确性7.5→8.0→8.2、测试7.8→8.6→8.7、可靠性7.5→8.3→8.4，未reported regression；模型版本/tokens/费用未知，分数不替代作者反证、独立审核或金融效果。CI余额API403、未知，不启用付费，仅合批一次PR及必要master验证。

## 3. 作者反证、传播与收尾

**U49 主动审计回执 / 作者Preflight与反证**：核旧schema真实版本而非假设损坏、全表/文件分母、单库事务与全局停写差异、WAL/DuckDB、未知外部结果与租约、恢复先hold、缓存/别名/路径、部分失败/覆盖、各scope/水位/操作恢复、保留计划回滚、原源无写和私人记录边界。现有get_engine guard不替代OS隔离；手工直接构造连接仅用于获准离线验收。自审不是独立Review。

按ashare-task-handoff原位回填W09、部署合同、细功能§16、INDEX和本页；总方案/plan-registry/AGENTS/协作/Skills/产品导航/猎场/Jev/开放世界不适用长期设计变更：本项落实既有备份/恢复/停外发合同，不改模型、业务语义、角色、调度或交易准入。发布前本地门禁、准确diff和三job CI以本轮回执记录；无付费启用。

收尾保留紧凑日志/XML/manifest/hash和一份已验证私有备份；恢复测试副本、干净Git检出、pytest basetemp/构建缓存核无活动/唯一事实后清理。备份不按缓存删除，旧生产数据和依赖不动；活动RSH-031副本保留。后续继续先重算selector；本轮不自动领取OPS-003或新研究任务。
