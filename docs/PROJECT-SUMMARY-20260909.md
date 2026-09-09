# AShare AI Trader 项目全景总结（2026-09-09）

> 本文基于 2026-09-09 代码实态扫描（commit 至 `b931318`），所有数字来自实测（目录/端点/配置/测试计数），无臆造。
> 配套文档：`docs/PROJECT-MASTER.md`（技术总览）、`docs/system-review-20260909.md`（系统审查）、`docs/INDEX.md`（文档索引）。

---

## 1. 项目概述

| 项 | 内容 |
|---|---|
| **名称** | AShare AI Trader（A 股量化投研工作台） |
| **定位** | 实时行情 + 量化投研 + 模拟交易 + 事件驱动选股 + AI 自主进化 的一体化个人工作台 |
| **核心目标** | ① 盘中秒级感知市场（题材/梯队/资金）；② 选股全链可解释可追溯；③ 系统**自己迭代自己**（AI 大脑：感知→决策→执行→验证→留痕闭环）；④ 一切结论=偏向+依据+失效条件，绝不输出确定性买卖结论 |
| **应用场景** | 个人 A 股短线/题材博弈研究与复盘；盘中题材演化跟踪；选股策略回测验证；AI 辅助投研（对话式查询+自动复盘+自主改进） |
| **红线** | 禁真实券商/自动下单（只有模拟盘）；禁 mock 冒充实盘；API Key 只在 backend/.env；撮合规则硬拦截不可绕过；新页面先论证复用 |

---

## 2. 项目架构

### 2.1 技术栈总览
- **后端**：Python 3.11 + FastAPI + uvicorn + SQLAlchemy 2.0 + Alembic（schema 唯一归口）+ SQLite（业务库）+ DuckDB（marketdb 日K仓）+ Polars + easy_tdx（通达信协议直连，钉 <1.30）+ akshare 1.18.94（扩展面）+ akquant 0.3.58（Rust 内核回测/TA-Lib 103 指标，指标一律 `backend="rust"`）
- **前端**：Next.js 16（App Router, flat config）+ React + TypeScript + Tailwind + lightweight-charts（K线/分时）+ vitest + RTL
- **AI**：LLM 网关（`claude -p` CLI → GLM 系模型，多级降级）用于复盘叙事/议程裁决/告警判读；严格 JSON 输出契约
- **测试**：后端 pytest（tests/ 40+ 文件、1480 个测试函数定义）+ 前端 vitest 211 用例 + pyflakes 0 + tsc 0 + eslint 0 + `scripts/api-sweep.js` 载荷巡检
- **部署**：docker-compose（dev）/ docker-compose.prod.yml（生产双镜像）；前端同源 `/backend` Route Handler 反代（避 CORS）

### 2.2 目录结构（backend/app 22 个包）
```
backend/app/
├── main.py            # lifespan：迁移 stamp-or-upgrade、20+ 后台任务装配、优雅关停
├── api/routes/        # 19 个路由文件、154 个 REST 端点
├── data_providers/    # 四源链：ths(官方主源)→tencent→eastmoney→sina + composite(熔断) + mock
├── market/            # sina 全市场快照、tdx_kline(easy_tdx)、speed_sampler、trading_status、parquet_store
├── services/          # 29 个服务：quote_hub(1s 实时)/snapshot_service/theme_service/evolution(大脑)/
│                      #   experiments+shadow/watch_ledger(台账)/leader_archive/push_policy/data_health_loop/
│                      #   alert_triage/code_executor/replay_gate/official_match/agent_tasks/agent_params…
├── picks/             # 25 个选股子模块：morning_brief/watcher/buy_point/gate/regime/echelon/risk/
│                      #   intraday_opportunity/watch_ledger/replay/signal_health/style_router…
├── review/            # 盘后复盘 Agent（规则分析器+LLM 叙事+方法论版本化+元结论）
├── risk/              # 风控引擎（7 档市场状态→下单预检）
├── paper/             # 模拟交易（撮合引擎 T+1/涨跌停/整手/费用/停牌 全硬拦截）
├── events/            # 事件卡规则抽取（来源分级/半衰期/方向词典）
├── news/              # 新闻采集与摘要（规则层）
├── factors/           # 因子库（TDX 日K 算子，离线仪表盘，IC 复核待接运行时）
├── predict/           # 新题材预判（六维+四问验证）
├── sentiment/         # 盘中情绪监控（高度板炸板/炸板率/指数急杀）
├── assistant/         # AI 助手 14 只只读工具（对话式查询）
├── notifiers/         # 通道抽象（feishu webhook/自建应用 + in_app）
├── core/              # config(92 配置项)/db/ttl_cache/llm_client/migrations
├── models/ schemas/ repositories/  # ORM(23 表)/契约/数据访问
└── data_quality/ websocket/        # 质量五级校验 / WS 实时推送
apps/web/              # Next 前端（app/ 7 页面 + components/ 25 组 + lib/api.ts 单点契约 + hooks）
docs/                  # 75+ 篇设计/审查/复盘文档（INDEX.md 为唯一入口）
scripts/               # api-sweep.js / bootstrap.sh
backend/scripts/       # replay_picks / replay_sweep / sync_marketdb / build_push_cards / factor_ic_review / repo_watch / backtest_picks / run_factor_eval
skills/                # WorkBuddy 技能包（ashare-daily-review 等）
```

