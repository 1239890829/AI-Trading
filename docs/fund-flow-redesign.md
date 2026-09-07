# 资金流向重构设计方案（2026-09-07）

> 定位：按「大盘 → 板块/题材 → 个股」三级资金流体系重构资金流向功能，打通到板块监控、选股器、自选、个股详情、预警、策略信号的流转。
> 状态：**P0 已实施（2026-09-07）**——board_flow.py 服务 + 3 端点（/market/board-fund-flow*）+ 落盘
> （data/boardflow/daily.json + daykline.json）+ fund-tab 三段式（①②③）+ R1 命名统一 + R2 kline-chart.tsx 下线。
> 实施偏差：①成员/日度沉淀深度取 Top20/kind、保留 60 交易日（控文件体积）；②龙头股列仅名称（成员净额放抽屉）；
> ③5d/10d 用 clist 官方字段 f164/f174（实测 = 今日 f62 + 近 N-1 完成日 bar，偏差 0.00 亿，见 data-sources.md §3.2），
> 20 日区间与连续流入仍走落盘自算（读路径零外呼）。P1/P2 流转项（题材徽标/watcher/alert/助手工具/tech_score v4）未实施。

---

## 0. 结论摘要

1. **数据可行性已实测闭环**：东财接口原生支持板块级资金流——板块现值列表（净流入/主力占比/领涨股）、板块日度五档历史（40 日，可自算连续流入天数）、板块分钟分时、板块成员个股资金排行，四块全部实测可用，且 push2 主域当前可达（此前"仅 push2delay 可用"结论已过时，实现按双域 failover 设计）。
2. **唯一新增后端服务** `board_flow`：板块资金流的唯一实现，落盘复用 fundflow 的 daily.json 模式，读路径零外呼。
3. **前端 fund-tab 重构为三段**：大盘资金（现状保留）→ 板块资金流（新主视图）→ 板块下钻（分时曲线 + 成员排行）。
4. **冗余合并 7 项**：最大一项是资金流三级命名统一（market-fund-flow / board-fund-flow / capital-flow），另有 K 线死组件下线等。
5. **流转落地走 P0/P1/P2 三批**，P0 只做「板块资金流主视图 + 唯一服务」，流转类（选股/预警/助手）进 P1/P2。

---

## 一、资金流向 Tab 优化：板块/题材聚合维度

### 1.1 现状与差距

现状（`apps/web/components/market/fund-tab.tsx`，759 行）：只有**大盘口径**——两市成交额对比、五档净额卡、分钟级资金流曲线、历史回看、机构/游资（龙虎榜聚合）。数据源 `backend/app/market/fund_flow.py`（765 行，自建 httpx，不走 provider 链，落盘 `data/fundflow/daily.json`）。

**差距**：无板块/题材维度、无板块内龙头个股、无连续流入天数、无区间切换。盘中想回答「今天钱在往哪个方向堆」只能靠肉眼云图，无法排序跟踪。

### 1.2 数据层：新增 `board_flow` 服务（唯一实现）

新建 `backend/app/market/board_flow.py`（与 fund_flow.py 平级、同风格：TTLCache + 失败不缓存 + degraded 显式透传 + 落盘）。**四个实测数据源**：

| 能力 | 接口（均已实测） | 说明 |
|---|---|---|
| 板块现值列表 | `push2.eastmoney.com/api/qt/clist/get`，`fs=m:90+t:3`（概念 504）/`m:90+t:2`（行业），`fid=f62` | 字段：f12 代码 / f14 名称 / f3 涨跌幅 / f6 成交额 / f62 主力净额 / f184 主力净占比 / f104,f105 涨跌家数 / f128,f140 领涨股。**单页上限 100，必须按 total 翻页**（docs/data-sources.md §3.2 已有先例） |
| 板块日度五档历史 | `push2his.eastmoney.com/api/qt/stock/fflow/daykline/get`，`secid=90.BKxxxx`，lmt=40 | 字段序同指数 fflow：日期,主力,小单,中单,大单,超大单,…,涨跌幅。**连续流入天数、5/10/20 日区间直接自算** |
| 板块分钟分时 | `push2delay.eastmoney.com/api/qt/stock/fflow/kline/get`，`klt=1`，`secid=90.BKxxxx` | 延迟 ~15min 口径，图表必须标注（同现有大盘分时纪律） |
| 板块成员个股排行 | `push2.eastmoney.com/api/qt/clist/get`，`fs=b:BKxxxx`，`fid=f62` | f62/f66/f72/f184 齐全 → 板块内资金龙头与超大单占比 |

