# 数据源说明

> **速览（30 秒）**：本文 = **数据源接入策略**（主源 → 备源 → 降级链），不是选型依据也不是实测对比。
> 核心约束：失败必须**可见**（标 `stale` + health=degraded，绝不伪装实时）；**同名字段异源异单位不可混算**；
> 降级口径必须随结论一起给（如 push2delay 的 15min 延迟）。
> **导航**：§0 接入策略 → §1–§6 逐源能力与边界（腾讯 / 新浪 / 东财 / 同花顺 / Mock / 社区参考）→ §7 入库必带字段 → **§8 全量清单与使用度审计（2026-09-16）**。
> ⚠️ **本节导航曾失真（2026-09-16 更正）**：原文写「§2 口径差异 → §3 降级与告警」，而正文 §2 = 新浪、§3 = 东方财富、§4 = 同花顺、§5 = Mock、§6 = 社区参考，**从未有过这两节** —— 属 `doc-health` 查不出的「章节指针失效」（与 `AGENTS.md` 红线 6 引 `architecture-design.md §0` 同族）。**引用章节前先确认锚点存在**。
> 改数据源前**先读** `data-source-comparison.md`（实测对比），改完回填。
> **同族文档（数据源族，2026-09-10 归口）**：选型依据 → `data-source-comparison.md`（四源实测对比）；专项评估 → `archive/orderbook-source-evaluation.md`（五档盘口，结论：ths 无五档）；**外部情报（⚠️ 零本机实测）** → `../archive/external-data-source-survey-2026-09-11.md`（付费源调研：四家均无 L2、两家与我们同源；未决建议见 `retro-and-gaps.md` §六 **P2-31**）。

## 0. 接入策略（§2.2 主源→备源→降级）

`ASHARE_DATA_PROVIDER` 主源 + `ASHARE_PROVIDER_FALLBACKS` 备源，由 `CompositeProvider` 逐方法自动切换：

```text
行情/K线/盘口/搜索: 腾讯 → 东财（search suggest / K线）
涨停池 / 龙虎榜:    东财 push2ex / datacenter（稳定，独立主机）
逐笔成交:           链上东财 details（push2his，本机被 WAF 拦 ⇒ 恒 502）
                    → 路由层降级 TDX 直连逐笔（app/market/tdx_tick.py，IMP-038）
默认链:             主源 ths + 备源 tencent, eastmoney, sina（core/config.py；
                    2026-09-07 修正——ths 官方 API 为主源，sina 已入 1Hz
                    实时对冲组 realtime_rank=1；「chain(tencent→eastmoney)」
                    为 2026-08 的旧链路，已被替换）
```

- 全部 Provider 客户端 `trust_env=False`：行情源为国内站直连，**不走用户系统代理**（SOCKS 代理会导致 httpx 启动失败或绕道境外）。
- 切换日志写入 `CompositeProvider.switch_log`，日志输出 `provider switched`。
- 全链失败 → QuoteHub 标记 `stale`，health 报 `degraded`，**不伪造实时数据**。

## 1. 腾讯（主源，实测 2026-08-28，Level-1 快照 3 秒级）

| 用途 | 端点 | 口径 |
|---|---|---|
| 实时快照/五档 | `qt.gtimg.cn/q=sh600519,...` (GBK, `~` 分隔) | 字段(0起): 1名 3现价 4昨收 5开 6量(手) **9-18买五档价量 19-28卖五档价量** 30时间(北京) 31涨跌 32涨跌% 33高 34低 36量(手) 37额(**万**) 38换手% |
| 日/周 K 线 | `web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600519,day,,,320,qfq` | `data.sh600519.qfqday` 行=`[日期,开,收,高,低,量(手)]`，qfq=前复权 |
| 分钟 K 线 | `ifzq.gtimg.cn/appstock/app/kline/mkline?param=sh600519,m5,,320` | `[YYYYMMDDHHMM,开,收,高,低,量]`，m1~m60 |
| 当日分时 | `web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh600519` | 行=`HHMM 价格 量(手) 累计额(元)`，1 分钟粒度 → `/api/minute-line/{symbol}` |
| 搜索 | `smartbox.gtimg.cn/s3/?v=2&q=...&t=all` (GBK) | `^`分组 `~`字段：名称/代码(sh600519) |

单位：成交量手（×100 转股），成交额万（×1e4 转元）——已用茅台盘后数据双重验证
（16126 手×100 ≈ 20.86 亿÷1297.40 ✓；指数 9703.65 亿与东财 f6 一致 ✓）。
时间戳为北京时间，Normalizer 统一转 UTC。

## 2. 新浪（备源）

`hq.sinajs.cn/list=sh600519,...`（GBK，必须带 `Referer: https://finance.sina.com.cn/`）。
字段：0名 1开 2昨收 3现价 4高 5低 6买一价 7卖一价 8量(**股**) 9额(元) 10-19买五档 20-29卖五档 30日期 31时间。
提供快照 + 五档盘口；K 线未实现（由腾讯/东财兜底）。

## 3. 东方财富（专项源）