### 2.3 模块依赖与数据流
```
四源链(熔断) ──► QuoteHub(1s) ──► WS ──► 前端(分时/K线实时合成)
      │                └► REST（quality 五级标注，失败=stale+degraded）
snapshot_service(15s) ──► 内存快照 ──► 换手/市值/接棒溢价；parquet 落盘(数据底座 2.8G)
涨停池(ths/东财) ──► theme_service 聚类 ──► 题材梯队/阶段/龙头 ──► 盘面看板 + 猎场机会
                              │
morning_brief(盘前) ──► watcher(盘中确认/证伪) ──► watch_ledger 台账(首见登记)
                              │                      │
review_intraday(收盘对照) ──► 台账清算(pnl/verdict) ──► signal_health(胜率)
                              │
review Agent(15:30) ──► action_items ──► evolution 议程(15:45, 七路证据)
                              │
              A(参数→影子队列)/B(日报)/C(代码沙箱)
                              │
      experiments(30日劣化回滚) ◄── replay_gate(回放对比) ◄── 台账/回放统计
```

---

## 3. 功能清单（模块 → 子功能 → 入口/流程/逻辑）

### 3.1 行情与数据
| 功能 | 入口/流程 | 实现逻辑 |
|---|---|---|
| 实时行情 | 全站自动（WS） | QuoteHub 1s 固定节奏；腾讯 realtime_rank 优先；瞬时失败 stale_after(10s) 容忍；分时/K线 WS tick 实时合成（lib/kline-live.ts），实测 0.6~1.3s 更新 |
| K 线 | 工作台 KlineChartPro | TDX 分钟级底座（5min≈2年）；三源+熔断；事件标记（公告琥珀●/新闻蓝●） |
| 分时 | 工作台/盘面 | 均价线+量比基线；纵轴按板块动态限制（涨停/跌停线贴边，price-limit.ts） |
| 全市场快照 | 后台 15s 轮询 | sina 分页抓取（换手/流通市值/量额），parquet 原子写；**盘后重启内存空→端点必须有 parquet 兜底**（09-08 教训） |
| 涨停/炸板/跌停池 | 盘面 tab | ths 官方池（归因逐字）+ 东财 push2ex 增强（连板/封单/开板）；跌停池空池=合法语义（09-09 修复误报） |
| 官方题材目录 | 盘面/搜索/弹窗 | fuyao 390 概念+成分，TTL 24h 自动校准（实测逐符号一致）；哨兵盯成分新鲜度 |
| marketdb 日K仓 | 离线 sync | DuckDB 单文件 + 复权事件流；RPS/横截面底座；哨兵盯 >26h 停更 |
| 交易日历 | 全局 | 官方+备源，mtime 缓存；「30 天覆盖事故」后加哨兵（≥200 天+末日新鲜） |
| 涨速榜 | 指数详情页 | 最近 5 分钟涨跌幅（自算，腾讯批量快照惰性采样） |

