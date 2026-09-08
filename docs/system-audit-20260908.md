# 系统全面审查报告（2026-09-08）

> 触发：用户指令「停下当前开发，对整个系统进行一次深度、严谨、全面的审查与重构规划」。
> 方法：4 路并行深度代码调研（后端工程 / 前端体验 / 板块结构 / 文件与因子库）+ 决策文档与 git 历史交叉核验。
> 原则：每条结论落 `文件:行号`；调研结论之间的矛盾点经交叉验证后修正（见 §1.3 修正注记）；只审查与规划，P0 之外的代码改动待批准后分批执行。

---

## 〇、总体判断

**工程纪律显著好于同体量的快糙猛项目**（TTLCache 统一抽象、to_thread 纪律、错误契约、golden 回归测试、22k 行测试零 skip、git 工作区零未跟踪文件），但累积出五类明确债务：

| 债务类型 | 量级 | 风险 |
|---|---|---|
| 死代码 | 3 个死文件（~600 行）+ 8 个死 schema + 7 个零引用函数 | 误导后续开发 |
| 孤立 API 端点 | 19 个（P2-E 盘全） | 前端未来按错误契约对接 |
| 指标/HTTP/时区口径分裂 | 3 套指标真值、5 处东财 HTTP 封装、30+ 处自建时区 | 口径漂移 |
| 事件循环同步 IO | trade_calendar 每次调用读盘（热路径） | 盘中延迟 |
| 前端加载三态缺失 | 工作台详情区/精选页等 8 处 | 用户报告的「闪现」 |

规模基线：后端 app/ ~40,247 行 Python、22 子包、142 端点；前端手写 `useState+fetch`（**无** zustand/SWR/React Query，三个数据原语全部自研）；tests ~22,374 行。

---

## 一、代码与工程质量

### 1.1 死代码（建议删除，P0 批次）

**整文件级**（经 AST import 图 + 全仓 token 双重验证）：
- `backend/app/data_providers/base.py` — MarketDataProvider Protocol 全仓零引用（协议本可约束 7 个 provider，实际无人 import）→ 删，或让 providers 显式实现
- `backend/app/factors/candidates.py`（239 行）— 「候选因子登记册」零代码引用，纯文档 → 迁 docs/
- `backend/app/market/minute_backtest.py`（344 行）— 仅 `tests/test_minute_backtest.py:20` 引用，无生产调用方 → 连测试一起归档

**函数/类级**（零引用）：
- `app/core/db.py:61 get_db`、`app/data_quality/validator.py:171 is_future`、`app/assistant/tools.py:515 _timedelta_hour8`、`app/picks/intraday_opportunity.py:51 _rank_level`、`app/news/flash.py:126 poll_stats`、`app/data_providers/tencent.py:63 _price_raw`、`app/market/minute_signals.py:66 _bj_hhmm`
- 死 schema ×8：`schemas/market.py:224 BoardQuote`、`schemas/envelope.py:207 AdjustmentEvent`、`:229 CycleSegment`、`:116 SentimentIndicator`、`:154 ThemeSummary`、`:92 MinutePointModel`、`schemas/risk.py:21 PositionParamsModel`、`schemas/news_digest.py:28 DigestModel`
- `schemas/envelope.py` 另有 5 个响应模型从未被任何 route 用作 `response_model`（路由实际返回手写 dict）→ 文档型 schema 与真实返回静默漂移，比没有更误导 → 删未用项，或逐端点挂载

**修正注记（交叉验证推翻 Agent 结论）**：调研中一度将 `websocket/routes.py` 报为孤儿，经复核 `apps/web/hooks/use-quote-stream.ts:124` 存在 `new WebSocket('/ws/quotes...')` —— **WS 端点是在用的，保留**。工作台列表、个股详情均已消费。

### 1.2 冗余实现（收敛目标，P1）

