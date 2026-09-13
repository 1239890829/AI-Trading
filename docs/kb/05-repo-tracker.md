# 仓库追踪台账（agent 分组）

> 用途：GitHub「agent」star 分组内各仓库的**用途 / 服务于哪个功能 / 使用轨迹 / 评估结论**。
> 本文件是仓库发现闭环的数据源：每周发现轮**先读本文件的「筛选经验」**改进检索与甄别 →
> 发现新候选 → 评估 → 回填本文件 → 系统内实际使用后更新「使用轨迹」→ 结论反哺经验。
> 面板消费：AI 控制台「仓库追踪」tab（Markdown 渲染）。
> 维护纪律：条目永不删除；淘汰/放弃标 ❌ 并写明原因——**失败原因是最有价值的筛选经验**。

## 状态定义

✅ 已采纳（进入系统依赖/代码）｜ 🔶 试用中 ｜ ⏳ 候选（已评估未使用）｜ ❌ 淘汰（原因必填）

## 分组收录（10 仓，2026-09-09 首轮 8 仓 + 2026-09-13 用户供仓 2 仓，均 ≥1000★）

### ArvinLovegood/go-stock — 7.5k★ Go
- **用途**：AI 股票分析 + 涨跌报警推送（A股/港股/美股）
- **实际运行评估**：`go build ./backend/agent/` **编译通过**（Go 1.24 + eino LLM 框架）；agent 层 27 个测试文件（checkpoint/resume/fact_check/feedback/intent 全覆盖）
- **可借鉴**：① agent 断点续跑（checkpoint/resume）；② **fact_check 事实核查层**（AI 结论先核查再推送——值得加进我们的 triage）；③ feedback + user_profile_learner（用户反馈修正提醒偏好）；④ 飞书长连接机器人（双向对话，不只是推送）；⑤ 外推卡片截断 3000 字
- **使用轨迹**：🔶 未接线。fact_check 模式候选进提醒告警优化（挂 P2）
- **结论**：🔶 保留观察——agent 层工程化程度是我们目前最接近的对标

### shy3130/tick-stock-panel — 4.5k★ Python
- **用途**：自托管 A 股「选股 + 监控 + 回测」量化工作台（LLM 策略定制 + 个股分析 + 复盘）
- **实际运行评估**：`uv pip install -e .` 装机成功（52 依赖，polars 系）；`import app` 冒烟通过；**无测试套件**（tests 空目录）
- **可借鉴**：① **四类监控分类**（策略/个股信号/价格/异动，多条件 AND/OR）——比我们的规则类型更清晰；② **语音播报**渠道；③ 连板梯队 + 封单监控（与我们重叠，对照实现查漏）
- **使用轨迹**：🔶 未接线。四类监控分类法已用于本轮告警审计（见 docs/hunting-review）
- **结论**：🔶 保留观察——监控分类法值得抄，代码质量一般（无测试）

### LeekHub/leek-fund — 3.8k★ TypeScript
- **用途**：VSCode 韭菜盒子——自选行情 + 涨跌提醒（价格/涨跌幅，IDE 内通知）
- **实际运行评估**：结构性评估（VSCode 扩展，未装 IDE 环境）——提醒配置粒度：每标的独立开关 + 价格/涨跌幅双阈值
- **可借鉴**：**按标的开关提醒**的粒度设计（我们目前是全局规则）；极简设置交互
- **使用轨迹**：❌ 不接入（VSCode 扩展形态与 Web 系统不兼容）——但按标的提醒开关记入经验
- **结论**：❌ 淘汰（形态不兼容），经验已提取

### AI4Finance-Foundation/FinGPT — 21k★ Python
- **用途**：开源金融 LLM（新闻情绪打分/因子化）
- **实际运行评估**：未深评（模型推理需 GPU，超本机沙箱）——README/架构级
- **可借鉴**：新闻情绪→因子化的管道设计（消息面因子的参考实现）
- **使用轨迹**：⏳ 候选。待消息面因子立项时深评
- **结论**：⏳ 收录观察

