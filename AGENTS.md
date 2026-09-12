# AGENTS.md — AI 开发者交接手册（必读，2026-08-31 全量重写；2026-09-10 更新：门禁实测回填、阶段 A/B 销账、待决清单收敛）

你接手的是 **AShare AI Trader**：A 股实时行情 + 量化投研 + 模拟交易 + 事件驱动选股的一体化工作台。
本文件是你的作业手册：现状、待办、阶段安排、工作纪律全在这里。
**动手前先读完本文件，再按需查 `docs/PROJECT-MASTER.md`（技术总览）与 `docs/archive/plan-review.md`（历史计划复盘，只读）。**

---

## 0. 红线（违反即事故）

1. **禁止**连接真实券商 / 自动真实下单。系统只有模拟交易（`/api/paper/*`）。
2. **禁止**把 mock 数据、过期缓存冒充实盘。数据源失败 → 标 `stale` + health=degraded。
3. **禁止**输出确定性买卖结论（必涨/稳赚）。一切结论 = 偏向 + 依据 + 失效条件；
   事件标的池等"机会输出"必须带「不构成买卖建议」声明。
4. **API Key 只存 `backend/.env`**（已 gitignored），绝不入库/入前端/入文档。
5. 撮合规则（T+1/涨跌停拒/整手/费用/停牌拒）是硬拦截，不可绕过。
6. **新增页面/板块需先论证**：默认通过复用、扩展、联动实现需求（联动设计原则，见 `docs/summary/architecture-design.md` §0）。

---

## 1. 快速启动

```bash
# 后端（venv 已建好；.env 含 THS key，LLM 走 `claude -p` → GLM-5.3 网关）
cd backend && source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
# ⚠️ 绝不用 --reload：与 SQLite 锁组合会反复挂死（2026-09-02 定位，见 §6.1）

# 前端（node_modules 已装）
cd apps/web && npm run dev                        # http://localhost:3000/workbench

# 测试与门禁（每次改动全部跑，全绿才算完；**数字必须实测回填，勿凭记忆**）
cd backend && .venv/bin/pytest --basetemp=/tmp/pytest-basetemp     # 后端 2619 项（2557 passed / 62 skipped）· 171 文件（09-12 实测）
# ⚠️ 不要在这条命令上再叠一个 `-q`：`pyproject.toml` 的 addopts 已有 `-q`，
# 叠加后等价于 `-qq`（extra-quiet），pytest 9.1.1 在该级别下**不打印汇总行**
# （只剩 `....  [100%]`，`passed/skipped` 全看不见）——取数会以为"测试没跑完"。
# 需要机器可读计数时改用 `--junitxml=/tmp/be.xml` 解析（2026-09-11 踩）。
# ⚠️ 耗时强依赖「8000 是否在跑」：后端服务停着 ~64s，服务在跑时实测 93s~330s 波动。
# 原因是常驻调度与测试同时抢 SQLite/网络；**报耗时必须说明前提**，否则会被当成回归。
# ⚠️ `--basetemp` 不可省：默认临时目录会被沙箱拒绝创建（EEXIST → PermissionError），
# 表现为几十个 E 而非 F，极易误判成代码回归（2026-09-11 踩，见 kb/03）。
# ⚠️ **跳过数 2 → 62 是新增守卫的参数化产物，不是覆盖率丢失**：62 = **60** + 2，全额对上——
# 60 项来自 `test_import_lint.py`「装配层/其他：不受本规则约束」（分层规则表按模块参数化，
# 非业务层模块显式跳过；**新增一个非业务层 .py 就 +1**，如 S2-8 的 `app/core/bjtime.py`、
# 2026-09-12 的 `app/models/notification.py`），另 2 项为既有的「指数无涨跌停概念」后端不适用项。
# ⚠️ **本条自身也曾失真**：原文写「58 项来自 import_lint」，而 58+2=60≠61（差额 1）——
# 实测 `pytest tests/test_import_lint.py` = 171 passed / **60 skipped** ⇒ 60+2=62 才自洽。
# **教训与本文档的警告同源：数字标注要么当轮实测回填，要么写"实测方法"而不写死数值。**
cd apps/web && npx tsc --noEmit                   # 类型 0 错误
cd apps/web && npx eslint .                       # 0 error / 0 warn（P1-27 已清零；余 1 处 C 类显式豁免）
cd apps/web && CODEBUDDY_SAFE_DELETE_ENABLED=0 npx vitest run   # 前端 429 项 / 52 文件（09-12 实测）
cd backend && .venv/bin/python -m pyflakes app tests scripts   # 0（scripts 已纳入口径，P2-18）
python3 scripts/doc-health.py                    # 文档体检：0 待处理（收尾必跑，见 kb/07 §8.2）
# 生产构建前必须先停 dev server（.next 冲突已踩两次）：
lsof -ti tcp:3000 | xargs kill -9; cd apps/web && CODEBUDDY_SAFE_DELETE_ENABLED=0 npx next build
```

> **门禁口径**：后端 2619 项（2557 passed / 62 skipped）· 171 文件、前端 429 项 / 52 文件、eslint **0 error / 0 warn**
> （25 条回归已按 P1-27 清零；仅 notification-drawer 保留 1 处带理由的 C 类豁免）。
> **测试规模与告警数同属「会失真的状态标注」**——改动后要实测回填，不要沿用旧数字
> （此前「≤1 warn / 后端 580 / 前端 97 / 219 / 257 / 263 / 342 / 1925 / 2553 / 2556 / 2557 / 2574 / 2576 /
> 2584 / 2585 / 2590 / 2602 / 2606 / 前端 423」均已被后续改动追过，教训见 `docs/retro-and-gaps.md` §七）。
> ⚠️ **交接 note 里的门禁数字也会失真**（2026-09-12 实测：note 写「2574 / 2513」，当轮为 2576 / 2515，新加 8 项后为 2584 / 2523 ⇒ 差值恰好等于新增测试数，可自洽核对）——**取数一律自己跑一遍**。
> **加测试文件就会让这里过期**，改测试后请顺手回填。

