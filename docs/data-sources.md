# 数据源说明

## 0. 接入策略（§2.2 主源→备源→降级）

`ASHARE_DATA_PROVIDER` 主源 + `ASHARE_PROVIDER_FALLBACKS` 备源，由 `CompositeProvider` 逐方法自动切换：

```text
行情/K线/盘口/搜索: 腾讯 → 东财（search suggest / K线）
涨停池 / 龙虎榜:    东财 push2ex / datacenter（稳定，独立主机）
逐笔成交:           东财 details（push2his，本机被限流时该能力 502）
默认链:             chain(tencent→eastmoney)
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
详见 `docs/sentiment-phase-review.md`。

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
- 板块 3/5/10 日涨跌幅字段（f160/f109/f110）为字段序推断，**未经 K 线交叉验证**，应作为参考值并显式标注待验证。

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

## 4. 同花顺（分时候选，已验证可达）

`d.10jqka.com.cn/v6/line/hs_600519/01/today.js`（JSONP）返回当日分时：开/高/低/现价/量/额/均价等。
社区项目常用其分时与增量 K 线（`/v6/line/hs_600519/01/2026.js`）。Phase 3 做**分时图**时优先接入，
当前未实现 Provider（保持接口契约，见 docs/api.md）。

## 5. Mock（演示/测试专用）

确定性种子数据，`source="mock"`、`realtime=False`，永不冒充实盘。只能单独使用
（`ASHARE_DATA_PROVIDER=mock`），禁止混入真实源链。

## 6. 社区参考（GitHub 调研 2026-08-28）

- [easyquotation](https://github.com/shidenggui/easyquotation)（5.4k★）：新浪/腾讯实时快照——与本库选型一致。
- [akshare](https://github.com/akfamily/akshare)：聚合新浪/东财/腾讯，全品类数据（Phase 4 基本面接入候选）。
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

调研全文与阶段判据对照表见 `docs/sentiment-phase-review.md`。

## 7. 数据入库必带字段

`created_at / updated_at / data_timestamp / received_at / source / quality / version`（§3.3）；
quality ∈ high/medium/low/stale/invalid，低质量数据 AI 禁用、回测禁用、前端强制标识。
