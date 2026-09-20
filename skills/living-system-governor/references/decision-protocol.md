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
