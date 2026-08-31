# AGENTS.md — AI 开发者交接手册（必读，2026-08-31 全量重写，同日五次交付后更新至 commit ca15f74）

你接手的是 **AShare AI Trader**：A 股实时行情 + 量化投研 + 模拟交易 + 事件驱动选股的一体化工作台。
本文件是你的作业手册：现状、待办、阶段安排、工作纪律全在这里。
**动手前先读完本文件，再按需查 `docs/PROJECT-MASTER.md`（技术总览）与 `docs/plan-review.md`（计划与优先级）。**

---

## 0. 红线（违反即事故）

1. **禁止**连接真实券商 / 自动真实下单。系统只有模拟交易（`/api/paper/*`）。
2. **禁止**把 mock 数据、过期缓存冒充实盘。数据源失败 → 标 `stale` + health=degraded。
3. **禁止**输出确定性买卖结论（必涨/稳赚）。一切结论 = 偏向 + 依据 + 失效条件；
   事件标的池等"机会输出"必须带「不构成买卖建议」声明。
4. **API Key 只存 `backend/.env`**（已 gitignored），绝不入库/入前端/入文档。
5. 撮合规则（T+1/涨跌停拒/整手/费用/停牌拒）是硬拦截，不可绕过。
6. **新增页面/板块需先论证**：默认通过复用、扩展、联动实现需求（联动设计原则，见 docs/linkage-design.md §0）。

---

## 1. 快速启动

```bash
# 后端（Python 3.11，venv 已建好；.env 含 THS key / 新闻 LLM 留空占位）
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# 前端（node_modules 已装）
cd apps/web && npm run dev                        # http://localhost:3000/workbench

# 测试与门禁（每次改动全部跑，全绿才算完；当前基线：后端 456 / 前端 56）
cd backend && .venv/bin/pytest                    # 456 用例
cd apps/web && npx tsc --noEmit                   # 类型 0 错误
cd apps/web && npx eslint .                       # 0 error（24 warn 是挂账项，见 eslint.config.mjs 注释）
cd apps/web && CODEBUDDY_SAFE_DELETE_ENABLED=0 npx vitest run   # 56 用例
cd backend && .venv/bin/python -m pyflakes app tests            # 0
# 生产构建前必须先停 dev server（.next 冲突已踩两次）：
lsof -ti tcp:3000 | xargs kill -9; cd apps/web && CODEBUDDY_SAFE_DELETE_ENABLED=0 npx next build
```

CI（GitHub Actions）：后端 pytest+pyflakes、前端 tsc+eslint+vitest+build。推送后**自查 CI**
（`source ~/.zshenv` 拿 GITHUB_TOKEN → `/actions/runs?head_sha=<完整SHA>` → jobs → logs），绝不问用户。

## 2. 当前状态快照（2026-08-31，实时行情修复后更新）

**535 后端测试 + 83 前端测试全绿 · 96 REST + 1 WS 端点 · 四源链 `ths→tencent→eastmoney→sina`**

本日已完成：P0-5 统一缓存层（`3315c3f`）→ reconciliation 502 修复（`fb833f1`）→
B1 题材人气（`5abeabd`）→ B4 晋级率源自证（`f0d10b0`）→ P1-8 K线事件点（`ca15f74`）→
实时行情修复（K线/分时/盘口/逐笔盘中刷新 + 指数点击详情）+ 指数详情页改造（分时Y轴修复/名称显示/涨速榜/板块涨幅 tab）。
**P0 只剩 P0-3b（等 Parquet 快照积累）；阶段 C 只剩题材指数。**

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
| 9 联动系统（跨页面选中标的统一路由 / 题材⇄个股双向联动 / 官方 K 线交叉验证 / 事件面板） | 🔶 核心闭环全部打通；余切片 E 跳转（P2） |

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
收盘定次日+换股门槛 15 分+盘中硬性失效。四个子模块各自独立可测：
- `echelon.py` **梯队地位**（第六维）：涨停股走 `classify_role` 精确判定，非涨停股用题材基准超额
  推导（领涨/同步/滞涨）；地位分 = 角色基础分 × **题材阶段系数**（启动1.05/发酵1.10/高潮0.95/
  分歧0.75/退潮0.55）× 梯队完整度——个股再强，退潮期也要打折
- `regime.py` **炒作阶段**：财报硬日历（1/2/3/4/7/8/10 月）+ 业绩事件密度校验 → 切换六维权重
  （业绩期基本面 25%/情绪 10%；空窗期情绪 25%/基本面 5%/梯队 20%）。**空窗期还按基本面打分
  会系统性错过妖股**
- `gate.py` **空仓闸门**：退潮/冰点、晋级率<30%、炸板率≥35%、跌停≥15 家、接力亏钱 多条件 OR；
  触发则顶部红色横幅逐条列因 + 组合标注「仅观察」并撤除买入范围（记录仍保留以便复盘）
