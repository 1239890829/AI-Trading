# GitHub star 分组 trading 审计（第 4 组）：daily_stock_analysis + 杂项

> 审计日期：2026-09-03。审计对象：trading 分组最后 4 个仓库。
> 方法：clone/下载源码到 `/tmp/gh-audit/<repo>` 实际查看，daily_stock_analysis 做了**真实安装运行**（mock LLM 端到端）。
> 证据等级标注：**A = 实际运行验证**；**B = 读了代码未运行**；**C = 只看元数据/README**。
> 关联文档：`docs/github-repo-audit-dsa-misc.md`（本文件）。前几组报告见 docs/ 下其他 audit 文档。

---

## 一、ZhuLinsen/daily_stock_analysis（64530 star）

**一句话定位**：基于 LLM 的多市场自选股每日分析 + 决策仪表盘推送系统（Python，33 万行 + 15 万行测试），与我们的场景重合度是四组审计中最高的——但它是「自选股分析器」，不是「市场情绪/题材结构引擎」，重合面比标题看起来小。

### 1.1 star 真实性判定：真项目 + 病毒式营销推高（证据等级 A/B）

**结论先行：不是刷出来的假 star，但 6.4 万 star 有明显营销水分；代码和社区是真材实料。**

| 信号 | 数值 | 判读 |
|---|---|---|
| 建仓时间 | 2026-01-10 | 8 个月涨 6.4 万星，速度异常 |
| Forks | 54094 | **fork/star = 0.84**，远超正常仓库（0.1~0.3）|
| Watchers | 255 | 6.4 万 star 仅 255 关注，典型「fork 即用即走」结构 |
| Contributors | 120+（贡献页共 120 页），页首 100 人贡献 955 次 | 真实多人社区 |
| Issues / PRs | 837 / 1440 | 真实活跃度，非空壳 |
| 测试 | 275 个测试文件、约 15 万行 | 工程成熟度极高 |
| 代码 | 618 个 py 文件、33.2 万行 | 绝非玩具 |
| Releases | 周更（v3.27→v3.31，7-8 月连发 5 版）| 持续维护 |

**水分来源判定**（证据 B）：README 带 Trendshift「#1 Python Repository Of The Day」、HelloGitHub 推荐、B 站教程视频（`BV11FEb66EXH`）、以及 Anspire / AIHubMix / SerpAPI 三家**带返利参数的赞助商链接**（`?share_code=`、`?aff=`、`utm_source=github_daily_stock_analysis`）。「fork + 配 secret 零成本白嫖」的使用模式天然驱动大量 fork/star（fork 才能用 GitHub Actions），叠加自媒体教程传播 → star 曲线陡峭。**判读：营销式增长而非机器人刷星；star 数 ≠ 深度用户数。**

无硬编码刷 star 迹象（无假 CI、无 star 交换脚本；commit 作者分布自然：zhulinsen 44/100、Nicholas-Xiong 10、ObVious55 7……多数为社区 PR）。

### 1.2 实际运行记录（本次审计的硬验证）

| # | 验证项 | 结果 | 证据等级 |
|---|---|---|---|
| 1 | 依赖安装 | uv + 清华镜像装精简依赖集；**requirements.txt 不完整**，实测缺 `pydantic`、`fake_useragent`、`markdown2`、`newspaper3k`、`lxml_html_clean` 时连续 ModuleNotFoundError，逐个补装后才跑通 | A |
| 2 | `--dry-run`（仅取数） | **跑通**。真实 A 股行情全链路：大盘涨跌 2178/3168、涨停 43/跌停 9、两市 11136 亿、行业板块 Top5（efinance 源）；akshare 概念排行连接被断→自动 fallback→最终标记 empty，**降级链路也验证了** | A |
| 3 | mock LLM 单股全流程（600519） | **跑通**。本地 mock OpenAI 服务（18001 端口返回合法 `AnalysisReportSchema` JSON），全链路：技术引擎评分 51/观望 → LLM 决策仪表盘 → 完整性校验拦截缺字段 → 重试 1 次 → 占位补全（不阻塞）→ **守卫把 LLM 的 62 分 buy 降级为 59 分观察** → 日报落盘 → 推送跳过（未配置）→ 自动回测启动 | A |
| 4 | 真实 LLM 分析 | 未验证（无 key）。代码路径与 mock 相同 | — |
| 5 | 新闻搜索 | 未运行（无搜索 API key）；7 渠道实现为代码精读 | B |
| 6 | 筹码分布 | 未跑通：akshare `stock_cyq_em` 实测被东财拒绝连接（本地网络限流），代码确认走该接口 | B/A |
| 7 | 飞书推送 | 未运行（lark_oapi 未装）；实现为代码精读 | B |
| 8 | GitHub Actions 零成本运行 | 未实际 fork 运行；workflow 文件（565 行）已读：cron 周一~五 UTC10:00、随机延迟、交易日检查、30 分钟超时、环境变量检查齐备 | B |

