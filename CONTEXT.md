# CONTEXT.md — 领域词汇表

本文件是项目统一语言（Ubiquitous Language）的唯一权威定义，随 grilling 会话即时更新；只放术语定义，不放实现细节。

## Language

### 持仓域

**Real Position（真实持仓）**:
用户在券商（如同花顺客户端）实际成交后，手工录入本系统的持仓记录。本系统不连接券商、不真实下单，真实持仓只是一份账本：按**实际成交价**记账，用于成本、市值与盈亏的跟踪。与模拟账户完全独立。

**Paper Account（模拟账户）**:
paper 引擎维护的虚拟资金账户，委托按市价撮合、受 T+1/涨跌停/整手/费用硬拦截。与真实持仓互不写入、互不影响。

**Fill Price（实际成交价）**:
用户在券商真实成交的价格（如 18.479 元）。真实持仓记账必须以此价入账，而不是当时的行情现价。

**Holdings Group（持仓分类）**:
自选板块中的一个派生视图：当前真实持仓的标的集合。它不要求标的出现在自选中。

**Trade Flow（成交流水）**: 真实持仓的每一笔买入或卖出记录（实际成交价、数量、日期），是账本的事实层，录入后不可篡改（可删除重录）。

**持仓视图（Position View）**: 由成交流水加权聚合出的当前持仓（数量、摊薄成本）。用户可直接覆盖其数量或总成本，覆盖后以覆盖值为准并在界面上标注「已手动修正」。

**已实现盈亏（Realized PnL）**: 卖出流水产生的盈亏 =（实际卖出价 − 卖出时点摊薄成本）× 卖出数量，只属于真实持仓域。

## Relationships

- Real Position 持有多个 Fill Price 记录（多次买入）；Paper Account 与 Real Position 无外键、无联动。
- Holdings Group ⊂ Real Position 的标的集合，独立于 Watchlist 分组。

## Example dialogue

- 用户：「我今天在券商以 18.479 买了共进股份 1000 股。」→ 开发：记入 Real Position，Fill Price=18.479，与 Paper Account 无关。
- 用户：「这个月模拟盘赚了 5000。」→ 开发：这是 Paper Account 的数字，不会出现在 Real Position 盈亏里。

## Avoid

- Real Position 的 _Avoid_: 模拟持仓、模拟账户持仓（那是 Paper Account 的东西）。
- Fill Price 的 _Avoid_: 现价、市价（Fill Price 是用户实际成交价，行情现价只是录入时的默认值）。