1. **指标三处手写**：`market/tech_score.py:42,53,61`（sma/ema/rsi14 主实现）、`market/backtest.py:135 _sma_at`、`routes/picks.py:208-233 _atr_pct/_ma_value` → 后两处并入 tech_score
2. **分位数/中位数 5 份实现**：`services/theme_service.py:618 _median`（stdlib `statistics.median` 已有人用）、`core/perf.py:67 _quantile`、`market/performance.py:110 _pctl`、`market/chip.py:143 _quantile_price`、`sentiment/calibration.py:81` → 收敛 core util
3. **东财 HTTP 封装 ×5**：`data_providers/eastmoney.py:87`、`market/fund_flow.py:72`、`market/board_flow.py:80`、`news/flash.py:55`、`market/article.py:216` → 抽 `app/market/em_client.py`（keep-alive/Referer/超时单点；article.py:218 的 keep-alive 断连处理目前只有一处有）
4. **时区定义 30+ 处自建**（`_BJT`/`_BJ`/`_TZ_BJ`/`CST`/`BJ_OFFSET`…）→ 新建 `app/core/timeutil.py` 统一（最大收敛点）；`api/routes/market.py:241` 还在用 deprecated `datetime.utcnow()`
5. **亿元换算**：`board_flow.py:106 _to_yi` vs `fund_flow.py:304 _yi` vs `heatmap_service.py:99` 内联 `/1e4`
6. **交易时段判定残留 2 处内联**：`predict/collector.py:40`、`routes/notifications.py:53` → 改调 `trade_calendar.in_wide_market_window`（R2 收口真正关账）
7. **手写缓存残余**（项目已有 TTLCache 约定）：`services/heatmap_service.py:34-66`、`data_providers/ths.py:97-119`、`main.py:347 picks_env_cache`、`picks/watcher.py:522-525`、`sentiment/intraday_monitor.py:271`
8. **模块级 TTLCache 违背「挂 holder」纪律**（测试跨用例污染风险）：`market/tdx_kline.py:14`、`market/stock_flow.py:31`、`market/board_flow.py:64-70`、`market/fund_flow.py:51-57,353`
9. **每次调用新建 httpx client**：`notifiers/feishu.py:140,179,231`、`market/sina_market.py:64`、`market/minute_backfill.py:109`

### 1.3 过度设计（去留决策）

- **predict 包半死**（~1,000 行）：REST 5 端点全部孤立，唯一活口是 `review/service.py:160` 的 `maybe_auto_verify` → **决策：瘦成 auto-verify 库**，5 个端点删除，预判引擎保留（龙虎榜静默覆盖等修复都沉淀在内）
- **路由层承载业务逻辑**：`routes/picks.py` 999 行，`_candidate_pool/_limit_up_context/_deep_score_candidates/_assemble_card`（:69-605）500+ 行选股业务在 route 层，与 `picks/engine.py` 两套编排 → 下沉 picks 包
- **god 文件**：`routes/market.py`（1,561 行 40+ 端点）、`services/theme_service.py`（1,243 行）、前端 `stock-detail.tsx`（870 行）、`workbench/page.tsx`（730 行）→ 按域拆分（P2，非紧急）
- 反例澄清（**不算**过度设计）：NotifierRegistry 3 通道均有消费；data_providers 7 源均被 composite 引用；config.py 抽查配置项全有消费方

### 1.4 性能热点

1. **P0·热路径同步读盘**：`market/trade_calendar.py:246 _load_persisted()` 每次调用从磁盘读 JSON；挂在 `data_quality/validator.py:45,127 validate_quote` → 逐行情校验 ×N 自选 ×请求频率，事件循环内同步文件 IO → **加 mtime 缓存（~10 行，收益最大）**
2. **P1·async 路径同步 write_text**（低频小文件，P2 修）：`fund_flow.py:476,700`、`board_flow.py:308`、`trade_calendar.py:71`、`sentiment/metric_history.py:187`、`picks/heat_history.py:116,176`、`picks/shadow.py:218`
3. **P1·DuckDB 连接每调必开**：`picks/rps.py:122`、`market/chip.py:193` → 服务实例持长连接（两文件还各定义了一份 `DEFAULT_DB_PATH`）
4. **P1·picks 深度评分 N+1 外呼**：`routes/picks.py:377-530` 每候选 `get_kline`+`get_financials`，CAP=40 → 单次生成最多 ~80 次上游调用；financials 建议批量化或缓存

### 1.5 孤立 API 端点（19 个，P2-E 盘全）

