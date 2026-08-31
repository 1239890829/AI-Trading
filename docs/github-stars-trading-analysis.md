# GitHub Stars「trading」分组深度分析报告

> 生成：2026-08-30 · **复核更新：2026-08-31（按真实 star 分组精确校正）**
> 分析对象：**GitHub 用户 `1239890829` 的 star 分组 `trading`——GraphQL `viewer.lists` 精确拉取，共 14 个仓库**
> 2026-08-31 复核结论：首版因 GitHub Star Lists 无 REST API 而改用"交易相关性"人工划定 13 个，
> 与真实分组相比 **漏 3 个**（freqtrade 53.9k / last30days-skill 60.7k / PolymarketBTC15mAssistant 1.0k）、
> **误收 2 个**（a-share-heatmap、FinceptTerminal 不在该分组）。漏掉的三者恰恰覆盖了首版缺失的
> 三块能力：**出场纪律**、**消息面扫描**、**另类市场打法**——见下表第 14–16 行。结论按仓库本身成立，不受分组方式影响。
> 对标基准：本项目 AShare AI Trader 现状（四源行情链 / 模拟撮合 / 题材梯队 / 情绪引擎 / 复盘 Agent / 新题材预判 / 做T信号+分钟回测底座 / 266 测试）。红线不变：禁实盘接入、禁确定性结论。

---

## 一、总览与结论速查

| # | 仓库 | ★ | 分类 | 一句话结论 |
|---|---|---|---|---|
| 1 | HiThink-Tech/Financial-API | 2.0k | **可替代/激活现有功能** | 就是我们在用的同花顺官方服务本体；价值=盘点未启用的官方能力（竞价/复权/marketdb） |
| 2 | handsomejustin/easy_tdx | 1.0k | **建议引入** | TDX 协议直连：分钟历史深度/逐笔历史/前复权/缠论，直击我们回测底座最大短板 |
| 3 | wenyuanw/a-share-heatmap | 251 | **建议引入**（前端） | A 股 treemap 云图，数据我们已有，缺的只是这层可视化 |
| 4 | TauricResearch/TradingAgents | 101.7k | 架构参考 | LLM 多角色编排+决策日志+checkpoint，复盘 Agent LLM 升级时的蓝本 |
| 5 | HKUDS/Vibe-Trading | 32.1k | 架构参考 | Shadow Account/PIT 数据纪律与我们已有设计互证，全能但无需引入 |
| 6 | ZhuLinsen/daily_stock_analysis | 64.3k | 架构参考 | 推送通道（企微/飞书/TG）架构可抄；其"买卖点位"结论风格违反我们红线 |
| 7 | virattt/ai-hedge-fund | 63.1k | 架构参考 | mandate 文件（策略与标的解耦）思想可用于回测参数化 |
| 8 | microsoft/qlib | 48.1k | 暂不采用 | ML 因子平台，依赖重、域不同频；远期做因子挖掘再评 |
| 9 | vnpy/vnpy | 44.9k | 暂不采用 | 实盘交易框架为核心（券商接口），与我们"禁实盘"红线正交 |
| 10 | OpenBB-finance/OpenBB | 72.5k | 暂不采用 | 数据平台层，我们数据源已够用且更贴 A 股 |
| 11 | Fincept-Corporation/FinceptTerminal | 30.8k | 暂不采用 | AGPL 终端产品，功能与 workbench 重叠，引入≈重写 |
| 12 | quantskills/quantskills | 2.2k | 暂不采用（收录观察） | 量化 Skill 社区目录，可当索引用 |
| 13 | rosemarycox5334-debug/AlphaMaster | 553 | 暂不采用 | MT5/加密强化学习因子挖掘，域不符 |
| 14 ✅ | freqtrade/freqtrade | 53.9k | **已择优落地（出场纪律）** | 值钱的不是选币而是**出场**：止损/跟踪止盈/ROI 分档/仓位管理/dry-run。已映射为本项目「风险档位 + 止损参考位 + 失效条件」（`app/picks/risk.py`，参数按 A 股重设）——这是"即使不涨停也能盈利"在规则层面的唯一落点 |
| 15 ✅ | mvanhorn/last30days-skill | 60.7k | **方法论内化（消息面）** | 跨 Reddit/X/YouTube/HN/Polymarket 的"近 30 天"主题研究范式。不引其依赖，把**滚动 30 天窗口 + 衰减**思想用在自有事件流上（EventCard 半衰期已是同构设计），Polymarket 源留待阶段 B |
| 16 | FrondEnt/PolymarketBTC15mAssistant | 1.0k | 观察（另类市场） | 预测市场隐含概率可作宏观/地缘事件的**前瞻信号**——这是"美股期货等其他市场打法"里唯一对我们有信息增量的部分；但事件→A 股题材映射需规则维护，且国内可访问性待验证，**暂不接入** |

