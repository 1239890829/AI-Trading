# 架构设计

## 1. 数据流（full.md §2.2）

```text
数据源（东方财富免费接口 / Mock / 未来: 新浪·腾讯·akshare·盘后文件导入）
  ↓
Provider Adapter        backend/app/data_providers/     统一 MarketDataProvider 协议
  ↓
Data Normalizer         backend/app/market/normalizer.py  各数据源字段 → 统一 schema
  ↓
Data Quality Validator  backend/app/data_quality/validator.py  5 级质量标记
  ↓
Data Cache              QuoteHub 内存缓存 + 轮询历史（后续接入 Redis/Parquet）
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
Domain Layer     app/market, app/data_quality, ...（后续: factors, sentiment, backtest...）
Repository Layer app/repositories      SQLAlchemy 访问（SQLite 业务库）
Provider Layer   app/data_providers    外部数据源适配
Task Layer       后台任务（当前: QuoteHub 轮询协程；后续: APScheduler）
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

## 4. AI 多 Agent 研究流（§15，Phase 7）

```text
数据收集 → Researcher → Critic（反方审查）→ Strategist（分级+失效条件）
        → 风险引擎拦截 → Auditor（盘后对照 T+1/3/5 表现）→ 写入研究记忆
```

AI 输出必须遵循统一评分结构（总分/因子贡献/正反理由/风险项/失效条件/有效期/数据时间/置信度），
禁止保证性语言，禁止把评分等同未来收益概率。

## 5. 回测防泄露（§13.2，Phase 6）

代码级强制隔离，规则清单与测试用例要求见 `docs/backtest-rules.md`。

## 6. 技术栈

- 后端：Python 3.11 / FastAPI / Pydantic v2 / SQLAlchemy 2 / SQLite（业务）/ httpx / pytest
- 前端：Next.js 15 / React 19 / TypeScript / Tailwind CSS / lightweight-charts / WebSocket
- 后续按阶段引入：Parquet+Polars（行情落库）、pandas-ta（指标）、APScheduler（任务）、Redis（可选缓存）

## 7. 当前已实现 vs 计划

见根目录 README 的“阶段路线图”。
