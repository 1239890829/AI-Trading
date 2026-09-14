# 计划文档登记表（Plan Registry）

> **定位**：查「某计划做了没、现在什么状态、去哪看」先看本表。
> **⚠️ 重要变更（2026-09-10 晚，用户指令）**：**计划文档不再长期保留**——方案落地后，精华提炼进 `docs/summary/` 或 `retro-and-gaps.md` §六，**原件即删除**（规范见 `kb/07-doc-curation.md` §3.2）。
> **新增待办一律写 `retro-and-gaps.md` §六（唯一明细账本）**，不再新建计划文档。本表转为**历史计划去向表**，供追溯。

## 一、历史计划去向

> ⚠️ **2026-09-14 更正**：本节原标题写「**全部已闭环**」，经全仓复查发现该断言**对 `docs/archive/` 的计划类文档为假**——
> 它们长期处于「已归档但**未销账**」的灰色地带（**未走** §三 的四步流程）。已补登记至
> `docs/retro-and-gaps.md` **§6.7-B**：`archive/assistant-optimization-plan.md`（P1/P2 共 14 项**状态未知**、头部仍写「未实施」）·
> `archive/plan-review.md`（Auditor 与 AI 结论有效期 / 监控告警）· `archive/minute-chart-plan.md` ·
> `archive/architecture-linkage-plan-2026-09-01.md` · `archive/full-project-review-2026-09-01.md`。
> **本表只覆盖 `docs/` 根目录的计划文档，不含 `archive/`**——引用「全部已闭环」前请先确认口径。

| # | 原计划文档 | 定稿 | 落地状态 | 精华去向 |
|---|---|---|---|---|
| 1 | ai-agent-console-plan.md（321 行） | 09-08 | 🟢 全落地 | `summary/ai-evolution.md` §1–3；断点已闭合（`services/agent_params.py` 补「建议→变更单→生效→回滚」） |
| 2 | ai-brain-plan.md（149 行） | 09-08 | 🟢 主体落地 | `summary/ai-evolution.md` |
| 3 | evolution-brain-plan.md（193 行） | 09-08 | 🟢 全落地 | `summary/ai-evolution.md` §2（议程 A/B/C + 30 日劣化回滚 + C 类沙箱） |
| 4 | strategy-evolution-plan.md（135 行） | 09-09 | 🟢 P0 全落地 / P2 延后 | `summary/ai-evolution.md` §4；延后项 → `retro` §6.3 P2-7 |
| 5 | trading-star-merge-plan-20260909.md（79 行） | 09-09 | 🟢 P0–P2 已执行 | `summary/ai-evolution.md` §5 |
| 6 | trading-agent-positioning-20260909.md | 09-09 | 🟢 已落地 | `summary/ai-evolution.md` §1（命名） |
| 7 | live-trading-guosen-plan.md（71 行） | 09-07 | ⚫ 搁置（用户决策） | **原位保留**（未来蓝图，恢复条件见文档头） |
| 8 | plan-review.md（270 行） | 08-31 | ⚫ 已归档 | `archive/plan-review.md` |
| 9 | fund-flow-redesign.md / hotspot-pipeline-design.md / news-event-module-redesign-20260909.md | 09-07~09 | 🟡 部分实施 | 已实施部分 → `summary/architecture-design.md`；**未做项 → `retro` §6.2/6.3** |

## 二、仍保留的计划类文档

| 文档 | 状态 | 说明 |
|---|---|---|
| `live-trading-guosen-plan.md` | ⚫ 搁置 | 用户不接受 Windows 依赖；保留作蓝图 |
| `retro-and-gaps.md` | 🟢 活 | **唯一待办账本**（§六 = 全量待办总账，40+ 项已按 P0/P1/P2 归集） |

## 三、规范（2026-09-10 起，长期遵循）

> **一句话**：**不为开发计划新建文档**；功能做完当轮「沉淀 → 登记 → 解引用 → 删除」，不排队、不堆叠。

**A. 功能点完成后的四步（当轮执行，见 `kb/07-doc-curation.md` §3.2 v2.0）**

```
① 沉淀  → 成果精华写进「已有的分类文档」：现役 L2 文档 / L0 KB 条目 / docs/summary/
          ⛔ 禁止新建文档承载成果
② 登记  → 没做完的部分写进 retro-and-gaps.md §六（唯一待办总账），每条带关键设计要点
           （阈值/口径/挂载点），避免删件后信息丢失
③ 解引用 → 全仓 grep 原件名 → 同步代码注释 / 文档指针（零断链）
④ 删除  → scripts/safe-trash.sh（可 --restore 找回）；INDEX 与 plan-registry 标「已删除 + 精华去向」
```

**保留例外（仅两类）**：① 用户明确要求保留；② 属**现役基线/数据**而非方案。
**「有未做项」不再是保留原件的理由**——提炼进总账即可；多份文档各写一半才是真正的阅读负担。

**B. 执行前的强制复核（防重复开发）**

3. **状态标注会随时间失真**：任何「未做/未实施」结论，**动手前必须 grep 代码复核**（09-10 两轮实测 **10 处偏差，8 处是低估完成度**——照单开发就会重复造轮子）。
4. **复核本身也要防假阴性**：一律用 **Grep 工具**，**不用 shell grep**（BSD grep 不支持 `\|`，模式会整体失效并静默返回空——KB-ENG-04 复现）。
5. **先查是否已有更好实现**：方案文档写「新建 X 模块」，但在方案落笔后可能已被更优实现取代（实例：P0-1 原设计要新建独立 tracker，实际 09-09 已落地 `exit_engine.py`，正确做法是复用挂载点）。

**C. 文档本身**

6. 每次文档变更同步本表与 `docs/INDEX.md`。
7. **执行完一批功能后跑一次 `kb/07-doc-curation.md` §8.2 月度体检**（超层阈值 / 摘要前置 / 未登记 / 同类聚集 / 孤岛 / ⏳ 超期）——文档治理是功能交付的**固定收尾步骤**，不是可选项。

## 关联

- 规则：`docs/kb/07-doc-curation.md`（分层模型 / 精华五要素 / 淘汰 / §3.2 完成态计划处置）
- 待办总账：`docs/retro-and-gaps.md` §六
- 文档总入口：`docs/INDEX.md`
- 历史归档：`docs/archive/README.md`