### 3.2 盘面分析（/tape 四合一）
| 功能 | 说明 |
|---|---|
| 指数卡+详情 | 点击展开右面板分时/K线；指数 symbol 强制带前缀（sh000001） |
| 题材梯队看板 | 涨停池→官方标签直通聚类→唯一归属→梯队/强弱/阶段/健康度/官方成分徽标/3·5·10 日涨幅（官方 K 线验证） |
| 概念详情弹窗 | 官方成分全量（换手/流通市值/涨幅）+涨停成员（连板/开板/封单/官方归因）+细分 tab；涨停置顶排序；整行点击跳工作台 |
| 龙虎榜 | 交易所日榜/三日榜（金额不可相加口径标注）+概念等分守恒资金追踪 |
| 涨停池表格 | 整行点击跳工作台；题材成员高亮 |

### 3.3 猎场（/hunting 选股中枢）
| 功能 | 入口/流程 | 逻辑 |
|---|---|---|
| 盘前简报 | 09:26 自动生成（可手动重跑） | 涨停池题材∪事件方向→rank_directions top3→每方向标的池/触发/证伪条件；`daily_plan` 段（昨日复盘结论+未完成事项+昨日议程） |
| 盘中跟踪 | 机会手风琴（题材→个股瀑布流） | opportunities 每 60s：涨停池聚类→阶段/确定性/辨识度判定→**机会三层徽标**（今日最强/悄悄启动/孕育待发酵，规则可解释） |
| 跟踪台账 | 手风琴尾部 WatchLedgerPanel | **首见登记**（watcher 确认/买点/机会候选三来源，当日唯一不可移除）→**收盘清算**（入选价 vs 收盘→盈亏→success/flat≤2%/fail）→当日/历史胜率统计 |
| 模拟建仓 | 台账 tracking 行按钮 | placePaperOrder 100 股@入选价；撮合硬拦截 |
| 历史龙头档案 | GET /api/picks/leader-archive?theme= | 近 30 日涨停池按官方标签聚合 top3 最高连板（妖股≥5 板标记），缓存 20h |
| 精选复盘 | 对照表 | 方向级 outcome（confirmed/falsified/pending）+全量提醒收益回填 |
| 空仓闸门 | 顶部红幅 | 退潮/冰点、晋级率<30%、炸板率≥35%、跌停≥15、接力亏钱 多条件 OR；触发撤买入范围 |
| 主题材预判 | predict | 六维评分+D1 四问；结果进议程第七路证据 |

### 3.4 每日精选（/picks → 并入猎场）
- ≤5 只瀑布流卡片（MasonryColumns 贪心分列）；六维评分（情绪/消息/技术/基本面/资金+梯队）+meta 置信档+止损/出场纪律；收盘定次日+换股上限 2 只+carryover 兜底；卡片可展开详情弹窗（原样复现）

### 3.5 工作台（/workbench 个股中枢）
- 搜索（四源 suggest，空=无匹配不熔断）→自选分组（动态：每日精选/盘中跟踪）→右面板（分时/K线/盘口/逐笔/资金/资料/事件）→交易页签（风控实时预检+parseNum 修复）→回测（mandate yaml+防泄露）

### 3.6 市场（/market）
- 板块资金流（主力净额排行+成分抽屉，整行点击）、事件面板（EventCard：来源分级/事实与解读/方向）、akshare 扩展面（CPI/两融/龙虎榜二源，未装显式降级）