- **涨停池** `push2ex.eastmoney.com/getTopicZTPool`：p=价格×100、fbt/lbt=HHMMSS、fund=封单额(元)、zbc=炸板、lbc=连板、zttj={days,ct}（"7天7板"）。实测 2026-08-28：82 只。
- **炸板池** `push2ex.eastmoney.com/getTopicZBPool`（同 ut/dpt 参数）：实测 2026-08-28：16 只。炸板率 = 炸板 ÷（涨停＋炸板）。
- **板块列表** `push2delay.eastmoney.com/api/qt/clist/get`：本机 `push2` 主域被 WAF 拦截，只有 `push2delay` 延迟域可用。单页上限 **100 条**（即使传 `pz=600` 也只回 100），必须按 `total` 分页。板块名体系与 ths 涨停原因标签不同（如 ths「黄金珠宝」↔ 东财「黄金概念」），精确匹配命中率极低，需做剥离后缀 + 双向包含模糊匹配。
- **龙虎榜** `datacenter-web.eastmoney.com/api/data/v1/get` (RPT_DAILYBILLBOARD_DETAILSNEW)：SECURITY_CODE / BILLBOARD_* / **EXPLAIN**(上榜原因)。
- 行情族 `push2/push2his`：本机直连与经代理均被 WAF 拦（空回复，疑似共享出口 IP 风控），保留为链上 search/K线/逐笔的备源；家庭宽带通常可用。
- `ulist` 与 `stock/get` 字段编号**不一致**，不可混用映射表。

### 3.1 ⚠️ 涨停池/炸板池：非交易日会**静默回退**到最近交易日（2026-08-29 实测）

传入非交易日（周末、节假日）或未来日期时，接口**不报错、不返回空、不返回日期字段**，
而是直接返回**最近一个交易日**的数据：

```
date=20260829（周六）→ 82 条，首条 000712 锦龙股份 3板
date=20260828（周五）→ 82 条，首条 000712 锦龙股份 3板   ← 完全相同的响应
date=20260827（周四）→ 77 条
```

响应**信封**里其实有 `data.qdate` 字段（2026-08-29 复核更正：正文确实没有日期字段，
但信封有）。**但它不能用来校验日期**——它是"最近交易日"，不是你所请求的那天：

```
GET ...date=20260828 → "tc":82, "qdate":20260828, 首条 000712 锦龙股份
GET ...date=20260827 → "tc":77, "qdate":20260828, 首条 002855 捷荣技术
                                        ↑ 两次都是 20260828
```

数据本身是对的（82 / 77 只与下方实测记录一致），**只有 `qdate` 是常量**。
所以"不能用响应侧判别日期是否串了"这个结论依然成立，但理由要改成
"**字段存在但不可信**"——否则后来者一旦"发现"这个字段，很可能误以为可以拿它做校验，
从而重新引入自指计算事故。

**已造成的事故**：`/api/market/sentiment` 用 `date.today()` 和 `today-1` 分别取池，
周六调用时两者都拿到周五数据；再用"昨日池"查"今日快照"（快照也是周五收盘）→
涨停股查自己涨停那天的收盘价，**恒等于 +10%**，于是产出"昨涨停均值 10.38%、
翻红率 100%、再涨停率 100%"的假指标，并把阶段误判为「高潮」且置信度"高"。
详见 `docs/strategy/sentiment.md`「历史误判案例库」。

**硬性要求（动这块之前必读）**：

1. 调用前先确认 `date` 是交易日（交易日历）；非交易日直接返回空 + 打日志，
   **不要依赖接口的隐式回退**。
2. 任何"昨日池 × 今日快照"的跨日计算，必须断言两者日期严格相差一个交易日。
3. 加哨兵断言：`再涨停率 == 1.0` / `翻红率 == 1.0` 在正常市场不可能出现，
   出现即说明日期串了。

### 3.2 ⚠️ 板块列表：push2delay 可用 / 分页上限 100 / 板块名与 ths 标签体系不一致

- `push2.eastmoney.com` 主域在本机被 WAF 拦截（空回复），`push2delay.eastmoney.com` 可用。
- 板块列表接口单页最多返回 **100 条**（total=504 时传 `pz=600` 仍只回 100），必须按 `total` 分页。
- 东财板块名与 ths 涨停原因标签体系不同：精确匹配命中率极低（如 ths「黄金珠宝」vs 东财「黄金概念」）。
  实践中采用「剥离概念/行业后缀 + 双向包含」模糊匹配，并取最长匹配作为最具体板块。
- 板块 3/5/10 日涨跌幅字段（f160/f109/f110）为字段序推断。2026-08-31 起题材看板
  用同花顺官方板块 K 线（`/api/a-share-index/prices/historical`，88xxxx.TI）交叉验证
  并覆盖（`multi_day_verified=true`，推断值保留在 `chg_*_inferred`）；实测创新药
  官方 3 日 +0.10% vs 推断 -4.78%——**字段序推断确实不可信**。未命中官方目录的
  题材保留推断值并在 caveats 说明。
- 板块资金流字段（2026-09-07 盘中实测，board_flow.py 消费）：f62 主力净额 / f66 超大单
  / f69 超大占成交比 / f184 主力占成交比（=净额/成交额×100，与自算两样本一致）/
  **f164,f165 5日 / f174,f175 10日主力净额与占比为官方字段**（实测 = 今日 f62 +
  近 N-1 根完成日 bar 之和，BK1650 两窗口偏差均 0.00 亿，非推断字段）。
  f267/f268 语义未验证不采用。主域 push2 可用性漂移（09-07 晨实测再次 HTTP 000），
  board_flow 双域 failover 硬编码。板块级资金取数唯一入口 = `app/market/board_flow.py`
  （L2 枢纽，任何模块不得自行请求东财板块接口）。