**口径纪律（红线继承 fund_flow.py）**：
- 主力=超大+大，东财真实口径，不自创；
- 板块 f62 是东财官方板块口径（非成分股相加），**不与个股新浪 MoneyFlow 口径混用**；
- 三态：历史缺失 → None + degraded，绝不填 0；
- 双域 failover：主域 push2 → 备域 push2delay（延迟口径需显式标注），全败 available=False。

**落盘与排名变化**：新建 `data/boardflow/daily.json`——收盘后（≥15:05）快照当日板块 Top50 净流入 + 成员排行 Top20 进落盘（复用 `_snapshot_today_if_closed` 模式）。排名变化 = 今日榜位 − 昨日落盘榜位；连续流入天数 = daykline 尾部连续 f62>0 计数。**读路径零外呼**（性能优先，历史请求不打上游）。

**缓存分层（TTLCache，失败不缓存）**：
| 缓存 | TTL | 说明 |
|---|---|---|
| board-rt（现值列表，概念+行业全量） | 盘中 30s / 盘后 300s | 全市场板块一次拉齐，前端排序不回源 |
| board-daykline | 落盘为准 + 内存 6h | 仅对 TopN 板块按需拉取 |
| board-minute（单板块分时） | 60s | 下钻时才拉，同时最多缓存 4 个板块 |
| board-members（单板块成员排行） | 60s | 同上 |

**题材语义层**：东财板块名与 ths 题材标签体系不同，复用 `theme_service.match_board`（剥离后缀+双向包含匹配）把 ths 题材 → 东财板块关联。**题材卡上的资金合力字段 = 关联板块的 f62**，不做「成分股相加」口径（避免第二次口径与 504×N 次请求）。匹配不上的题材显式 `fund: null`，不冒充。

### 1.3 前端新信息架构（fund-tab 三段式）

```
市场页 /market?tab=fund
├─ ① 大盘资金（保留现状：成交额对比卡 + 五档净额卡 + 分钟曲线 + 机构/游资卡）
├─ ② 板块资金流榜（新主视图，Panel「板块资金流」）
│     ├─ 区间切换：今日分时 | 5日 | 10日 | 20日（后端聚合，前端只切参数）
│     ├─ 维度切换：概念 | 行业 | ths题材(映射)
│     ├─ 列：板块名 | 涨跌幅 | 净流入额(亿) | 主力占比 | 超大单占比 | 连续流入 | 龙头股(名+净额) | 排名Δ
│     ├─ 排序：默认净流入额降序；点击列头切换（涨跌幅/主力占比/连续流入）
│     ├─ 筛选 chips：连续流入≥3 | 净流入>0 | 主力占比>5% | 排名上升
│     └─ 行点击 → ③ 下钻抽屉
└─ ③ 板块下钻抽屉（Drawer，不离开 tab）
      ├─ 板块资金分时累计曲线（五档，延迟 15min 标注）
      ├─ 5/10/20 日主力净额柱状序列 + 连续流入高亮
      └─ 成员个股资金排行 Top20（代码/名称/涨跌幅/主力净额/占比，点击跳工作台个股）
```

**性能预算（第一优先级纪律）**：
- 后端单端点返回全量板块行（504+86 条 × ~14 字段 ≈ 60KB），前端排序/筛选纯内存计算，**切换排序不触发请求**；
- 前端渲染 TopN（默认 50 行 + 「展开全部」），列表用已有 Panel/Skeleton 体系，不做虚拟化（50 行量级不需要）；
- 轮询统一 30s 一档（盘中），单端点；下钻抽屉数据独立 60s 轮询、关闭即停（usePollingFetch 卸载即清）；
- 图表 props 引用稳定（useMemo），分时曲线 SVG 定高（项目既有契约）。

### 1.4 新端点清单（后端）

| 端点 | 参数 | 返回 |
|---|---|---|
| `GET /api/market/board-fund-flow` | `kind=concept\|industry`、`range=intraday\|5d\|10d\|20d` | 板块行数组（含连续流入/龙头/排名Δ）+ degraded |
| `GET /api/market/board-fund-flow/{board_code}/minute` | — | 板块分钟五档累计曲线 |
| `GET /api/market/board-fund-flow/{board_code}/members` | — | 成员个股资金排行 Top20 |
| `GET /api/ext/...（不变）` | — | 现有大盘资金流 6 端点全部保留不动 |

---

## 二、功能盘点与去重