### 3.7 AI 系统
| 功能 | 说明 |
|---|---|
| 盘后复盘 Agent | 15:30 自动：规则分析器（九类走坏归因+买点质量单独评估）→LLM 叙事（严格 JSON/模型路由降级）→action_items（可 PATCH 处置，三元组守卫防串号） |
| 进化大脑 | 15:45 议程：**七路证据**（复盘/信号健康/判读统计/计划对齐/数据健康哨兵/因子IC(待)/新题材预判）→LLM 严格 JSON ≤3 项→A(参数影子)/B(日报)/C(代码) 自动执行；停机开关 ASHARE_AGENT_AUTONOMY=0；C 类独立开关 |
| 影子队列 | A 类变更不直接生效→剧变检测（逐相位权重 L1≤0.25+灭声维≥0.05）→达标自动转正+挂 30 日实验；剧变拒绝归档 |
| 实验记录 | 转正后 30 日 signal_health 对照，劣化自动回滚；样本不足延长≤2 次 |
| 回放门禁 | C 类涉 picks/ 合入后自动 10 日回放 vs 基线（换手/持有/组合分 deltas，degraded 进下轮议程） |
| 变更留痕 | record_mutation 三入口强制（A/C/人工 apply），任务中心可见全程 |
| 告警判读 | watcher 规则触发→LLM verdict（notify/ignore/escalate）→响应建议（题材归属+当日状态+观察指引）→**判读即终态**（自动 acknowledged） |
| 数据健康哨兵 | 7 路检查（日历/快照新鲜/marketdb/告警心跳/成分新鲜/磁盘/快照目录数）盘中 15min 循环+议程证据双通道；新异常推飞书红卡 |
| AI 助手 | 悬浮球对话（14 只只读工具）+实体词典联动+判读提醒气泡 |

### 3.8 通知与推送
- **矩阵**（push_policy.py）：CRITICAL 买点卡（盘中实时）＞ ANOMALY 系统异常（哨兵实时，状态变化才推）＞ REPORT 进化总结报告（**每日唯一**，15:45 automation，what/why/how 三段式）＞ SILENT 其余全 in_app
- **站内通知中心**：三源合并（个股机会/每日精选/评分≥60 新闻+risk 健康预警）+ AI 判读合入 body；未读**数字徽标**+全部已读+一键清除（**时间戳语义**：覆盖全部历史，clearBeforeTs 切线）
- 4 个旧推送 automation 已停用（09:26/14:40/15:35/15:40）

### 3.9 模拟交易
- 撮合引擎（T+1/涨跌停拒/整手/费用/停牌拒全部硬拦截）；账户/持仓/订单/成交；无挂单降频轮询（30s→5s）；台账一键建仓入口

---

## 4. 功能菜单（导航结构）

| 菜单 | 路由 | 内容 |
|---|---|---|
| 工作台 | /workbench | 搜索→自选分组→个股详情（分时/K线/盘口/资料/事件/交易/回测） |
| 盘面 | /tape | 指数概览带 + 涨停池/题材梯队/跌停池/龙虎榜 tabs |
| 市场 | /market | 板块资金流 + 事件面板 + 扩展数据 |
| 猎场 | /hunting | 盘前简报（今日计划）+ 当前机会（三层徽标+瀑布流）+ 跟踪台账 + 精选/跟踪 tabs + 复盘对照 + 统计面板 |
| AI 控制台 | /agent | 进化（今日议程+实验+影子队列+历史议程）/ 任务中心 / 复盘（action_items 处置）/ 提醒与告警（规则 CRUD+事件表+AI 判读徽标）/ 参数配置（白名单参数+变更单全生命周期） |

**全局件**：搜索框（symbol/名称/题材拼音）、通知铃铛（数字徽标/全部已读/一键清除）、主题切换、AI 悬浮球（拖拽+贴边收纳为墨玉半胶囊把手，hover 展开；告警气泡→控制台）

---

## 5. 参考仓库（github-stars 评测，docs/archive/github-stars-trading-analysis.md）

| 仓库 | 决策 | 借鉴/用途 |
|---|---|---|
| HiThink-Tech/Financial-API | 采用（在用） | 同花顺官方服务本体；盘点未启用官方能力 |
| handsomejustin/easy_tdx | **引入** | TDX 协议直连：分钟历史深度/前复权（K 线底座） |
| wenyuanw/a-share-heatmap | 引入（前端思路） | treemap 云图可视化 |
| TauricResearch/TradingAgents | 架构参考 | LLM 多角色编排+决策日志——复盘 Agent LLM 升级蓝本 |
| HKUDS/Vibe-Trading | 架构参考 | Shadow Account/PIT 数据纪律互证 |
| ZhuLinsen/daily_stock_analysis | 架构参考 | 推送通道架构（其买卖点位结论风格违反红线，不学） |
| virattt/ai-hedge-fund | 架构参考 | mandate 文件思想→回测参数化 |
| freqtrade | 思想借鉴 | 止损/跟踪止盈/ROI 分档 → risk.py A 股映射 |
| akquant | 引入 | Rust 回测内核+TA-Lib 103 指标（backend="rust"） |
| microsoft/qlib、vnpy、OpenBB、FinceptTerminal、quantskills、AlphaMaster | 暂不采用 | 依赖重/域不同频/红线冲突（触发重评条件已写明） |

