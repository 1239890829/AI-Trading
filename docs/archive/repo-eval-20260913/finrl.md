# FinRL 评估笔记（repo-eval-20260913）

- 仓库：github.com/AI4Finance-Foundation/FinRL（浅克隆 commit 时间 2026-09，版本 0.3.8；PyPI wheel 为 0.3.7）
- 机器：macOS arm64 (Apple Silicon)，Python 3.11.12（/opt/homebrew python@3.11），venv 位于 `finrl/.venv`
- 评估日期：2026-09-13

## 1. 安装 / 评估方法（含两次失败实录）

1. `pip install finrl`（清华镜像）→ **表面成功实为空壳**：PyPI wheel 仅 127KB、**不带任何依赖**（`Successfully installed finrl-0.3.7`，requirements.txt 里的 torch/ray/alpaca 等一个都没装）。PyPI 发行物与仓库 setup.py（读 requirements.txt）不一致。
2. `pip install -e . --no-deps` → **失败**：pip 构建期执行 `finrl/__init__.py`，其急切 import 链 `__init__ → test/train/trade → meta.data_processor → processor_alpaca/processor_wrds`（alpaca、wrds 等重依赖不在最小集），构建即崩。
3. **最终走最小集路线**（任务指定的兜底）：`pip install torch stable-baselines3 gymnasium pandas numpy matplotlib`（清华镜像，全部 wheel 直装，含 torch 2.14.0 / sb3 2.9.0 / gymnasium 1.3.0 / pandas 3.0.5 / numpy 2.4.6 / matplotlib 3.11.2），再用 stub 绕过包初始化直接加载仓库真实 env 代码：
   ```python
   sys.modules["finrl"] = types.ModuleType("finrl")  # 注入 __path__ 指向 <repo>/finrl
   from finrl.meta.env_stock_trading.env_stocktrading import StockTradingEnv
   ```
   未安装的依赖：ray、elegantrl、alpaca、ccxt、yfinance、wrds 等（不影响 env+SB3 训练回路）。
4. 训练脚本：`finrl/run_min_ppo.py`（合成 1000 根单标的日 K，OHLCV + macd/rsi_30 两指标列，PPO 2000 timesteps 硬限制，然后各跑一个完整 episode）。完整输出：`finrl/run_min_ppo_output.txt`。

## 2. 架构核心

- 目录：`finrl/agents/`（stablebaselines3 / elegantrl / rllib / portfolio_optimization 四套 agent 封装）+ `finrl/meta/`（data_processors 9 个数据源处理器、env_stock_trading 6 个 env 变体、env_portfolio）+ `train.py/test.py/trade.py` 三个入口 + `config*.py`（内置 DOW_30_TICKER、INDICATORS、日期区间）。
- 核心资产 = **gymnasium 环境族**：`StockTradingEnv`（默认，保证金式现金账户）、`env_stocktrading_cashpenalty`（现金惩罚）、`env_stocktrading_stoploss`、`env_stock_papertrading`（对接 alpaca paper）、`env_stocktrading_np`（ElegantRL 用 numpy 态）。
- 数据流：data_processor（拉数+加指标）→ df[date, tic, open/high/low/close/volume, 指标列] → env 逐日 step → SB3/RLLib/ElegantRL 训练。
- 本质是「教学脚手架」：框架层薄，价值在 env 状态/动作设计与 demo 齐全（examples/ 大量 notebook）。

## 3. 训练证据（实测 2026-09-13，Apple Silicon CPU）

```
obs_space: (5,)  act_space: (1,)        # state = [cash, price, shares, macd, rsi]
| total_timesteps | 2048 |              # SB3 n_steps=2048 对齐（learn(2000) 一个 rollout 就收）
[TRAIN] total_timesteps=2000 wall=0.8s fps=2531
[EVAL:deterministic] episode steps=999 dates=2022-01-03..2025-10-31
[EVAL:deterministic] initial_asset=1,000,000.00 final_asset=830,637.91
[EVAL:deterministic] cumulative_return=-16.94%  trades=999  cost=728.48
[EVAL:sampled] final_asset=822,436.75 cumulative_return=-17.76% trades=989 cost=5,011.52  Sharpe: -0.384(env 自带打印)
```
- 结论：**环境接口形态 + 训练回路跑通**（PPO 训 2000 steps ≈1 秒，episode 999 步完整走完，撮合/费用/账本/收益输出齐全）。收益为负属预期（合成数据 + 2000 steps 远不够收敛），不代表策略质量。
- 踩坑记录：SB3 `DummyVecEnv` 在 done 时**自动 reset 并清空 env 的 asset_memory/rewards_memory**，事后读 env 拿到的是重置后空账本——评估要么走裸 env，要么逐步快照。

