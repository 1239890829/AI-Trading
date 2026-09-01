# 全项目系统性审查报告（2026-09-01）

> 审查方式：全量盘点（后端 21,240 行 Python/52 文件 · 前端 9,257 行 TS/52 文件 · 文档 36 份 6,363 行）+ 三路并行深度审查（后端代码 / 前端代码 / 文档体系，全部 findings 附 文件:行号 证据）+ 主审交叉抽查验证（analyze 无 memo、文档数字漂移、sync N+1 等均实证）。本报告与既有三份评审的关系：功能层面引用 system-review（已修项标注 ✅）、架构层引用 linkage-plan、题材层引用 theme-attribution-review；本报告新增**代码质量三维度**与**文档体系**两个此前未覆盖的审查面，并复核全部功能模块。
> 风险等级：**高**=错误行为/数据污染/红线相关；**中**=性能或可维护性明显问题；**低**=卫生类。

---

## 〇、总体结论

| 维度 | 打分 | 一句话 |
|---|---|---|
| 后端工程化 | 7.5/10 | 纯计算/IO 分离、诚实降级、熔断与原子写等纪律优秀；失分在 async 纪律不彻底（同步 SQLite/Parquet/TDX 散布在 async 路径）与两条核心管线超长函数 |
| 前端工程化 | 7/10 | 架构选型克制自洽（URL 真相源/事件契约/质量徽标）；失分在重复实现三份（calcMA/pctText）、图表生命周期三范式并存、主题双态样式纪律 |
| 文档体系 | 6/10 | 记录密度极高、踩坑沉淀珍贵；但索引层与事实层脱节——端点数四处互相矛盾（79/82/94/96）、README 目录指向已删页面 |
| 功能合理性 | 良 | 三轮评审后无明显冗余堆积；删除决策全部有实测依据（见 §一） |

---

## 一、功能合理性审查（逐模块）

**审查原则声明（对应用户要求）**：删除决策必须实测背书。本项目至今的功能移除全部经过实测验证：分钟信号端点（做 T 与持仓周期 2.46 天不匹配、无复盘闭环）、screener 冻结（与每日精选定位冲突，用户拍板保留代码）、板块排行 tab 删除（与工作台详情同源重复，能力零损失）。本轮审查未发现新的"应删"项，发现 2 处"死代码待清"与 3 处"欠佳但保留"。

### 1.1 工作台（主入口）
- 设计合理：自选+指数+详情三合一，管理模式承载原自选页能力，分组 CRUD 完整（A1 ✅）。
- `[中]` 左栏"模拟持仓"面板与「持仓」chip 并存，语义易混：前者是 paper 账户持仓、后者是真实持仓账本。**依据**：两个入口数据源不同（paper vs real）。**建议**：模拟持仓 Panel 标题已带"模拟"前缀可接受；长期可考虑收敛为一个"持仓视图切换"。保留观察，不动。

### 1.2 盘面 /tape（三 tab）与 市场 /market（双视图）
- 合并后定位清晰（梯队/涨停证据/龙虎榜 + 总览/云图），板块 tab 已按同源重复原则移除 ✅。
- `[低]` 市场页"涨停速览"与盘面涨停生态保留双入口（M2 既有建议：速览降级为摘要+链接）——保留，待做。

### 1.3 每日精选 /picks（系统产出）
- 六维评分+regime 双权重表+空仓闸门+组合稳定约束，端到端链路完整（linkage-plan §四）。
- `[中]` 消融验证缺口：六维组合的历史优势未被验证（回放只覆盖梯队+技术两维）——linkage-plan 已规划落选者落库+五策略对照，属"欠缺"非"冗余"。
- `[低]` 前端 PickCard 未 memo（前端审查 F-24）：纯展示组件，父页 busy 变化全部重渲。加 memo 零成本。

### 1.4 研究 /research（回测|预警[|复盘待加]）
- 回测引擎代码级防泄露、预警通道抽象设计合理。
- `[中]` 预警真实推送通道缺失（等用户选通道，A3）；复盘完整报告无前端入口（A2，linkage-plan 已规划研究页第三 tab）。