**建议删除**（predict 全家 6 个 + 无前景 5 个）：`/predict/*` ×5、`/events/collect`（调度器直调函数，路由壳无用）、`/events/extract`、`/events/{id}/review`、`/review/compare`、`/review/methodology/versions`、`/backtest/walkforward`（连带 `market/walkforward.py`）、`/system/provider-capabilities`（连带消费收窄）
**建议标注 ops-only 保留**（有运维/诊断价值）：`/themes/catalog/sync`、`/themes/catalog/reconciliation`、`/themes/catalog/index`、`/themes/catalog/{code}/members`、`/picks/shadow`（影子持仓状态，待融合板块接入后转正）、`/picks/signal-health`（**融合板块统计条的直接数据源，转正**）、`/assistant/daily-summary`（15:35 automation 在用——**非孤立**，Agent 漏查 automation 消费方，修正）

### 1.6 scripts/ 与 tests/ 卫生

- **可归档**（一次性使命完成）：`scripts/akquant_lab.py`（通路验证结论已进 requirements 注释）、`scripts/patch_eval_rolling.py`（自述一次性补偿）、`scripts/theme_audit.py`（09-01 一次性审计，流程可复用留档）
- **常驻保留**：sync_marketdb / build_push_cards / repo_watch / backtest_picks / run_factor_eval / factor_ic_review / replay_picks / replay_sweep
- **tests 卫生良好**：无 skip/xfail、无 scratch；唯一挂账是 test_minute_backtest 随模块归档

### 1.7 依赖

- `pytest` 混在运行时 requirements.txt:7 → 移 dev 分组
- `akquant==0.3.58`：`app/` 零使用，仅 akquant_lab.py（待归档）import → **建议保留**（P2 因子库升级的真值候选，见 §6）
- `./vendor/wheels/jsonpath`（8K）：akshare 间接依赖的有意 vendor，**保留**；lock 内 pandas/curl_cffi 等约半数为 akshare 传递依赖，可标注来源便于未来清理

### 1.8 trading 分组仓库排查结论

| 仓库 | 现状 | 结论 |
|---|---|---|
| akfamily/akquant | 已 pip 装入 venv（0.3.58 rust 后端），`app/` 零接入 | **保留依赖**，P2 作为指标真值升级候选 |
| bukosabino/ta | 评测被否（RSI 头部 NaN→0 污染偏差 10.29），未装未声明 | **已废弃**；验证脚本 verify_ta.py 已归档 docs/archive/ |
| akshare | 已装入 venv（1.18.94），唯一消费入口 `services/akshare_ext.py:95`（zt_pool_previous 等） | **在用，保留**；/tmp 下的 21M clone + 228M venv 已清理 |

仓库内无 vendor/external 源码 clone；`vendor/wheels/jsonpath` 是有意 vendored 依赖非垃圾。

### 1.9 垃圾文件（本轮已清 + 待确认清单）

**本轮已清理**（用户指令授权，均为主仓库外临时工作区或可再生缓存）：
- `/tmp/eval-repos/`（pip-tmp 50M + akshare CSV + 验证脚本）、`/tmp/star-eval-20260907/`（259M 工作区）——验证脚本先归档 `docs/archive/verify_{ta,akquant}.py` 再删
- 一次性日志与 wrapper 脚本 8 个；仓库内全部 `__pycache__/`、`.pytest_cache/`（~30 处）；孤儿 pyc（screener_service.cpython-311.pyc）

**待确认（涉及 git 跟踪或数据文件，不动手）**：
- `backend/data/ashare.db.bak-20260901-orphan-cleanup`（11M，已被 gitignore，本地磁盘可删）
- `docs/archive/theme-audit-2026-09-01.json`——审计跑批数据产物混进 git（建议 untrack；`docs/archive/*.json` 加 gitignore）
- `skills/_archived/`（3 个归档技能包已跟踪，与主链路无关，删否看是否想留历史）
- **git 跟踪了两个运行时自积累状态文件**：`backend/data/sentiment_metrics.json`、`backend/data/trade_calendar.json`（每次 pytest 后人工还原的就是后者）→ 建议 untrack + gitignore + 提供种子文件；否则多机部署互相覆盖
- `.gitignore` 预防性缺口：`.mypy_cache/`、`.ruff_cache/` 未列（当前不存在）

---

## 二、性能与体验（闪现根因与修复）