**未跑通事项与原因**：真实 LLM/搜索/推送均缺 key；东财免费源在我网络下多次 `RemoteDisconnected`（这也是它设计 fallback 链的原因）；`litellm` 启动时拉取 GitHub model cost map 10 秒超时拖慢启动（网络环境问题，有本地兜底）。

### 1.3 「决策看板」具体实现（重点对比项，证据等级 A）

**数据结构**（`src/schemas/report_schema.py`，Pydantic 校验 LLM 输出）：

```
Dashboard
├── core_conclusion    一句话结论 + 信号类型 + 时效性 + 分仓位建议(空仓/持仓两套)
├── data_perspective   趋势(MA排列/趋势分) + 价格位置(乖离/支撑压力) + 量能 + 筹码(获利比例/平均成本/集中度/健康度)
├── intelligence       最新消息 + 风险警报 + 利好催化 + 业绩展望 + 舆情摘要
├── battle_plan        点位(理想买/次优买/止损/止盈) + 仓位策略 + 行动检查清单
├── phase_decision     盘中阶段化决策护栏(行动窗口/即时动作/下次检查时间/置信度理由/数据限制)
├── signal_attribution 信号归因(技术/新闻/基本面/大盘四维贡献度, 归一化到100)
└── agent_disagreement_explanation  多agent分歧解释(见1.5)
```

**实测渲染结果**（mock LLM 输入，真实行情混合，A 级证据）——注意两点：

1. 确定性数据与 LLM 字段并存：行情表（收盘 1295.48，来源标注「腾讯财经」）由 pipeline 填入，mock LLM 虚报 1520 也只是另列一行，**没有让 LLM 伪造行情的空间**；
2. **守卫实测生效**：mock 返回 `decision_type=buy, score=62`，最终报告为「持有 | 评分 59」，一句话决策被改写为：
   > 持有观察：资金流数据缺失，买入结论缺少资金面确认，先按观察处理。

即确定性护栏在「关键数据缺失 + LLM 给出方向性结论」时强制降级。这是我们「弃权语义」最直接的参照物。

**与我们对比**：我们的「卡片」是告警 text + 盘前简报（规则底稿）。DSA 的看板强在：分仓位建议、点位四件套、归因、数据限制披露。我们的强在：六维打分和炒作阶段判定是 DSA 没有的市场结构视角。

### 1.4 「多源行情」与「新闻分析」（证据等级 B）

**多源行情**（`data_provider/`，18 个 fetcher，比我们多出的主要是**多市场**与**token 级源**）：
- 免费：efinance、akshare、baostock、pytdx、tencent、yfinance
- token：tushare、tickflow、longbridge、futu、finnhub、alphavantage
- fallback 链显式按优先级排列（requirements.txt 注释即文档）；市场能力边界单独文档化（`docs/market-support.md`）
- 我们已有同构轮子：`data_providers/composite.py`（eastmoney/sina/tencent/ths）。**差集只在多市场与 token 源，A股场景我们够用**。

**新闻分析**（`src/search_service.py` 4946 行）：
- 7 个搜索渠道：Anspire / SerpAPI(百度) / Tavily / Bocha / Brave / MiniMax / **SearXNG 自建（零配额兜底）**，每个渠道都是 **key 池轮换**（`xxx_keys: List[str]`）
- 新闻喂 LLM 两条路：① 非 agent 路径——搜索结果拼成 `news_context`（analyzer.py:258「📰 舆情情报」段）；② agent 路径——Agent 自己调 `search_stock_news` 工具
- **证据计数三态语义**（`src/agent/news_evidence.py`，A 级设计参考）：
  ```python
  # 渠道不可用 → None，披露「未配置搜索渠道」
  # 渠道可用但零命中 → 0，披露「未获取到可用的新闻面数据」
  # 两者语义不同，绝不混用（否则披露与事实相反）
  ```
  并强调「Agent 实际消费的新闻条数」与「事后补查命中数」是两个命题，用 ContextVar 累加器保证只计真实喂给 LLM 的条数。
