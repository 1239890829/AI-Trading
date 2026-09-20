# 选股信号链路与提醒体系（定位 / 触发 / 流向 / 断点）

> v9.10 当前目标链见 ../implementation-plan.md、../product/product-closure-design.md 与 ../product/hunting-decision-design.md；Jev 语义协处理边界见 ../ai/jev-integration.md；盘前/盘中来源保留，页面/消息/模拟/复盘逐步共用机会及决定版本。原双家族断点继续作为事实证据，不因UI合并就假定已打通；控制台调试视图迁后台，用户风险结果保留。

> **编号**：SM-08 ｜ **层**：L2 ｜ **领域**：链路汇总 ｜ **状态**：现役
> **⚠️ 状态不在此（`GOV-002` 收敛）**：**任何「未做 / 待办 / 进度」断言一律去 `retro-and-gaps.md` §6.0**
> （全仓唯一任务清单）。本文只写**链路现状与断点取证**；正文若出现「未做」等字样，指的是**取证边界**
> （该项尚未做实测，见 §7），**其排期与状态一律以账本 §6.0 为准**。
> **来源**：用户 2026-09-16 提问「AI 判读提醒与其他选股功能是什么关系、是否联通、是否一条完整链路」的取证结论。
> **取证方式**：全部结论均以 `Grep`/`Read` 在当轮代码上取行号，**无推理代替观察**（用户长期纪律）。
> **维护触发**：`dispatch_alert` 扇出步骤变化 / 新增告警规则 / 通知中心收口口径变化 / 下方断点被修复时。

---

## §0 一页结论

**答：部分联通，但整体不是一条完整链路。**

| 结论 | 依据 |
|---|---|
| 三大提醒（告警面板 / 悬浮球 AI 判读 / 通知中心个股机会）在**同一族内是联通的** | 共享唯一汇聚点 `dispatch_alert`（`picks/watcher.py:931`）与同一个 `AlertEvent` 主键，**不是各自独立提醒各自的** |
| 但**体系整体存在两个家族**，二者只在「猎场」页面被**拼成一张混合视图** | `append_alert` 有两类调用方：经 `dispatch_alert` 的（入事件流）与引擎**直写**晨报的（**不产生 `AlertEvent`**） |
| 因此「一条完整链路」不成立 | 断点见 §4：**横向断点**（家族 B 不可追溯）+ **纵向断点**（判读不闭环）+ **表现层断点**（落点不一致） |

一句话：**「同族内联通、跨族不联通；判读是闸门但只关了半扇门」。**

---

## §1 各功能定位对照

| # | 功能 | 定位（是什么） | 用户可见面 | 权威数据源 |
|---|---|---|---|---|
| 1 | **提醒 / 告警** | 底层**事件流**：系统规则命中即落一条 `AlertEvent`，是全部提醒的**共同上游** | 控制台告警页（`/agent?tab=alerts`）· 任务中心（`escalate` 时） | `AlertEvent` 表 + `GET /api/alerts/events` |
| 2 | **AI 判读提醒（悬浮球气泡）** | 事件流的**降噪闸门 + 二次解读**：LLM 对事件给 `verdict ∈ notify/ignore/escalate` + `reason`，只有 `notify` 且未确认才弹 | 右下悬浮球气泡（30s 轮询） | `GET /api/agent/triage/pending`（`routes/agent.py:100`）→ `alert_triage.pending_bubbles` |
| 3 | **消息通知中的个股提醒** | **精选收口后的机会出口**：`IMP-028` 后只保留「经多维筛选的个股买点」，板块/新闻/日选摘要**已移出** | 导航栏铃铛 → 右侧抽屉 | `GET /api/notifications`（`routes/notifications.py`） |
| 4 | **猎场** | **聚合分析页**（不是提醒通道）：盘前 / 盘中提醒 / 复盘 / Watcher / 统计 | `/hunting` | 多源聚合，其中「盘中提醒」读当日晨报 `payload.alerts`（`hunting/page.tsx:238`） |

---

## §2 唯一汇聚点：`dispatch_alert` 的五步扇出

`picks/watcher.py:931` 是全部**经事件流**提醒的单一编排点。顺序即依赖：

