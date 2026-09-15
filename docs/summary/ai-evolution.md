# AI 智能体与进化汇总（AI & Evolution Summary）

> **定位**：AI 控制台 / 自主进化 / 策略进化 / 仓库整合 / LLM 微调 的**已落地结论与待办**汇总。
> **现役不在此**：`llm-gateway-probe.md`（网关探针）、`review-agent.md`（复盘模块说明）。
> **整理**：2026-09-10。

---

## 1. 定位与命名（原 AI 控制台）

- 名称：**交易智能体 · 自主进化体**（前端 5 处文案已落地：导航 / 页标题 / 悬浮球上下文 / 返回 / 加载）。
- 结构：四个 tab（任务中心 / 复盘 / 提醒与告警 / 参数配置）+ 权限分级 **L0–L3**。
- 红线：不真实下单 / 不伪造实盘 / 凭据只在 `backend/.env` / 撮合规则硬拦截。

## 2. 自主执行机制（进化议程，默认关闭）

```
每日 15:45 → 收集十路证据 → LLM 裁决（严格 JSON，≤3 项）
  → 默认只输出建议议程；管理员显式开启后才分级执行：
     A 类 调参数（自动生效 + 30 日劣化自动回滚）
     B 类 改文档 / 写 KB
     C 类 改代码（worktree 沙箱 → LLM diff → apply --check → 全量门禁 → commit → ff-only 合并；每日 ≤1）
  → 后置守护：回归门禁 + 劣化回滚 + 红线清单 + 预算上限
```

- 安全默认：`ASHARE_AGENT_AUTONOMY_ENABLED=0`、`ASHARE_AGENT_CODE_CHANGE_ENABLED=0`。
  只有管理员显式设为 `1` 才能启用相应能力；C 类执行要求两个开关同时开启。
  （变量名以 `.env.example` 为准。**勘误 2026-09-14**：本文档与代码注释此前写作少 `_ENABLED` 的短名，
  按该名设置**根本不生效**——短名现已兼容但会告警，见 KB-ENG-76。）
- 停机语义：关闭时议程照常生成（降级为建议清单）；代码修改关闭时只允许建议、议程或 patch 预览，
  **不得修改工作区、提交或合并**。调度器的自动转正/自动回滚也随自治开关停止；
  元评估周报是显式例外（只产出可读 artifact，不改系统行为）。
- 十路证据含：复盘改进项（含 `repeat_pending` 重复未落实检测）、信号健康、告警统计、台账归因、KB 健康、数据健康、`framework_backlog`（06 框架演化日志）等。
- 设计要点：自治能力采用显式管理员 opt-in；未开启时系统保持只读建议模式。

## 3. 复盘闭环（含最后一公里）

- 链路：06 七阶段复盘 → `action_items` → 议程 → 裁决执行 → `applied` 回写。
- **补上的断点**：`suggest_methodology_changes()` 此前只展示无消费方（"看得到改不了"）→ `app/services/agent_params.py` 补齐「建议 → 变更单 → 生效 → 回滚」，白名单 + 值域钳制 + 证据门槛（不足只允许 draft）+ 运行时覆盖层（免重启）+ before 记录可回滚。
- 纪律：**该模块不自动改任何参数**；生效来自人工确认或显式 API。

## 4. 策略进化（strategy-evolution）P0 落地

| P0 项 | 承载 |
|---|---|
| 信号健康度监控 | `picks/signal_health.py`（滚动 20 次胜率/期望 + CUSUM 漂移） |
| 筹码分布引擎 | `market/chip.py`（CYQ 衰减模拟：获利盘/集中度/密集峰）+ `GET /chip` |
| 复盘策略健康维度 | `review/strategy_health.py` |
| 相位对账 | `sentiment/reconcile.py` |

**P2 延后（按设计，依赖样本积累 ≥200 或满季度）**：HMM regime / 游资席位画像 / meta-labeling 数据版 / walk-forward 门禁 / 回测成本建模 / 新闻情绪特征入模。
> ⚠️ **状态不在此（2026-09-14 `GOV-002`）**：各项**状态与触发条件一律见账本 §6.0**
> （整组随 `RSH-018` 维持**观察**：`RSH-016` HMM regime / 席位画像 / meta-labeling，其余见 §6.3 触发条件表）。
> 本节只说明"为什么按设计延后"，不承担进度断言。

