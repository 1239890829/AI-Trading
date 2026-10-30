# 数据源说明

## 2026-10-30 基金数据与美股来源条件

统一工作台的基金沿原THS金融数据服务，只新增正式目录搜索、基金NAV、profile和披露持仓读者，不增加行情供应商。本轮能力与实抓对照见[能力比较§23](data-source-comparison.md#23-2026-10-30-基金同源读取与美股条件)。官方合同：[基金概览](https://fuyao.aicubes.cn/docs/api-reference/funds/)、[净值](https://fuyao.aicubes.cn/docs/api-reference/fund-performance/)、[披露持仓](https://fuyao.aicubes.cn/docs/api-reference/fund-holdings/)。保留完整source_symbol/asset_type/币种；不猜同号股票、后缀、中文别名或基准。

读取独立TTL/同key在飞共享与小并发上限，不进入A股Hub和全市场循环；满额明确本地忙，不称供应商失败。公开回包按原始正文hash单存，小回执分别记录端点/参数/接收时刻，避免同数据重复下载/存储；Key/header不入证据。NAV所属日、持仓报告/披露/修改钟与接收钟分开，响应timestamp不冒充源发布时间。坏身份/值/日期不缓存成成功，未知和未覆盖保留。

已有最小实抓确认净值/联接一层披露可读，也发现源中文名称和范围字段缺口。profile的经理/费率/规则不等于完整投资范围或当前申购状态；diagnostics的上游概率不作事实或gold；资讯标题不代招募书。缺正式范围/基准/境外价格/估值FX时分别标研究待核、估算不可提供，不造推荐。

美股现无满足用户完整免费要求的已准入源。Alpaca个人免费IEX不覆盖全市场，免费夜盘成交延迟15分钟且quote是指示性；新Key未申请、未实接。THS现有合同未证明支持美股/纳指100。腾讯/新浪/东财公开页面可读性也不自动证明完整时段、时效及留存/展示许可。资料依据见[统一工作台](../product/cross-asset-workbench.md#4-美股只读的条件)，源限制不靠换成A股或估值隐藏。

2026-10-27全域来源核验见[能力对比§22](data-source-comparison.md#22-2026-10-27-全系统来源核验)。用户允许明确标注的自有计算，供应商自身正式来源纳入“官方”；不把来源品牌、HTTP200或接收钟当用途授权、业务可用或原始源钟。当前有可行正式主干，但不能宣称全部功能免费实时完整可用；THS既有Key的公开三表/估值/复权/日历/资讯等8项正常读取成功，端内资金/高频限制独立，不再沿用“THS无新闻”或“Key缺失”的全称判断。

2026-10-26复盘读取补强仅扩展既有消费者，不新增供应商或交易评分：同花顺正式行业/概念目录快照只筛候选，原日期日线核每类最多20个强弱样本，沿board_flow原日存档复用；无资金不跨东财BK/同花顺TI身份拼接。已有marketdb同版相邻日复权收盘仅作有限覆盖涨跌对照，成交额取原始日线turnover人民币元；日标签、首次源钟、同步接收钟分别保留。正常闭市指数只供报告partial历史观察，原stale不升级实时。竞价顶层timestamp经实抓为就绪钟，原接口未给逐行源日，仍只作明确未核采集参考。消费者与调度依赖见[复盘现役契约](../review/review-agent.md#2026-10-26-复盘内容与来源承接)，取源实测见[能力对比](data-source-comparison.md#19-2026-10-26-复盘内容补强实测)。

2026-10-26三项参考价格研究只读已有marketdb的`daily_k_adj.close_adj`与同证券/日期的`daily_k.volume`，不以原始收盘补复权缺失，不把turnover成交额当换手率。按北京日终涵盖源库UTC零点的日标签，已完成源日与本机运行钟分开，首次发布时间未知。源文件、质量报告与规则/reader版本哈希绑定，源变更不发布混版；质量告警和缺档保留未知。原库已有A股代码行不等于历史全集/存续认证，研究匹配不进正式选股、通知或模拟；完整定义及35项取舍见[策略登记](../strategy/strategy-registry.md#2026-10-26-三项可运行价格研究与逐项取舍)。

> **速览（30 秒）**：本文 = **数据源接入策略**（主源 → 备源 → 降级链），不是选型依据也不是实测对比。
> 核心约束：真实过期、无来源和缺项必须**可见**（标 `stale` + health=degraded，绝不伪装实时）；拒绝的新观测另保留诊断，不自动改写仍可信的当前事实；**同名字段异源异单位不可混算**；
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
逐笔成交:           链上东财 details（push2his，2026-09-16本机实测WAF失败）
                    → 路由层降级 TDX 直连分笔（app/market/tdx_tick.py，IMP-038）
                    → 腾讯 HTTP 分笔后备（IMP-079；4秒/24请求；可单独关闭）
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
| 实时快照/五档 | `qt.gtimg.cn/q=sh600519,...` (GBK, `~` 分隔) | 字段(0起): 1名 3现价 4昨收 5开 6量(手) **9-18买五档价量 19-28卖五档价量** 30时间(北京) 31涨跌 32涨跌% 33高 34低 36量(科创股/其余手) 37额(**万**) 38换手% |
| 日/周 K 线 | `web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600519,day,,,320,qfq` | `data.sh600519.qfqday` 行=`[日期,开,收,高,低,量(科创股/其余手)]`，qfq=前复权 |
| 分钟 K 线 | `ifzq.gtimg.cn/appstock/app/kline/mkline?param=sh600519,m5,,320` | `[YYYYMMDDHHMM,开,收,高,低,量]`，m1~m60 |
| 当日分时 | `web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh600519` | 行=`HHMM 价格 累计量(科创股/其余手) 累计额(元)`，1 分钟粒度 → `/api/minute-line/{symbol}` |
| 搜索 | `smartbox.gtimg.cn/s3/?v=2&q=...&t=all` (GBK) | `^`分组 `~`字段：名称/代码(sh600519) |

单位（2026-10-08更正）：科创板688/689的报价、日/分钟K线、分时累计量原值为股；
其余现役股票/ETF对应字段为手（×100转股）。五档盘口仍为手（×100），HTTP分笔为手（不转换）。
成交额万（×1e4转元）。不能把茅台的单位推广至科创板。原茅台盘后数据验证保留
（16126 手×100 ≈ 20.86 亿÷1297.40 ✓；指数 9703.65 亿与东财 f6 一致 ✓）。
时间戳为北京时间，Normalizer 统一转 UTC。

前端分时合成（2026-10-12，W01/BUG-030）：归一后累计量为股、累计额为元；
股票均价只用同一份有效quote的额/量，指数保持空均价。
同分钟量额差分须证明同源、同日相邻基准；分钟量另核原尾量与累计差配对。
跨源、缺口或未封口的新分钟只更新已观察价格/全日累计/股票均价，分钟量额留空，
由既有REST校准恢复。tooltip消费显式缺值，不用跨分钟累计差冒充该分钟成交额。
这是显示层修复，不改变Provider、HTTP字段、源优先级、选股或消息规则。

所属板块资金接口（2026-10-12，W07/BUG-031）：公司资料已证明的名称/代码/industry或concept归属不再依赖资金榜是否有该行；资金榜缺行只将动态字段及kind留空。资料本身未知仍缺省，代码未知不按名字猜资金；保留现有缓存和单批50只上限，无新取数源。前端区分当前金额、上次归属与未知，不用旧金额补缺项。
2026-10-14 IMP-019按离线时钟反例去掉动态资金整包的额外60秒结果缓存，避免叠在原30/300秒输入缓存上续龄。静态主板块仍6小时，缺失/失败仅60秒并按股票单飞；每响应从原底层缓存装配动态值，不变更供应商轮频或以Hub时间补成板块源钟。

**2026-10-12腾讯形成中尾桶证据**：公开同次响应的分钟标签可比内嵌quote源钟领先一分（13:48标签、13:47:01报价，同价同累计股数）。保留原HHMM/ts与历史点；仅身份、交易日、源钟连续时段、下一分钟、价量全部吻合时，尾点带可选`time_label=interval_end`及真实`observation_ts`。集合/午休哨点或缺证明不打标；不拿received_at冒源钟。前端按此证据跟进最新quote，浮层区分分钟截至与实际报价钟，分钟量额缺基准仍未知。工程状态归[W01/BUG-033](../stages/w01-charts.md#bug-033)，不是对腾讯任意时段的正式文档担保。

**2026-10-26开盘竞价锚点纠偏**：09:21与09:25实际同包响应只含`0930`一行，qt源钟分别仍在竞价/开盘等待；个股09:25已可有非零累计成交量额，指数qt量与minute量不是同一口径。仅单09:30标签、同日同包精确代码/价格、合法09:15≤qt源钟<09:30且不晚于真实接收钟时，标`time_label=opening_anchor`；个股累计股数还须吻合。保留原标签/价/累计量额，源钟`observation_ts`与接收钟`received_at`分列，不补写为当前成交分钟。旧包09:30后仍为锚点；它不进入连续曲线或相邻分钟差分，只有新可信连续报价另建观察点。前端须另核交易日、日期与阶段才显示等待，HTTP失败、普通未来/错日和收盘竞价原尾点门保持。实源复现与工程/加载分层见[性能报告§34](../system/runtime-performance.md#34-2026-10-23-被拒观测与当前事实分离)，并非所有竞价数据均可用的担保。

## 2. 新浪（备源）

`hq.sinajs.cn/list=sh600519,...`（GBK，必须带 `Referer: https://finance.sina.com.cn/`）。
字段：0名 1开 2昨收 3现价 4高 5低 6买一价 7卖一价 8量(**股**) 9额(元) 10-19买五档 20-29卖五档 30日期 31时间。
提供快照 + 五档盘口；K 线未实现（由腾讯/东财兜底）。

## 3. 东方财富（专项源）

- **涨停池** `push2ex.eastmoney.com/getTopicZTPool`：p=价格×1000、fbt/lbt=HHMMSS、fund=封单额(元)、zbc=炸板、lbc=连板、zttj={days,ct}（"7天7板"）。实测 2026-08-28：82 只。 2026-10-13单位复核：官方ZT原始`002343 p=7360`→7.36元，与同日THS及腾讯缓存一致；原×100说明及归一化已被真实降级反例否定。原字段`hybk`是行业，未提供涨停原因时不能补造。
- **炸板池** `push2ex.eastmoney.com/getTopicZBPool`（同 ut/dpt 参数）：实测 2026-08-28：16 只。炸板率 = 炸板 ÷（涨停＋炸板）。
- **板块列表** `push2delay.eastmoney.com/api/qt/clist/get`：历史实测的延迟域、单页100条限制按原日期保留；2026-10-27旧双域在HTTP响应前断开，不能据此认定WAF或整个供应商不可用。必须按实际total核分页。旧剥后缀/双向包含只可生成东财参考候选，不构成THS正式桥，也不贡献已确认资金/响应；正式身份始终按来源+代码，不能由同名提高确定性。
- **龙虎榜** `datacenter-web.eastmoney.com/api/data/v1/get` (RPT_DAILYBILLBOARD_DETAILSNEW)：SECURITY_CODE / BILLBOARD_* / **EXPLAIN**(上榜原因)。
- 行情族 `push2/push2his`：旧空回复曾被推断为WAF/共享出口问题，现仅保留为当时推测；当前逐入口、机器和日期实抓见能力对比，不据空回复断言原因或家庭网络必可用。链上search/K线/成交等能力各自验收。
- `ulist` 与 `stock/get` 字段编号**不一致**，不可混用映射表。
- **7x24 快讯** `np-weblist/np-listapi` 的 `comm/web/getFastNewsList` 是独立专项源，不走行情 `CompositeProvider`。`code` 是来源条目身份，`showTime` 是来源发布时间，系统收到/可见时间另由 `EventObservation` 记录；`stockList` 的全部 A 股和板块代码均保留。2026-09-24 本机只读实抓：首页 50/50 有 code 与可解析 `showTime`，两页 100 条可用 `sortEnd` 翻页，带上一条 code 的二页补采能找到重叠；同日另一次只读抓取连续到第 21 页，每页 50 条，`sortEnd` 未循环，时间从当日 14:09 延伸至前一日 08:38。此为当次可用性/字段观察，不保证接口永久稳定或任意历史可回溯。`flash_watermark` 按频道保存完成水位、首次基线、断档时间与原因；只有翻页覆盖到旧水位且所有条目入库成功才前移。`GET /api/events/flash-coverage` 暴露持久状态。首次基线前的历史不宣称完整；超过补采页上限、来源删除旧 ID、翻页中断或启动前失败会保留 `gap_at`，不能把当轮抓到部分内容写成“无断档”。

  超过正常轮询 20 页上限的缺口可用 `backend/scripts/recover_flash_gap.py` 单频道补采。先停止应用及调度器，在 `backend/` 下运行 `.venv/bin/python scripts/recover_flash_gap.py --db ../data/ashare.db --channel 100 --expected-last-code OLD_CODE --max-pages 21` 做默认只读预检，其中 `OLD_CODE` 换成覆盖接口显示的旧 code；确认后添加 `--apply`。实际运行前在忽略的 `artifacts/recovery/` 做 SQLite 一致备份，单次最多 100 页。只有旧 code 在补采结果中重叠、全部条目入库且持久水位写入成功，才关闭缺口；来源已删除旧 ID、仍超页数、页面错误或入库错误时保留原水位与缺口。此操作不是首次基线前历史的证明，也不应在应用运行时与常规轮询并发。

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

### 3.2 板块列表：双域可用性需实测 / 分页上限 100 / 板块名与 ths 标签体系不一致

- 早期本机主域空回复、延迟备域可读。旧记录将空回复直接定性为WAF，证据不足；不能沿用为当前原因或可用性结论。
- 2026-10-14盘后重验：生产双域均出现响应前断开；直接HTTPX按当前参数和东财公开页面请求形态各测两域，共4次均RemoteProtocolError（117–170ms）。另一次原生curl独立传输也获empty reply；TLS已建立但无HTTP响应，不能断言WAF/IP封禁。官网HTML/公开JS可读，不等于clist服务可读。此5次为额外诊断，不能混进生产可用率分母；没有证据支持改参数或延长缓存即可恢复。新浪板块涨跌排行与既有同花顺目录/价格不是同口径f62资金备源，不混算、不以历史补实时。
- 同日22时新增隔离复核：[东财资金官网](https://data.eastmoney.com/bkzj/hy.html)200，显式direct的curl/HTTPX同URL各一次clist仍响应前断开；环境代理未参与不代表已排除VPN/TUN。新浪按官方真实ID `0/new_dlhy` 查到2026-10-14板块历史，但[其帮助](https://finance.sina.com.cn/temp/guest4377.shtml)说明Level-1、通常延迟5–10分钟、主力按单笔≥100万元统计，不与东财f62直接互换。东财[板块异动官方页面](https://quote.eastmoney.com/changes/boardlist.html)的push2ex备端三次返回真实BK/主力值（万元）/当日批次dt，保留LAB候选；当时业务全集与分类层级、精确f62交叉、盘中源钟与稳定性未证，仅保留LAB；2026-10-26按下文独立异动统计资金视角准入只读产品，不是透明替换f62，盘中稳定率仍未证。缺超大单、占比或多日等非核心字段不单独否决其核心补源价值，缺值不补零。逐项证据与边界见[数据源对比§16](data-source-comparison.md)；原TTL、轮频、源序及选股/通知规则不变。
- 2026-10-15两次真实交易窗被动日志仍有AM32、PM31次双域失败汇总；主域、备域、汇总是关联层，不能相加成独立HTTP次数，部分分页200不证明完整批次成功。固定push2ex隔离LAB共4次5行读取均200、约183–237ms且批dt推进；它仍属同供应商另一服务，逐行源钟、分类全集与zjl/f62精确同口径未知，不能据此准入或宣布更稳定。原始响应、单次claim和失败保留，不追加重试求成功，见[性能报告§27](../system/runtime-performance.md#27-2026-10-15-真实交易双窗与sqlite读取隔离)。
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

2026-10-12 IMP-019补充：20d视图独立读取既有本地日线，无实时榜外呼依赖。统一截止日、至少20条有效存档、有限本地覆盖与historical/as_of/quality字段；今日涨幅/当日主力净额及占比/5d/10d/龙头/排名变化均null。主/备clist断连仍如实不可用，不以历史或新浪个股汇总代替同口径实时板块资金。资金收盘snapshot仅在日历确定交易日时新写，读历史排除确定非交易日，日历覆盖外保留+degraded；不重写旧文件，不宣称已恢复旧丢失源日期。
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


### 4.1 标准指数当前成分（2026-10-15）

既有同花顺官方`/api/a-share-index/constituents/ths-stock-list`支持标准指数SH/SZ代码。工作台六指数实际返回2229/500/100/50/300/1000条，完整响应校验见[性能报告§28](../system/runtime-performance.md#28-2026-10-15-工作台标的视角与按需读取)。内部`/api/indices/{带市场前缀的代码}/members`仅接受现有六指数；完整性/重复/代码市场校验失败返回失败，不把空集当成功。静态关系缓存6小时、最多6键、北京自然日隔离，不改变报价缓存或源钟，快照就绪钟不是成分调整生效日期。无历史成分、权重或排名含义；不为打开列表订阅全体成分行情，不写选股/题材归属库。


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

### 7.1 选股、通知与复盘消费者的源时点约束（IMP-087，2026-10-09）

- 每日精选的generated_at是最终结果完成时刻，trade_date是当日候选归属日。原quote_audit.data_timestamp、盘中snapshot_as_of、事件recorded_at分别保留；不能用生成、抓取或接收时间补造旧报价当时可见性。每日最终items与生成钟共同绑定selection_version，供消息和复盘核原版本。
- 六维dimension_evidence保存success/valid_empty/partial/missing/error与实际理由。成功读取无关联事件可为valid_empty；资金净额0、负PE及零增长仍可用。原计算保留中性占位，卡片/通知明确缺项；可用性字段不补造不存在的逐维源时点。旧记录未保存读取状态保持未知。
- 自设个股条件在事件生成与发送前共用完整data_timestamp、source、有效现价和freshness核验；received_at不足以取得触发资格。真实持仓风险核hub报价或快照行自身源钟/质量，快照抓取钟不能升级行证据；缺报价、源钟或成本保留uncompleted与逐股原因，不解释为风险不存在。
- 每日复盘只对明确表现日及原生成版本。09:30前生成的open_to_close统计须有个股和基准high/medium、本日完整时区收盘源时间，按现有60秒收盘窗校验；开盘后生成缺入选后路径时不可评。参考价收益另核quote_audit与本次生成时刻的窗口，历史日只读已有归因，不调用今天行情补写。
- 可信D0价格观察不证明成交或T+1收益；marketdb T+5反事实对照使用两组相同T+5窗。ISO/compact同日先归一再取历史容量，缺窗口、版本漂移、仅观察和非有限值排除可信统计，仍保留原记录。

本轮只修消费者的身份、时效和失败语义，未替换或新增数据源。夹具/隔离库测试不证明实时覆盖、长期可用性或实源SLO；详细消费者见[细功能审计§41](../product/feature-closure-audit.md#41-imp-087-原选股与可靠消息的细功能契约2026-10-09)。

### 7.2 首次加入与旁览消费者的事实身份（IMP-088，2026-10-09）

- selection-entry-v1分recorded/partial/missing。selected_at是原首次持久记录钟，reference_price/reference_change_pct是原记录时的报价值，quote_as_of保持原源钟，source_version绑定daily selection_version或intraday run_id；观察字段不代表执行或成交。首次记录不随GET、重生成或现价改变；缺失不以现在的价格/读取时间补填。
- 每日记录由原组合落库事务保存；recorded须原报价有来源、high/medium质量和同北京日的明确时区源钟；盘中按首次记录时60秒新鲜窗及既有5分钟未来偏差界限，收盘后按当日15:00窗口核验。异常报价保留原观察价/加入钟并标partial及原因，后续GET不重新升级；旧名单首次未记录或生成/日期不满足契约时保持missing。盘中只从当日已提交、合格rank且处于真实展示容量的selection-entry-v1快照读取，拒绝、unknown、超容量和仅参考行不取得首次加入资格。结构版本为pit-evidence-v4.selection-entry；旧v3及更早归档保留身份，默认效果读取按feature分开，不回填为新版。
- 涨停数/最高连板及最高证券身份来自原完整涨停池，后续候选截取不改变该池身份。题材机会/断层旁览固定点击时opps批次，说明实际题材展示范围；原涨停池旁览可继续按原日期接口读取，其数据更新不冒称等于旧点击批次。
- 影子activation描述当前真实配置，runtime.as_of描述后台回执；二者不等于行情源时间或成交时间。两影子GET及main账户read_only=true旁览只读；默认main读取保留原初始化与T+1结算。旁览未创建账户金额为null；真实委托与成交继续读原PaperOrder，不用enabled/已有委托代替成交事实。

本轮不新增或替换行情源，不升级质量，不增加研究样本写者。首次加入是现有持久证据的读投影；真实前向覆盖、报价可用性、后续涨跌和端到端SLO仍须原任务取证。字段消费者与入口取舍见[细功能审计§42](../product/feature-closure-audit.md#42-imp-088-原地旁览与首次加入记录的入口覆盖2026-10-09)。

---

## 8. 全量数据源清单与使用度审计（2026-09-16）

> **速览（30 秒）**：当前共 **8 个行情/数据外部源 + 2 类本地存储 + 2 个非行情外部服务**，
> 使用度**极不均衡**：同花顺 fuyao **23 / 59** 端点（39%）、通达信 TDX **4 / 20** 数据类方法（20%）、
> akshare **9 / 11**（2 项零调用）。2026-09-16 的实测更正见 §8.9：
> ths 财务指标**全通**（原记「全失败」）；2026-09-16 跌停池 4 只与快照逐只一致，但 2026-09-24 与东财各 13 只仅共同 11 只（§8.6②），不得泛化为全市场同口径。
> **最明确的一处「改用更优质源」= 逐笔成交**：现役东财 `push2his` 在本机**恒 502**
> （复测更正：不是"通而空"，是 3/3 WAF 快速失败），而 TDX `get_transactions` 走 TCP 直连、
> 不经 HTTP、不受 WAF 影响 —— ✅ **已于 2026-09-16 落地为降级备源（`IMP-038`，§8.6①）**。
> **不建议接入**：ths 基金 28 端点（与短线题材定位无关）、`ExTdxClient` 扩展市场（港股/美股/期货）。
> ⚠️ **本节不另立任务清单**：未完成项一律登记在账本 §6.0（`kb/07 §3.3`）。
> 以上是 2026-09-16 的目录快照，不是永久的端点总数或接入目标；2026-09-26 对消费者的结论见 §8.10。
> akshare 是适配层，其东财池与直接调用东财属于同根；不能将表中入口数相加为独立上游数。

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
| 5 | **通达信 TDX** | `app/market/tdx_kline.py` · `app/market/minute_backfill.py` · `app/services/heatmap_service.py` · `app/market/tdx_tick.py` | 无（TCP 7709） | 日K·分钟K·行业板块·板块成分·逐笔降级 | **4 / 20** 数据类方法 |
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

**② 跌停池 —— 双源候选，先核覆盖口径再接入**

- 现役：`app/data_providers/eastmoney.py` 的 `get_limit_down_pool`（`push2ex`，4 个消费方）。
- 候选：ths `special-data/limit-down-pool`（**代码未封装** —— `ths.py` 无此方法）。
- 2026-09-16 实测：ths 跌停池 **4 只** vs 全市场快照自算 **4 只**，**逐只一致、池 ⊆ 快照且零例外**；此为当日有限样本。
- **2026-09-24 同日复测**：ths 与东财各 13 只，但仅共同 11 只；ths 独有两只 ST（002650、002856），东财独有两只北交所（920025、920229）。共同项价格/涨跌幅对齐，覆盖却不能互替。ths 端点仍未封装，**当前不改现役链路**；补缺须先定义交易所/ST 的范围、缺字段与部分覆盖反馈。
- **日期真实性**：2026-09-25 休市，ths 返回 0，东财对该日请求回退到 09-24 的 13 条；东财 `qdate` 是最新更新日而非历史请求日的证明。`/api/limit-down` 已在 Provider 调用前用交易日历拒绝已知休市、对未覆盖/不可用日期返回 503；不能把回退行标成请求日。2026-08-28 的原始 1 对 4 样本保留于比较文档 §2.4，不再泛化成“ths 永远漏报”。

**③ 财务 / 估值 —— 官方 REST 候选，暂不替代现役字段**

- 现役：`app/data_providers/eastmoney.py` 的 `get_financials`（3 个消费方）。
- 候选：ths `financials/{indicators,income-statements,balance-sheets,cash-flow-statements}`
  + `valuations/snapshot` —— **本轮实测全通**（§8.3），且是官方 REST 而非页面接口。
- 价值：ths 侧是有业务码与显式字段名的官方 REST；但 HTTP 成功不等于字段可互换。
  2026-09-26 同期实源复核见 §8.10：茅台 2026 中报 THS `operating_income` 与东财
  `TOTAL_OPERATE_INCOME` 不同口径；估值也未完成同刻、同股与有效时点验证。
  现役东财财务保留，已修正返回报告期上限并公开公告日期。

**④ 全市场快照 —— 盘后候选，不预先指定权威源**

- 现役：腾讯 1Hz 轮询（实时）+ 东财。
- 候选：ths `prices/snapshot` 全市场分页（**实测 5,573 只，0.09s/页，`limit=1000` 可用**）。
- 用途：**不替换实时链路**（ths 配额 + 8s 超时不进秒级链）。现有
  `snapshot_service → sina_market` 已消费全市场宽度、并落 Parquet；
  `sync_marketdb.py` 又以 THS dump 建本地日线库。增加 THS 全市场快照前，须比较
  同日完整代码集、源时间、配额与现役消费者需求，避免第三套无主定稿事实。

**⑤ 分钟 K / 分时 —— 无更优源（诚实边界）**

- 现役：腾讯当日分时 + TDX 历史 1min（≈94 交易日）/ 5min（≈495 交易日）。
- ths 官方 capability-map **明确声明不覆盖**分钟 K 与 tick ⇒ **不存在"更优的免费源"可换**。
  TDX `get_tick_chart`（分时含历史）是**唯一可能的增量**，但属「补深度」而非「换更优」。

### 8.7 未充分利用但具挖掘价值（按价值排序）

| 序 | 对象 | 为什么值得 | 现状 |
|---|---|---|---|
| 1 | ~~TDX `get_transactions` 逐笔~~ | 填补「现役源本机不可用」的真空 | ✅ **已接入**（`IMP-038`，见 §8.6①） |
| 2 | ths `financials/*` + `valuations/snapshot` | 官方 REST 可供核对；营收定义有差异 | 暂不切流，见 §8.10 |
| 3 | ths `limit-down-pool` | 局部覆盖候选，不能消除北交所缺口 | 暂不切流，见 §8.6②/§8.10 |
| 4 | ths `prices/snapshot` 全市场 | 与现役宽度快照/marketdb 对照 | 暂不增第三事实，见 §8.6④/§8.10 |
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

### TradingAgents显式研究构包（2026-10-21）

W04/RSH-030的正常研究消费者复用本页已登记来源，不新增源或改变全局优先级。构包是用户显式研究任务的一步，普通报告GET不抓取、不调用模型、不写样本。来源可用、图完成、结论正确和净收益准入分别验证。

| 输入 | 既有owner与边界 | 缺失或失败 |
| --- | --- | --- |
| 公司依据 | 已验证公开profile的发行人PDF摘录；保留原件/页码/quote hash与实际checked_at | 无profile在模型前阻断；不隐式重抓或把短摘录当全文 |
| 快照 | 当前Hub纯缓存，准确证券/市场、可信质量及真实data_timestamp/received_at；价量/区间/有限性重验 | 陈旧、缺源钟、低质或错证券拒用；不请求报价或以接收钟补源钟 |
| 日线 | 按已配置顺序选择首个具有接口的免费Tencent/Eastmoney实例，单次读取；400日历日内最多300行qfq，源/复权/股量/元额分别绑定 | 失败不自动换源重试；形成中当日条、raw回退、混复权、重复日期、错身份及异常价量拒用；不证明完整交易日历、历史首次发布或独立最终性 |
| 财务 | 既有Eastmoney RPT_LICO_FN_CPD最多8期摘要；report_date/notice_date、CNY/每股/百分数分列 | 缺公告日保持未知；不是完整三张报表或已独立核实正式版/预告优先序 |
| 新闻 | 现有EventStore近7日最多30条候选，只读同事务原始公开标题/摘要、最新有效版本及原content hash | 待修订/撤回/错版本/缺钟/非公开文本拒用；题材卡或模型解释不能补source_symbols，零条不代表公司无新闻 |

原版13种指标在隔离worker内对同一冻结OHLCV计算，沿固定上游公式和初始化记录，不请求外部行情；不足200根时SMA200缺失。较短初始化不是五年预热，有限指标可算不证明日线真值或投资效果。实际接收时刻/源时刻/公司资料时刻各自保留；有新采集时用真实采集完成钟，不把新数据回填旧profile截点。语义来源版本排除纯采集nonce用于已完成报告复用，仍绑定原数据、质量、口径和可用性变化；partial/unknown不重发。

本次固定stockstats 0.6.9源码的MFI是0–1资金流比率，前14条0.5是初始化，不是实测中性资金。上游原说明的80/20与此实际实现不一致。适配器仅在准确依赖版本及模块SHA匹配时向工具结果和共同指标表注明量纲；不乘100改原值，不改原图/公式。0.8/0.2仅为相同尺度换算，不是新增交易阈值或投资信号；其他实现量纲未核则明确未知。原13项说明/公式的有界源码核对不是独立数值真值、收益或策略验证。

单股真实构包取得261根日线和8期摘要，原13公式208个可比值一致；完整原生图与接续零外部模型证明属于工程证据。新四公司整图/单模型对照另冻同一原始数据与全部原公式结果，不把原149次实验算成已用此链。社交、账户、完整财表及缺失源钟不由模型补造；当前质量准入、共享预算和默认关闭边界见[运行说明](../system/deployment.md#tradingagents完整研究运行边界)。

同一原表另给`latest_session_projection`：实际历史会话日期、同日原OHLCV收盘及13项具名原字符串/None。没有历史时全部明确缺失；不会借前日值、SMA50或采集日补当前收盘。入口重算该投影并核原表/原始帧及回执哈希，重绑外层哈希也不能让错日/错列通过。完整历史、公式及原值保留；这是表示与身份修补，尚不能证明模型已读对或净提升。

### 8.10 IMP-040 整项能力处置（2026-09-26，只读实源 + 代码消费者）

本节按**能力与消费者**定主备，不按 2026-09-16 的 59 端点目录决定开发量。
本机已配置 THS Key 的只读调用证明以下 capability 对**该账户当次**可访问；
官方以 `code=0` 为业务成功、`2001` 为无效 Key、`2003` 为无权访问，HTTP 200 不代表权限成功。
Key 对应的再分发/商用许可、额度与长期 SLA 未取得条款证据，均记**未知**；
不提交原始响应、凭据，也不为候选能力开通付费权限。

| 能力、实际消费者 | 现役主源 / 备源或缺口 | 实测分母、身份与字段 | 本轮处置与重议条件 |
|---|---|---|---|
| 跌停池：市场 API、助手、扩展对照 | 东财 push2ex；THS 候选仅局部覆盖，akshare 跌停包装同根东财 | 09-16 两源 4/4 同集；09-24 两源各 13、共同 11，THS 独有 2 ST、东财独有 2 北交所；共同项价/涨幅逐只相符。09-25 休市东财回退 09-24 行 | **保留东财**；三入口同用交易日历日期闸，未知 503/明确休市 422，助手不读假池；扩展对照按实际返回行公开 `*_sources`，空源保持未知。若后续增加 THS 备源，须按 SH/SZ/BJ、ST 与字段逐只列失配并显式报告部分覆盖 |
| 涨停原因：题材/助手 | THS 主、东财池备（原因缺失） | 09-24 THS 51/51 行有原因；东财 0/52，共同 51，东财独有 `920748`；`reason` 是标签/归因而非公告事实 | 维持主备；无原因时保持未知，不把东财空原因补成推断。若 THS 异常，按当日代码集与原因缺失率反馈降级 |
| 财务：详情/助手/选股基础面 | 东财 `RPT_LICO_FN_CPD`；THS 四端点仅候选，无可互换备源 | 09-26 对茅台/平安银行 2026 中报，THS indicators **2/2**、东财 **2/2** 业务成功；茅台 THS 营业收入 907.03 亿元/同比 1.469869%，东财 `TOTAL_OPERATE_INCOME` 922.78 亿元/同比 1.300099%。净利同比 THS -1.951595% 对东财 -1.95%，ROE 同为 16.75%。平安银行同比两侧约 1.7756%、ROE 同为 5.22%，毛利率两侧缺失 | **不切流/不混列**；东财 `periods=8` 实抓 103/122 行，现由 Provider 去重后截到所请求期数。`report_date` 明确为报告期末，`notice_date` 取东财 `NOTICE_DATE`，详情和助手展示。要换 THS，先定 `OPERATE_INCOME` 与 `TOTAL_OPERATE_INCOME`、单位 CNY/百分数、公告可见时刻、缺失字段与评分阈值，做同期多股多期影子验证 |
| 估值：行情快照填充/基础面 | 腾讯 `fill_valuation`；THS `valuations/snapshot` 候选 | THS 两股 2/2 成功，茅台 PE TTM 18.989012、PB MRQ 6.154543；平安银行 5.045833/0.468348。响应 `timestamp` 为源给出的毫秒时间，非公司财报报告期 | **保留腾讯**；无同一时刻、同标的、同 PE/PB 定义与缺失率对照，不能用可达性切流；异常时保留 unknown 而非填 0 |
| 全市场宽度/盘后快照 | `snapshot_service → sina_market` 与 Parquet；marketdb 由 THS dump 构建 | 既有 THS `prices/snapshot` 分页探针 5573 只、0.09s/页是历史样本；尚无与当期全市场主链同刻、全集、源时点的受控对照 | **不建第三套定稿事实**；遇现役完整性/时效不达标，再以同刻代码集、字段、请求数/配额、版本及落盘恢复比较后决定 |
| 搜索/排行/竞价/异动与分钟级补数 | 腾讯 smartbox/东财 suggest；东财排行；THS 竞价/异动；腾讯/TDX 分钟线 | THS `meta/tickers/search`、TDX `get_stock_quotes_list` 等只是候选方法；当前无同范围失败分母与具体失效消费者，THS 官方能力图不覆盖分钟 K/tick | **暂不增加入口**；现役消费者实际失败或缺关键字段时，按当前源、许可、延迟、预算和跨交易所覆盖重做对照。TDX 另协议族需单列连接与运维成本 |

THS 的三个接入面仍有不同运行边界：Provider 的已接方法通过 Composite 的
方法级预算/熔断/health；`theme_catalog_service.py` 的板块成员直连由本服务校验业务码、
成员完整性与持久写保护，但不进入 Composite 视图；`sync_marketdb.py` 的导出直连是
离线批处理，其成功以脚本校验/持久库状态为准，不表示在线行情 healthy。
这两个直连面不是本任务的“多两个独立源”，也不能因未进 Composite 就机械迁移：
其调用频率、失败反馈和恢复路径分别由原消费者维护。若要统一，先核具体消费者
和预算，不能把离线导出放到在线请求熔断链。

公开资料与实源证据边界：[THS 快速开始](https://fuyao.aicubes.cn/docs/quickstart/)
说明业务码/权限；[财务指标契约](https://fuyao.aicubes.cn/docs/api-reference/financial-indicators/)
说明 `report=YYYY-[1-4]`、百分比数值与 `null`；
[茅台 2026 半年报](https://static.cninfo.com.cn/finalpage/2026-08-15/1225475868.PDF)
提供报告口径核查。这里只证明 2026-09-26 的只读样本与当前消费者契约；
许可、长期可用率、盘中时效、跨全市场完整性与业务效果不由此样本证明。


### 8.11 腾讯HTTP后备与通达信离线日线包（2026-10-08）

- 报价/K线/分时科创量归一化由TencentProvider负责；分钟差分和均价使用股，盘口转换独立。现役单位证据见比较文档§14。
- 日/周腾讯K线有界分段，每段最多640条；分钟仍最近320条。三域名同源、不算三家供应商；失败冷却120秒，整次10秒预算，身份/序列格式/重复/非正复权价失败可见。无前复权序列时原价标medium并说明，不混合不同复权基础跨段返回。API日期按北京交易日构造，保留已有时间戳形态。
- 2026-10-29明确K线来源口径：`price_basis`只由源构造按实际返回序列标为`qfq/hfq/none/unknown`，未知不冒前复权，源失败不靠改口径掩盖。前端历史页只在证券、来源、复权口径相同且历史重叠OHLC一致时拼接；复权未知、换源、口径变化或历史重叠冲突须明确拒混。公共K线接口仍沿原`start/end`及单页`limit≤1000`，不改变腾讯每段640等源能力上限；空页、取源失败或暂不可用不证明已到上市。历史至最新同口径有效K收盘的比较是价格变化，不另计交易费用或现金分红，不当作交易收益。
- HTTP分笔只在原Provider链及TDX未给出结果后执行，两处消费者（行情API、助手成交工具）共用备链；开关`ASHARE_TRADES_TENCENT_HTTP_FALLBACK_ENABLED`默认true，测试强制false。最新200笔内，定位尾页，不遍历全天；4秒/24请求/255尾页上限，3秒TTL单飞/64键，缓存保留原received_at且复制返回。身份、页码、序号、源日期跨日和价格量额校验失败，不返回旧尾或空市场冒充成功。有效空、接口不支持的证券与源故障仍按备链规则区分；北交所/指数不支持不把原有效空变成502，既有链/TDX真故障仍保留。失败写日志，逐行source/quality及前台口径明确HTTP聚合非L2；不声明全天完整。
- 离线CLI：`backend/.venv/bin/python backend/scripts/archive_tdx_daily.py --date 2026-09-30`。可加`--package 本地.zip`离线输入；`--verify`核已存文件。默认根`data/parquet/archives/tdx_daily`，不写线上marketdb、不自动调度。仅按沪深北A股代码前缀取有价记录，排除ETF/指数/债券；不是经官方上市历史验证的全市场分母。
- 归档原价不复权，股/元；zip原文、Parquet及manifest单目录原子发布，保留两文件SHA256、获取/归档时间和各市场筛选分母。本地旧文件输入的HTTP获取时间为unknown（null），不得把归档时间冒充当时可见时间；历史归档`point_in_time=false`。日期/块长度/身份/有限数/市场成员缺失拒绝，404表示未发布/休市/超保留期的未定缺包，不能声称空市场；同日期同hash可复用，修订hash冲突不静默覆盖。
- 公告/新闻增源尚无同输入增量证据，沿用东财及既有官方全文核验，不创建无消费者接口。未来具体缺口在原owner重议；付费、模型、通知、策略与交易规则不变。


### 8.12 涨停归因与默认交易日读取（2026-10-10）

- 未指定日期的涨跌停查询走原交易日历最近已形成的交易日，页面保留返回trade_date。显式涨停日复用与跌停同一日历事实闸：休市422、未覆盖/不可用503，不接受上游静默回退再改日标签。
- 原THS涨停reason与record.source/trade_date通过涨停池、题材梯队、概念详情、选股盘中和接力榜透传；无reason的来源明确缺失。源归因不等已核实的上涨因果，题材名称不能代替原因。
- 所有证券详情复用同一原因读者，quote.data_timestamp的北京日与池日相同才解释本次行情。无源日期、错日、未入池、读取失败与缺因分别展示，不借received_at或现价回填。
- pool API按交易日有界60秒TTL/单飞，命中保留原meta.generated_at并标cached，返回副本不修改共享证据；客户端仅合并最新池在途读取。没有新源、模型调用或交易规则改变。


### 2026-10-13 涨停原因观察的来源边界

THS等源已提供的同日原因可作为独立`reason_observations`旁览；EM等当前源没有字段时不以行业/旧源原句填补当前reason。观察按证券/交易日/来源有界保留，携原观察时点；不等于当前在池、公司受益或涨停因果。现存涨停池60秒缓存与源优先级保持，附加观察86400秒保留按日键防跨日误用，2日/每日4096上限及进程退出缺失明确。它不新增Provider、数据库或第二套业务事实源。

### 2026-10-14 炸板完整性与源钟倒退恢复

炸板EM要求rc为非bool整数0且data.pool为显式数组，tc与有效身份数量完整；THS逐页校验code/data/items/pagination，总量/页次漂移、坏身份/重复/空尾不能静默当合法空。合法空仍成功。qdate或ready/接收钟不自动证明上游实际交易日，未知日期边界不补造。Composite跌停/炸板复用同一合法空与deadline控制，炸板原NON-REALTIME预算12秒，不走无预算的allow_empty。

详情可选include_breaks=true；默认涨停GET行为不扩充。up/break独立原60秒单飞，合并响应不再套第二层TTL；每域覆盖/失败保留，正向同日观察只证明曾触板，不证明当前在板。原原因观察不是资格凭据。

实时4秒原总预算内，仅主源同市场唯一证券源钟明确倒退，才尝试既有优先级的其余源。恢复截止取原总预算与本轮poll_interval剩余空闲的较早者，无空闲不另开预算；正常行先推进，慢备源实际取消，不挤占下一轮主源。轮频/全局预算配置不改，但不保证慢备源获得完整4秒。备源须通过原价格/身份门、可靠同日源钟、非未来、严格新于保留值及原源龄窗口；不改变原质量标记。正常路径不额外请求，缺价/未知钟等不盲重试，所有备源失败保留旧报价并如实stale。该修补不能保证全部免费源长期稳定，也不替代长期多窗口度量。


2026-10-14 IMP-019剩余请求验证：`ThsFuyaoProvider.get_quotes/get_indices`当前固定显式thscodes模式，依据[当日四源对照](data-source-comparison.md#15-当前机器四源盘中对照2026-10-14)的45行源钟缺失及脱敏合同fixture，声明这两个方法不能承担source-clock recovery。此声明只作用于已明确倒退后的恢复支路；普通价格备源照常，其他方法/未知能力仍按既有链尝试。若官方端点、模式或字段合同变化，原Provider owner须重新实抓逐行可靠源钟并复核声明；不以接收时点/名称表加载时点补钟，不按`ths`名称全源屏蔽。相关反例包含未知能力、可恢复其他方法和普通fallback，原预算/源序/freshness保持。


### 2026-10-16 官方概念身份与同步约束

同花顺原始涨停原因标签、官方概念名称/代码/成分、研究推断是三种证据，不互相替写。取消未经验证的近义词合并；未分类不构成题材。成分重叠只保留导航，候选共同驱动须原始标签与目录同名且全部支持股均为官方成员。官方标签仍是来源判断，不等于因果已证实。

完整概念目录沿既有事件收集周期检查，目录24小时更新与成员原TTL分离，合并并发同步。业务码失败、空目录、缺字段或重复代码不覆盖旧目录；目录缺席条目保留历史行并退出当前归属。未来成员时间排除当前归属并进入原重试路径；目录更新不刷新成员时钟。目录/成员成功更新使已有倒排与机会缓存失效，不新增轮询。

10月16日一次同窗实抓：同花顺37只/35只有原因，东财39只且仅行业字段，无原因字段；字段覆盖不当准确率，单次延迟不当稳定性排名。官方目录390项，本地990001.TI与测试样例一致，写入来源未知；原始恢复证据留本地，不能把旧季度概念与异常测试行一并删除。执行状态归W04/BUG-034。

### 2026-10-20 公司原文与经营传导

经营解释的原始来源为CNINFO披露PDF（static.cninfo.com.cn/finalpage），不是新增行情provider或东财F10替代。首批显式CLI使用预审文档计划、最多6份的限制保留为旧操作方式；下节已增加通用自动发现及正常PDF依赖，不再要求每次手填页码或由宿主工具采集。两种方式都绑定身份页、页内短引文、原件SHA、接收时刻和报告期，服务GET只读。公告/半年报中的计划、客户、子项目、收入确认与现金流不得互相替写，来源自述不当独立事实/效果证明。

### 2026-10-20 公司依据常规管线

`app/events/company_sources.py`调用CNINFO公开网页的`new/information/topSearch/query`和`new/hisAnnouncement/query`，先以六位代码与A股/orgId精确消歧，再从官方static附件核完整法定名与精确代码同页、URL/披露日、原始SHA和实收钟。法定后缀覆盖“股份有限公司”和“股份公司”；000559正式原件第7页的后一写法已复验，简称或异页代码仍不能替代完整身份。网页参数column/type不作为交易所身份。定期报告550日窗和近期经营公告45日窗各取前30条，经营报告主位优先可用的年报/半年报全文，先按覆盖期、再按同期间披露/修订时间排序；无合格全文才取季度。独立report-policy版本只控制资料缓存，未变文档仍复用原句子抽取/模型回执；实际财务章节标题之后不作经营片段，目录/正文提及不触发且原物理页码不变；未定位或截断的边界保留未知。财务附注可能有真实业务事实，本次未采纳不等于不存在；最多一个报告加一个公告；有后续页明确部分覆盖，未找到不当公司没有业务或订单。午夜披露戳只按日精度，以当日23:59:59+08:00为保守可用上界，晚于as_of不采纳，不宣称已知首次可见时刻。

原件复用不可变接收记录与hash，不重复下载；漂移、symlink/恢复冲突、非PDF、重定向和错误响应拒绝。每份最多24MiB、350页；实际文本最多前70页、每页12000字符、总512Ki字符，每公司最多12条完整候选句，所有截断及缺类别显式保留。pypdf固定为6.21.1，在无服务凭据的限时子进程解析，限制CPU/输出并采样RSS；macOS采样保护不称OS安全沙箱。扫描件/身份未证/超限保持失败或未知，不伪造OCR。片段类别是抽取候选，不保证已证客户或新订单。

本次无凭据公共访问可用，不等许可API、稳定性/自动化配额或批量再分发许可。仅用于本机研究，不再分发原件；原始元数据、唯一PDF与恢复证据留忽略目录，页面/获准模型只消费有界公开短引文及链接。进程内至少1秒请求间隔、ready缓存6小时、missing 1小时、failed 30分钟是本地负载措施，不是官方额度；403/429/超时显式失败，不换代理或入口绕过。首次冻结10个新公司场景+1个既见回归取得13份原件、105条逐页精确引文；只证明该批素材和身份覆盖，未证明全市场、语义或选股效果。任务/读取和模型边界见[归因蓝图](../product/limit-up-attribution.md#2026-10-20-公司依据常规管线)。


### 2026-10-20 工作台身份与失败契约纠正

THS日K按身份选择官方路径：股票用`/api/a-share/prices/historical`，显式沪深指数用`/api/a-share-index/prices/historical`；指数1d不发送adjust，返回thscode/interval/adjust须匹配。裸000001保留股票000001.SZ语义，中央目录外或市场冲突的显式指数在本Provider拒绝并沿原链回退，不伪装成股票。官方PriceBarItem.volume单位为股。一次000001.SH实抓成功，399006.SZ返回429即停止；这不证明六指数当前实源均可用，股票原兼容字段也不冒充新增严格验证。

板块clist同源双域均失败时，沿原有界缓存保存30秒None/degraded，命中不续龄；期满按原单飞重试，成功恢复原30/300秒缓存。不发布空成功、不延长有效行情、不把异口径板块资金字段拼接填空。新浪全集保留缺页/重复/总数一致硬门；整批先识别456限流再抛普通分页错误，错误带页码和类型。仅HH:MM:SS的行钟与接收时点不能补成已证源日期。实测分母、429及尚未证实的时效见[对照§17](data-source-comparison.md#17-工作台数据故障核对2026-10-20)。


### 2026-10-21 直取时段与入选研究前置

Tencent指数、个股及盘口只复用同一在飞原始行；完成后再读仍发新请求，不增加价格TTL。原始重复指数/擅增身份保留给原拒绝门，合法superset请求的额外证券不泄漏到当前消费者。单股的指定源/缓存未命中出口与Hub共用证券/市场、源年龄和日历展示装饰；指数前缀先正确识别再核裸码，真实低质量、未知源钟和过期不因休市被升级。

2026-10-23纠偏：晚到、非法或缺源钟的新观测仍拒绝，当前值及其源钟不推进；但不能据此把另一份仍可信的当前值立即标为stale。当前值必须自身为high、只有正常时段上下文、有明确且不晚于展示时刻的源钟，并仍在原`stale_after`内，才保留原质量。暂停解释仅在可信日历确认closed、源日期等于现役`expected_source_date`且尾点达到`expected_source_after`并不晚于展示时刻时保留；只有`market_closed`／`off_session`上下文可走该路径，不为竞价缺尾点新造例外。股票、指数与REST `present_quote`共用合同；`source_rejections`、coverage和真实缺项继续报告，原源钟与fresh window均不延长。超过原窗、未知钟、低质量和其他真实异常仍降级告警。工程与实际加载分别按本轮发布回执核验，不把保留当前事实写成上游观测已恢复。

真正入选后完整研究缺公司profile时先复用既有免费原文采集及唯一下载，不先调用旧公司selector；来源失败仍在模型前明确缺项。行情/财务/公开消息与当前冻结截点沿原构包契约；免费数据不是全量覆盖保证，普通GET不采集或生成。


### 2026-10-23 现有观察复用与搜索字段纠错

指数成员关系仍取同花顺原有标准指数成分接口并缓存6h。涨跌幅只关联已有MarketSnapshotService原子版本及缺项的Hub被动观察，不为该页面新增供应商请求或订阅整个指数；来源/源钟/观察钟/质量及已知数量单列，成员时间不代表行情时间。非法数值/错市场/重复身份或坏钟不进入排序，未知不造0；这是已存观察的排序，不保证全体实时完备。

Tencent搜索原始五列为市场、六位代码、Unicode证券名称、拼音简称、类型；旧解析错读前两列而丢弃正确结果。既有源本身支持中文、缩写及全拼，本批只修搜索解码与身份；不新增数据源、拼音依赖或改变Tencent报价/构包函数。空匹配与网络/形状失败分开；实际免费来源只读证据和红绿反例保存在本批忽略产物，发布和加载按handoff及准确HEAD回执核验。对冲读取的主源/备源失败竞态修补和竞价叠加等待边界见[运行报告§34](../system/runtime-performance.md#34-2026-10-23-被拒观测与当前事实分离)。

### 2026-10-23 全市场普通源失败与备源边界

新浪精确计数/去重完整性门继续执行；现役批量行情备源除了限流，亦可在ProviderError/httpx失败且当前快照unavailable/stale时尝试一次。已有新鲜degraded不重复全量补取。备源只复用既有股票名单，动态事实来自本轮合格报价，90%覆盖、盘中新鲜度和真实源钟均不放松；缺项不填旧值，失败不冒充有效空或完整快照。普通故障不写成WAF限流。采集期限与请求/共享owner边界见[性能§35](../system/runtime-performance.md#35-2026-10-23-全市场漏页与成员冷启动承接)，没有新增数据商、单股订阅或收费来源。


## 2026-10-25 官方宏观RSS与只读新闻补源

已核美联储[官方RSS](https://www.federalreserve.gov/feeds/feeds.htm)及[使用说明](https://www.federalreserve.gov/disclaimer.htm)：一般正文属public domain，须注明来源，第三方内容/徽标不在本次复制范围。仅固定货币政策RSS，沿原flash loop每900秒读取；不跟跳转或正文外链，512KiB/30项/近30日，HTTP总截止8秒；重复原ID/hash幂等，修订追加，源发布/接收/可用时刻分开，不补历史首见。`official_news_enabled`可停用；`source_snapshot`只读运行健康和feed窗口，不冒全历史。

此源零股票/题材方向，仅宏观事实读者；自动LLM候选明确排除，原选股评分与通知资格不变。HTTP200实抓不证明长期可用。金十[官方MCP](https://mcp.jin10.com/app/doc.html)存在，新闻持久接入按[专用许可复核](#2026-10-25-金十新闻接入许可复核)排除；“需Token”只描述MCP，不能外推为官网所有浏览方式；BLS RSS本次403，ECB MID内容偏支付系统且不匹配消费者，均排除。原行情源顺序、费用/风控不变。


## 2026-10-25 金十新闻接入许可复核

用户要求复核TSP接线及免费、留存、展示条件后再接新闻。结论：**当前条款不允许本系统的自动采集→EventStore持久留存→重排阅读/复盘链路，不接入**。这取代此前笼统“许可未知”的待查状态；不声称金十全部服务收费或全面禁止AI。核验日期为2026-10-25。

| 路径 | 当前依据与边界 | 本系统决定 |
|---|---|---|
| 旧免费引用页 | [官方停服通知](https://south.jin10.com/example/websiteiframe.html)明确2025-12-01起停止引用页及关联快讯/日历接口；历史§3要求框架原页引用，§6禁止过滤、非临时存储、缓存和加工 | 排除；旧“永久免费”文案不能覆盖后续停服公告 |
| 官方MCP | [专用规则](https://www.jin10.com/privacy/index.html#/jin10/mcp_service)，版本2026-05-07：第三条2/3限制批量抓取及替代留存；第八条3直接禁止本地保存、批量复制、再传播和建库 | 即使个人、本机且免费，EventStore建库仍不符合；不制作待启用适配器 |
| MCP的AI用途 | 专用规则第一条限个人非商业查询；第三条6禁止训练/微调/蒸馏/向量化及知识库存储；第三条7和第六条限制自动交易建议/信号/个性化策略。第五条承认第三方AI理解、总结、翻译的事实，不能概括成禁止全部AI | 临时个人查询与持久新闻消费者分开；未配置、激活或调用MCP |
| 在本系统重排展示 | [现役用户协议](https://www.jin10.com/privacy/index.html#/jin10/agreement)，更新/生效2026-01-23：§6.1限制未经书面许可在源页之外编辑整理展示；现役知识产权为§9.1，不沿用旧快讯协议§10.1节号 | 本系统摘要、分类、检索及复盘留存不能借官网可阅读而取得授权 |
| 免费价格与配额 | [接入指南](https://mcp.jin10.com/app/doc.html)只明确登录、激活及账号绑定Token；本次公开规则/激活页未给出免费期限、稳定配额或价格承诺 | 费用证据不够，不断言收费或永久免费；留存禁令独立成立，不以取得Token替代许可 |

官方规则页的公开正文接口本次HTTP200，未使用Authorization/cookie、未生成凭据或同意服务。MCP文档ID为`oM3wZ0romThFLb8Ya7Tkg`，正文`data.content`按UTF-8计算SHA-256为`6af473c76b08ebfd6b60e62aac59ef3cea145e7b4050f7ec15db7941165bbb2b`；用户协议ID为`v89OhLWdtioonVIbtJwLQ`，对应哈希`a061b2386c05ff1439357433fc6be519d8c4c81e3bd2d65597eb99384d490b56`。这固定本次公开版本，不承诺以后条款不变。仅保存许可回执与必要摘要，不采新闻、不复制整篇条款或请求/context日志。

TSP仅指用户给定的本机版本：`server/src/index.js`的两处金十`connected:true`是硬编码；数据源成功钟及延迟同样即时构造。新闻路由读取`seed/news_flash.json`静态副本，20条均标财联社；源矩阵原种子为403，不能证明原站接线。原远端站是否另有金十实现仍不清楚。没有可迁移的金十采集链，不能把演示状态、HTTP可读或第三方开源包装许可当成新闻授权。

重开条件：金十提供明确覆盖目标消费者、自动采集、留存期限、数据库/恢复副本、重排展示及拟用AI用途的另行授权，并证明无需启用付费，才沿现有flash→EventStore→新闻读者做有界实抓和行为验证。未获该授权前不抓网页/未文档化快讯接口，不用第三方转接规避。既有东财快讯和美联储RSS继续独立运行；旧宏观研究的来源与许可属于原研究范围，本轮不重采、不删除，也不把MCP专用规则直接推定为所有历史接口协议。


## 2026-10-25 选股分类发现与原名单对照

沿用THS24小时热股、当前官方目录、东财板块L2资金及其成员，不新增数据源。热榜ts来自提供方timestamp，不等于A股固定交易日；主题只覆盖榜内热股×已知目录。新归属投影最多200只/两次本地SELECT，source_as_of未知，content_version只证内容，requested_trade_date只指名单上下文，不能当历史成分版本。

资金榜新增received_at记实际共享源读取完成钟，缓存命中不改写；source_as_of仍未知，coverage_state=unknown不称全市场完整覆盖。source_mode标primary/delayed/unknown，不变available/排序或资格；20日沿原as_of与quality并标local_sample。只在打开类别/成分时按需读取；代码空间BK与THS分开，不按同名拼成因果或历史依据。消费者与失败验收见[猎场分类](../product/hunting-decision-design.md#2026-10-25-选股分类发现与原名单对照)。


2026-10-25同轮实页补修：`latest_saved`显式读取原L2的daily收盘Top50存档，行业/概念各取最近已完成且非未来的同日快照，读路径不外呼、不同步、不写档。归档日`as_of`、原保存钟`stored_at`、未知`source_as_of=null`分别保留；`historical=true`、`quality=stale`、`coverage_state=local_sample`，全集数量未知。Top50原按当日主力净流入截取，只可对这个样本按原涨跌幅/净额浏览，不推断完整市场或无净流出。当前榜失败仍保留，用户主动切换最近存档，不把历史伪装为即时恢复；BK下钻另读当前成员，不能将其当原归档日成员。缺档、坏结构、错身份、未来/未完成日期和缺值分别保守反馈。

## 2026-10-25 正式板块身份与按需研究

本轮没有新数据商或付费源。复用同花顺正式概念目录与原同源`tag=industry`目录、有限`thscodes`批量指数快照；实抓行业320条与三个已核正式概念快照，HTTP200/业务码整数0，名称代码来自目录，不能从网页路径编号猜TI代码。

同花顺目录本来含融资/股通等宽标签；来源正式身份不等于热门题材性质或官方主线排名。热股集中度只表示本次热股样本/有效成分总数，本系统计算，不是源方热榜。缺失/过期/未来成员分母留null；人工override不能进入官方榜。原原因、正式目录与成员重叠分别保留。

快照的data.timestamp是数据就绪钟，不代替行情时点；本次显式批请求实际返回该字段，旧‘显式必为null’只适用当时合同，不覆盖本次事实。`source_ready_at`与received_at分开，`source_as_of`仍未知；价格<=0、未来就绪钟保留未知。业务码False/0.0或残留坏行拒绝。板块日K date_ms按北京交易日解释并截于所选日，不能用最新K线核历史。

东财资金即时双域不可用仍保留真实失败；正式行业/概念目录不再依赖它，也不以同花顺报价冒充东财主力净额。旧Top50/20日有限存档继续明确历史与覆盖。复盘只读已有情绪汇总缺原始行hash/源钟，不能冒点时样本或补齐031；金十许可排除及旧安全拒绝不变。

## 2026-10-26 独立异动统计资金视角

原东财clist双域仍实测在响应前断开，失败不抹除。沿同数据商公开板块异动产品新增独立阅读视角，官网页面/原站JS标明`zjl`为主力净流入（万元），转换到页面原币及亿元；不宣称数值等同f62，不替换评分/策略输入。公开访问不等于上游数据再分发许可，原始响应仅进本机忽略证据，公共仓只保留契约和脱敏小夹具，不发布原始数据库。

本次21页共1015行同一`dt=20261026150000`，原批次钟与接收钟分开；正式目录128行业、516概念、31地域，精确代码/名称核得124行业、516概念，地域31排除，其余344身份未归属并计数，不按名称或成员猜归属。完整源批不是已认证的全市场最强资金榜；正式行业仍缺4项，概念/行业身份不合并。混批、重复、冲突、未来钟和非有限数拒收。

默认资金阅读显式标出独立产品，原`intraday/5d/10d`链接保持原合同。最近已存单日复用原10月23日Top50有限存档，二十日历史继续标原覆盖与陈旧，不暗换今天。成功档保留源hash/目录hash及日期，归档不触碰原f62数组；失败后不能只重读旧档就称来源恢复。人工fresh实际回源，闭市后台失败退避不放宽数据时效。

异动统计盘中源龄按原300秒窗口逐响应重算，接收钟/缓存命中不能续龄；超窗保留数值但标stale。当天15:00闭市批显式作为收盘观察而非即时，quality=stale/historical=true；源钟为整批，逐行时点未认证。

## 2026-10-26 新浪独立板块资金与成员

> **历史采用说明**：本节保留10月26日独立新浪整链的定义与实测。本批唯一市场资金入口默认改为[东财官网全集](#2026-10-27-东财官网全集资金与成员)，新浪仍可独立选择；不改写原48/181分类、62/112成员及当时源故障事实。

用户允许采用其他真实来源。本轮新增的是新浪独立只读资金方向，原board_flow仍为唯一L2 owner；不透明替换东财f62、不混BK/THS/Sina成员身份。旧“非f62同义字段”限制仅阻止混算，不能据此拒绝一套明确标源的完整产品。

官方[资金页脚本](https://n.sinaimg.cn/finance/cnstock/pc/zjlx.z.js?ver=1.1)声明板块`MoneyFlow.ssl_bkzj_bk`/`ssc_bkzj_bk`和成员`ssl_bkzj_ssggzj`/`ssc_bkzj_ssggzj`，接口根为新浪quotes_service；cate_type/category原身份完整保留。netamount为全部净额（元÷1e8展示亿元），ratioamount/changeratio为比例×100；r0_net仅成员/日线主力，[官方帮助](https://finance.sina.com.cn/temp/guest4377.shtml)定义单笔≥100万元，Level1通常延迟5–10分钟，非东财“超大＋大”同义。源未给当前榜/成员源秒钟，质量不得升级实时；原opendate日标签独立保留。

真实验证行业48/48、概念181/181，电力62/62及锂电池112/112成员，金额与本源榜/当日日线勾稽一致。两例零报价2/9不丢成员，不补零价格或零涨幅。完整性由前后计数、分页长度、原身份唯一性/顺序校验；有界4并发、22秒整轮、单页512KiB、最多80页。正常缓存60秒、失败15秒并显式失败，人工重试可重新取源；暖读不改源钟/接收钟。读路径不另建同步器或写交易、选股、研究、通知数据。对应产品及实测归[猎场](../product/hunting-decision-design.md#2026-10-26-新浪资金方向与同源成员)、handoff和本批PR。

## 2026-10-27 东财官网全集资金与成员

用户最终要求资金浏览只留市场板块资金。唯一主入口为`/market?tab=fund`，沿原`board_flow`唯一L2 owner默认读取东财官网全集；Discovery撤除重复资金入口。官网本次范围为516概念、128行业，完整成员按所选BK读取；旧多日、归档和异动视角保留原合同。新浪旧能力/历史与同花顺目录不因入口收敛改写身份。日常Notebook下载导入和全THS成员净额聚合探索均已停止，不增加新metric/source框架、同步器或资金/选股写入者。工程、准确发布与运行分别按本批PR实际回执记录。

[东财概念资金页](https://data.eastmoney.com/bkzj/gn.html)的公开脚本明载同源`/dataapi/bkzj/getbkzj`。普通无账户GET已取得`code=m:90+t:3`的516/516概念，与本次官网目录的BK身份和名称精确相合；联合字段请求同样返回516行。正式成员过滤`code=b:BK1184`真实返回人形机器人314/314行，`(f13,f12)`唯一；这是一例完整成员验证，不把它外推成所有板块都已逐个验收。不是重放原失败clist、执行挑战、迁移cookie或伪造签名后的结果；旧clist响应前断开仍保留为该入口的失败。

本批使用`key=f62,f3,f2,f124,f152`：`f62`为源方主力净额人民币元，展示亿元时除以1e8；原始`f3`除以100得到百分数，`f2`依本行`f152`小数位转换价格。`f124`只承载报价时钟，不能作为资金更新时钟。资金源日期/秒钟仍未知，HTTP Date、接收钟及`full=1`也不能补成源钟或实时质量证明。接口未给出的净额占比、大小单分项、5/10/20日资金、排名变化等保留null，不填零、不借其他视角补齐。

市场原资金页复用区间参数`range=website`；成员复用原成员读路由的`view=website`并显式绑定`kind`，不新增一级页面。来源、类别、BK及股票市场身份、源总数/实际行数/唯一身份、接收钟、资金源钟未知和缺项随返回保留。计数不等、重复、错身份、失败或坏字段不能包装成完整空成功；缺报价保留原成员与资金。缓存与在飞复用按本视角/类别/原身份隔离，失败和人工fresh独立处理，命中不刷新来源时间；不得用新浪或旧东财缓存冒充本视角成功。市场保留原布局，官网成员提供名称/代码搜索、涨幅双向排序、增量加载与显式fresh；工程反例、完整本地门、准确HEAD发布、实际加载及页面验证分别验收，不能由HTTP200提前推定。

同花顺边界同步更正：现有Fuyao正式目录390项与成员可读；当前公开文档虽列`/api/a-share/capital-flow/historical`及`snapshot`，却明确客户端限定、不开放外部集成，而且是单只A股资金，不是概念资金。本批没有调用该限定端点。官网初始概念页只收到50行；正常免费产品分支首请求401 Chameleon、旧详情403后停止，不能把首屏外推成完整资金全集，也不将具体拒绝归因为所有同花顺服务均不可用。

已登录SuperMind普通免费研究实测一股一日9/9资金字段，并取得2026-10-26概念表1201行；其中450项有TI，现有Fuyao390/390可按`concept_thscode`精确连接，保留3处同码异名，751项无TI不按名称补造。网站已收50行可按`concept_symbol`精确连接，不能推定未收到的其他资金页。该证据证明有限身份桥和平台内研究能力，不证明PIT修订钟、官网全集资金或无人值守服务接口。用户已否定日常下载承接，本批不将Notebook原件、手工JSON或跨源拼接资金接入应用。

原始公开页面/脚本、请求回执、body哈希和离线覆盖核对保存在本机忽略的`artifacts/runs/ths-complete-funds-20261027`，其中`eastmoney-public-contract/getbkzj-concept-f62-coverage.json`和`getbkzj-member-BK1184-combined-receipt.json`分别承载概念覆盖与成员联合字段。早于它们的“等待登录/成员未验证”快照只保留历史身份。本批TSP对照资金路由仅是10月23日静态种子，不能充当实时取源或已闭环证据。该读侧变更不进入策略、风控、选股评分、研究、通知或模拟资格，也不证明持续SLO。

旧`browse=funds`仅迁移到市场资金读者：保留可识别的类别、区间和准确BK；新浪、THS及未知代码保留原source+code提示并暂停不匹配读取，由用户明确选择官网后才进入官网目录。选股仍使用THS390目录，各模块原source+code不改，不把EM板块钱贴到同名THS概念，不按名称把TI/Sina转换成BK。个股正式THS题材chip保留代码/来源/类别的完整目标；未知公司标签或归因文本继续作为线索，不能伪装正式概念。旁览必须承接完整deep-link，不能只按模块名开默认页而丢失对象或参数；无法承接时保留正常导航。


2026-10-27源龄恢复纠错：合法但超龄且已接纳的股票/指数行，可在原恢复剩余预算内尝试合格备用；只在可信日历连续交易生效，跨竞价再次核阶段。原10秒源龄、同日/未来/身份/质量/单调门和provider熔断均不放松，合法旧备用单列观测拒绝。该修补没有新源、价格TTL或接收钟续龄；具体反证与限制见[性能§40](../system/runtime-performance.md#40-2026-10-27-竞价个股分时与指数叠加分离)。全市场缺页及degraded资格是另一合同，不以本修补宣称全部恢复。


2026-10-27全市场采集纠错：原新浪hs_a分页HTTP200也可能给非list或短页，整批缺行会进入真实降级。原owner用当前poll_interval作为整轮截止，count/原页/有界修复共用；最多6并发，仅短list/瞬时传输故障的坏页同轮重试一次，额外页不超原并发，成功页不重读。401/403/456、挑战、坏JSON、非法或重复身份不重试；已观察456不能被同波超时掩盖。最终raw/规范化计数与身份全唯一完整才原子发布，缺页、degraded和原退避不放宽；该修补不能保证14:53缺200的未知原body必被修复。

stock_universe仅由完整正常主轮冻结hs_a身份，按同北京日/非未来验证钟返回独立只读副本；fallback不建立或改写。股票消费者核身份与报价质量分别执行，不借报价接收钟续身份日期，不按前缀补股票类型。合同及案例归[猎场股票身份](../product/hunting-decision-design.md#2026-10-27-股票身份与逐阶段候选留痕)。


2026-10-27正式涨幅读取：原`/api/themes/catalog/quotes`增加显式`scope=directory&kind=concept|industry`，与codes范围互斥，原codes<=50不变。按正式同花顺目录版本取原指数快照pct，不由成员估算；目录/返回/有效涨幅覆盖独立，坏批保留null。15秒总界内按50码批读；只有整数code0且整包/逐行合法才继续，非0应用错误、坏结构/JSON和HTTP拒绝（含456）停止后续批，不绕拒绝取源。原source_as_of/trade_date未知，接收钟与source_ready范围不升级为交易源钟。现有源行业目录未给层级体系，名称相近不合并。


同批THS报价规范化反例：源`price_change_ratio_pct`缺失或null保持未知，不以`or 0`补平盘；实际0/合法正负数保留原两位四舍五入口径。股票/指数HTTP合同分别验证，顶层返回钟继续不补逐行源钟。正式目录原生实抓为概念390/390、行业320/320（含有效涨幅）；这是一次可读观察，未知行情交易日不升级。

## 2026-10-27 正式目录映射与原始标签

同花顺涨停原因中的原始交易标签与正式概念、行业目录是不同事实。共享解析器复用原`read_board_catalog(concept|industry)`，不新建数据源、别名字典、收费能力或数据库目录副本。正式身份键为source+kind+code；完整联合范围中只有唯一精确原名才能映射。目录版本由实际两类内容及目录记录绑定，observed_at是系统观察钟，不是源行情时点或历史成员生效日。源码与读者合同见[联合映射](../product/hunting-decision-design.md#2026-10-27-同源目录联合映射与历史参照)。

现主库仅持久概念，行业沿既有独立目录缓存，不能声称数据库已存710条。已有冻结390概念/320行业中，汽车零部件精确对应industry 881126.TI；具身智能与“羊”字辈在此范围无精确同名，保留原因原文，不替换为人形机器人或机器人概念。55个已存标签的审计仅来自Top10卡片/21股票（源汇总池42）；9项精确对应及46未对应不是全市场标签全集，也不证明当时成分、可成交或预测效果。

正式目录身份、官方成分、指数快照/区间、来源原因、自有强度分别验收。当前参考不回填旧报告或入选记录；目录失败/未就绪、同名歧义、没有对应和历史未保存凭据分别标注。原行情源钟缺失、资金源口径与许可限制仍保留。

2026-10-28慢读承接只观察上节同源目录的原在途结果：completion_key绑定service/revision/attempt，完成GET只cache/原future，不新增源、Token、价量取数或分类身份。原provider预算与目录TTL不变，前端一次共享等待，不逐标签重拉。旧报告/原入选只更新旁列当前参考，不能补当时成员/源钟或指标；失败与无对应分开。协议见[性能完成承接](../system/runtime-performance.md#2026-10-28-目录慢读完成承接)。

## 2026-10-28 研究簇来源与证据分层

研究簇复用现役同花顺涨停池全部原始原因及共享目录，不新增供应商、Token、模型或日常下载。正式source/kind/code/name、原标签/证券、来源日期/可核源钟、接收钟、规范化来源行哈希、目录版本、覆盖与缺失分别保存；该哈希不是供应商原始响应字节哈希，数据就绪钟不能充行情源钟、首次公开时间或历史成员生效日。原池完整仅表示涨停池读取完整，不能称完整题材宇宙。

结构依据可引用公开产业文件，并记录原文链接/发布时间/适用范围；当期共同驱动另核实际事件及公司联系，不把宏观产业体系写成个股订单。原原因可能为供应商AI摘要，只是来源归因材料；羊字辈等保留为交易叙事候选，不按同音造成员，不伪造TI/BK。目录未知、无唯一映射和正式身份成立分别反馈。

v1证据清单只登记人形机器人产业结构关系，其他原始交易标签只形成候选，没有通用产业归类或当期驱动自动晋级。扩展必须补真实证据、更新清单/方法版本及对应实现，经正常PR/审核/发布生效；本批来源可读和存档不证明全部题材已完成共同驱动研究。

当前投影经`GET /api/themes/research-clusters`读取并形成不可变研究观察版本，存放忽略的`backend/data/research_clusters/`；历史只读原版本，不重采或重写旧选股/报告。关系依据和自算去重统计不冒官方板块数据；不平均板块涨幅、累加重合资金、计算缺全分母的胜率。完整字段、关系准入、复核和失败语义归[唯一产品合同](../product/hunting-decision-design.md#2026-10-28-有据研究簇与原始题材)，准确实测及发布另按本批回执核验。

## 2026-10-29 报价字段与备源身份核验

腾讯原始响应先核header代码与正文代码，单证券/盘口重复身份拒绝，不以调用方前缀改名掩盖错股。已有在飞请求复用和请求量不变，指数重复行仍交原Hub身份门。补价只认同证券/同市场、明确且非未来的源钟与接收钟、可信质量及有限数值；只填缺项，不改主源价格、质量、源钟。当天限价不得跨日借用，估值两源钟差沿原60秒窗口，正常闭市不因墙钟推进而制造新的源观察；负PE/PB是真实可能值，不改正数。

`field_sources`仅给实际补入的六项估值/市值/限价记录原source/data_timestamp/received_at；不把腾讯补充归成主源原生。前端继承字段时同时继承该来源，不跨证券/市场/源日，把来源与截至时间作为指标按需说明，原布局与请求数量不变。旧记录无字段来源则保留字段级未知，不回填；界面可另行标注“行来源”，不冒充该字段原生来源。

全市场fallback在计覆盖前核请求key、原宇宙证券/市场、真实Quote、正有限价、可信质量和源钟。连续交易及明确交易日的竞价/开盘等待沿原新鲜度，正常闭市沿Hub原日历源日/尾点，unknown不推定闭市；合格行仍须90%，整体仍degraded，不建立或刷新主源股票宇宙。缺失行不沿旧动态值，来源钟/接收钟与整批就绪钟分开。新浪NaN/Inf保留缺失，金额汇总溢出拒发布，旧有效版本不被破坏。上游真实缺页/限流仍可失败，不宣称免费源永不延迟。

### 2026-10-29 历史范围与本地忙分层

腾讯历史已验证的非空、身份/值/复权一致响应可能不覆盖所请求的旧区间；此时仍失败且历史完整性未知，不写成功空集或历史尽头，不增加全源故障计数，也不清除原有失败。空包、业务错误、坏值继续按真实源失败处理。共享读取本地满额只返回明确本地忙，不因未出源请求错误熔断或扩散备源；原合法并发结果不丢弃。行情自选成员同步诊断独立于源质量/源龄，不能当行情刷新钟。