### AI4Finance-Foundation/FinRobot — 7.9k★ Python
- **用途**：金融 AI Agent 平台（多 agent 编排）
- **实际运行评估**：未深评（同上，架构级）
- **可借鉴**：market forecasting / document analysis / strategy 各 agent 的职责切分
- **使用轨迹**：⏳ 候选
- **结论**：⏳ 收录观察

### hsliuping/TradingAgents-CN — 31.7k★ Python
- **用途**：TradingAgents 中文增强版（多 Agent LLM 交易框架，A股数据源适配）
- **实际运行评估**：未深评（与 TauricResearch/TradingAgents 同源，trading 分组已有原版）
- **可借鉴**：A股数据源适配层（tushare/akshare 接法）
- **使用轨迹**：⏳ 候选（与原版二选一深评时优先原版社区）
- **结论**：⏳ 收录观察

### simonlin1212/TradingAgents-astock — 3.2k★ Python
- **用途**：A股多 Agent 投研框架——龙虎榜/游资/解禁等 A股特色数据源 + 7 分析师辩论
- **实际运行评估**：未深评（需 LLM 网关多模型配置）
- **可借鉴**：**龙虎榜/游资数据进 agent 论证**的结构（我们已有龙虎榜数据，缺的是让它进分析链）
- **使用轨迹**：⏳ 候选
- **结论**：⏳ 收录观察

### hummingbot/hummingbot — 19.9k★ Python
- **用途**：高频做市/交易机器人（成熟的通知/webhook 生态）
- **实际运行评估**：未深评（做市场景与 A 股 T+1 不匹配）
- **可借鉴**：事件通知分层（info/warning/critical + per-channel 路由）——与我们的推送矩阵同构验证
- **使用轨迹**：❌ 不接入（场景不匹配），分层通知经验已提取
- **结论**：❌ 淘汰（场景不匹配），经验已提取

### muxuuu/serenity-skill — 4k★ Markdown/Python（agent skill 包，用户供仓）
- **用途**：把博主 Serenity 的投研方法论封装成 skill——主题 → 产业链八层 → 稀缺层识别 → 标的排序 → 证据核验（28 文件：SKILL.md + 8 份方法论文档 + 瓶颈评分卡 + 2 个轻脚本）
- **实际运行评估**：结构性评估（2026-09-13）：文件树核验 28 文件、`deep-research-workflow` / `evidence-ladder` 正文抽读——**内容是真方法论非水词**；skill 本体未装（我们自己就是 agent，装提示词包无意义）
- **可借鉴**：①「主题→系统变化→受约束环节」三段转换；②八层产业链 checklist；③稀缺层信号堆叠（含「市场仍按旧行业归类公司」的错误定价视角——六条中最值得自动化的一条）；④证据阶梯的「候选证据标准」与「弱证据不得单独支撑头部候选」；⑤「点名排名低的热门方向并说明为什么」的完成标准（与红线 3 同频）
- **使用轨迹**：✅ **方法论已提炼**进 [[KB-STOCK-34]]；「卡点」排序因子登记 §6.3 P2-34
- **结论**：⏳ 收录——skill 不装、方法论已入 KB；因子化落地前必须过本仓实证核验（方法论是 N=1 博主经验）