**发布前额外做一次接口载荷体检**（plan-review 三.7，2026-09-01 纳入）：
`node scripts/api-sweep.js`（服务在跑时）——它能抓出"HTTP 200 但数据是空的"这类
测试与类型检查都发现不了的问题（CI 无真实数据跑不了，只能本地/部署后跑）。

CI（GitHub Actions）：后端 pytest+pyflakes、前端 tsc+eslint+vitest+build。推送后**自查 CI**
（`source ~/.zshenv` 拿 GITHUB_TOKEN → `/actions/runs?head_sha=<完整SHA>` → jobs → logs），绝不问用户。

## 2. 当前状态快照（沿革记录；**会失真的数字见 §1 门禁行与 `/openapi.json`**）

**测试规模见 §1 门禁行（此处刻意不写数字，见下方 ⚠️）· 四源链 `ths→tencent→eastmoney→sina`（含熔断）· REST 端点数以 `/openapi.json` 为权威**

> ⚠️ **本节只沉淀「不随版本漂移」的口径、决策与沿革**。测试数 / 端点数 / 告警数这类**会失真的数字**，
> 一律以 §1 门禁行与 `/openapi.json` 为准（教训见 KB-ENG-36：清单类内容不该被摘要吞掉，
> 数字类内容不该被写死当事实——本节此前写死的「585 / 94 REST」两条都早已过期）。

**复盘改进项闭环（2026-09-01）：`PATCH /api/review/action-items/{id}` 处置入口 + 前端四态处置控件，破解"改进项只能产出、无法消费"（107 条全 pending、采纳率恒 0）。注意 `get_report` 会用表行状态覆盖 payload 快照——payload 是生成时快照，不同步就会"点了确认回读仍待处置"。PATCH 请求体带 `trade_date/category/title` 守卫三元组：id 是 SQLite rowid 别名且无 AUTOINCREMENT，重跑删除重建后 id 会漂移/跨日串号（实测 111→72），三元组不符返回 409 要求刷新，绝不静默挂到不相干项上。**
**实时行情秒级化（2026-09-01，`5b0024a`）：QuoteHub 1s 固定节奏 + WS 订阅队列终身复用（换队列孤儿化 writer 是"约 30s 才更新"的真因）+ 实时方法腾讯源优先（realtime_rank，ths 付费配额/8s 超时移出秒级链）+ 瞬时失败 stale_after(10s) 容忍 + 分时/K线 WS tick 实时合成（`lib/kline-live.ts`）。实测：列表/头部/K线/分时全部 0.6~1.3s 更新。**
**前端导航 5 项：工作台 / 盘面 /tape / 市场 /market / 每日精选 /picks / 研究 /research**（2026-09-01 页面合并，旧路由 302）

08-31 ~ 09-01 已完成：实时行情修复 → 真实持仓账本 → 题材合力 → 每日精选五维评分+梯队/阶段/闸门/出场纪律 →
跨日回放+参数扫描（组合稳定性：MAX_SWAPS_PER_DAY=2）→ **系统盘点（`docs/archive/architecture-redesign.md`）全清单清零**：
P0 K线三源+熔断+回放限流（`e77971f`）→ 事件采集调度+消息面六维打通（`d8e4e52`）→ 角色胜率（`2b719f3`）→
盘点清理（`50d5b8d`）→ **页面合并：盘面四合一/云图入市场/研究折叠/自选入工作台（`a9d42fa`）** →
基本面 ROE/毛利率评分补全（`ede98be`）→ **09-01 下午**：评审三批执行（分组 CRUD/一屏化/
性能主刀 O1+O2，`cfcbc7c`~`e15f084`）→ 全项目审查+P0/P1 修复（`b1708d0`/`a65b52d`）→
题材 chips 涨跌幅排序（`7ab289b`）→ 时段感知质量判定+P0 撞码修复（`ab2ddba`/`d607756`）→
市场页一屏化+事件契约简化（`52662dc`/`b893819`）→ 待办清零+六项拍板执行：
screener 彻底删除、消融验证启动（`07f29a7`/`c38cb05`）。
**09-03 ~ 09-04 已完成**（明细见 retro-and-gaps.md §五增量账）：联动切片 E/G 全勾账 → 工作台刷新「非法」瞬态修复
（validator `unset_high_low`）→ ESLint set-state-in-effect 26→0（`use-polling-fetch` + 渲染期 adjust-state）→
分时图纵轴按板块限制动态设置（涨停/跌停线贴边，`lib/price-limit.ts`）→ 自选动态分组（每日精选/盘中跟踪，
`top_watch_stocks`+`/api/picks/intraday-top`）→ 复盘新增 picks 准确率维度（逐股归因自动触发+失误 findings）→
盘中情绪监控本体（sentiment P2 #14：高度板炸板/炸板率/指数急杀三类纯规则告警）。
**唯一在途：消融数据自然积累（约 2026-10 中旬跑 `--days 30 --compare-ablation` 出验收）。**

