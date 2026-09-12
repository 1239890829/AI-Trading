# 架构设计

## 1. 数据流

```text
数据源（四源链 ths → 腾讯 → 东财 → 新浪，含熔断；Mock；akshare 扩展面；盘后文件导入）
  ↓
Provider Adapter        backend/app/data_providers/     统一 MarketDataProvider 协议
  ↓
Data Normalizer         backend/app/market/normalizer.py  各数据源字段 → 统一 schema
  ↓
Data Quality Validator  backend/app/data_quality/validator.py  5 级质量标记
  ↓
Data Cache              QuoteHub 内存缓存 + 轮询历史（Parquet/DuckDB 落库已接入；Redis 未接）
  ↓
Quote Hub               backend/app/services/quote_hub.py  订阅/广播/seq
  ↓
WebSocket / REST API    backend/app/websocket/ + backend/app/api/
  ↓
前端工作台              apps/web（WS 优先，断线自动降级 REST 轮询）
```

前端**永远不直接**高频访问第三方数据源；一切外部请求都由后端 Provider 完成。

## 2. 后端分层（§3.2）

```text
API Layer        app/api/routes        只做参数校验与编排，禁止业务逻辑
Service Layer    app/services          QuoteHub 等编排服务
Domain Layer     app/market, app/data_quality, app/factors, app/sentiment, app/picks, app/risk
Repository Layer app/repositories      SQLAlchemy 访问（SQLite 业务库）
Provider Layer   app/data_providers    外部数据源适配
Task Layer       后台任务（统一 `TaskRegistry`：26 个常驻任务一份声明，`GET /api/system/schedulers`）
```

## 3. 数据质量模型（§2.3）

每条行情数据携带：`source / quality / quality_reasons / received_at / data_timestamp`。

| quality | 含义 | 处理要求 |
|---|---|---|
| high | 通过全部校验 | 正常使用 |
| medium | 延迟数据源（预留） | 前端展示“延迟”标识 |
| low | 触发可疑规则（跳价>涨跌停幅度、涨跌幅与昨收矛盾、时间倒退、缺价） | 前端必须展示风险标识；AI 分析默认禁用；回测禁用 |
| stale | 刷新失败/超时，Hub 统一标记 | 停止伪造实时数据，展示最近有效时间 |
| invalid | 结构性非法（价格≤0、high<low、盘口交叉、时间戳在未来等） | 禁止进入任何分析 |

校验规则清单见 `data_quality/validator.py`，新增规则只需在该模块扩展。

## 4. AI 多 Agent 研究流（§15 · **设计稿，未实现**）

> ⚠️ 当前**没有** `Researcher` / `Critic` / `Strategist` / `Auditor` 这四个 Agent 角色
> （全仓代码中不存在这些标识符，可 grep 复核）。已实现的是**研究与复盘的能力模块**：
> 因子库（`app/factors/`）、事件采集与六维消息面、复盘归因（`app/review/`）、
> 盘后 T+1/3/5 对照（`picks/backtest.py`、`picks/review_intraday.py`）。
> 下面的流水线是**目标形态**，落地前不得当作现有能力引用。

```text
（目标）数据收集 → Researcher → Critic（反方审查）→ Strategist（分级+失效条件）
        → 风险引擎拦截 → Auditor（盘后对照 T+1/3/5 表现）→ 写入研究记忆
```

AI 输出必须遵循统一评分结构（总分/因子贡献/正反理由/风险项/失效条件/有效期/数据时间/置信度），
禁止保证性语言，禁止把评分等同未来收益概率。

## 5. 回测防泄露（§13.2 · **已实现**）

代码级强制隔离，规则清单与测试用例要求见 `docs/backtest-rules.md`。

## 6. 技术栈

- 后端：Python 3.11 / FastAPI / Pydantic v2 / SQLAlchemy 2 / SQLite（业务）/ httpx / pytest
- 前端：Next.js 15 / React 19 / TypeScript / Tailwind CSS / lightweight-charts / WebSocket
- 已引入：Parquet + DuckDB（行情落库 marketdb）、pandas-ta（指标）。
- **未引入**：Redis（可选缓存）。**`APScheduler` 不引入**——常驻任务由自研 `TaskRegistry`
  （asyncio + 注册表，含死亡自愈与可观测出口）承担。

## 7. 当前已实现 vs 计划

见根目录 README 的“阶段路线图”。
