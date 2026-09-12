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
Eastmoney 源前复权（fqt=1）；复权方式切换在 Phase 2 后期提供。

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

## 信号等级（Phase 5 评分系统，§10/§11）

`观察 → 候选 → 强势候选 → 高风险强势候选 → 数据不足 → 禁止分析`；
每个评分必须输出：原始指标→公式→标准化→因子权重→贡献→总分→数据时间→来源→有效期→失效条件→风险项→质量→置信度。
