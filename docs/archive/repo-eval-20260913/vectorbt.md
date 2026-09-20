# vectorbt（polakowo/vectorbt）实跑发现（2026-09-13）

## 安装实测
pip 安装成功，但**默认 plotly 6.x 不兼容**（"Bad property path: scattermapbox" 崩溃）——需 `pip install "plotly<6"` 钉版本。numba 首次编译有分钟级延迟（一次性成本）。

## 架构核心
**列向量化回测**：信号矩阵（entries/exits 各是一组列）一次喂给 numba 编译的撮合内核，100 组参数 = 100 列同时算完。Portfolio 对象提供 total_return/max_dd/trades 等全链指标，支持 fees/slippage/init_cash/size 逐列配置。

## 最小回测证据（关键数字）
- 单组合双均线：跑通（-7.95%，5 笔）
- **100 组参数网格：首跑 1.90s（含 numba JIT 编译），二跑 ≈0.00s**——对比：我们 replay_sweep 60 交易日 4 组参数是分钟级、backtest.py 网格是逐组循环。**数量级差异 2-3 个数量级。**
- 注意：free 版是 2021 后停更（vectorbt PRO 才是活跃版）；合成数据上最优组合 (3,10) +8.05% 是过拟合噪声，正好说明需要 walk-forward。

## A 股适配核查
- T+1：无原生（向量化撮合无持仓时间状态；可用 from_signals 的 `sl_stop`/方向限制模拟部分，严格 T+1 需自写 numba 内核或用 from_order_func）
- 涨跌停：无原生；**可在价格序列预处理层近似**（把超出边界的成交价 clip 到涨跌停价 + 用信号门控）
- 整手：无约束
- 费用：✓ fees/slippage/fixed_fees 逐列可配
- License：**Fair Code（Apache-2.0 + Commons Clause）**——内部使用/研究 OK，不可作为收费服务出售。对本项目（私有研究）无障碍。

## 判定：引入（pip 依赖，不改源）——本批最大价值点
落点：**参数扫描/网格与 walk-forward 的向量加速层**。我们的 replay_sweep（4 组参数分钟级）、backtest.py 单标的网格、P1-30 门槛回差扫描、P2-14 消融验证全是「同数据 × 多参数」形态，与 vbt 列向量化完美同构。前提：T+1/涨跌停在**输入预处理层**做（信号门控 + 价格 clip），撮合假设注明近似口径。风险：free 版停更——但它的角色是离线研究加速器，不是生产链。
