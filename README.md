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

> 待办明细账本：`docs/retro-and-gaps.md`（唯一明细）；阶段复盘见 `docs/archive/plan-review.md`（已归档，只读）。

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 基础框架 | 目录/配置/SQLite/日志/健康检查/基础布局/全局搜索/主题切换 | ✅ |
| 2 行情基础设施 | Provider 链 ths→tencent→eastmoney→sina、Normalizer、5 级质量校验、QuoteHub、REST 96 端点、WS 推送、指数/个股/K线/分时/盘口/逐笔 + 数据可靠性（Parquet 原子写/容错读） | ✅ |
| 3 市场与板块 | 市场宽度、情绪周期判定+历史序列、板块排行、题材梯队看板、涨停池、云图、新题材预判 | ✅；余：题材事件树/生命周期 |
| 4 投研数据 | 龙虎榜深度（席位/历史）、资金流、新闻+摘要 v1、公告、财务、估值、公司资料、集合竞价、复权因子 | ✅；余：营业部关系图谱、筹码、解禁、两融、大宗 |
| 5 量化系统 | 多因子技术评估（单股+全市场选股器+六维评分卡，防飞刀口径）+ 风控引擎 v1（7 档市场状态→仓位参数→下单预检） | ✅ |
| 6 模拟交易与回测 | 撮合引擎（T+1/涨跌停/费用/挂单）+ 交易页签 + B/S 点 + 成本线 + 重置 + 日线回测引擎（代码级防泄露）+ 历史回放 + 分钟级 TDX 底座 | ✅ |
| 7 AI 系统 | 盘后复盘 Agent（规则分析/模型路由/方法论版本化/元结论迭代）+ 新题材预判 + 新闻摘要 v1 | 🔶；**LLM 接入代码已就绪**（LLMAnalyzer/LLMSummarizer 完整实现+路由降级，2026-09-02），余：LLM 凭据（填 `.env` 即用）、四角色编排 |
| 8 通知与部署 | 预警规则/触发/通道抽象/管理页 + 同源反代 + 运维巡检工具 | 🔶；**飞书通道已落地**（feishu notifier + 未配置徽标，2026-09-02），余：webhook 凭据（`ASHARE_ALERT_FEISHU_WEBHOOK`）、Docker 生产化、监控 |
| 9 联动系统 | 统一路由（URL 唯一真相源）、题材⇄个股双向联动、官方板块 K 线交叉验证、事件驱动面板 + 个股相关事件行 | 🔶；核心闭环 ✅，余：切片 E 跳转（P2） |
| 10 系统重构 | 系统盘点（docs/architecture-redesign.md）→ P0 K线三源+熔断+回放限流、P1 事件采集调度+角色胜率、**页面合并：导航 13→5**（盘面四合一 /tape、云图入市场、研究折叠 /research、自选入工作台）、P2 基本面 ROE/毛利率补全、screener 冻结 + skills 归档 | ✅（2026-09-01） |

测试：后端 pytest 801 例 + 前端 vitest 134 例，CI（GitHub Actions）四门禁全绿。

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
apps/web        Next.js 工作台（workbench / market / watchlist / boards / heatmap / limit-up /
                themes / screener / backtest / longhu / predict 等，components 含图表与回放）
backend/app     FastAPI（api / core / models / repositories / services / data_providers /
                data_quality / websocket / market / paper / review / predict / schemas / migrations）
backend/tests   pytest 329 例（防泄露回测/迁移三态/情绪序列/选股器/复盘/预判/鉴权…）
data/           SQLite 业务库 + parquet 快照与分时 + trade_calendar.json 日历兜底
docs/          文档：INDEX（总入口）/ kb（知识库）/ summary（主题汇总）/ 现役规范 + archive 归档
                （已完成方案的精华进 summary 后原件即删，见 docs/kb/07-doc-curation.md）
skills/         仓库随行技能：design-taste（**唯一权威视觉规范**，三源合成）/ hithink-finance
                （API 契约查询）；其余（含残缺的 impeccable v4.1.2 副本）已归档 _archived/
.github/workflows  CI（pytest/pyflakes + tsc/vitest/ESLint 四门禁）
```

## 文档索引

- 总览与交接：[AGENTS.md](AGENTS.md) · [docs/PROJECT-MASTER.md](docs/PROJECT-MASTER.md)
- 架构与数据流：[docs/architecture.md](docs/architecture.md)
- 数据源实测口径（含限流与降级）：[docs/data-sources.md](docs/data-sources.md) · 四源对比 [docs/data-source-comparison.md](docs/data-source-comparison.md)
- 回测强制禁令（代码级）：[docs/backtest-rules.md](docs/backtest-rules.md)
- 复盘 Agent：[docs/review-agent.md](docs/review-agent.md) · 新题材预判：[docs/theme-prediction.md](docs/theme-prediction.md)
- 情绪判定与误判复盘：[docs/sentiment.md](docs/sentiment.md)（含「历史误判案例库」）
- 欠缺清单（**唯一待办总账**）：[docs/retro-and-gaps.md](docs/retro-and-gaps.md) §六
- 文档总入口：[docs/INDEX.md](docs/INDEX.md) · 计划去向：[docs/plan-registry.md](docs/plan-registry.md)
- 其余：api / websocket / data-dictionary / risk-management / mcp / deployment