**09-08 ~ 09-09 已完成**（明细见 `docs/retro-and-gaps.md` §五/§六、`docs/kb/`）：
`/hunting` 两页合一（旧 `/picks` `/intraday` 302）· 六相位 style_router（偏移叠加 regime，`|offset|≤0.06` fail-fast）·
空仓闸门三态 `follow_state`（闸门日不给 buy_range）· 每日精选四子模块（`echelon` 梯队地位 / `regime` 炒作阶段 /
`gate` 空仓闸门 / `risk` 风险档位与出场纪律）· 跨日回放 + 参数扫描
（**稳定性靠 `MAX_SWAPS_PER_DAY`，不靠分差门槛**——涨停股梯队分差 30+ 使 15 分门槛形同虚设）·
因子库 P0 评估闭环（qlib Alpha158 族 + TA-Lib，`app/factors/`）· 进化大脑每日议程（15:45 自动执行 + A/B/C 分类 +
代码变更走 worktree 沙箱）。

**09-10 已完成**（单日大批，逐项状态以 §六 账本为准）：
- **工程门禁与缺陷修复**：测试提速二期（全量 17 分 27 秒 → **5 分 55 秒**，根因是 `test_assistant` 的 function 级
  fixture 被 15 例共用）· eslint 25 → 0 warn · **快讯事件被 UNIQUE 冲突整条丢弃**（四来源方向行只做了部分去重；
  `except IntegrityError` 把「并发重复」与「新行非法」合并成一类 → 整条丢失且每轮重试都失败）
- **闸门与阈值口径**：闸门**分档**（相位级 `退潮/冰点` 或多信号叠加才撤买入区间；单条量化擦线只提示，
  判据按**信号性质**而非 `level` 标签——`level` 是理由条数的计数产物）· 量化阈值改**历史分位**口径
  （`PROMO_FLOOR=30%` 落在 241 日分布之外、近似恒真），绝对经验值降为兜底且**理由里写明所用口径**；
  连带修掉情绪指标库**静默停更 6 个交易日**（调度传字符串日历 → TypeError 被 except 吞）与
  「用昨天的位置描述今天」（`describe()` 给的是历史末行分位 → 新增 `percentile_of_value`）
- **猎场（每日精选 + 盘中跟踪）**：两卡**合并为一个组件 + 两个适配器**（`TradingCard` 中间模型，
  4 组异名同义字段单点归一；`WatchCard` 已退役）· 重新分区为**两条瀑布流（盘中在上）**·
  盘前名单**名额由质量决定**（`MAX_PICKS` 降为容量上限 + 新增 `MIN_PICK_SCORE=50` 入选门槛，
  实测 5 只 → 2~3 只；`meta.removed` 记录出列归因）
- **控制台**：任务中心**留痕合一**（议程自动执行 → 只读任务视图，不新建表/不写第二份数据）·
  参数白名单 1 → 5 + 运行时覆盖层（**免重启生效**）+ 回滚归因（封闭集合）+ 变更存活率（三数分工，
  排除 superseded 假存活）；**风控/资金类参数永久排除**在白名单外
- **UI 可发现性**：内容行内跳转入口统一 pill（`components/ui/jump-link.tsx`）；
  `Panel` extra 位的跳转**刻意保持低调**（勿无差别套用）
- **文档治理**：09-02 调研的十项候选因子**逐项复核销账**（6 已完成/等价、5 数据阻塞、0 数据具备却未实现；
  核查表在 `docs/summary/factor-system.md §5`）；归档事故教训入 **KB-ENG-36**
  （可执行清单压成一句概括 = 丢失 N 个待办，恢复只能回 git 历史）

**09-11 已完成**（架构改进计划 阶段 0/1/2 全量交付，逐项证据见 `docs/retro-and-gaps.md` §6.5）：
- **阶段 0**（S1-1 / S1-2 / S1-4 / S1-5 / S1-6 ＋ P0-2）：模拟盘 scope 隔离 + 超卖拒单 ·
  持仓三态读取 + 哨兵 · **缺价即拒单**（红线 5 的静默失效修复）· 前端列表接口类型谎言 20 处 ·
  **涨跌停幅度实测两处漂移**（302 段 / 88 段）· 题材强度 N+1 **391 查库 541ms → 2 查库 142ms**
- **阶段 1**（S2-1 / S2-2 / S2-3）：`Freshness` 契约（失败有类型）· `TaskRegistry`
  （26 常驻任务收敛为**一份声明**，停机 66 行 → 1 行，`GET /api/system/schedulers`）·
  四源链**请求级预算**（`REQUEST_BUDGET_SECONDS=12`，按失败进熔断）
- **阶段 2**（S2-4 / S2-5 / S2-6 / S2-7 / S2-9 / S2-10 ＋ P0-3 / P0-4 / P1-1~P1-6）：
  **管线抽离**（`services/picks_pipeline.py`，`picks.py` 1161 → 367 行，反向 import 与
  伪造 `SimpleNamespace` 全解）· **`useResource`**（三态 + 可见性暂停 + 盘外降频封顶 120s，
  裸 `setInterval` **实收编 10 处**）· **`PanelBoundary`**（集成进 `Panel` body，
  一处改动覆盖全站）· **相位常量 9 处副本收编**（并实测抓出「启动」误当市场相位导致
  **「修复」相位从未被覆盖**两处真缺陷）· 角色配色合并为 `lib/role-style.ts` 一份 ·
  三态文案补 `null` 键 + 前后端逐键守卫 · relay-rank **并发化 8.0x**（2044.7ms → 256.2ms）·
  情绪**缓存槽合一**（5 消费方 1 次计算）· 同源双取数收口 · 4 处 `memo` · WS 连接复用
- **唯一未做**：**S2-8 北京时间收敛**——已裁定**拆为阶段 2.5**（改动面是阶段 2 其余项之和，
  且每处 `date.today()` 携带交易日归属语义，接近「口径变更」红线，需逐处核对）