| 步 | 动作 | 代码行 | 产出 / 副作用 |
|---|---|---|---|
| 1 | `append_alert(target, alert)` | 942 | 写当日晨报 `payload.alerts`（按 `key` 去重，`morning_brief.py:622`）⇒ **猎场「盘中提醒」读的就是它**。返回 `False` = 当日重复，直接终止 |
| 2 | `record_sighting(...)` | 946–997 | 写 `watch_ledger` 台账（`layer` 按 kind 映射 `pre_limit` / `today_strongest` / `quiet_starting`）。**KB-DEC-011**：提醒时已封板则只提醒不入册 |
| 3 | `maybe_open(...)` | 1001–1010 | 仓位引擎裁定是否开模拟仓（仅 `buy_point` / `confirm` 在白名单；`pre_limit` 不在） |
| 4 | `repo.record_trigger(...)` | 1029 | **写 `AlertEvent`** —— 这是「可追溯」的来源，`snapshot` 必须含 `name`（缺则悬浮球整条过滤，2026-09-10 修复） |
| 5 | `NotifierRegistry.dispatch` + `update_event_channels` | 1036–1037 | 按 `push_policy` 分发（`CRITICAL` 才推飞书）+ 回写通道 |

---

## §3 触发来源全清单

### §3.1 系统内置告警规则（7 条）

| 规则名 | 定义位置 | 覆盖信号 |
|---|---|---|
| `__picks_watcher__` | `picks/watcher.py:71`（`ensure_system_rule`，`:863`） | 盘中个股扫描（`condition_type="picks_intraday"`）——**默认规则**，临板预警与手动重放也走它 |
| `__picks_buy_point__` | `picks/buy_point.py:47` | 买点确认——**唯一进通知中心**的规则 |
| `__signal_health__` | `picks/signal_health.py:200` | 信号健康度异常 |
| `__sentiment_monitor__` | `sentiment/intraday_monitor.py:43` | 盘中情绪监控 |
| `__ths_reason_sentinel__` | `services/ths_sentinel.py:37` | 涨停原因数据新鲜度哨兵 |
| `__llm_gateway_probe__` | `services/llm_probe.py:245` | LLM 网关存活探针 |
| `__board_surge__` | `picks/board_surge.py:568` | 板块异动 |

> 另有**用户自定义规则**（`POST /api/alerts/rules`），同样落入 `AlertEvent` ⇒ 同样进 AI 判读。

### §3.2 `append_alert` 的两类调用方（**本文件最关键的区分**）

| 家族 | 调用位置 | 是否产生 `AlertEvent` | 可达面 |
|---|---|---|---|
| **A｜经 `dispatch_alert`** | `watcher.py:1084`（盘中扫描）· `pre_limit_radar.py:252`（临板预警）· `buy_point.py:296`（买点，独立规则）· `picks_intraday.py:121`（手动重放） | ✅ 是 | 猎场 + 通知中心（限买点）+ 悬浮球 + 告警页 + 台账 |
| **B｜引擎直写晨报** | `pre_limit_radar.py:195`（`board_reopen` 开板重评）· `position_engine.py:280`（`position_open` 自动开模拟仓）· `exit_engine.py:171`（持仓监护：止损/止盈/出场） | ❌ **否** | **仅**猎场「盘中提醒」 |

**⇒ 家族 B 的三类提醒没有事件主键、不进判读、不进通知中心、不出现在告警页。**
猎场「盘中提醒」区块因此是一个**混合视图**：A 的写入 + B 的直写混在同一列表里，外观无差别。

---

## §4 断点清单（回答「是否一条完整链路」）

### G1｜纵向断点：**判读闸门只作用于悬浮球，不作用于通知中心**（真实缺陷）

- `pending_bubbles`（`alert_triage.py:329`）过滤条件含 `verdict == "notify"`。
- `_alert_items`（`routes/notifications.py:117`）过滤条件只有 **规则名 + `kind == "buy_point"` + 有效代码 + 有名称**（`:128`），**完全不看 `verdict`**。
- 后果：被判为 `ignore`（已降噪）的买点机会**仍会出现在消息通知里**，而 `body` 里还拼着「AI 判读（**已降噪**）：…」（`:137–138`）——**同一条通知自我矛盾**：既说已降噪，又推送出来。
- 性质：不是设计取舍，是 `IMP-028` 收敛通知来源时**未同步引入判读闸门**。

