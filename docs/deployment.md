# 部署与运维

## 本地开发（推荐）

```bash
# 后端（Python 3.11+）
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env   # 按需修改
uvicorn app.main:app --reload --port 8000

# 前端（Node 18.18+）
cd apps/web
npm install
npm run dev   # http://localhost:3000
```

## Docker

```bash
docker compose up --build
# backend: http://localhost:8000  web: http://localhost:3000
```

## 环境变量（敏感配置只走环境变量，禁止入库/入前端/Git）

| 变量 | 说明 |
|---|---|
| ASHARE_DATA_PROVIDER | eastmoney \| mock |
| ASHARE_POLL_INTERVAL_SECONDS | 行情轮询间隔（默认 5，被限流时调大） |
| ASHARE_WATCHLIST | 初始自选（逗号分隔，首次建库种子） |
| ASHARE_DATABASE_URL | 默认 `sqlite:///<repo>/data/ashare.db` |
| ASHARE_REQUEST_TIMEOUT_SECONDS | Provider 超时 |
| ASHARE_CORS_ORIGINS | 允许的前端来源 |
| NEXT_PUBLIC_API_BASE / NEXT_PUBLIC_WS_BASE | 前端连接的后端地址 |
| LLM_API_KEY 等 | Phase 7 AI 模块接入时使用，绝不写入前端 |

## 运维要点

- SQLite 文件在 `data/ashare.db`，行情/因子/回测结果规划写入 `data/parquet/`，注意备份 data/ 目录。
- `/api/health` 报告数据源健康（consecutive_failures / last_error / is_stale），可接监控告警。
- 结构化日志 + 每个后台任务的名称/起止/状态/重试统计在 Phase 8 补全（当前轮询协程有日志与失败计数）。
- 免费数据源被限流（空回复）属预期：系统自动进入 stale 降级，调大 ASHARE_POLL_INTERVAL_SECONDS 或换备源。

## 已踩过的坑（务必记住）

1. **pip 必须走清华镜像**：`pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <pkg>`。
   直连 pypi.org 在本网络会超时；系统代理（SOCKS）会导致 pip 卡死——不要让 pip 读 ALL_PROXY。
2. **行情源客户端已设 `trust_env=False`**（直连国内站）。若给后端配了系统代理环境变量，
   httpx 会尝试走 SOCKS 并因缺 `socksio` 直接启动失败——保持现状，勿全局代理后端。
3. **Next.js dev 运行时禁止执行 `npm run build`**：两者共用 `.next` 目录会互相破坏（前端 500）。
   构建验证只在停掉 dev server 后进行，或 `rm -rf .next` 后重启 dev。
4. 逐笔成交仅东财 details 源（本机被限流时 `/api/trades` 返回 502，前端显示空态）；
   盘中细粒度数据用 `/api/minute-line/{symbol}`（腾讯 1 分钟分时）。