### 1.5 详情终端（StockDetailPanel，8 页签）
- 功能密度合理；分级加载后首屏路径干净。
- `[低]` 右列"逐笔"页签价值有限（数据单源腾讯、与持仓周期不匹配）但保留——切股不再预拉（O2 已绑页签），保留成本已降为 0。不动。

### 1.6 后端能力层（情绪/梯队/regime/闸门/风控/复盘/预判/回测/paper）
- 全部有消费方（picks 六维、gate、前端展示），无孤儿模块。
- `[中]` predict（新题材预判 357 行）消费方仅为 API（无前端入口、picks 未接入其输出）——**保留**：它曾是周末房产政策案例的验证工具且有独立 mandate；但应在文档标注"待接入"（避免被误判为死代码而删除）。
- `[低]` minute_signals.py / minute_decisions.py 全库零引用（分钟端点已删）——**可删**（这是死代码清理，不是功能删除：入口已不存在，文件保留只会误导检索）。

### 1.7 数据层（四源链+目录/成分/快照/事件）
- 健康度良好：题材归属修复后 100% 对齐官方；熔断、限流、原子写齐备。
- `[中]` 事件采集不含涨停股已修（R6 ✅ 35/86 覆盖），剩余 51 只受 TopN 约束——**设计约束非缺陷**，随轮次累积。

---

## 二、代码质量审查——后端（7.5/10）

### 2.1 性能维度

| # | 位置 | 问题 | 依据 | 风险 | 建议 |
|---|---|---|---|---|---|
| B1 | api/routes/picks.py:289-319+447 | 每候选股重复全量扫事件表：_score_one 对 24 只各调 _active_event_hits，内部每次 list_events(30) + 逐条 directions_of | 24×(1+30) 次查询/轮生成，且在 asyncio.gather 内同步跑 SQLite | **高** | 生成开始时一次性载入活跃事件+方向，按 symbol 分组成 dict O(1) 查 |
| B2 | services/theme_catalog_service.py:289-296,320-330 | sync_catalog/sync_members 循环内逐条 SELECT（N+1），同步 SQLite IO 跑在事件循环上 | 390 题材≈390 查询；sync_members 单题材数百成员逐条 select | **高** | 循环前一次 in_ 查询拉 dict 再 upsert；DB 段包 asyncio.to_thread |
| B3 | api/routes/picks.py:322-636 | generate_picks 约 315 行串联六阶段；_theme_benchmark 每候选同步查库 | 单函数 315 行、局部状态跨阶段传递 | **高** | 按阶段拆独立 async 函数；svc 查询包 to_thread |
| B4 | api/routes/market.py:134-139 | sparkline 循环 50 只同步 TDX 文件 IO，串行阻塞事件循环 | tdx_daily_bars 同步读盘 ×50 | 中 | asyncio.gather + to_thread 并行 |
| B5 | api/routes/market.py:406-408 | minute_line 的 load_vr_baseline（Parquet 读）同步执行 | 同文件 986 行已有"必须丢线程池否则卡死服务"的先例注释 | 中 | to_thread |
| B6 | services/snapshot_service.py:44-63 | parquet 写盘在 async refresh 内同步执行 | write_parquet_atomic 直接调用 | 中 | asyncio.to_thread(self._maybe_save) |
| B7 | services/quote_hub.py:162,169-183 | 订阅队列无界；广播按订阅者重复序列化 | asyncio.Queue() 无 maxsize；慢消费者队列无限增长 | 中 | Queue(maxsize=32) 满则丢帧；同订阅集复用序列化 |
| B8 | api/routes/market.py:941-951,608-609 | 每请求重复同步拉全量目录/成分（_attach_official_flags 每卡片 get_catalog(1000)+get_members） | 无缓存，SQLite 阻塞 | 中 | name→code 映射与成员集合用 cache_on 包 5min TTL |
| B9 | api/routes/events.py:177-186,335-340 | events_for_symbol 50 事件逐条查 directions（N+1）；collect 60 只串行拉新闻 | 每事件一次查询；单只 1s 时整轮 60s+ | 中 | EventStore 批量方向接口；Semaphore(6)+gather |
| B10 | services/theme_service.py:649-658 | active_days 每题材重复解析整天涨停池 | O(题材×天数×池大小) | 中 | 循环外按天预计算 tag_set 查表 |