### wbh604/UZI-Skill — 6.9k★ Python（agent skill 插件，A 股全流程分析，用户供仓）
- **用途**：单票 22 维数据采集（akshare/efinance/tushare/baostock 多源 failover）→ 22 种华尔街模型 → 66 位模拟投资人评委（规则引擎）→ 600KB HTML 报告；14+ slash 命令（含 `scan-trap` 杀猪盘排查）
- **实际运行评估**：结构性评估（2026-09-13）：471 文件核验（`lib/pipeline` 管线库 + 13 万字 BUGS-LOG + CI workflow，工程化认真）；**未实跑**（5-8 分钟/票、LLM 重度）
- **可借鉴**：①**scan-trap 杀猪盘排查**——我们风控/选股器均无庄股识别维度（登记 §6.3 P2-35）；②**估值模型层**（DCF/Comps/IC 备忘录）——六维基本面维无估值概念（登记 §6.3 P2-36）；③数据缺口 `_data_gaps.json` 显式暴露机制（与我们质量五级同思想，无需引入）
- **使用轨迹**：❌ 不接入运行时（分钟级 LLM 流水线 vs 秒级实时链路；数据源为爬虫聚合、无质量五级口径）；**用户侧离线二次意见工具可选装**（装进自己的 agent 跑 `analyze-stock`/`dcf`，与系统解耦）
- **结论**：❌ 不接入——两项经验已提取登记 P2；66 评委输出为模拟观点且带买卖倾向，任何结论引用须过红线 3 声明

## 筛选经验（每轮发现前必读——下一轮检索与甄别的依据）

1. **形态匹配优先**：VSCode 扩展（leek-fund）、桌面 Wails 应用形态与我们的 Web 栈天然不兼容——除非只提取经验，否则直接跳过，省评估成本
2. **场景匹配**：crypto 7×24 做市（hummingbot/jesse/freqtrade）与 A 股 T+1 涨跌停生态差异大——只借鉴其**通知分层/工程实践**，不引入其交易逻辑
3. **测试覆盖是质量信号**：go-stock agent 层 27 个测试文件 vs tick-stock-panel 0 个——同等功能下选有测试的做深评
4. **搜索词要轮换**：本轮命中主要靠「llm trading agent / stock alert monitor」；下轮补充「连板 监控」「涨停 预警」「A股 agent」「dragon tiger」等我们领域的原生词汇
5. **≥1000★ 硬门槛**保持；trading 分组已有的仓不重复收录（去重表见 git log / kb 面板）
6. **A 股 LLM 应用层正在爆发**（TradingAgents 系 31k+、daily_stock_analysis 64k）——每轮必查该类新仓库

## trading 分组 diff 台账（2026-09-09，22 仓快照 vs 历史已评）

> 2026-09-09 拉取 trading 分组 22 仓，与 docs/archive/github-stars-trading-analysis.md
> （08-30 深评）+ github-repo-audit-financial-api-sequoia-x.md（09-03）做差集——
> **已评 14 仓直接引用历史结论（不重复评估）**；真新增 8 仓浅评如下。

### A. 已评仓（引用 archive，状态不变）
持续使用：Financial-API（ths fuyao 官方，四源链首源）、akshare（双源校验）、akquant（talib rust）
保留参考：freqtrade（出场纪律已落地）、myhhub/stock（筹码反面教材）
归档/不采用：TradingAgents（12 次 LLM/票不可接受）、ai-hedge-fund、daily_stock_analysis、
Vibe-Trading、qlib、OpenBB、quantskills、zvt
废弃：vnpy（禁实盘红线正交）
忽略：Sequoia-X（无 LICENSE，09-03 已判）

### B. 真新增 8 仓浅评（本次）
| 仓库 | 判定 | 一句话 |
|---|---|---|
| KylinMountain/AlphaAgents (5★, 09-09 push, 有 LICENSE) | ⏳ 候选试点 | 「会因亏过钱改变下次判断的 AI 交易员」——新闻驱动自主选股+反思记忆，场景最贴我们的消息面/议程自动演进；星低但方向精准，P2 TradingAgent 演进参照物之一 |
| Tencent/WeKnora (22k★ Go) | 归档观察 | 腾讯 LLM 知识平台（KB/RAG/多 agent 读取）；我们 KB 已自建+议程第八路消费，触发条件=KB 规模爆炸再评 |
| waditu/czsc (6k★ Rust 缠论) | 归档观察 | 缠论 Rust 库，集成成本高；easy_tdx 已带缠论（历史采纳），无新增 |
| zvt (4.3k★) | 归档 | modular quant 中频框架，与 qlib 同族域不同频 |
| stockstats (1.5k★) | 归档 | pandas 指标包装，域是 Python vs 我们 TS technical-analysis |
| cvxportfolio (1.3k★ Stanford) | 归档远期 | 学术组合优化；我们无组合需求，仓位引擎规则级已够 |
| tqsdk-python (5k★) | 废弃 | 期货域外 |
| efinance (4k★) | 不引入 | 与 akshare 东财源重叠（日 K 冒烟可拉，涨停池接口不匹配），非替换项 |

