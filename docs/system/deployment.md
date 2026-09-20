# 部署与运维

> **定位**：本地开发 / Docker 生产部署 / 环境变量 / 前端连后端 / 运维巡检的**唯一操作手册**。
> **上游**：`docker-compose.prod.yml`、`apps/web/Dockerfile`、`backend/.env.example` ｜ **下游**：任何部署、排障、密钥注入任务
> **状态**：现役（Docker 生产化 2026-09-04 完成）

## 速览（30 秒）

| 要点 | 结论 |
|---|---|
| **端口纪律** | 前端固定 **3000**、后端固定 **8000**；不起多余实例，任务结束须确认两者仍在跑 |
| **数据持久化** | 挂宿主 `./data` → 容器 `/data`。**挂 `/app/data` 不生效** |
| **密钥（红线 4）** | API Key 只走 `backend/.env`（`env_file` 注入），**绝不进镜像层 / 前端 / git** |
| **前端连后端** | 同源 `/backend` 相对路径 + 服务端运行时代理 `BACKEND_ORIGIN`（**改后端地址无需重新构建**）；WS **不走**代理，需 nginx 反代或自动降级 5s 轮询 |
| **公网 / NAS 必配** | `ASHARE_AUTH_MODE=shared` + `ASHARE_API_TOKEN`（**R22 默认拒绝：全部接口**，backend 与 web 容器都要有）+ `ASHARE_CORS_ORIGINS`（加实际访问域名） |
| **鉴权漏配 = 全站 401** | 后端配了 token 而 web 容器没拿到 ⇒ 连读接口也 401（代理层不附加请求头） |
| **发布门禁** | `node scripts/api-sweep.js`——专抓「HTTP 200 但数据为空」这类测试与类型检查都发现不了的问题 |

**章节导航**：本地开发 ｜ Docker ｜ 环境变量 ｜ 前端如何连后端 ｜ 运维工具（全 GET 端点巡检）｜ 运维要点 ｜ 已踩过的坑 ｜ Docker 用户侧验证清单（8 项）

---

## 本地开发（推荐）

```bash
# 后端（Python 3.11+）
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env   # 按需修改
uvicorn app.main:app --port 8000
# ⚠️ 勿加 --reload：与 SQLite 锁组合会反复挂死（见 AGENTS.md §6.1）

# 前端（Node 18.18+）
cd apps/web
npm install
npm run dev   # http://localhost:3000
```

## Docker

**生产部署**（2026-09-04，双镜像构建）：

```bash
docker compose -f docker-compose.prod.yml up -d --build
# web: http://localhost:3000（同源 /backend 反代 → backend:8000）
# backend 仅本机暴露 127.0.0.1:8000（巡检/WS 直连）
```

- 数据持久化：挂载宿主 `./data` → 容器内 `/data`（`REPO_ROOT` 在容器内推导为 `/`，
  SQLite/Parquet 默认路径都落在 `/data`）。**不要挂 `/app/data`，不生效。**
- API Key：`env_file: backend/.env` 注入，绝不进镜像层。
- WS：Route Handler 不代理升级，默认 5s 轮询降级；要实时 WS 见下文「前端如何连后端」。

**开发容器**（源码挂载 + npm dev）：`docker compose up --build`。

## 环境变量（敏感配置只走环境变量，禁止入库/入前端/Git）

| 变量 | 说明 |
|---|---|
| ASHARE_DATA_PROVIDER | ths（需 KEY）\| tencent \| sina \| eastmoney \| mock（mock 只能单独使用） |
| ASHARE_THS_API_KEY | 同花顺 fuyao 官方 Key（`.env`，勿提交） |
| ASHARE_POLL_INTERVAL_SECONDS | 行情轮询间隔（默认 5，被限流时调大） |
| ASHARE_ALERT_POLL_INTERVAL_SECONDS | 预警引擎轮询间隔（默认 5） |
| ASHARE_SNAPSHOT_* | 全市场快照抓取 / Parquet 落盘间隔（60 / 300） |
| ASHARE_WATCHLIST | 初始自选（逗号分隔，首次建库种子） |
| ASHARE_DATABASE_URL | 默认 `sqlite:///<repo>/data/ashare.db` |
| ASHARE_PARQUET_DIR | Parquet 快照目录 |
| ASHARE_REQUEST_TIMEOUT_SECONDS | Provider 超时 |
| ASHARE_CORS_ORIGINS | 允许的前端来源（**部署到 NAS/云主机要把实际访问域名加进来**） |
| ASHARE_REVIEW_* / ASHARE_NEWS_* | 分析器/摘要器：rules（默认）或 llm（配 *_LLM_BASE_URL / *_LLM_API_KEY / *_LLM_MODEL） |
| ASHARE_AUTH_MODE | `local`（默认）/ `shared`。`shared` 表示"会有回环之外的访客"⇒ **必须配 token**（未配则**拒绝启动**）。取值拼错同样拒绝启动（否则静默退回 `local`＝全放行）。⚠️ **2026-09-15 R22 新增** |
| ASHARE_API_TOKEN | 访问鉴权；**R22 起为默认拒绝**——配了之后**所有** HTTP 路由都要 `X-API-Token`（写接口与 GET 一视同仁），唯一豁免 `GET /api/health`。**部署到公网/NAS 必须配置随机值**（`openssl rand -hex 32`）。backend 从 `backend/.env` 读，**web 容器需经 compose 透传同一值**（代理层运行时给**全部**请求附加请求头），见「鉴权凭据的传递路径」。⚠️ 前缀**绝不能**是 `NEXT_PUBLIC_` |
| NEXT_PUBLIC_API_BASE | 前端 REST 基址；**留空即同源 `/backend`**（推荐） |
| NEXT_PUBLIC_WS_BASE | 前端 WebSocket 基址；留空时同源尝试，失败自动降级 5s 轮询 |
| BACKEND_ORIGIN | **服务端**变量，前端反代的目标后端地址（默认 `http://127.0.0.1:8000`），运行时生效 |

