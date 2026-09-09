# 仓库追踪台账（agent 分组）

> 用途：GitHub「agent」star 分组内各仓库的**用途 / 服务于哪个功能 / 使用轨迹 / 评估结论**。
> 本文件是仓库发现闭环的数据源：每周发现轮**先读本文件的「筛选经验」**改进检索与甄别 →
> 发现新候选 → 评估 → 回填本文件 → 系统内实际使用后更新「使用轨迹」→ 结论反哺经验。
> 面板消费：AI 控制台「仓库追踪」tab（Markdown 渲染）。
> 维护纪律：条目永不删除；淘汰/放弃标 ❌ 并写明原因——**失败原因是最有价值的筛选经验**。

## 状态定义

✅ 已采纳（进入系统依赖/代码）｜ 🔶 试用中 ｜ ⏳ 候选（已评估未使用）｜ ❌ 淘汰（原因必填）

## 分组收录（8 仓，2026-09-09 首轮，均 ≥1000★）

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

## 筛选经验（每轮发现前必读——下一轮检索与甄别的依据）

1. **形态匹配优先**：VSCode 扩展（leek-fund）、桌面 Wails 应用形态与我们的 Web 栈天然不兼容——除非只提取经验，否则直接跳过，省评估成本
2. **场景匹配**：crypto 7×24 做市（hummingbot/jesse/freqtrade）与 A 股 T+1 涨跌停生态差异大——只借鉴其**通知分层/工程实践**，不引入其交易逻辑
3. **测试覆盖是质量信号**：go-stock agent 层 27 个测试文件 vs tick-stock-panel 0 个——同等功能下选有测试的做深评
4. **搜索词要轮换**：本轮命中主要靠「llm trading agent / stock alert monitor」；下轮补充「连板 监控」「涨停 预警」「A股 agent」「dragon tiger」等我们领域的原生词汇
5. **≥1000★ 硬门槛**保持；trading 分组已有的仓不重复收录（去重表见 git log / kb 面板）
6. **A 股 LLM 应用层正在爆发**（TradingAgents 系 31k+、daily_stock_analysis 64k）——每轮必查该类新仓库

## 发现日志（append-only）

- **2026-09-09 首轮**：检索 10 组关键词 → 15 候选（≥1000★）→ 收录 8 仓（去重 trading 分组后）→ 深评 3（go-stock 编译通过/tick-stock-panel 装机通过/leek-fund 结构性）→ 浅评 5。经验 6 条。运行受限诚实记录：FinGPT 需 GPU、leek-fund 需 IDE 环境、其余需 LLM 多模型配置——均标注未实跑部分。
