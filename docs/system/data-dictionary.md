# 数据字典

所有主要数据对象必带审计字段：`source`（来源）、`quality`（high/medium/low/stale/invalid）、
`quality_reasons`（触发规则）、`received_at`（接收时间）；SQLite 业务表另带
`created_at / updated_at / data_timestamp / version`。

## Quote（行情快照）

| 字段 | 类型 | 说明 |
|---|---|---|
| symbol | str | 6 位代码 |
| market | str | SH / SZ / BJ |
| price/open/high/low/prev_close | float? | 元；停牌为 null |
| change / change_pct | float? | 元 / 百分点 |
| volume | float? | **股**（源为手，Normalizer ×100） |
| amount | float? | 元 |
| turnover_rate | float? | 百分点 |
| data_timestamp | datetime? | 行情时间戳（UTC 存储，展示转本地） |

## OrderBook / OrderBookLevel

`bids` 按价降序、`asks` 按价升序，各 5 档；level = {price, volume(股)}。
校验：买一价 ≥ 卖一价为非法（crossed）、档位乱序为非法。

## Kline

`timeframe`（1m…1w）、`ts`（UTC）、`open/high/low/close/volume(股)/amount/change_pct/turnover_rate`。
Eastmoney 源前复权（fqt=1）；日/周 K **固定前复权**，系统**不提供复权方式切换**——
`/api/kline/{symbol}` 无 `adjust` 参数，marketdb 也只物化 `close_adj`（前复权收盘）。
原 `/api/market/adjustment-events/{symbol}` 端点已于 2026-09-07 健康度审查删除
（全仓 0 引用的死端点）；provider 层 `get_adjustment_events` 作为数据能力保留
（复权事件流是 marketdb 因子推算的地基）。

## Trade

`ts / price / volume(股) / side(buy|sell|neutral)`。

## LimitUpRecord（涨停池）

`price / change_pct / first_seal_time / last_seal_time / break_count(炸板) / seal_amount(元) /
turnover_rate / consecutive_boards(连板) / boards_stat("3天2板") / trade_date`。

## LongHuRecord（龙虎榜）

`close / change_pct / turnover_rate / amount(榜内成交额) / net_buy / buy_amount / sell_amount /
reason(上榜原因 EXPLAIN) / trade_date`。

### 龙虎榜口径规则（2026-09-10 并入，原 longhu.md）

- **上榜阈值必须配置化驱动**，不得假设所有板块同一规则：±7% 偏离 / 15% 振幅 / 20% 换手 / 三日 ±20% / 退市整理 / ST 异动 …
- **席位分类 8 类**：机构专用 / 沪股通 / 深股通 / 知名游资 / 一线游资 / 普通营业部 / 量化席位 / 不明席位——**允许人工修正，修正必须写审计日志**
- 统计周期口径：历史 / 近 5 / 20 / 60 日 / 近 1 年
- 详情页表现口径：次日/3/5/10 日涨跌、成功率、平均与中位收益、最大收益、最大回撤、负收益比例

## SymbolSearchItem

`symbol / name / market / source / is_realtime`（搜索结果永不标记实时）。

## WatchlistItem（SQLite）

`symbol(唯一) / name / note / source / quality / version + 审计字段`。

## OpportunityDecisionSnapshot / OpportunityOutcomeLabel（SQLite）

- `opportunity_decision_snapshot` 是 append-only 的逐时点决策证据：`run_id / snapshot_id /
  trade_date / as_of / scenario / stage(candidate|hard_gate|rank|notification) / symbol /
  source_theme / decision / rank / strategy_version / feature_version / data_state /
  entry_price / evidence(JSON)`。被过滤标的同样逐股留存，缺行情必须记为 `unknown`，不得
  塌缩成“未入选”。
- `opportunity_outcome_label` 与快照分表，避免盘后事实改写当时证据：`snapshot_id / horizon /
  target_date / state(pending|labeled|unknown) / label(positive|flat|negative|unknown) /
  reference_price / outcome_price / return_pct / reason / source / labeled_at`。
- 当前已接 `d0_close`；缺收盘价保持 `pending` 可重试，决策时价格缺失终结为 `unknown`，
  绝不以 0 收益代替。样本不足只报告覆盖率与事实分布，不触发策略晋级。

## 信号等级（§10/§11 · **设计稿，未实现**）

> ⚠️ 全仓**没有**按下面六个等级分档的实现。唯一近似物是 `services/dragon_service.py` 的
> `DRAGON_GRADES`（龙头相 / 强势候选 / 观察 / 杂毛·回避）——那是**龙头分级**，不是信号等级，
> 勿混。已实现的是**每日精选六维评分**（`picks/engine.py::synthesize`：子项加权合成综合分，
> `vetoes` 一票否决压制 ×0.4）与质量五档 / 置信度。

`（设计稿）观察 → 候选 → 强势候选 → 高风险强势候选 → 数据不足 → 禁止分析`；
每个评分必须输出：原始指标→公式→标准化→因子权重→贡献→总分→数据时间→来源→有效期→失效条件→风险项→质量→置信度。