| 阶段 | 状态 |
|---|---|
| 1 基础框架（布局/搜索/主题/错误边界） | ✅ |
| 2 行情基础设施（四源链/质量五级/QuoteHub/WS/K线/分时/盘口 + 数据可靠性：Parquet 原子写与容错读、涨跌停价补全共享化、交易日历兜底、统一缓存层 ttl_cache、天梯 seal_nextday 源自证） | ✅ |
| 3 市场与板块（宽度/情绪周期+历史序列/涨停池/炸板池/题材梯队看板/云图/官方题材目录与成分/题材人气 B1） | ✅（余：题材事件树，P2） |
| 4 投研数据（龙虎榜/资金流/财务/公司资料/新闻公告摘要 v1/集合竞价/复权因子） | ✅（余：营业部图谱/筹码/解禁/两融/大宗，P2） |
| 5 量化系统（多因子评估+防飞刀三修正 / 全市场选股器+六维评分 / 风控引擎 v1：7 档市场状态→下单预检） | ✅ |
| 6 模拟交易与回测（撮合引擎/交易页签/B/S 点+成本线/回测引擎+mandate 化+历史回放/分钟级 TDX 底座） | ✅ |
| 7 AI 系统（盘后复盘 Agent 规则层 / 新题材预判 / 新闻摘要 v1 / 事件驱动选股规则层） | 🔶 规则层全部完成；余 LLM 增强（等凭据）、MCP 封装（等调用方） |
| 8 通知与部署（预警规则+引擎+通道抽象+管理页 / 同源反代 / api-sweep 巡检 / reset 审计） | 🔶 余真实推送通道、Docker 生产化、监控（等部署决策） |
| 9 联动系统（跨页面选中标的统一路由 / 题材⇄个股双向联动 / 官方 K 线交叉验证 / 事件面板） | ✅ 全部完成（切片 E 跳转 2026-09-03；余 L9 事件条目→标的池等 §4 后续与外部触发项） |

---

## 3. 已完成模块清单（索引级；细节看对应文档）

**行情与数据**：Provider 协议 + 四源 failover 链；质量五级校验；QuoteHub（WS 推送+REST 轮询降级）；
K 线（TDX 2 年分钟级底座）；分时（均价线+量比基线）；盘口/逐笔；Parquet 快照（原子写+损坏容错读）；
官方题材目录/成分/板块 K 线（fuyao，T1）；交易日历持久化兜底。

**市场分析**：情绪周期判定（防自指修复/晋级率/中位数/真实炸板池）+ 10 日历史序列；
题材梯队看板（唯一归属/强弱分级/健康度/官方成分徽标/官方 K 线验证的多日涨幅）；
题材人气（B1：`GET /api/themes/hot`，ths 热股榜 × 官方成分反查聚合）；晋级率源自证
（B4：`GET /api/market/ladder-check`，seal_nextday 对照，实测 5 可比日零漂移）；
板块排行（含题材内资金合力 P1-5：官方成分批量快照聚合，`GET /api/themes/catalog/strength` + 官方板块指数 `GET /api/themes/catalog/index`，卡片合力条）；龙虎榜；全市场快照。
**指数详情改造（2026-08-31）**：分时 Y 轴修复（指数无均价概念，`avg` 数学上不成立——
cum_amount/cum_volume 对指数给出 ~15 元荒谬值，该 series 拉爆 Y 轴致"分时一条直线"；
腾讯分钟线对指数 avg 置 null，`is_index_minute_symbol`）；分时/K线标题带标的名称；
右列指数专属 tabs：涨速榜（`GET /api/speed-rank`，口径=最近 5 分钟涨跌幅，同花顺行情
"涨速"列同口径；ths 无涨速数值字段故自算，腾讯批量快照惰性采样 `speed_sampler.py`，
采样历史不足如实显示"采样中"）+ 板块涨幅（复用 /api/boards）；指数页关闭自我叠加、
跳过盘口/逐笔数据源。

**量化**：前端 `analyze` 与后端 `tech_score` 防飞刀口径完全对齐（含量价维度）；
全市场选股器（截面过滤→TDX 日K→六维评分卡，5550 只冷跑 ~20s）；风控引擎（市场状态分类→仓位参数→7 项下单预检，拦截时禁用提交）。

**交易与回测**：撮合引擎（T+1/涨跌停/费用，全部硬拦截）；交易页签（含风控实时预检、parseNum 千分位修复）；
日线回测（代码级防泄露 + mandate yaml 配置化 + meta.applied 来源分层）；历史回放（逐 bar 重算）。

**AI 与事件**：盘后复盘 Agent（规则分析器+模型路由降级+方法论版本化+元结论迭代）；
**每日精选**（`app/picks/`，grill-with-docs 两轮澄清共九项决策）：≤5 只瀑布流卡片（/picks），
**六维**规则版多角色评分（情绪/消息/技术/基本面/资金 **+ 梯队**，TradingAgents 编排思想的规则落地），
收盘定次日+**两道稳定性约束**+盘中硬性失效。四个子模块各自独立可测：
- `echelon.py` **梯队地位**（第六维）：涨停股走 `classify_role` 精确判定，非涨停股用题材基准超额
  推导（领涨/同步/滞涨）；地位分 = 角色基础分 × **题材阶段系数**（启动1.05/发酵1.10/高潮0.95/
  分歧0.75/退潮0.55）× 梯队完整度——个股再强，退潮期也要打折
- `regime.py` **炒作阶段**：财报硬日历（1/2/3/4/7/8/10 月）+ 业绩事件密度校验 → 切换六维权重
  （业绩期基本面 25%/情绪 10%；空窗期情绪 25%/基本面 5%/梯队 20%）。**空窗期还按基本面打分
  会系统性错过妖股**
- `gate.py` **空仓闸门**：退潮/冰点、晋级率<30%、炸板率≥35%、跌停≥15 家、接力亏钱 多条件 OR；
  触发则顶部红色横幅逐条列因 + 组合标注「仅观察」并撤除买入范围（记录仍保留以便复盘）