---

## 6. 技能与技术栈要点

**语言/框架**：Python 3.11（FastAPI/SQLAlchemy 2/Alembic/Polars/DuckDB/httpx）、TypeScript（Next 16/React/Tailwind/lightweight-charts）
**关键技术点**：
- 四源熔断 failover + 质量五级 + 空池语义分型（涨停空=异常，跌停空=合法）
- TTL 缓存统一层（ttl_cache）+ N+1 砍除 + 前端 useMemo 稳引用
- Alembic stamp-or-upgrade（23 表）；新表必须迁移
- LLM 严格 JSON 契约 + 确定性去重前置 + LLM 挂回退标 llm_fallback 不伪装
- 三态纪律（unknown=「未判定」≠低）；对外文案单点收口（tri_text）
- 三层安全模型：影子剧变检测 → 回放对比门禁 → 30 日劣化自动回滚
- 台账「首见登记+收盘清算」幂等设计；时间戳切线语义（通知清除）
- 工程纪律：门禁五件套全绿才交付；接口契约变更必须端点冒烟；跨日/时区日期源同源

**设计模式**：Composite failover（Provider 链）、Repository（数据访问）、Observer（QuoteHub/WS）、状态机（变更单 draft→shadow→applied→rolled_back；任务 queued→running→succeeded/failed）、纯函数评分（style_router/echelon 可测可回放）、Rules-before-LLM（规则层打地基，LLM 只做裁决与叙事）

---

## 7. 配置 / 环境 / 部署

- **配置**：backend/app/core/config.py 共 92 项 settings（`ASHARE_*` 前缀环境变量）；关键项：数据源选择、轮询间隔、LLM 网关（base_url/api_key/model/cli_path）、agent 预算（LLM 日预算/任务日预算/进化时刻 15:45）、通知新闻评分阈值（默认 60）、ths 哨兵开关
- **密钥**：backend/.env（gitignored）：THS fuyao key、LLM key、飞书 webhook/app 凭据
- **数据库**：SQLite 业务库（alembic 管 schema，head=a9c3e5f7b1d4 watch_ledger）；marketdb DuckDB；data/parquet 快照（90 天保留窗口，prune 显式触发）
- **端口**：后端 8000 / 前端 3000 固定；**交易日 12:00 前勿重启 8000**（简报重生成 bug）
- **部署**：`docker-compose up`（dev：backend 8000 + web 3000 node dev）；生产 `docker-compose.prod.yml` 双镜像 + /data 挂载 + BACKEND_ORIGIN 反代
- **本地起栈**：`cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000`；`cd apps/web && npm run dev`
- **门禁命令**：`pytest -q` / `pyflakes app tests` / `tsc --noEmit` / `eslint .` / `vitest run` / `node scripts/api-sweep.js`（载荷体检）

---

## 8. 当前在途与已知限制（诚实清单）

1. 消融验证自然积累（约 2026-10 中旬出 `--days 30 --compare-ablation` 验收）
2. 产业链图谱策展+扩散模型（厄尔尼诺/CPO/PCB 案例）——需盘中真实数据
3. 规律挖掘/因子发现机制（周度假设→回放验证→固化哨兵）——方案已设计未实施
4. 控制台四模块（策略时间线/实验对比看板/议程历史/健康面板）——数据端点全有，纯前端
5. LLM 增强（复盘叙事质量）等凭据；真实推送通道/Docker 生产化等部署决策
6. 因子库未反哺运行时（IC 复核通过后接 tech_score）；prediction 消费回路已接（第七路）