## 5. 仓库整合结论（trading 分组 + 新仓库评估）

- **trading 分组 22 仓**：14 仓已有历史深评结论（直接引用不重评），真新增 8 仓；**无替换级候选**——换血重点回到系统内部（因子 0 消费 / 形态薄弱 / 记忆效应排序）。
- **akquant（0.3.58）建议引入**（回测 + 指标引擎）；**talib 一律指定 `backend="rust"`**（Python 后端有 NaN→0 头部污染）。
- 「AI 能否替代定时任务」的结论：**不是替代，是三层分工**（数据枢纽 / 分析决策 / 自主执行）。
- 教训：**大分组评估三步**——先查 archive + 台账 diff → 只评真新增 → 浅评即可。

## 6. 自主化路径（research-autonomous-agent）

**结论：分层而非单选**——
| 层 | 方案 |
|---|---|
| 触发层 | ~~launchd + `claude -p`（headless）~~ ❌ **2026-09-12 裁定 B 不采用**（见下） |
| 能力层 | 补一个 MCP server（多步工具循环：查数 → 读 KB → 跑脚本 → 写结论） |
| 治理层 | KB 复盘框架 + 现有推送矩阵 |

差异点：15:45 议程是**固定管线**（十路 → 一次裁决），非自由探索。

**触发层裁定（2026-09-12，B 方案）**：`launchd + claude -p` 选型**已推翻**。实测 `com.ashare.review`
自 09-09 安装后 `runs=4` **全部 exit 126**、零产出——项目位于 `~/Desktop`（macOS **TCC 保护目录**），
launchd 启动的进程无该目录访问授权。**改用后端已有常驻调度承担**：`review-scheduler`
（交易日 15:30 → `data/review/reports/YYYYMMDD.json`，实测 09-02~09-11 **连续 9 个交易日准点产出**）
+ `picks-intraday-review`（15:35）。**不新增独立机制**——KB-ENG-62 的普适结论。
ad-hoc headless 的真实成本样本（供未来评估）：**$1.40 / 38 轮 / 8.2 分钟**。
拆除明细与教训：`kb/09-verification-pitfalls.md` KB-ENG-62、`retro-and-gaps.md` §6.10。

## 7. LLM 微调路径（三层，守零新增付费依赖）

| 层 | 内容 | 状态 |
|---|---|---|
| 层 1 | pending 事件交给 GLM 结构化判定（走 analyzers 纪律 + `llm_aux` 标记不伪装） | ✅ 已落地（`events/llm_aux.py` + 20 分钟盘中轮询，默认开关） |
| 层 2 | 情绪因子批量打分（若过池内 IC） | 状态见账本 §6.0（**等窗**：需算力/成本确认，属零新增付费依赖纪律的待拍板项） |
| 层 3 | **`event_card` 判定沉淀自标注数据集**（事件×方向×依据×买点五因素×T+1/T+5） | ✅ 脚本已交付（`scripts/export_event_labels.py`） |

**核心洞见**：我们的 `event_card` 判定结果本身就是"强模型/规则标注 → 蒸馏小模型"路径里最缺的高质量标签数据。
**近期不实际训练**（无 GPU，训练即新增付费依赖）。

---

## 原始文档指针

| 原始文档 | 处置 |
|---|---|
| ai-agent-console-plan.md / ai-brain-plan.md / evolution-brain-plan.md | 已归档（全落地） |
| strategy-evolution-plan.md | 已归档（P0 落地，见本文 §4；**P2 延后项状态 → 账本 §6.0**，不在本文维护） |
| trading-star-merge-plan-20260909.md / trading-agent-positioning-20260909.md | 已归档 |
| research-autonomous-agent-20260909.md | **已删除**（未落地方案 → 账本 §6.0：`RSH-018` 观察档） |
| llm-finetune-research-20260909.md | **已删除**（层 2 → 账本 §6.0 等窗；层 1/3 已落地见本文 §7） |