### 2.2 结构维度

| # | 位置 | 问题 | 依据 | 风险 | 建议 |
|---|---|---|---|---|---|
| B11 | theme_service.py:865-1132,582-737 | _build_card 268 行 / build_theme_board 156 行，四段职责混杂 | 局部变量 20+ | 中 | 拆 _build_ladder/_compute_premium/_build_leaders（与纯函数范式一致） |
| B12 | 全仓 60+ 处 `except Exception:` | 部分完全静默（bare pass）——历史上 net_main 字段错正是被静默降级掩盖 | picks.py:470-471 无日志 pass；对比 theme_service.py:751 有规范 warning | 中 | 约定：任何降级必须 log.warning 带上下文；ruff BLE 规则固化 |
| B13 | paper/engine.py:38 与 quote_hub.py:15 与 market.py:186 | 同一"北京时间"三种推导并存 | astimezone()（随机器时区）/utcnow+8h（弃用）混用；机器非 CST 时 paper T+1 解冻日错日 | 中 | 抽统一 beijing_now()（zoneinfo Asia/Shanghai） |
| B14 | websocket/routes.py:49-58,66-69 | reader 的 pong 与 writer 的行情推送并发 send_json 无互斥 | Starlette WebSocket 不保证并发写帧不交错 | 中 | asyncio.Lock 包所有 send，或 pong 走订阅队列 |
| B15 | api/routes/market.py:186 | 全仓唯一弃用 datetime.utcnow() | sentiment-history 内 | 低 | 统一 app.core.db.utcnow |
| B16 | api/routes/market.py:659 | longhu_detail 用周末规则版 _default_trade_date()，与日历版并存 | 违反"日期必须日历锚定不猜" | 低 | 改 await _default_trade_date_async |
| B17 | services/theme_service.py:925-926 | primary=True 死变量（is_primary 恒真，注释语义已不存在） | 赋值后无分支 | 低 | 删除 |
| B18 | services/quote_enrich.py:30-84 | 涨跌停价与估值双缺失时对腾讯打两跳重复请求 | fill_limit_prices/fill_valuation 各自独立 await | 低 | 合并 fill_missing_fields 一次补齐 |

### 2.3 工程化维度

| # | 问题 | 依据 | 风险 | 建议 |
|---|---|---|---|---|
| B19 | quote_hub 与 websocket 路由**零测试** | 50 个测试文件无 test_quote_hub/test_websocket；行情底座的状态机（stale 沿触发、降级、广播）是红线 2 相关的关键路径 | 中 | 优先补 _refresh_closed_state 与 refresh 失败→_mark_all_stale 单测；ws 订阅/退订集成测试 |
| B20 | core/db.py:49-50 迁移失败 except: pass 静默 | 列缺失问题延迟暴露、根因难定位 | 低 | log.warning 带 exc_info |
| B21 | api/routes/market.py:534,969 `request: Request = None` 类型标注错误；picks.py:735 市场基准缺失时把个股涨幅当超额写复盘 | 污染 classify_failure 归因统计 | 低 | 去默认值；excess=None 显式处理 |

---

## 三、代码质量审查——前端（7/10）

### 3.1 性能维度