- 社交舆情（Reddit/X/Polymarket）走第三方 Stock Sentiment API，**仅美股**，可选。

### 1.5 多 agent 编排与分歧语义（证据等级 B，代码精读）

`src/agent/orchestrator.py`（2150 行）+ `disagreement.py`（210 行）+ 5 个专职 agent（technical/intel/risk/portfolio/decision，各 100~320 行）+ 15 个 YAML 技能策略（`strategies/*.yaml`：情绪周期、龙头战法、缩量回踩、缠论、波浪、一阳三阴等）。

`disagreement.py` 的核心机制（这正是我们缺的「置信度/弃权语义」成品参照）：
- 信号分桶（bullish/bearish/neutral）+ **无效意见单独披露**（`invalid_opinions` 计数进 diagnostics，不静默丢弃）
- **risk agent 否决权**：risk agent 看多一律压成 hold（`_effective_signal`）
- 冲突分类 11 种（`mixed_directional_signals` / `partial_bullish_with_degraded_inputs` / `insufficient_opinions`…），每种映射一条 `decision_path_hint`（如 degraded_only → "state_data_limitations_before_recommendation"）
- **降级阶段跟踪**：任何 agent 超时/预算跳过/失败都记入 `degraded_stages`，最终结论必须先声明数据局限
- 评分-动作一致性（`decision_scale.py`）：score≥60 但 action=hold/watch **必须**给出降级原因，否则判为 conflict（`score_action_conflicts_without_guardrail`）——实测中就是这条把 62/buy 压成 59/观察

### 1.6 推送对比（证据等级 B）

- DSA：14 渠道 sender（飞书/企微/Telegram/Discord/Slack/邮件/钉钉/ntfy/gotify/pushover…），飞书 sender 644 行：webhook + 应用机器人双通道、markdown 渲染、按字节分片、`md2img.py` markdown 转图片兜底（不支持 markdown 的渠道）、lark 文档上传
- 我们：`notifiers/feishu.py` 133 行，**仅 text 消息**（msg_type=text）
- 差集明确但价值有限：我们推送卡片不含新闻是**产品决策**而非能力缺失；若要升级为 markdown 卡片，DSA 的分片+转图兜底是现成参考

### 1.7 能力差集清单（它有我们没有，按价值排序）

| # | 能力 | DSA 实现 | 对我们价值 | 建议 |
|---|---|---|---|---|
| 1 | **评分-动作一致性守卫** | canonical scale 5 档 + conflict 检测 + guardrail_reason 强制披露（decision_scale.py 161 行，零依赖纯函数） | 我们 LLM 增强层无任何一致性校验；这是最小成本引入「弃权语义」的起点 | **采纳设计**（不用引库，照着重写 <200 行） |
| 2 | **数据缺失三态披露** | news_result_count None/0/N 语义 + degraded_stages + 「筹码未纳入判断」声明 | 我们推送/简报目前数据缺失时静默留空 | **采纳设计** |
| 3 | **新闻搜索渠道池** | 7 渠道 + key 池轮换 + SearXNG 自建兜底 | 直接补「新闻/消息面接入弱」短板的实现路径图；SearXNG 自建 = 零成本 | **参考设计**（我们已有 news 模块，加搜索渠道层而非替换） |
| 4 | **筹码结构数据** | akshare `stock_cyq_em`（获利比例/平均成本/90%集中度）进看板 + 缺数据时显式声明 | 我们明确缺筹码分布；短炒场景（获利盘压力）价值高 | **采纳**（单接口封装，成本极低；注意东财限流） |
| 5 | **LLM 观点-结果回测** | BacktestService：历史 AI 报告 vs 后续行情自动核验；skill_opinion_outcome 多 horizon 评估 + 权重再校准 | 我们已有题材级 `predict/service.py::verify_outcome`（hit/partial/miss + 命中率看板），**思路同构**；差集仅在 per-opinion 多 horizon 与权重自动再校准 | **改进现有实现**（给 verify_outcome 加 horizon 与权重回写，勿新建平行系统） |
| 6 | **LLM 输出完整性校验** | 必填字段缺失→重试 1 次→占位补全（不阻塞流水线），实测有效（A 级证据） | 我们 extract_json_object 只管解析不管字段完整 | **采纳**（扩展 llm_client 校验层） |
| 7 | 多 agent 编排/专职 agent | 5 agent + 15 skill + 预算/超时治理 | 我们当前 76 行同步客户端 + 两处增强，**远未到需要多 agent 的阶段**；且 33 万行工程不可直接搬 | **暂缓**（等 LLM 增强层跑出样本再考虑） |
| 8 | 多市场/token 行情源 | 18 fetcher | A 股场景我们已覆盖 | 放弃 |