前端数据获取是三原语自研体系：`lib/api.ts:84-137`（fetch 封装）、`hooks/use-polling-fetch.ts:18`（轮询）、`hooks/use-quote-stream.ts:28`（WS 1Hz→REST 降级）。统一骨架基建已存在（`components/ui/loading.tsx`：Skeleton/CardListSkeleton/TableSkeleton/FadeSwap/FadeIn），**问题是覆盖不全**。

### 2.1 闪现根因（按可能性排序）

| # | 根因 | 证据 | 机制 |
|---|---|---|---|
| R1 | **切股 key 重挂载** | `workbench/page.tsx:707-714` | 切股=整面板 remount，14 个请求从零走；期间行情条整条消失（`stock-detail.tsx:542` null 不渲染）、盘口闪**误导错误文案**「免费源仅盘中提供」（`book-trades-view.tsx:27`——没拉回来≠不可用）、逐笔闪「暂无」、模拟交易整块空白（`stock-detail.tsx:839`） |
| R2 | **空态文案抢跑** | `app/picks/page.tsx:114,180-185`、`workbench/page.tsx:560-587`、`board-rank-panel.tsx:83-88`、`profile-panel.tsx:88` | `data?.items ?? []` 模式：加载中与确认空数据同渲染「暂无…」，数据到达整块替换。正例已在库内：`market/page.tsx:75`、`intraday/page.tsx:580` 的 pending 三态 |
| R3 | null→内容硬弹 | `detail/info-panel.tsx:110-141` | 资讯区无加载占位，digest（15s 档）拉回后内容弹入 |
| R4 | tab 硬切 + 图表实例重建 | `stock-detail.tsx:650,675,715,834-855` | 图表 tab 条件渲染 → lightweight-charts 销毁重建白一帧；右栏 tab 每次切入重拉闪「加载中」；FadeSwap 只覆盖 market/tape/research 顶层 |
| R5 | WS 快照窗口期 | `use-quote-stream.ts:212-229`、`workbench/page.tsx:260-267` | 新 symbol quote 到达前 `.filter(Boolean)` → 行逐个蹦出跳动 |
| R6 | 详情页 1Hz PriceFlash | `stock-detail.tsx:189`（无 throttleMs）+ `price-flash.tsx:19-21` | 详情面板 quote 每秒变 → 420ms 红绿闪每秒触发；列表侧已修过（3s 节流），详情侧漏了 |
| R7 | 暗/字体/图片 | — | **无问题**：主题脚本 `<head>` 同步执行 + SSR 输出 className + suppressHydrationWarning；系统字体栈无 FOUT |

### 2.2 修复方案（对应编号）

- **F1**（R1）：保留 key 重挂载（防串股残留是有意设计），把「未拉到」与「确认无」分离——book/trades/quote/paper 传 `undefined=加载中→Skeleton`、`null=拉过且空→现文案`；QuoteStrip null 时渲染同高骨架条
- **F2**（R2）：把 market/intraday 的 `pending` 三态范式移植到 picks 页、workbench 空态、board-rank、profile、资金图 tab
- **F3**（R3）：InfoPanel anns/news null → 2-3 行 Skeleton
- **F4**（R4）：图表区/右栏 tab 用现成 `FadeSwap` 包一层（swapKey=chartTab/rightTab，零新代码）；SpeedPanel/BoardRankPanel 数据提升到壳层 idle 调度
- **F5**（R6）：详情面板 `useQuoteStream` 补 `throttleMs: 3000`
- **F6**：`stock-detail.tsx:479-483 techInput` 每秒全量重算 analyze()（依赖随 WS 换引用的 displayBars）→ 依赖收窄到「bars+最新收盘价」
- **F7**：4 个图表组件改 `next/dynamic` 减工作台首包
- **F8**（组件卫生）：抽 `components/ui/badge.tsx` 收敛 6 处手写徽标；`heatmap-tab.tsx:98-110` 与 `theme-chips.tsx:18-21` 重复的 pctColor/pctText 收敛回 `lib/format.ts`；intraday 内部 `ThemeCardView` 与 `theme-card.tsx` 重名 → 改名

### 2.3 骨架屏矩阵结论