| # | 位置 | 问题 | 依据 | 风险 | 建议 |
|---|---|---|---|---|---|
| F1 | components/stock-detail.tsx:383-385 | **analyze() 每渲染重算且无 memo**——displayBars 随 WS 秒级 tick 变化，MA/MACD/KDJ 全量重算 | 主审实证（383 行，组件体直接调用） | **高** | useMemo([displayBars])；replay-chart.tsx:52-61 已是正确示范 |
| F2 | app/workbench/page.tsx:475,65,185-192 | 表格行 map 内 sparks.items.find O(n²)；merged={...extra,...quotes} 每渲染新建；watch/holding 列表每渲染 filter+map | 30 只自选 × WS 每 5s tick | **高** | merged/watchQuotes/holdingQuotes useMemo；spark 建 Map<symbol,closes> O(1) 取 |
| F3 | components/kline-chart.tsx:60-66 | effect 依赖 [bars,height] → bars 每变 chart.remove()+createChart 全量重建 | 与 kline-chart-pro 的 fillRef 原位更新范式分叉（第三处范式不一致） | 中 | 改 fill 模式：chart 仅 mount 建，bars 走 setData/update |
| F4 | components/minute-chart.tsx:318-324,266-285 | 轮询触发整图重建；十字光标 O(n) 线性查 K 线 | points 引用变即 remove/create | 中 | 增量 update+append；查找按索引换算 |
| F5 | components/kline-chart-pro.tsx:182,194 | marker 过滤渲染路径 O(n²) | 每 bar 内遍历 markers 数组 | 中 | 先按时间建 Map<ts,marker[]> 再单遍 |
| F6 | app/workbench+market 多路轮询不随 document.hidden 暂停 | 后台标签页仍打后端 | workbench 5 条+market 3 条定时器 | 中 | usePolling(fn,ms) hook 内置 visibilitychange |
| F7 | components/theme-card.tsx:197-203 | ladderByBoard Map 渲染期构建未 memo | 每次父渲染重建 | 低 | useMemo |

### 3.2 结构维度

| # | 位置 | 问题 | 依据 | 风险 | 建议 |
|---|---|---|---|---|---|
| F8 | kline-chart-pro.tsx:41-50 + screener/page.tsx:16-24 + backtest-tab.tsx:25-31 | calcMA 与 pctText/pctCls **三处重复实现**，口径漂移风险 | lib/technical-analysis 与 lib/format 已有权威版 | 中 | 全部收敛到 lib，删本地副本 |
| F9 | tape/themes-tab.tsx:133-152 | 四个筛选 handler 同构三连（set+updateUrl+load） | 新增参数易漏步 | 低 | 抽 applyFilter(partial) |
| F10 | market/heatmap-tab.tsx:132,141 | 可变 Set 包 useMemo + 整对象浅拷贝强刷（注释自认 hack） | watchSymbols mutation 不触发渲染 | 中 | watchSymbols 改 state/版本号，删浅拷贝 hack |
| F11 | components/search-box.tsx:17-27 | 搜索无竞态守卫（250ms debounce 只取消定时器不取消在途请求），旧响应可能覆盖新结果；quickAdd catch{} 吞错 | 快速输入场景 | 中 | 递增 seq 或 AbortController 丢弃过期响应；失败给行内提示 |

### 3.3 工程化维度

| # | 位置 | 问题 | 依据 | 风险 | 建议 |
|---|---|---|---|---|---|
| F12 | **主题双态样式纪律**：profile-panel.tsx:60/66/75（text-zinc-200/300 裸用）、market/page.tsx:196/219、kline-chart-pro.tsx:349-361 缩放按钮、heatmap hover 卡 267-276 | 浅色主题下文字不可读/按钮隐形（用户 IDE 是 light 主题，属可见缺陷） | 多处裸深色类无 dark: 前缀 | 中 | 一次全局"裸浅色文字"排查统一补双态；可加 lint 规则拦截 |
| F13 | lib/api.ts:183/188/193 | 三接口 `<T = unknown>` 泛型默认 + 665 行双重断言 | 调用方被迫自行断言 | 中 | 补具体 interface，删 unknown 默认 |
| F14 | trade-form.tsx:36 | 佣金/印花税魔法数字硬编码前端，与后端撮合费率无单一来源 | est*0.00025/0.0005 与 paper engine 配置漂移即估算失真 | 中 | 费率由后端预检响应返回，前端仅展示 |
| F15 | detail/info-panel.tsx:87/104 | 列表 key 用数组索引 | 公告/新闻刷新时 diff 失效 | 低 | 改业务键 |
| F16 | workbench 分组管理用 window.prompt/confirm | 原生阻塞弹窗、不可测试、与全局 UI 不一致 | A1 实现（本轮我写的） | 低 | 换内联输入/受控 Modal——接受现状，列入后续打磨 |
| F17 | screener/page.tsx:178-183 | tr onClick + window.open "_self" 绕过 Next 路由（整页刷新、无键盘可访问性） | 页面已冻结，但代码在 | 低 | 若保留页面则改 Link；若执行 D2 删除则随之消失 |