### 3.3 公司资料 / 所属板块（F10 CoreConception）— 2026-08-29 实测

- **公司档案** `datacenter-web.eastmoney.com/api/data/v1/get` (RPT_F10_BASIC_ORGINFO)：
  filter 用 `SECUCODE="600519.SH"`（代码在前、市场在后）。
- **所属板块** `emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax?code=...`：
  需带 `Referer: https://emweb.securities.eastmoney.com/`。
  **code 两种格式都可用**：`SH600519`（市场在前）与 `600519.SH`（与 RPT 接口同格式）实测均返回相同 27 条。
- 响应结构：`{ssbk: [...], hxtc: [...]}`，`ssbk` 单条字段为
  `SECUCODE / SECURITY_CODE / SECURITY_NAME_ABBR / BOARD_CODE / BOARD_NAME / IS_PRECISE / BOARD_RANK`。

**坑（已踩）：`IS_PRECISE` 是字符串 `'0'`/`'1'`，不是整数，也可能为 `null`。**
写 `== 1` 会静默全部失配、不报错。比较前必须 `str(x) == "1"`。

**`ssbk` 无类别字段，但按 `BOARD_RANK` 升序天然分段**（实测 600519/000001/600036/601318/300750/002594 六只一致）：

| 段位 | 内容 | 例 |
|---|---|---|
| 1–3 | 东财行业三级（大类/Ⅱ/Ⅲ） | 食品饮料 → 白酒Ⅱ → 白酒Ⅲ |
| 4 | 地域（名称以「板块」结尾） | 贵州板块 |
| 中段 | 风格标签 + 指数成分 | 大盘股、茅指数、MSCI中国、融资融券 |
| 尾部 | 概念题材 | 味蕾经济、白酒、乡村振兴 |

据此实现 `normalizer.classify_boards()`：行业按 `BOARD_RANK≤3`、地域按「板块」后缀、
概念按**首个 `IS_PRECISE='1'` 及其之后**（尾部规则可正确收进 `IS_PRECISE='0'` 的「酿酒概念」），
其余归风格/指数。属启发式，边界个股可能有误差，故 `boards` 仍保留全量混合列表不丢数据。

**⭐ 取「个股主板块」必须用行业三级 L2，不能用「首个 `IS_PRECISE='1'`」（2026-09-10 实测）**：
`IS_PRECISE='1'` 只标记**概念段的起点**，而该位置是概念里**排序靠前的边缘标签**，与公司业务无关——
实测茅台→「味蕾经济」、平安银行→「跨境支付」、中国平安→「互联网医疗」，全部语义不成立。
正确口径是 `industry[1]`（行业Ⅱ级）：茅台→白酒Ⅱ、平安银行→银行Ⅱ、宁德时代→电池、
比亚迪→乘用车、中国平安→保险Ⅱ（5 只样本全对）。实现见 `normalizer.main_board()`（P1-4 消费）。
**教训：需求文档里的「第 N 个字段」往往来自写方案时的印象，动手前要用真实数据验一遍语义。**

**⚠️ 板块代码两套体系（2026-09-10 实测）**：F10 `ssbk` 的 `BOARD_CODE` 是**纯数字 ID**
（白酒Ⅱ=`1277`、银行Ⅱ=`475`、电池=`1033`），而板块资金榜（`board_flow` 走 clist `f12`）是
`BK` + **4 位补零**（`BK1277` / `BK0475` / `BK1033`）。**不做补零规范化，按代码跨源直取会全部 miss**
（`475` ≠ `BK475`）。转换收口在 `normalizer.board_code_norm()`（幂等：已是 `BKxxxx` 原样返回），
provider 与 `main_board` 都经它。

## 4. 同花顺（分时候选，已验证可达）

`d.10jqka.com.cn/v6/line/hs_600519/01/today.js`（JSONP）返回当日分时：开/高/低/现价/量/额/均价等。
社区项目常用其分时与增量 K 线（`/v6/line/hs_600519/01/2026.js`）。
⚠️ **分时最终未采用本源**：当日分时由**腾讯**实现并在用（§1 `minute/query` → `/api/minute-line/{symbol}`）。
本节保留为「可达但未接入」的记录，ths 未实现分时 Provider（接口契约见 docs/system/api.md）。

## 5. Mock（演示/测试专用）

确定性种子数据，`source="mock"`、`realtime=False`，永不冒充实盘。只能单独使用
（`ASHARE_DATA_PROVIDER=mock`），禁止混入真实源链。

## 6. 社区参考（GitHub 调研 2026-08-28）