- `risk.py` **风险档位与出场纪律**（借鉴 freqtrade：止损/跟踪止盈/ROI 分档，参数按 A 股重设）：
  止损 = max(档位基准, 1.5×ATR%) clamp 3%~12%；每只带失效条件（题材退潮/高度塌陷/跌破均线/事件证伪）
每日自动复盘（走坏原因九类归类，**买点质量单独评估**：区分"选错了"与"选对了但追高"）
+ 周末元结论建议调权（人工确认生效）；参考仓库择优见 docs/github-stars-trading-analysis.md
（2026-08-31 按真实 star 分组复核：补入 freqtrade/last30days/Polymarket 三项，修正漏 3 误收 2）；
新题材预判（六维评分+D1 四问验证）；新闻/公告摘要（规则层，表格正文丢弃纪律）；
**事件驱动选股 v1**（EventCard 规则抽取：来源分级/事实与解读/半衰期模板/方向词典+国产替代对冲；
标的池=题材官方成分反查；market 页事件面板 + 详情页相关事件行）。

**联动系统**（docs/linkage-design.md）：统一路由 `lib/routing.ts`（URL 唯一真相源）；
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
`.env.example` 漂移守护测试；同源反代（Route Handler 运行时代理）；
统一缓存层 `app/core/ttl_cache.py`（TTL/LRU 有界/异步单飞/命中率统计，11 处自写缓存收敛，
`/api/system/caches` 可观测——新缓存一律用它，勿再手写 TTL 元组）。

---

## 4. 后续规划（分阶段；明细账本 docs/retro-and-gaps.md，优先级依据 docs/plan-review.md）

> **接手者看这里**：无阻塞待办只剩阶段 C 第 4 项（题材指数）。其余全部在等外部触发
> （阶段 B 的用户决策）或等数据（P0-3b）。**继续推进须等用户明确指令**（工作模式，
> 用户 2026-08-31 定）；用户说"继续"时按阶段 C 剩余项顺序做，动手前先读 §6。

### 阶段 A · P0（只剩一项，等数据）
1. **sentiment 历史分位校准**（P0-3 残留；阈值配置化 ✅ 已完成 2026-08-31：`band_config.py` + `ASHARE_SENTIMENT_HEAT_BANDS_JSON`/`ASHARE_SENTIMENT_EARNING_BANDS_JSON` 覆盖，非法配置启动即失败）：等 Parquet 快照积累后用本地数据算分位，替换照搬网络的阈值。快照目录在**项目根** `data/parquet/snapshots/`（按日分目录），2026-08-31 时只有 2 个交易日样本，需数十个交易日。
   来源：docs/sentiment-phase-review.md P2 #13。
2. ~~**统一 provider 缓存层**（P0-5 / 数据源 C3）~~ ✅ 已完成（2026-08-31）：`app/core/ttl_cache.py`
   （TTLCache：monotonic/LRU 有界/异步单飞/命中统计 + 弱引用注册表）+ `GET /api/system/caches` 观测；
   11 处自写缓存收敛，端点 79→80。

### 阶段 B · 等用户触发（外部条件成熟即做）
| 项 | 触发条件 | 一举关闭 |
|---|---|---|
| 推送通道接入（email/企微/飞书/TG） | 用户选通道 | Phase 8 收尾 + 复盘推送 + 盘中情绪监控 |
| LLM 接入（LLMAnalyzer + 新闻摘要增强 + 事件方向 LLM 分类） | 用户给凭据 | 复盘四角色编排 + 事件 E3 |
| 生产部署（Docker/编排/监控） | 用户定环境 | Phase 8 全收尾；需有 Docker 的环境实测 |
| 事件复盘回写（E4：T+N 胜率回写事件权重） | 上线运行积累数据后 | 事件模板自校准 |

### 阶段 C · P1 功能项（✅ 全部完成 2026-08-31）
1. ~~**B1 热股榜**~~ ✅ 2. ~~**B4 seal_nextday 交叉验证晋级率**~~ ✅ 3. ~~**新闻/公告事件点画上 K 线**~~ ✅ 4. ~~**题材指数与板块内资金合力**~~ ✅（`GET /api/themes/catalog/strength` 合力聚合 + `GET /api/themes/catalog/index` 官方指数日 K + 卡片合力条；归属=官方成分反查，行情=腾讯批量快照）

### 阶段 D · P2 远期/触发式（维持观察）
marketdb DuckDB 日级底座 · qlib 因子挖掘 · L2 盘口（无免费源） · 逐笔历史+主动买卖比 ·
题材事件树/生命周期 · 营业部图谱/筹码/解禁/两融/大宗 · MCP 工具层封装（API 契约已就绪） ·
切片 E 跳转（热力图/回测/总览→详情） · 组件渲染测试按需增补。