### 3.4 hooks/quote 流（已完成项回顾）
use-quote-stream 动态订阅、事件契约、分级加载均已落地（P1/O1 ✅）。残留：空 catch 静默（连接失败用户仅靠状态徽标感知）——可接受（有 StreamStatus 显示），不动。

---

## 四、文档体系审查（6/10）

### 4.1 数字漂移（多处互相矛盾，主审已实证）

| # | 问题 | 依据 | 风险 | 建议 |
|---|---|---|---|---|
| D1 | 端点数四处矛盾：README"79"、api.md"82"、AGENTS §5"94"、PROJECT-MASTER §1.2"96"；实际 96 | 四文件 grep 实证 | **高** | 唯一权威 = /openapi.json；各文档不写具体数字只链接 api.md |
| D2 | 测试数矛盾：AGENTS §1"456/56"（§2 又 551/83）、PROJECT-MASTER 头部"118"、§二"118 用例"、README"551+83" | 同文档内部也矛盾 | **高** | 基线数字只在 AGENTS §2 一处；§1 快速启动注释删数字 |
| D3 | PROJECT-MASTER §六.1 仍写 tape"四 tab 含板块排行"、§三目录树仍列 minute_signals.py | boards-tab.tsx 已删；minute_signals 零引用 | **高** | tape 改三 tab；目录树注明已删（文件本身随 D-D2 清理） |
| D4 | api.md 仍列已删除的 minute-signals/minute-decisions 端点；缺分组 CRUD 三端点、题材合力/官方指数两端点 | 与 routes 现状 diff | **高** | 删两行补五行；注明"以 openapi.json 为权威" |
| D5 | README 阶段表"REST 79 端点"、目录列已删页面（watchlist/boards/heatmap 等）、"docs 14 篇"（实际 30） | 与现状 diff | 中 | 目录块重写为 5 导航 |
| D6 | CONTEXT.md 每日精选词条"五维评分"与现行六维矛盾；architecture.md"Next.js 15"、数据流图首源仍写东财 | echelon 第六维已上线 | 中 | 词条改六维；architecture 两行更新 |
| D7 | ui-redesign-plan §5 全部 ⬜ 但 plan-review 已逐项核验 ✅ | 两文档矛盾 | 中 | 打 ✅ 后归档 |

### 4.2 冗余堆积（改一处漏一处的高危点）

| # | 问题 | 建议 |
|---|---|---|
| D8 | 待办账本四处分裂（AGENTS §4 / PROJECT-MASTER 近期路线 12 项全 ✅ / README 阶段表 / retro）——plan-review 自己承认"账本分裂发现 3 处矛盾" | **高**；PROJECT-MASTER 近期路线压成两行指针；确认"retro 唯一明细 + AGENTS §4 阶段规划"两处 |
| D9 | 行为基线三处逐字重复（AGENTS §6.4 / PROJECT-MASTER §十四 / retro §四） | 中；权威留 AGENTS，余改链接 |
| D10 | 踩坑清单两处维护（PROJECT-MASTER §十 vs deployment.md） | 中；删前者留链接 |
| D11 | 阶段状态表三份（AGENTS/README/PROJECT-MASTER §十二，后者缺阶段 10 行） | 低；PROJECT-MASTER 改一行式索引 |

### 4.3 缺失与索引