### 2.1 全站功能清单（6 页 + 全局，数据来源/口径/消费方）

**盘面 `/tape`**
| 功能 | 数据来源 | 口径 | 消费方 |
|---|---|---|---|
| 题材梯队 | ths 涨停池 reason 归因 + em 板块增强 | ths 归因（唯一权威） | tape/themes-tab；picks_intraday 复用 build_theme_board |
| 涨停生态（池/连板梯次/原因） | composite(ths→em) | 收盘价落限价 | tape/limit-up-tab；market 总览速览 Top10 |
| 跌停池 | composite(em) | 同上 | tape/limit-down-tab |
| 龙虎榜（日/3日/席位） | composite datacenter | 榜单口径 | tape/longhu-tab；fund-tab 机构游资卡 |

**市场 `/market`**
| 功能 | 数据来源 | 口径 | 消费方 |
|---|---|---|---|
| 总览（指数/宽度/情绪/成交额/涨停速览/事件） | hub+snapshot(新浪全市场)+market_context+events | 沪深京（宽度）/沪深（成交额），已各自标注 | market/page 内联 |
| 资金 tab（现状 5 卡） | fund_flow.py 自建 httpx | 沪深两市；东财五档 | 仅本 tab（无其他 service 消费） |
| 云图 | heatmap_service（新浪快照+TDX 行业映射） | 市值加权 | market/heatmap-tab |
| 事件 tab | events 四级三档 | 影响力分 | market/events-tab；event-panel；个股 stock-events |

**工作台 `/workbench`**：自选/分组管理（watchlist_repo）、8 右页签（盘口/逐笔=腾讯链、模拟交易=paper engine+风控预检、真实持仓=real_service、资料/资讯=em、涨速=speed_sampler、板块=sina boards、K线/分时/资金图=provider 链+sina MoneyFlow）、顶部成交额摘要（fund_flow 同源）、AI 助手浮窗（SSE+受限工具）。

**每日精选 `/picks`**：五维评分引擎（ths/em 池+竞价+tech_score+RPS marketdb）、空仓闸门、影子持仓、复盘。
**盘中跟踪 `/intraday`**：盘前简报、watcher 状态机、机会视图（复用 build_theme_board）、盘后对照。
**研究 `/research`**：回测（三套引擎见 §2.2）、预警（规则/事件/渠道）、复盘（review 三表 PDCA）。

### 2.2 冗余与合并建议（同类能力只留一处）

| # | 冗余项 | 现状 | 建议 | 批次 |
|---|---|---|---|---|
| R1 | **资金流三级口径命名混乱**：大盘=fund_flow.py 自建 / 个股=sina provider 单点 / 板块=em f62 散在 theme_service | 三处三种封装 | **统一三层命名与归属**：`market-fund-flow`（大盘，fund_flow.py 保持）、`board-fund-flow`（新 board_flow.py 唯一实现，theme_service 只消费不封装）、`capital-flow`（个股，维持新浪单点+provider 链纪律）。文档 capability-map 同步登记 | P0 |
| R2 | K 线双组件：`kline-chart.tsx` 已被 `kline-chart-pro.tsx` 取代，无消费方 | 死代码 | 下线 kline-chart.tsx | P0 |
| R3 | 涨停池三处轮询：limit-up-tab 全量 / market 总览 Top10 / metric_history 回补 | 同端点多份独立轮询 | 前端两处展示保留，轮询合并进共享 hook（useLimitUpPool + 60s SWR 语义），后端回补不动（落盘用途） | P1 |
| R4 | 路由库双轨：`lib/routing.ts` 与 `lib/nav-targets.ts` | 职责边界模糊 | 合并为 nav-targets 单一入口（routing 保留薄别名过渡一轮） | P1 |
| R5 | provider 能力清单双份：`provider_capabilities.py` 静态表 vs `composite._OBSERVABLE_METHODS` 探测 | 漂移风险 | composite 探测改为读静态表驱动，删 `_OBSERVABLE_METHODS` | P1 |
| R6 | 成交额/指数双轮询：market 总览与 workbench 顶部条各自拉 overview/turnover | 同端点×2 轮询 | workbench 摘要保留但降频到 120s（已有 60s），或加 ETag 式 short-TTL 后端缓存（已有 30s TTL，实际成本低，可仅记录不动） | P2 观察 |
| R7 | 三套回测引擎 / 三套"复盘"命名 | 对象不同非真重复 | 不合并；仅统一命名文档化（strategy-backtest / pick-review / market-review），避免后续混淆 | P2 文档 |