- [easyquotation](https://github.com/shidenggui/easyquotation)（5.4k★）：新浪/腾讯实时快照——与本库选型一致。
- [akshare](https://github.com/akfamily/akshare)：聚合新浪/东财/腾讯，全品类数据。**已部分接入**
  （2026-09-07 star 仓库评测后落地 `backend/app/services/akshare_ext.py`，定位**扩展面**、**不入行情热链路**：
  涨停三池 / 龙虎榜明细 / 宏观 CPI / 两融账户 / ETF·可转债日 K / 美股指数 / 外汇日线；懒加载 + 失败有类型，
  本机被墙的 push2 系一律不接）。**基本面不走 akshare**，由东财源提供。
- 同类聚合库普遍采用「腾讯/新浪快照 + 东财数据网」组合；腾讯字段更全（知乎测评）。

### 6.1 情绪周期专项（GitHub 调研 2026-08-29，改 sentiment 前必读）

- [vibe-astock](https://github.com/simonlin1212/vibe-astock)（Apache-2.0，341 测试）：
  契合度最高。核心观点——**"涨停家数只是原料，真正决定盘面状态的是派生读数"**。
  派生四件套：赚钱效应（昨日涨停股今天的均值/**中位数**/翻红率/再涨停率）、
  晋级率（1进2 / 2进3 / 3板+）、连板溢价、梯队结构 + **断层检测**。
  两点直接可抄：① "均值与中位数经常背离，看多数人的体感以中位数为准"；
  ② **"1进2 最敏感：明显走低=退潮，回升=修复"**。
  情绪周期做成近 10 日曲线并定位本轮起点与"第几天"，而非单点截面判定。
- [daben-review](https://github.com/WhiteWolf-js/daben-review)：工程分层成熟
  （`data → metrics → agent → app`），指标层纯本地计算不限频。
  盘中 P0 三类纯规则事件：高度板(≥4板)炸板 / **实时炸板率破 40%** /
  指数急杀（上证近15min ≥0.8% 或创业板 ≥1.2%）。候选分级 + 次日验证滚动命中率。
- akshare 情绪口径：`stock_zt_pool_em`（涨停池）、`stock_zt_pool_previous_em`
  （**昨日涨停股今日表现**——正是本次手搓错的那一项）、`stock_zt_pool_zbgc_em`（炸板）。
  **待验证**：返回字段与东财 push2ex 是否同源。

调研全文与阶段判据对照表见 `docs/strategy/sentiment.md`「历史误判案例库」。

## 7. 数据入库必带字段

`created_at / updated_at / data_timestamp / received_at / source / quality / version`（§3.3）；
quality ∈ high/medium/low/stale/invalid，低质量数据 AI 禁用、回测禁用、前端强制标识。

---

## 8. 全量数据源清单与使用度审计（2026-09-16）

> **速览（30 秒）**：当前共 **8 个行情/数据外部源 + 2 类本地存储 + 2 个非行情外部服务**，
> 使用度**极不均衡**：同花顺 fuyao **23 / 59** 端点（39%）、通达信 TDX **4 / 20** 数据类方法（20%）、
> akshare **9 / 11**（2 项零调用）。本轮**实测推翻两条既有结论**（§8.9）：
> ths 财务指标**全通**（原记「全失败」）、ths 跌停池**今日与快照自算逐只一致**（原记「漏报」）。
> **最明确的一处「改用更优质源」= 逐笔成交**：现役东财 `push2his` 在本机**恒 502**
> （复测更正：不是"通而空"，是 3/3 WAF 快速失败），而 TDX `get_transactions` 走 TCP 直连、
> 不经 HTTP、不受 WAF 影响 —— ✅ **已于 2026-09-16 落地为降级备源（`IMP-038`，§8.6①）**。
> **不建议接入**：ths 基金 28 端点（与短线题材定位无关）、`ExTdxClient` 扩展市场（港股/美股/期货）。
> ⚠️ **本节不另立任务清单**：未完成项一律登记在账本 §6.0（`kb/07 §3.3`）。

### 8.1 取数方法（可复现，禁凭印象）

| 维度 | 方法 | 输出 |
|---|---|---|
| 方法清单 | AST 扫 `def` / `AsyncFunctionDef` 节点 | 各 Provider 公开方法 + 行号 |
| 调用点 | AST 扫 `Call` 节点，按**方法名**聚合 | 生产 `app/` · 测试 `tests/` · 脚本 `scripts/` 三档 |
| 外部主机 | 正则扫 `app/**/*.py` 的 `https?://` 字面量 | 全部出网域名 + 引用文件数 |
| 端点可用性 | 读 `.env` 的 `ASHARE_THS_API_KEY` 直连 fuyao | HTTP 码 / 耗时 / `data` 形状 / `code` |

⚠️ **口径声明（数字不带口径必被误读）**：

1. 调用点按「**方法名精确匹配的 `Call` 节点**」计（`x.get_quotes()` 与裸名 `get_quotes()` 都算；
   `def` 行、`import` 行、注释不算）。
2. **同名方法跨 Provider 会合并计数** —— `get_quotes` 的 24 个生产点 = **6 个 Provider 合计**，
   不代表某一家被调 24 次。
3. 本节「未使用」一律指**代码事实**，**不等于端点不可用** —— 可用性另见 §8.3 实测列。

### 8.2 全量清单总表

| # | 源 | 生产承载文件 | 鉴权 | 信息维度 | 使用度 |
|---|---|---|---|---|---|
| 1 | **同花顺 fuyao**（官方） | `app/data_providers/ths.py` · `app/services/theme_catalog_service.py` · `scripts/sync_marketdb.py` | `X-api-key`（`.env`） | 涨停/炸板/天梯/龙虎榜/热榜/竞价/异动/日K/日历/复权事件 | **23 / 59** 端点 |
| 2 | **腾讯** | `app/data_providers/tencent.py` | 无 | 实时快照·五档·日/周K·分钟K·当日分时·搜索 | 6 端点，**实时热链主源** |
| 3 | **新浪** | `app/data_providers/sina.py` | 无（需 `Referer`） | 快照·五档·个股资金流 | 3 端点，备源 |
| 4 | **东方财富** | `app/data_providers/eastmoney.py` · `app/market/board_flow.py` · `app/news/flash.py` · `app/market/article.py` | 无 | 涨停/炸板/跌停池·板块·龙虎榜·财务·公告·快讯 | 8 域，**专项主源** |
| 5 | **通达信 TDX** | `app/market/tdx_kline.py` · `app/market/minute_backfill.py` · `app/services/heatmap_service.py` | 无（TCP 7709） | 日K·分钟K·行业板块·板块成分 | **3 / 20** 数据类方法 |
| 6 | **akshare**（聚合层） | `app/services/akshare_ext.py` | 无 | 涨停三池·龙虎榜·宏观CPI·两融·ETF/可转债·美股·外汇·商品·宏观日历 | **9 / 11**（2 项零调用） |
| 7 | **百度股市通** | `app/services/akshare_ext.py` | 无（cookie） | 财经日历 | 1 端点 |
| 8 | **NOAA CPC** | `app/market/climate.py` | 无 | ENSO ONI 气候指数 | 1 端点 |
| 9 | **本地 DuckDB marketdb** | `scripts/sync_marketdb.py` | — | 全市场日K + 复权因子（由 fuyao dump 构建） | 3 表 / **1029 万行** |
| 10 | **Parquet 落盘** | `data/parquet/` | — | 分钟线（腾讯/TDX）·全市场快照 | **3.3 GB** |
| 11 | **LLM 网关** | `app/core/llm_client.py` | 本机 `claude` CLI | 事件判读·资讯摘要·复盘解读 | `claude_cli` 生效 |
| 12 | **飞书 OpenAPI** | `app/notifiers/feishu.py` | `app_id` + `secret` | 告警推送 | 2 凭据在位 |

> **marketdb 实测（2026-09-16 16:32 同步后）**：`daily_k` **10,287,732** 行 / **5,564** 标的 /
> 2016-09-05 ~ 2026-09-16；`adjust_factor` 57,183 行 / 5,422 标的（1991-02-26 起）；
> `daily_k_adj` 10,287,732 行。质量报告 `status=ok` · `issues=[]` · 新鲜度滞后 1 日。

### 8.3 同花顺 fuyao：59 端点 × 使用度

按官方能力地图（随仓附带的官方 skill 包 `skills/hithink-finance/` 内 `api/capability-map.md`）分 10 类：

| 分类 | 官方 | 已接 | 接入位置 / 未接说明 |
|---|---|---|---|
| 元信息 | 2 | 1 | Provider（`tickers/list`）；`tickers/search` 未接 |
| 行情与公司行为 | 3 | 3 | Provider（含**零调用**的 `adjustment-factors`） |
| 财务数据 | 4 | **0** | **实测全通**（§8.9②），仅缺封装 |
| 估值数据 | 1 | **0** | **实测通**（五项指标），仅缺封装 |
| 交易日历 | 1 | 1 | Provider |
| 集合竞价 | 2 | 2 | Provider |
| 指数与板块 | 4 | 3 | 服务层直连（`theme_catalog_service.py`）；`prices/snapshot` 未接 |
| 公募基金 | 28 | **0** | **不建议**（§8.8①） |
| 特色数据 | 11 | 10 | Provider；缺 `limit-down-pool`（§8.6②） |
| 全市场导出 | 3 | 3 | 脚本直连（`sync_marketdb.py`） |
| **合计** | **59** | **23** | Provider 17 · 服务层直连 3 · 脚本直连 3 |

⚠️ **架构不一致（本轮发现）**：同一个源有**三个入口** —— Provider 层 17 个、
`app/services/theme_catalog_service.py` 直连 3 个（自建 `httpx` client，**绕过** `CompositeProvider`）、
`scripts/sync_marketdb.py` 直连 3 个。**直连的 6 个端点不经过熔断 / 请求预算 / 降级链**，
也不出现在 `provider_capabilities` 真值矩阵与 health 视图里 ⇒ **它们坏掉时系统不会报 degraded**。

**实测可用性（2026-09-16 收盘后，免费档 key）**

| 端点 | HTTP | 耗时 | 返回形状 |
|---|---|---|---|
| `special-data/limit-down-pool` | 200 | 0.23s | 跌停 **4** 只（`pagination.total=4`） |
| `valuations/snapshot` | 200 | 0.09s | `pe_ttm` / `pe_mrq` / `pb_mrq` / `ps_ttm` / `pcf_ttm` |
| `financials/indicators`（`report=2024-4`） | 200 | 0.14s | 5 类 ability，数值正常 |
| `financials/income-statements`（`period=annual`） | 200 | — | 4 期 |
| `financials/balance-sheets` / `cash-flow-statements` | 200 | — | 各 2 期 |
| `prices/snapshot`（全市场分页） | 200 | 0.09s/页 | **5,573** 只；`limit=1000` 可用 |
| `meta/tickers/search` | 200 | 0.08s | 消歧命中 |
| `a-share-index/prices/snapshot` | 200 | 0.11s | 命中 |
| `a-share-index/catalog/ths-index-list` | 200 | — | 390 概念 |

⚠️ **参数名不统一（易踩）**：`financials/indicators` 用 `report=YYYY-[1-4]`，
而利润/资产负债/现金流三表用 `period=annual|quarterly` + `limit`。
传错返回 `code=1001 Missing required parameter: period` —— **不是 401/403**，
按 HTTP 码判断「权限不足」会得出完全错误的结论。

### 8.4 通达信 TDX：20 个数据类方法 × 使用度

依赖 `easy_tdx` 1.20.12。本项目只用 `MacClient`（MAC 协议 TCP 7709）——
其 30 个公开方法 = **20 个数据类** + 10 个连接/文件生命周期类。

| 分组 | 方法 | 状态 |
|---|---|---|
| 日/分钟 K | `get_stock_kline` | ✅ 用（日K QFQ + 1/5min） |
| 板块 | `get_board_list` · `get_board_members` | ✅ 用（行业映射） |
| 板块排行 | `get_board_ranking` · `get_board_change_ranking` · `get_board_summary` | ❌ 未用（现役东财 `board_flow`） |
| **逐笔** | `get_transactions` | ✅ **用（`IMP-038`，2026-09-16；逐笔降级备源，口径 = 3 秒快照聚合）** |
| 分时 | `get_tick_chart` · `get_tick_charts` | ❌ 未用（现役腾讯当日分时） |
| 竞价 | `get_auction` | ❌ 未用（现役 ths `auction/*`） |
| 行情 | `get_stock_quotes` · `get_stock_quotes_list`（含 50+ 排序键） | ❌ 未用（现役腾讯/新浪） |
| 资金 | `get_capital_flow` | ❌ 未用（现役新浪） |
| 异动 | `get_unusual` | ❌ 未用（现役 ths 异动） |
| 元信息 | `get_symbol_info` · `get_goods_list` · `get_belong_board` | ❌ 未用 |
| 其他 | `get_chart_sampling` · `get_kline_offset` · `get_stock_kline_with_indicators` | ❌ 未用 |

⚠️ **另一协议族完全未接入**：`easy_tdx.TdxClient`（29 个公开方法，含 `get_price_limits`
涨跌停价 / `get_xdxr_info` 除权除息 / `get_finance_info` / `get_transaction_data` /
`get_history_transaction_data` / `get_fund_flow` / `get_minute_time_data`）与 `MacClient`
**不是同一个连接族**。**不要把「MacClient 未用方法数」当成「TDX 未用能力总数」** ——
后者需要另起一套连接与运维面，成本不同。

### 8.5 零调用 / 仅测试 / 单点脆弱

**① 完全零调用（生产 0 · 测试 0 · 脚本 0）—— 11 个**

| 符号 | 位置 | 性质 |
|---|---|---|
| `get_adjustment_events` | `app/data_providers/ths.py` · `app/data_providers/composite.py` | ths 复权事件流；与 marketdb `adjust_factor` **功能重叠** |
| `backfill_tdx` | `app/market/minute_backfill.py` | TDX 分钟线批量回填 |
| `load_vr_baseline` | `app/market/minute_backfill.py` | 量比基线 |
| `lhb_dir` | `app/market/lhb_archive.py` | 龙虎榜归档目录 |
| `_write_atomic` | `app/market/lhb_archive.py` | 原子写辅助 |
| `is_today_trade_day` | `app/market/trade_calendar.py` | 交易日判定（已被 `is_trade_day_on` 取代） |
| `macro_cpi` | `app/services/akshare_ext.py` | 宏观 CPI |
| `margin_account` | `app/services/akshare_ext.py` | 两融账户 |
| `us_treasury_10y_daily` | `app/services/akshare_ext.py` | 美债 10Y |
| `usdcnh_daily` | `app/services/akshare_ext.py` | 离岸人民币 |
| `name` | `app/data_providers/composite.py` | Provider 名属性 |

**② 仅测试调用（生产 0）—— 5 个**

| 符号 | 位置 |
|---|---|
| `breaker_state` | `app/data_providers/composite.py` |
| `invalidate_bars_cache` | `app/market/tdx_kline.py` |
| `load_day` | `app/market/lhb_archive.py` |
| `nth_prev_trade_date` | `app/market/trade_calendar.py` |
| `invalidate_cache` | `app/market/trade_calendar.py` |

**③ 单点生产依赖（prod = 1，无冗余路径）—— 3 个**

| 符号 | 唯一消费方 |
|---|---|
| `get_limit_up_ladder` | `app/sentiment/ladder_check.py` |
| `get_hot_rank_trend` | `app/api/routes/market_longhu.py` |
| `get_hot_stock_list_history` | `app/predict/service.py` |

⚠️ 三者共同点：**上游只有 ths 一家**（`CompositeProvider` 无备源），消费方各只有一处
⇒ 上游抖动即整条能力不可用，且**没有第二条路径能发现它坏了**。

### 8.6 可改用更优质源替代（按确定性排序）

**① 逐笔成交 —— 最明确的一处（现役源在本机不可用）**　✅ **已落地（`IMP-038`，2026-09-16）**

- 现役：`app/data_providers/eastmoney.py` 的 `get_trades`（`push2his` 系）。
  实测状态见 `data-source-comparison.md` §0 / §10：**本机 push2his 被 WAF 拦**。
  ⚠️ **2026-09-16 复测更正了本条原措辞**：不是「接口 ok 但**数据恒为空**」，而是
  **3/3 抛 `ProviderError`**（`Server disconnected without sending a response.`，
  0.12–0.22s 快速失败 = WAF 特征）⇒ 线上 `GET /api/trades/600519` **恒 502**。
- 候选：TDX `MacClient.get_transactions` —— **TCP 7709 直连**，不经 HTTP、不受出口 IP 风控影响；
  历史逐笔另有 `TdxClient.get_history_transaction_data`（**另一协议族，仍未接入**）。
- 消费方仅 2 处：`app/assistant/tools/market.py` · `app/api/routes/market_quotes.py`。
- ✅ **已落地为「链主源 + TDX 降级备源」**（`app/market/tdx_tick.py`）—— 不是替换主源。
  **实测答案（原「落地前必须实测」三项）**：① 字段口径 = `bs_flag` **与东财相反**
  （TDX `0=买/1=卖/2=中性/5=盘后`），量纲 = **手**（三向交叉验证：Σvol 26,243 手 ↔
  fuyao 日线 26,235.24 手 + 盘后 8 笔）；② 单次返回条数与时间窗 = **`start` 以「最新」为原点、
  段内升序**，`count≤1000` 时单次 IPC 即给最新 N 笔（median 20.1ms），**`count>1000` 的库内
  自动分页页序是错的**（须手动翻页）；③ **与东财「大单」分档不可比** —— TDX 给的是
  **3 秒快照聚合**（实测秒位只落 3 的倍数、相邻差众数 3s），东财给的是逐笔明细 ⇒
  **口径不同、不可混拼**，故前端口径行由 `Trade.source` 推导显示。
  ⚠️ 与 §11 教训的关系：本条只换**逐笔明细**源，**不触碰聚合资金口径**
  （`get_capital_flow` 仍在 sina，§11「异源主力净额符号可相反」的警告面不在此）。
- **状态码口径**（收尾修正）：只有**真故障**才 502；两源都**没给出数据**（空 / 备源未启用）
  ⇒ **200 + 空列表**，原因进 `meta.trades_detail`。判据是 `trades_failure_detail()` 的
  **白名单**（`chain: empty` / `tdx: empty` / `tdx: disabled` 不算故障），**不是** `if detail:`
  —— 后者会把"没数据"报成"数据源故障"（前端会把源问题显示成"该股没有逐笔"，方向恰好反了）。
  ⚠️ 也不能按"含 Error/timeout 字样"判：真实 `ProviderError` 文本
  （`Server disconnected without sending a response.`）**一个关键字都不含**。
- ⚠️ **未验证边界**：盘中实时性（取证在盘后）；北交所覆盖不全（920819 有数据，
  430047/830799 返 0 行）。详见 `docs/handoff.md` §IMP-038。

**② 跌停池 —— 建议升为「双源」，而非替换**

- 现役：`app/data_providers/eastmoney.py` 的 `get_limit_down_pool`（`push2ex`，4 个消费方）。
- 候选：ths `special-data/limit-down-pool`（**代码未封装** —— `ths.py` 无此方法）。
- 本轮实测：ths 跌停池 **4 只** vs 全市场快照自算 **4 只**，**逐只一致、池 ⊆ 快照且零例外**。
- ⚠️ **但不足以推翻原结论**：`data-source-comparison.md` §2.4 的「漏报」实测于 2026-08-28
  （ths 1 只 vs 推导 4 只，其中 603319 封死跌停却漏）；**今日只有 4 只跌停（弱市），样本量过小**
  ⇒ 该缺陷**是否已修复须在大跌日复测**才能定论。**当前不改现役链路**。

**③ 财务 / 估值 —— 可替代东财爬虫（ths 是官方 REST）**

- 现役：`app/data_providers/eastmoney.py` 的 `get_financials`（3 个消费方）。
- 候选：ths `financials/{indicators,income-statements,balance-sheets,cash-flow-statements}`
  + `valuations/snapshot` —— **本轮实测全通**（§8.3），且是官方 REST 而非页面接口。
- 价值：东财侧受 `push2` 系 WAF 与**字段序推断**影响（本文 §3.2 已记录
  「字段序推断确实不可信」），ths 侧是**显式字段名**。

**④ 全市场快照 —— 可作盘后「定稿口径」权威源**

- 现役：腾讯 1Hz 轮询（实时）+ 东财。
- 候选：ths `prices/snapshot` 全市场分页（**实测 5,573 只，0.09s/页，`limit=1000` 可用**）。
- 用途：**不替换实时链路**（ths 配额 + 8s 超时不进秒级链），
  而是给盘后落盘/快照留痕一个**非爬虫、带 `timestamp` 的权威口径**。

**⑤ 分钟 K / 分时 —— 无更优源（诚实边界）**

- 现役：腾讯当日分时 + TDX 历史 1min（≈94 交易日）/ 5min（≈495 交易日）。
- ths 官方 capability-map **明确声明不覆盖**分钟 K 与 tick ⇒ **不存在"更优的免费源"可换**。
  TDX `get_tick_chart`（分时含历史）是**唯一可能的增量**，但属「补深度」而非「换更优」。

### 8.7 未充分利用但具挖掘价值（按价值排序）

| 序 | 对象 | 为什么值得 | 现状 |
|---|---|---|---|
| 1 | ~~TDX `get_transactions` 逐笔~~ | 填补「现役源本机不可用」的真空 | ✅ **已接入**（`IMP-038`，见 §8.6①） |
| 2 | ths `financials/*` + `valuations/snapshot` | 官方 REST 替东财爬虫；实测全通 | 见 §8.6③ |
| 3 | ths `limit-down-pool` | 跌停池第二源，消除单点 | 见 §8.6② |
| 4 | ths `prices/snapshot` 全市场 | 盘后定稿口径 | 见 §8.6④ |
| 5 | TDX `get_stock_quotes_list` | 内含 **50+ 排序键**，现役排行全靠东财 clist | 未用 |
| 6 | TDX `get_capital_flow` | 现役新浪资金流为**爬虫**，且当日行分类可能为 0 | 未用 |
| 7 | TDX `get_auction` 竞价 | 现役 ths 竞价**无备源** | 未用 |
| 8 | TDX `get_unusual` 异动 | 现役 ths 异动**无备源** | 未用 |
| 9 | TDX `get_board_ranking` / `get_board_summary` | 板块榜现役东财受 WAF + 单页 100 条限制 | 未用 |
| 10 | ths `meta/tickers/search` | 现役搜索走腾讯 smartbox / 东财 suggest（均为爬虫） | 未用 |
| 11 | `TdxClient.get_price_limits` | 现役涨跌停价**全自算**（`limit_pct`），缺外部交叉校验源 | 未接入该协议族 |
| 12 | `TdxClient.get_xdxr_info` | 现役除权除息走 ths `adjustment-factors`（**该封装零调用**） | 未接入该协议族 |

### 8.8 诚实边界：不成立的假设与不建议接入项

1. **ths 基金 28 端点不建议接入** —— 本系统定位是 A 股短线题材/情绪；基金资料、经理履历、
   净值与持仓披露**不进入任何现役链路**。接入 = 只增维护面。
2. **`ExTdxClient` 扩展市场（港股/美股/期货/期权/外汇）不建议接入** —— 同上，与定位无关。
3. **「代码里没有」≠「端点不可用」** —— 本轮已证：`financials/*`、`valuations/snapshot`、
   `limit-down-pool`、`prices/snapshot` **全部 HTTP 200 且返回真实数据**，只是**没人封装**。
4. **「调用点 0」≠「应当删除」** —— `macro_cpi` / `margin_account` / `us_treasury_10y_daily` /
   `usdcnh_daily` 是**已实现未接线**的宏观面能力，删或接属**取舍**（须拍板）；
   与 `lhb_dir` / `_write_atomic` / `name` 这类**纯内部辅助**性质不同，不可一并处置。
5. **本审计不对策略增益下任何断言** —— 它只回答「有没有接、接了多少、能不能用」，
   **不回答「接进来是否提高收益」**。后者属回测/消融范畴，须另立证据。

### 8.9 本轮推翻 / 更正的既有结论（3 处）

**① `ths.py` 模块 docstring 的能力承诺 > 实际实现（文档漂移）**

`app/data_providers/ths.py` 头部声明能力含「涨停/**跌停**/炸板池、连板天梯、龙虎榜、
**财务三表、估值**、集合竞价、异动、热榜、**全市场导出**」。逐项核对代码：
**无跌停池方法、无财务三表、无估值**；「全市场导出」实现在 `scripts/sync_marketdb.py`
而**不在本文件**。⇒ 属 `doc-health` A–N 各项**查不出**的一类失真（与 [[KB-ENG-85]] 同族）：
**它不产生死链、不产生未登记文件，只产生错误的读者预期**。

**② `data-source-comparison.md` §4 表「ths 财务 report 格式仍全失败」—— 已过时**

本轮实测 `financials/indicators?report=2024-4` **HTTP 200 且 5 类指标全部有值**
（growth / profitability / solvency / operation / cash-flow）。
成因见该文 §7 表第 3 行：**当年是参数名靠猜**（`s` / `start` / `report` 混试），
而官方端点详情页（同 `skills/hithink-finance/` 包内 `api/endpoints-financials.md`）明写 `report=YYYY-[1-4]`。
⇒ 该行按「ths 财务**已通**，仅缺封装」更正。

**③ `AGENTS.md` 门禁行称 `trading_days`「57 生产调用点，全仓最热」—— 数值待复核**

按本节 §8.1 的 AST 精确口径实测：`trading_days` 生产 **37** · 测试 5 · 脚本 2（合计 44）。
`AGENTS.md` 自身即写明「数字必须实测回填，勿凭记忆」，故此处**只标差异、不擅改**——
差异成因（口径不同 / 已过时 / 含 `get_trading_days(` 子串）**需当轮复核后回填**。
「全仓最热」这一**结论**仍成立（第二名 `get_limit_up_pool` 生产 27）。
