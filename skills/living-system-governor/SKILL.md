---
name: living-system-governor
description: 治理复杂长期系统的跨模块取舍、机制生命周期和重大决策传播。用于模块融合拆分、能力增减、工具选型或系统性反证；简单局部修复无需完整流程。
metadata:
  version: "1.3.0"
---

# Living System Governor

本 Skill 是上层判断协议。项目当前授权、阶段门、任务状态和发布条件分别以 AGENTS.md、docs/handoff.md、docs/retro-and-gaps.md 与所属 stage 为准；本 Skill 不产生第二账本或执行许可。先恢复当前代码、消费者和已合并成果，再做取舍。

## 核心判断

1. 写清用户任务、系统目标、真实消费者、当前基线和失败反馈。代码存在、菜单可点、模型给分、历史成功都不足以证明价值。
2. 比较 KEEP、最小 FIX、复用或 MERGE、受控 EXPERIMENT、WATCH、RETIRE；每项记录增量、反例、成本、迁移、恢复和重开条件。权限、资金、外发、数据质量与阶段门仍由确定性规则裁定。
3. 证据按设计、工程、隔离行为、真实运行、独立效果分别记录。成功、失败、漏选、误报、弃权、unknown 和未成交都进入分母；不得把较低层证据升级为生产效果。
4. 重要机制保留 owner、适用域、版本、复核触发、衰退信号、Champion/Challenger 和退出路径；稳定且无新证据时可以保持现状。

## 模块与组件边界

分别论证四种边界：导航负责用户去哪里完成任务；业务能力负责定义、计算、状态和事实 owner；组件视图负责呈现；W/G/P 阶段账本负责任务归属、门序与优先级。合并入口不合并账户、数据事实、权限或运行循环；拆分入口也不自动新增业务 owner。

对模块融合、拆分、子功能增减和组件选型，使用 [决策卡](references/decision-protocol.md)：列出旧入口与候选归属、对象/时间/版本/账户 scope、输入与消费者、失败及恢复；比较保留、修补、复用、替换和不采用。核许可、安全、兼容、迁移与维护成本。具体导航候选、名称、数量、风格和库版本从所属项目现行产品蓝图读取，不在通用核心冻结；本项目的 B/C 比较见 docs/product/product-closure-design.md。

后台接管前先证明触发、单写者、幂等、持久结果、失败/取消/重试/恢复、权限、预算和维护入口。用户命令、风险/错误/过期提示与纯前端交互按实际用途保留；关闭页面后继续推进的承诺要用运行证据验收。研发 Jev 共审、实际采纳和股票业务效果分别留证；不足或分歧可弃权。

## 工作与传播

重大取舍先做有界 U49 反证：自锁、双事实源、顺序/部分失败、幂等、unknown、权限、动态状态、测试固化坏行为和陈旧指针。发现归原 owner/stage；不借治理跨门。形成决策后按 docs/plan-registry.md §1.1 核总方案、专题、INDEX、stage、AGENTS、Skills 与 handoff 的影响面，逐项更新或说明不适用。当前授权、准确 HEAD 审核与发布门仍按协作规范执行。

### U49 主动缺陷发现门（Proactive Discovery Gate）与 U50

正常模式的 Preflight 和独立 Review、明确授权的 DEGRADED_FULL_CONTROL 模式之作者 DegradedRelease 均按当前 AGENTS、collaboration-workflow 与 handoff 的准确 HEAD 契约执行；本 Skill 不改写身份或放行条件。

## 按需参考

- [governance-handbook.md](references/governance-handbook.md)：原 §0–§19 的完整治理循环、领域扩展与 Skill 演进；复杂长期机制才读相关节。
- [decision-protocol.md](references/decision-protocol.md)：模块边界、组件和 KEEP/FIX/MERGE 等取舍的可复核决策卡。
- [audit-checklist.md](references/audit-checklist.md) 与 [anti-patterns.md](references/anti-patterns.md)：审计或反证时按需读。
- [AI-Trading 示例](examples/ai-trading-example.md)：历史经验与写法示例，不是当前状态、菜单、模型或授权的权威。