- **稳定性约束**（跨日回放实证，见 docs/picks-replay-baseline.md）：
  · 换股门槛 15 分：防小幅波动换股——但**单靠它不够**（涨停股梯队分 88 vs 非涨停 52，
  分差动辄 30+，门槛形同虚设，实测日均换手仍 57%）
  · **每日换股上限 2 只**：才是真正的稳定器（实测日均换手 100%→37.1%，平均持有 1.0→2.22 天）
  · carryover：昨日成员即使今日未进候选池也兜底重评（防止因"没上热榜"而静默消失）
- `replay.py` + `scripts/replay_picks.py` **跨日回放**：用历史涨停池+K线回放组合轨迹，
  四策略对照（无门槛/仅门槛/无carryover/完整）。**只有梯队与技术两维可回放**
  （消息/情绪/基本面/资金依赖当前快照，无法回填历史）——勿当作完整选股质量回测
- `scripts/replay_sweep.py` **参数敏感性扫描**：一次拉数据多组参数评估（build/evaluate 解耦）。
  60 交易日实测：换股上限 1/2/3/不限 → 日均换手 20%/39.7%/56.3%/68.8%；
  **门槛 10/15/25 分结果完全一致**（分差型门槛在分层候选池下不起作用）。
  **调组合稳定性请先动 MAX_SWAPS_PER_DAY，不要指望门槛**；2 只=容量 40%/日，
  全组合轮换约 2.5 天，匹配 A 股题材 2~5 天周期
- `risk.py` **风险档位与出场纪律**（借鉴 freqtrade：止损/跟踪止盈/ROI 分档，参数按 A 股重设）：
  止损 = max(档位基准, 1.5×ATR%) clamp 3%~12%；每只带失效条件（题材退潮/高度塌陷/跌破均线/事件证伪）
每日自动复盘（走坏原因九类归类，**买点质量单独评估**：区分"选错了"与"选对了但追高"）
+ 周末元结论建议调权（人工确认生效）；参考仓库择优见 `docs/kb/05-repo-tracker.md`
（2026-08-31 按真实 star 分组复核：补入 freqtrade/last30days/Polymarket 三项，修正漏 3 误收 2）；
新题材预判（六维评分+D1 四问验证）；新闻/公告摘要（规则层，表格正文丢弃纪律）；
**事件驱动选股 v1**（EventCard 规则抽取：来源分级/事实与解读/半衰期模板/方向词典+国产替代对冲；
标的池=题材官方成分反查；market 页事件面板 + 详情页相关事件行）。

**联动系统**（`docs/summary/architecture-design.md`）：统一路由 `lib/routing.ts`（URL 唯一真相源）；
`/stock/[symbol]` 中转修复（路径参数 bug）；题材归属 chips ⇄ 题材看板 focus 聚焦（L4/L5）；
板块多日涨幅官方 K 线交叉验证（B3 关闭，实测推断值方向都反）；预警→详情跳转（L6）；
新闻/公告事件点画上 K 线（P1-8：`lib/event-markers.ts` + KlineChartPro「事件」开关，
公告琥珀●/新闻蓝●，复用 digest 数据零新增请求）；
**指数点击详情（L 扩展，2026-08-31）**：指数卡点击 → 右面板展开指数分时/K线（与自选股同交互）；
指数详情 symbol 规范为带前缀形态（`indexDetailSymbol`：裸 000001 是平安银行、上证指数必须
sh000001）；QuoteHub.get_quotes 兜底 indices + 前缀归一化（model_copy），腾讯 _snapshot key
修复（原裸代码 key 使带前缀查询永远 miss）；指数下隐藏 加自选/交易/资料/资金图。

**工程化**：Next 16 升级（flat config）；错误边界；vitest+RTL 组件测试基建（含变异验证纪律）；
`scripts/api-sweep.js` 全端点巡检（载荷体检）；alembic 三态迁移（手写对齐 ORM）；
**研究/核验工具** `app/research/strategy_verify.py`（战法核验器：特征物化 + **同日市场中性基准**
+ 累计漏斗/单条件独立/参数敏感性/环境分层/分年度稳定性/可成交性，配 22 项合成数据单测）
—— 新战法只写条件表达式，**勿再重写窗口 SQL**（见 `docs/kb/03-engineering.md` **KB-ENG-39**）；
`.env.example` 漂移守护测试；同源反代（Route Handler 运行时代理）；
统一缓存层 `app/core/ttl_cache.py`（TTL/LRU 有界/异步单飞/命中率统计，11 处自写缓存收敛，
`/api/system/caches` 可观测——新缓存一律用它，勿再手写 TTL 元组）。

---

## 4. 后续规划（分阶段；明细账本 docs/retro-and-gaps.md，优先级依据 `docs/retro-and-gaps.md` §六 + `docs/plan-registry.md`）

> **接手者看这里**：系统盘点与重构清单（`docs/archive/architecture-redesign.md`）已于 2026-09-01 **全部清零**，
> 导航已收敛为 5 项。**当前剩余待办一律以 `docs/retro-and-gaps.md` §六 为唯一账本**——09-10 已把 §6.2 P1
> **全部清空**（P1-17 当日拍板保留、其余逐项落地），剩下的只有**等时间的阻塞项**：
> P1-22 / P1-23 / P1-30（样本不足）与 P1-24（分钟决策库需前瞻积累约 3 个月）；§6.3 P2 全为触发式，**维持观察、不主动做**。
> **继续推进须等用户明确指令**（工作模式，用户 2026-08-31 定）；动手前先读 §6。
> **红线**：涉及风控/资金口径变更、删除数据或文件、凭据类动作，一律先经用户确认。

