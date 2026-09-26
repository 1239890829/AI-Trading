# Decision Protocol

## 一、决策卡模板

每个重要提议先填：

```text
Proposal:
Goal:
Observed facts:
Unknowns:
Consumers:
Current baseline:
Problem / gap:
Competing explanations:
Options:
  A. KEEP
  B. minimal FIX
  C. reuse / MERGE
  D. EXPERIMENT / redesign
Expected incremental value:
Evidence level:
Counterexamples:
Falsifier:
Risks:
Cost:
Rollback:
Owner:
Decision:
Next smallest slice:
Acceptance evidence:
Review / revisit trigger:
```

## 二、动作选择矩阵

| 情况 | 默认动作 |
|---|---|
| 当前机制仍有证据、成本合理、无更强反证 | KEEP |
| 目标正确但实现/契约明确有错 | FIX |
| 与其他机制重复且无独立增量 | MERGE |
| 假设有价值但证据不足 | EXPERIMENT |
| 当前不值得做，但未来条件可能改变 | WATCH |
| 无消费者/长期无增量/被替代/成本失衡 | RETIRE |
| 权限、费用、真实资金、安全、法律、不可逆风险 | ESCALATE |

## 三、新任务创建门槛

只有同时满足多数条件才新增任务：

- 不是已有任务的同义重复；
- 有真实目标和消费者；
- 问题有证据或可测假设；
- 不能通过当前任务最小扩展解决；
- 验收和退出条件可定义；
- 成本值得；
- 不会人为制造第二状态源。

否则优先并入、观察或不做。

## 四、模块边界与组件选型决策卡（U52–U55）

本项目已批准的规范来源为 `docs/implementation-plan.md` 与 `docs/product/product-closure-design.md`；任务状态仍归原 stage，本文不产生施工或发布授权。
在原决策卡内按需补充以下字段，不另建任务总表：

- 用户任务、旧入口与候选新归属；分别说明导航、业务能力、组件视图和账本阶段。
- 唯一事实负责人、输入对象/时间/版本/账户范围、结果消费者和失败恢复。
- 比较保持现状、最小修补、复用、局部重设计及不采用，记录各自反证和迁移成本。
- 后台接管先核触发、单写者、幂等、持久结果、权限、取消/重试、恢复和维护入口，再退出普通前台控制。
- JEV只提供有来源的候选判断；保留证据不足与分歧，建议、实际采用及后续效果分别记录。
- 以同任务的查找、错误、完成、键盘/焦点、响应和维护代价验收；模型置信度不代表体验或收益。
- 减少菜单、移走按钮、合并呈现、停用机制和删除数据分别论证；低频保护不能按使用率删除。

具体菜单名称只在产品蓝图维护；本卡不冻结模块数量，也不改变阶段门、权限或已有发布检查。