### G2｜表现层断点：同类个股跳法三处不一致

| 入口 | 落点 | 代码 |
|---|---|---|
| 悬浮球「查看详情」 | **个股详情弹窗** `openSymbolDetail` | `floating-assistant.tsx:797`（2026-09-16 本轮修正） |
| 猎场个股链接 `StockLink` | 个股详情弹窗 | `components/stock-link.tsx`（统一左键拦截） |
| 通知抽屉**行体** | **通用**详情弹窗 `kind: "generic"` | `notification-drawer.tsx:155–159` |
| 通知抽屉**个股 chip** | 个股详情弹窗 | 同行并列的 `StockLink`（`:418`） |

⇒ 同一行通知里，点正文与点股票码，**弹出来的东西不一样**。

### G3｜横向断点：家族 B 不可追溯（见 §3.2）

无 `AlertEvent` ⇒ 无统一 ID ⇒ 复盘时**无法把「自动开的这个仓」回指到「哪条提醒触发」**（`maybe_open` 只在家族 A 的第 3 步被调用，家族 B 的开仓通知在开仓**之后**才补写，二者没有共同键）。

### G4｜过期断言：~~4 处~~ **10 处**注释与 `IMP-028` 后的现实不符 —— ✅ 已全部订正

> **状态**：`IMP-034`（2026-09-16）**已逐处按现实改写**（非删注释）。
> 实施时按「同族全扫」原则复查全仓 `通知中心` 字样，**新发现 5 处**原清单未列
> （下表单列 ①②③④⑤ 中的 ⚠️ 标记行）——原表把范围记小了，且**标题写「4 处」而表里列 5 行**
> 本身即一处计数不自洽。**教训**：收敛口径的改动要按**关键词全仓回扫**，
> 不能只改「上次扫出来那几处」。

| 位置 | 原现文（已不成立） | 现实（改写后口径） |
|---|---|---|
| `picks/pre_limit_radar.py:26`（模块 docstring） | 「`dispatch_alert` —— 规则直发**通知中心**，不经 LLM 判读」 | 进**当日简报 `alerts[]`** + 落 `AlertEvent`（watcher 系统规则）；**不进通知中心**——`_NOTIF_RULE_NAMES` 白名单只含 `__picks_buy_point__` |
| `picks/pre_limit_radar.py:236` | 同上（代码内注释逐字重复） | 同 |
| ⚠️ `picks/pre_limit_radar.py:147`（`pre_limit_sweep` docstring） | 「先登记（台账）→ 再提醒（**通知中心**）」 | 同；**本轮新发现**（原表未列） |
| `picks/position_engine.py:275` | 「**通知中心留痕**（in-app；飞书矩阵不动）」 | 仅写当日简报 `alerts[]`（猎场「盘中提醒」），不进通知中心 |
| `picks/exit_engine.py:159`（`_notify` docstring） | 「**通知中心** + （critical 时）飞书」 | 同 |
| ⚠️ `picks/exit_engine.py:470` | 「当日一次在**通知中心**留痕」 | 同上（**与 `_notify` 同一落点**，改一处必改另一处）；**本轮新发现** |
| ⚠️ `services/alert_triage.py:351` | 「无代码 = 无效个股提醒（**方向级事件走通知中心**）」 | 方向级事件在**盘面页「事件」标签**（`/api/events/impact`），不进通知中心 |
| ⚠️ `api/routes/picks.py:292` | 「预警已接线（**通知中心** + 自动 action_items）」 | 告警台账 `alert_event` + 通道矩阵 + 自动 action_items（后者**经核为真**：`review/service.py:149` → `build_signal_health_action_item`）；**不进通知中心** |
| ⚠️ `review/service.py:199` | 「信号健康度预警接线（P1）：warning/drift → **通知中心**/飞书」 | → 告警台账 / 飞书；不进通知中心。**本轮新发现** |
| ⚠️ `core/config.py:104` | 「**站内通知中心**：事件评分 ≥ 此阈值才进通知」 | `notifications_news_min_score` **已空转**（端点只回显、无过滤消费）；保留仅为旧客户端兼容。**本轮新发现** |

