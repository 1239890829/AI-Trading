# AGENTS.md — AI 开发者交接手册（必读，2026-08-31 全量重写；2026-09-10 更新：门禁实测回填、阶段 A/B 销账、待决清单收敛）

你接手的是 **AShare AI Trader**：A 股实时行情 + 量化投研 + 模拟交易 + 事件驱动选股的一体化工作台。
本文件是你的作业手册：现状、待办、阶段安排、工作纪律全在这里。
**动手前先读完本文件，再按需查 `docs/PROJECT-MASTER.md`（技术总览）与 `docs/archive/plan-review.md`（历史计划复盘，只读）。**

---

<!-- project-entry:start -->
## 项目入口

- 作业边界与发布流程：本文件 §0、§1、§6.5。
- 唯一文档入口：`docs/INDEX.md`；唯一状态账本：`docs/retro-and-gaps.md` §6.0。
- 当前现场与实测：`docs/handoff.md` §1；经验按 `docs/kb/00-INDEX.md` 定位。
- 接续工作：`skills/ashare-ledger-continue/SKILL.md`；当轮交接：`skills/ashare-task-handoff/SKILL.md`。
- 盘后复盘：`skills/ashare-daily-review/SKILL.md`，流程与逐项核验归既有 SOP / checklist。
- 发布核验：`scripts/audit/release_check.py`；恢复清单：`scripts/audit/platform_assets.py`。
- 报告渲染与校验：`scripts/reports/md-report-html.py`、`scripts/reports/md-html-parity.py`。
- 本地产物与恢复副本：忽略的 `artifacts/`；迁移状态归账本 `GOV-018`，不建立新的 MEMORY 权威入口。
<!-- project-entry:end -->

## 0. 红线（违反即事故）

1. **禁止**连接真实券商 / 自动真实下单。系统只有模拟交易（`/api/paper/*`）。
2. **禁止**把 mock 数据、过期缓存冒充实盘。数据源失败 → 标 `stale` + health=degraded。
3. **禁止**输出确定性买卖结论（必涨/稳赚）。一切结论 = 偏向 + 依据 + 失效条件；
   事件标的池等"机会输出"必须带「不构成买卖建议」声明。
4. **API Key 只存 `backend/.env`**（已 gitignored），绝不入库/入前端/入文档。仓库现为 **Public**：tracked 文件还**禁止**固化个人 home 绝对路径（统一改用 `$HOME/...` / `<repo>/...`）、本地邮箱/机器名、真实 `.env` / 私钥 / 证书 / token-like 值；提交前必须通过 `python3 scripts/audit/public_repo_scan.py`。本仓 Git author 固定使用 GitHub noreply 邮箱，勿再产生本机 `.local` 作者邮箱。
5. 撮合规则（T+1/涨跌停拒/整手/费用/停牌拒）是硬拦截，不可绕过。
6. **新增页面/板块需先论证**：默认通过复用、扩展、联动实现需求（联动设计原则，见 `docs/summary/architecture-design.md` §1 跨页面联动设计）。
   ⚠️ **2026-09-15 更正**：此处原写「§0」，但 `GOV-002`（2026-09-14）收敛后该文正文已从 **§1** 起，**§0 不存在** ——
   属 `doc-health` 查不出的"指针失效"（[[KB-ENG-85]]）。**引用章节前先确认锚点存在**。
7. **禁止应用内自我修改代码并落地**（2026-09-15，审计 O1 / [[KB-DEC-026]]）：`app/services/code_executor.py`
   的 C 类执行器**只提议、不落地**——产出 = patch 归档 + `evolution/*` 隔离分支 commit + 审计，
   **不得合并回主分支、不得推送**；落地一律走 `codex/*` 分支 → PR → 完整 CI → 网页版审查
   （返回值 `merged=False` / `review_required=True` 是硬契约，勿改回合并）。
   ⚠️ **`backend/tests/**` 不得纳入 AI 白名单**：门禁是在**宿主解释器**里跑 pytest，
   允许 AI 生成测试 = 允许 AI 借测试在宿主上执行任意代码；本仓**没有**
   「无宿主凭据 / 无外网 / 非必要目录只读挂载 / 限 CPU·内存·进程·时间」的隔离容器
   ⇒ **不拿 worktree 冒充 OS 沙箱**（worktree 只是 git 层隔离）。
   ⚠️ 用户对「`codex/*` PR 自动合并」的授权**不构成**对「应用内 LLM 自主改码并落地」的授权
   —— 两者是**不同的授权主体与执行体**，不可互推。

---

## 1. 快速启动

