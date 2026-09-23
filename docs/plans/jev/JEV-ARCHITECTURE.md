# JEV Architecture

## 定位

JEV = Decision Explanation Layer。

JEV负责解释已有系统决策，不负责产生交易决策。

## 边界

负责：
- 决策形成解释
- 证据关联
- 不确定性整理
- 决策回放辅助

不负责：
- 修改选股评分
- 生成买卖信号
- 控制交易执行

## 数据关系

Events / Picks / Predict

→ OpportunityDecisionSnapshot

→ JEV解释

→ Review与Learning

## 原则

JEV作为增强层，不应成为核心交易链运行依赖。