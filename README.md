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
uvicorn app.main:app --reload --port 8000
# API 文档: http://127.0.0.1:8000/docs

# 2) 前端（Node 18.18+）
cd apps/web
npm install
npm run dev
# 打开 http://localhost:3000/workbench

# 3) 测试
cd backend && pytest
```

无网络 / 演示模式：`ASHARE_DATA_PROVIDER=mock uvicorn app.main:app --port 8000`（数据标记 source=mock）。

## 阶段路线图（对应 docs 与 full.md §23）

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 基础框架 | 目录/配置/SQLite/日志/健康检查/基础布局/全局搜索/主题切换 | ✅ |
| 2 行情基础设施 | Provider 协议 + 东方财富/Mock 双实现、Normalizer、5 级质量校验、QuoteHub、REST、WS 推送、指数/个股/K线/盘口/逐笔 | ✅（涨停池、龙虎榜总览提前接入） |
| 3 市场与板块 | 市场宽度、情绪周期、板块排行、概念题材、连板梯队深度 | 🔶 slice 1 完成（全市场快照/宽度/Parquet 落库）；余：情绪周期判定、板块排行 |
| 4 投研数据 | 龙虎榜深度（席位/关系图）、资金流、新闻、公告、财务、估值、股东、解禁、两融、大宗 | ⬜ |
| 5 量化系统 | 技术指标、因子库、选股器、可解释评分、市场状态、仓位建议、风险引擎 | ⬜ |
| 6 模拟交易与回测 | 模拟账户、撮合引擎（T+1/涨跌停/停牌/费用）、无未来函数回测引擎、历史回放 | ⬜ |
| 7 AI 系统 | Researcher/Critic/Strategist/Auditor 四角色、MCP 工具、Skills、研究记忆、预测审计 | ⬜ |
| 8 通知与部署 | 预警、通知渠道、Docker 生产化、监控 | ⬜（开发用 compose 已有） |

## 数据管线（核心设计）

```text
数据源 → Provider Adapter → Data Normalizer → Data Quality Validator → Cache/QuoteHub → REST/WebSocket → 前端
```

- **数据源链（自动切换）**：腾讯（实时快照/五档/K线）→ 东财（search/K线/逐笔 + 涨停池/龙虎榜专项）→
  全链失败标 stale。新浪为热备备源；同花顺为分时图候选（Phase 3）。详见 `docs/data-sources.md`。
- 前端不直接访问第三方接口；后端批量轮询（默认 5s）+ WebSocket 推送。
- 每条数据必带 `source / quality(high|medium|low|stale|invalid) / quality_reasons / received_at / data_timestamp`。
- 数据源失败 → 停止伪造实时数据：缓存标记 `stale`、health 报 `degraded`、前端显示过期状态。
- 低质量/过期/非法数据：AI 分析禁用、回测禁用、前端强制风险标识。

## 目录

```text
apps/web        Next.js 工作台（workbench / market / watchlist / stock/[symbol] / limit-up / longhu）
backend/app     FastAPI（api / core / models / repositories / services / data_providers / data_quality / websocket / market）
backend/tests   pytest（40 例：质量校验、Normalizer、API、Mock、仓库）
data/           SQLite 业务库 + 规划中的 parquet/raw/cache
docs/           11 篇文档（architecture / data-sources / data-dictionary / api / websocket /
                backtest-rules / longhu / sentiment / risk-management / mcp / deployment）
```

## 文档索引

- 架构与数据流：[docs/architecture.md](docs/architecture.md)
- 数据源实测口径（含限流与降级）：[docs/data-sources.md](docs/data-sources.md)
- 回测强制禁令（代码级）：[docs/backtest-rules.md](docs/backtest-rules.md)
- AI 多 Agent 与 MCP 工具：[docs/mcp.md](docs/mcp.md) · [docs/architecture.md](docs/architecture.md)
- 其余：data-dictionary / api / websocket / longhu / sentiment / risk-management / deployment