### 阶段 E · 系统重构（✅ 全部完成 2026-09-01，记录见 `docs/archive/architecture-redesign.md` §五）
P0 K线多源冗余+熔断+回放限流 · P1 事件采集调度+角色胜率分布 · P1/P2 页面合并（/tape 四合一、
云图入市场、/research 折叠、自选入工作台，导航 13→5）· P2 screener 冻结+分钟信号删除 ·
P2 基本面 ROE/毛利率 · P3 skills 归档。

### 阶段 A · P0（✅ 全部完成）
1. ~~**sentiment 历史分位校准**~~ ✅ **已完成（2026-09-02 落地，2026-09-10 进一步分位化）**。
   **注意实际走的路与当时的设想不同**：不是用 Parquet 快照算，而是建了**指标历史库**
   `app/sentiment/metric_history.py`（ths 涨停池/炸板池回补，窗口 247 交易日）+
   `app/sentiment/calibration.py` 等分位切档；闸门层进一步按分位判并写明所用口径（KB-DEC-015）。
   **数据底座受数据健康哨兵保护**（该文件曾静默停更 6 个交易日，见 KB-ENG-33）。
2. ~~**统一 provider 缓存层**（P0-5 / 数据源 C3）~~ ✅ 已完成（2026-08-31）：`app/core/ttl_cache.py`
   （TTLCache：monotonic/LRU 有界/异步单飞/命中统计 + 弱引用注册表）+ `GET /api/system/caches` 观测；
   11 处自写缓存收敛。

### 阶段 B · 等用户触发（外部条件成熟即做）
| 项 | 触发条件 | 状态 |
|---|---|---|
| ~~推送通道接入~~ | — | ✅ **已完成**：飞书 webhook 落地（盘中只保留买点卡，2026-09-08 定稿） |
| ~~LLM 接入~~ | — | ✅ **已完成**：`claude -p` → GLM-5.3 网关 + `events/llm_aux.py`（pending 事件二次判定） |
| 生产部署（Docker/编排/监控） | 用户定环境 | 🟡 镜像与编排已交付（09-04），本机无 Docker 待用户侧实测 |
| 事件复盘回写（E4：T+N 胜率回写事件权重） | 上线运行积累数据后 | ❌ 未做（远期：需先有 T+N 事件样本积累，属事件因子闭环） |

### 阶段 C · P1 功能项（✅ 全部完成 2026-08-31）
1. ~~**B1 热股榜**~~ ✅ 2. ~~**B4 seal_nextday 交叉验证晋级率**~~ ✅ 3. ~~**新闻/公告事件点画上 K 线**~~ ✅ 4. ~~**题材指数与板块内资金合力**~~ ✅（`GET /api/themes/catalog/strength` 合力聚合 + `GET /api/themes/catalog/index` 官方指数日 K + 卡片合力条；归属=官方成分反查，行情=腾讯批量快照）

### 阶段 D · P2 远期/触发式（维持观察）
marketdb DuckDB 日级底座 · qlib 因子挖掘 · L2 盘口（无免费源） · 逐笔历史+主动买卖比 ·
题材事件树/生命周期 · 营业部图谱/筹码/解禁/两融/大宗 · MCP 工具层封装（API 契约已就绪） ·
~~切片 E 跳转（热力图/回测/总览→详情）~~ ✅ 2026-09-03 完成（themesUrl 构造器 + 三处接线 + agent-browser 端到端实测）。

### 明确不做（防复发）
C2 全市场日 K dump（已被 TDX 替代）；"等 LLM 再做摘要"（规则先行范式）；next.config rewrites 反代（已被 Route Handler 替代）。

---

## 5. 文档地图（核对过的事实源）