## 4. Reward 设计（读源码确认，env_stocktrading.py）

- **默认 reward = 账户净值一阶差分**：`self.reward = end_total_asset - begin_total_asset`（cash + Σ shares×price），再乘 `reward_scaling`（默认建议 1e-4，纯数值缩放不改排序）。**无风险调整项、无 Sharpe/回撤项**（portfolio_optimization env 才有方差项）。
- 交易成本：`buy_cost_pct/sell_cost_pct` 按成交额扣减现金；卖出仅当价格>0（缺失数据日不可卖）。
- 风险分支：`turbulence_threshold` 传入且当日 `turbulence` 列超阈 → **强制清仓**（actions=-hmax）且**禁止开新买**。turbulence 本身是美股 VIX 类波动率指标（risk_indicator_col 默认 "turbulence"）。

## 5. 美股假设核查（逐项）

| 项 | 现状 |
|---|---|
| 必需列 | date / tic / close + tech_indicator_list 列（open/high/low/volume 不参与撮合，仅指标可选） |
| turbulence | 可选参数（不传即无此假设），但机制本身是美股 VIX 类概念，无 A 股对应物 |
| 股数 | `actions*hmax` 后 `astype(int)`——整数股，**无 100 股整手概念**；hmax 为单标的单日最大股数 |
| T+1 | **无**：无持仓冻结期模型（日频 step 下天然隔日，但买入当日语义上可卖，只是每日只走一根 K） |
| 涨跌停 | **无**：任何价格都能全量成交，无限价、无拒单、无滑点模型 |
| 数据源 | processor_alpaca / wrds / yahoofinance（全美股）；processor_ccxt（加密）；**processor_joinquant（聚宽）是唯一 A 股相关处理器**，另有 processor_quantconnect / eodhd / sinopac（台股） |
| 内置标的/日历 | config 硬编码 DOW_30_TICKER + 美股日期区间，无交易日历概念（df 有几行走几天） |

## 6. License

- **MIT**（setup.py classifiers 与 LICENSE 文件均为 MIT，Copyright 2024 AI4Finance Foundation）。

## 7. 对本项目借鉴点（设计级，非引入）

- **不引入**：RL 策略无法给 basis（多层 MLP 输出连续动作向量，无任何可引用依据），与本仓「每处可解释输出带 basis」「禁止确定性买卖结论」两条红线正面冲突；且样本效率不可行——本仓全 A 约 2400 交易日，日频截面下 PPO 类 on-policy 算法通常要 1e5~1e6 steps（本次 2000 steps 连 1 个 rollout 都不满），等效于把全部历史重复回放几十遍，过拟合无法证伪；FinRL 全链又绑定 alpaca/wrds 等美股数据设施。
- **可借鉴的设计**：
  1. env 的**状态布局** [cash, 各标的价, 各标的持仓, 指标…] 与 **动作→hmax 归一缩放**：若未来做「组合层强化学习实验」（影子账户、不影响任何对外输出），这个接口形态是现成范式；
  2. **cashpenalty env** 的现金惩罚项：鼓励资金利用率的同时抑制满仓梭哈，可类比到本仓持仓集中度约束；
  3. **turbulence 驱动强制清仓**的机制形态（风险列超阈 → 撤买入+清仓）与本仓空仓闸门同构——本仓闸门的优势恰是规则可解释（逐条列因），这个对照值得写进 kb；
  4. `env_stock_papertrading.py` 展示了「env 包一层 paper broker API」的做法，与本仓 /api/paper 思路一致，无增量。
- 与本仓已有能力重叠度：本仓回测引擎（T+1/涨跌停/整手/费用硬拦截）+ 六维评分 + 跨日回放已覆盖 FinRL env 的撮合语义，且更贴近 A 股。FinRL 的定位应是「如果哪天要试 RL 预测层，env 契约怎么定」的参考，而非依赖。