**明确不合并**：`/api/themes`（归因口径）vs `/api/themes/hot`（热度口径）语义不同，保留双端点但前端 Label 必须区分口径（已有 QualityBadge 体系）。

---

## 三、关联与数据流转设计

### 3.1 资金流数据四层级模型

```
L1 大盘   fund_flow.py（现有）      → market-fund-flow* 6 端点 → fund-tab ①
L2 板块   board_flow.py（新，唯一）  → board-fund-flow* 3 端点 → fund-tab ②③ + 下述所有消费方
L3 题材   match_board 映射（ths标签 → L2 板块行）→ theme_service 消费 L2
L4 个股   sina capital-flow（现有，provider 链单点）+ em 成员排行（L2 的 members）
```

**流转原则**：L2 是枢纽——所有「板块级资金」需求只允许从 `board_flow.py` 取数；任何模块不得自行请求东财板块接口（防止第二实现 + 双倍上游压力）。

### 3.2 模块间数据流（L2/L3 向下游）

```
board_flow.py（L2 枢纽）
├─→ fund-tab ②③           板块榜 + 下钻（主视图）
├─→ tape 题材梯队           题材卡新增「资金合力」徽标：f62 净额 + 连续流入天数
│                            （theme_service.build_theme_board 增量字段，卡内排序权重可配）
├─→ 盘中跟踪 watcher        新提醒规则：板块净流入额分钟增量突增（阈值可配）
│                            + 「低吸异动」形态：板块大额流入 & 涨幅<2%
├─→ 每日精选 engine         tech_score v4 增「板块资金维」(权重 ≤0.05)——改权重必递增 SCORER_VERSION（P2）
├─→ 自选股（workbench 左栏） 个股行加「所属板块资金」小徽标（正/负 + 净额），数据随个股批量接口带上
├─→ 个股详情                题材归属行(theme-chips)带板块净流入；flow-chart 升级为
│                            个股五档(现有) + 所属板块分时曲线(新, L2 minute) 上下对照
├─→ 预警 alert_engine       新规则类型 board_flow_spike：板块净流入/占比阈值触发，
│                            走现有 alert_events + 飞书通道（规则创建收口到 alert_repo，不在服务内散建）
├─→ AI 助手 tools.py        新受限工具 {{tool:board_flow|kind=concept}}：LLM 可答"今天资金在堆哪个方向"
└─→ 策略信号/回测(P2)       boardflow/daily.json 落盘后成为回测因子源（板块资金流前向胜率）
```

**个股级联动细节**：个股 → 所属板块映射用现有 `normalizer.classify_boards()`（东财 F10，IS_PRECISE 尾段规则）+ ThemeMember 表，缓存 24h。自选行徽标取「个股主板块（首个 IS_PRECISE=1）」的 L2 行。

### 3.3 落地批次

| 批次 | 内容 | 门禁 |
|---|---|---|
| **P0** | board_flow.py 服务 + 3 端点 + 落盘；fund-tab ②③ 重构；R1 命名统一、R2 死组件下线 | pytest 全量 + pyflakes + tsc + eslint 0 error；真数据验收（盘中实测 Top 榜与自算连续流入对照） |
| **P1** | 题材梯队资金徽标、自选徽标、watcher 新规则、alert board_flow_spike、助手工具；R3/R5 | 同上 + alert 规则收口核查（grep 无新增散建） |
| **P2** | tech_score v4 板块资金维、flow-chart 板块对照、回测因子、R4/R6/R7 | 同上 + SCORER_VERSION 递增 + rps/tech 相关测试更新 |

### 3.4 风险与已知坑

1. **push2 主域可用性会漂移**（8 月末被拦、9 月初实测可用）：双域 failover + 延迟口径标注是硬要求，不做单域依赖。
2. **f160/f109/f110 多日涨幅是字段序推断**（docs 已警告实测不可信）：区间切换一律走 fflow daykline 自算，**不用 clist 推断字段**。
3. 504 板块 × 翻页 6 次的现值请求要 `asyncio.gather` 并发 + 全表 30s 缓存，严禁前端按需逐板块拉现值。
4. 北交所无资金流数据（fund_flow.py 头注红线）：板块维度天然不含北交所个股，degraded 说明带上。
5. ths 题材 ↔ 东财板块模糊匹配命中率有限：未命中题材 `fund: null` 显式留空，归因仍走 ths reason 唯一权威口径，两套标签不互相冒充。