| 文档 | 内容 / 地位 |
|---|---|
| **docs/INDEX.md** | **文档总入口**（所有文档索引 + 使用地图） |
| **docs/PROJECT-MASTER.md** | 技术总览：目录逐文件/数据源口径/API/阶段状态表 |
| **docs/retro-and-gaps.md** | **唯一待办账本**——§一~§三 历史盘点 / §四 行为基线勿回退 / **§六 全量待办总账（P0/P1/P2）** / §七 偏差更正 / §八 计划文档处置 |
| **docs/summary/** | 主题汇总 6 份（stock-strategy / factor-system / data-market / architecture-design / ai-evolution / review-governance）——**已完成方案的精华收敛处** |
| **docs/kb/** | 权威知识库（KB-STOCK/TRADE/ENG/DEC + 00-INDEX，**全序列唯一登记处**）；**工程教训 KB-ENG 按子类分四册**（09-12 按 §5.2 条件① 拆出，引用只写 `[[KB-ENG-NN]]` 即可，查条目不必知道册名）：`03-engineering.md` = **应用与设计层**（架构/接口/口径判据/前端/方法论）/ `09-verification-pitfalls.md` = **验证层**（测试·门禁·CI·防线有效性）/ `10-data-contract-pitfalls.md` = **数据契约层**（写入·去重·传输·时间·质量门）/ `08-tooling-pitfalls.md` = 工具与环境陷阱速查（KB-ENG-01~15 操作类短条目）；`07-doc-curation.md` = 文档治理规范（v1.6：§3.2 完成即沉淀删件、`📎 示例` 状态、`scripts/doc-health.py` 一键体检）。**示例/题材案例一律标 `📎`，不得与 `✅ 已落地` 混用** |
| docs/plan-registry.md | 历史计划去向表 + 文档处理规范（**不再新建计划文档**） |
| docs/api.md | API 契约（端点数以 /openapi.json 为权威，文档按域分节） |
| docs/data-sources.md + data-source-comparison.md | 字段口径实测记录 + 四源能力选型（改 Provider 前必读） |
| docs/sentiment.md + theme-sentiment-methodology.md + theme-prediction.md | 情绪口径 / 题材情绪方法论 / 新题材预判 |
| docs/review-agent.md + daily-review-sop.md + daily-review-checklist.md | 复盘 Agent 架构 / 复盘 SOP / 执行清单 |
| docs/backtest-rules.md / risk-management.md | 回测代码级禁令（做回测前必读） / 风控红线 |
| docs/deployment.md / websocket.md / mcp.md / architecture.md / data-dictionary.md | 部署+环境变量全表 / WS 契约 / MCP 清单 / 架构 / 数据字典 |
| docs/live-trading-guosen-plan.md | 实盘接入蓝图（⚫ 搁置，等用户恢复） |
| docs/daily-review/ · evolution/ · repo-watch/ | 逐日复盘 / 进化议程日志 / 仓库跟踪周报 |

**账本约定**：待办明细以 `docs/retro-and-gaps.md` §六 为**唯一账本**（已完成方案文档即删，精华进 `docs/summary/`）；
优先级看总账 P0/P1/P2；README/PROJECT-MASTER 只留阶段级索引。**完成一项划一项并 git checkpoint。**

---

## 6. 工作方式（前任验证过的教训，勿重蹈覆辙）

### 6.1 交付纪律
- **验收以实际看到的为准**：UI 改动用 `agent-browser snapshot`（无障碍树文本）+ `eval` 直读 DOM 验收；
  当前模型读不了 PNG，截图拍了无法目视。布局类问题如实说明"需人工目视"。
- **每阶段流程**：实测数据源（curl 先行）→ 小切片实现 → 全量门禁 → 浏览器文本验收 → commit/push → CI 自查 → 文档同步 → 记忆。
- **改完后端必须重启验证 8000 上的实例**（无 --reload 时改完不重启=旧代码）；起服务必须用
  `run_in_background`，bash 子 shell `( &)` 会被沙箱收割（踩过 3 次）。
- 禁止 dev server 运行时 `next build`；pytest/build 需要 `CODEBUDDY_SAFE_DELETE_ENABLED=0`（沙箱批量删除保护）；
  **trade_calendar.json 已是未跟踪运行态**（gitignored，git checkout 对它无效）：测试已隔离、不再改写它
  （2026-09-12 实测 45 项 calendar 测试前后文件无变化），健康判据 = `source:"official"` 且 days ≥ 240；
  重启后端会合法重写该文件的 fetched_at，diff 只有时间戳属正常。
- **测试隔离是"库隔离了、文件没隔离"**：`tests/conftest.py` 把库设成 `sqlite:///:memory:`，
  所以测试改不到生产数据行；但凡写盘的目录（如 `app.review.storage.REPORT_DIR`）必须一并指向临时目录，
  否则测试垃圾会落进 `data/review/reports/`，且按 trade_date 删文件的清理逻辑会误删真实报告
  （2026-09-01：测试把 `20990101.json` 留在生产目录，差点删掉 `20260901.json`）。
  同理，`_cleanup(sf, td)` 只接受 `2099*` 开头——`review_reports.trade_date` **没有唯一约束**
  （只有 `review_id` 唯一），同一天可并存多行，按日期删会连真实报告一起删。
- **进程内缓存一律用 `app/core/ttl_cache.py` 的 TTLCache**（`cache_on(holder, name, ttl, maxsize)`），
  勿再手写 `(time.time(), payload)` 元组；键空间必须有界；命中响应标注 cached 用
  `model_copy(update={...})`，不变异共享缓存对象。
- 复杂 JSX 整文件重写；长内容写脚本文件；**文档/代码编辑一律用 Edit/Write 工具**（node -e 撞 shell 引号已翻车 3+ 次）。
- `apps/web/tsconfig.tsbuildinfo` 已 gitignore（tsc --noEmit 会改它，不入库）。

### 6.2 接新数据源五步法（见 docs/data-sources.md）
curl 先行 → 记录字段口径与类型陷阱 → 多采样找规律 → fixture 从实抓数据生成 → 写进文档。
**缩放陷阱实例**：东财涨停池价格 ×100、炸板池 ×1000——用自家 TDX 日 K 交叉验证。
**官方文档类型描述不可全信（2026-08-31 再证）**：`seal_nextday` 文档写 string 实为布尔；
热股榜 `heat` 是字符串数字 "6002184"；`sign_level` 文档 string 实为恒 0 整数；
`boards.*`"最多 4 只"实为无上限。凡响应字段，以 curl 实抓为准并写进 provider docstring。

### 6.3 工程教训（2026-08-30/31 两轮密集迭代 + 同日五次交付沉淀）
- **Next.js**：`[symbol]` 路径参数在 page 里是 `params`（Promise），`searchParams` 是查询参数——
  两者不匹配**不报错只静默丢参**（跨页面联动 bug 根因）；`NEXT_PUBLIC_*` 构建期内联；
  `rewrites()` 构建期求值，运行时代理必须用 Route Handler。
- **React/vitest**：未开 globals 时 RTL 自动 cleanup 不注册（症状：单跑过全量红）；防抖组件断言等真变的值；
  回归测试要做**变异验证**（临时改回 bug 版本确认测试变红）。
- **monkeypatch 时钟会冻结事件循环**：给 TTL 缓存做可控时钟时 `setattr(tc.time, "monotonic", fake)`
  打的是 stdlib time 模块本身（事件循环 `loop.time()` 同源）→ `asyncio.sleep` 永不触发、测试死锁。
  模块内 `from time import monotonic` 后补丁打**模块级名字**（`tc.monotonic`）才安全（P0-5 踩过）。
- **接功能前先盘点已有能力**：B1 的 ths provider 方法早已存在且完备，缺的只是消费端；
  详情面板已拉 digest 就不用为 K 线事件点加新请求。先 grep 再设计。
- **fake 桩必须复刻真实契约**：回归测试里桩若容忍非法输入（如 `date_ms(None)` 的崩法），
  测试锁不住 bug——桩收到该崩的输入就该抛同样的错。
- **lightweight-charts 多套 marker 必须合并后排序、一次 setMarkers**（时间升序）；
  分开调用会互相覆盖。画在 canvas 上的东西 a11y 文本快照看不见：能验的是开关存在/
  无错误横幅/canvas 数量，视觉如实转人工目视。
- **SQLAlchemy**：`query.delete()` 绕过 ORM 级联留孤儿行；删有关联对象走 `session.delete(obj)`；
  sessionmaker 不支持 with 语法（用 `sf()` 返回的 Session）。
- **pytest**：共享内存库跨文件污染——测试用独立代码/变体标题，**断言锁成员关系不锁全等**
  （`assert "题材" in themes` 而非 `themes == [...]`，单跑过全量炸的常见根因）；
  `logging.basicConfig` 会破坏后续 caplog（审计断言打桩 logger）；
  窗口/序列类数据测试要构造完整邻接关系（如天梯的"次日锚行"）；
  全量 pytest 偶发卡在线程锁等待（与 uvicorn/dev server 并发抢资源的环境抖动）——
  杀掉重跑再判断，勿直接改代码。
- **排查**：页面 `performance.getEntriesByType('resource')` 看真实请求 URL；
  bash grep 在沙箱不可靠——查代码用 Grep 工具或 node -e。
- **轮询/刷新类验收**：resource buffer 默认 250 条会**静默溢出**（计数停滞假象）——
  先 `performance.clearResourceTimings()` 再测间隔；headless 页面挂 5 分钟后进入
  intensive throttling，长间隔轮询计数偏低属环境行为，以"清 buffer 后短窗计数"为准。
- **词表/规则类功能**：漏词是常态，靠真实数据发现并补测试；错误归类不要信 catch-all（Parquet 损坏曾被误报为"TDX 源不可用"）。

### 6.4 行为基线（勿回退）
红涨绿跌 · tabular-nums · 所有数据带来源/时间/质量标注 · mock 不冒充实盘 ·
布局锁一屏（容器内滚动）· 每处可解释输出带 basis · 右列宽度用户可调（260-480px）。

---

## 7. 待用户决策（阻塞项，勿催促，列清单等待）

> 已解决不再列：~~推送通道~~ ✅ 飞书 webhook（09-08 定稿，盘中只留买点卡）、~~LLM 凭据~~ ✅ `claude -p` → GLM-5.3。
> **本节只列「需要人拍板」的**；样本不足类阻塞（P1-22/23/30、P1-24）不进这里，它们等时间不等人。

| # | 决策 | 影响面 |
|---|---|---|
| 1 | 部署环境（NAS / 云服务器 / Vercel+Railway） | Docker/编排/监控；需有 Docker 的环境实测 |
| 2 | 飞书已收敛为「盘中只保留买点卡」，4 个定时 automation（09:26/14:40/15:35/15:40）**是否也一并停** | 减少打扰 vs 保留兜底（2026-09-08 记录，未决） |

> **已决（留痕，勿重开）**：控制台「自定义规则 UI」→ **2026-09-10 拍板保留**（成本近零，且是全系统唯一能写自定义阈值提醒的入口；
> 位置：`/agent?tab=alerts`「提醒与告警」，链路 = `POST /api/alerts/rules` → `AlertEngine` 轮询 → `alert_triage` → 悬浮球/in_app。
> 当前用户自建规则 0 条，库内 4 条全是 `__` 前缀系统规则——是**入口深 + 无需求**，不是功能缺失）。详见账本 P1-17。

> `data/parquet/snapshots/20260830/` 下 7 个损坏文件**不占决策位**：读取已容错、新快照会自动覆盖；
> 若要清理，按删除纪律走 `scripts/safe-trash.sh`（进项目回收站，可 `--restore`）即可，不必问。

---

## 8. 关键常识

- Provider 链 `ths→tencent→eastmoney→sina` 逐方法 failover；加数据源 = 实现协议 + 注册 factory + 加链；
  特殊数据直取特定 Provider（`_pick_provider`），避免 composite 串行重试拖垮事件循环
- 质量五级：high/medium/low/stale/invalid；low 及以下 AI 禁用、回测禁用、前端强制标识
- 交易撮合 `app/paper/engine.py`；风控引擎 `app/risk/`（状态分类→参数→预检）；
  事件引擎 `app/events/`（抽取/存储/标的池）；题材目录 `app/services/theme_catalog_service.py`；
  统一缓存 `app/core/ttl_cache.py`（命中率/逐出经 `GET /api/system/caches` 观测）
- fuyao 官方端点：题材目录（cn_concept 390 个）/成分/板块 K 线/涨停池/连板天梯/热股榜，
  文档在 `skills/hithink-finance/docs/api/*.md`，key 在 settings.ths_api_key——**调接口前先读对应 md，
  但字段以 curl 实抓为准**
- 东财 push2 本机被 WAF 限流：行情走腾讯，特殊数据走 datacenter/push2ex；
  涨停池价格 ×100、炸板池 ×1000（缩放已用 TDX 交叉验证）
- 非交易日/盘前语义：当日涨停池为空、归因空是正确语义（`?date=` 回看历史）；
  天梯矩阵最近交易日 `seal_nextday` 全 null 是正常语义（无次日参考）
- Parquet 快照每 5 分钟落 **项目根** `data/parquet/snapshots/`（按日分目录，选股器/情绪地基），
  写入必须走 `parquet_store.write_parquet_atomic`；backend/data 下没有快照