> 同族教训见 `kb/09-verification-pitfalls.md`（**断言与实现脱钩**：收敛口径的改动必须回扫所有自称该口径的注释）。

**另附：本轮连带修掉的一处「守卫与实现脱钩」**（同族，但住在测试里）——
`tests/test_event_loop_no_block.py` 的 `GUARDED_ROUTES` 原登记
`notifications.py` + needle `asyncio.to_thread(store.list_events, active_only=False, limit=80)`，
保护「通知抽屉的新闻源（limit 80，实测 9.6ms）」。删除 `_news_items` 后该文件**已无任何
`store.list_events` 调用点** ⇒ 守卫开始对一个**够不到的落点**报警（门禁实测 1 红）。
处置：**删条目**（非放宽断言——`fn="store.list_events"` 的裸调用判据保留，其余 7 条目不变），
并在原处留注释说明「若接回事件源须一并恢复」，避免被误读成漏登记。

### G5｜死代码但有测试 —— ✅ 已删除

~~`_daily_pick_item()`（`notifications.py:156`）与 `_news_items()`（`:204`）在 `IMP-028` 后**不再被 `items` 拼装引用**（`:315` 仅 `items = alert_items`），但 `tests/test_notifications.py:147/165/171` 仍在测 ⇒ **测试守着一个够不到的落点**，给出虚假的覆盖信心。~~

**处置（`IMP-034`，2026-09-16，取「删除」而非「接回」）**：`_daily_pick_item` /
`_news_items` / `_classify_four_row` 三函数**连同 `_FOUR_LABEL` 字典一并删除**
（`notifications.py` 净减 127 行），并清理 `asyncio` / `timedelta` /
`get_session_factory` / `BJ_OFFSET` 四个随之无用的导入；
`tests/test_notifications.py` 删除对应用例与 `_FakeRow`/`_FakeDB`/`_FakeStore`/`_event_row`
夹具（净减 53 行），三个路由用例去掉已无用的 `event_store` 注入。
**端点响应契约不动**：`policy="stock_opportunities_only"` 与 `news_min_score` 字段照旧返回。

---

## §5 与账本已有记录对应

| 账本项 | 状态 | 与本链路的关系 |
|---|---|---|
| `IMP-028` | ✅ 已闭环 | **通知中心只推个股机会**——本文件 G1/G4/G5 的直接成因（收敛了来源，未收敛判读与注释） |
| `IMP-021` | ✅ 已闭环（由 `IMP-028` 收口） | 告警站内通道；旧断言「全仓无 `in_app`」已过时 |
| `IMP-013` | A 档 P2 | 助手写入通道（自然语言建告警）——**会新增规则即新增事件来源**，落地时须同步本文件 §3.1 |
| `IMP-015` | A 档 P2 | 悬浮球 in_app 告警角标等交互增强——与 G2 落点统一同处一个改动面 |
| `IMP-030` | ✅ 已闭环 | 主动漏洞发现探针轮（`services/evolution_probes.py`） |
| `RSH-026` | 部分闭环 | 机会成本/可成交性/样本门槛记分卡；剩余部分前置未满足 |
| `RSH-027` | A 档 P1 | 场景化 KB 路由与消融——KB 是本链路的**解释层**，不改变硬门 |

> 其余相关：`BUG-010`（墙钟敏感判据，D 档观察）、`BUG-014`（已销账）、`GOV-015`（账本档位守卫）。

---

## §6 链路结论与串联建议

### 结论

现状是**「单点汇聚 + 双家族分裂 + 半扇闸门」**：

