# Living System Governor Skill v1.1.0

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

## 自我进化

本 Skill 自 v1.1.0 起明确管理自己的演进：后续真实协作中持续观察用户稳定的判断方式、长期要求、重复纠偏和反例，但**不会把每一次临时要求都写进核心规则**。

进入 Skill 的方法必须先区分 Core / Domain Extension / Experience，并通过“是否稳定复现、是否改变真实判断、是否与现有原则重复或冲突、是否增加不必要上下文成本”的检查。每次实质变化保留版本、Git/PR 原因与可恢复历史；Skill 自身也允许 FIX / MERGE / RETIRE。

可核证明标准见 `SKILL.md` §19：不是靠聊天承诺，而是看候选是否被识别、为什么进入/不进入、是否形成版本差异和 Git 证据、后续是否真的改变判断。

## 一句话

> 计划永远演进，任务必须闭环；所有机制只有阶段性有效资格；变化由证据触发，而不是由“想显得更先进”触发。