**反过来，我们有它没有的**：六维选股打分、炒作阶段权重、题材天梯/连板梯队、涨停池、盘中 watcher、TDX 分钟线——DSA 完全没有市场情绪周期引擎与题材结构，它只做「给定自选股 → 每日体检」。**不要因为它星高就把我们的结构搬向它。**

### 1.8 风险与坑

1. **免费源限流是常态**：实测东财 push2 多次拒绝连接；DSA 自己 README 也承认「稳定性不保证」。任何借它数据源设计的功能都要带 fallback 与显式降级。
2. **requirements.txt 不完整**：按 README「pip install -r requirements.txt」路径实测会连环 ModuleNotFoundError（1.2 节），issue 区类似报告不少。引入任何片段代码时要自带依赖审计。
3. **赞助商驱动的设计偏向**：默认推荐 Anspire/AIHubMix（返利），架构上无锁定，但选型文档有商业倾向。
4. **litellm 重依赖**：启动时联网拉 model cost map；我们若引其任何代码路径注意别连带 litellm。
5. **体量陷阱**：33 万行、Web UI、bot、桌面端、多市场。**只取 1.7 表中 #1/#2/#4/#6 四个可独立摘取的纯函数/小模块设计，其余不碰。**

---

## 二、mvanhorn/last30days-skill（60981 star）

**一句话定位**：AI agent 技能包——跨 Reddit/X/YouTube/HN/Polymarket/GitHub/StockTwits 等 14+ 平台的「近 30 天」人物/主题搜索与综合简报引擎（Python 7000 行脚本 + 2307 行 SKILL.md）。**不是误收也无 trading 直接能力，但工程方法论值得看一眼。**（证据等级 A：完整源码精读，未运行——与 trading 场景关联弱不值得装环境）

### 核心发现

- **真东西**：不是 README 空壳。`scripts/last30days.py` 3900 行主引擎、`store.py` 1283 行、`watchlist.py` 319 行（含 https-only webhook 校验等安全细节）、`evaluate_search_quality.py` 588 行（搜索质量自评）。GitHub Trending #1、Trendshift 认证。
- **核心创意**：各平台围墙花园数据（Reddit 评论、X 帖、YouTube 转录、Polymarket 赔率）由 agent 自带 key/browser session 并行拉取，**按真实互动量（赞/回复/真金赔率）打分**，再由 LLM 综合成简报。
- **SKILL.md 是「法律条文」式工程**：11 条 LAW，每条都附带**真实事故复盘**（如 LAW 6：LLM 把内部证据簇原样吐给用户的事故，含根因分析与修复位置行号）。这是把 agent 失败模式文档化的少见范例。

### 对我们「新闻/消息面接入弱」的补足评估

**结论：无直接补足价值。**（证据 A）
- 全部数据源为英文/海外平台；**没有任何 A 股信源**（grep 确认无雪球/微博/东财股吧/同花顺；StockTwits 仅覆盖美股 ticker 语境）
- 我们的研判场景需要的是东财股吧热帖、财联社电报、公告原文——last30days 一概不碰
- 形态是 Claude Code 等 agent 运行时的技能包（SKILL.md + 子命令），不是可嵌入 FastAPI 后端的库

### 可借鉴的设计（不引代码）

1. **互动量加权评分**：如果我们未来接股吧/雪球情绪，用「赞/评论/真金」加权而不是纯计数，可直接参考其 scoring 设计；
2. **LAW 式失败模式文档**：我们 LLM 增强层的踩坑可以按这种「条文 + 事故案例 + 修复行号」格式沉淀进 AGENTS.md；
3. **watchlist webhook**：主题盯梢 + 新发现推送的结构与我们 watcher 同构，无新增量。