> 完整清单与说明见 `.env.example`（由 `backend/tests/test_env_docs.py` 守护与 `config.py` 不漂移）。

## 前端如何连后端（部署必读）

浏览器侧的 `NEXT_PUBLIC_*` 在**构建期**内联到产物里。若把后端地址硬编码成
`http://127.0.0.1:8000`，部署到 NAS/云主机后浏览器会去连"访问者自己电脑的 8000 端口"，
前端直接废掉，而且换主机必须重新构建。

现在的做法是**同源相对路径 + 服务端运行时代理**：

1. `API_BASE` 默认 `/backend`（同源），请求发到前端自己的域名
2. `apps/web/app/backend/[...path]/route.ts` 在服务端把它转发到 `BACKEND_ORIGIN`
3. `BACKEND_ORIGIN` 不是 `NEXT_PUBLIC_*`，**运行时读取，改了重启即生效，无需重新构建**

WebSocket 例外：Route Handler 不代理 WS 升级。生产环境要么前置 nginx 反代并放开
`Upgrade` 头，要么显式设置 `NEXT_PUBLIC_WS_BASE`；两者都不做时 `useQuoteStream`
自动降级为 5s 轮询，功能完整只是不够实时。开发环境由 `.env.development` 直连后端。
启用鉴权后 WS 还需带**子协议凭据**（经同源 `/api/ws-credential` 下发），
nginx 反代时**不要剥掉 `Sec-WebSocket-Protocol`**；详见下文「WebSocket 面」。

### 生产模式自检

```bash
cd apps/web
npx next build                              # dev server 运行时禁止执行
BACKEND_ORIGIN=http://127.0.0.1:8000 npx next start -p 3100
curl http://127.0.0.1:3100/backend/api/health   # 应返回后端健康信息
```

### 鉴权凭据的传递路径（2026-09-14 起；2026-09-15 R22 扩面）

**HTTP 面**的 `X-API-Token` **只在服务端流转**，浏览器侧不持有：

```
浏览器 ──(不带 token)──▶ Next Route Handler ──(附加 X-API-Token)──▶ backend
                            ↑ 运行时读 ASHARE_API_TOKEN，给**全部**请求附加
```

- 历史实现用 `NEXT_PUBLIC_API_TOKEN`，而 `NEXT_PUBLIC_*` 被 Next **构建期内联**成客户端
  bundle 里的字面量 ⇒ 等于把唯一凭据公开，且会写进浏览器历史与反代访问日志。
  已修复；防回潮守卫 `apps/web/lib/env-secrecy.test.ts`（含掩码器自证与注入验证）。
- **2026-09-15 R22 起注入面扩到全部请求**：此前只有非 GET/HEAD 注入，因为当时保护面
  只是写接口；现在保护面是"默认拒绝"，读接口同样要求凭据 ⇒ 若沿用旧条件，
  **配了 token 的环境下所有读接口 401**。传递实现收敛到
  `apps/web/lib/proxy-headers.ts::buildUpstreamHeaders`（单点）。
- 因此 **web 容器也必须拿到 `ASHARE_API_TOKEN`**，由 `docker-compose.prod.yml` 透传。
  刻意**不使用** `env_file: backend/.env` —— 那会把 THS Key / DB URL 等全部后端凭据
  一并搬进 web 容器。推荐从单源派生，避免两处漂移：

```bash
export ASHARE_AUTH_MODE="$(sed -n 's/^ASHARE_AUTH_MODE=//p' backend/.env)"
export ASHARE_API_TOKEN="$(sed -n 's/^ASHARE_API_TOKEN=//p' backend/.env)"
docker compose -f docker-compose.prod.yml up -d --build
```

