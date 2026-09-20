# Living System Governor Skill v1.0.0

这是从长期复杂项目协作中蒸馏出的通用治理 Skill。

## 文件

- `SKILL.md`：主 Skill，可直接放入支持 Skill 的环境。
- `references/decision-protocol.md`：KEEP/FIX/MERGE/EXPERIMENT/WATCH/RETIRE/ESCALATE 决策协议。
- `references/audit-checklist.md`：重要改动前后的治理审计清单。
- `references/anti-patterns.md`：常见系统性误区与纠偏规则。
- `examples/ai-trading-example.md`：以 AI-Trading 场景演示方法如何落地。

## 推荐用法

### 作为通用 Skill

将整个 `living-system-governor/` 目录放入你的 Skill 目录，主入口为 `SKILL.md`。

### 作为项目级治理层

不要让本 Skill 替代项目自己的：

- AGENTS / Rules；
- 任务账本；
- 产品/架构文档；
- 风控/权限；
- 专业领域策略登记册。

它应作为**上层思考与治理协议**，要求各领域继续拥有自己的真实状态源。

## 一句话

> 计划永远演进，任务必须闭环；所有机制只有阶段性有效资格；变化由证据触发，而不是由“想显得更先进”触发。