**采纳建议：忽略引入；收藏其 SKILL.md 写法作 skill 工程范式参考。预期收益：低（方法论层面）。**

---

## 三、FrondEnt/PolymarketBTC15mAssistant（1056 star）

**一句话定位**：Polymarket「BTC 15 分钟涨跌」二元市场的 Node.js 实时盘口助手（1899 行：Polymarket/Chainlink/Binance 三路行情 WS + 启发式 TA 加分器）。与 A 股日频系统**零交集**，确认怀疑。（证据等级 B：完整目录结构与核心引擎精读，未运行）

### 核心发现

- 数据面：Polymarket WS 盘口 + Chainlink 链上价 + Binance 现货参考，含 Polygon RPC 多节点 fallback、代理支持——**WS 多源冗余接入的写法尚可**；
- 信号面：`engines/probability.js` 是**朴素加分制**（price>VWAP +2、RSI>55 且上行 +2、MACD 柱扩张 +2……up/down 计数比），无校准、无回测、无资金管理；
- 每期二元市场结算机制、赔率-概率换算（`engines/edge.js`）与我们完全无关。

### 能力差集与建议

**差集：无。** 唯一沾边的是「多路 WS 行情冗余 + 代理容错」的工程写法，但我们的 quote_hub/speed_sampler 已覆盖同类需求且是 A 股语境。

**采纳建议：放弃。理由：市场、标的、频率、信号方法论全部不匹配；1056 star 无社区证据加成。预期收益：零。**

---

## 四、quantskills/quantskills（2193 star）

**一句话定位**：确认怀疑——**纯导航仓库**：两个大 README（中文 82KB + 英文 89KB）+ 20MB 截图 + CI 快照脚本，零业务代码。（证据等级 A：源码全部内容已查验）

### 核心发现

- PandaAI 发起的量化 Skill 社区目录：214 项资产、10 个分类的**定期快照表格**（`<!-- CATALOG:START -->` 注释区机器生成），交互目录在 quantskills.ai；
- 表格抽查显示绝大多数条目状态为「**待维护者审核 / 无公开端点**」——早期圈地阶段，无质量背书（其 README 自己声明「不代表质量背书、收益承诺或生产可用性保证」）；
- 分类体系本身清晰（数据→因子→组合→风控→执行），个别 A 股条目名字有吸引力（如 `skill-a-share-pit-fundamental-vintage-builder` PIT 财务数据构建），但均无公开实现可验证。

### 建议

**采纳建议：忽略本体；可作为「按类查 skill」的索引页收藏**（等个别条目状态变为「已发布」再回访，例如 PIT 数据构建、数据质量审计类与我们的 data_quality/validator.py 可能互补）。预期收益：现在为零，未来是目录价值。

---

## 五、总表：本组行动项

| 仓库 | 定性 | 行动 |
|---|---|---|
| daily_stock_analysis | 真项目 + 营销水分；自选股分析器，与我们重合但结构不同 | **摘 4 个设计**：①评分-动作一致性守卫 ②数据缺失三态披露 ③筹码结构接口（akshare stock_cyq_em）④LLM 输出完整性校验（重试+占位）。均照设计自写、不引依赖。新闻搜索渠道池留作补新闻短板时的路径图 |
| last30days-skill | 真东西，但海外舆情引擎，A 股无源 | 忽略；借鉴「互动量加权」与「LAW 式失败文档」范式 |
| PolymarketBTC15mAssistant | 小玩具（启发式加分，无校准） | 放弃 |
| quantskills | 纯目录页，条目多未审核 | 忽略；收藏为未来 skill 索引 |

**验证透明度声明**：A 级（实际跑通）——DSA dry-run 大盘复盘、mock LLM 单股全流程、依赖安装踩坑链；B 级（代码精读）——DSA 新闻搜索/飞书推送/orchestrator/回测服务、last30days 全部、Polymarket 全部；C 级（元数据）——star/fork/issue 统计中的 fork 分布推断。GitHub 网络不稳定导致 git clone 三次失败，最终全部改用 codeload tarball 校验后解压完成。