```
                    ┌─────────────── 家族 A（可追溯）───────────────┐
盘中扫描   ─┐        │                                              │
临板预警   ─┼─ dispatch_alert ─┬─ 晨报 alerts ──────────► 猎场「盘中提醒」
买点       ─┤   (watcher:931) ├─ watch_ledger 台账
手动重放   ─┘                  ├─ 仓位引擎 maybe_open ─► 模拟持仓
                              ├─ AlertEvent ─┬─ AI 判读 ─► 悬浮球气泡（过 verdict 闸门）
                              │              ├─ 通知中心（只 buy_point，✗ 不过闸门）
                              │              └─ 告警页 / 任务中心
                              └─ NotifierRegistry（飞书，仅 CRITICAL）

开板重评 ─┐
自动开仓 ─┼─ 直写晨报 alerts ─────────────────► 猎场「盘中提醒」（✗ 无 AlertEvent）
持仓监护 ─┘                                      家族 B（不可追溯）
```

⇒ **不是一条完整链路**：家族 A 内部是一条**有依据、可追溯**的链（触发 → 快照 → 判读 → 分发，共享主键）；家族 B 是**三条游离的旁路提醒**，只在页面层与 A 混显。且判读这一唯一「语义闸门」只关了悬浮球半扇门，导致**同一判读结论在下游被两种对待**。

### 串联建议（按优先级，**待批**）

| 级别 | 建议 | 预期效果 | 代价 |
|---|---|---|---|
| **P0** | **把 `verdict` 提升为统一闸门**：通知中心对 `ignore` 不再产出条目（或显著降权且文案自洽） | 消除 §G1 的自相矛盾；「降噪」成为全局语义而非局部 | 需定口径：`ignore` 是「不通知」还是「折叠」——**属口径变更，须拍板** |
| **P0** | **家族 B 纳入 `dispatch_alert`**（或至少落一条轻量 `AlertEvent`） | 消除 §G3：开仓/出场可回指触发来源，复盘闭环 | 三类提醒语义不同，需逐一定 kind 与 push_policy |
| **P1** | ✅ **已实施**（`IMP-033`，PR #16/#17）：**统一个股落点为 `openSymbolDetail`**（通知抽屉行体改走 symbol 弹窗） | 消除 §G2，三处体验一致 | 小，纯前端 |
| **P1** | ✅ **已实施**（`IMP-034`，2026-09-16）：**修正 §G4 过期断言**（实为 10 处，非 5 处）+ **删除 §G5 死代码** | 消灭「注释说的 ≠ 代码做的」与虚假覆盖信心 | 小 |
| **P2** | 为「同族内联通」补一条**端到端可追溯测试**（同一 `AlertEvent.id` 在三处产出的关联断言） | 把本文件的结论变成**可机械守住的契约** | 中 |

> ⚠️ 以上为**建议**，按用户「改进先提后做」纪律，**未获确认前不实施**。P0 两项涉及口径变更，须拍板。
>
> **实施进度（2026-09-16）**：P1 两条**均已实施完毕**（落点统一 → `IMP-033`；断言订正 + 死代码
> 清理 → `IMP-034`）。**P0 两条仍待拍板**（`verdict` 统一闸门 / 家族 B 纳入 `dispatch_alert`）——
> 二者都改变推送口径，属「先提后做」范围。P2 未动。

---

## §7 待验证项（本文件未证实、勿当结论使用）

1. ~~家族 B 的三类提醒在**真实盘中**是否确实从未出现在通知中心~~ —— **已实测（2026-09-16 `BUG-016` 取证轮）**：
   生产库 `alert_event` 共 **1997** 条、跨度 `09-02 11:01:34` → `09-16 11:29:51`（覆盖 11 个交易日），
   其中 `snapshot.kind == "buy_point"` **0 条**；`alert_rule` 5 条**无一**名为 `__picks_buy_point__`
   ⇒ 通知中心自建立起**从未产出过条目**，本文件 G4 结论由代码路径取证升级为**实机证据**。
   （⚠️ 同轮更正一处误读：`opportunity_decision_snapshot` 0 行**不等于**「循环没跑到判定阶段」——
   该表当天才随迁移落地，运行中的进程未必带这段代码。详见账本 `BUG-016`。）
2. `maybe_open` 与 `position_open` 之间是否存在其他隐藏关联键——仅核了 `dispatch_alert` 与本文件所列调用点，**未全仓穷举**。
3. 通知中心当前实际事件的 `verdict` 分布（有多少 `ignore` 被推送）——需查库统计，**未做**
   （注：`buy_point` 事件数为 0，故当前该分布**无样本**；待首条买点事件产出后再统计）。
