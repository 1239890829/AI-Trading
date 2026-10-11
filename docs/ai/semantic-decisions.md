# 语义判断与外部决策服务

> 2026-10-11用户要求退役不可用Jev；本页取代原Jev现役蓝图。退出范围与状态只在[W07/IMP-091](../stages/w07-simplification.md#imp-091)。原蓝图与调用实测保留历史身份，不产生当前采用或调用义务。

## 1. 当前执行路径

| 用途 | 当前基线 | 退出项 | 失败与保护 |
|---|---|---|---|
| 提醒判读 | 规则时效/去重→已配置LLM→明确规则降级 | Jev影子、级联、高置信直用、后台比较worker | 消息资格、风险、singleflight、真实thread drain、预算、来源标签均保留 |
| 事件题材/方向 | 目录/时效/版本规则→已有批量LLM | Jev中性前置和双模型比较 | 原CAS与全批失败不标记；未知/中性/待复核保持原义 |
| 助手工具 | 执行注册表与真实依赖→当前生成模型选只读工具 | Jev工具组路由与RouteTrace | 不扩权限；不可用工具既不进提示词也不能执行；流式取消/资源预算/使用量保留 |
| 答案核验 | 已注入数据与工具原文→grounding数字/越权校验 | 外部Jev claim/evidence影子核验 | 不宣称具备完整语义真伪验证；事实不足照实写未知 |
| 研究评估 | 人工盲标/导入、队列与预测hash绑定、完整/部分分母评分 | Jev自动预标注与研究专属模型调用 | 人工gold与历史数据保留；规则、模型预测均不能当gold |
| 研发审查 | 作者反证、代码/行为/完整CI与准确HEAD发布门 | Jev评分必须调用的义务与不可用插件依赖 | 正常独立Review/降级DegradedRelease身份、release_check及平台保护不降级 |

项目不再向TypeSafe发请求，不读取其凭据，不为它注册后台任务。历史数据库中的provider/model与旧审核失败回执不改写。安装第三方库包含同名字串不等于Jev消费者，不删除无关依赖。

通用研究工具：`backend/scripts/semantic_goldset.py`负责export-events/export-blind/import-human/validate/prioritize/compare-reference/score；`backend/scripts/rationale_audit.py`负责机会理由覆盖与原证据审计。旧Jev预标注命令退出；现有人工标注和预测文件保持原字段，不伪造新的概率含义。

## 2. OpenAI Decisions API：已核事实与采用边界

2026-10-11已打开官方[指南](https://developers.openai.com/api/docs/guides/decisions)及[Python API参考](https://developers.openai.com/api/reference/python/resources/decisions/methods/create)。接口为`POST /v1/decisions`，公开beta，指南当前列出的模型为`gpt-6-luna`。支持文本/图像与多问题，返回`predicate`条件概率、`choice`固定选项、`score`有序等级加权评分；也可能返回refusal。

基础价格按指南为每百万输入token 0.10美元，只计输入；地区处理与长上下文可能加价。官方约10倍于Responses的速度是厂商描述，未经本项目同输入、同质量、端到端实测，不能写成已取得收益。账户权限、地区/网关支持和余额当前未验证；Codex/ChatGPT订阅不当API额度。

这是候选替代，不是本轮生产默认；没有安装SDK、配置新Key、开启付费或新语义调用。当前`claude_cli`兼容生成链不能证明它能代理`/v1/decisions`，不得猜测可复用凭据。

### 2.1 对应关系与不兼容点

| 原窄判断需求 | Decisions候选 | 必须处理 |
|---|---|---|
| 条件成立/相关性 | predicate | 原noul字段不是新API字段；重新验证概率与样本阈值 |
| 提醒分类/事件类别/工具候选组 | choice | questions为数组、选项对象与返回概率数组；名字/顺序/类型/覆盖必须核对 |
| 有序严重度/审查rubric | score | score是等级索引的概率加权值，不是任意0–100自评分或正确率 |
| 跨问题依赖 | 分步调用 | 独立问题可同批；依赖前一个答案的不能假装并行 |
| 缺失/拒答/超时/格式错 | unknown或已有baseline | 不伪造0/中性、不把运输成功当语义正确 |

### 2.2 后续准入步骤

1. 仅选一个有真实消费者的场景，例如公开新闻类别或提醒优先级；不从全市场逐条调用起步。
2. 用户明确同意账户、费用上限与调用范围，Key只进后端secret。固定模型、question/rubric版本和脱敏输入；保护私有账户/聊天与提示注入边界。
3. 复用既有预算/使用量和人工gold流程，隔离LAB比较现有规则/LLM与候选。分母覆盖成功、误报、漏报、拒答、失败、域外和未知；统计质量、延迟P50/P95、真实token/成本、采纳与升级率。
4. 先shadow，不改变现役评分、消息资格、风控或资金。不同模型的旧阈值不得直接搬用；独立人工标签校准并达成原准入条件后才由原owner批准有限消费。
5. 达不到收益/费用/质量目标则结束实验，保留原baseline；失败不能阻塞取行情/读选股、风险提示或用户操作。

不把模型概率、confidence或自评当股价上涨概率、胜率、事实正确率或发布批准。价格、权限、T+1、涨跌停、风控、账户与执行一律由确定性代码裁定。

## 3. 治理与验收

遵守现行v9.13、U49主动反证与Preflight、U50 `DEGRADED_FULL_CONTROL`和`DegradedRelease`准确HEAD回执；正常独立Review与作者自审不可互相冒充。只退出Jev专属评分调用，不退出其余完整门禁、独立审核、发布或恢复能力。跨模块变化按[重大决策传播](../plan-registry.md#11-重大决策传播契约防遗漏)核对总方案/INDEX/AGENTS/Skills/stage/handoff。

[持续演进](continuous-evolution.md)负责候选/实验/采用边界；本页不另建任务账本或生产参数登记册。用户已确认项目接入与本机插件、skill一起清理。Jev Review/Guard经官方入口卸载；专属MCP、四个skill、CLI、Keychain注入服务、环境hook及只供旧Jev浏览器的8777桥退出。全局配置先备份且解析对照无关字段；现有生成模型配置hash不变。历史使用量/路由元数据只保留在私有恢复包，不删除业务数据或人工gold。恢复包位于忽略artifacts，仅人工受控恢复，不能重新产生自动调用义务。