- **判据是"两边一致"**：变量缺失时代理不附加请求头，与后端的姿态语义对称
  （`local` + 未配 token = 本地开发零配置、后端全放行）；但**后端配了而 web
  没传 ⇒ 全部接口 401**（R22 前只有写接口会 401）。排查（不打印明文）：

```bash
docker compose -f docker-compose.prod.yml exec web printenv ASHARE_API_TOKEN | wc -c
# 0 ⇒ 未注入（除换行外无字符）；>0 ⇒ 已注入
```

### WebSocket 面：子协议凭据（2026-09-15 R22）

浏览器 `WebSocket` 构造器**不允许设置自定义请求头**（平台约束，非本项目选择），
而 `?token=` 通道已刻意关闭 ⇒ 唯一可用通道是 **`Sec-WebSocket-Protocol` 子协议**。

```
浏览器 ──GET /api/ws-credential──▶ Next Route Handler（运行时读 ASHARE_API_TOKEN）
   ▲                                    └─ 返回 {"subprotocol":"ashare-token.<b64url>"}
   └── 内存持有，new WebSocket(url, [该子协议]) ──▶ backend /ws/quotes
```

- **这是全系统唯一"凭据出服务端"的地方**——回显约束（RFC 6455 §4.1：客户端提议了子协议
  而服务端一个都没选 ⇒ 客户端主动判定连接失败）要求浏览器**自己知道**凭据。
  三重约束保证它不比 HTTP 面更弱：① `/api/ws-credential` 在同源 Next 服务端，**构建期不内联**
  任何秘密；② 返回的是**编码后的子协议**而非明文 token，不进 URL / 浏览器历史 / Referer / 反代日志；
  ③ 只在**内存**持有，不落 localStorage / cookie。
- ⚠️ **残留风险（别当成"已解决"）**：能打开前端的访客可取得该凭据，进而绕过前端
  直连后端端口。它保护的是"后端端口不对未授权者开放"，**不是"区分前端访客身份"**。
  配套纪律：后端端口只绑回环（`docker-compose.prod.yml` 已如此），共享部署把反代作为唯一入口。
- 未启用鉴权时 `/api/ws-credential` 返回 `{"subprotocol": null}`，前端**不传第二个参数**
  （注意 `new WebSocket(url, [])` 与 `new WebSocket(url)` 在浏览器里**不等价**）
  ⇒ 与加固前行为逐字一致。
- WS **不走** Next 代理（Route Handler 不代理升级），故此凭据对 nginx 反代场景同样适用：
  nginx 只要不剥掉 `Sec-WebSocket-Protocol` 即可。

## 运维工具：全 GET 端点巡检

```bash
node scripts/api-sweep.js                    # 默认打 http://127.0.0.1:8000
node scripts/api-sweep.js http://nas:8000    # 指定目标

# R22 后：后端启用鉴权时必须给凭据，否则**全部端点 401**（会被误读成"全站挂了"）
ASHARE_API_TOKEN="$(sed -n 's/^ASHARE_API_TOKEN=//p' backend/.env)" node scripts/api-sweep.js
```

从 `/openapi.json` 取权威清单（不会漏也不会多），自动替换路径参数、填必需查询参数，
逐个打真实后端并输出非 2xx 明细与慢端点。**只读**，不触碰写接口。
凭据取值顺序：环境变量 `ASHARE_API_TOKEN` → 未设置则尝试 `backend/.env`；
两者皆空即不带头（本地 `local` 姿态下正确）。
⚠️ **"401 满屏" 不是数据问题**：那说明后端配了 token 而巡检没带凭据，
先核对凭据再解读结果（R22 前该现象不存在，因为保护面只有写接口）。
2026-08-30 首次运行：52 个 GET 端点，47 通过；5 个异常均为真实原因而非代码缺陷
（东财逐笔被限流、非交易日无竞价数据、复盘报告/预判/预警规则本就不存在）。

## 运维要点

- SQLite 文件在 `data/ashare.db`，行情/因子/回测结果规划写入 `data/parquet/`，注意备份 data/ 目录。
- `/api/health` 报告数据源健康（consecutive_failures / last_error / is_stale），可接监控告警。
- 结构化日志；**后台任务可观测性已落地**——`TaskRegistry`（S2-2）把 26 个常驻任务收敛为
  一份声明，逐任务记录 `state / last_tick / failures / last_error / restarts`，经
  `GET /api/system/schedulers` 暴露，并带死亡自愈（指数退避重启）。
  **未做**的是外部监控接入（Prometheus / 告警面板）。
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
5. **`next.config.ts` 的 `rewrites()` 在构建期求值并烘进产物**，`next start` **不会**
   重新读取 `BACKEND_ORIGIN`。实测：以 `BACKEND_ORIGIN=http://127.0.0.1:8999` 启动的
   生产服务器，仍然把 `/backend/*` 打到构建期默认的 8000 端口。
   所以后端反代**不能**用 rewrites，改用 `app/backend/[...path]/route.ts`（运行时求值）。