**非交易类（原 8 个中的 7 个，已从 trading 分组中剔除 last30days-skill）**：FreeToken、LongHorizon-Harness、agency-agents、Cyber-Lobster-Soul、edict、autoresearch、Agent-Reach——AI/Agent 通用工具，不在本轮范围。

---

## 二、逐仓库深度评估

### 1. HiThink-Tech/Financial-API —— 可激活现有功能（价值最高的一发现）

**它是什么**：同花顺官方 A 股金融数据服务（fuyao.aicubes.cn）的本体仓库——**就是我们四源链第一优先级 `ths` 在用的那个服务**，同一 API Key 体系。项目里 `skills/hithink-finance/` 本就是它的官方 Skill 镜像。

**深度盘点后真正的价值**：不是"引入新东西"，而是发现我们**只用到了官方能力的约一半**。README 能力矩阵对照我们的使用现状：

| 官方能力 | 我们现状 | 影响 |
|---|---|---|
| 行情/K线/涨停池/龙虎榜/热榜/交易日历/财务 | ✅ 已深度使用 | — |
| **集合竞价快照 + 短期强弱基准** | ❌ 未接（预判模块已标 gap） | 新题材预判的 D1 竞价失效条件（"竞价无一字且板块高开≤2%"）目前只能次日人工核对——接上后预判验证可全自动化 |
| **公司行动 + 复权因子（前复权/后复权）** | ❌ 未用 | **我们回测底座的正确性缺口**：分钟回测直接用未复权价格，跨除权除息日的信号与价差全是错的。这是回测模块 P0 级修复项 |
| **marketdb 本地 DuckDB**（增量同步+SQL+复权计算） | ❌ 未用 | 我们自己写了 Parquet 分钟落盘（22 天×5m）；官方 marketdb 提供日 K/公司行动的标准落库+增量同步，可作为日级历史的事实底座 |
| 估值批量快照（PE/PB/PS 全市场） | 部分（行情快照带估值字段） | 选股器（Phase 5 待办）可白嫖 |
| 公募基金数据 | ❌ 未用 | 当前无基金需求，记录备查 |
| 全市场数据导出（Market Dumps） | ❌ 未用 | 选股器/因子研究的数据地基 |

**结论：可替代/激活现有功能。** 行动项：① 接 auction 端点进预判模块；② 用公司行动+复权修回测正确性；③ 评估 marketdb 替换自写 Parquet 日级落盘（分钟级仍需 TDX，见下）。成本均为 Provider 层新方法（1 天级），零新依赖。

---

### 2. handsomejustin/easy_tdx —— 建议引入（数据层，直击最大短板）

**它是什么**：通达信协议直连的开源 Python SDK（MIT、免 Key、`pip install easy-tdx`），K 线/报价/分时明细/逐笔成交/板块资金流/竞价/公告（巨潮）全免费，外加 34 个技术指标、**缠论分析**（笔/中枢/买卖点/背驰）、**内置回测引擎 + Vue3 Web UI + S/A/B/C/D 五档评级**（卡玛/回撤/胜率/利润因子等六维加权+一票否决）、策略库 SQLite、参数网格寻优、`easy-tdx serve` 一键 REST。

