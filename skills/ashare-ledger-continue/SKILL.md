---
name: ashare-ledger-continue
description: 按 AShare AI Trader 最终融合方案与阶段总账复核当前证据、选择切片并分批实施。用于继续项目工作。
---

# 接续项目

用 git rev-parse --show-toplevel 定位仓库，先读 AGENTS.md。文档入口 docs/INDEX.md；总账 docs/retro-and-gaps.md §6.0 链接十个阶段，任务只在所属阶段页维护。docs/handoff.md 只反映当前现场与最新验收。

1. 先刷新 GitHub、origin/master、在途 PR、工作区及未跟踪资产；从最新主干建独立 codex/* 分支，已有成果查代码与提交，不重复实现。
2. 阅读用户最终融合方案和对应附件，核对当前任务的真实消费者、证据、依赖及验收。旧编号可能已合并或退出，查 docs/archive/ledger-transition-20260917.md，不能照旧清单开工。
3. 按最终目标比较现状、最小修补和替代；设计、架构、机制、规则均可改进，但须显著受益、性能与机制不退化、风险可恢复。依据与限制写任务本页；没有真实缺口的候选不自动实施，发现高价值遗漏才取未用 ID 登记。
4. 简述切片、依据、验收和假设后实施。安全/凭据/付费/外发按具体授权判断，不因旧规则重复索取已给授权，也不扩权。目录退出严格按恢复清单和 MIG 顺序，未知资产保留。

按 AGENTS §1 验收：文档也影响后端守卫；新文件先精确暂存，迁移用干净检出验证；前后端全量串行。新守卫以缺陷注入证明能判红并恢复，不放宽断言。按 skills/ashare-task-handoff/SKILL.md 收口，状态不复制到总账或 handoff。