最佳实践页：**盘中跟踪、市场总览**（pending 三态+骨架齐全）；资金/云图/事件/题材梯队/涨停生态 tab 基本齐全；**缺口集中在工作台详情区**（指数卡、行情条、盘口、逐笔、模拟交易、公司资料）与**每日精选页**（无骨架、空态抢跑）；回测/预警/复盘低频页空态代替骨架可接受。全局 button/a/tr 140ms 过渡健康，无过度动画。

---

## 三、板块融合方案（新板块：**猎场**）

> 命名理由：板块回答的问题是「今天/现在值得盯哪些票」——精选是盘后布下的猎物（tag「精选」），跟踪是盘中正在追的猎物（tag「跟踪」），题材异动是猎群（展开）。备选名：标的台 / 机会板。

### 3.1 与既有文档的关系

`docs/picks-intraday-fusion-assessment.md`（09-04）是**策略层**融合（执行闸门/影子持仓），其数据结论直接复用：盘后选股可执行 alpha≈0、gap≥9.5% 桶胜率 12% 应禁买、盘中出场 +0.19%/次。本节是**呈现层**融合，两者互补不冲突。`/picks/shadow` 端点已存在（孤立），融合板块统计区可顺带转正。

### 3.2 结构设计

```
/hunting（导航「猎场」，合并原 /picks + /intraday；旧路径 302，项目有先例 nav-bar.tsx:15-18）
├─ 统计条（各 tag 独立口径，三态纪律：insufficient 显式「样本不足」绝不显示 0%）
│   ├─ 精选：role_performance（/api/picks/meta 已有）+ signal-health win_rate/mean_excess（前端从未接——零成本增量转正）
│   └─ 跟踪：IntradayReviewStats.alert_t1/alert_t3.win_rate（/api/picks/intraday-review 已有）
├─ tag 切换：全部 / 精选 / 跟踪 → 带对应 tag 的瀑布流（PickCard variant="pick"|"watch"）
├─ 异动题材手风琴 panel（保留现「当前机会」交互 + 增强）
│   ├─ 整行可点展开收起（现已是 button，page.tsx:131）+ 补 hover:bg-* 变色（现无）
│   └─ 展开后：题材内个股渲染为带 tag 的瀑布流卡片（替换现 OpportunityStockRow 行式）
├─ 精选独立 panel（保留）：gate 横幅 + 复盘区（role_performance/reason_distribution/逐日 reviews）
└─ 盘中节拍信息（简报/提醒）并入精选 panel 下方或折叠区
```

### 3.3 技术要点（调研实证）

1. **卡片复用走 CardShell 路线**：`pick-detail-modal.tsx:39` 的 TopDetailCard 已是「与 PickCard 同构」的跟踪版 → 抽共享布局壳，两卡各填分节。若坚持单组件双 variant，PickCard 必达字段（`sub_scores/bases/vetoes/related_events/score/price`，pick-card.tsx:72-253）需 5 处改条件渲染
2. **tag 复用工作台 chips 既有命名**「每日精选/盘中跟踪」（workbench/page.tsx:513）；新板块名不得与用户分组防撞过滤（:253）冲突
3. **symbol 关联键已验证三处先例**：workbench `pickInfoBySymbol/topInfoBySymbol` Map join（:282-283）是成熟样板；通知中心时间线已合并两类但未做 symbol 互链（可补）
4. **Panel 契约**：新 tab 视图根节点必须 `h-full` 或 `flex-1 min-h-0`（panel.tsx:20-31）
5. **深链迁移**：`/intraday?theme=&sec=` 参数同步迁到新路由（page.tsx:583-597）
6. 语义差异保持：精选是 date+symbol 持久组合（DailyPickSet 表），跟踪是当日实时动态名单（无落库）——统计条必须分别标注口径，不可混算

---

## 四、选股逻辑精进（不推翻，叠加）

现有资产：tech_score 八维 + RPS + CYQ 筹码 + meta_confidence 三档 + chip_signal 留痕 + 相位对账 + signal_health CUSUM。精进方向四条：

