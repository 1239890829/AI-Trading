# backtrader（mementum/backtrader）实跑发现（2026-09-13）

## 安装实测
pip 装 backtrader+matplotlib+pandas，零坑，<1 分钟。版本：0.1.x 系（仓 2023 年停更，无 breaking 变化风险即"不会再变"）。

## 架构核心
事件驱动（next() 逐 bar 回调）+ cerebro 引擎；feeds（PandasData 即插）、brokers（现金/保证金两套）、commission 可配置（万三实测过）、策略 params/indicators/observer 分层。**代码已冻结**——社区只有 fork 维持。

## 最小回测证据
合成 A 股样式 200 根日 K（含两次跳空涨停）+ 双均线策略：跑通，期末市值 99946.13（10 万本金），4 笔交易。佣金万三+滑点 5bp 配置生效。

## A 股适配核查（源码 grep）
- T+1：**零原生支持**（当日买次日卖需自写 cheat/状态机 hack）
- 涨跌停：**零原生支持**（comminfo.py 命中的"limit"是保证金限额，非价格边界）
- 整手：size 任意 int，100 股约束需策略层自觉
- 费用：✓ 佣金/滑点可配
- 范式：单标的逐 bar，与我们 paper engine + backtest.py 完全同域且我们原生 A 股约束更全

## License
**GPL-3.0**——私有部署不分发场景运行时使用风险低，但代码级引入（copy/改源）必须避免。

## 判定：不引入（重合且我们占优）
回测/撮合能力与我们现役完全重叠，且它无 A 股微观结构处理、已停更。无增量价值。唯一观察：`bt.Cerebro.optstrategy` 的多进程参数优化在它停更前就没适配好 pandas 2.x——不值得修。