**补我们什么短板**：

1. **分钟历史深度**——我们回测底座受制于"腾讯 mkline 320 根无翻页、新浪 5m×22 交易日"（见五档评估与分钟回测记录），22 天样本连阈值校准都不够。TDX 协议的分钟 K 历史深度业界普遍远超免费 HTTP 源（具体深度依行情服务器，**接入前先实测 600519 的 m1/m5 可回溯天数**，实测前不下结论）。
2. **逐笔成交历史**（`transaction --date`）——我们逐笔只有盘中实时，无历史；做 T 信号回测的撮合精度可升级。
3. **前复权**（`--adjust QFQ`）——与 ths 复权因子二选一或互验，修回测正确性缺口。
4. **缠论**——我们没有的能力，低成本获得（笔/中枢/背驰可作为做 T 信号的第 6 个候选指标，须过样本外验证）。

**客观甄别——不替换的部分**：它的 34 指标/回测引擎与我们的 `technical-analysis.ts` + 分钟回测底座重叠，但我们的实现带 as_of 防未来函数前缀属性测试 + A 股口径撮合（T+1/涨跌停拒单），**引擎不换**；只把 TDX 当**数据通道**接入 `MarketDataProvider` 协议（加一个 `tdx.py` Provider 进链尾或作分钟历史专用源）。它的 S/A/B/C/D 评级思想与我们的 strength_tier（溢价一票否决）同源，互相印证，不需引入其评级代码。

**风险**：TDX 协议是民间逆向的 TCP 协议，服务器列表需维护、稳定性不如官方 API——**定位为"回测/研究数据源"而非盘中实时源**（实时我们已有四源链）。

**结论：建议引入（P1，先实测分钟深度与一周稳定性再定去留）。**

---

### 3. wenyuanw/a-share-heatmap —— 建议引入（前端可视化）

**它是什么**：Next.js + Canvas 的 A 股大盘云图：矩形树图按流通市值定块大小、涨跌幅定颜色，支持全市场/沪深300/创业板等范围切换、行业下钻、多周期（当日/5日/20日/今年以来）、自选热力图、市场概览面板（涨跌家数/量能变化）。

**对我们**：boards 页目前是**列表排行**，缺的正是这种"一屏看清涨跌结构与板块轮动"的形态——而且数据我们全有（全市场快照每 5 分钟落 Parquet，含流通市值），缺的只是 treemap 渲染层。它还带 WebMCP 工具与截图分享。自选热力图（按我们的 watchlist 生成个人云图）是差异化点。

**结论：建议引入（P1.5，新增 /heatmap 页，复用我们的快照数据 + 借鉴其 Canvas 矩形树图实现；注意其 AGPL/MIT 许可需核对——借鉴思想自绘则无许可问题）。**

---

### 4. TauricResearch/TradingAgents —— 架构参考（101.7k★，领域标杆）

LLM 多角色交易框架（分析师/研究员/交易员/风控角色辩论，LangGraph 编排）。对我们的参考点：**结构化输出 agent + 决策日志持久化 + LangGraph checkpoint 恢复 + 数据访问契约**。这正是复盘 Agent `LLMAnalyzer`（现为占位壳）升级时的角色分工蓝本——我们的复盘"三维度分析→改进项→元结论"天然映射到它的角色编排。

**边界**：它是美股数据生态（Alpha Vantage/FRED/Polymarket），输出**交易决策**（违反我们红线 3）——借架构不借结论，数据层不用。

### 5. HKUDS/Vibe-Trading —— 架构参考（互证为主）

FastAPI+React 全能研究工作台：跨市场数据+回测、自改进 agent+记忆、**Shadow Account**（影子账户=对经纪行为的规则化对照诊断——与我们 paper 撮合+复盘 Agent 同域，我们已有等价物且更贴 A 股口径）、**PIT（point-in-time）数据验证**——与我们 backtest-rules 的防泄露禁令互为印证，说明该纪律是业界共识而非我们过度设计。日志可见其认真对待 kill-switch 失效这类工程细节，工程可信度高。依赖 akshare 做 A 股数据（免费源稳定性声明与我们实测一致）。

