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
| **前端连后端** | HTTP及WS均走同源 `/backend`、运行时读取 `BACKEND_ORIGIN`；WS用子协议凭据，失败保留既有轮询降级 |
| **公网 / NAS 必配** | `ASHARE_AUTH_MODE=shared` + `ASHARE_API_TOKEN`（**R22 默认拒绝：全部接口**，backend 与 web 容器都要有）+ `ASHARE_CORS_ORIGINS`（加实际访问域名） |
| **鉴权漏配 = 全站 401** | 后端配了 token 而 web 容器没拿到 ⇒ 连读接口也 401（代理层不附加请求头） |
| **启动验收** | `scripts/local-services.py check`核受保护纯缓存读取与代理鉴权；实际WS另在已授权页面验101及非空行情帧。工程发布仍走 `AGENTS.md` 完整门 |

**章节导航**：本地开发 ｜ Docker ｜ 环境变量 ｜ 前端如何连后端 ｜ 运维工具（全 GET 端点巡检）｜ 运维要点 ｜ 已踩过的坑 ｜ Docker 用户侧验证清单（8 项）

---

## 本地启动与重启

首次安装按根目录 `README.md`。两个独立终端从仓库根启动，后端Python必须已安装项目依赖：

```bash
backend/.venv/bin/python scripts/local-services.py backend
# 另一个终端：已有生产构建直接启动；开发时显式加 --dev
backend/.venv/bin/python scripts/local-services.py frontend
# 两端启动后，在第三个终端验证
backend/.venv/bin/python scripts/local-services.py check
```

入口只解析 `backend/.env` 的鉴权字段，向Next内存传递服务端 `ASHARE_API_TOKEN`、固定回环 `BACKEND_ORIGIN` 和运行模式；不执行 `.env`，不复制供应商、模型或数据库配置。继承环境与文件冲突、重复鉴权字段、未知模式、共享姿态缺凭据或不支持的插值直接拒绝；不会改写凭据。前端要求服务token并保留LAN监听；无token的本地前端启动拒绝，须先配置。设备配对与维护授权见本文对应章节。

前台进程使用 `exec`，同工作区端口锁随服务存活；端口已占用或归属无法确认则拒绝，不启动第二实例。入口不自动安装、构建、终止或重启。生产构建缺失时明确失败；构建仍须先停止同工作区dev，独立工作区不得误停原服务。

重启时先核真实LISTEN的PID、cwd、命令与启动时刻，只正常终止已确认的本项目进程并等待退出；再用以上入口重启。**不得用手工拼环境的 `next start` 代替入口**，否则前端不会自动读取后端凭据。不要向前端复制整份 `.env`。

`check`只读固定回环health、受保护`/api/quotes`缓存与配对状态桥。无凭据直连后端必须401、带服务凭据必须200；未配对前端业务代理与WS票据端点必须401。配对状态桥返回unpaired才证明Next服务凭据可用。空缓存不代表市场数据可用；实际WS与数据新鲜度另验。检查不创建会话，不输出凭据，不做全GET扫荡。

真实WS验收复用获准工作台，配对后完整刷新，核新101、当前证券非空帧与后续行情。每个连接使用新短效票据，不缓存长期凭据。不导出含票据的HAR/请求头，不为启动检查另建工具WS；握手不提升盘前/休市/失败报价质量。

## Docker

**生产部署**（2026-09-04，双镜像构建）：

```bash
docker compose -f docker-compose.prod.yml up -d --build
# web: http://localhost:3000（同源 /backend 反代 → backend:8000）
# backend 仅本机暴露 127.0.0.1:8000（巡检；WS默认经前端同源代理）
```

- 数据持久化：挂载宿主 `./data` → 容器内 `/data`（`REPO_ROOT` 在容器内推导为 `/`，
  SQLite/Parquet 默认路径都落在 `/data`）。**不要挂 `/app/data`，不生效。**
- API Key：`env_file: backend/.env` 注入，绝不进镜像层。
- WS：默认通过 Next 16 `proxy.ts` 对精确升级路径同源转发；认证与失败后的既有轮询降级保留。

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
| NEXT_PUBLIC_WS_BASE | 可选直接 WebSocket 基址；留空走同源升级代理，失败保留既有5s轮询降级；手机忽略旧回环覆盖 |
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

WebSocket 默认连接同源 `/backend/ws/quotes`，由 Next 16 `apps/web/proxy.ts` 在运行时做精确升级路径的外部 rewrite，沿同一个 `BACKEND_ORIGIN` 转发。普通 HTTP 仍走 Route Handler。可选 nginx 或 `NEXT_PUBLIC_WS_BASE` 直接连接，不再是同源实时推送的必需项；既有重连和5s轮询降级保留。手机不能使用访问者自己的 localhost 作为后端，HTTPS页面只接受wss。
启用鉴权后 WS 还需带**子协议凭据**（经同源 `/api/ws-credential` 下发），
nginx 反代时**不要剥掉 `Sec-WebSocket-Protocol`**；详见下文「WebSocket 面」。

### 生产模式自检

```bash
cd apps/web
npx next build                              # dev server 运行时禁止执行
cd ../..
backend/.venv/bin/python scripts/local-services.py frontend
# 另一个终端：核受保护代理及凭据配置，不以豁免health代替
backend/.venv/bin/python scripts/local-services.py check
```

### 鉴权凭据的传递路径（2026-09-14 起；2026-09-15 R22 扩面）

**HTTP 面**的 `X-API-Token` **只在服务端流转**，浏览器侧不持有：

```
已配对浏览器 ──(设备Cookie)──▶ Next Route Handler ──(服务token＋会话＋Origin)──▶ backend
                            ↑ 运行时读服务凭据；后端逐请求核设备与能力
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

- **判据是"两边一致"**：变量缺失时代理不附加请求头，与后端的姿态语义对称。
  后端低层仍保留纯本地无token兼容，但当前前端启动要求配置token及设备配对。**后端配了而 web
  没传 ⇒ 设备桥不可用**。排查（不打印明文）：

```bash
docker compose -f docker-compose.prod.yml exec web printenv ASHARE_API_TOKEN | wc -c
# 0 ⇒ 未注入（除换行外无字符）；>0 ⇒ 已注入
```

### WebSocket 面：设备一次性票据

浏览器使用短效子协议票据；服务端长期token不再返回浏览器。完整初始化、配对、撤销与恢复步骤见本文「设备配对与维护授权」。

```
已配对浏览器 ──GET /api/ws-credential + httpOnly设备Cookie──▶ Next固定桥
   ▲                     后端核设备/Origin，签发60秒一次性票据
   └── new WebSocket(url, [ashare-ticket.…]) ──▶ 后端原子消费票据