**换血结论**：真新增无「替换级」候选——金融/量化大仓 08-30~09-03 已评完并落地；
数据源层早为 ths fuyao + easy_tdx + akshare + 腾讯多源，无需新源。
**剩余真实差距回到系统内部**（因子库 0 消费 / 形态薄弱 / 记忆效应排序），不依赖新 repo。

## 历史审计索引（archive 专项审计归口，2026-09-10）

> 用途：查"某仓库/工具评过没、结论是什么"**先看本表**，再按指针翻 archive 详情（archive 只读、不再更新）。
> 主表：`archive/github-stars-trading-analysis.md`（trading 分组 16 仓速查表 + 逐仓深评）——**唯一总表，勿另造**。

| 专项审计（`docs/archive/`） | 覆盖对象 | 一句话结论 | 时间 |
|---|---|---|---|
| **github-stars-trading-analysis.md** | trading 分组 16 仓 | **主表**：结论速查 + 逐仓评估（含 2 项已落地） | 09-03 |
| github-repo-audit-chips-pattern-chanlun.md | 筹码分布 / TA-Lib 形态 | 筹码不自研（akshare `stock_cyq_em` 取东财已算结果）；TA-Lib 日线形态噪音大 → 放弃引入，改自实现已有 3 形态 | 08-30~09-03 |
| github-repo-audit-dsa-misc.md | daily_stock_analysis 等 | star 真实性=真项目 + 营销推高（star≠深度用户）；架构参考、不引入依赖 | 08-30~09-03 |
| github-repo-audit-financial-api-sequoia-x.md | 同花顺官方 API / Sequoia-X | Financial-API = 在用的 fuyao 本体（价值=盘点未启用官方能力）；Sequoia-X 无 LICENSE → 忽略 | 08-30~09-03 |
| github-repo-audit-frameworks.md | freqtrade / vnpy / OpenBB / AlphaMaster | **freqtrade 回测引擎对本项目不可用**（撮合假设根本差异，硬用产系统性乐观偏差）；其余暂不采用 | 08-30~09-03 |
| github-repo-audit-qlib-quantmind.md | qlib / QuantMind | 不引入 qlib 本体；取其「涨跌停按板块映射」等 3 项做法 | 08-30~09-03 |
| github-llm-agent-audit.md | LLM Agent 类项目 | 编排不引入；采纳「置信度数值必须附生成它的规则引用」的 prompt 契约 | 08-30~09-03 |

## 发现日志（append-only）

- **2026-09-09 首轮**：检索 10 组关键词 → 15 候选（≥1000★）→ 收录 8 仓（去重 trading 分组后）→ 深评 3（go-stock 编译通过/tick-stock-panel 装机通过/leek-fund 结构性）→ 浅评 5。经验 6 条。运行受限诚实记录：FinGPT 需 GPU、leek-fund 需 IDE 环境、其余需 LLM 多模型配置——均标注未实跑部分。
- **2026-09-13 用户供仓批次**：用户指路 2 仓（serenity-skill 4k★ / UZI-Skill 6.9k★，非发现轮检索产物）→ 结构性评估（文件树核验 + 方法论/数据源文档正文抽读，均未实跑——实跑成本与形态不匹配已如实标注）→ serenity 方法论提炼进 [[KB-STOCK-34]] 并登记 P2-34（卡点因子）；UZI 两项经验登记 P2-35（杀猪盘排查）/ P2-36（估值维度），本体裁定不接入运行时。**新增一条来源类型：用户直接供仓**——评估纪律不变（结构核验优先于 README 宣称），收录门槛不变（两仓均 ≥1000★ 达标）。
