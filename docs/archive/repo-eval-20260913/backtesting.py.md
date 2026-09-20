# backtesting.py（kernc/backtesting.py）实跑发现（2026-09-13）

## 安装实测
pip 一装即通，<1 分钟。活跃度：低频维护（~0.6.x）。

## 架构核心
单标的事件回测（next() 回调 + self.I 指标装饰器 + self.data 切片），**报告层是亮点**：stats 表自带 Win Rate / Profit Factor / Expectancy / **SQN** / Kelly / Avg Trade Duration，另有 bt.plot() 交互式 HTML 报告与 `bt.optimize()` 网格优化（multiprocessing）。

## 最小回测证据
同合成数据双均线：跑通，4 笔交易、Win Rate 25%、SQN -2.27、Expectancy -3.48%（随机游走合成数据上策略无效——**这正是指标层有用的证明**，SQN 直接告诉你别用）。

## A 股适配核查
- T+1：无（订单下一 bar open 成交，当日买卖无约束）
- 涨跌停：无
- 整手：无约束（size 任意）
- 费用：✓ commission+spread+margin 可配
- **硬伤：Backtest() 只收单标的 OHLCV**，无多标的组合语义

## License
**AGPL-3.0**——传染性最强档（网络服务即触发）。按 QuantMind 先例（AGPL 一票否决）：**只抄设计不引码**。

## 判定：不引入；抄一个东西
**SQN/Expectancy/Kelly 成套绩效指标进我们的战法核验报告**（strategy_verify / backtest.py 的 report 目前只有收益/回撤/p-value，SQN（系统质量数）与 Kelly 是低成本高信息量的补充——纯公式，无许可风险）。
