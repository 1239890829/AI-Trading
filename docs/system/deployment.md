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

## 离线备份与隔离恢复

GOV-013 使用 `backend/scripts/recovery_bundle.py`，只在运维命令中运行，不从 GET、网页按钮或调度启动。它不加载应用、凭据或通知渠道。SQLite 使用只读连接和 backup API；清单保留 schema、逐表行数/内容哈希、迁移版本和外键检查。其余文件按字节哈希核对；副本相同不代表行情质量或策略有效。

### 范围与前置

- 停止**所选源的全部写者**，包含后端/调度、DuckDB 同步、文件报告及研究写者；`--quiescent` 是操作者确认，不是程序自动证明。SQLite 单库事务一致性不等于多文件全局事务。文件身份及复制前后哈希检查用于发现变化，不能替代停写。
- 默认包含源根下 `data/`、`backend/data/` 的当前文件：用户自选/手工流水、各模拟scope、通知状态/意图/尝试、Agent审计/参数/预算/任务、点时研究/报告/标签、水位、DuckDB与Parquet。表级细分以本次manifest和对应ORM/服务为准，不另建事实注册表。
- 旧 `.bak-` 副本、OS杂物、`.gitkeep/.gitignore`、空的历史 `backend/data/ashare.db` 占位不属于本次当前事实；SQLite WAL/SHM由backup API归并。DuckDB有未checkpoint的WAL时拒绝。应用代码版本单独记录；`.env`、密钥与浏览器私人历史不复制。自定义数据库/数据目录不在这两棵树内时，**该包不覆盖它们**，必须另行制定范围；活动的隔离RSH-031目录不在本次生产数据范围。
- 8GiB/100000文件上限，SQLite复制120秒上限；软链、源/目标重叠、已存在目标、源变化或清单损坏拒绝。仅失败时清理本命令新建的目标；不修改或删除源。

### 执行与对账

从仓库根运行，目标均为不存在的目录。示例仅表示命令，不自动授予停服务/生产恢复权限：

```bash
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/recovery_bundle.py backup --source-root . --destination artifacts/backups/recovery-YYYYMMDD --quiescent --code-head FULL_COMMIT_SHA
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/recovery_bundle.py verify --bundle artifacts/backups/recovery-YYYYMMDD
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/recovery_bundle.py restore --bundle artifacts/backups/recovery-YYYYMMDD --destination artifacts/restores/recovery-YYYYMMDD
```

备份manifest出现并通过verify后才接受；被中断的残留目录不是有效备份。清单只保留统计/哈希，但payload可能含私人记录，整个包保持本机私有、不入Git、不外发。操作者只保存必要恢复点；新恢复点验证及保留义务核清后才退出旧副本，不按缓存删除业务备份。

恢复先写 `data/RESTORE_HOLD.json` 和每个SQLite父目录的同名标记，再复制事实。`get_engine()` 在迁移/服务/调度前拒绝带hold的配置库及软链别名，缓存engine也复查；不靠改旧pending/sending为“已发”或“待重试”制造结论。标记不是OS沙箱，显式离线迁移/只读对账可在指定目标连接执行；直接自行构造连接不属于应用启动放行。

1. 在恢复副本核逐表事实、ID/sequence、scope、水位、未处理意图和版本，再按目标连接迁移。新增空表与必要类型演进单独列出，旧事实行数/哈希不丢；复用paper reconcile核资金/订单/持仓，不自动修余额。
2. 关注/取消关注、已读/清除单调水位、手工修正/删除、分scope撤单、节假日T+1冻结/次交易日一次解冻、参数生效/回滚用独立文件夹具验证再操作；不得用真实恢复副本制造订单或外发求测试。
3. 回滚先考虑**旧版本代码配套的完整备份恢复到新目录并继续hold**。禁止强行downgrade以删成交证据；hunting-shadow迁移已有保留计划硬门。Git回退不撤销外部副作用，也不能保留升级后新事实却声称回到旧时点。
4. 真正恢复运行前，另行获得运行授权，核配置/认证/版本/数据目录、租约代次、任务预算和渠道已受理记录；未知渠道结果保持unknown且停外发，防止旧意图再执行。仅在该对账完成后由操作者明确移除hold，不能把本次“继续”推成生产恢复授权。

本次实测：2026-10-02冻结代码`cc7391009658d64f8046f55be5564776a91a1ec5`，停写检查及本机只读源，11300文件/4649342884字节；backup27.99秒，完整verify后的restore复制/核对14.01秒。45表原事实哈希相同；隔离迁移a4e8c2d9f6b1→e2c6a8f4b9d1后51表，原业务事实不变、外键违规0，paper reconcile异常0。11173份Parquet元数据可读，DuckDB三表可读。watchlist旧group_name可空/默认值、sentiment_history旧server default与新库DDL不同：现有值无NULL、写入消费者显式提供或默认处理，保留兼容差异，不为DDL文本一致盲改生产。原库/日历及独立研究进程未改。

以上时间是**本机该体量离线演练**，restore时间不含之前verify、审批和业务复核；生产RPO/RTO、外部渠道对账与实际重启仍由OPS-003的授权窗口取得，不写成0或保证值。完整私有manifest/恢复/质量证据只存忽略的artifacts，公开材料不含持仓/消息正文。


## OPS-003本机隔离运行回执（2026-10-02）