1. **相位→风格路由表**（新增，规则版）：六相位 → 当日默认风格 + 因子权重偏移。例：发酵/高潮 → 题材情绪权重↑（echelon/theme 维）、趋势维↓；冰点/退潮 → 趋势/低位反转优先；修复 → 均衡。挂 meta_confidence 的相位维度扩展，所有偏移进配置文件不硬编码（延续 09-04 文档纪律：改阈值必附参数扫描）
2. **gate 分层：只堵不疏 → 堵疏结合**（直接呼应 09-04 评估「空仓=策略停滞」）：
   - 组合级 gate 保留（数据未证伪它，晋级率/炸板率判据方向正确）
   - 新增单票「可跟」档：gate 触发日，满足龙头/跟风判据（连板高度 + 梯队 role + 题材催化）的标的进「可跟」名单——**不给买入范围**（纪律不破），但从「仅观察」升级为「可跟」tag，参与须经影子持仓先验证（09-04 文档 §3.3 原则）
   - observation_only 单档 → 三态：禁买 / 仅观察 / 可跟
3. **消息面深度叠加**：related_events 已进精选卡片；下一步把**盘前简报竞价溢价（auction_premium 已有）接到次日执行状态**（09-04 P0-A 执行闸门：gap≥9.5% 禁买）——这是数据证实的最强单一执行信号，且基础设施全部现成
4. **基本面「视情况忽略」**：涨潮期（发酵/高潮）基本面权重实质下调（meta_confidence 相位规则），退潮期恢复——规则版先行，IC 复核（factor_ic_review.py 已建）月度验证后再定稿

---

## 五、模块复用与数据互通

### 5.1 因子库价值评估（调研结论）

因子库（tech_score 八维 + RPS + CYQ + factors/library.py 37 因子）是**全仓口径纪律最好、测试锚定最完整的资产**，但价值只兑现了「单点评分」一层：真值分散在手写函数与 akquant rust 两套互不相通的实现，`liquidity/rps` 两维在 IC 复核之外零消费。

### 5.2 三条最有价值的扩展

1. **akquant 反哺 tech_score**：rust 后端已装已验证精度（TA-Lib 零偏差），逐步替换/交叉校验手写 sma/ema/rsi14/kdj，消除前端(technical-analysis.ts)/后端/回测三套口径漂移根源
2. **八维打开成独立因子**：liquidity/rps/profit_ratio/concentration 注册进 factors/library.py（candidates.py:223 已有 seq-rps-breakout 占位），走 run_factor_eval IC 跑批优胜劣汰，评分卡权重从 IC 结果建议（人工确认）
3. **因子健康回流复盘**：维度 IC 衰减作为 signal_health 与 daily-review 一等公民输入（factor-lifecycle-governance.md 已规划未落线）——「哪个维度最近失效」进每日复盘

### 5.3 互通缺口处置

- 东财 HTTP ×5 → em_client 单点（§1.2.3）；新浪三套客户端无共享层（低优先级，P2）
- 时区 30+ 处 → core/timeutil（§1.2.4）
- DuckDB DEFAULT_DB_PATH ×2 → 共享只读连接 helper
- `akshare_ext` 与 `eastmoney` 端点同源是**有意的第二源交叉校验**，重构时显式标注防止误清理

---

## 六、补齐项（用户未注意到的）

1. **trade_calendar 热路径读盘**（§1.4.1）——用户可感知的盘中延迟，改动最小收益最大
2. **模块级 TTLCache 测试污染**（§1.2.8）——「单跑全绿全量失败」类 flake 的潜在来源
3. **git 跟踪运行时状态文件**（§1.9）——每次跑测试都要手工还原 trade_calendar.json 的根因就是它
4. **envelope.py 文档型 schema 漂移**（§1.1）——比没有 schema 更误导
5. **盘口误导性错误文案**（§2.1 R1）——「免费源仅盘中提供」在数据未拉回时出现，属于把「加载中」说成「不可用」的判定类语义污染
6. **详情页每秒 PriceFlash**（§2.1 R6）——列表侧修过、详情侧漏掉的不对称
7. **predict 包双状态漂移**（§1.3）——引擎千行只靠 auto-verify 活着，是最容易被误读为「活功能」的死区
8. 旧挂账确认：plan-review.md 遗留决策 #3 部署环境仍开放（Docker/监控依赖它）；parquet 损坏文件 7 个（代码已容错，物理删除待确认）

---

## 七、旧决策废弃清单（齐备性检查）

