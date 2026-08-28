# MCP 工具体系 — full.md §16（Phase 7）

## 工具清单

```text
get_quote  get_kline  get_order_book  get_trades  get_market_overview  get_market_breadth
get_sentiment  get_limit_up_pool  get_limit_down_pool  get_longhu_records  get_capital_flow
get_board_rankings  get_financials  get_valuation  get_shareholders  get_restricted_shares
get_margin_data  get_block_trades  get_news  get_announcements  get_macro_data
run_factor_screen  run_backtest  get_portfolio  get_paper_account  create_alert
get_research_notes
```

当前已实现可直接映射的后端能力：get_quote / get_kline / get_order_book / get_trades /
get_market_overview（部分字段）/ get_limit_up_pool / get_longhu_records。
其余工具随对应模块落地；**先有 API 契约再做 MCP 封装**，两者共用 Service 层。

## 统一返回信封

```json
{
  "success": true,
  "data": {},
  "data_timestamp": null,
  "received_at": null,
  "source": "",
  "quality": "high",
  "is_realtime": false,
  "warnings": []
}
```

- `quality ∈ {low, stale, invalid}` 时 success 仍可为 true，但 warnings 必须说明，
  由调用方（AI Agent）决定是否可用；AI 默认不得使用低质量数据生成实时判断。
- mock 数据源时 `source="mock"`、`is_realtime=false`，并在 warnings 中提示演示数据。

## 权限与审计

- 每个工具声明所需权限级别（只读 / 计算 / 写业务库），写类工具（create_alert、run_backtest）
  需要在配置中显式开启。
- 全部工具调用写入 AI 分析审计日志（§18）：输入参数、数据时间戳、来源、耗时。

## Skills 工作流（与 MCP 并行）

盘前扫描、盘中异动扫描、涨停池复盘、龙虎榜复盘、板块轮动分析、基本面/技术面/资金面分析、
多 Agent 个股研究、情绪分析、组合风险检查、回测报告生成、盘后预测审计、新闻影响分析。
每个 Skill 必须定义：输入参数、所需数据、执行步骤、输出格式、数据不足处理、失败处理、
风险提示、是否允许实时使用、是否允许写入记忆。