这是已交付代码的限定运行验收，任务状态只在[W09/OPS-003](../stages/w09-acceptance.md#ops-003)。用户“继续”承接此前明确的10分钟本机隔离方案；生产部署继续搁置。此窗口已结束，不建立永久运行服务。

### 固定范围与隔离

- 源码：干净master `d9f728ad8b352f3b6a6cf415950023469e4afa18`，Git archive仅复制跟踪源码；没有复制生产数据库、日历或dotenv。新库迁移至`e2c6a8f4b9d1`，51表中50张业务表；只有两行公开测试自选股，非用户持仓。
- 窗口：北京时间2026-10-02 23:33:10.561898至23:43:10.559050，共600.028秒。前180秒正常读取，中间120秒代理拒绝腾讯连接，再恢复180秒，480秒处重启同一隔离实例，继续至600秒。覆盖失败、保留事实与恢复，不是长时或跨日验收。
- 两项原任务`quote-poller`/`market-snapshot`启用，其余30项明确禁用；没有前端/浏览器页面。一次性适配器只限定调度范围并将httpx传输指向本机代理，原Provider解析和任务实现未修改。没有模拟Provider返回。
- macOS sandbox只允许写本批目录、执行已核Python入口、访问本机8000/8001；拒绝读原数据和密钥路径。代理仅放行腾讯`qt.gtimg.cn`及新浪`vip.stock.finance.sina.com.cn`的443连接、冻结解析地址，预算400连接/80MiB；实际79连接/2196585传输字节。没有模型凭据、费用或真实通知；临时API token经私有管道注入，不写文件或命令参数。
- 护栏实测：允许目录可写，目录外写、密钥读、非Python子进程及非白名单网络均拒绝。首次沙箱地址语法和Python启动器权限失败发生于正式窗口前，修正实际地址/可执行路径后才开始；没有放宽到外部任意网络。

### 结果与失败分母

| 项目 | 实际证据 | 裁决与范围 |
|---|---|---|
| 代码加载身份 | 两次启动PID有回执；第二实例PID10904的实际导入文件hash逐项与固定副本一致 | 第二实例直接验证；第一实例没有单独留存导入hash清单，不用后一次清单冒充两次均已核 |
| 无页面后台推进 | 腾讯38次真实HTTP200；新浪171次真实HTTP200；保存3份各5571行Parquet，重启后重新采样 | 两项后台独立于页面推进通过；不代表其余30任务 |
| 源失败与恢复 | 注入4次腾讯CONNECT 503；两轮刷新失败，健康degraded/is_stale=true；恢复后23:38:40刷新成功、失败计数归零 | 失败被显式呈现，恢复无需页面触发；故障为注入，非腾讯真实事故 |
| 冷启动 | 初次/重启快照未就绪时degraded；实际取得新浪快照后恢复 | 未就绪未冒充可用；首次/重启快照约65/66秒，不是用户响应SLO |
| GET持久副作用 | 自选/龙头研究/机会/Provider四接口连续两遍，50业务表完整行内容hash均不变；全窗首尾同样不变 | 此四接口、新空业务库与当前禁用范围通过；不外推全部GET或真实订单/私人数据 |
| 鉴权 | OpenAPI 197个非health操作中实发181，全部无凭据401 | 16个未替换参数的操作未覆盖；health唯一公开API存活探针，WS另未验 |
| HTTP分母 | 本机235请求：200共53，401共181，404共1；中位6.28ms、最大1860.6ms | 最大含启动/请求等本机混合场景，不定义生产SLO；误探`/api/paper/state`为404，正确契约是`/api/paper/account`，本轮未补其带凭据业务验收 |
| 重启持久事实 | 50业务表逐行hash/行数、schema不变；隔离实例和代理最终已停止、8000/8001无监听 | 保留数据库事实通过；退出码-15，没有强杀回执，但不把SIGTERM退出冒充所有后台graceful drain已验 |
| 原资产保护 | 原数据库和日历SHA-256前后相同；活动RSH-031 PID81481持续存在 | 原资产未改，未重启研究；不把其存活当有效研究样本 |

代理另外19次拒绝来自不在白名单的日历接口；属于隔离限制，不能当原接口自然失败或通知遗漏。休市源返回的新浪记录只有`ticktime`，`received_at`是接收时间，无法据此确认完整交易日；日历取数未获准放行。健康`ok`只签收本窗口采样链恢复，**不能据此宣称当天实时行情有效、日历覆盖或选股收益**。采集时间目录也不自动等于源交易日期。

没有实施真实前端/Canvas/WS交互、开盘/跨日、真实用户关注/订单/通知渠道或剩余任务；这些仍按原任务取得新输入，不能把本次10分钟窗口计成生产全栈总验。无实际发送，自然不存在本轮渠道受理/送达时延或unknown reconciliation样本。

### 证据、发布与恢复

忽略的`artifacts/runs/ops003-isolated-runtime-20261002/`保留manifest、加载文件hash、沙箱canary、逐条HTTP/故障/阶段回执、前后逻辑表hash、三个公开快照、有限日志及一次性启动脚本；紧凑`acceptance-summary.json`绑定上述文件SHA-256。完整新库含公开自选两行，没有账户/成交/研究样本。已将原checkout的data与backend/data移至本批runtime-data保留唯一实验数据，再清理可再生checkout约20.08MB和pytest临时目录；日志中的旧路径保留时点身份。

本轮只修改运行回执/阶段/索引/细功能/交接文档；未改变生产代码、默认配置、策略阈值、调度开关或安装永久代理。生产启动仍须另有明示授权。没有新增长期架构/机制，因此总方案、Jev、产品设计、AGENTS及Skills不适用传播修改；验收事实已同步当前消费者。准确PR HEAD、三job及DegradedRelease另由GitHub发布回执绑定，不把运行基点冒充本轮文档HEAD。
