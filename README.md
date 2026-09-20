# AShare AI Trader · A 股量化投研与模拟交易工作台

专业级 A 股实时行情与 AI 量化投研工作台：行情展示、投研分析、选股观察、模拟交易、策略回测。
**第一阶段禁止连接真实券商与自动下单** —— 系统只做研究与模拟。

> ⚠️ 所有数据仅供投研学习与策略验证，不构成投资建议。

## 快速启动

```bash
# 1) 后端（Python 3.11+）
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
# ⚠️ 勿加 --reload：与 SQLite 锁组合会反复挂死（见 AGENTS.md §6.1）
# API 文档: http://127.0.0.1:8000/docs

# 2) 前端（Node.js 22，与仓库 CI 一致）
cd apps/web
npm install
npm run dev
# 打开 http://localhost:3000/workbench

# 3) 测试
cd backend && pytest
```

无网络 / 演示模式：`ASHARE_DATA_PROVIDER=mock uvicorn app.main:app --port 8000`（数据标记 source=mock）。

## 当前能力与实施入口

当前改造从 [阶段总账](docs/retro-and-gaps.md#60-阶段索引) 进入 W00–W09；任务状态、实施范围和验收只在对应阶段文档维护。历史阶段回顾见 [历史复盘](docs/archive/plan-review.md)，不据历史完成标记领取新任务。

| 能力 | 主要范围 |
|---|---|
| 行情与研究 | 多源行情、数据质量与降级、题材、资金、新闻事件及公司资料 |
| 机会与风险 | 候选、资格硬门、排序、盘中跟踪与可解释的不提醒原因 |
| 模拟与复盘 | 模拟账户、真实持仓手工账本、回测/回放与决策证据；二者账本互不混用 |
| 助手与通知 | 研究助手、复盘、通知中心及飞书通道；渠道受理不等于送达/已读 |

具体能力和限制以当前代码及所属阶段证据为准。测试命令见 `AGENTS.md` §1；最近实测结果及前提以 `docs/handoff.md` §1 为准，不在这里复制动态计数或声称所有能力已经完成。

## 数据管线（核心设计）

```text
数据源 → Provider Adapter → Data Normalizer → Data Quality Validator → Cache/QuoteHub → REST/WebSocket → 前端
```

- **数据源链（自动切换）**：腾讯（实时快照/五档/K线）→ 东财（search/K线/逐笔 + 涨停池/龙虎榜专项）→
  全链失败标 stale。新浪为热备备源；同花顺的**分时**接口经评估**未接入**（当日分时由腾讯实现）。详见 `docs/data/data-sources.md`。
- 前端不直接访问第三方接口；后端批量轮询（默认 5s）+ WebSocket 推送。
- 每条数据必带 `source / quality(high|medium|low|stale|invalid) / quality_reasons / received_at / data_timestamp`。
- 数据源失败 → 停止伪造实时数据：缓存标记 `stale`、health 报 `degraded`、前端显示过期状态。
- 低质量/过期/非法数据：AI 分析禁用、回测禁用、前端强制风险标识。

## 目录

```text
apps/web        Next.js 工作台（workbench / market / watchlist / boards / heatmap / limit-up /
                themes / screener / backtest / longhu / predict 等，components 含图表与回放）
backend/app     FastAPI（api / core / models / repositories / services / data_providers /
                data_quality / websocket / market / paper / review / predict / schemas / migrations）
backend/tests   pytest 回归（防泄露回测/迁移/行情/选股/复盘/鉴权及治理守卫）
data/           SQLite 业务库 + parquet 快照与分时 + trade_calendar.json 日历兜底
docs/          文档：根目录仅保留 6 个控制面入口；现役正文按 system / data / product /
                strategy / ai / review / research 分类；kb / summary / stages 各司其职，
                archive 仅存历史，时间序列另归 daily-review / evolution / repo-watch
skills/         仓库随行技能：design-taste（**唯一权威视觉规范**，三源合成）/ hithink-finance
                （API 契约查询）；其余（含残缺的 impeccable v4.1.2 副本）已归档 _archived/
.github/workflows  CI（pytest/pyflakes + tsc/vitest/ESLint 四门禁）
```

## 文档索引

- 当前总入口与交接：[AGENTS.md](AGENTS.md) · [docs/INDEX.md](docs/INDEX.md) · [docs/handoff.md](docs/handoff.md)；08-29 历史技术基线：[docs/archive/PROJECT-MASTER.md](docs/archive/PROJECT-MASTER.md)
- 架构与数据流：[docs/system/architecture.md](docs/system/architecture.md)
- 数据源实测口径（含限流与降级）：[docs/data/data-sources.md](docs/data/data-sources.md) · 四源对比 [docs/data/data-source-comparison.md](docs/data/data-source-comparison.md)
- 回测强制禁令（代码级）：[docs/strategy/backtest-rules.md](docs/strategy/backtest-rules.md)
- 复盘 Agent：[docs/review/review-agent.md](docs/review/review-agent.md) · 新题材预判：[docs/strategy/theme-prediction.md](docs/strategy/theme-prediction.md)
- 情绪判定与误判复盘：[docs/strategy/sentiment.md](docs/strategy/sentiment.md)（含「历史误判案例库」）
- 阶段总账与任务入口：[docs/retro-and-gaps.md](docs/retro-and-gaps.md#60-阶段索引)
- 文档总入口：[docs/INDEX.md](docs/INDEX.md) · 计划去向：[docs/plan-registry.md](docs/plan-registry.md)
- 其余按目录归类：system（API/WS/MCP/部署/数据契约）· product（产品闭环/功能审计）· strategy（策略/因子/风控/回测）· ai（Jev/持续演进）· review（复盘）· research（专题研究）