**结论：不引入（我们已有等价能力且更垂直），作为"我们走在正确方向上"的旁证与未来功能对照表。**

### 6. ZhuLinsen/daily_stock_analysis —— 架构参考（推送通道）

LLM 驱动的自选股每日分析+推送（企微/飞书/TG/Discord/Slack/邮箱），GitHub Actions 零成本部署，多 LLM 路由（DeepSeek/Qwen/GLM/Ollama 本地），多源行情 fallback（AkShare/Baostock/Pytdx…）。我们的复盘 Agent 缺的正是**最后一公里推送**——报告躺在库里等人看，它证明推送层是成熟低成本的。

**边界**：其 AI 报告输出"买卖点位/操作建议"（违反我们红线 3）；行情源主打免费无 token（稳定性其 README 自认不保证——我们四源链+质量分级更严）。**借推送架构与 LLM 路由思路，不引结论风格、不引数据源。**

### 7. virattt/ai-hedge-fund —— 架构参考

教育性 AI 对冲基金（明确定位 educational、不真实交易）。可借鉴点：**mandate 文件**——把"策略/人员/风险/资金/节奏"与"标的"解耦成声明式文件，一个 mandate 可回测可复跑。我们的回测底座目前参数硬编码在 CLI 参数里，mandate 化（yaml 声明回测配置）是顺手且正确的演进方向。美股数据生态，不引数据层。

### 8. microsoft/qlib —— 暂不采用

AI 量化平台（因子库/模型/回测/RD-Agent 因子挖掘）。**暂不采用的理由**：依赖重（完整数据管线+ML 栈）、面向日频截面因子研究，与我们的短线博弈/题材梯队/盘口做 T 域不同频；我们的分钟数据深度也喂不饱它。**触发重评条件**：Phase 5 选股器进化到"多因子截面选股"且数据底座（marketdb/TDX）铺好后。它的 RD-Agent（LLM 自动因子挖掘）值得远期关注。

### 9. vnpy/vnpy —— 暂不采用

VeighNa 量化交易框架，核心价值在**券商/期货实盘接口网关**与事件驱动 CTA 引擎——与我们"禁实盘"红线正交；其回测/数据管理我们已有垂直实现。**触发重评条件**：无（除非红线变更，那不是技术问题）。

### 10. OpenBB —— 暂不采用

"connect once, consume everywhere" 数据平台层（Python/MCP/Excel/Workspace 多端）。我们已自建等价层（Provider 协议+质量分级+Envelope），且 OpenBB 的 A 股覆盖来自社区源（我们直连官方 ths 更强）。它的**平台化思想**（同一数据多端消费）我们已用 REST+WS+Parquet 实现。不引入。

### 11. FinceptTerminal —— 暂不采用

AGPL 终端产品（企业版闭源收费，社区版月更）。功能与 workbench 大量重叠（行情/分析/组合），引入=二次造轮子+AGPL 传染性许可风险（我们 MIT/私有，需隔离）。个人学习参考可以，集成价值为负。

### 12. quantskills/quantskills —— 暂不采用（收录观察）

量化 Skill 社区目录（PandaAI 发起）：可发现/可安装/可验证的 Skill 注册表。本质是**索引**不是能力。价值：当我们要给复盘/预判 Agent 找现成社区 Skill 时先查这里；其 skill-template 的"声明数据来源/假设/限制/风险边界"规范与我们 DataGap+失效条件纪律同构。

### 13. AlphaMaster —— 暂不采用

强化学习因子挖掘流水线（MT5/加密 K 线，`tanh(因子)` 连续仓位，飞书转折提醒）。域不符（外汇/加密 H1 周期）；AGPL。其"训练/回测/实时共用同一套信号逻辑（StackVM 解释执行）"是正确工程（与我们信号引擎单一实现口径一致），远期做 ML 因子时的参考。