```

每个连接独立签发，不共享、不缓存、不允许重放。刷新、重连重新取票；未配对返回401并保持配对页。后端连接定期核撤销，不因握手成功把行情质量升级。Next `proxy.ts`和nginx只原样转交子协议，不注入长期服务密钥。

2026-09-15旧协议曾把编码后的服务token交给前端。编码不提供保密或设备身份边界；本批首次激活必须轮换旧token。旧协议仅为无浏览器Origin的获准服务调用保留，不能作为浏览器回退路径。后端继续只监听回环，LAN仅开放前端。

## 运维工具：全 GET 端点巡检

```bash
node scripts/api-sweep.js                    # 默认打 http://127.0.0.1:8000
node scripts/api-sweep.js http://nas:8000    # 指定目标

# R22 后：后端启用鉴权时必须给凭据，否则**全部端点 401**（会被误读成"全站挂了"）
ASHARE_API_TOKEN="$(sed -n 's/^ASHARE_API_TOKEN=//p' backend/.env)" node scripts/api-sweep.js
```

从 `/openapi.json` 取权威清单（不会漏也不会多），自动替换路径参数、填必需查询参数，
逐个打真实后端并输出非 2xx 明细与慢端点。**GET不保证无副作用**：部分旧端点会外采、历史回补或触发模型。该全扫脚本不作为生产验收默认入口；生产仅使用已核纯读取白名单，完整巡检须隔离库、网络和后台任务。
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
- 免费数据源被限流或失败时保留真实 stale/失败状态。先核请求去重、并发、回退和源钟；不得为掩盖故障增大刷新周期、TTL或过期阈值。备源需同口径实测后按现役协议准入。

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

- Route Handler 处理普通 HTTP；Next 16 `proxy.ts` 对同源 WebSocket 精确升级路径做运行时转发，原子协议/鉴权与失败降级保留；
- 可选直接 WS：前置 nginx 放开 Upgrade 头，或设 `NEXT_PUBLIC_WS_BASE`
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

## OPS-003鉴权与WS补验（2026-10-03）

本批承接用户“继续执行任务”，只验收已交付代码的HTTP鉴权与WS传输。普通G0–G4 selector为空，未开启新的业务开发。上一批十分钟运行保留其时点身份，本批补齐参数遗漏并新增真实WS连接证据。

### 输入、窗口与隔离

- 基点为干净master `c60d03ec18eec241e4b138471f89792eee8c5ba6`，Git archive生成固定源码副本；实际PID46300导入的182个app模块hash与副本一致。新库迁移至`e2c6a8f4b9d1`，51表中50张业务表，公开自选只有600127、000001。没有复制原数据库、日历、dotenv或用户账户。
- 事先限定最长180秒，断言完成即停。实际就绪窗口北京时间12:07:37.382794至12:07:40.637112，共3.255秒；包含冷启动的实验计时5.208秒，最终进程/代理关闭计时5.415秒。不能写成运行三分钟或长时稳定通过。
- 原`quote-poller`一项运行，其余31项禁用。一次性适配器仅限定任务和本机代理传输；API/WS路由、凭据判定和行情解析均使用原代码。无前端实例，WS客户端为真实网络协议客户端，非浏览器用户流或TestClient。
- macOS sandbox仅允许本批写入、已核Python入口及回环8000/8001；原数据/密钥路径读取和其它进程/外网拒绝。五项canary通过。代理只允许`qt.gtimg.cn:443`，新解析地址固定，预算20连接/4MiB；临时API token经私有管道注入，无模型/渠道密钥。全部写请求只用缺失或错误凭据，没有带合法凭据执行写操作。

### 实测与可成立的结论

| 项目 | 本批实测 | 适用范围 |
|---|---|---|
| HTTP全操作鉴权 | 从当前实例OpenAPI取得197个非health操作，通用替换所有路径参数；无凭据和错误凭据各197次，全部401，零跳过 | 补齐上批16操作；拒绝行为通过，不表示197项业务功能均通过 |
| HTTP分母 | 实际409次请求：394次401、15次200，无HTTP失败；中位6.64ms、最大1946.5ms含冷启动 | 混合本机请求统计，不是生产响应SLO；200包含公开health/OpenAPI与带合法凭据的限定读取 |
| WS拒绝 | 缺子协议、错误编码凭据、格式异常子协议、仅HTTP凭据头四种握手均HTTP403；订阅数保持0 | 在accept前拒绝；没有把403记成WS成功建连后关闭码1008 |
| WS合法连接与换股 | 两次合法连接准确回显客户端子协议；每次3份snapshot、3份pong；600127→000001→两股，seq严格递增；非法JSON后后续订阅和心跳仍可用 | 验证真实握手、路由消息和订阅变更；裸000001为SZ股票，没有混入SH上证指数 |
| WS断线/重连 | 初连与重连每次订阅数0→1→0；队列上限64、累计丢帧0 | 此两次短连接未残留订阅；未测试慢客户端、有负载丢帧、自动重连或持续行情增量 |
| 来源与质量 | 腾讯4次真实HTTP200；WS中的两股源时间为9月30日，均`quality=stale`/`quote_age_exceeded` | 实源最近交易日数据传输通过，不冒充10月3日实盘，也不证明休市日历覆盖 |
| 持久事实与原资产 | 50业务表完整行hash/行数与schema前后相同；原数据库/日历SHA-256不变；RSH-031 PID81481仍存活 | 无业务写入或研究重启；研究进程存活不表示研究样本有效 |
| 停止与恢复 | 进程/代理已停，8000/8001无监听；日志含application shutdown complete，退出码-15，无强杀 | 本隔离实例收尾完成；31项被禁任务的drain不在验收范围 |

代理6次CONNECT中4次放行、2次拒绝，共29182传输字节；两次拒绝仍为白名单外的日历访问，属于隔离限制。没有故障注入、重启或跨日试验，不重复上一批休市恢复窗口。源码导入hash只证明实际已导入的182模块，不扩大为全模块覆盖或前端构建身份。

### 回填、证据与剩余条件

忽略的`artifacts/runs/ops003-auth-ws-runtime-20261003/`保留源码manifest、加载hash、沙箱canary、197操作清单、16项新增覆盖、逐请求回执、两份WS消息、前后表hash、日志与绑定SHA-256的`acceptance-summary.json`。唯一新库已移至本批runtime-data，约22.64MB可再生源码副本与本批目录外canary已清理；原研究副本和恢复备份保留。

本批关闭HTTP16项漏测及限定WS协议缺口，浏览器接续证据见下节。OPS-003整项仍按[W09](../stages/w09-acceptance.md#ops-003)保留部分完成；持续行情增量、剩余后台任务、开盘/跨日、通知检测/排队/受理/unknown对账与生产运行需各自新输入或授权，不重复静态清单求更多轮数。

本轮只回填deployment、W09、细功能、INDEX与handoff。原代码、鉴权/交易规则、默认配置及长期方案没有变化，AGENTS/Skills/总方案/产品蓝图/Jev/plan-registry无需修改。准确文档HEAD的DegradedRelease、三job、release_check与主干CI另由PR记录绑定；作者自审不冒充独立Review。

### 同轮接续：浏览器凭据与换股图表

用同一固定源码另建隔离新库，Node `v24.14.0` / Next `16.3.3`运行开发Webpack实例；不是生产build。API源码与WS判定继续原样使用；只有quote-poller一项启用。Next运行时读取经私有管道传入的临时token，实际`/api/ws-credential`与预期子协议相符、响应`cache-control: no-store`；浏览器不带token请求Next代理的自选GET为200。前端由固定源码副本启动，但清理前route hash扫描选错开发产物目录，清单为空；没有取得前端实际编译route hash，不能证明生产build/完整加载身份。后端PID52469的182模块加载hash与副本一致。

事先最长600秒，实际北京时间12:16:45.361202至12:26:30.574803，共585.238秒，585.475秒关闭全部实例。macOS沙箱限定新批目录写入、已核Node/Python与回环3000/8000/8001；复用原node_modules只读，不写原构建目录。代理新增公开腾讯`web.ifzq.gtimg.cn:443`供日K/分时和日历，冻结新解析地址，预算200连接/16MiB；实际115 CONNECT、1007720字节，74放行、26拒绝，另外连接/超时状态不由这两个数推算成功率。没有模型/渠道密钥、真实订单或生产启动。

| 场景 | 实际观察 | 可签收及限制 |
|---|---|---|
| 对象与换股 | 原生浏览器从金健米业600127切平安银行000001；名称/SH或SZ/报价/盘口/日K均跟随，银行11.57没有变成上证指数3842.19 | 此两股真实页面与协议联动通过；没有覆盖全证券/账户scope/乱序回包 |
| 真实Canvas | 目视银行日K与分时、金健日K；DOM/截图与同版实源响应分开保留。银行分时267点、源日期9月30日、末点价11.57，与图中末价/报价相符 | 曲线实际绘制、对象和所核价格相符；不等于所有复权/会话过滤/指标、光标、缩放或像素几何已穷举 |
| 刷新与旧链 | 银行页面刷新后仍为SZ000001；`/stock/600127`最终跳转`/workbench?symbol=600127`，恢复金健报价/盘口/日K | 对象恢复通过；分时按钮的本地选中态没有持久化为URL，未签收所有视图状态恢复 |
| 降级与恢复 | 正常显示“休市 · 展示最近交易日数据”；四次quote-poller ReadTimeout期间显示“数据过期 · 后端刷新异常”，保留值，后续源200继续到来 | 有失败反馈，不冒充当天实时；超时可能含受限代理/网络影响，没有认定腾讯自然事故或生产可靠性 |
| 持久写入 | 50业务表中49表hash/行数不变；`notification_read_state`从0行到1行，对应前端自动PUT一次200 | 是通知已读同步的明确写动作，非GET生成研究/订单；不得写“浏览器访问数据库完全不变” |
| 浏览器关闭 | 原生标签清单已空，随后后端仍有1订阅；累计丢帧0 | 未通过“关标签即订阅归零”。原生宿主保留webview还是应用清理问题证据不足；先前协议客户端0→1→0证据不覆盖此差异 |
| 停机与保护 | 前端退出0、后端-15，日志application shutdown complete；3000/8000/8001无监听，原数据库/日历hash不变 | 本窗口回收完成；没有借此签收浏览器清理未知项或31项禁用后台 |

前端访问日志实际473次：GET472、通知已读PUT1；200共413、503共12、400共10、502共34、500共2、404共2。503为无题材源配置，400为首帧空symbols的sparkline调用，404为浏览器Apple图标；502含未放行/未配置的资料源与行情超时。两次`/backend/api/picks/today`500伴随Next日志`Unexpected end of JSON input`，后续同接口200，未保存这两次响应体/完整内部栈，原因未定；**不能称浏览器全栈全绿**。这些观测与关闭残留都保留在OPS-003原owner，不凭隔离/开发环境现象修改生产代码或放宽门禁。

忽略批`artifacts/runs/ops003-browser-runtime-20261003/`保存各对象AX、三张截图、原分时/报价响应、HTTP分母、前后表hash、后端加载hash/前端空产物扫描回执、沙箱/来源/停机日志和绑定SHA-256的summary。约162.84MB可再生源码与Next构建缓存已清理，唯一新库保留，标签已关闭。未操作RSH-031或GOV-013备份。

本轮新增关闭浏览器凭据、两股换股、刷新、旧链接和限定Canvas实源证据缺口；下一验收要有差异：固定生产build的隔离用户流/500复现、可观测的浏览器卸载/订阅清理、后续真实交易日与通知条件。未获生产/真实渠道授权的部分继续等待，不重复相同开发窗口或通过更多静态截图来替代。

## OPS-003固定生产构建与订阅退出（2026-10-03）

本轮按上批差异输入验收已交付代码，不重复静态鉴权清单。固定源码`93253017e096d808a4b5d15da26fc404653612e5`，独立新库；Node `v24.14.0` / Next `16.3.3`，隔离执行`next build --webpack`后`next start`。构建12.432秒、无构建凭据，build ID `MvGczT1r4NiwPWZ-smkK4`；不是GitHub CI产物或实际生产部署。新库schema `e2c6a8f4b9d1`，182个预启动导入app模块hash与固定源码一致。

**U49作者Preflight与反证**：最新master与普通selector核无候选，OPS-003硬依赖均已完成；G5只验收原已交付代码。先冻结构建身份、真实页面卸载/刷新/关标签、失败分母与停止条件；区分框架生产模式和用户生产部署，ASGI断开与宿主标签清单，源能力缺失与原上游故障。仍为DEGRADED_FULL_CONTROL作者自检，非独立Review。

最长420秒、实际257.140秒，257.364秒连进程/代理关闭。原quote-poller运行，31项后台禁用；沙箱只允许本批写入、验证解释器和localhost端口，真实腾讯行情/K线通过限定域名/IP代理。五项沙箱canary通过。代理61次CONNECT、48次放行、6次拒绝、502057字节；其它为连接/超时等，数字不构成完整互斥成功率分母。无模型、订单、真实渠道发送或生产凭据。

| 差异验收 | 本轮实测 | 结论边界 |
|---|---|---|
| 实际构建身份 | 105份server/manifest文件SHA-256；运行结束前全部不变。浏览器DOM实际引用的16个脚本，经服务返回的字节hash全部与构建文件相同；运行PID98210和build ID绑定 | 补齐上批编译哈希缺口；没有声称105份文件均已执行、其它构建器/版本/部署也相同 |
| 页面卸载与刷新 | 工作台→复盘研究，ASGI收到断开并订阅归零；重开及刷新，旧连接结束后新连接建立。全窗6个WS scope均有结束回执，最高同时2个订阅、丢帧0 | 此场景无永久孤儿；不把短暂双连接写成全窗单连接，也不覆盖慢客户端/持续增量 |
| 原生标签关闭 | 标签清单为空；关闭动作后约0.119秒后台收到1001，scope结束后订阅0。约65.987秒后实际HTTP读数仍0/queue64/drop0，窗口内未再建连接 | 本生产构建场景通过；上批开发实例为何残留1个仍未定位，不能改写历史或声称全域修复 |
| Next 500差异输入 | 4并发线程、20次真实前端代理`picks/today`均200且可解析JSON；后台此接口24次均200（含前端流/直接探针）；前端日志无上批JSON.parse栈、ASGI异常0 | 本窗未复现。全前端HTTP状态未逐条捕获，不能把后台分母当全前端500分母，不能定为已修复 |
| 真实用户流/画面 | 金健米业名称、13.14、盘口和真实日K Canvas一致；切平安银行SZ000001/11.57，导航、重开与刷新可核 | 报价源仍9月30日，界面标休市。银行K线/分时及盘口本窗遭上游/代理连接超时，未签收银行完整图表成功；已有上批开发证据保持原身份 |
| HTTP失败分母 | ASGI完整169请求：GET168/PUT1；200为134、503为4、400为3、502为27、404为1；逐请求状态/body hash及失败响应捕获 | 400为首次空symbols，404为探针误用不存在的`market/envelope`，改查实际`quotes`信封。20个502是单腾讯provider没有资金/竞价/财报/公司/新闻方法，其余7为行情/K线/盘口/分时连接失败；不冒称生产全绿 |
| 持久事实与停机 | 49/50业务表不变；已读表0→1来自实际前端PUT一次。原数据库/日历hash不变，RSH-031未操作。前端143、后端-15退出，无强杀，3000/8000/8001无监听 | 不把读状态同步写成无写入；31项禁用任务drain、渠道受理和生产RPO/RTO未验 |

分时失败的TDX备源一次被沙箱拒绝，另一次为指数码不在备源支持域；不将这些失败归因于腾讯自然故障。ASGI观察层只记录响应、断开与订阅变化，不改应用路由/Provider/WS生命周期。前端源失败时银行K线仍显示“等待K线数据”，此失败反馈线索留给后续明确复现/整改，不把未完成画面计为通过。

紧凑证据在忽略的`artifacts/runs/ops003-production-runtime-20261003/`：manifest、build/served资源hash、ASGI逐请求/断开记录、20次代理探针、前后表hash、截图/AX、日志和一次性脚本；acceptance-summary绑定文件hash。保留唯一新库；已停止的可再生源码/构建缓存156825519字节和自有canary已清理。应用代码/默认配置未修改。

本轮构建身份和此生产构建的卸载/关标签验收收口。上批开发500与残留订阅转为**未复现的历史线索**，仅在相同故障再现或有新堆栈/宿主连接证据时重开；不重复同一休市窗口求“证明永远不发生”。OPS-003整体仍按真实交易日、其它后台、源失败反馈、慢客户端/增量和真实渠道授权验收；生产部署仍搁置。发布只签收本批事实文档，准确HEAD结果看PR回执。

### 归档误标的窄范围恢复

从backend运行`PYTHONPATH= .venv/bin/python -m scripts.review_archive_repair`只读出计划。核对后以`--apply-digest`绑定精确摘要，并用`--backup`指定不存在的SQLite备份文件。只有完整自动“已写入日报”说明、状态applied且指纹未变的行改为recorded；人工状态不覆盖。应用需已加载本轮修复，避免旧运行器重新误标。

恢复用`--restore-digest`及另一个不存在的备份目标，只恢复仍与修复后状态一致的原行，后续人工变化使整批拒绝。该工具不恢复整个数据库、不删除审计、不改变参数/策略或通知。备份、实际摘要、数量、运行版本和前后核验只保留于受控本地产物；历史归档误标纠正不等于原建议已经实施。


## 设备配对与维护授权（2026-10-16 用户批准，统一激活后适用）

### 首次启用与日常使用

1. 后端与前端继续由local-services.py启动。前端必须有服务token，即使local模式也不再接受无token启动。后端仍只监听回环。
2. 在电脑项目终端运行`backend/.venv/bin/python scripts/local-services.py pairing-code`。命令先核8000/3000进程归属，再向本机签发5分钟一次性初始化码。只把它输入电脑网页的“电脑首次启用”，不要放聊天、日志或报告。
3. 手机点“请求配对”，管理电脑从顶部“设备与权限”核对名称和6位识别码后批准。请求5分钟过期；批准会话30天，不因读取自动续期。相同设备换浏览器、访问主机名或清Cookie需重新配对。
4. 普通会话可用行情、选股、既有研究、自选、人工记录和通知。后台生产/参数/内部模拟/未登记端点默认维护；按FastAPI注册模板精确匹配，新增相似路径不继承个股权限；管理电脑可为自己或已配对设备单独授予15分钟维护。维护不赋予设备管理权，不替代独立参数晋级审批。
5. 管理电脑丢失会话时，使用`pairing-code --recover`。生成码本身不撤销设备，成功使用恢复码时原设备与未用WS票据全部撤销；此命令不自动执行。

### 路由身份验收

普通数据读取对管理电脑和已配对手机都不要求维护授权。FastAPI 0.137以后，`scope["route"].path`可能缺少include前缀；权限检查使用已匹配的effective route context完整模板，并包含子应用挂载前缀。上下文缺失或身份冲突不从用户URL猜权限。必须用生产实际注册的路由验普通读写和维护拒绝，不能只测直接挂在测试app上的端点。生产状态抽屉中的状态可普通读取，维护命令仅在当前维护授权有效时显示和执行。

### 凭据与失败语义

- Next只接受httpOnly、SameSite=Strict设备Cookie，丢弃访客伪造的权限头，再把会话与来源交给后端。写请求必须同源；读请求拒绝cross-site/same-site与不匹配Origin。通用代理不得访问access桥；初始化码签发还要求真实回环调用且不携带任何浏览器/桥标识。
- 会话、初始化码与WS票据只存SHA-256摘要。独立`data/device-access.sqlite3`权限600，最多20待批、100活动设备，每设备32未用票据。过期/撤销记录在写入时清理，无新调度。测试路径由conftest统一隔离。权限库失败直接拒绝，不放宽权限，也不依赖行情库锁。
- WS每个连接申请一张60秒票据，不能共享或重放；绑定设备和Origin，浏览器不能再用长期token。刷新/重连重新签发，不更改行情刷新频率或源时效。现有连接每15秒只读权限小库；退出本设备立即卸载业务连接，其它设备撤销最多15秒终止WS。已经获准且在执行中的HTTP动作不会被伪称已取消。
- 浏览器助手只保留普通研究工具；即使临时维护获准，聊天也不读取内部模拟、参数变更或后台任务。后台查看通过维护界面进入，原服务端工具通道保留。普通“浏览工作区”和快捷命令面板仍可用，仅隐藏维护入口；授权结束后已打开的命令面板同步收敛。
- HTTP后续请求检查当前权限；客户端维护授权到期立即卸载维护组件，迟到的旧状态响应不能覆盖退出状态。网络失败不假装权限已恢复。
- HTTPS才设置Secure Cookie；现役LAN HTTP不提供传输加密，只用于可信网络。本批没有新增公网/TLS部署。配对不是抗恶意宿主、同源XSS或网络窃听的完整防线。
- 采用OWASP会话管理/CSRF基本原则，参考[Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)与[CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)。摘要不是加密；浏览器配对与本机操作员是不同信任边界。

### 首次迁移与验收

旧版本曾把编码后的长期token交给浏览器，编码不是保密。因此统一激活时轮换backend/.env中的服务token，再由统一启动脚本最小传递；不输出token，不保留旧前端继续开放LAN，不自动信任旧浏览器。权限小库属于业务访问状态，清理工具不得删。此前普通局域网代理/长期WS凭据说明保留为历史，以上协议取代该访问边界。

`local-services.py check`只读核后端服务认证、前端未配对HTTP/WS返回401、配对状态桥可验证服务端凭据；不获取长期凭据、不创建会话或工具WS。实际刷新/票据重连在隔离用户流与统一激活后的已授权页面分别验。行情源新鲜度、真实交易窗口与手机真机性能仍独立验收。

## TradingAgents完整研究运行边界

2026-10-20新增的工程链复用原AgentTask/任务中心及个股报告读者，固定上游v0.7.0/e8f5503；不会拆成另一套角色流程。`ASHARE_TRADINGAGENTS_RESEARCH_ENABLED=0`为默认值；关闭时bootstrap不读取可选运行环境或启动CLI。旧[同输入对照](../ai/semantic-decisions.md#2026-10-20-六新场景完整图与四臂对照)和新[三主模型筛选](../ai/semantic-decisions.md#用户改为三主模型整图筛选)分别保留，候选及参数试验不是生产净收益准入，不能把以下环境准备当作启用批准。

服务器配置单点为忽略目录`backend/data/tradingagents_research/runtime.json`，只含`version=1`、`upstream_sha`、`upstream_dir`、`python`、`dependencies`、`dependency_manifest`、`transport`七键。路径仅由服务器操作员配置为绝对路径；正常新建HTTP只接受证券symbol，专用维护恢复仅接受原task及run身份，不接受主机路径。transport绑定现有CLI程序SHA、模型/effort和认证文件位置，仅允许四个标准代理变量；不包含或公开凭据内容。若CLI是Node脚本，transport还须绑定`node_executable`及`node_executable_sha256`，使用准确已有解释器执行；缺失/漂移在推理前拒绝。原生CLI不额外要求Node。固定原图276个源码文件逐hash核实；独立venv及相对文件SHA清单由`freeze_dependency_manifest`显式准备，bootstrap不安装、升级或自动生成环境。环境首次/执行前完整核，普通读取复用已核观察；依赖、代码、CLI或准入版本漂移则拒绝新执行。

2026-10-21真实CLI预检发现npm入口解析symlink后丢失Node搜索路径，原返回127及`env: node: No such file or directory`保全；没有发出Codex模型请求。显式绑定既有Node后，同CLI/同隔离参数的实际features预检返回0，全部禁止项仍false，并在模型启动标记前停止验证；不是放宽隔离或换模型通道。完整图零模型预演不能代替这条实际传输入口检查。

2026-10-21用户追加的当前启用方式：真正入选后解释/复盘采用独立`RESEARCH_ONLY_AUTHORIZED`授权，由`ASHARE_TRADINGAGENTS_RESEARCH_AUTHORIZATION_DIGEST`绑定同目录`authorization.json`；准确方法、用户`EXPLICIT`授权、有效期、`net_gain_verified=false`与`production_eligible=false`均核实。旧`ASHARE_TRADINGAGENTS_RESEARCH_ADMISSION_DIGEST`／`admission.json`仍代表效果准入，不能拿研究授权填成`VALIDATED`或净收益通过。原8次与22次冲突不再限制这个专属研究scope，逐调用真实用量仍记账，其他消费者配额不变；墙钟、输入输出、权限、未知及取消排空保留。

后台仅从当日持久daily/top发现真实新入选，排除参考/题材参与/audit-only；在原selection claim owner冻结pending，已有研究active也先保留，pending不是queued AgentTask。已接纳身份不因午夜或后来名单移出而自动取消，原trade_date/selected_at/rank及证据不改；关闭research、授权撤销、取消或坏证据仍阻断。派发继续只传symbol，handler/driver凭服务器task_id、created_by=scheduler及claim归属验证冻结原selection，普通HTTP symbol不获任意历史启动权限。同股同日和同入选身份复用claim，报价刷新不重跑；后采材料用真实as_of/版本，只作原入选后的研究补充，不回填原通知。失败/取消/unknown保留原任务，不自动换输入重发。SchedulerRegistry原清单承接此消费者，启动只装配固定已有环境，不新增下载/安装；用户撤销时关闭原开关并保留结果/记账。新授权模式同时退出旧宽范围公司自动selector，免费原文采集由完整研究前置承接；其他显式维护能力不变。

此前合法开工的手动任务跨午夜只续原冻结intent，普通HTTP不能首次补开历史入选。handler/native与每次reserve/start继续核同一expected intent、真实task owner、原来源、授权和取消；claim删除、task_id漂移或intent被抹除必须拒绝，不回落普通接纳。原claim仍是单写者/CAS，未新增请求自授参数或平行账本。

2026-10-23待发用量修补分别验证可信完整终态、严格tokens与答案JSON/最终文件/schema；失败答案仍拒绝，只有独立终态用量证明可failed+known。权限/安全、turn.failed、乱序、多终态、截断、无终态/非法tokens等仍unknown；原七条unknown不靠新规则自动核账，具名许可只解除对应ID的暂停，不修复其原因。DB只重试原幂等finish，不能重跑模型或把数字观察直接入账。固定有界`error_observations`仅类目/类型/长度/位置/哈希并标`channel_report_only`，不含原文、URL、路径、请求/context；未分类就明确不清楚，不作为重试或根因证明。当前4/8与第7许可归[语义记录](../ai/semantic-decisions.md#2026-10-23-终态用量与答复准入分离)；本批完整门、准确HEAD发布与加载事实由关联PR及回执接续核验，施工快照不证明已加载，不能用旧a78/e674运行或旧CI替代。

正常构包要求验证公司profile；新选股后研究缺profile时先复用既有免费采集，仍未取得则在模型前真实阻断，不先运行公司selector模型。显式任务可复用现有免费实例，单次取得400日历日内最多300根qfq日线及8期财务摘要；有效报价只读现有Hub缓存，新闻只读已有库的原始公开观察，普通GET不触发这些操作。来源/证券/单位/质量/真实源钟与接收钟按[构包契约](../data/data-sources.md#tradingagents显式研究构包2026-10-21)分别绑定；实际采集后按真实截点冻结，不把新行情填入旧profile时刻。日线形成中尾条排除，原指标公式对冻结历史计算，完整财表、首次发布精确时刻、独立最终性、社交及账户仍明确缺失。新来源与方法身份改变后须重新验证准入，不能沿用旧149次模型结果或旧质量digest。

完整待核七报告只写研究目录，绑定证券、输入/方法/结果hash和原生成/资料时刻；普通配对GET可读取，源/引用有限失败与同伴待审明示，不变成已验证结论。完成后只清该run自有的原生checkpoint数据库残页，保留报告、原答和MemoryLog；失败、中断或未知则保留恢复证据。初版无自动调度；当前仅增加上述真正入选研究调度，不发新通知、不改变选股评分、资金或部署边界。现役页面加载、已保存结果读取、真正模型任务及质量准入仍须分别取实际证据。

超时不算成功。CLI先真实TERM/必要KILL并等待排空，再仅从有界typed流中提取严格单轮最终`turn.completed`用量和安全终态投影；原始请求/context不落日志。缺最终、截断、多轮、非法计数仍为unknown，安全/授权停止优先且不取用量。超时失败与本地drain事实分开保存，不能推断远端已取消。2026-10-21已丢失原流的call11无法由此追溯恢复，原unknown和停止回执永久保留。用户随后明确授权在原100预约内有限接续，准确边界见[三主模型记录](../ai/semantic-decisions.md#用户改为三主模型整图筛选)；这不是超时修补自身产生的权限，也不豁免任何新unknown、权限或安全停止。该有限LAB例外不改变历史账；当前选股后研究的独立启动授权与无次数配额规则以上文为准，效果门不变。


### 完整研究的显式原生恢复

正常新建与后台领取不自动重试失败/unknown。维护恢复复用`tradingagents_research`任务类型和原单写者、逐调用授权/记账、取消排空与墙钟边界；专用`POST /agent/tasks/{source_task_id}/recover`不属于普通设备读权限，body只声明`run_id`。当前服务器已pin的`authorization.json`必须包含该原任务/run的单次精确恢复grant，不以HTTP写入授权或任意换输入。

恢复前核当前方法/授权、原失败任务、selection claim、冻结packet/config/staged代码/checkpoint及全部已保存输出/回执，登记旧/新方法兼容依据。队列与单次claim按owner/CAS处理部分失败，不把不明dispatch当成新任务重新领取；执行从原生未完成节点继续，新计数接续，已完成角色在模型预留前拒绝重生成。旧failed任务终态不改，原失败历史先保全，结果带有界恢复lineage；GET继续只读取，结果完成也不使来源或投资效果自动通过。授权撤销/漂移、新unknown、输入或checkpoint漂移、安全失败照常停止。实际执行须在准确HEAD工程发布后另核原答哈希、首次新角色、调用区间、七报告与普通页面读取。

首调用失败且零原答的初始恢复仅允许counter1/call1、step0四分析pending、原错误与消息指纹、精确receipt/原DB failed-unknown及具名许可、完整源绑定一致；保留物理absence，不造model-output或空占位，下次物理调用从2。已有答、普通单角色、非初始、缺许可、未来答预置或漂移仍拒绝，详见[同批恢复合同](../ai/semantic-decisions.md#2026-10-21-cli启动警告与重复失败恢复纠错)。该施工快照不证明002803恢复成功，也不把历史unknown升known。

冻结checkpoint读取不能仅以SQLite `mode=ro`声称无写入：WAL格式但无sidecar的原库仍可能被创建wal/shm。只有EXPLICIT首call1零答/step0、源failed、已绑定worker终态与checkpoint.files、主DB SHA一致，且绑定及现场wal/shm同时缺失，才走正式窄`immutable=1`入口；前后都核哈希与absence。默认、活动库或已有WAL不自动immutable，不删sidecar求通过、不在实服stub。2026-10-23因此主动中断的144.111秒pytest不是完整绿色；原142/244定向范围、新helper门与准确HEAD发布/加载各自留证，详见上述语义合同。

PR #325历史方法迁移只增加已审`tradingagents_source_gate.py`与`transport_process.py`的两个准确有向hash对，完整值归[语义迁移记录](../ai/semantic-decisions.md#2026-10-21-cli启动警告与重复失败恢复纠错)。原9281→d88对保留，四个通用host组件allowlist不扩；旧许可不自动迁移，graph/model/deps/packet/tools及原答/账行不变。当前服务器grant须完整pin准确old/new，未来hash、逆向或缺old均拒绝。源码兼容不是803恢复已执行或新授权；完整门、准确HEAD发布与加载事实按本轮关联PR及回执核验。


2026-10-21正式恢复实际失败后补充：再次续恢复仍使用同一维护POST与服务器digest-pin授权；每个已发生恢复段的任务、方法、原grant归档、调用区间和claim哈希显式进入当前恢复绑定。只接受连续、无重叠且失败/取消终态的历史段；旧claim及原用量不可删除或改写，未知队列不能当失败重领。普通读者和调度复用时核完整历史链及结果身份，公开lineage继续保留真正原source与所有继承unknown数量。新增unknown另获授权前不启动，历史grant过期不回写旧事实，当前grant仍须在其有效窗口内。

CLI启动warning的兼容依据与固定版本见[语义纠错记录](../ai/semantic-decisions.md#2026-10-21-cli启动警告与重复失败恢复纠错)。只接受官方非致命启动警告，不放行终端后追加或权限/安全错误；有界顺序诊断不保存警告原文或完整请求。


### 2026-10-23 发送前输入失败的精确续接

本轮仅对原counter已前移但reserve/start尚未发生的input_budget_exceeded增加独立input_prelaunch_tail证明。完整合同在[语义蓝图](../ai/semantic-decisions.md#2026-10-23-无损证据呈现与输入前失败续接)：原源码/数字模板、全部已知账与N−1回执、单末槽absence、原三角色终答及Market wrap_up指纹、worker失败终态和原packet/checkpoint/config/staged身份均须精确绑定。新调用从N+1继续，不重跑原答、不造槽N用量；不是自动重试或新unknown的许可。Market wrap_up先经已钉上游`_as_text`再经native serializer，新grant单独绑定实际wire指纹，原tail/checkpoint指纹不覆盖。继承已失败恢复只限原输入前tail的准确零调用history：每段N+1→N、已失败task/claim/grant/归档与整task model账0均实核，任何reservation也拒绝。原BudgetError从首archive证明，当前失败与终态另绑定；未来既有execution保存有界具体ResearchBlocked code。无sidecar的已终止检查点仍走正式immutable窄入口，前后哈希/absence一致，不在运行服务中stub读取。

服务器单次grant采用独立frozen-packet-owner compatibility kind时，显式差异集合只可为服务端已审的quote_hub展示修补与Tencent纯search的准确正向hash对，声明恰等于实际变化；其余packet_builder、model/graph/依赖/工具与原packet严格不变，执行不得重新调用builder。原kind保持原约束。授权安装前核发布的准确HEAD、本地完整门、required CI、release_check与master CI，核0 active及dispatch单写者；保留原授权备份和七条具名unknown原账，不改变原外层到期或单次资源门。

调用维护POST只能声明source_task_id/run_id，实际源/目标方法与grant由服务器pin；准备grant和离线读取器成功不等恢复已执行。激活后另核首个新物理调用、原件哈希、known账闭合、12角色/七报告/Memory及普通GET/实页读取；旧报告继续可读，研究效果和人工gold不因工程修补晋级。


### 2026-10-23 快照故障承接与只读界面更新

全市场普通ProviderError/httpx故障且原快照unavailable/stale时，复用已配置批量备源一次；新鲜degraded不发额外全量，456冷却和主源退避保留。采集等待复用原新鲜窗；共享HTTP owner与保存不冒称同一硬期限，详见性能§35。成员仅active/可见时读取原内存快照。组件交互、报告对照或刷新仍不创建模型任务；短视图和完整原文共用原证券/日期/方法身份。源守卫v8变更需准确新方法授权及原未调用pending承接；旧八报告/原恢复grant/七unknown保留，安装前只读验证，发布/主干CI/停止旧进程/启动器加载与实际页面分别记证。

## RSH-031确定性前向采集

采集合同、授权期限及证据限制归[研究§39.5](../research/limit-up-dragon-research.md#395-准点采集修复与有限新批2026-10-23)。`backend/scripts/rsh031_capture_service.py`只运行原免费早/收盘叶脚本，输出限忽略artifacts；不启动完整后端、不读取.env或模型、不改生产库。

本机user LaunchAgent参数固定为现有解释器、已发布小型Python源码副本、批次、日历和宇宙。源码副本只含两脚本与backend/app，清单绑定发布commit和文件hash；不复制.env、依赖树或业务库。活动输出为安装回执指向的该副本artifacts/rsh031-forward，外层不复制health/PID作为另一事实源。RunAtLoad与工作日09:45/15:10预先启动，09:55/15:25才取源；失败由launchd有界节奏重启，STOP/期限/容量正常停止。不要手写第二个collector或让聊天再次调叶脚本。用脚本`--help`核当前参数及`print-plist`生成结果，核JSON/锁/PID/health和launchctl实际状态，不能只看旧manifest。

停止先在确认批次输出写STOP，待进程排空，再卸载确认属于该批的用户LaunchAgent；不删除样本或旧missing。睡眠醒后已过窗口记missing，不回填。启动/落盘权限被拒时保留原错误、停止对应动作并恢复现有入口，不修改macOS安全设置或换路径规避。实际安装参数、版本与进程核验归handoff/本批PR；未来第一笔真实样本另验。

### 2026-10-24 入选完整研究并发运行

`ASHARE_TRADINGAGENTS_RESEARCH_CONCURRENCY`默认3，范围1–3，仅用于已授权selected-stock完整研究；回退为1可恢复串行，不改变原报告/账。普通AgentTask互斥、同股单写及原授权/unknown/超时/取消门继续生效。运行租约只保存task/usage/owner/method身份，权限0700/0600、持有真实flock；失锁/孤儿不能当活跃账免验。无迁移或新收费服务。完整研究使用原模型与原入选claim，不在普通读请求启动。

只读原报告不做启动前检；显式启动仍走完整校验。重新启用/换模型不由此配置授权。新入选上下文可读主paper预检，提交仍需维护身份与明确动作，且真实交易窗/源钟/风险逐次核。休市真实主账户空仓是合法结果，不能用隔离夹具的fill冒充主账户已成交。

原维护POST `/api/picks/intraday-review/run` 可用 `outcome_date` 只运行已完成交易日的结果owner，原无参复盘保留。必须是官方日历确认且已发生的收盘，未来/未知拒绝；最多已有provider两对日K、同股合并各周期与原12秒时限，不新增模型/服务/外发。原调度有限slot和失败回执可重启恢复；简报成功不当作结果采集成功。

维护恢复可选`symbols`（逗号分隔1–20个六位码，必须与outcome_date合用且属于原归档/台账范围）；先只读核scope再写，所有准备/标签同批过滤，不改批外pending。返回`partial_batch/processed_count/remaining_count/scope_count`，剩余不为0时`state=partial`，不能把批内成功当全历史已齐。原无参/自动行为保持，不新增结果账或队列。

最终执行边界：main/hunting-shadow的立即成交与挂单撮合共用窗口、证券身份、源钟/质量及已装配风险门；不带selection绑定的维护请求和自动maybe_open也不能旁路。研究shadow保留既有独立晨窗协议与限价/T+1/整手/费用，不混main结果。原选择一旦已有未决、unknown或真实成交，后续执行判定换版不能把旧占位清成not_started；最新判定只控制新资格。历史无明确绑定的未决事实保守阻断，不猜成交或重跑动作。

## 2026-10-25 正式板块身份与按需研究

2026-10-26原授权版本承接：从backend运行`PYTHONPATH=. .venv/bin/python scripts/tradingagents_authorization.py`默认只读。核准确source差异，用重复`--reviewed-source`与`--prepare`冻结私有计划，再以`--apply PLAN_ID`在原pin/文件/活动锁未漂移时承接同一授权。只备份授权及旧pin，不复制整份.env，不创建/延期/扩模型；重载前核无活动模型，仍走local-services/check及真实页面。

嵌套owner需按实际inspect逐项核：Tencent完整模块32699b46…→73af8b2f…允许具名路径`packet_builder.owner_sources.data_providers/tencent.py`的准确正向承接。新分钟节点不在TA实际get_kline/search读取链，其他AST不变有冻结证明；仍须显式声明该完整路径及所有实际host差异。IMP-019续修另具名核Hub的5d970…→c4386…正向对，以`packet_builder.owner_sources.services/quote_hub.py`经同一prepare/CAS入口承接；它保留provider低质判定，不能称为完全无消费者影响。仅维护原授权，不迁移旧恢复grant或启动新研究。续修另登记精确c4386…→4b2fd81d…单步边；合法备用可改变未来缓存新鲜事实，快照的免费来源/质量/截止/10秒门及其它Hub AST不变。历史边保留但不允许跳级、反向或未来；仍须同一路径逐项review、prepare和CAS，不自动承接私有授权。原恢复kind/旧grants、模型/依赖、期限/7unknown和非pin环境字节不动，不能手改authorization/runtime文件跳检查。

`same_method_cooperative_stop_v1`同版取消恢复仅支持四分析师fan-out/root step0，必须known原答完整回送、worker真实结束、原生流/receipt/账/检查点完整；`--prepare-grant SOURCE_TASK_ID --run RUN_ID`只准备准确单次恢复许可，按同owner CAS安装。旧缺流故障不放宽。检查使用独立维护GET `/api/agent/research/tradingagents/{symbol}/recovery`；普通保存GET即使附旧query也不运行检查，恢复POST只含run_id，不重跑原答。

严格单个未启动尾使用显式`--cooperative-prelaunch-tail`，仅核真实取消前缀、known账和已进入原检查点的提案/工具结果；不把分配高水位当物理次数。同方法不引用历史计划；仅已审host恢复源码迁移时另传`--source-rebind-plan PLAN_ID`，核原算子已应用计划/旧授权/当前pin及完整身份后再准备grant，仍用原`--apply`安装并正常重载。计划ID不能替换成任意路径或HTTP参数；保留原counter/答/账，首个新编号为高水位+1，普通配对权限不扩。

2026-10-26发布后实测：PR #349准确HEAD `2754eecd7c2df89415fb98088da736fb16cb50c3`、DegradedRelease6408634541、release_check exit0，PR CI40272096918和合并master CI40272723361三项通过；合并`027ed3a5769087f3191f8d0756659b5473608958`已加载BE6728／FE77077及build `kbu4l1B900JQEKYlNcrU2`。P2/P3原计划承接和单次grant已安装、期限未延长，旧7unknown/旧grant未改。恢复任务b729ddfa…在原run27a33e8d…成功完成12角色/7报告/Memory，复用原答1、新3–16全部known、新unknown0；两次普通GET为39.554／31.587ms，实际Chrome角色/报告、原答、对照、重读与血缘通过。运行回执归PR评论6409087831；手动研究可用，不代表语义、accuracy、收益或自动生产准入。

原32运行2237文件、五份用户原件、旧答/receipt/流前缀已核保全；原检查点历史归档仅元数据及hash，不是物理DB副本，恢复中的活动检查点可以合法推进。`authorization-maintenance`下source-rebind计划、before/applied及grant证据仍为活恢复依赖，不能按缓存清理；本批已按准确清单清理41个结束的可再生测试沙箱，64份外置证据不变，清理后启动器/卫生/保存读取通过；真实空间变化及保留资产以本批最终PR运行回执核验。有限检查FAIL1/同伴待审7/`source_verified=false`原样保留，局部SMA反证不使原警告自动撤销。三参考方法原存档的17次读取和实页状态/日期/下拉验收未运行模型或重新扫描。

三项参考价格研究使用原任务中心`reference_strategies`，只接受可选ISO完成日trade_date。固定本机marketdb只读、完整前缀共享，默认最新已完成源日，不接任意SQL/路径/阈值/模型。原子存档在忽略的`backend/data/picks/reference-runs/`，读取现有method-runs接口要求准确方法/日期/内容版本，不写正式选股/通知/模拟表。相同源/定义复用；首次源钟未知。`verify_reference_strategies.py`仅产描述研究，不是生产晋级回执。

总开关`ASHARE_TRADINGAGENTS_RESEARCH_ENABLED`继续管原手工完整研究和准确授权；新增`ASHARE_TRADINGAGENTS_AUTO_RESEARCH_ENABLED=0`只管新自动派发，不能通过关闭总开关冒充只停自动。原queued/running任务及unknown不取消、不清账，恢复仍按原准确任务/方法/具名许可。

旧报告的小摘要维护命令归本安装backend：`PYTHONPATH=. .venv/bin/python scripts/tradingagents_saved_summary.py --all-saved`默认dry-run；核验后显式加`--write`仅写derived旁车。支持重复`--symbol`限定，不接受任意root路径，不重跑模型或改原件/业务数据库；坏原件保留并明确unavailable，不能为显示完整状态补造。主目录启服前核开关/在途状态、原授权和来源副作用，沿原启动器激活并单独验HTTP/代理与实际页面。

## 2026-10-26 具名未知用量保留与研究反馈

重读保存结果与生成/恢复分开；新报告仍需原维护能力、真实入选、完整执行器及准确pinned研究授权。当前启动阻断在操作区直接可见，重读不会自动解决版本/用量问题。

用户对一条新的跨来源未知用量作具名许可后，原本机CLI支持`--prepare-retained-unknown USAGE_ID`，并必须显式给`--source-budget-date`、`--source-scope`、`--source-purpose`、`--source-state`、`--authorization-reason`及每个`--reviewed-source`。只准备0600私有计划，再由原`--apply PLAN_ID`比较源码、原行元数据hash、原授权和pin后安装；不接受任意路径/SQL，不保存整份环境备份，不改原用量行或自动重试模型。

该入口追加独立历史许可，不覆旧七条/恢复grant或延期；只解除准确具名事实对selected_research_llm的阻断，其他消费者和新unknown继续拒绝。`agent_budget.py`是完整方法指纹的一部分，必须列入实际已审source差异；安装后重载及真实读取/复用/新任务分别验收。CLI已定义BudgetError输出稳定code，未知/含私路径错误继续脱敏。