```bash
# 后端（venv 已建好；.env 含 THS key，LLM 走 `claude -p` → cc-switch 当前 DeepSeek，2026-09-19 实测 `deepseek-v4-flash`）
cd backend && source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
# ⚠️ 绝不用 --reload：与 SQLite 锁组合会反复挂死（2026-09-02 定位，见 §6.1）

# 前端（node_modules 已装）
cd apps/web && npm run dev                        # http://localhost:3000/workbench

# 测试与门禁（每次改动全部跑，全绿才算完；**数字必须实测回填，勿凭记忆**）
python3 scripts/audit/public_repo_scan.py                     # Public repo 密钥/隐私硬门（约 3s）
cd backend && .venv/bin/pytest --basetemp=/tmp/pytest-basetemp     # 后端 collect 3339 项（3263 passed / 76 skipped / 0 failed / 240.74s；收尾复跑 237.56s）（09-16 RSH-003 切片 2 轮实测；前提：8000 在跑）
# ⚠️ 不要在这条命令上再叠一个 `-q`：`pyproject.toml` 的 addopts 已有 `-q`，
# 叠加后等价于 `-qq`（extra-quiet），pytest 9.1.1 在该级别下**不打印汇总行**
# （只剩 `....  [100%]`，`passed/skipped` 全看不见）——取数会以为"测试没跑完"。
# 需要机器可读计数时改用 `--junitxml=/tmp/be.xml` 解析（2026-09-11 踩）。
# ⚠️ 耗时强依赖「8000 是否在跑」：后端服务停着 ~64s，服务在跑时实测 79s~330s 波动。
# 原因是常驻调度与测试同时抢 SQLite/网络；**报耗时必须说明前提**，否则会被当成回归。
# ⚠️ `--basetemp` 不可省：默认临时目录会被沙箱拒绝创建（EEXIST → PermissionError），
# 表现为几十个 E 而非 F，极易误判成代码回归（2026-09-11 踩，见 kb/03）。
# ⚠️ **跳过数 2 → 75 是新增守卫与切片分片的参数化产物，不是覆盖率丢失**：75 = **73** + 2，全额对上——
# 73 项来自 `test_import_lint.py`「装配层/其他：不受本规则约束」（分层规则表按模块参数化，
# 非业务层模块显式跳过；**新增一个非业务层 .py 就 +1**，如 S2-8 的 `app/core/bjtime.py`、
# 2026-09-12 的 `app/models/notification.py`、09-15 的 `api/routes/market_envelope.py` + 8 个域分片、
# 09-15 `IMP-027` 的 `app/bootstrap/` 三个文件（`__init__` / `services` / `schedulers`）），
# 另 2 项为既有的「指数无涨跌停概念」后端不适用项。
# ⚠️ **分母要能机械核验，别只写结论**：`app/` 下非业务层模块 = **73** =
# 34（装配层：`api/**` 30 + `bootstrap/**` 3 + `main.py` 1）+ 39（其他非业务层：core/data_quality/models/repositories/schemas/websocket）
# ⇒ 与 `skipped − 2` 逐字相符。**此类数字每次新增/删除非业务层 .py 都会变，回填前先实测。**
# ⚠️ **推论：「+N 个文件 ⇒ +N 个测试」≠「+N 项覆盖」**——增量可能全部是**跳过的参数化项**
# （09-15 切片：collect +9 / passed ±0 / skipped +9）。报数字时只说 collect 涨了，
# 等于把「没跑的用例」记成「覆盖增强」（见 [[KB-ENG-97]]）。
# ⚠️ **本条自身也曾失真**：原文写「58 项来自 import_lint」，而 58+2=60≠61（差额 1）——
# 实测 `pytest tests/test_import_lint.py` = 171 passed / **60 skipped** ⇒ 60+2=62 才自洽。
# **教训与本文档的警告同源：数字标注要么当轮实测回填，要么写"实测方法"而不写死数值。**
cd apps/web && npx tsc --noEmit                   # 类型 0 错误
cd apps/web && npx eslint .                       # 0 error / 0 warn（P1-27 已清零；余 1 处 C 类显式豁免）
cd apps/web && CODEBUDDY_SAFE_DELETE_ENABLED=0 npx vitest run   # 前端 603 项 / 67 文件（09-16 `IMP-033` 轮实测；其中 1 项为既有 `BUG-010` 并发超时候选）
# ⚠️ 默认并行度偶发 **SIGKILL(exit 137) 且零输出**（非测试失败）⇒ 先降并行度复跑：
#   npx vitest run --maxWorkers=1
# ⚠️ **凡改动/新增涉及时间·时区的断言，必须再用 `TZ=UTC` 复跑一遍**（CI 跑在 UTC，本地是 UTC+8）：
cd apps/web && TZ=UTC CODEBUDDY_SAFE_DELETE_ENABLED=0 npx vitest run
# 2026-09-12 真实踩过：`longhu-tab.test.tsx` 用 `new Date(2026, 8, 2, 14, 20)` 钉"盘中"，
# 那是**本机时区**解释 —— 在 UTC 上变成北京 22:20（"盘中"→"盘后"）、`at(17,5)` 更跨到次日，
# **本地 5 例全绿、CI 3 例红**。生产侧走 `bjToday()/bjMinuteOfDay()`（北京口径）本身是对的，
# 错的是测试钉了宿主墙钟。修法：用带 `+08:00` 偏移的字面量构造绝对时刻。
# ⇒ **"本地全绿" 不构成 CI 绿；时区是宿主属性，不改代码也能翻断言**。
cd backend && .venv/bin/python -m pyflakes app tests scripts   # 0（scripts 已纳入口径，P2-18）
python3 scripts/doc-health.py                    # 文档体检：0 待处理（收尾必跑，见 kb/07 §8.2）
# ⚠️ **新增任何「路径存在性」判据前，先问一句：它在 CI 检出里长什么样？**
# 判定面必须取自 git 跟踪清单（`_tracked_paths/_tracked_dirs`），**不要用 `Path.exists()`**：
# `.workbuddy/`、`data/picks/` 等都是 gitignored ⇒ CI 检出里没有 ⇒ 用文件系统口径必然
# 「本地恒绿 / CI 恒红」（2026-09-15 N/O 两项实测，见 [[KB-ENG-95]]）。
# 怀疑「本地绿/CI 红」时，**先用干净检出复现**（比推 CI 等结果快得多）：
#   git worktree add --detach /tmp/ci-sim HEAD && (cd /tmp/ci-sim && python3 scripts/doc-health.py)
# ⚠️ **改了 `docs/`（或 `AGENTS.md`）⇒ 后端门禁也必须跑，"后端代码零改动" 不构成豁免**
# （2026-09-16 实测踩到：`IMP-031` 轮只改了 frontend + docs，判定"后端零改动不用跑后端"，
#  结果 PR #16 的 **CI backend job 红**——`tests/test_doc_status_truthfulness.py`
#  判据 2 要求 `docs/summary/*.md` **谈状态就必须带 §6.0 指针**，而新建的
#  `docs/summary/pick-signal-chain.md` 含「未做」却无指针。
#  ⇒ **后端门禁里有一类"扫 `docs/` 的文档守卫"**（`test_doc_status_truthfulness.py` 等），
#    它们的输入是文档 ⇒ 只改文档照样让后端红。**"没改 .py" 与 "后端不会红" 是两件事。**
#  修法 = 按 `GOV-002` 惯例在 summary 文档头部加 `§6.0` 权威指针，**不是改守卫、不是登记豁免**。）
# 生产构建前必须先停 dev server（.next 冲突已踩两次）：
lsof -ti tcp:3000 -sTCP:LISTEN | xargs kill -9; cd apps/web && CODEBUDDY_SAFE_DELETE_ENABLED=0 npx next build
# ⚠️ **按端口 kill 一律带 `-sTCP:LISTEN`**：`lsof -ti tcp:<port>` 收的是「**所有持有该端口 fd 的进程**」
# —— **包含客户端**（Next dev 是 8000 的客户端、浏览器是 3000 的客户端）
# ⇒ 用 `lsof -ti tcp:8000 | xargs kill -9` 重启后端会**连带打死前端 dev server**，
#    且前端无任何报错、日志停在重启那一刻，因果在本进程日志里**看不见**（2026-09-16 实测踩三次，见 [[KB-ENG-106]]）。
# 杀完**复查两个端口**，不要只确认"我杀的那个没了"。
# ⚠️ **改完立刻跑那条能覆盖它的判据；同一批次不要对同一文件并发两处编辑**——
#   「工具报成功」**不是**落盘证据，第二处改动可能**静默丢失**，
#   而代码会停在「旧行为 + 新文档」的自洽组合上 ⇒ **门禁全绿也抓不到**（[[KB-ENG-105]]）。
#   "这次只是改文案所以不用跑" 是最贵的一句话：本轮两次丢失都是靠随后的判据才发现的。
```

> **最新门禁口径（2026-09-17 IMP-044 / BUG-024 / GOV-018 合批，本地完整验收）**：后端 **3716 collect = 3638 passed + 78 skipped，0 failed**；
> 前端本地/UTC 均 **675 passed / 71 文件**；tsc **0**、eslint **0/0**、pyflakes **0**、生产构建通过。
> 后端 **96.43s**：8000/3000 停用，前后端全量串行，期间有文档、静态检查及独立恢复演练；负载抽样 3.90/3.19/3.55，非空载基准。前端单 worker **40.00s / 38.30s**。
> 基线 `18f0eb4`：后端 **3670 → 3716 / 222 → 224 文件**，原用例身份全部保留；新增 **46** = Outbox/冷却 **37** + 导入环工具 **7** + 非业务模块参数化跳过 **2**，skipped **76 → 78** 自洽。前端源码零差异。
> 固定时钟先复现冷却时区 2 红及新鲜度窗口 1 红，再修复；发送意图的事务/租约故障注入均判红并恢复。独立进程与备份恢复仅首次重启发送一次，全部隔离 HTTP，无真实通知。持续分钟复核归 IMP-043，W02 其它入口仍开放；权威证据与预算见 `docs/handoff.md` §1。
>
> **历史门禁口径（2026-09-17 BUG-020 跌停失败语义切片）**：后端 **3546 collect = 3470 passed + 76 skipped，0 failed**；
> 前端本地/UTC 均 **650 passed / 70 文件**；tsc **0**、eslint **0/0**、pyflakes **0**、生产构建通过。
> 后端 **198.33s**：8000/3000 均未运行，后端与前端全量串行，期间有文档与 Git 核查；前端单 worker **44.41s / 46.82s**。
> 基线 PR #23 实测 **3521 / 214 文件** → 本批 **3546 / 215 文件**；**+25** 全来自跌停失败回归，import-lint 两侧均 **269**、skipped 不变。
> doc-health **22 条交接索引 / 0 档位冲突**；完整 OpenAPI **170 paths / 零差异**；证据见 `docs/handoff.md` §1。
>
> **历史门禁口径（2026-09-17 融合首批）**：后端 **3521 collect = 3445 passed + 76 skipped，0 failed**；
> 前端本地/UTC 均 **650 passed / 70 文件**；tsc **0**、eslint **0/0**、pyflakes **0**、生产构建通过。
> 后端 **189.42s**：8000/3000 均未运行，未与前端全量并发，期间有只读代码核查；前端单 worker **47.24s / 45.19s**。
> 基线 `4052c80` 实测 collect **3453 / 211 文件** → 本批 **3521 / 214 文件**；
> **+68 = 发布核验 35 + 资产恢复 5 + 迁移工具 22 + 索引守卫 5 + 数据隔离 1**；import-lint 两侧均 **269**，skipped 不变。
> `doc-health` **22 条交接索引 / 0 档位冲突**；权威现场与证据见 `docs/handoff.md` §1。
>
> **历史门禁口径**：后端 collect **3453 项（3377 passed / 76 skipped / 0 failed）**、
> 前端 **650 项 / 70 文件**、eslint **0 error / 0 warn**、`tsc` **0** · `pyflakes` **0** ·
> `doc-health` **全部通过**（`P 交接索引` 18 条 / `Q 档位一致性` 0 冲突）
> （2026-09-17 `BUG-020` 数据源身份修复**集成**（PR #21）轮实测；后端 **259.88s**，前提 **8000 在跑**；
> **报耗时（含前端数字）必须带负载前提**，否则会被当成回归）。
> ⚠️ **较上一值「后端 3432 / 前端 650·70」的 Δ 已机械归因（两侧 collect 各自实测求和，不凭记忆）**：
> master 实测 **3432（210 文件）** → 集成态 **3453（211 文件）**，
> **+1 文件** = 新增 `tests/test_provider_security_identity.py` **21 例**（全 passed），
> `3377 + 76 = 3453` 自洽 ⇒ 后端 **+21 collect = +21 passed / ±0 skipped**；
> `tests/test_import_lint.py` 两侧实测**逐字相同 269 例** ⇒ 参数化面未变（[[KB-ENG-97]]：跳过数不变 = 无覆盖丢失）。
> 前端 **±0** 且**有独立依据**：该 PR 相对 `origin/master` 在 `apps/web/` 下**零差异**
> （`git diff --cached origin/master --name-status` 实测）⇒ 与上轮逐字一致。
> ⚠️ **前端那 1 项红 = 已登记的 `BUG-010`**（`markdown-view.test.tsx::docs/ 下全部 md 均可渲染完成`，
> 预算固定 5000ms）：本轮**本地与 `TZ=UTC` 同项同因**（各 1 项）⇒ **非时区问题、非本轮引入**；
> 根因是**宿主负载**（该用例要渲染 `docs/**` 全部 md，而 `docs/` 只增不减）⇒ **余量已被吃光**，
> 建议提高该用例 `timeout` 或改按文件粒度断言（**未擅自改，留待拍板**）。
> ⚠️ **合并会引入「双方各自不超、合并后超」的红**：本轮 `doc-health` 的 **C 超层配额**曾
> FAIL `docs/data-source-comparison.md(501>500)`（基点 446 / master 449 / PR#21 498 / 合并 501）
> ⇒ 无损压缩至 **499 行**后通过。**集成分支必须重跑门禁，不能拿两边的绿拼成"应该绿"。**
> ⚠️ **提交前查未暂存漂移，提交后才要求本任务工作区干净**：`git add -A` **之后**再编辑的文件，
> 用 `git commit`（**不带 `-a`**）**不会**进提交 —— 本轮 `AGENTS.md` / `handoff.md` 的门禁回填
> 就是这样静默丢失的（`merge --stat` 显示的是双方差异、CI 照样绿、`doc-health` 也查不出）。
> 补救与判据见 [[KB-ENG-113]]。
> ⚠️ **权威位仍是 `docs/handoff.md` §1**（本头行必须与它同轮同步；历史各轮 Δ 归因见该处）。
> （2026-09-16 `GOV-016`（`ths.py` docstring 能力承诺 > 实现）轮实测；后端 collect **3373 项
> （3297 passed / 76 skipped / 0 failed）**、前端 **643 项 / 69 文件**、eslint **0 error / 0 warn**；
> 后端 **252.32s**，前提 **8000 在跑且无并发负载**；`tsc` **0** · `pyflakes` **0** ·
> `doc-health` **全部通过**（`P 交接索引` 13 条 / `Q 档位一致性` 0 冲突）；
> 本地时区与 `TZ=UTC` **均为 643/69**）。
> ⚠️ **较上一值「后端 3372 / 前端 643·69」的 Δ 全部机械归因**：
> 后端 **+1 collect = +1 passed / ±0 skipped** = `tests/test_provider_capabilities.py`
> 新增 1 例守卫 `test_ths_docstring_unimplemented_endpoints_stay_unwired`；
> **本轮无新增/删除后端模块** ⇒ `tests/test_import_lint.py` 未变、`skipped 76` 不变**自洽**
> （[[KB-ENG-97]]：跳过数不变 = 无覆盖丢失）。前端 **±0**（`apps/web/` 零改动）。
> ⚠️ **门禁数字的权威位仍是 `docs/handoff.md` §1**（本头行必须与它同轮同步）。
> （2026-09-16 `BUG-017`（`doc-health` 跳过谓词·目录形态锚点）轮实测；后端 **416.23s**，
> 前提 **8000 在跑**——⚠️ 该耗时**含本轮跑测期间的文档编辑负载**，**非空载**，勿直接与其他轮对标；
> 较上一值「后端 3366 / 前端 643·69」**+6 collect = +6 passed / ±0 skipped**
> = 新钉子 `backend/tests/test_doc_health_memory_index.py`（14 → 20 例）；同轮另发现 master 上一条
> 与本轮无关的红（`9364ef0` 的两项 SQLite/共享状态用例，**同一提交树实测为 success** ⇒ 判为偶发），
> 归因见 `docs/handoff.md` §1 与 `§BUG-017`（[[KB-ENG-111]]））。
> （2026-09-16 `IMP-035` **用户七问交付**（资讯/事件 tab · 模型写死 · 提醒去重 · 猎场闸门读时重算）轮实测；
> 后端 **301.48s**，前提 **8000 在跑且无并发负载**；`tsc` **0** · `pyflakes` **0** · `doc-health` **全部通过**；
> 本地时区与 `TZ=UTC` **均为 643/69**）。
> ⚠️ **报数必须带前提**：前端那两次数值在**解除后端并发**时取得；**与后端全量并发跑时默认并行度偶发 1 项红**
> —— `components/agent/markdown-view.test.tsx::docs/ 下全部 md 均可渲染完成` 超时 5240ms，
> **是已登记的 `BUG-010`（D 档观察项，判据挂在墙钟上）**，隔离复跑 10 项全绿（整文件 1.6~2.8s）⇒ **非回归**。
> ⚠️ **较上一值「后端 3339 / 前端 608·67」的 Δ 全部机械归因**：
> 后端 **+27 = +27 passed / ±0 skipped** = `+10`（新 `tests/test_picks_live_gate.py`）
> + `+7`（新 `tests/test_llm_model_single_source.py`）+ `+7`（`test_notifications.py` 13→20）
> + `+3`（`test_sentiment.py` 25→28，基线用**干净检出 worktree** 实测）。
> **非业务层模块数未变** ⇒ 实测 `tests/test_import_lint.py` = **194 passed / 74 skipped**（共 268 例），
> `74 = 76 − 2` 与上轮**逐字相同**（[[KB-ENG-97]]：跳过数不变 = 无覆盖丢失）。
> 前端 **+35 项 / +2 文件** = `+18`（新 `lib/picks-gate.test.ts`）+ `+6`（新
> `notification-event-tab.test.tsx`）+ `+11`（`pick-card` +6 / `notification-drawer` +4 /
> `notification-row-landing` +1；由 `git diff` 逐文件计 `it(`/`test(` 净增）。
> ⚠️ **权威位仍是 `docs/handoff.md` §1**（本头行必须与它同轮同步，否则下一位会拿旧基线把正常增量当回归）。
> ⚠️ **上一轮的门禁头行在本文件也曾落后**：它写着 **3247 / 603·67**（`IMP-033` 轮），
> 而 `docs/handoff.md` §1 已到 **3287 / 608·67** —— 中间两轮（`BUG-016` 子项③、`RSH-027`）
> 更新了 handoff、**未同步本文件**（各自的 Δ 归因在 handoff §1 可查）。
> ⇒ **纪律：门禁数字以 `docs/handoff.md` §1 为权威位，本头行必须与它同轮同步**，
> 否则下一位接手者会拿旧基线去比、把正常增量误判成回归。
> （2026-09-16 `IMP-033` 提醒落点统一为个股详情弹窗轮实测；较上一值「后端 3247 / 前端 598·66」
> 增量 **前端 +5 项 / +1 文件**，来源自洽：**恰等于**新文件
> `components/notifications/notification-row-landing.test.tsx` 的用例数（5）；
> 后端 **±0**，理由是**本轮未改后端代码与后端测试**，故沿用上轮实测值（本轮**复跑复验**为同一数字）。
> 本地时区与 `TZ=UTC` **均为 603/67**。
> ⚠️ 前端那 603 里**有 1 项**是全量并发下超时的 `BUG-010`（D 档观察项，非回归）——
> 归因见 `docs/handoff.md` §1；**两道全量不要并发跑**。
> ⚠️ **本轮后端 pytest 曾真红一轮**：`doc-health` J 项 + `test_doc_health_anchors.py`
> 报 `docs/handoff.md:88 → <新建但尚未 git add 的测试文件>（全仓不存在）`
> —— 判定面取自 `git ls-files` ⇒ **未跟踪的新文件在本地就是"不存在"**。
> **修法 = 先 `git add`**（见 [[KB-ENG-102]] 同族反向情形 / [[KB-ENG-95]]）。）
> （2026-09-16 `IMP-031` AI 判读气泡点击语义修正 + 选股提醒链路梳理轮实测；较上一值「后端 3247 / 前端 593·65」
> 增量 **前端 +5 项 / +1 文件**，来源自洽：**恰等于**新文件
> `components/assistant/floating-assistant-alert-bubble.test.tsx` 的用例数（5）；
> 后端 **±0** 的理由是**本轮未改后端代码**（纯前端 + 文档），故沿用上轮实测值。
> 本地时区与 `TZ=UTC` **均为 598/66**。）
> （2026-09-16 `GOV-015` 账本档位一致性守卫 + `BUG-014` 销账轮实测；较上一值「后端 3235 / 前端 593·65」
> 增量 **后端 collect +12 = passed +12 / skipped ±0**。机械核验：`+12` **恰等于**新文件
> `backend/tests/test_doc_health_ledger_stages.py` 的 `--collect-only` 计数 **12**；
> 同轮另一处测试改动 `backend/tests/test_doc_health_anchors.py` **只补 `_NEUTRAL` 一项、不新增用例**；
> `skipped` 保持不变的理由是**本轮未新增 `app/` 非业务层模块**（`scripts/` 与 `tests/` 不进该口径）。
> 前端 **±0**，本地时区与 `TZ=UTC` 均为 593/65。
> ⚠️ **`BUG-010`（D 档观察项）的触发条件是「宿主负载」，不是「并发」——别把它的红当成本轮回归**：
> 用例 = `components/agent/markdown-view.test.tsx::docs/ 下全部 md 均可渲染完成`（固定 5s 墙钟）。
> ① 与后端全量**并发**跑时 **4 轮 3 红**，解除并发后 ×3 全绿；
> ② **2026-09-16 `IMP-031` 轮更正**：**未跑后端全量**（前端全量单独跑）时**连续 3 轮全红**，
> 宿主 `uptime` 实测 **load 28.16 / 8 核** ⇒ 「并发」只是满载的一种成因，**自变量是负载本身**。
> **归因证据（对照跑，非推理）**：临时移出该轮新增的 1 份 `docs/` md + 1 个前端测试文件后，
> 基线**同样 `1 failed / 592 passed`、同一用例同一形态** ⇒ 与新增文档/用例**无关**；
> 增量自洽 `597−592=5`、`66−65=1` 恰等于新增用例数 ⇒ **新增用例全部通过**。
> 该用例单独跑 **10/10 · 1000ms**（`docs/` 已 87 份 md，阈值 5000ms，**5× 余量**）。
> ⚠️ **更本质的一点**：该用例的判定目标是「渲染不得死循环」，而同文件第 32–34 行**明确记录
> 真死循环的表现是「挂死」而非变红**（同步渲染期占死事件循环、超时打断不了）⇒
> **这个 5s 超时抓不到它要抓的缺陷**，属「判据与目标错位」（[[KB-ENG-94]] 同族）。
> 按 `BUG-010` 原判：**不改判据、不单点放宽超时**（放宽只是掩盖），真正的修法是改成结构性判据
> ——须与 `BUG-008` 一并**按族排期拍板**。门禁实操：看到这条红**先看 `uptime` 负载**，
> 再看对照跑；**它的红不代表代码回归**。CI 各 job 独立 runner，不受本地负载影响。
> （2026-09-16 `BUG-014` + `GOV-014` 轮实测；较上一值「后端 3225 / 前端 593·65」增量
> **后端 collect +2 = passed +2 / skipped ±0**，机械核验 = `git diff -U0 HEAD -- 'backend/tests/*.py'`
> 新增 `def test_` **10 个、删除 0 个**，其中 8 个已计入上一值 `RSH-026` 口径 ⇒ 本轮净 **+2**，
> 均在 `tests/test_db_migrations.py`：`test_opportunity_learning_tables_match_their_models`（新建）
> + `test_migration_scripts_never_use_the_default_engine`（**AST 结构守卫**，禁止迁移脚本调用
> `get_engine()`）。⚠️ **本轮首跑是 3 failed**（`3148 passed`），三项**全为真回归**且都已修：
> ① `test_cmd_guidance_guard::test_repo_has_no_reload_guidance` —— 新建的 `docs/handoff.md` 写下
> 「后端 8000（uvicorn，单实例·无 `--reload`）」⇒ 该行**同时含 `uvicorn` 与 `--reload` 却无禁用标记**，
> **修文案（改「绝不用 `--reload`」）而非放宽守卫**；②③ `test_doc_health_anchors.py` 两条结论行用例 ——
> `GOV-010` 的 **AST 反查守卫再次真命中**，点名新检查 `check_handoff_index` 未进 `_NEUTRAL`
> （这是该守卫**第二次**抓到真漏打桩，0 误报）⇒ 补入且第 4 位中性值必须是 `None`（写字面值 `""`
> 会让"保险丝生效"与"中性"不可区分）。前端 **±0**，本地时区与 `TZ=UTC` 均为 593/65。）
> （2026-09-16 `RSH-026` 个股机会学习闭环第一批实测；较上一值「后端 3213 / 前端 593·65」增量
> **后端 collect +12 = passed +11 / skipped +1**：7 条新闭环测试来自
> `tests/test_opportunity_learning.py`，+1 为 `test_tradability.py` 逐股过滤审计，+2 为两个新增 GET
> 端点自动进入全量冒烟参数化，+1 为新增业务模块 `picks/opportunity_learning.py` 进入 import-lint；
> `models/opportunity_learning.py` 属非业务层，产生 **+1 显式跳过**。机械核验：`76 − 2 = 74`
> = **34 装配层 + 40 其他非业务层**，较上轮 73 恰好只新增该模型模块。前端 **±0**，本地时区与
> `TZ=UTC` 均为 593/65。）
> （2026-09-16 `IMP-030` 主动漏洞发现探针轮实测；较上一值「后端 3207 / 前端 593·65」增量
> **后端 collect +6 = passed +6 / skipped ±0**：5 条新行为测试来自
> `tests/test_evolution_probes.py`，另 +1 是新增业务模块 `services/evolution_probes.py` 自动进入
> `test_import_lint.py` 分层参数化；前端 **±0**，本地时区与 `TZ=UTC` 均为 593/65。）
> （2026-09-16 最终技术方案与装配重构集成轮实测；较装配重构基线 **后端 collect +2 = passed +2 / skipped ±0**：
> 新增猎场同键并发单航班守卫 1 项 + 通知个股机会策略净增 1 项；前端 **±0**，本地时区与
> `TZ=UTC` 均为 593/65。全量首两轮在 `test_events_api_lifecycle` 稳定复现 `BUG-012` 同族的
> `StaticPool :memory: + refresh` 失败，取证为生产自治默认开启后测试未显式停机；`conftest.py`
> 补测试环境 `ASHARE_AGENT_AUTONOMY_ENABLED=false` 后全量复跑 0 failed，生产默认值不变。
> （2026-09-15 §6.48 装配重构轮（`IMP-027`）实测；较上一值「后端 3202 / 前端 593·65」增量
> **后端 collect +3 = passed ±0 + skipped +3**，来源自洽：新增 `app/bootstrap/` 三个文件
> （`__init__.py` / `services.py` / `schedulers.py`）落在 `app/` 非业务层 ⇒
> `test_import_lint.py` 分层规则按模块参数化**显式跳过**（[[KB-ENG-97]] 同族：
> **这 3 项增量是「没跑的用例」，不是覆盖增强**）。
> **机械核验**：`skipped(75) − 2 = 73` = `app/` 下非业务层模块数
> = **34**（装配层：`api/**` 30 + `bootstrap/**` 3 + `main.py` 1）+ **39**（其他非业务层）⇒ 逐字相符。
> + **前端 ±0**（本轮**纯后端 + 文档改动**，git 核对 `apps/web/` 无改动 ⇒ 上轮 593·65 本轮实测逐字一致）
> （2026-09-15 §6.47 R22 统一鉴权边界轮实测；较上一值「后端 3176 / 前端 576·62」增量
> **后端 collect +26 = passed +25 + skipped +1**，来源自洽且**分两类**：
> `+25` = 新文件 `backend/tests/test_auth_boundary.py`（姿态 fail-closed / 遍历
> `app.openapi()` 逐条断言无凭据必 401 / 豁免集合恰为 `/api/health` / WS 子协议往返与拒绝 /
> 四路注入自证）；`+1` = 新模块 `app/core/auth.py` 属 `app/core/**` ⇒ `test_import_lint.py`
> 分层规则判**非业务层** ⇒ 显式跳过（[[KB-ENG-97]] 同族：**这点增量是「没跑的用例」，不是覆盖**）。
> **机械核验（两道）**：① `pytest tests/test_import_lint.py -rs` ⇒ `SKIPPED [70] 装配层/其他`
> 且 `70 = skipped(72) − 2`（另 2 项为既有的「指数无涨跌停概念」不适用项）逐字相符；
> ② 按该守卫自己的判据（`_ASSEMBLY_PREFIXES/_ASSEMBLY_FILES/_BUSINESS_PREFIXES`）复算
> `app/` 下模块：**70 = 31（装配层：`api/**` + `main.py`）+ 39（其他非业务层）**，
> 其中 39 比上一轮 **38 恰好 +1**（`core/auth.py`）。⇒ 恒等式 `+26 = 25 passed + 1 skipped`。
> + **前端 +17 项 / +3 文件**，来源自洽：新文件 `lib/proxy-headers.test.ts`（5，
> 代理注入单点——含"不得手搓凭据头"反向断言）+ `lib/ws-credential.test.ts`（8，
> 取凭据与缓存失败方向）+ `hooks/use-quote-stream.test.tsx`（4，**await 窗口竞态**：
> `connect()` 因取凭据变 async 后，卸载发生在 await 期间不得再建 socket；另覆盖
> "未配置 ⇒ 不传第二个参数"——`new WebSocket(url, [])` 与 `new WebSocket(url)` 在浏览器里
> **不等价**）；`lib/env-secrecy.test.ts` 6 例为**改写**（原「注入写鉴权头」改为
> 「运行时读凭据且不自己拼头」）。
> **四路注入自证（后端全红）**：① 摘掉某 router 的守卫 ⇒ 该 router 端点集体逃逸被抓；
> ② 整 router 豁免 ⇒ **实测连带放开 6 条 `/system/*`（含会真实花钱的 `/system/llm-probe`）**，
> 这就是「豁免粒度错误」——豁免挂在 router 上而 router 里还有别的端点（见 §6.4 纪律）；
> ③ WS 去掉子协议校验 ⇒ 无功可连；④ 空 token fail-open ⇒ 被姿态校验与运行期判定双杀。
> **前端两路注入自证（精确变红）**：删掉 await 后的 `if (closed) return;` 与把
> `new WebSocket(url)` 无条件改成 `new WebSocket(url, protocols)` ⇒ 4 例中 **2 例红**
> （恰为对应的那两条），还原后复绿。
> （2026-09-15 §6.46 `BUG-002`（`ntile` 无 tie-break）确定性修复轮实测；**较上一值「后端 3174 / 前端 576·62」增量
> 后端 collect +2 / passed +2 / skipped ±0**，来源自洽：`backend/tests/test_factors.py` **36 → 38**，
> 新增两条判据不同源的守卫（① 行为：同池连跑 3 次整份因子记录逐字相等 ② 结构：扫**生成的 SQL**，
> `PARTITION BY date_ms` 且按行序取值的窗口函数须含 `thscode`）；另 1 例**既有**用例
> `test_adding_factor_does_not_change_existing_numeric_conclusions` 的抖动断言由**正向翻转为反向**
> （判据加强、不增用例数）⇒ 恒等式 `+2 = 2 passed + 0 skipped`。
> ⚠️ **本轮基线是实测的，不是沿用文档上一行**：`git worktree add --detach /tmp/ci-sim HEAD` 干净检出实测
> **3174 collect / 3103 passed / 71 skipped**（198 文件）。⚠️ **文档上一行写的 `3149`（§6.45）与实测基线差 25** ——
> 差额来自 §6.45 之后合并的 **PR #6 / #7**（`codex/disable-autonomous-code-execution` /
> `codex/harden-code-executor`）：那两批**只更新了自身分支内的文档，未回填本口径行**。
> ⇒ **纪律：口径行的「上一值」必须以干净检出实测为准，不得沿用文档里的上一行**——
> 否则本轮增量会被算成 **+27** 并把 25 项归错因。
> ⚠️ **另有一处「改了却看不出」的陷阱**：`ALGO_VERSION` bump 后**不会**让用例数变化，
> 但它改变的是**结论口径**（旧结论须走 `review_required`）⇒ **门禁数字全绿 ≠ 无需复核**（口径变更另走 `IMP-026`）。
> + **前端 ±0**（本轮**纯后端改动**，git 核对 `apps/web/` 无改动；仍照跑，实测 576 passed / 62 files
> 逐字一致、全量与 `TZ=UTC` 复跑同绿——**"某一侧不变"同样是可核对的自洽项**）
> （2026-09-15 §6.45 `market.py` 切片（`IMP-005` 批 3）轮实测；较上一值「后端 3140 / 前端 576·62」增量
> **后端 collect +9 / passed ±0 / skipped +9**，来源自洽且**分两步**：
> +1 = 第 1 步新增 `routes/market_envelope.py`；+8 = 第 2 步新增 8 个域分片
> （`market_sentiment/quotes/flow/themes/longhu/pools/board/stock.py`）。
> ⚠️ **9 项全部落在 skipped 而非 passed**，因为 `test_import_lint.py` 的分层规则把 `app/api/**`
> 判为**装配层**（`_is_business()` 返回 `False`）⇒ 显式跳过；机械核验：`app/` 下非业务层模块
> = **69** = 31（`api/` 装配层）+ 38（其他非业务层），与 `skipped − 2 = 69` **逐字相符**
> ⇒ **非覆盖率丢失**（恒等式 `+9 = 0 passed + 9 skipped`）。
> ⚠️ **推论（本轮新增纪律）：「+8 个文件 ⇒ +8 个测试」不等于「+8 项覆盖」**——
> 增量可能是**参数化跳过项**；报门禁数字时只说 collect 涨了，等于把「没跑的用例」记成「覆盖增强」。
> 真正约束新分片的判据是 `test_routes_do_not_import_each_others_privates`（遍历全部 `api/routes/**`、
> **不因分层规则跳过**），且它**因切片才首次被赋权**（切片前 50 端点同处一文件，跨模块私有导入不可能发生）。
> **「行为不变」由四路机械判据保证**：**OpenAPI 契约逐条相同**（166 paths / 177 ops / 106 schemas 全等，
> `operationId`/`parameters`/**`tags`** 逐条比对——此项曾抓到门面与子 router 重复声明 tags 导致
> `["market","market"]`，而"路由表条数相同"的判据**完全抓不到**）+ 静态自证 C1–C4
> （62 定义无遗漏无重复 / 正文逐字 / 50 端点集合一致 / 遮蔽关系集合不变）。
> 见 [[KB-ENG-97]] / [[KB-ENG-93]]）
> + **前端 ±0**（本轮**纯后端改动**，git 核对 `apps/web/` 无改动；仍照跑，实测 576 passed / 62 files
> 逐字一致、全量与 `TZ=UTC` 复跑同绿——**"某一侧不变"同样是可核对的自洽项**）
> （2026-09-15 §6.43 板块权限准入轮实测；较上一值「后端 3132 / 前端 575·62」增量
> **后端 +8 项**（3 板块权限（`test_tradability`）+1（`test_picks_pipeline`）
> +1（`test_review_picks_dimension`）+2（`test_intraday_opportunity`）
> +1（`test_pre_limit_radar`），collect 与 passed 两侧同为 +8、skipped 恒 62 ⇒ 自洽）
> + **前端 +1 项**（`pick-card.test.tsx` 板块徽标）
> （2026-09-15 §6.42 猎场「可参与」口径轮实测；较上一值「后端 3097 / 前端 571·62」增量
> **后端 +35 项**（`= 20`（新文件 `tests/test_tradability.py`）`+ 7`（`test_picks_pipeline.py`）
> `+ 1`（`test_review_picks_dimension.py`）`+ 4`（`test_intraday_opportunity.py`）
> `+ 3`（`test_import_lint.py`：新增「函数内导入可解析」守卫 2 例 + 新业务模块进分层参数化 1 例），
> **collect 与 passed 两侧同为 +35、skipped 恒 62 ⇒ 自洽**）
> + **前端 +4 项**（`pick-card.test.tsx` 新增可参与性三态 4 例）
> （2026-09-15 §6.38 后端切片轮实测；较上一值「后端 3088 / 前端 571·62」增量
> **后端 +9 项**（`assistant/tools.py` 切成 `tools/` 包 ⇒ `test_import_lint.py` 按模块参数化，
> 新增分片计入；实测 **3035 passed / 62 skipped / 0 failed**，`skipped` 不变故差额全落在 passed）
> + **前端 ±0**（该轮**纯后端改动**，「某一侧不变」同样是可核对的自洽项）
> ⚠️ **同轮曾出现 1 项偶发**（当时 load≈26，8 核机器上我并发跑了前端全量）：
> `test_provider_budget::test_remaining_budget_is_shared_across_sources` 断言 `hang.calls==1` 失败，
> 根因是 s0 的**墙钟**开销在满载下超出 0.15s 预算 ⇒ 第二个源被**直接跳过**（单独跑绿）
> ⇒ 已登记并于**同日修复** `BUG-007`（§6.40）——复读该判据时发现它**既会假红也会假绿**：
> 阈值型（`elapsed < 0.3`）+ 仅 1.5× 余量 ⇒ 满载下偶发假红；而注入"每源一份配额"的**真缺陷**时
> 它**完全漏判**（缺陷形态 0.25s 恰好落在阈值之下）⇒ 已改为**结构关系判据**（第二个源拿到的是"残值"）
> （2026-09-15 弹窗外壳统一轮实测；较上一值「后端 3088 / 前端 561·61」（详情弹窗化轮）增量
> **后端 ±0 项**（本轮**纯前端改动**，git 核对 `backend/` 无代码改动；仍照跑一遍，
> 实测 **3026 passed / 62 skipped**，与上轮**逐字一致**）
> + **前端 +10 项 / +1 文件**，来源自洽：新增 `components/ui/modal-shell.test.tsx` 10 例
> （**5 个弹窗收敛到统一外壳**：portal / 遮罩 / Esc / 尺寸档 / 层级 / 头尾槽只实现一处，
> 并顺带统一了此前不一致的三处——遮罩点击判定方式、`role="dialog"` 挂载位置、
> 业务侧一律改依赖零依赖的 `symbol-detail-context` 而非整个面板子树；见 [[KB-ENG-92]]）
> · `TZ=UTC` 复跑同绿 · 浏览器实测 5 个弹窗全部正常（资讯 / 概念 / 精选详情 /
> 内容详情 / 标的详情，含遮罩点击与 Esc 关闭））
> （2026-09-15 详情弹窗化轮实测；较上一值「后端 3088 / 前端 536·59」（§6.37）增量
> **后端 ±0 项**（本轮**纯前端改动**，后端逐字未变——实测 3026 passed / 62 skipped / 198.95s，
> 与 §6.37 记录逐字一致；「某一侧不变」同样是可核对的自洽项）
> + **前端 +25 项 / +2 文件**，来源自洽：10（新文件 `components/detail/symbol-detail-modal.test.tsx`）
> + 6（新文件 `components/stock-link.test.tsx`）+ 9（`lib/detail-tabs.test.ts` 4 → 13，
> URL→弹窗入参解析）· `TZ=UTC` 复跑同绿 · 浏览器实测三处入口（盘面涨停池 / 市场指数卡 /
> 事件面板标的池）点开弹窗且 **URL 不跳转**、指数分支在弹窗内同样生效，见 [[KB-ENG-92]]）
> （2026-09-15 §6.37 实测；较上一值「后端 3088 / 前端 533·58」（§6.36）增量
> **后端 ±0 项**（该轮亦为**纯前端改动**——`lib/api.ts` 切片，后端逐字未变）
> + **前端 +3 项 / +1 文件**，来源自洽：新增门面守卫 `lib/api-facade.test.ts` 3 例
> （**切片把 2751 行的 `lib/api.ts` 拆成 14 分片 + 26 行薄门面，判据"行为不变"经四路机械证明**，
> 见 §6.37 与 [[KB-ENG-91]]）· 全量 + `TZ=UTC` 复跑同绿 · 四业务域运行时冒烟全出真实数据）
> （2026-09-15 §6.36 实测；较上一值「后端 3088 / 前端 502·57」（§6.35、§6.34）增量
> **后端 ±0 项**（本轮**纯前端改动**，后端逐字未变 —— collect 3088 与 §6.35 记录**逐字一致**，
> 「某一侧不变」同样是可核对的自洽项；耗时因 8000 在跑而偏长，==前提相关，勿当回归==）
> + **前端 +31 项 / +1 文件**，来源自洽：22（新文件 `lib/assistant-sessions.test.ts`）
> + 9（`components/assistant/floating-assistant.test.tsx` 2 → 11 条）· `TZ=UTC` 复跑同绿）
> （2026-09-14 §6.34 实测；较上一值 3070（§6.33）增量 **+4 项 / +0 文件 / ±0**，
> 来源自洽：后端 = 4（`tests/test_factors.py` 新增的 `RSH-003` MFI/OBV 四用例：
> 朴素参考逐点比对 + 三态极值与缺口 + 除权口径 + **判据自证**），
> **实测 3012 passed / 62 skipped、157.52s（前提 8000 在跑）**，与记录值逐字一致；
> 前端本轮**零改动** ⇒ 502 / 57 逐字不变、`TZ=UTC` 复跑同绿——**"某一侧不变"同样是可核对的自洽项**）
> （25 条回归已按 P1-27 清零；仅 notification-drawer 保留 1 处带理由的 C 类豁免）。
> ⚠️ **「文件数」有两个口径，混用会造出假缺口**（2026-09-14 实测）：`ls tests/*.py` 与
> `pytest --collect-only` 的「有测试的文件数」**不等**——本仓恒差 2（存在 2 个 0 用例的测试文件）。
> master 实测：`ls` = 184 而 collect = 182；当前：`ls` = **198** 而 collect = **196**（09-14 §6.32 复测）。
> **引用前先确认用的是哪个口径**，并把基线数字改从 `git worktree add /tmp/base <ref>` 实测取，
> 不要沿用手写记录（09-14 曾因「ls 基线 184 vs collect 记录 182」差 2，一度像是本轮多加了文件）。
> **测试规模与告警数同属「会失真的状态标注」**——改动后要实测回填，不要沿用旧数字
> （此前「≤1 warn / 后端 580 / 前端 97 / 219 / 257 / 263 / 342 / 1925 / 2553 / 2556 / 2557 / 2574 / 2576 / 2625 /
> 2584 / 2585 / 2590 / 2602 / 2606 / 2619 / 2620 / 2632 / 2635 / 2674 / 2691 / 2695 / 2708 / 2720 / 2731 / 2860 /
> 3053 / 3062 / 前端 423 / 429 / 444 / 454 / 472」均已被后续改动追过，教训见 `docs/retro-and-gaps.md` §七）。
> ⚠️ **交接 note 里的门禁数字也会失真**（2026-09-12 实测：note 写「2574 / 2513」，当轮为 2576 / 2515，新加 8 项后为 2584 / 2523 ⇒ 差值恰好等于新增测试数，可自洽核对）——**取数一律自己跑一遍**。
> ✅ **自洽核对法已再次生效**（2026-09-12 批次 1 收尾）：上一值为 2632 / 429，本轮 2635 / 438 ⇒ 差值 **+3 / +9**，
> 恰好等于本轮新增的 3 条后端守卫与 1 个前端文件（`lib/market-hours.test.ts` 9 项）——**差值对不上就说明有别的改动混入，值得查**。
> ⚠️ **差值对不上时，别先怀疑自己——先用 worktree 实测基线**（2026-09-12 批次 3 收尾）：
> 记录值 2635、实测 2652，差 **+17**，而我按新增用例只数到 **+15**。做法：
> `git worktree add /tmp/base <上一个提交>` → 在 worktree 里跑 `pytest --collect-only` → 与当前 collect 输出**逐文件 diff**。
> 实测基线 2637 ⇒ 真实差值 **+15 = 4+4+3+4+1**，全额对上；**记录值 2635 是「#29 提交之前」的数字，
> 提交时忘了回填那 2 条 watcher 守卫**。⇒ 教训不是"数错了"，而是「**回填滞后于提交**会留下缺口」；
> 好在 `--collect-only` 的产出可以逐文件对账，比记总量可靠。
> ✅ **自洽核对（2026-09-12 批次 5 收尾）**：F-4 轮 2652 / 171 → **2661 / 172** ⇒ 差 **+9 项 / +1 文件**，
> 恰好等于新增的 `tests/test_doc_health_empty_sections.py`（9 条用例）——**差额与新增文件数、用例数三方自洽**；
> G-2 轮 2661 / 172 → **2674 / 172** ⇒ 差 **+13 项 / +0 文件**（全部加在两个既有测试文件内：
> `test_event_loop_no_block` 16−4=+12、`test_picks_pipeline` +1）——**"零新增文件"也要能对上差额**。
> F-10 轮 2674 / 172 → **2691 / 173** ⇒ 差 **+17 项 / +1 文件**，恰好等于新增的
> `tests/test_doc_health_anchors.py`（17 条用例）——同样三方自洽。
> ✅ **自洽核对（2026-09-12 缺陷修复轮 §6.14）**：前端 444 / 53 → **454 / 54** ⇒ 差 **+10 项 / +1 文件**，
> 恰好等于新增的 `components/agent/markdown-view.test.tsx`（10 条用例）；后端**未改动**（2691 / 173 不变，
> 实测 2629 passed / 62 skipped 与记录逐字一致）——**"某一侧不变"同样是可核对的自洽项**。
> ✅ **自洽核对（2026-09-12 深夜 §6.16）**：后端 2695 / 173 → **2708 / 173** ⇒ 差 **+13 项 / +0 文件**，
> 恰好等于 `test_event_loop_no_block.py` **16 → 29**（新增 `GUARDED_ROUTES` 9 条 + 自证 4 条）——
> **"零新增文件"也要能对上差额**。前端本轮**零改动**，实测仍 **454 / 54**（复验通过）。
> ⚠️ **前端取数坑（§6.16）**：默认并行度下 `vitest run` 会**连续被 SIGKILL（exit 137）且零输出**；
> 加 **`--maxWorkers=1`** 即可跑通。**无输出的 137 ≠ 测试失败**，先降并行度复跑再下结论。
> ✅ **自洽核对（2026-09-13 §6.17）**：后端 2708 / 173 → **2720 / 174** ⇒ 差 **+12 项 / +1 文件**，
> 恰好等于新增的 `tests/test_doc_health_tables.py`（K 项 12 条自证用例）——三方自洽。
> **加测试文件就会让这里过期**，改测试后请顺手回填。
> ✅ **自洽核对（2026-09-13 §6.18 账本全量执行轮）**：后端 2720 / 174 → **2731 / 175** ⇒ 差 **+11 项 / +1 文件**，
> 恰 = 风控接线 9（`test_paper_risk_gate.py` 7 行为级 + `test_risk.py` 2 装配/口径级）+ C 类钉子 2
> （anchors 豁免治理 1 + data_path 前提钉死 1），文件 = `test_paper_risk_gate.py`；前端 454 → **456** ⇒ 差 **+2**
> （resetKey 默认清除行为 + 数据刷新成本两用例，其中 1 处为改写非净增）——三方自洽。
> ✅ **自洽核对（2026-09-14 全面审查实施轮，分支 `review/full-audit-20260914`）**：后端 2785 / 182 → **2813 / 184**
> ⇒ 差 **+28 项 / +2 文件** = 18（`test_degradation_contracts.py`）+ 4（`test_paper_pending_cash.py`）
> + 4（`test_write_token.py`）+ 1（`test_picks_pipeline.py`）+ 1（`test_meta_review.py` 定点守卫）；
> 前端 456 / 54 → **472 / 56** ⇒ 差 **+16 项 / +2 文件** = 6（`env-secrecy.test.ts`）+ 3（`news-modal.test.tsx`）
> + 4（`format.test.ts`）+ 3（`css-vars.test.ts`）。**同轮 3 个既有失败收敛为 0**：1 个文档锚点（新文件需 `git add`
> 进索引，守卫判定面 = `git ls-files`）+ 2 个「测试绑定真实运行日」（`test_meta_review` seed 用 UTC 而过滤走北京
> naive ⇒ **每周一凌晨必红**；`_calendar_days_ms` 未注入 `today` ⇒ 只有夹具当天是绿的）——**两条都在周一凌晨实跑复现并定点修复**。
> ✅ **自洽核对（2026-09-14 批次 2 · 低风险正确性轮 R05/R06/R09/R11/R13/R21）**：后端 2813 / 184 → **2860 / 185**
> ⇒ 差 **+47 项 / +1 文件**，用 `git worktree add /tmp/base master` 实测基线后**逐文件对账**（比记总量可靠）：
> 28（`test_paper_order_guards.py`，本轮唯一新增文件 · R05/R06）+ 10（`test_position_loop.py` · R09）
> + 4 + 1（`test_evolution.py` + `test_agent_tasks.py` · R11）+ 2（`test_backtest.py` · R13）
> + 1 + 1（`test_data_path_isolation.py` + `test_theme_catalog.py` · R21）= **47**，全额对上。
> 全仓基线对账：master collect = **2785 / 182** → 当前 **2860 / 185**，两批合计 **+75 项 / +3 文件**
> （= `test_degradation_contracts.py` + `test_paper_pending_cash.py` + `test_paper_order_guards.py`）。
> **前端本轮零改动**，实测仍 **472 / 56**（含 `TZ=UTC` 复跑同绿；另跑 `--maxWorkers=1`）——**"某一侧不变"同样是可核对的自洽项**。
> ⚠️ **本轮两次踩到"记录值而非实测值"**：①文件数口径混用（见上一段 ⚠️）；②批 1 记的「184 文件」是
> **collect 口径**、而我一度拿 `ls` 口径的 184 去比，凭空多出 2 个文件的"缺口"。
> **两条修法同源：差值对不上时先用 worktree 取实测基线，再逐文件 diff——不要先怀疑自己数错。**
> ✅ **自洽核对（2026-09-14 批次 3 · 正确性收尾轮 R18/R07-a/R19/R20/R24/R26）**：后端 2860 / 185 → **2887 / 186**
> ⇒ 差 **+27 项 / +1 文件**，同样以 worktree 逐文件对账，**并对上了全部 102 项全仓增量**：
> 6（`test_quote_hub.py` · R18）+ 6（`test_position_loop.py` 16 项里归 R07-a 的那 6，余 10 属批 2 R09）
> + 4（`test_board_surge_phase2.py` · R26）+ 4（`test_quote_hub_cadence.py`）+ **7**（`test_ws_quotes_lifecycle.py`，
> 本轮唯一新增文件）= **27**，全额对上；前端 472 / 56 → **484 / 56** ⇒ 差 **+12 项 / +0 文件**
> = 8（`trade-form.test.tsx` 5→13 · R19）+ 4（`use-resource.test.tsx` 16→20 · R20）——**"零新增文件"同样要能对上差额**。
> ⚠️ **本轮抓到的"清单漏项"**：差额一度显示 +27 而我的清单只凑到 +23，缺的 **+4 正是 `test_board_surge_phase2.py`（R26）**
> ——**收尾清单本身也会漏项，唯一靠得住的是逐文件 diff**（这正是上一条纪律的第二次生效）。
> ⚠️ **eslint 回归是"改完当场没跑全仓"漏掉的**：R19 在渲染期写 `ref.current`（`sigRef.current = orderSig`）
> 被 `react-hooks/refs` 判为 **error**（React 官方规则：渲染必须纯），门禁要求 0 error ⇒ 已把该写入移到
> **effect 体内首行**（依赖含 `orderSig`，草稿一改即重跑；而请求最少等 300ms 去抖 + 一个往返，
> **任何回包都晚于本次 effect 的同步段** ⇒ 语义不变、无窗口）。**教训：局部 `eslint <file>` 通过 ≠ 全仓通过，
> 门禁必须整仓跑**（本轮 `npx eslint .` 全量复跑后为 0）。
>
> ✅ **自洽核对（2026-09-14 日期陈旧类 F1–F6 实施轮）**：后端 2887 / 186（**中途快照，非基线**）→ **2954 / 189**
> ⇒ 差 **+67 项 / +3 文件**。⚠️ **但 2887 本身就不是基线**——按纪律不先怀疑自己，取
> `git worktree add /tmp/base master` 实测：**master collect = 2785 / 182**（同目录 `ls tests/*.py` = 184），
> 当前 **2954 / 189** ⇒ **逐文件 diff = +169 项 / +7 文件**，全额对上：
> 新文件 7 个 = 5（`test_agent_toggle_switches`）+ 4（`test_dated_consistency`）+ 19（`test_degradation_contracts`）
> + 28（`test_paper_order_guards`）+ 4（`test_paper_pending_cash`）+ 12（`test_paper_t1_settlement`）+ 7（`test_ws_quotes_lifecycle`）
> = **79**；既有文件扩充 **90**；合计 **169**。
> **前端本轮零改动**，实测仍 **484 / 56**（`--maxWorkers=1`；含 `TZ=UTC` 复跑同绿）——**"某一侧不变"同样是可核对的自洽项**。
> ⚠️ **"记录的中间值"比"记错"更常见**：2887 是批次 3 提交当时的数，此后 F1–F6 又加了 67 项却**未回填**
> ⇒ 「**回填滞后于提交**」第三次留下缺口。**取数一律自己实测，勿沿用上文任何数字。**
>
> ✅ **自洽核对（2026-09-14 F7 三态收口 + 全仓防复发守卫轮）**：后端 2954 / 189 → **2966 / 190**
> ⇒ 差 **+12 项 / +1 文件**，逐项对账**全额对上**：
> 6（**新增文件** `tests/test_tradedate_freshness_guards.py` · 守卫 B1/B2/B3/B3b/B4×2）
> + 2（`test_morning_brief.py` · 未判定必须「不置位可重试」）
> + 1（`test_picks_autogen.py` · 未判定可重试 + 日志口径）
> + 1（`test_ths_sentinel.py` · 未判定⇒`calendar_unknown` 可见降级）
> + 1（`test_intraday_monitor.py` · 同形）+ 1（`test_review.py` · 未判定不得触发复盘）= **12**。
> **前端本轮零改动**，实测仍 **484 / 56**（`--maxWorkers=1`；`TZ=UTC` 复跑同绿）。
> ⚠️ **本轮的"回归"不在代码而在测试夹具**：`test_review.py::test_scheduler_skips_when_today_report_exists`
> 桩的是**旧名** `tc.is_trade_day`，而 `review/service.py` 已改调三态 `is_trade_day_on` ⇒ 桩**失效**、
> 用例隐式绑定真实日历 ⇒ 全量门禁 **1 failed**。**教训：改「判定原语的名字/元数」时，
> 全仓 `grep` 必须用**裸 token**（`is_trade_day`）而不是带前缀的 `_is_trading_day`——
> 本轮第一遍就是被过窄的模式漏掉了这一处**（同族：shell `grep "\|"` 在 BSD 下静默返空，见 kb）。
>
> ✅ **自洽核对（2026-09-14 全面审查轮 · 收尾：缺陷修复 + 报告交付）**：后端 2966 / 190 → **2971 / 190**
> ⇒ 差 **+5 项 / +0 文件** = 1（`test_tradedate_freshness_guards.py` · 守卫 **B1b**：禁止把该名字**绑定**为
> 局部变量，堵 AST 只看 `ast.Call` 的盲区）+ 2（`test_degradation_contracts.py` · 惰性补录**成对**行为守卫：
> `False`（确认休市）才可拦截 / `None`（日历未覆盖今天）须**乐观补录**）+ 2（`test_morning_brief.py` ·
> `is_trading_day` 三态**成对**守卫：`None` 与 `False` 两向都测）。终态实测
> **2909 passed / 62 skipped / 0 failed（2971 项 / 190 文件，121.89s，前提 8000 在跑）**。
> 前端 484 / 56 → **494 / 57** ⇒ 差 **+10 项 / +1 文件** = `components/quality-badge.test.tsx` 6
> （4 条语义 + 2 条防漂移：策略符号只许出现在 2 个策略文件、调用点不得自带 `&&` 门控）
> + `lib/format.test.ts` 4（`shouldShowQualityBadge` 单点策略）。全绿含 `--maxWorkers=1` 与 `TZ=UTC` 复跑。
> ⚠️ **文件数口径再提醒**：本轮 `ls tests/*.py` = **192**、`--collect-only` = **190**（本仓恒差 2）。
>
> ✅ **自洽核对（2026-09-14 数据目录盘点轮）**：后端 2971 / 190 → **2976 / 190** ⇒ 差 **+5 项 / +0 文件**
> = 2（`test_data_path_isolation.py` · CWD 相对路径扫描 + **判据自证**）+ 3（`test_heat_history.py` ·
> skyrocket **读写同源往返** + 缺失**披露**（成对）+ 读侧**不得自持路径常量**（结构））。终态实测
> **2914 passed / 62 skipped / 0 failed（2976 项 / 190 文件，161.05s，前提 8000 在跑）**；
> **前端本轮零改动**，`TZ=UTC` + `--maxWorkers=1` 复跑仍 **494 / 57**（"某一侧不变"同样是可核对项）。
>
> ✅ **自洽核对（2026-09-14 用户报障「两市成交额怎么没出来了」修复轮）**：后端 2976 / 190 → **3002 / 191**
> ⇒ 差 **+26 项 / +1 文件**，**全部来自新建的 `tests/test_snapshot_availability.py`**
> （456 限流识别 / 异常类型**穿透 `gather`** / 退避四态 / `run()` 的 `except` 顺序**静态钉** /
> overview 透传**成对** / 成交额口径一致）——该文件先有 **21 项**，随后因"限流冷却改**渐进式**"
> 又把其中 1 条拆为 1 + 4（参数化）+ 1 ⇒ 净增 **26**。终态实测
> **2940 passed / 62 skipped / 0 failed（3002 项 / 191 文件，140.04s，前提 8000 在跑）**；
> 前端 494 / 57 → **499 / 57** ⇒ 差 **+5 项 / +0 文件** = `lib/format.test.ts` 的 `triAmount`
> （成交额三态渲染：有值 / 未就绪 / 真缺失 / 状态缺失 / NaN）。**6 组注入全部精确判红**
> （含"`except` 顺序"一条：**除该静态守卫外其余 20 项全绿** ⇒ 证明这类判据只能静态钉），
> 复原后各文件 sha256 逐字节一致、`INJECTED` 残留 0。⚠️ 同为 `ls`=**193** / collect=**191**（恒差 2）。
>
> ✅ **自洽核对（2026-09-14 §6.32 · `RSH-002` 因子多窗口扫描轮）**：后端实测
> **3005 passed / 62 skipped / 0 failed = 3067 项**（collect **196** / `ls` **198**，恒差 2；**185.28s**，
> 前提 8000 在跑）。较上一记录值 3062（§6.30）⇒ 差 **+5 项 / +0 文件**，全额等于 §6.31 自述的
> `test_factors.py` **+5 例** ⇒ **§6.31 未留门禁行**（「回填滞后于提交」第 5 次）；
> **§6.32 本身测试零改动**，故本轮**只补数值、不制造增量**（`AGENTS.md` §1 的 3067 / 3005 与本次实测逐字一致）。
> 前端 **502 / 57 逐字不变**（本轮零改动；`TZ=UTC` 复跑同绿）。
> ⚠️ **本轮真实改动是脚本而非测试**：新增 `backend/scripts/verify_window_sensitivity.py`。
> 门禁对「新增脚本」的覆盖只有 **pyflakes（口径含 `scripts/`）** 与 **doc-health 的 J 项**
> ——后者**曾判红一次**，原因不是脚本有问题，而是**新文件未 `git add`**（该守卫判定面是 `git ls-files`
> ⇒ **新文件必须先进索引**，否则「文档点名的代码路径不存在」假红）。
>
> 📌 **「未就绪」不得与「缺失」同形（本轮的判据，`kb/10` KB-ENG-48 续）**：`total_amount is None`
> 有两种成因——上游尚未就绪（会自愈）与真的没有数据；原先前端一律渲染 `--`，用户在界面上
> **无从分辨"在加载"还是"坏了"**。现在后端随值透传 `total_amount_freshness`（复用 S2-1 契约），
> 前端渲染单点 `lib/format.ts::triAmount`（未就绪 → 「加载中…」；链路正常却无值 → `--`）。
> 上游限流侧的处置见 `kb/03` KB-ENG-83（信号结构化 / 穿透中间转换层 / 退避按故障性质分档）。
>
> ⚠️ **本轮的两个静默缺陷都是"注入验证 + 逐定义判定"抓出来的**（详见 `docs/kb/10` KB-ENG-82）：
> ① **读写分叉**——同一路径写两处、锚定方式不同（写侧 `REPO_ROOT` 落**仓库根**、读侧裸相对落 `backend/data`）
> ⇒ 读侧缺文件**静默返回 `{}`** ⇒ `W_SPIKE`（20/100）恒 0 无标记；实测 0/83 → 83/83。
> ② **判据按"名字"全局汇总 ⇒ 同名互相豁免**——新守卫第一版把"是否锚定"收敛成全仓**名字集合**，
> 于是"对的那份"给"错的那份"背书；**注入验证时它仍全绿**才暴露（[[KB-ENG-72]] 标准形态）⇒ 改**逐定义**（键 `<文件>:<常量名>`）。
>
> 📌 **数据目录边界（2026-09-14 裁定，报告：`.workbuddy/reports/data-dir-inventory-20260914.{md,html}`）**：
> 本仓**两套数据根并存且都在被实时写入**，二者都合法，但**分工必须显式遵守**——
> | 根 | 锚定写法 | 放什么 |
> |---|---|---|
> | `<repo>/data/` | `REPO_ROOT / "data" / …`（`parents[3]`） | **主库 `ashare.db`**、`parquet/`、`picks/heat`、`picks/shadow`、`review/predictions` |
> | `<repo>/backend/data/` | `Path(__file__).resolve().parents[2] / "data" / …` | `marketdb/`、`boardflow/`、`fundflow/`、`factors/`、`minute_decisions/`、`position_plans/`、`research/`、`theme_momentum/`、`trade_calendar.json` 等 |
>
> **硬规则**：①**新增数据路径禁止裸相对 `Path("data/…")`**（解析基准是进程 CWD，`uvicorn`/`pytest`/容器/systemd 各不同，且两侧都不报错）——守卫 `test_no_cwd_dependent_data_paths_in_app` 判红；
> ②**一条路径只允许定义一次**，读侧**调用时**取写侧模块属性，不得复制路径；③缺数据必须**显式披露**，空 `{}` 不得冒充"确实为 0"。
> （统一到单一根属**架构变更**，已评估为高迁移风险，**未执行**——需要时单独排期、非交易日做。）

**发布前额外做一次接口载荷体检**（plan-review 三.7，2026-09-01 纳入）：
`node scripts/api-sweep.js`（服务在跑时）——它能抓出"HTTP 200 但数据是空的"这类
测试与类型检查都发现不了的问题（CI 无真实数据跑不了，只能本地/部署后跑）。

CI（GitHub Actions）：后端 pytest+pyflakes、前端 tsc+eslint+vitest+build。推送后**自查 CI**
（`source ~/.zshenv` 拿 GITHUB_TOKEN → `/actions/runs?head_sha=<完整SHA>` → jobs → logs），绝不问用户。
⚠️ **仓库 slug ≠ 目录名，必须从 remote 推导、不要硬编码**：真实远端是
`git@github.com:1239890829/AI-Trading.git`，写成 `hezifeng/ashare-ai-trader` 时 API 会**静默返空**
（`total_count: None`、无报错），极易误判成"CI 没跑"。可靠取法：

```bash
SLUG=$(git config --get remote.origin.url | sed -E 's#.*github.com[:/]([^/]+/[^/.]+)(\.git)?#\1#')
```

## 2. 当前状态快照（沿革记录；**会失真的数字见 §1 门禁行与 `/openapi.json`**）

**测试规模见 §1 门禁行（此处刻意不写数字，见下方 ⚠️）· 四源链 `ths→tencent→eastmoney→sina`（含熔断）· REST 端点数以 `/openapi.json` 为权威**

> ⚠️ **本节只沉淀「不随版本漂移」的口径、决策与沿革**。测试数 / 端点数 / 告警数这类**会失真的数字**，
> 一律以 §1 门禁行与 `/openapi.json` 为准（教训见 KB-ENG-36：清单类内容不该被摘要吞掉，
> 数字类内容不该被写死当事实——本节此前写死的「585 / 94 REST」两条都早已过期）。

**复盘改进项闭环（2026-09-01）：`PATCH /api/review/action-items/{id}` 处置入口 + 前端四态处置控件，破解"改进项只能产出、无法消费"（107 条全 pending、采纳率恒 0）。注意 `get_report` 会用表行状态覆盖 payload 快照——payload 是生成时快照，不同步就会"点了确认回读仍待处置"。PATCH 请求体带 `trade_date/category/title` 守卫三元组：id 是 SQLite rowid 别名且无 AUTOINCREMENT，重跑删除重建后 id 会漂移/跨日串号（实测 111→72），三元组不符返回 409 要求刷新，绝不静默挂到不相干项上。**
**实时行情秒级化（2026-09-01，`5b0024a`）：QuoteHub 1s 固定节奏 + WS 订阅队列终身复用（换队列孤儿化 writer 是"约 30s 才更新"的真因）+ 实时方法腾讯源优先（realtime_rank，ths 付费配额/8s 超时移出秒级链）+ 瞬时失败 stale_after(10s) 容忍 + 分时/K线 WS tick 实时合成（`lib/kline-live.ts`）。实测：列表/头部/K线/分时全部 0.6~1.3s 更新。**
**前端导航 5 项：工作台 / 盘面 /tape / 市场 /market / 每日精选 /picks / 研究 /research**（2026-09-01 页面合并，旧路由 302）

08-31 ~ 09-01 已完成：实时行情修复 → 真实持仓账本 → 题材合力 → 每日精选五维评分+梯队/阶段/闸门/出场纪律 →
跨日回放+参数扫描（组合稳定性：MAX_SWAPS_PER_DAY=2）→ **系统盘点（`docs/archive/architecture-redesign.md`）全清单清零**：
P0 K线三源+熔断+回放限流（`e77971f`）→ 事件采集调度+消息面六维打通（`d8e4e52`）→ 角色胜率（`2b719f3`）→
盘点清理（`50d5b8d`）→ **页面合并：盘面四合一/云图入市场/研究折叠/自选入工作台（`a9d42fa`）** →
基本面 ROE/毛利率评分补全（`ede98be`）→ **09-01 下午**：评审三批执行（分组 CRUD/一屏化/
性能主刀 O1+O2，`cfcbc7c`~`e15f084`）→ 全项目审查+P0/P1 修复（`b1708d0`/`a65b52d`）→
题材 chips 涨跌幅排序（`7ab289b`）→ 时段感知质量判定+P0 撞码修复（`ab2ddba`/`d607756`）→
市场页一屏化+事件契约简化（`52662dc`/`b893819`）→ 待办清零+六项拍板执行：
screener 彻底删除、消融验证启动（`07f29a7`/`c38cb05`）。
**09-03 ~ 09-04 已完成**（明细见 retro-and-gaps.md §五增量账）：联动切片 E/G 全勾账 → 工作台刷新「非法」瞬态修复
（validator `unset_high_low`）→ ESLint set-state-in-effect 26→0（`use-polling-fetch` + 渲染期 adjust-state）→
分时图纵轴按板块限制动态设置（涨停/跌停线贴边，`lib/price-limit.ts`）→ 自选动态分组（每日精选/盘中跟踪，
`top_watch_stocks`+`/api/picks/intraday-top`）→ 复盘新增 picks 准确率维度（逐股归因自动触发+失误 findings）→
盘中情绪监控本体（sentiment P2 #14：高度板炸板/炸板率/指数急杀三类纯规则告警）。
**唯一在途：消融数据自然积累（约 2026-10 中旬跑 `--days 30 --compare-ablation` 出验收）。**

**09-08 ~ 09-09 已完成**（明细见 `docs/retro-and-gaps.md` §五/§六、`docs/kb/`）：
`/hunting` 两页合一（旧 `/picks` `/intraday` 302）· 六相位 style_router（偏移叠加 regime，`|offset|≤0.06` fail-fast）·
空仓闸门三态 `follow_state`（闸门日不给 buy_range）· 每日精选四子模块（`echelon` 梯队地位 / `regime` 炒作阶段 /
`gate` 空仓闸门 / `risk` 风险档位与出场纪律）· 跨日回放 + 参数扫描
（**稳定性靠 `MAX_SWAPS_PER_DAY`，不靠分差门槛**——涨停股梯队分差 30+ 使 15 分门槛形同虚设）·
因子库 P0 评估闭环（qlib Alpha158 族 + TA-Lib，`app/factors/`）· 进化大脑每日议程（15:45 生成；
受限自治默认开启，代码修改仍默认关闭且须管理员显式开启；代码变更走 worktree 隔离且**只提议不落地**，
落地须走 `codex/*` → PR → CI → 审查，见 §0 红线 7）。

**09-10 已完成**（单日大批，逐项状态以 §六 账本为准）：
- **工程门禁与缺陷修复**：测试提速二期（全量 17 分 27 秒 → **5 分 55 秒**，根因是 `test_assistant` 的 function 级
  fixture 被 15 例共用）· eslint 25 → 0 warn · **快讯事件被 UNIQUE 冲突整条丢弃**（四来源方向行只做了部分去重；
  `except IntegrityError` 把「并发重复」与「新行非法」合并成一类 → 整条丢失且每轮重试都失败）
- **闸门与阈值口径**：闸门**分档**（相位级 `退潮/冰点` 或多信号叠加才撤买入区间；单条量化擦线只提示，
  判据按**信号性质**而非 `level` 标签——`level` 是理由条数的计数产物）· 量化阈值改**历史分位**口径
  （`PROMO_FLOOR=30%` 落在 241 日分布之外、近似恒真），绝对经验值降为兜底且**理由里写明所用口径**；
  连带修掉情绪指标库**静默停更 6 个交易日**（调度传字符串日历 → TypeError 被 except 吞）与
  「用昨天的位置描述今天」（`describe()` 给的是历史末行分位 → 新增 `percentile_of_value`）
- **猎场（每日精选 + 盘中跟踪）**：两卡**合并为一个组件 + 两个适配器**（`TradingCard` 中间模型，
  4 组异名同义字段单点归一；`WatchCard` 已退役）· 重新分区为**两条瀑布流（盘中在上）**·
  盘前名单**名额由质量决定**（`MAX_PICKS` 降为容量上限 + 新增 `MIN_PICK_SCORE=50` 入选门槛，
  实测 5 只 → 2~3 只；`meta.removed` 记录出列归因）
- **控制台**：任务中心**留痕合一**（已执行的议程项 → 只读任务视图，不新建表/不写第二份数据）·
  参数白名单 1 → 5 + 运行时覆盖层（**免重启生效**）+ 回滚归因（封闭集合）+ 变更存活率（三数分工，
  排除 superseded 假存活）；**风控/资金类参数永久排除**在白名单外
- **UI 可发现性**：内容行内跳转入口统一 pill（`components/ui/jump-link.tsx`）；
  `Panel` extra 位的跳转**刻意保持低调**（勿无差别套用）
- **文档治理**：09-02 调研的十项候选因子**逐项复核销账**（6 已完成/等价、5 数据阻塞、0 数据具备却未实现；
  核查表在 `docs/summary/factor-system.md §5`）；归档事故教训入 **KB-ENG-36**
  （可执行清单压成一句概括 = 丢失 N 个待办，恢复只能回 git 历史）

**09-11 已完成**（架构改进计划 阶段 0/1/2 全量交付，逐项证据见 `docs/retro-and-gaps.md` §6.5）：
- **阶段 0**（S1-1 / S1-2 / S1-4 / S1-5 / S1-6 ＋ P0-2）：模拟盘 scope 隔离 + 超卖拒单 ·
  持仓三态读取 + 哨兵 · **缺价即拒单**（红线 5 的静默失效修复）· 前端列表接口类型谎言 20 处 ·
  **涨跌停幅度实测两处漂移**（302 段 / 88 段）· 题材强度 N+1 **391 查库 541ms → 2 查库 142ms**
- **阶段 1**（S2-1 / S2-2 / S2-3）：`Freshness` 契约（失败有类型）· `TaskRegistry`
  （26 常驻任务收敛为**一份声明**，停机 66 行 → 1 行，`GET /api/system/schedulers`）·
  四源链**请求级预算**（`REQUEST_BUDGET_SECONDS=12`，按失败进熔断）
- **阶段 2**（S2-4 / S2-5 / S2-6 / S2-7 / S2-9 / S2-10 ＋ P0-3 / P0-4 / P1-1~P1-6）：
  **管线抽离**（`services/picks_pipeline.py`，`picks.py` 1161 → 367 行，反向 import 与
  伪造 `SimpleNamespace` 全解）· **`useResource`**（三态 + 可见性暂停 + 盘外降频封顶 120s，
  裸 `setInterval` **实收编 10 处**）· **`PanelBoundary`**（集成进 `Panel` body，
  一处改动覆盖全站）· **相位常量 9 处副本收编**（并实测抓出「启动」误当市场相位导致
  **「修复」相位从未被覆盖**两处真缺陷）· 角色配色合并为 `lib/role-style.ts` 一份 ·
  三态文案补 `null` 键 + 前后端逐键守卫 · relay-rank **并发化 8.0x**（2044.7ms → 256.2ms）·
  情绪**缓存槽合一**（5 消费方 1 次计算）· 同源双取数收口 · 4 处 `memo` · WS 连接复用
- **S2-8 北京时间收敛 → 阶段 2.5 ✅ 已完成**（低风险子集 09-11；`date.today()` 禁令 09-12 收口）：
  `app/core/bjtime.py` 为唯一权威，`tests/test_bjtime.py` **12 项守卫**（扫 `app/tests/scripts`，
  禁自建 UTC+8 偏移与重复时钟函数）。`app/` 下 `date.today()` 实测**清零**——**含 import 别名形式**：
  首版守卫只匹配 `Name(id="date")`，`from datetime import date as date_cls` 可整条绕过，
  `akshare_ext.py` 因此**漏了 1 处而测试全绿**（09-12 修代码 + 守卫解别名 + 别名自证测试固化）。
  ⚠️ **扫描面仅 `app/`**：`scripts/` 2 处、`tests/` 9 处「取今天」不在其内 → §7 第 3 条

| 阶段 | 状态 |
|---|---|
| 1 基础框架（布局/搜索/主题/错误边界） | ✅ |
| 2 行情基础设施（四源链/质量五级/QuoteHub/WS/K线/分时/盘口 + 数据可靠性：Parquet 原子写与容错读、涨跌停价补全共享化、交易日历兜底、统一缓存层 ttl_cache、天梯 seal_nextday 源自证） | ✅ |
| 3 市场与板块（宽度/情绪周期+历史序列/涨停池/炸板池/题材梯队看板/云图/官方题材目录与成分/题材人气 B1） | ✅（余：题材事件树，P2） |
| 4 投研数据（龙虎榜/资金流/财务/公司资料/新闻公告摘要 v1/集合竞价/复权因子） | ✅（余：营业部图谱/筹码/解禁/两融/大宗，P2） |
| 5 量化系统（多因子评估+防飞刀三修正 / 全市场选股器+六维评分 / 风控引擎 v1：7 档市场状态→下单预检） | ✅ |
| 6 模拟交易与回测（撮合引擎/交易页签/B/S 点+成本线/回测引擎+mandate 化+历史回放/分钟级 TDX 底座） | ✅ |
| 7 AI 系统（盘后复盘 Agent 规则层 / 新题材预判 / 新闻摘要 v1 / 事件驱动选股规则层） | 🔶 规则层全部完成；余 LLM 增强（等凭据）、MCP 封装（等调用方） |
| 8 通知与部署（预警规则+引擎+通道抽象+管理页 / 同源反代 / api-sweep 巡检 / reset 审计） | 🔶 余真实推送通道、Docker 生产化、监控（等部署决策） |
| 9 联动系统（跨页面选中标的统一路由 / 题材⇄个股双向联动 / 官方 K 线交叉验证 / 事件面板） | ✅ 全部完成（切片 E 跳转 2026-09-03；余 L9 事件条目→标的池等 §4 后续与外部触发项） |

---

## 3. 已完成模块清单（索引级；细节看对应文档）

**行情与数据**：Provider 协议 + 四源 failover 链；质量五级校验；QuoteHub（WS 推送+REST 轮询降级）；
K 线（TDX 2 年分钟级底座）；分时（均价线+量比基线）；盘口/逐笔；Parquet 快照（原子写+损坏容错读）；
官方题材目录/成分/板块 K 线（fuyao，T1）；交易日历持久化兜底。

**市场分析**：情绪周期判定（防自指修复/晋级率/中位数/真实炸板池）+ 10 日历史序列；
题材梯队看板（唯一归属/强弱分级/健康度/官方成分徽标/官方 K 线验证的多日涨幅）；
题材人气（B1：`GET /api/themes/hot`，ths 热股榜 × 官方成分反查聚合）；晋级率源自证
（B4：`GET /api/market/ladder-check`，seal_nextday 对照，实测 5 可比日零漂移）；
板块排行（含题材内资金合力 P1-5：官方成分批量快照聚合，`GET /api/themes/catalog/strength` + 官方板块指数 `GET /api/themes/catalog/index`，卡片合力条）；龙虎榜；全市场快照。
**指数详情改造（2026-08-31）**：分时 Y 轴修复（指数无均价概念，`avg` 数学上不成立——
cum_amount/cum_volume 对指数给出 ~15 元荒谬值，该 series 拉爆 Y 轴致"分时一条直线"；
腾讯分钟线对指数 avg 置 null，`is_index_minute_symbol`）；分时/K线标题带标的名称；
右列指数专属 tabs：涨速榜（`GET /api/speed-rank`，口径=最近 5 分钟涨跌幅，同花顺行情
"涨速"列同口径；ths 无涨速数值字段故自算，腾讯批量快照惰性采样 `speed_sampler.py`，
采样历史不足如实显示"采样中"）+ 板块涨幅（复用 /api/boards）；指数页关闭自我叠加、
跳过盘口/逐笔数据源。

**量化**：前端 `analyze` 与后端 `tech_score` 防飞刀口径完全对齐（含量价维度）；
全市场选股器（截面过滤→TDX 日K→六维评分卡，5550 只冷跑 ~20s）；风控引擎（市场状态分类→仓位参数→7 项下单预检，拦截时禁用提交）。

**交易与回测**：撮合引擎（T+1/涨跌停/费用，全部硬拦截）；交易页签（含风控实时预检、parseNum 千分位修复）；
日线回测（代码级防泄露 + mandate yaml 配置化 + meta.applied 来源分层）；历史回放（逐 bar 重算）。

**AI 与事件**：盘后复盘 Agent（规则分析器+模型路由降级+方法论版本化+元结论迭代）；
**每日精选**（`app/picks/`，grill-with-docs 两轮澄清共九项决策）：≤5 只瀑布流卡片（/picks），
**六维**规则版多角色评分（情绪/消息/技术/基本面/资金 **+ 梯队**，TradingAgents 编排思想的规则落地），
收盘定次日+**两道稳定性约束**+盘中硬性失效。四个子模块各自独立可测：
- `echelon.py` **梯队地位**（第六维）：涨停股走 `classify_role` 精确判定，非涨停股用题材基准超额
  推导（领涨/同步/滞涨）；地位分 = 角色基础分 × **题材阶段系数**（启动1.05/发酵1.10/高潮0.95/
  分歧0.75/退潮0.55）× 梯队完整度——个股再强，退潮期也要打折
- `regime.py` **炒作阶段**：财报硬日历（1/2/3/4/7/8/10 月）+ 业绩事件密度校验 → 切换六维权重
  （业绩期基本面 25%/情绪 10%；空窗期情绪 25%/基本面 5%/梯队 20%）。**空窗期还按基本面打分
  会系统性错过妖股**
- `gate.py` **空仓闸门**：退潮/冰点、晋级率<30%、炸板率≥35%、跌停≥15 家、接力亏钱 多条件 OR；
  触发则顶部红色横幅逐条列因 + 组合标注「仅观察」并撤除买入范围（记录仍保留以便复盘）
- **稳定性约束**（跨日回放实证，见 docs/picks-replay-baseline.md）：
  · 换股门槛 15 分：防小幅波动换股——但**单靠它不够**（涨停股梯队分 88 vs 非涨停 52，
  分差动辄 30+，门槛形同虚设，实测日均换手仍 57%）
  · **每日换股上限 2 只**：才是真正的稳定器（实测日均换手 100%→37.1%，平均持有 1.0→2.22 天）
  · carryover：昨日成员即使今日未进候选池也兜底重评（防止因"没上热榜"而静默消失）
- `replay.py` + `scripts/replay_picks.py` **跨日回放**：用历史涨停池+K线回放组合轨迹，
  四策略对照（无门槛/仅门槛/无carryover/完整）。**只有梯队与技术两维可回放**
  （消息/情绪/基本面/资金依赖当前快照，无法回填历史）——勿当作完整选股质量回测
- `scripts/replay_sweep.py` **参数敏感性扫描**：一次拉数据多组参数评估（build/evaluate 解耦）。
  60 交易日实测：换股上限 1/2/3/不限 → 日均换手 20%/39.7%/56.3%/68.8%；
  **门槛 10/15/25 分结果完全一致**（分差型门槛在分层候选池下不起作用）。
  **调组合稳定性请先动 MAX_SWAPS_PER_DAY，不要指望门槛**；2 只=容量 40%/日，
  全组合轮换约 2.5 天，匹配 A 股题材 2~5 天周期
- `risk.py` **风险档位与出场纪律**（借鉴 freqtrade：止损/跟踪止盈/ROI 分档，参数按 A 股重设）：
  止损 = max(档位基准, 1.5×ATR%) clamp 3%~12%；每只带失效条件（题材退潮/高度塌陷/跌破均线/事件证伪）
每日自动复盘（走坏原因九类归类，**买点质量单独评估**：区分"选错了"与"选对了但追高"）
+ 周末元结论建议调权（人工确认生效）；参考仓库择优见 `docs/kb/05-repo-tracker.md`
（2026-08-31 按真实 star 分组复核：补入 freqtrade/last30days/Polymarket 三项，修正漏 3 误收 2）；
新题材预判（六维评分+D1 四问验证）；新闻/公告摘要（规则层，表格正文丢弃纪律）；
**事件驱动选股 v1**（EventCard 规则抽取：来源分级/事实与解读/半衰期模板/方向词典+国产替代对冲；
标的池=题材官方成分反查；market 页事件面板 + 详情页相关事件行）。

**联动系统**（`docs/summary/architecture-design.md`）：统一路由 `lib/routing.ts`（URL 唯一真相源）；
`/stock/[symbol]` 中转修复（路径参数 bug）；题材归属 chips ⇄ 题材看板 focus 聚焦（L4/L5）；
板块多日涨幅官方 K 线交叉验证（B3 关闭，实测推断值方向都反）；预警→详情跳转（L6）；
新闻/公告事件点画上 K 线（P1-8：`lib/event-markers.ts` + KlineChartPro「事件」开关，
公告琥珀●/新闻蓝●，复用 digest 数据零新增请求）；
**指数点击详情（L 扩展，2026-08-31）**：指数卡点击 → 右面板展开指数分时/K线（与自选股同交互）；
指数详情 symbol 规范为带前缀形态（`indexDetailSymbol`：裸 000001 是平安银行、上证指数必须
sh000001）；QuoteHub.get_quotes 兜底 indices + 前缀归一化（model_copy），腾讯 _snapshot key
修复（原裸代码 key 使带前缀查询永远 miss）；指数下隐藏 加自选/交易/资料/资金图。

**工程化**：Next 16 升级（flat config）；错误边界；vitest+RTL 组件测试基建（含变异验证纪律）；
`scripts/api-sweep.js` 全端点巡检（载荷体检）；alembic 三态迁移（手写对齐 ORM）；
**研究/核验工具** `app/research/strategy_verify.py`（战法核验器：特征物化 + **同日市场中性基准**
+ 累计漏斗/单条件独立/参数敏感性/环境分层/分年度稳定性/可成交性，配 22 项合成数据单测）
—— 新战法只写条件表达式，**勿再重写窗口 SQL**（见 `docs/kb/03-engineering.md` **KB-ENG-39**）；
`.env.example` 漂移守护测试；同源反代（Route Handler 运行时代理）；
统一缓存层 `app/core/ttl_cache.py`（TTL/LRU 有界/异步单飞/命中率统计，11 处自写缓存收敛，
`/api/system/caches` 可观测——新缓存一律用它，勿再手写 TTL 元组）；
**防腐化扫描** `scripts/deadcode_scan.py`（月度手动跑，§6.5b #5 裁定不进 CI；
候选 ≠ 死代码——装饰器注册/pytest 约定/跨名引用三类假阳性已固化进判据与 13 项自证）。

---

## 4. 后续规划（**历史沿革**；**唯一任务清单 = `docs/retro-and-gaps.md` §6.0**；文档去向 = `docs/plan-registry.md`）

> **接手者看这里**：系统盘点与重构清单（`docs/archive/architecture-redesign.md`）已于 2026-09-01 **全部清零**，
> 导航已收敛为 5 项。**当前剩余待办一律以 `docs/retro-and-gaps.md` §6.0「任务登记总表」为唯一清单**——
> **「还有什么没做 / 现在能做什么 / 什么要等条件」的唯一答案 = §6.0**
> （2026-09-14 第二次收敛，取代此前「三处结转合起来看」的读法）。
>
> ⭐ **§6.0 的读法**：统一 ID（`<域>-<三位序号>`，域 = `BUG` 正确性 / `IMP` 改进 / `RSH` 研究策略 /
> `GOV` 治理 / `OPS` 运维）+ **五档状态**（可做 / 待批 / 等窗 / 观察 / 搁置）+ **三级优先级**
> （P0/P1/P2，**与状态正交**），按「状态档 → 优先级 → 编号」排序；**旧代号保留为「旧代号」列**
> （A1 / E1 / R03 / P1-42 / #5 …），既有引用仍可检索。
> · **要施工** → 读 §6.0 的 **A 可做**表（**P0 三条最先**：`BUG-001` 历史污染审计 /
>   `IMP-001` 一行 `--reload` 文案 / `IMP-002` 红线 2 界面层缺口）；
> · **查某项的详情与证据** → 按 §6.0「出处」列跳详述层（§6.2 / §6.3 / §6.5b / §6.6 / §6.7）。
>
> ⚠️ **两重历史教训（勿重蹈）**：① 曾写「唯一答案 = §6.5b」，**单一指针导致结构性漏找**；
> ② 扩为「三处结转合起来看」后，核覆盖面仍按「**某来源是否已被覆盖**」判断 ⇒ **漏掉同一份审查报告里
> 的 F 系列与 O 系列共 12 项**（含 **P0 级**「R01/R02 历史污染审计与重算」）。
> ⇒ **核覆盖面必须按「产出物 × 类别」双轴逐类点名**；
> **结构问题不能用「记得看多处」解决，只能用「只有一处可看」解决。**
>
> **纪律（2026-09-14 用户确立，见 `kb/07` §3.3）**：**任何文档新增任务一律登记 §6.0，
> 单文档不得各自维护任务清单**（只留详述 + 指针）；完成一项**当轮**改 §6.0 状态。
> **继续推进须等用户明确指令**（工作模式，用户 2026-08-31 定）；动手前先读 §6。
> **红线**：涉及风控/资金口径变更、删除数据或文件、凭据类动作，一律先经用户确认。

### 阶段 E · 系统重构（✅ 全部完成 2026-09-01，记录见 `docs/archive/architecture-redesign.md` §五）
P0 K线多源冗余+熔断+回放限流 · P1 事件采集调度+角色胜率分布 · P1/P2 页面合并（/tape 四合一、
云图入市场、/research 折叠、自选入工作台，导航 13→5）· P2 screener 冻结+分钟信号删除 ·
P2 基本面 ROE/毛利率 · P3 skills 归档。

### 阶段 A · P0（✅ 全部完成）
1. ~~**sentiment 历史分位校准**~~ ✅ **已完成（2026-09-02 落地，2026-09-10 进一步分位化）**。
   **注意实际走的路与当时的设想不同**：不是用 Parquet 快照算，而是建了**指标历史库**
   `app/sentiment/metric_history.py`（ths 涨停池/炸板池回补，窗口 247 交易日）+
   `app/sentiment/calibration.py` 等分位切档；闸门层进一步按分位判并写明所用口径（KB-DEC-015）。
   **数据底座受数据健康哨兵保护**（该文件曾静默停更 6 个交易日，见 KB-ENG-33）。
2. ~~**统一 provider 缓存层**（P0-5 / 数据源 C3）~~ ✅ 已完成（2026-08-31）：`app/core/ttl_cache.py`
   （TTLCache：monotonic/LRU 有界/异步单飞/命中统计 + 弱引用注册表）+ `GET /api/system/caches` 观测；
   11 处自写缓存收敛。

> **本节（阶段 E/A/B/C/D）是历史沿革记录**，其未完成项**已全部登记账本 §6.0**，此处只留指针：
> **新增任务请登记 §6.0，不要写回本节。**

### 阶段 B · 等用户触发（外部条件成熟即做）
| 项 | 触发条件 | 状态（→ 账本 §6.0） |
|---|---|---|
| ~~推送通道接入~~ | — | ✅ **已完成**：飞书 webhook 落地（盘中只保留买点卡，2026-09-08 定稿） |
| ~~LLM 接入~~ | — | ✅ **已完成**：`claude -p` → cc-switch 当前 DeepSeek（2026-09-19 为 `deepseek-v4-flash`）+ `events/llm_aux.py`（pending 事件二次判定） |
| 生产部署（Docker/编排/监控） | 用户定环境 | ⏸ **搁置** → **`OPS-004`**（镜像与编排已交付 09-04，本机无 Docker） |
| 事件复盘回写（E4：T+N 胜率回写事件权重） | 上线运行积累数据后 | 🔶 **等窗** → **`RSH-017`**（需先有 T+N 事件样本积累，属事件因子闭环） |

### 阶段 C · P1 功能项（✅ 全部完成 2026-08-31）
1. ~~**B1 热股榜**~~ ✅ 2. ~~**B4 seal_nextday 交叉验证晋级率**~~ ✅ 3. ~~**新闻/公告事件点画上 K 线**~~ ✅ 4. ~~**题材指数与板块内资金合力**~~ ✅（`GET /api/themes/catalog/strength` 合力聚合 + `GET /api/themes/catalog/index` 官方指数日 K + 卡片合力条；归属=官方成分反查，行情=腾讯批量快照）

### 阶段 D · P2 远期/触发式（维持观察）
> 全部已登记账本 **§6.0 观察档 `RSH-018`**（= §6.3 P2 前瞻登记册，逐项自带触发条件）；
> **L2 盘口另见搁置档 `RSH-019`**（已拍板维持不接入：券商渠道有货但需花钱 + 开户 + Windows 采集器）。
> **本节不再逐项维护。**

marketdb DuckDB 日级底座 · qlib 因子挖掘 · L2 盘口（无免费源） · 逐笔历史+主动买卖比 ·
题材事件树/生命周期 · 营业部图谱/筹码/解禁/两融/大宗 · MCP 工具层封装（API 契约已就绪） ·
~~切片 E 跳转（热力图/回测/总览→详情）~~ ✅ 2026-09-03 完成（themesUrl 构造器 + 三处接线 + agent-browser 端到端实测）。

### 明确不做（防复发）
C2 全市场日 K dump（已被 TDX 替代）；"等 LLM 再做摘要"（规则先行范式）；next.config rewrites 反代（已被 Route Handler 替代）。

---

## 5. 文档地图（核对过的事实源）

| 文档 | 内容 / 地位 |
|---|---|
| **本文件的「项目入口」块** | 会话短入口（≤3000 字符，只放指针）；文档与任务分别由 `docs/INDEX.md`、`docs/retro-and-gaps.md` §6.0 管理，不新建 MEMORY 权威文件 |
| **docs/INDEX.md** | **文档总入口**：编目（§0.0）+ **主题路由**（§0：按主题列「先读→再读→红线」，含**任务动线** / **症状反查** / **已拍板·勿顺手修清单** / **硬约束速查**）——接到任务不知从哪读起时先看 §0 |
| **docs/PROJECT-MASTER.md** | 技术总览：目录逐文件/数据源口径/API/阶段状态表 |
| **docs/retro-and-gaps.md** | **唯一待办账本**（2026-09-13 压缩：**1226 行 / 19.2 万字符 → 294 行 / 3.5 万字符，−81%**；原文快照 `.workbuddy/artifacts/retro-and-gaps-full-snapshot-2026-09-13.md`）。结构：§一 里程碑 / **§六 待办总账**（6.1 P0 ✅全闭环 · 6.2 P1 · 6.3 P2 前瞻登记册 · 6.4 防重复开发正向索引 · **6.5 执行轮次索引（12 轮）** · **⭐6.5b 结转：仍未闭环的 9 项**） / §七 偏差更正（**错题本**，34 处） / §八 计划文档处置。**「还有什么没做」的唯一答案 = ⭐ §6.0 任务登记总表**（2026-09-14 建；§6.5b 已**降为详述与证据层**，其未完成项全部登记在 §6.0）；执行细节去 `.workbuddy/memory/` 逐日日志 |
| **docs/summary/** | 主题汇总 6 份（stock-strategy / factor-system / data-market / architecture-design / ai-evolution / review-governance）——**已完成方案的精华收敛处** |
| **docs/kb/** | 权威知识库（KB-STOCK/TRADE/ENG/DEC + 00-INDEX，**全序列唯一登记处**）；**工程教训 KB-ENG 按子类分四册**（09-12 按 §5.2 条件① 拆出，引用只写 `[[KB-ENG-NN]]` 即可，查条目不必知道册名）：`03-engineering.md` = **应用与设计层**（架构/接口/口径判据/前端/方法论）/ `09-verification-pitfalls.md` = **验证层**（测试·门禁·CI·防线有效性）/ `10-data-contract-pitfalls.md` = **数据契约层**（写入·去重·传输·时间·质量门）/ `08-tooling-pitfalls.md` = 工具与环境陷阱速查（KB-ENG-01~15 操作类短条目）；`07-doc-curation.md` = 文档治理规范（v1.6：§3.2 完成即沉淀删件、`📎 示例` 状态、`scripts/doc-health.py` 一键体检）。**示例/题材案例一律标 `📎`，不得与 `✅ 已落地` 混用** |
| docs/plan-registry.md | 历史计划去向表 + 文档处理规范（**不再新建计划文档**） |
| docs/api.md | API 契约（端点数以 /openapi.json 为权威，文档按域分节） |
| docs/data-sources.md + data-source-comparison.md | 字段口径实测记录 + 四源能力选型（改 Provider 前必读） |
| docs/sentiment.md + theme-sentiment-methodology.md + theme-prediction.md | 情绪口径 / 题材情绪方法论 / 新题材预判 |
| docs/review-agent.md + daily-review-sop.md + daily-review-checklist.md | 复盘 Agent 架构 / 复盘 SOP / 执行清单 |
| docs/backtest-rules.md / risk-management.md | 回测代码级禁令（做回测前必读） / 风控红线 |
| docs/deployment.md / websocket.md / mcp.md / architecture.md / data-dictionary.md | 部署+环境变量全表 / WS 契约 / MCP 清单 / 架构 / 数据字典 |
| docs/live-trading-guosen-plan.md | 实盘接入蓝图（⚫ 搁置，等用户恢复） |
| docs/daily-review/ · evolution/ · repo-watch/ | 逐日复盘 / 进化议程日志 / 仓库跟踪周报 |

**账本约定**：待办明细以 `docs/retro-and-gaps.md` §六 为**唯一账本**（已完成方案文档即删，精华进 `docs/summary/`）；
优先级看总账 P0/P1/P2；README/PROJECT-MASTER 只留阶段级索引。**完成一项划一项并 git checkpoint。**

---

## 6. 工作方式（前任验证过的教训，勿重蹈覆辙）

### 6.1 交付纪律
- **验收以实际看到的为准**：UI 改动用 `agent-browser snapshot`（无障碍树文本）+ `eval` 直读 DOM 验收；
  当前模型读不了 PNG，截图拍了无法目视。布局类问题如实说明"需人工目视"。
- **每阶段流程**：实测数据源（curl 先行）→ 小切片实现 → 全量门禁 → 浏览器文本验收 → commit/push → CI 自查 → 文档同步 → 记忆。
- **改完后端必须重启验证 8000 上的实例**（无 --reload 时改完不重启=旧代码）；起服务必须用
  `run_in_background`，bash 子 shell `( &)` 会被沙箱收割（踩过 3 次）。
- 禁止 dev server 运行时 `next build`；pytest/build 需要 `CODEBUDDY_SAFE_DELETE_ENABLED=0`（沙箱批量删除保护）；
  **trade_calendar.json 已是未跟踪运行态**（gitignored，git checkout 对它无效）：测试已隔离、不再改写它
  （2026-09-12 实测 45 项 calendar 测试前后文件无变化），健康判据 = `source:"official"` 且 days ≥ 240；
  重启后端会合法重写该文件的 fetched_at，diff 只有时间戳属正常。
- **测试隔离是"库隔离了、文件没隔离"**：`tests/conftest.py` 把库设成 `sqlite:///:memory:`，
  所以测试改不到生产数据行；但凡写盘的目录（如 `app.review.storage.REPORT_DIR`）必须一并指向临时目录，
  否则测试垃圾会落进 `data/review/reports/`，且按 trade_date 删文件的清理逻辑会误删真实报告
  （2026-09-01：测试把 `20990101.json` 留在生产目录，差点删掉 `20260901.json`）。
  同理，`_cleanup(sf, td)` 只接受 `2099*` 开头——`review_reports.trade_date` **没有唯一约束**
  （只有 `review_id` 唯一），同一天可并存多行，按日期删会连真实报告一起删。
- **进程内缓存一律用 `app/core/ttl_cache.py` 的 TTLCache**（`cache_on(holder, name, ttl, maxsize)`），
  勿再手写 `(time.time(), payload)` 元组；键空间必须有界；命中响应标注 cached 用
  `model_copy(update={...})`，不变异共享缓存对象。
- 复杂 JSX 整文件重写；长内容写脚本文件；**文档/代码编辑一律用 Edit/Write 工具**（node -e 撞 shell 引号已翻车 3+ 次）。
- `apps/web/tsconfig.tsbuildinfo` 已 gitignore（tsc --noEmit 会改它，不入库）。

### 6.2 接新数据源五步法（见 docs/data-sources.md）
curl 先行 → 记录字段口径与类型陷阱 → 多采样找规律 → fixture 从实抓数据生成 → 写进文档。
**缩放陷阱实例**：东财涨停池价格 ×100、炸板池 ×1000——用自家 TDX 日 K 交叉验证。
**官方文档类型描述不可全信（2026-08-31 再证）**：`seal_nextday` 文档写 string 实为布尔；
热股榜 `heat` 是字符串数字 "6002184"；`sign_level` 文档 string 实为恒 0 整数；
`boards.*`"最多 4 只"实为无上限。凡响应字段，以 curl 实抓为准并写进 provider docstring。

### 6.3 工程教训（2026-08-30/31 两轮密集迭代 + 同日五次交付沉淀）
- **Next.js**：`[symbol]` 路径参数在 page 里是 `params`（Promise），`searchParams` 是查询参数——
  两者不匹配**不报错只静默丢参**（跨页面联动 bug 根因）；`NEXT_PUBLIC_*` 构建期内联；
  `rewrites()` 构建期求值，运行时代理必须用 Route Handler。
- **React/vitest**：未开 globals 时 RTL 自动 cleanup 不注册（症状：单跑过全量红）；防抖组件断言等真变的值；
  回归测试要做**变异验证**（临时改回 bug 版本确认测试变红）。
- **monkeypatch 时钟会冻结事件循环**：给 TTL 缓存做可控时钟时 `setattr(tc.time, "monotonic", fake)`
  打的是 stdlib time 模块本身（事件循环 `loop.time()` 同源）→ `asyncio.sleep` 永不触发、测试死锁。
  模块内 `from time import monotonic` 后补丁打**模块级名字**（`tc.monotonic`）才安全（P0-5 踩过）。
- **接功能前先盘点已有能力**：B1 的 ths provider 方法早已存在且完备，缺的只是消费端；
  详情面板已拉 digest 就不用为 K 线事件点加新请求。先 grep 再设计。
- **fake 桩必须复刻真实契约**：回归测试里桩若容忍非法输入（如 `date_ms(None)` 的崩法），
  测试锁不住 bug——桩收到该崩的输入就该抛同样的错。
- **lightweight-charts 多套 marker 必须合并后排序、一次 setMarkers**（时间升序）；
  分开调用会互相覆盖。画在 canvas 上的东西 a11y 文本快照看不见：能验的是开关存在/
  无错误横幅/canvas 数量，视觉如实转人工目视。
- **SQLAlchemy**：`query.delete()` 绕过 ORM 级联留孤儿行；删有关联对象走 `session.delete(obj)`；
  sessionmaker 不支持 with 语法（用 `sf()` 返回的 Session）。
- **pytest**：共享内存库跨文件污染——测试用独立代码/变体标题，**断言锁成员关系不锁全等**
  （`assert "题材" in themes` 而非 `themes == [...]`，单跑过全量炸的常见根因）；
  `logging.basicConfig` 会破坏后续 caplog（审计断言打桩 logger）；
  窗口/序列类数据测试要构造完整邻接关系（如天梯的"次日锚行"）；
  全量 pytest 偶发卡在线程锁等待（与 uvicorn/dev server 并发抢资源的环境抖动）——
  杀掉重跑再判断，勿直接改代码。
- **排查**：页面 `performance.getEntriesByType('resource')` 看真实请求 URL；
  bash grep 在沙箱不可靠——查代码用 Grep 工具或 node -e。
  ⚠️ **`grep "a\|b"` 不是可移植写法**：`\|` 不属于 POSIX BRE；不同 grep 实现可能拒绝、
  当字面量，或作为 GNU 兼容扩展接受。当前本机 `/usr/bin/grep` 自报
  `BSD grep, GNU compatible 2.6.0-FreeBSD`，实测会把它当交替并正常命中；因此历史上的返空
  **不能再归因于“BSD grep 不支持”**。统一写 `grep -E "a|b"` 或用 `rg`。
  **更硬的规矩**：任何「某物不存在」的结论，在**据此动手改之前**
  必须用 Grep 工具复核一次——该坑的真实危害不是漏看，而是它会被当成"不存在"的证据，
  **诱发主动的错误修正**（2026-09-14：据此断言报告无 §5.3，实际在第 345 行）。见 KB-ENG-04。
- **轮询/刷新类验收**：resource buffer 默认 250 条会**静默溢出**（计数停滞假象）——
  先 `performance.clearResourceTimings()` 再测间隔；headless 页面挂 5 分钟后进入
  intensive throttling，长间隔轮询计数偏低属环境行为，以"清 buffer 后短窗计数"为准。
- **词表/规则类功能**：漏词是常态，靠真实数据发现并补测试；错误归类不要信 catch-all（Parquet 损坏曾被误报为"TDX 源不可用"）。

### 6.4 行为基线（勿回退）
红涨绿跌 · tabular-nums · 所有数据带来源/时间/质量标注 · mock 不冒充实盘 ·
布局锁一屏（容器内滚动）· 每处可解释输出带 basis · 右列宽度用户可调（260-480px）。
**鉴权默认拒绝**（R22，2026-09-15）：`ASHARE_API_TOKEN` 配了之后**所有** HTTP 路由
都要 `X-API-Token`（唯一豁免 `GET /api/health`）、`/ws/quotes` 要子协议凭据。
**新增端点无须做任何事**——它自动受保护；**要豁免才需要动清单**（`core/auth.py::AUTH_EXEMPT_PATHS`，
全库仅 1 条 + 有测试钉住）。三条纪律：

1. **豁免只能挂到"真正只想开一个端点"的对象上**：`include_router(某 router, dependencies=...)`
   是**整组**生效。R22 实施时踩到——豁免 `health_route.router` 连带放开 6 条 `/system/*`
   （含 `force=1` 会真实花钱的 `/system/llm-probe`）。已把 `/health` 拆到独立的
   `liveness_router`。**新增 router 前先看它里面有几个端点。**
2. **判据要用行为式，不要用结构式**：本版 FastAPI 的 `include_router` 生成 `_IncludedRouter`
   包装对象，`route.path` 是**未加 prefix** 的原始路由、router 级 `dependencies` **不在**其上
   ⇒ 遍历 `route.dependant` 会得出"176 条全部无守卫"的错误结论。正确做法是遍历
   `app.openapi()["paths"]` **逐条发无凭据请求断言 401**（不关心守卫挂在哪一层）。
   ⚠️ `app.openapi()` 结果被实例缓存 ⇒ **注入自证前必须先清 `openapi_schema = None`**，
   否则新注入的路由根本不在探测面里（假绿）。
3. **失败方向必须是 fail-closed**：`shared` 姿态未配 token、`auth_mode` 取值拼错
   ⇒ **拒绝启动**（拼错一个字母会让判定静默退回 `local` = 共享部署全放行）；
   运行期判定也独立堵死空 token（**不能把启动校验当唯一防线**——测试夹具/脚本直连/自定义
   ASGI 入口都可能绕过 `main.py`）。

### 6.5 GitHub Collaboration Workflow（适用于整个仓库）

> 本节是仓库级 GitHub 协作规范，适用于所有功能、修复、重构与文档任务；取代此前以 `develop`
> 为日常开发分支及允许 Codex 自行合并 `master` 的约定。历史分支和提交仍保留在 Git 历史中。

- `master` 是主分支；**2026-09-19 已启用 GitHub 平台级 branch protection（`GOV-012` 闭环）**：必须经 Pull Request，required checks = `backend (pytest + pyflakes)` / `frontend (tsc + lint)` / `docs (doc-health)`，`strict=true`，管理员同样受约束；force-push / 删除 `master` 禁止，未解决 PR 对话禁止合并。开发任何功能、修复或重构前，必须先同步远程最新的 `master`；禁止直接在 `master` 上修改、提交或推送代码。
- 功能开发必须在从最新 `master` 创建的独立分支中完成；Codex 创建的分支统一命名为
  `codex/<简短英文任务名>`，禁止直接在 `master` 上开发或提交。
- 功能分支必须通过 Pull Request 合并到 `master`，不得通过直接推送绕过 Pull Request。
- 只修改当前任务需要的文件；不得覆盖、删除或回滚用户已有的无关改动。
- 修改完成后必须运行适用于本次改动的测试、代码检查和构建命令；具体门禁及环境注意事项见 §1。
- 提交前精确暂存本任务文件：`git diff --exit-code` 查未暂存漂移，
  `git diff --cached --name-status` 对范围，`git diff --cached --check` 查补丁，再读暂存正文确认回填。
  `git ls-files --others --exclude-standard` 逐项分类；正常 staged A/M/D 是预期，不要求 status 为空。
  提交后核实际 commit 文件集与本任务工作区干净，禁止用 `git add -A` 吸收无关改动或清用户脏树。
- 禁止提交 `.env`、API Key、Token、密码、私有数据、缓存文件或无关构建产物；Public 仓库还禁止提交个人 home 绝对路径、本地邮箱/机器名。`public_repo_scan.py` 为 required CI 的组成部分，不得通过豁免/删除扫描器来绕过。
- **npm 依赖安全**：收到 Dependabot/npm audit 告警时，优先做可验证的最小安全补丁；禁止直接 `npm audit fix --force`。本机 npm 默认镜像 `npmmirror` 不实现 audit API，安全复核须显式 `npm audit --registry=https://registry.npmjs.org`。有 lock/shrinkwrap 的子项目必须同步锁文件并用 `npm ci` 重新验证；无 lock 的归档示例至少用一次性临时 lock + 官方 registry audit 证明修复后无已知漏洞。
- 使用清晰的英文 commit message。
- 完成后必须提交修改，将功能分支推送到 GitHub，并设置 upstream。
- 如果任务需求存在会明显影响实现方案的歧义，应先询问用户；否则直接完成开发、测试、提交和推送。
- 每次交付必须汇报：分支名称、主要改动、修改文件、测试结果、commit SHA、远程分支或 PR 链接，
  以及遗留问题或风险。
- Codex 负责本地实现、验证、功能分支交付与合并；**不再要求网页版 ChatGPT 对功能分支进行最终代码审查**
  （2026-09-16 用户长期授权）。该授权只适用于 Codex 开发流程，不改变 §0 红线 7 对应用内 LLM
  自主改码执行器的隔离与禁止落地约束。
- 用户授予 Codex 长期授权：当以下条件全部满足时，Codex 可以直接合并
  功能分支的 Pull Request，无需再次征求用户确认：
  1. 当前任务要求已经完整实现；
  2. 适用的本地测试、代码检查和构建均通过；
  3. 对准确 PR HEAD 运行 `python3 scripts/audit/release_check.py <PR编号> --expected-head <完整SHA>`：
     最新 master 必须已集成，当前 CI 最新 attempt 的三 job 必须全部存在且 completed/success；
     其它 Actions/commit status 阻塞同样阻止合并。空结果、读取失败、旧 SHA、skipped/cancelled 均不得放行；
     本地门禁和准确 diff 审阅另行完成；此脚本是客户端发布证据，**补充而非替代**已启用的 `GOV-012` 平台 branch protection；
  4. 最新 `master` 已集成并重验；新提交、base 漂移或重跑 CI 使旧验收失效；
  5. Pull Request 中不存在未解决的 `Request changes` 或阻塞性审查意见；
  6. diff 中不存在敏感信息、无关文件或未经说明的破坏性修改。
- 满足全部条件后，Codex 应自动合并 Pull Request，无需等待用户再次确认。任一条件不满足时不得合并；
  应修复问题并重新验证。如果无法解决，应向用户汇报阻塞原因。
- 自动合并授权不包含强制推送、绕过 CI、忽略已有阻塞性审查意见、改写 `master` 历史或执行其他破坏性 Git 操作。
- 合并时使用 `gh pr merge <PR编号> --merge --match-head-commit <完整SHA>`；合并后复验 master CI 与实际运行版本，汇报 PR、合并 SHA、测试和遗留。
- Pull Request 合并并确认分支提交已进入最新 `origin/master` 后，应立即删除对应本地与远程功能分支。
- **CI 触发条件（2026-09-15 已修）**：`on: push: branches: [master, main, **develop**]` + `pull_request`。
  三份 job 分别执行后端 pytest+pyflakes、前端 tsc+vitest+eslint+`next build`、文档体检；不得削弱这些门禁。
  功能分支若需 GitHub CI 结果，应创建 PR 触发 `pull_request` 检查，但不得代替本地适用门禁。
- ⚠️ **`gh` 工具**：已装在本机 `~/.local/bin/gh`（v2.100.0；该目录**不在非交互 shell 的 PATH** 里，
  脚本里用绝对路径）。**✅ 已认证**（2026-09-16 实测：`gh auth status` → 账号 `1239890829`，
  token scopes `gist` / `read:org` / `repo`）⇒ 可代读 CI 运行/日志、建 PR 与合并。
  ⚠️ **本行曾写「尚未认证」并据此要求用户先 `gh auth login`** —— 属"事实性过期"：
  凭据早已就绪，却让人误以为通道未通（[[KB-ENG-85]] 同族：**指针/状态失效**）。
  本环境 `github.com` 需走本地代理 `127.0.0.1:7897`（沙箱代理 51931 到不了），`api.github.com` 可直连。


### 6.6 TypeSafe/Jev 固定协处理流程

> 详细架构、已安装全局能力、AShare 各域落点与启用门槛见 `docs/jev-integration.md`（FN-10）。

- **每个非简单任务先判断 Jev 是否值得用，但“判断”不等于“调用”**：确定性代码、用户已指定工具、只有一个合法下一步时直接执行，不得为了“用 Jev”再发请求。
- **项目业务不得各写一套 TypeSafe 客户端**：后端统一走 `app/core/jev_client.py`；Codex 一般 Choice/Noul/Score 走全局 `evaluate` MCP；只有 ≥2 个真实 Tool/Skill/MCP/CLI/Subagent 候选且选择不明显时才用 `jev-capability-route`。
- **默认 shadow / off 必须分清**：alert triage、pending event prefilter、assistant tool-group router 维持既有 shadow；Universal Verification 当前默认 `off`，只有显式 shadow 才运行，且只判 public evidence→claim。存在 `extra_block` 私有上下文、任一非公共工具，或用户问题本身含“我的持仓/仓位/成本价/账户/余额/自选/资产/盈亏”等私有语义时整轮 verifier 跳过，避免 claim 本身泄漏私人信息。
- **人工金标准不得被规则或 Jev 预标注污染**：`data/labels/jev_goldset_events_v1.jsonl` 的 `reference_rule` 仅供对照，`*_predictions_jev-*.jsonl` / `*_review_priority.jsonl` 仅用于安排人工审核顺序；准确率/阈值只认独立填写的 `human` 字段。任何自动脚本都不得把 rule/Jev prediction 复制进 `human`。未完成 `--require-human` 严格校验前，不得宣称 gold set 已完成或据此调生产阈值。规则/Jev agreement 永远标为 agreement/disagreement，禁止写成 accuracy。
- **jev-review 按价值使用**：高影响、跨模块、语义复杂或 tests/static 无法充分覆盖的 coherent code slice 才评审；简单机械修改、纯文档、确定性 guard 已充分覆盖的变更不强制调用。正常 tests/CI 永远优先于 Jev 分数。
- **jev-pref 按治理风险使用**：触及 shadow→production、交易/风控红线、概率语义、前后台边界、策略/因子/做T口径时使用；普通格式/文案/机械 diff 不为凑流程调用。
- **jev-context 保持 ask-only**：精确 rg/少量候选直接使用；只有宽检索/大输出且可能真实减少后续上下文时才过滤，不能用它证明“仓库不存在”。
- **浏览器验证按不确定性路由**：已知 URL/selector/验证条件直接 Browser Harness/普通 browser；只有 DOM/ARIA 下一动作或目标元素不确定时才用 `jev-browser`；复杂 iframe/canvas/上传/弹窗/视觉布局回退既有 browser/computer-use；最终状态必须独立复核。
- **高风险永不委托**：T+1、涨跌停、费用、仓位、RiskEngine、真实下单、资金/权限、时间/数值计算、回测真值仍由确定性代码负责。
- **概率语义不可混用**：Noul=yes 概率，Choice/Score 的 confidence 是模型结构化置信信息；它们都不是上涨概率、策略胜率或代码正确率。
- **节省额度必须实测**：只在“质量不降 + DeepSeek/Codex/ChatGPT 调用/token 实际下降”时宣称节省；Jev 聚合指标由 `GET /api/system/providers` 的 `jev` 字段、全局 metadata receipts 与 RSH-030 对照收集。
- **社区 Jev 工具进入活跃栈必须同时满足**：解决独特问题、有本机/本仓证据、不扩大权限/隐私面、调用频率可控。旧 `jev-route` 已因“不能自动切模型却固定多烧一轮”停用；OpenRouter provider 当前不接。

---

## 7. 待用户决策（阻塞项，勿催促，列清单等待）

> 已解决不再列：~~推送通道~~ ✅ 飞书 webhook（09-08 定稿，盘中只留买点卡）、~~LLM 凭据~~ ✅ `claude -p` → cc-switch 当前 DeepSeek（2026-09-19 `deepseek-v4-flash`）、
> ~~定时 automation 去留~~ ✅（见已决 ④）、~~RiskEngine 接入撮合~~ ✅（见已决 ④）。
> **本节只列「需要人拍板」的**；样本不足类阻塞（P1-30、P1-7+P2-11、P1-23、P2-14、P1-42）不进这里，它们等时间不等人。

| # | 决策 | 影响面 |
|---|---|---|
| 1 | 部署环境（NAS / 云服务器 / Vercel+Railway）——**⏸ 2026-09-12 用户明确「部署先放到后面再说」⇒ 主动搁置，非阻塞** | Docker/编排/监控；需有 Docker 的环境实测 |

> **已决（留痕，勿重开）**：
> ① **`date.today()` 守卫扩面 → ✅ 2026-09-12 已扩面并加固（无需再拍板）**。扫描面 `app/` → **`app/` + `scripts/` + `tests/`**；
> 守卫判据由「接收者叫 `date`」升级为**「任何 `.today()` 都违例」**（`date.today()` / `date_cls.today()` / `pd.Timestamp.today()` 取的都是**进程本地时区的今天**）。
> **触发这次加固的是一个真漏检**：`akshare_ext.py` 写 `from datetime import date as date_cls` + `date_cls.today()`，**整条绕过旧守卫且测试全绿**——
> 守卫存在绕过口比没有守卫更糟（它让口径分裂**看起来已解决**）。已改 `beijing_today()` 并新增**守卫自证测试** `test_today_detector_sees_every_receiver`
> （合成源码逐种等义写法断言被识别 + 反例不误伤），此类漏检不会再有下一次。详见账本 §6.5 #1b 与 §七第 32 条。
> ② **控制台「自定义规则 UI」→ 2026-09-10 拍板保留**（成本近零，且是全系统唯一能写自定义阈值提醒的入口；
> 位置：`/agent?tab=alerts`「提醒与告警」，链路 = `POST /api/alerts/rules` → `AlertEngine` 轮询 → `alert_triage` → 悬浮球/in_app。
> 当前用户自建规则 0 条，库内 4 条全是 `__` 前缀系统规则——是**入口深 + 无需求**，不是功能缺失）。详见账本 P1-17。
> ③ **外部付费数据源 → 🚫 2026-09-12 用户拍板「不要了」· 不接入**（防重开：不是预算暂缓，而是**花了钱也买不到要的东西**——
> 四家均无 L2；唯一候选 KlineShare ¥399/月 作 ths 备源还需先过「是否含涨停原因」等三项验证，投入产出比不成立）。详见账本 P2-31。
> ④ **2026-09-13 账本全量执行轮（用户授权「账本任务全部执行、不需确认」），四项裁定落地**：
> **a. RiskEngine.check_order → ✅ 接入模拟撮合硬拦截**（main 账户买入强制过闸；shadow 豁免与「卖出永不拦截」写成结构判据；
> 拒单原因「风控拦截：…」；口径单点 `paper.risk_check_context`）。详见账本 §6.5b #2 与 `docs/risk-management.md` §1。
> **b. PanelBoundary.resetKey → ✅ 默认随 label 变化清除**（显式 resetKey 保留给同 title 换内容；成本钉进测试）。详见账本 §6.5b #4。
> **c. 防腐化扫描 → ✅ 不进 CI、月度手动跑**（`python3 scripts/deadcode_scan.py`，候选 ≠ 死代码，删除前逐项人工复核）。详见账本 §6.5b #5。
> **d. 定时 automation → ✅ 3 条循环保留**（仓库发现/周报/进化总结推送）**+ 过期一次性已删**
> （ID `58209806`，用户在平台侧执行确认，2026-09-13）。

> `data/parquet/snapshots/20260830/` 下 7 个损坏文件**不占决策位**：读取已容错、新快照会自动覆盖；
> 若要清理，按删除纪律走 `scripts/safe-trash.sh`（进项目回收站，可 `--restore`）即可，不必问。

---

## 8. 关键常识

- Provider 链 `ths→tencent→eastmoney→sina` 逐方法 failover；加数据源 = 实现协议 + 注册 factory + 加链；
  特殊数据直取特定 Provider（`_pick_provider`），避免 composite 串行重试拖垮事件循环
- 质量五级：high/medium/low/stale/invalid；low 及以下 AI 禁用、回测禁用、前端强制标识
- 交易撮合 `app/paper/engine.py`；风控引擎 `app/risk/`（状态分类→参数→预检）；
  事件引擎 `app/events/`（抽取/存储/标的池）；题材目录 `app/services/theme_catalog_service.py`；
  统一缓存 `app/core/ttl_cache.py`（命中率/逐出经 `GET /api/system/caches` 观测）
- fuyao 官方端点：题材目录（cn_concept 390 个）/成分/板块 K 线/涨停池/连板天梯/热股榜，
  文档在 `skills/hithink-finance/docs/api/*.md`，key 在 settings.ths_api_key——**调接口前先读对应 md，
  但字段以 curl 实抓为准**
- 东财 push2 本机被 WAF 限流：行情走腾讯，特殊数据走 datacenter/push2ex；
  涨停池价格 ×100、炸板池 ×1000（缩放已用 TDX 交叉验证）
- 非交易日/盘前语义：当日涨停池为空、归因空是正确语义（`?date=` 回看历史）；
  天梯矩阵最近交易日 `seal_nextday` 全 null 是正常语义（无次日参考）
- Parquet 快照每 5 分钟落 **项目根** `data/parquet/snapshots/`（按日分目录，选股器/情绪地基），
  写入必须走 `parquet_store.write_parquet_atomic`；backend/data 下没有快照