| # | 问题 | 建议 |
|---|---|---|
| D12 | lib/events.ts 事件契约无文档覆盖；linkage-design §2.1 状态管理规范未提事件层 | 中；linkage-design 补 §2.5"前端事件契约" |
| D13 | theme_audit 脚本与题材修复无入口索引；AGENTS §4 未提 linkage-plan 的消融验证/落选者落库/E4 三计划（新接手者不知架构主线） | 中；AGENTS §5 补一行三链、§4 插"阶段 F：联动总线改造（进行中）" |
| D14 | 五份 09-01 关键文档不在 AGENTS §5 文档地图 | 中；补"2026-09-01 评审与规划"行 |

**归档建议（docs/archive/，一次性过程文档，历史价值保留、检索降噪）**：ui-redesign-plan、minute-chart-plan、architecture-redesign、plan-review、sentiment-phase-review、orderbook-source-evaluation、github-stars-trading-analysis、picks-stability-sweep、theme-audit-2026-09-01（+3MB json 移出 git 或压缩）。picks-replay-baseline 保留（含方法论基线数据）。

---

## 五、整体改进优先级汇总

### P0（本轮内修——错误行为/数据污染）
1. **F1** analyze 加 useMemo（WS 每 tick 全量重算技术指标，直接可感卡顿）
2. **F2** workbench 三处 O(n²)/无 memo 热点（WS 每 5s 全列表重算）
3. **D1-D4** 文档数字漂移统一（端点数/测试数/tape 三 tab/api.md 增删行）——半小时文档收口
4. **B21** picks.py:735 基准缺失时 excess=None（污染复盘归因统计）

### P1（下一次迭代——性能与正确性）
5. **B1** picks 事件 N+1 → 一次载入分组 dict（generate 性能主线）
6. **B2** theme_catalog N+1 + to_thread（题材同步阻塞事件循环）
7. **B3** generate_picks 315 行按阶段拆函数
8. **F3/F4/F5** 图表三范式收敛为 fill 增量模式（kline-chart/minute-chart 重建 + marker O(n²)）
9. **B4/B5/B6** 同步 IO 进线程池（sparkline/vr_baseline/parquet 写）
10. **B12** 静默降级纪律：log.warning 全仓排查 + ruff BLE 规则
11. **F12** 主题双态样式全局排查（用户 light 主题下多处不可读）
12. **B13** 统一 beijing_now()（paper T+1 跨时区正确性）

### P2（规划内/低频）
13. B7/B8/B9/B10（quote_hub 队列上限、路由缓存、events N+1、theme active_days）
14. F6 usePolling(visibilitychange)；F8-F11（重复实现收敛、搜索竞态、heatmap hack）
15. B14 WS 发送互斥；B19 补 quote_hub/websocket 测试
16. D8-D14 文档归档（docs/archive/ 9 份）与索引补齐（AGENTS §5/§4）
17. F14 交易费率单一来源；F16 prompt/confirm 换受控组件
18. minute_signals.py/minute_decisions.py 死代码文件删除（零引用，入口已不存在）

### 明确保留（不删，附理由）
- screener 页面+端点：用户拍板冻结保留代码，tech_score 被每日精选复用
- predict 模块：有独立 mandate 与历史验证案例，文档标注"待接入"
- 逐笔页签：O2 后保留成本为零
- 大盘叠加/竞价等 idle 次屏拉取：功能价值成立，仅时序调整

---

## 六、审查覆盖度声明

- **已覆盖**：后端 52 文件（services/api/sentiment/market/picks/predict/review/paper/risk/providers/core/websocket 全模块）、前端 52 文件（全部页面/组件/lib/hooks）、文档 36 份全清单、测试体系（551+83 的盲区分析）
- **抽样验证**：子代理 findings 中高优 5 条全部主审复核（analyze 无 memo、workbench O(n²)、文档数字漂移×3）
- **未深入**：skills/ 目录（开发期参考资料，非运行时依赖，08-31 已归档管理）、data/（Parquet 内容质量属数据专项）、CI 配置（GitHub Actions 未在本次范围）
