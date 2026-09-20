# Example: AI-Trading 中如何应用本 Skill

这只是示例，不把本 Skill 绑定到某个仓库。

## 情境

用户提出：“猎场挑出来的个股如果只推荐不模拟成交，就很难知道真实准确率；盘中也不能开盘一次判断后全天冻结。”

## Governor 分解

### 目标
验证猎场的真实识别质量、可执行质量和实际模拟结果，而不是只看收盘后涨跌。

### 发现的问题层
- reference price 与 fill price 身份混淆；
- observed / actionable / filled 分母可能混淆；
- 盘中 decision version 不应冻结全天；
- 用未来最低价倒推“最佳买点”会产生 hindsight。

### 竞争方案
1. 保持只登记 reference；
2. 所有推荐都自动模拟买入；
3. reference 跟踪 + actionable 后独立 shadow execution；
4. 直接接真实券商。

### 裁定
选择 3：

- reference track 验证发现与时机；
- execution track 只对 actionable 同版重检后模拟提交；
- fill/no_fill/reject/expired/exit/fees/slippage 全留痕；
- 盘中新事实生成新 decision_version；
- 真实券商不进入该阶段。

### 证据门
不能因为 reference 后来涨停就称为“模拟交易胜率”；只有真实 shadow fill + 合法退出才进入可实现收益统计。

### 生命周期
即使某战法当前有效，也要按 rolling regime / MFE/MAE / fill / cost-adjusted outcome 持续复核；失效后进入降级、Challenger 或 Retire。

这展示了本 Skill 的核心：**先澄清身份与证据，再设计闭环；不是先堆功能。**