### 明确不做（防复发）
C2 全市场日 K dump（已被 TDX 替代）；"等 LLM 再做摘要"（规则先行范式）；next.config rewrites 反代（已被 Route Handler 替代）。

---

## 5. 文档地图（核对过的事实源）

| 文档 | 内容 / 地位 |
|---|---|
| **docs/PROJECT-MASTER.md** | 技术总览：目录逐文件/数据源口径/82 API/阶段状态表 |
| **docs/plan-review.md** | 计划复盘：10 份方案逐项盘点 + P0/P1/P2 整合清单（§六）+ 遗留用户决策（§八） |
| **docs/linkage-design.md** | 联动系统总纲：状态管理规范/路由规范/联动矩阵 L1-L10/题材三层归属/事件 SOP；切片标记在此 |
| **docs/retro-and-gaps.md** | 唯一明细账本（§一功能欠缺 20 项全清 / §二布局 / §三技术债 / §四行为基线勿回退） |
| docs/api.md | API 契约（96 端点，按域分节） |
| docs/data-sources.md + data-source-comparison.md | 字段口径实测记录 + 四源能力选型（改 Provider 前必读） |
| docs/sentiment-phase-review.md + sentiment.md | 情绪方法论调研 + 误判复盘 + 优化清单 |
| docs/theme-prediction.md / review-agent.md / theme-sentiment-methodology.md | 预判 / 复盘 Agent / 题材情绪方法论 |
| docs/backtest-rules.md | 回测代码级禁令（做回测前必读） |
| docs/deployment.md | 部署 + 环境变量全表 + 已踩坑 |
| docs/github-stars-trading-analysis.md / mcp.md / orderbook-source-evaluation.md / minute-chart-plan.md | 星标方案 / MCP 规划 / 盘口评估 / 分时图方案（均已完成或触发式） |
| docs/architecture.md / websocket.md / risk-management.md / longhu.md / data-dictionary.md | 架构 / WS 契约 / 风控红线 / 龙虎榜口径 / 数据字典 |

**账本约定**：待办明细以 retro-and-gaps 为唯一账本；跨计划优先级看 plan-review §六；
联动需求看 linkage-design；README/PROJECT-MASTER 只留阶段级索引。**完成一项划一项并 git checkpoint。**

---

## 6. 工作方式（前任验证过的教训，勿重蹈覆辙）

### 6.1 交付纪律
- **验收以实际看到的为准**：UI 改动用 `agent-browser snapshot`（无障碍树文本）+ `eval` 直读 DOM 验收；
  当前模型读不了 PNG，截图拍了无法目视。布局类问题如实说明"需人工目视"。
- **每阶段流程**：实测数据源（curl 先行）→ 小切片实现 → 全量门禁 → 浏览器文本验收 → commit/push → CI 自查 → 文档同步 → 记忆。
- **改完后端必须重启验证 8000 上的实例**（无 --reload 时改完不重启=旧代码）；起服务必须用
  `run_in_background`，bash 子 shell `( &)` 会被沙箱收割（踩过 3 次）。
- 禁止 dev server 运行时 `next build`；pytest/build 需要 `CODEBUDDY_SAFE_DELETE_ENABLED=0`（沙箱批量删除保护）；
  **每次跑完 pytest 必须 `git checkout -- backend/data/trade_calendar.json`**（测试会把真实全年官方日历
  覆盖成 5 天残片；另注意重启后端会合法重写该文件的 fetched_at，diff 只有时间戳属正常）。
- **进程内缓存一律用 `app/core/ttl_cache.py` 的 TTLCache**（`cache_on(holder, name, ttl, maxsize)`），
  勿再手写 `(time.time(), payload)` 元组；键空间必须有界；命中响应标注 cached 用
  `model_copy(update={...})`，不变异共享缓存对象。
- 复杂 JSX 整文件重写；长内容写脚本文件；**文档/代码编辑一律用 Edit/Write 工具**（node -e 撞 shell 引号已翻车 3+ 次）。
- 提交前还原 `apps/web/tsconfig.tsbuildinfo`（tsc --noEmit 会改它，且它被 git 跟踪）。

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

| # | 决策 | 阻塞的联动项 |
|---|---|---|
| 1 | 推送通道（email/企微/飞书/TG/webhook） | 预警真实推送 + 复盘推送 + 盘中情绪监控 |
| 2 | LLM 凭据 | 复盘 LLM 编排 + 摘要增强 + 事件方向 LLM 分类（E3） |
| 3 | 部署环境（NAS/云服务器/Vercel+Railway） | Docker/编排/监控；需有 Docker 的环境实测 |
| 4 | 是否物理删除 `data/parquet/snapshots/20260830/` 下 7 个损坏文件 | 无（读取已容错，新快照自动覆盖） |

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