6. **`.env.example` 会与 `config.py` 漂移**：曾缺 alert/review/news 三组共 11 项，
   且"写接口鉴权"整段重复两次。已加 `backend/tests/test_env_docs.py`
   守护（缺项/重复/已失效键都会红）。
7. **Parquet 快照必须原子写**。直接 `write_parquet(目标路径)` 时进程被 kill
   （重启/部署/崩溃）会留下**大小正常但内容损坏**的文件——2026-08-30 实测产生 7 个，
   只要最新那份落在其中，选股器与情绪端点全线 502，且错误信息还误报成"TDX 源不可用"。
   现在写入改为临时文件 + `os.replace()`，读取侧由 `app/services/parquet_store.py`
   从最新往回跳过损坏文件。发现既有损坏文件时应手工清理
   `data/parquet/snapshots/*/`（该目录不入库，属本地数据）。

---

## Docker 用户侧验证清单（2026-09-04）

> 配套 `docker-compose.prod.yml`（双镜像构建 + /data 挂载 + healthcheck）。
> 按顺序执行，每步都给了「预期结果」；任何一步不符，先停下对照排查项。

### 1. 构建与启动

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

预期：backend `Up (healthy)`（healthcheck 30s 间隔，start_period 20s 内别慌）、web `Up`。
首次 web 构建约 2~5 分钟（多阶段 npm ci + next build）。

### 2. 存活与页面

```bash
curl http://127.0.0.1:8000/api/health     # 预期 {"status":...}（8000 只绑 127.0.0.1，供本机巡检）
curl -I http://127.0.0.1:3000/workbench   # 预期 200
```

浏览器打开 `http://127.0.0.1:3000/workbench`：行情列表应有实时数据。

### 3. 反代与 CORS（最容易踩的坑）

- 浏览器 DevTools 网络面板确认请求全部走 **同源 `/backend/api/*`**，无 CORS 报错；
- 验证 `BACKEND_ORIGIN` 运行时生效：改 compose 里该变量 → `docker compose -f docker-compose.prod.yml up -d web`
  （**不需要 --build**，Route Handler 每次请求运行时求值）。

### 4. 数据持久化（重启不丢）

```bash
ls data/ashare.db data/parquet/snapshots/   # 容器跑起来后宿主侧应出现/更新
docker compose -f docker-compose.prod.yml restart backend
```

重启后检查：自选、预警规则、模拟单、复盘报告仍在（SQLite 在挂载卷 `/data`）。

### 5. 密钥注入（红线 4）

- backend/.env 经 `env_file` 注入：`docker compose -f docker-compose.prod.yml exec backend env | grep ASHARE_`；
- 确认 key **没进镜像层**：`docker history <backend镜像>` 不应出现密钥内容；
- 严禁 `--build-arg` 传 key。
- **写鉴权 token 也须到 web 容器**（2026-09-14 起，代理层运行时附加请求头）：
  `docker compose -f docker-compose.prod.yml exec web printenv ASHARE_API_TOKEN | wc -c`
  —— 配置后应 >0；**后端配了而这里为 0 ⇒ 所有写接口 401**（读接口不受影响）。
  详见「写鉴权 token 的传递路径」。同理确认 `.env*` 未进 web 镜像层
  （`apps/web/.dockerignore` 已排除，`COPY . .` 不会再把它带进构建层/运行镜像）。

### 6. 接口载荷体检（发布门禁）

```bash
node scripts/api-sweep.js http://127.0.0.1:8000
```

抓「HTTP 200 但数据是空的」——CI 没真实数据跑不了，只能部署后跑。

### 7. WS 与降级

- Route Handler 不代理 WebSocket 升级 → 默认 5s HTTP 轮询自动降级，**功能不受影响**；
- 要实时 WS：前置 nginx 放开 Upgrade 头，或设 `NEXT_PUBLIC_WS_BASE`
  （**构建期内联**，改了必须 `--build` 重构 web 镜像）。

### 8. 已知边界

- **公网部署**：8000 目前只绑 127.0.0.1，公网须由反代统一入口，勿直接暴露；
- **LLM 后端（claude_cli）在容器内不可用**（无 claude 二进制与用户凭据）：复盘/摘要自动降级
  rules 规则层（显式标注 degraded），`/api/assistant/chat` 会返回显式错误而非静默失败；
- `data/parquet/snapshots/` 属本地数据不入库；发现损坏 parquet 手工清理即可（读取侧会自动跳过）。
