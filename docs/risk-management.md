# 风险管理

## 1. 风险拦截位置

```text
信号产生（评分/策略） → 风险引擎 → 模拟交易/提示
```

风险引擎（`backend/app/risk/`）在**信号/策略输出与模拟撮合之间**提供订单预检
（`RiskEngine.check_order`），输出 `{allowed, max_qty, reasons, warnings, state}`。

⚠️ **定位澄清（勿高估，2026-09-12 复核）**：`check_order` 目前只被 `POST /api/risk/check-order`
预检端点消费，**模拟交易路由并未强制调用它**——即它是「预检 / 提示」，尚不是撮合链上的硬拦截。
引擎模块头自述亦如此：「所有数值为**建议参数**，模拟交易当前只做提示与预检，不强制改写订单」。
把它描述成「任何 AI 或策略输出都不得绕过」与代码不符。

## 2. 市场状态驱动的仓位参数

**已实现 7 档**（`risk/state_classifier.py::classify_market_state`）：
强势多头 / 震荡偏多 / 震荡 / 震荡偏空 / 下跌趋势 / 恐慌·极端波动 / 数据不足。
判定输入 = 指数快照（上证涨跌幅）+ 市场宽度（涨跌比、涨停/跌停家数）+ 情绪引擎（相位/温度/置信度），
输出**状态 + 依据列表**（同情绪阶段判定要求）。

每档配置 **7 个字段**（`risk/config.py::PositionParams`）：`single_stock_max_pct`（单票上限）、
`total_position_max_pct`（总仓位上限）、`strategy_weights`（趋势/追涨/低吸/价值权重）、
`stop_loss_pct`（止损幅度）、`add_position_limit`（加仓限制）、`high_position_stock_limit`（高位股限制）、
`drawdown_protection_pct`（回撤保护线）。`数据不足` 档即保守默认值（`get_params` 对未知状态 fallback 到它）。

> ⚠️ 本文 2026-08-29 初稿曾列 **12 档**状态与 **8 个**配置字段（含「单一题材/行业暴露上限」）——
> 那是**设计设想**，代码中从未实现，勿按旧稿引用。

## 3. 硬性拦截规则（不可被覆盖）

- **第一阶段**：禁止连接真实券商、禁止真实下单（系统层面不提供接口）——红线 1。
- **红线一票否决**：ST、退市整理、异动/停牌风险等由 `app/picks/halt_risk.py` 评估为 `vetoes`，
  在选股 `synthesize` 阶段**压制综合分（×0.4）并禁买**；风控闸门（`app/picks/gate.py`）与
  买点筛选（`app/picks/buy_point.py`）据此拒单。
  **不存在名为「禁止交易名单」的独立表或接口**——旧稿的提法是设计设想。
- 组合回撤超过保护线：只减不加，直至人工解锁（`check_order` 第 5 项；缺 `initial_cash` 时
  显式告警「回撤保护未生效」，不静默放行）。
- 结论表述必须是**偏向 + 依据 + 失效条件**（红线 3）：已实现于隔夜偏向
  （`market/overnight_bias.py`）、技术评分（`market/tech_score.py`）、个股风险档位与出场纪律
  （`picks/risk.py`）等处。
- 撮合规则（T+1 / 涨跌停拒 / 整手 / 费用 / 停牌拒）是 `app/paper/` 的硬拦截，不可绕过。

### 3.1 尚未实现（勿当作现有能力）

- **`Auditor` 角色**：全仓代码中不存在（仅 `risk/engine.py` 模块头标注「未来可接入」）。
  盘后对照 T+1/3/5 表现目前由**复盘体系的实测模块**承担
  （`picks/backtest.py`、`picks/review_intraday.py`、`app/review/`），不是 Agent 角色。
- **AI 结论有效期与自动失效通知**：未实现（同上）。现有的是**事件/题材条目的 `expires_at`
  过滤**（`services/theme_catalog_service.py`），与「AI 结论自动过期」不是同一机制。
- **风险引擎对撮合的强制拦截**：见 §1 的定位澄清。

## 4. 数据质量联动

`quality ∈ {low, stale, invalid}` 的数据：AI 分析默认禁用、回测禁用、
前端必须展示风险标识（QualityBadge）。评分系统对数据质量降级自动扣分并降低置信度。