| 旧决策 | 状态 | 新决策是否齐备 |
|---|---|---|
| ta 库作为指标源 | ❌ 废弃（精度否决） | ✅ akquant rust 后端（已装已验证） |
| 情绪绝对阈值 | ❌ 已被滚动分位校准替代（P0-3b 落地） | ✅ calibration.py + market_context.resolve_bands |
| envelope 文档型 schema | ❌ 废弃未用项 | ✅ 路由返回手写 dict + api.ts 类型为真值 |
| MarketDataProvider Protocol | ❌ 废弃（无人实现） | ✅ composite 模式即事实契约 |
| minute_backtest 模块 | ❌ 归档 | ⚠️ 无替代——回测需求已由 akquant + backtest.py 覆盖，确认无独有逻辑后删 |
| predict REST 端点 | ❌ 删除 | ✅ auto-verify 保留为库；预判展示需求由题材梯队+事件卡覆盖 |
| pytest 入运行时依赖 | ❌ 移 dev 分组 | ✅ |
| WS 孤儿端点（Agent 误报） | ✅ 保留 | —（use-quote-stream 在用） |
| 实盘接入 | 搁置维持（用户 09-04 决策不重启） | — |

---

## 八、执行计划（按优先级）

### P0（本周，高收益低风险）

| # | 项 | 量级 | 依据 |
|---|---|---|---|
| P0-1 | trade_calendar mtime 缓存 | ~10 行 | §1.4.1 热路径 |
| P0-2 | 前端闪现修复：F1（三态分离）+ F2（pending 范式移植 picks/workbench/board-rank/profile）+ F5（详情节流） | 中 | §2.2，用户体感最强 |
| P0-3 | 死代码删除：3 死文件+测试、8 死 schema、7 零引用函数、envelope 未用模型 | 删 ~800 行 | §1.1 |
| P0-4 | 孤立端点处置：删 predict 全家等 11 个、signal-health/shadow 转正融合板块、4 个标 ops-only | 中 | §1.5 |
| P0-5 | scripts 归档 3 个 + akquant_lab 结论留档；pytest 移 dev 分组 | 小 | §1.6/1.7 |
| P0-6 | 待确认清单过一遍（§1.9）：bak 文件/docs JSON untrack/gitignore 补口/运行时状态文件 untrack | 小 | 需逐项确认 |

### P1（下周）

| # | 项 | 量级 | 依据 |
|---|---|---|---|
| P1-1 | **猎场板块**分三批次：①CardShell 抽取+PickCard variant+tag → ②统计条（signal-health 转正接入）→ ③异动手风琴增强（hover 变色+展开后瀑布流）+路由合并 302 | 大 | §3 |
| P1-2 | 相位→风格路由表 + gate 单票「可跟」三态 | 中 | §4.1/4.2 |
| P1-3 | 执行闸门 P0-A（gap 竞价三态进推送卡）——09-04 文档已排期，基础设施现成 | 中 | §4.3 |
| P1-4 | 三大收敛：em_client / core/timeutil / 指标并入 tech_score | 中 | §1.2 |
| P1-5 | 模块级 TTLCache 收口（cache_on holder）+ DuckDB 共享连接 | 小 | §1.2.8 |
| P1-6 | picks.py 路由业务下沉 picks 包；交易时段 2 处内联收尾 | 中 | §1.3/1.2.6 |

### P2（触发式/数据积累后）

| # | 项 | 触发条件 |
|---|---|---|
| P2-1 | akquant 接入指标真值 + 八维注册 library 跑 IC | P0-3/P1-4 落地后 |
| P2-2 | F6 tech 每秒重算收窄 + F7 dynamic import + F8 组件卫生 | 与猎场改造同批顺手做 |
| P2-3 | god 文件拆分（market.py/theme_service/stock-detail/workbench） | 猎场改造稳定后 |
| P2-4 | 执行闸门 P0-B 影子持仓 A/B / P1-C 出场跟踪 / P1-D 阈值配置化 | 按 09-04 评估排期 |
| P2-5 | 新浪客户端共享层、async write_text 批改、部署环境（需用户定 Docker 环境） | 低频 |

### 本轮已完成（无需再批）

- /tmp 临时工作区与跑批残留清理（~310M）；verify_ta/verify_akquant 归档 docs/archive/；仓库内 __pycache__/.pytest_cache 全清；孤儿 pyc 清除