---

## 三、客观甄别：我们已足够优秀、无需重复建设的部分

放下自尊心逐项核对后，以下能力**经外部对照确认无需重建**：

1. **数据源纪律**：四源逐方法 failover + 质量五级 + DataGap 强制标注——强于本清单所有项目的数据层（daily_stock_analysis 自认免费源不稳定，Vibe-Trading 依赖 akshare，OpenBB A 股靠社区源）。
2. **模拟撮合的 A 股口径**：T+1/涨跌停拒单/整手/费用全配置化——本清单没有任何项目带这套（TradingAgents/ai-hedge-fund 是美股 T+0 语义）。
3. **题材梯队归属唯一化**（连板密度多数票）与**强弱分级**（溢价一票否决）——无外部同类实现。
4. **复盘方法论版本化 + 元结论采纳率/回退率闭环**——TradingAgents 有 decision log 但无"哪种方法有效"的统计迭代闭环。
5. **as_of 防未来函数的前缀属性测试**、backtest-rules 禁令——与 Vibe-Trading 的 PIT 纪律互证，且我们落到测试级。
6. **工程门禁**：266 测试 + 错误契约 + response_model + API 层收口——对照之下我们的工程化已越过大多数同类项目。

## 四、行动清单（按优先级）

| 级别 | 事项 | 来源 | 工作量 |
|---|---|---|---|
| ~~P0~~ ✅ | ~~回测正确性：接 ths 公司行动+复权因子~~ 已完成（2026-08-30）：get_adjustment_events + apply_adjustments 前复权修正，茅台实测 30 条事件（最新 2026-06-26 每股分红 28.02 元在窗口内） | Financial-API 盘点 | 1 天 |
| ~~P0~~ ✅ | ~~预判模块接 ths 集合竞价端点~~ 已完成（2026-08-30）：竞价前兆证据（定性 0 权重）+ D1 竞价一致性验证（一字判定/失效条件#1/风向标联动），实测我爱我家周五竞价 +3.75% 量比 1.81 → 当日涨停，证据链闭环 | Financial-API 盘点 | 0.5 天 |
| **P1** | ~~easy_tdx 试点：实测~~ ✅ 实测完成（2026-08-30，easy-tdx 1.20.12）：**5 分钟 23760 根=495 交易日（约 2 年）、1 分钟 22560 根=94 交易日（4.5 个月）**，vs 新浪 22 天为 22×/4.3×；TDX→引擎 schema 转换冒烟通过（240 点/日，avg 无越界，引擎直接消费）。**接入已完成（2026-08-30）**：backfill_tdx→minutes-tdx parquet（QFQ）+ run_backtest adjusted 标记 + CLI --source=tdx；2 年窗口实测 4 票 **1753 信号、样本外 hit_rate 0.662 ≥ 样本内 0.641**（无过拟合迹象），错误主因 avg_dev 519/536；精确量比基线同批上线 | easy_tdx | ✅
| **P1.5** | /heatmap 云图页（treemap，复用全市场快照；自选云图差异化） | a-share-heatmap | 2 天 |
| **P2** | 复盘 Agent LLM 升级时按 TradingAgents 角色编排 + 决策日志；报告推送通道（企微/飞书/TG）按 daily_stock_analysis 架构 | TradingAgents / daily_stock_analysis | 升级时并入 |
| **P2** | 回测配置 mandate 化（yaml 声明） | ai-hedge-fund | 0.5 天 |
| 备查 | marketdb DuckDB 评估（日级历史落盘）、quantskills 目录、qlib/AlphaMaster 触发条件已注明 | 各仓库 | — |

**一句话总结**：这批 Stars 里没有能整体替代我们系统的东西——但有三个精准补件（官方未启用能力、TDX 分钟历史、treemap 云图）和四个值得抄的架构（多角色 LLM 编排、推送通道、mandate、PIT 互证）。最意外的发现是第 1 项：**最强的补件一直躺在我们自己已付费的官方服务里**。
