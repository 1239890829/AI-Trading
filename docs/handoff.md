# 交接文档 · 任务明细层（HANDOFF）

> **定位**：本文件是**任务明细层**，与账本 `docs/retro-and-gaps.md` **§6.0** 分工——
> `§6.0` 回答「**有哪些任务 / 什么优先级 / 什么状态 / 前置是否满足**」（全仓**唯一**任务清单）；
> 本文件回答「**这个任务到底做了什么 / 凭什么说做完了 / 还剩什么**」。
> **本文件不另立任务清单**：任何新增或未完成项一律**归集至账本 §6.0**（规则见
> `docs/kb/07-doc-curation.md` §3.3），此处只写明细。
>
> **双向索引（机械可查）**：
> · 账本 `§6.0-H 交接索引` → 本文件 `§<任务 ID>`（点击即到条目）；
> · 本文件每条条目首行 → `账本 docs/retro-and-gaps.md §6.0 <任务 ID>`（回链状态与前置）。
> 两侧缺任一向即视为**指针失效**，由 `python3 scripts/doc-health.py` 的 **P 交接索引**判红。
>
> **维护纪律**：每完成一段可独立验收的工作，**当轮**追加或更新条目——不攒、不推迟、不留给下一位。
> 条目控制在一屏内；过程细节与长证据放忽略的 `artifacts/`、逐日日志或测试文件本身，此处只留**结论 + 指针**。
> ⚠️ **轮次编号口径**：`§6.NN` 形式的轮次小节位于 `.workbuddy/memory/<日期>.md`（逐日日志），
> **不是账本节号**；本文件不自行分配轮次号，只用日期标注。

## 0 条目模板（新增时照抄；字段固定，便于机械核对与快速接手）

```markdown
## <TASK-ID> <一句话结论>
- **账本**：`docs/retro-and-gaps.md` §6.0 `<TASK-ID>` ｜ **日期**：YYYY-MM-DD ｜ **状态**：✅ 闭环 / 🟡 部分闭环 / ⛔ 阻塞
- **缺口与验收标准**：开工前认定「哪里不对、做到什么算完」。
- **改动**：文件清单 + 一句话作用（含"刻意没动什么"）。
- **机械证据**：可复算或可复跑的数字/命令，不写形容词。
- **注入自证**：把缺陷形态注回去，守卫是否真的变红（没有守卫的项写明「无」）。
- **门禁**：后端 / 前端 / 静态检查 / doc-health 的实测数字。
- **遗留与下一步**：未做项与它的账本 ID；无则写「无」。
```

## 1 现场（每条任务收尾时更新）

- **最新授权与当前施工**：用户已解除下述历史额度暂停，允许使用剩余额度并优先降低后端 CI 成本。当前 `codex/ci-runtime-budget`（`/tmp/ashare-ci-budget`），从最新 master `d16be3c` 建立并快进复用 `7f635d7`；任务 `IMP-041`。
- **分钟预算（账户页面只读实测）**：1,810/2,000 已用，剩余 190，14 天后重置；常规集中批次暂分配 150，保留 40 供收尾/故障，每批先刷新实际用量。PR 与 master 的各 job 都计入预算，不靠删测试/跳检查省分钟；不启用付费。发布前账单刷新遇本机锁屏，当前沿用上述本轮实测余额（此后本任务未新增 Actions），不将其标成再次核实的账户余额。

- **当前完整门禁（IMP-041）**：后端 **3566 collect / 3490 passed / 76 skipped / 0 failed，141.26s**；8000/3000 未运行，前后端全量串行，期间有只读核查及文档编辑。前端本地/UTC **650 passed / 70 文件**，单 worker **54.86s / 45.03s**；tsc **0**、eslint **0/0**、pyflakes **0**、构建通过。
  与 `7f635d7` 的全量 JUnit 比较，3566 个用例身份逐一相同，仍 216 文件、import-lint 269、skipped 76；前端源码零差异。doc-health **23 条索引 / 0 档位冲突**，完整 OpenAPI **170 paths / 零差异**。云端耗时待新 HEAD 的完整 CI 实测；证据位于本批 artifacts/verification，交付归档到原项目 artifacts/runs/ci-budget-20260917。

- **上一切片（2026-09-17，额度暂停时快照）**：`codex/theme-member-write-guard`，工作区 `/tmp/ashare-theme-write`；自最新 master `d16be3c` 建立，再快进复用 PR #24 的 `8b155a7`。本批题材保护本地验收通过，收尾提交以本分支最新 Git 记录为准，未推送或创建 PR。
- **历史暂停安排（已被上方最新授权覆盖）**：收到 Actions 用量 90% 提醒后，用户明确选择「暂停新增 Actions，完成本地验收并保留待交付提交」。本轮不再 push、新建/更新 PR、合并或重跑；不改计费。恢复交付须用户调整此约束，并重新核对最新 master、准确 HEAD 与 CI，不能沿用本次旧证据直接合并。
- **在途交付**：[PR #24](https://github.com/1239890829/AI-Trading/pull/24) 保持打开，HEAD `8b155a771b5a733ca420b9add506ba14abecec7b`；既有 run `35176013949` attempt 1 的 backend/frontend/docs 三 job 均 success，只读 release-check 通过；此前因额度约束未合并；现集中追加题材保护与 IMP-041 后重验新 HEAD，旧绿色结果不复用。
- **首批交付**：[PR #23](https://github.com/1239890829/AI-Trading/pull/23) 已合并为 `d16be3c`；准确 HEAD `dc5732e` 的 CI run `35174599669` attempt 1 三 job 均 success。
  合并后 master CI run `35175576651` attempt 1 三 job 均 success；本机 master 已快进，服务仍均未启动。
  首批证据保存在原项目 `artifacts/runs/fusion-foundation-20260917/`；恢复包不动。
- **题材切片门禁**：后端 **3566 collect / 3490 passed / 76 skipped / 0 failed，190.74s**；8000/3000 停用，后端与前端全量串行，期间有只读核查与独立 SQLite 的 API 验收。
  前端本地/UTC **650 passed / 70 文件**，单 worker **44.50s / 46.06s**；tsc **0**、eslint **0/0**、pyflakes **0**、构建通过。
  基线 **3546/215 文件 → 3566/216 文件**；新增 **20** 全来自 `tests/test_theme_member_write_guard.py`，import-lint **269** 与 skipped **76** 均不变；前端源码零差异。
  doc-health **22 条索引 / 0 档位冲突**；完整 OpenAPI **170 paths / 零差异**。隔离实际路由+服务+SQLite：无凭据 401、业务失败 502 且旧成员/时间不变、有效空集 200 且正确清空。
  长证据由本批 artifacts/verification 归档到原项目 artifacts/runs/theme-write-20260917；跌停批次位于 artifacts/runs/limit-down-20260917，提交身份与校验收据随附。上游为测试夹具，未做 THS 实源或生产加载验收。
- **跌停切片门禁**：后端 **3546 collect / 3470 passed / 76 skipped / 0 failed，198.33s**；8000/3000 停用，未与前端全量并发，期间有文档与 Git 核查。
  前端本地/UTC **650 passed / 70 文件**，单 worker **44.41s / 46.82s**；tsc **0**、eslint **0/0**、pyflakes **0**、构建通过。
  基线 **3521/214 文件 → 3546/215 文件**；新增 **25** 全来自 `tests/test_limit_down_failures.py`，import-lint **269** 与 skipped **76** 均不变。
  doc-health **22 条索引 / 0 档位冲突**，完整 OpenAPI **170 paths / 零差异**；证据存本批 `artifacts/verification/`。

- **首批实施现场（历史快照）**：独立分支 `codex/fusion-foundation`，基点 `origin/master`
  `4052c80`（PR #22）；原项目开工无已跟踪改动。8000/3000 实测均无监听；本轮未启动服务。
  旧 PR #4 仍冲突并保留。恢复副本位于原项目忽略的 `artifacts/backups/`，不纳入发布。
- **本批门禁（2026-09-17，最终复验）**：后端 **3521 collect / 3445 passed / 76 skipped / 0 failed，189.42s**；
  8000/3000 均未运行、未与前端全量并发，期间有只读代码核查。前端本地与 UTC 均 **650 passed / 70 文件**，
  单 worker **47.24s / 45.19s**；tsc **0**、eslint **0/0**、pyflakes **0**、生产构建通过。
  基线 `4052c80` 两侧实测：**3453/211 文件 → 3521/214 文件**；**+68** 分别来自发布核验 **35**、
  恢复 **5**、迁移工具 **22**、既有入口守卫 **+5**、数据隔离 **+1**；import-lint 两侧均 **269**，skipped **76** 不变。
  doc-health **22 条索引 / 0 档位冲突**；完整 OpenAPI **170 paths / 零差异**；3 路真实行为注入精确判红并按哈希还原。
  证据存本批忽略的 `artifacts/verification/`；迁移恢复证据另见 §GOV-018。
- **上一轮分支记录**：`master`（最新交付 = **PR #21**，合并 commit `c0aa3ef`；功能分支
  `codex/data-source-routing-audit` 本地与远程**均已删除**）。任务见 §`IMP-040` / §`BUG-020`。
  该 PR 原自述「**待 PR #20 集成后在最新 master 重验**，不能据本分支单测通过跳过该条件」——
  该前置条件**已满足**：已把最新 `master`（含 PR #20）合入该分支并解决 4 处冲突后才合并。
  其中 `backend/tests/test_watch_ledger_kind.py` **取 master 侧的时钟钉法**：两个工作区
  对该文件的修法**同源**（都钉死到 `2026-09-12 15:00 +08:00`），但 master 侧更完整
  ——它把**到期日两侧**都写成契约用例（`test_window_cutoff_expiry_is_pinned`），故取 master 侧。
  ⚠️ **该 PR 与 master 曾发生 `BUG-018` 编号撞车**（它的「数据来源身份与失败语义」P0 项
  ⇄ 已合并的「台账归因测试**到期型**缺陷」）⇒ **该 PR 侧改号为 `BUG-020`**，
  改号范围与理由见 §`BUG-020` 末段（**不是**把已闭环项重开）。
  ⚠️ **该 PR 相对 `origin/master` 的净贡献只有 7 个文件 / +291−50**（`sina.py` / `tencent.py` /
  `ths.py` + 新增 `tests/test_provider_security_identity.py` + 3 份文档）——其余（含前端 **20 处**）
  **已在 PR #20 中合入**，故合并时那 58 个「改动面」绝大多数是 **master 侧内容**，
  **勿误判成该 PR 的改动面**（`git diff --cached origin/master --name-status` 一跑即明）。
  上一批 `codex/hunting-dynamic-and-notif-tabs`（**五批合一**：用户七问 `IMP-035` + `BUG-017`
  doc-health 跳过谓词修复 + 数据源审计批 `GOV-016`/`IMP-038` + CI 转绿两修
  `BUG-018`/`BUG-019`）已由 **PR #20** 合并（合并 commit `4590a78`），功能分支已删除；
  **合并后 master 的 CI 三 job 全绿**（合并前 master 自身是 `docs` + `backend` 双红）。
- **历史交付基线（PR #21）**：该轮交付 = **PR #21**（合并 commit `c0aa3ef`）；交付后
  `git log --oneline origin/master..HEAD | wc -l` 实测 **0**；**master 侧 CI run `35164380194`
  三 job 全 success**（`backend` / `frontend` / `docs`）。
- **历史服务记录（当前监听状态见本节开头）**：后端 8000（单实例；**绝不用 `--reload`**，原因见 `AGENTS.md` §6.1）、前端 3000。
- **门禁基线（`BUG-020` 数据源身份修复集成（PR #21）收尾实测，接手时可直接对照）**：后端
  **collect 3453（3377 passed / 76 skipped / 0 failed / 259.88s，8000 在跑）**、
  `pyflakes` **0**、`doc-health` **全部通过**（`P 交接索引` **18 条** / `Q 档位一致性` 0 冲突）、
  `tsc` **0**、`eslint` **0 error / 0 warn**、前端 **650 项 / 70 文件**（本地与 `TZ=UTC` **逐字一致**）。
  ⚠️ **较上值 3432/650·70 的 Δ = +21 collect = +21 passed / ±0 skipped**，**已机械归因**
  （两侧各自 `--collect-only` 求和，**不凭记忆**）：master 实测 **3432（210 文件）** →
  集成态 **3453（211 文件）**，**+1 文件** = 新增 `tests/test_provider_security_identity.py`
  **21 例**（全 passed），`3377 + 76 = 3453` 自洽；`tests/test_import_lint.py` 两侧实测
  **逐字相同 269 例** ⇒ 参数化面未变（[[KB-ENG-97]]：**跳过数不变 = 无覆盖丢失**）。
  前端 **±0** 且**有独立依据**：该 PR 在 `apps/web/` 下**零差异**（见上「净贡献」）⇒ 与上轮逐字一致。
  ⚠️ **`TZ=UTC` 那一项红 = 已登记的 `BUG-010`**（`markdown-view.test.tsx::docs/ 下全部 md 均可渲染完成`
  5000ms 超时），**本地与 UTC 同项同因**（各 1 项）⇒ **非时区问题、非本轮引入**；隔离复跑仍绿。
  ⚠️ **集成曾引入 1 项新 FAIL 并已消除**：`doc-health` 的 **C 超层配额**
  `docs/data-source-comparison.md(501>500)` —— **双方各自不超、合并后超 1 行**
  （基点 446 / master 449 / PR#21 498 / 合并 501）⇒ 已无损压缩该文件 `:7-9` 的「同族」块（3→2 行）
  与文末 OpenBB 句（2→1 行）至 **499 行**（留 1 行余量）。
  ⇒ **"两边各自绿"拼不出"集成绿"**；集成分支**必须重跑门禁**。
  ⚠️ **本基线块曾"写了却没进提交"**：首轮合并提交用 `git commit -F`（**不带 `-a`**），
  而 `AGENTS.md` 头行与本节回填是在 `git add -A` **之后**才编辑的 ⇒ **两处静默丢失**，
  由本条补账提交（`codex/handoff-post-merge-sync`）追回。**判据：提交前 `git diff --exit-code` 无未暂存漂移，并复核暂存正文；提交后检查任务工作区干净**
  （详见 [[KB-ENG-113]]）。
- **门禁基线（`BUG-018` + `BUG-019` CI 转绿两修 收尾实测，接手时可直接对照）**：后端
  **collect 3432（3356 passed / 76 skipped / 0 failed / 306.87s，8000 在跑、`load average` 15.95）**、
  `pyflakes` **0**、`doc-health` **全部通过**（`P 交接索引` 16 条 / `Q 档位一致性` 0 冲突）、
  `tsc` **0**、`eslint` **0 error / 0 warn**、前端 **650 项 / 70 文件**（本地与 `TZ=UTC` **逐字一致**）。
  ⚠️ **较上值 3431/650·70 的 Δ = +1 collect = +1 passed / ±0 skipped**，**已机械归因**：
  **+1** = `tests/test_watch_ledger_kind.py` 新增 `test_window_cutoff_expiry_is_pinned`
  （`BUG-018` 的**到期日两侧**契约用例）· `test_import_lint.py` 参数化面**未变**（无新增 `.py`
  ⇒ 非业务层模块数未变 ⇒ `skipped 76` 不变**自洽**，[[KB-ENG-97]]）·
  `tests/test_provider_budget.py`（`BUG-019`）改的是**常量与 docstring**、**用例数不变**
  ⇒ 不产生 collect 增量。前端 **±0**：`apps/web/` 本轮**零改动**（`git status` 核对无差异）。
  ⚠️ **报耗时必须带负载前提**：306.87s 是 `load average 15.95` 下实测（同机另有应用抢占 CPU）。
  ⚠️ **`TZ=UTC` 全量 = 650 项 / 70 文件（与本地逐字一致），其中 1 项红 = 已登记的 `BUG-010`**：
  全量并发下该用例实测 **8716ms / 5305ms**（本地两次）、`TZ=UTC` **6655ms**，预算固定 5000ms；
  **隔离复跑 10/10 全绿、该用例仅 1407ms** ⇒ **由宿主负载造成、非回归**。
  ⚠️ **但 `BUG-010` 的余量已被吃光**（本轮 `docs/` 净增约 150 行，而它渲染的正是 `docs/**` 全部 md）
  ⇒ 建议给该用例单独提高 `timeout` 或**按文件粒度断言**（**未擅自改**，属改守卫，留待拍板）。
  ⚠️ **本轮 3 项真红全部由「全量门禁」而非单跑抓出**：① `BUG-018` ② 本轮**自己**造的 KB 索引
  格式回归（`docs/kb/00-INDEX.md` 日期栏写成 `2026-09-12 / 09-17`，而解析器 `_INDEX_ROW_RE`
  要求**严格单日期** ⇒ 该行落进 `unparsed_rows`，`test_kb_routing.py` 两例判红）③ `BUG-019`。
  ⇒ **"我只改了 X"不构成不跑全量的理由**（本轮"只改了测试与文档"，仍红 3 项）。
  ⚠️ **门禁数字的权威位仍是本 §1**；`AGENTS.md` 头行须与它同轮同步——本轮已把该头行
  **去历史化**（只留当前口径 + 本轮 Δ + 常驻前提），历史各轮 Δ 归因以本 §1 为准。
- **门禁基线（`IMP-038` 逐笔 TDX 降级备源 收尾实测，接手时可直接对照）**：后端 **collect 3431
  （3355 passed / 76 skipped / 0 failed / 347.29s，8000 在跑）**、`pyflakes` **0**、
  `doc-health` **全部通过**（`P 交接索引` 14 条 / `Q 档位一致性` 0 冲突）、`tsc` **0**、
  `eslint` **0 error / 0 warn**、前端 **650 项 / 70 文件**（本地实测）。
  ⚠️ **较上值 3373/643·69 的 Δ = +58 collect = +58 passed / ±0 skipped**，**已机械归因且逐字对上**：
  **+55** = 新文件 `tests/test_tdx_tick.py`（五组语义判据：时间戳/方向/分页/降级链/**故障分类**）·
  **+1** = `tests/test_depth_tools.py` 新增 `test_trades_chain_failure_reports_reason_not_silent_empty`
  （11→12）· **+1** = `tests/test_import_lint.py` 因新增**业务层**模块 `app/market/tdx_tick.py`
  （268→269；**非业务层模块数未变** ⇒ `skipped 76` 不变**自洽**，[[KB-ENG-97]]）·
  **+1** = `tests/test_event_loop_no_block.py` 的 `GUARDED` **参数化**新增 `tdx_tick` 条目。
  ⇒ 55+1+1+1 = **58** ✓。前端 **+7 项 / +1 文件**，**恰等于**新文件
  `components/detail/book-trades-view.test.tsx` 的用例数（7）；`lib/format.test.ts` 只加断言、**不加用例**。
  ⚠️ **一次偶发红（已由复跑证伪）**：文档改动后的首轮复跑（load average **13.50**、
  耗时 **573.61s**）出现 **1 failed = `tests/test_api.py::test_paper_fills_and_reset`**
  （`sqlalchemy.exc.Invalid…`）；**隔离复跑 1 passed / 2.31s、整文件 13 passed / 9.33s**，
  且**同一提交树的再一轮全量复跑为 3355 passed / 0 failed（651.03s）** ⇒ 判为
  **共享状态（module 级 `client` fixture + SQLite）偶发**，**非本轮回归**（与 `BUG-017` 轮
  记录的 `9364ef0` 同族）。⚠️ 该红的**完整栈未取到**——按实标注为**未归因**；
  若再现请优先怀疑 `tests/test_api.py` docstring 所述「用例间共享同一份进程状态」。
  ⚠️ **耗时口径**：347.29s 是**低载**实测；651.03s 是**高载**（load 13.5）实测 ——
  **报耗时必须带负载前提**，否则会被当成回归。
  ⚠️ **本轮 3 次真红全部由「全量门禁」而非单跑抓出**：① `test_depth_tools.py` 两条
  （**测试触网 ⇒ 假绿形态**，见 §IMP-038）· ② `test_trades_empty_says_unavailable`
  （判据写错：`detail` 非空 ≠ 故障）· ③ `test_env_docs.py::test_env_example_covers_every_setting`
  （新增配置项未同步 `.env.example`）。**"我只改了 X" 不构成不跑全量的理由。**
  ⚠️ **`TZ=UTC` 全量 = 650 项 / 70 文件，其中 1 项红**，红点是**已登记的 `BUG-010`**
  （`components/agent/markdown-view.test.tsx::docs/ 下全部 md 均可渲染完成` 超时 5000ms）
  —— **已定性为负载抖动、非回归**（证据见下），其余 **649 passed**。
  即**项数/文件数与本地逐字一致**（650 / 70）。
  ⚠️ **本轮 `TZ=UTC` 取证过程本身不可复用于"次数"判断**：前两次尝试出现
  **5 个文件加载错误（65/70 文件 / 587 项）**与**1 failed + 6 errors（64 文件）**，
  形态均为 `[vitest-pool]: Failed to start forks worker … Timeout waiting for worker to respond`
  —— **宿主负载**所致：`uptime` 实测 **load average 13.50**，同一套用例本地 **249s** /
  UTC **471s / 1153s / 1395s**（正常 ~250s）。**报"前端数字"时必须带这个前提。**
  ⚠️ **`BUG-010` 的定性证据（本轮补强，值得记）**：该用例**渲染 `docs/**` 下全部 md**，
  预算固定在 **5000ms**。实测——
  `--testTimeout=60000` 隔离复跑 **10/10 全绿**，该用例实测 **3403ms**（≈ 预算的 68%）。
  ⇒ ① 它不是回归（放宽预算即绿）；② 但**余量只剩 1.47×**，而 `docs/` 只增不减
  （上一轮记录整文件 1.6~2.8s，本轮该用例单项已 3.4s）⇒ **这是一条正在被文档增长吃掉的预算**。
  建议（**未擅自改**，属改守卫）：给该用例单独提高 `timeout`，或按文件粒度断言而非整目录一次渲染。
  ⚠️ 本轮改动文件在 `TZ=UTC` 下**定向复跑 52/52 全绿**
  （`lib/format.test.ts` 45 + `components/detail/book-trades-view.test.tsx` 7）；
  且新用例**刻意不断言渲染出的时间字符串**（`timeText()` 按宿主时区渲染），
  时间戳口径由后端 `test_row_to_trade_uses_true_utc_not_pseudo_utc` 钉住 ⇒ UTC 面风险本就低。
  ⚠️ **门禁数字的权威位仍是本 §1**；`AGENTS.md` 头行须与它同轮同步。
- **门禁基线（`GOV-016` docstring 漂移修正 收尾实测，接手时可直接对照）**：后端 **collect 3373
  （3297 passed / 76 skipped / 0 failed / 252.32s，8000 在跑）**、`pyflakes` **0**、
  `doc-health` **全部通过**（`P 交接索引` 13 条 / `Q 档位一致性` 0 冲突）、
  前端 **643 项 / 69 文件**（本地与 `TZ=UTC` **逐字一致**；`apps/web/` 本轮**零改动**）。
  ⚠️ **较上值 3372/643·69 的 Δ = +1 collect = +1 passed / ±0 skipped**，**已机械归因**：
  恰等于 `tests/test_provider_capabilities.py` 新增的 1 例守卫
  `test_ths_docstring_unimplemented_endpoints_stay_unwired`；**本轮无新增/删除后端模块**
  ⇒ `tests/test_import_lint.py` 不变、`skipped 76` 不变**自洽**（[[KB-ENG-97]]：跳过数不变 = 无覆盖丢失）。
  ⚠️ **前端那两次数值是在「后端全量已跑完」之后取的**（两道全量**未并发**）⇒ 本轮**未**出现 `BUG-010` 抖动。
  详见 `§GOV-016`。
- **门禁基线（`BUG-017` doc-health 跳过谓词修复 收尾实测，接手时可直接对照）**：后端 **collect 3372
  （3296 passed / 76 skipped / 0 failed / 416.23s，8000 在跑）**、`pyflakes` **0**、
  `doc-health` **全部通过**、`tsc` **0**、`eslint` **0 error / 0 warn**、
  前端 **643 项 / 69 文件**（本地与 `TZ=UTC` **逐字一致**；`apps/web/` 本轮**零改动**）。
  ⚠️ **耗时前提**：416.23s 高于 `IMP-035` 轮的 301.48s —— 本轮跑后端全量时**同时在改文档**
  （非 CPU 密集，但共享 SQLite/网络）⇒ **不是回归**；要横向对标耗时应空载复跑。
  ⚠️ **较上值 3366/643·69 的 Δ 全部机械归因**：后端 **+6 collect = +6 passed / ±0 skipped**
  = 新钉子 `backend/tests/test_doc_health_memory_index.py`（实测 **14 → 20** 例，`+6`）；
  **本轮无新增/删除后端模块** ⇒ `tests/test_import_lint.py` 不变、`skipped 76` 不变**自洽**
  （[[KB-ENG-97]]：跳过数不变 = 无覆盖丢失）。前端 **±0**（`apps/web/` 零改动，`git status` 核对无差异）。
  ⚠️ **master 上另有一条与本轮无关的红**：`9364ef0`（PR #19 合并）的 CI **`backend` job 也红** ——
  两项 `tests/test_notification_read_state.py::test_put_read_state_roundtrip_and_monotonic`
  （`assert 0 == 9999999999999`）与 `tests/test_risk.py::test_risk_check_order_api`
  （`sqlalchemy.exc.InvalidRequestError: Could not refresh instance '<PaperAccount …>'`），
  属在册的「SQLite / 共享状态偶发族」（本仓用 `pytest-randomly` ⇒ 顺序随种子变）；
  而**同一提交树（`ab06193` ⊃ `9364ef0`）本轮实测 `backend` job 为 success** ⇒ 判为**偶发，非本轮引入**。
  详见 `§BUG-017`。
- **门禁基线（`IMP-035` 用户七问交付 收尾实测，接手时可直接对照）**：后端 **collect 3366
  （3290 passed / 76 skipped / 0 failed / 301.48s，8000 在跑且**无并发负载**）**、`pyflakes` **0**、
  `doc-health` **全部通过**、`tsc` **0**、`eslint` **0 error / 0 warn**、
  前端 **643 项 / 69 文件**（本地与 `TZ=UTC` **逐字一致**）。
  ⚠️ **报数必须带前提，否则会被当成回归**：前端那两次数值是在**解除后端并发**的前提下取的
  （`--maxWorkers=2`，或后端全量已跑完）。**与后端全量并发跑时默认并行度偶发 1 项红** ——
  `components/agent/markdown-view.test.tsx::docs/ 下全部 md 均可渲染完成` 超时 5240ms（>5s 默认）。
  该项**是已登记的 `BUG-010`**（D 档观察项，判据挂在墙钟上，与 `BUG-008` 同族），
  账本原文已写明「与后端全量并发跑时 4 轮中 3 轮红、解除并发后 ×3 全绿」，本轮**第三次独立复现同一触发条件**；
  隔离复跑 10 项全绿、整文件仅 1.6~2.8s ⇒ **非本轮引入**（本轮只改前端配色字典与契约清单）。
  ⚠️ **较上值 3339/608·67 的 Δ 全部机械归因**（不写"涨了"就完事）：
  · 后端 **+27 collect = +27 passed / ±0 skipped** = `+10`（新 `tests/test_picks_live_gate.py`）
    + `+7`（新 `tests/test_llm_model_single_source.py`）+ `+7`（`tests/test_notifications.py` 实测 13→20）
    + `+3`（`tests/test_sentiment.py` 实测 25→28，基线用**干净检出** worktree 实测）。
    实测 `tests/test_import_lint.py` 前后**均为 268（194 passed / 74 skipped）** ⇒ 本轮**无新增后端模块**，
    `skipped 76` 不变**自洽**（[[KB-ENG-97]]：跳过数不变 = 无覆盖丢失）。
  · 前端 **+35 项 / +2 文件** = `+18`（新 `apps/web/lib/picks-gate.test.ts`）
    + `+6`（新 `apps/web/components/notifications/notification-event-tab.test.tsx`）
    + `+11`（`pick-card.test.tsx` +6 / `notification-drawer.test.tsx` +4 / `notification-row-landing.test.tsx` +1，
    由 `git diff` 逐文件计 `it(`/`test(` 净增数得出）。
  ⚠️ **门禁数字的权威位是本文件 §1；`AGENTS.md` §1 的门禁头行必须与本文件同轮同步**（本轮已同步）。
  详见 `§IMP-035`。
- **门禁基线（`RSH-003` 切片 2 收尾实测）**：后端 **collect 3339
  （3263 passed / 76 skipped / 0 failed / 240.74s；收尾复跑 237.56s，均在 8000 在跑时）**、`pyflakes` **0**、
  `doc-health` **全部通过**、`tsc` **0**、`eslint` **0 error / 0 warn**、
  前端 **608 项 / 67 文件**（本地与 `TZ=UTC` **逐字一致**；`apps/web/` 本轮**零改动**）。
  ⚠️ **较上值 3306/3230 的 Δ = +33 collect = +33 passed / ±0 skipped**，**已机械归因**（非覆盖率虚增）：
  `+32` = 新文件 `tests/test_smoothing.py`（32 例，**全 passed**）；`+1` = `test_import_lint.py`
  分层参数化新增一个**业务层**模块 `app/factors/smoothing.py`（实测用例名
  `test_business_layer_never_imports_api[-factors/smoothing.py]`）。**非业务层模块数未变**
  ⇒ 实测 `tests/test_import_lint.py` = **194 passed / 74 skipped**、`74 = 76 − 2` 与上轮**逐字相同**
  （[[KB-ENG-97]]）。详见 `§RSH-003`。
  ⚠️ **门禁数字的权威位是本文件 §1；`AGENTS.md` §1 的门禁头行必须与本文件同轮同步**
  —— 本轮已同步（上一轮曾发现它**落后两轮**、仍写 3247/603·67）。
- **门禁基线（上一轮 `RSH-003` 切片 1 收尾实测）**：后端 **collect 3306
  （3230 passed / 76 skipped / 0 failed / 236.05s，8000 在跑）**、`pyflakes` **0**、
  `doc-health` **全部通过**、`tsc` **0**、`eslint` **0 error / 0 warn**、
  前端 **608 项 / 67 文件**（本地与 `TZ=UTC` **逐字一致**；`apps/web/` 本轮零改动）。
  ⚠️ **较上值 3287/3211 的 Δ = +19 collect = +19 passed / ±0 skipped**，**已机械归因**（非覆盖率虚增）：
  `+18` = 新文件 `tests/test_factor_novelty.py`；`+1` = `test_import_lint.py` 分层参数化新增
  一个**业务层**模块 `app/factors/novelty.py`（实测用例名
  `test_business_layer_never_imports_api[-factors/novelty.py]`）。**非业务层模块数未变**
  ⇒ 实测 `tests/test_import_lint.py` = **193 passed / 74 skipped**、`74 = 76 − 2` 与上轮**逐字相同**
  （[[KB-ENG-97]]）。详见 `§RSH-003`。
  ⚠️ **门禁数字的权威位是本文件 §1；`AGENTS.md` §1 的门禁头行必须与本文件同轮同步**
  —— 本轮发现 `AGENTS.md` 头行**落后两轮**（仍写 3247/603·67），已一并订正。
- **门禁基线（上一轮 `BUG-016` 子项③ 收尾实测）**：后端 **collect 3287
  （3211 passed / 76 skipped / 0 failed）**、`pyflakes` **0**、`doc-health` **全部通过**、
  前端 **608 项 / 67 文件**（本地与 `TZ=UTC` **一致** ⇒ 该轮用例不含时区敏感断言）。
  ⚠️ **较上值 3265/3189 的 Δ = +22 collect = +22 passed / ±0 skipped**，**已机械归因**（非覆盖率虚增）：
  `+17` = 新文件 `tests/test_notification_diagnostics.py`；`+4` = `tests/test_notifications.py` 的
  **端点接线守卫**（空态真调用并挂上 / 非空态**刻意不调用** / 抛错显式降级 / 两分支不变式）；
  `+1` = `test_import_lint.py` 分层参数化新增一个**业务层**模块 `app/picks/notification_diagnostics.py`。
  **非业务层模块数未变** ⇒ `skipped(76) − 2 = 74` 与上轮**逐字相同**
  （[[KB-ENG-97]]：跳过数不变 = 无覆盖丢失）。前端 Δ **+5 用例 / ±0 文件** =
  同一文件内新增的空态诊断 5 例。详见 `§BUG-016`。
  ⚠️ **较上值 3245/3169 的 Δ = +20 collect = +20 passed / ±0 skipped**，**已机械归因**（非覆盖率虚增）：
  `+18` = 新文件 `tests/test_kb_routing.py`；`+1` = 新 GET 端点 `/api/picks/kb-routing` 自动进入
  全量冒烟参数化（实测 `test_get_endpoint_never_returns_500[/api/picks/kb-routing-get-params91]`）；
  `+1` = `test_import_lint.py` 的分层参数化新增一个**业务层**模块 `app/picks/kb_routing.py`
  —— **在 HEAD 干净检出实测该文件为 190/74，本轮为 191/74**，逐字对上。
  **非业务层模块数未变**（`models/opportunity_learning.py` 是**既有文件改动**、非新增文件）
  ⇒ `skipped(76) − 2 = 74` 与上轮**逐字相同**（[[KB-ENG-97]]：跳过数不变 = 无覆盖丢失）。
  详见 `§RSH-027`。
  ⚠️ **前端那 1 failed 是 `BUG-010`（D 档观察项），不是回归**：用例 =
  `components/agent/markdown-view.test.tsx::docs/ 下全部 md 均可渲染完成`（固定 5s 墙钟）。
  上轮**归因（对照跑，非推理）**：单独跑该文件 **10 passed / 2170ms**（docs 渲染用例 **1897ms**），
  失败只出现在**全量并发**时；宿主实测 **load 39.16 / 8 核**。按原判**不改判据、不放宽阈值**
  （详细归因史见 `BUG-010` 行与 `AGENTS.md` 门禁段）。
  ✅ **本轮（`BUG-016` 子项③）未复现该超时**：前端全量连跑两次（本地 + `TZ=UTC`）均 **608/67 全绿**
  ⇒ 与「红只出现在高负载并发时」的既有归因一致；**仍不改判据、不放宽阈值**。
  ⚠️ **两道全量不要并发跑**：并发会因 CPU 竞争让上述用例超时假红——**它的红不代表代码回归**。
  **本地时区与 `TZ=UTC` 逐字一致（608/67）** ⇒ 本轮用例**不含时区敏感断言**。
  ⚠️ **后端全量耗时强依赖「8000 是否在跑」**：本轮 8000 在跑，实测 **253.61s**（4m14s）。
- **⚠️ 两个「门禁输入面 ≠ 代码面」的实例（`IMP-034` 轮新增，务必记住）**：
  ① **文档改动会让后端 job 变红**——后端有一类守卫**以 `docs/` 为输入**（`test_doc_*` /
  `test_cmd_guidance_guard` / `test_doc_status_truthfulness`）⇒ **「本轮没改后端代码」不构成「不用跑后端门禁」**；
  ② 反向：`docs/` 里写**未跟踪文件名**会让 **CI docs job** 红而**本地恒绿**（[[KB-ENG-95]]）。
  ⇒ **收尾一律跑全量后端 + 干净检出复验**，不要按改动面裁剪。（[[KB-ENG-102]]）
- **⚠️ 第三个实例（`RSH-027` 轮实测，「先 `git add` 再跑门禁」再次应验）**：本轮把新文件
  （`picks/kb_routing.py` / `tests/test_kb_routing.py` / 迁移）写进 `docs/handoff.md` 后跑体检
  ⇒ **`doc-health` J 文档代码锚点判红 4 处**（`kb_routing.py（全仓不存在）` 等），真因 = **文件尚是 `??` 未跟踪**，
  而 J 的判定面取自 **`git ls-files`**。**修法 = `git add`**（不是改文案、不是登记豁免）；
  入库后 J 转绿。⚠️ 与 [[KB-ENG-95]] 方向相反、根因相同。
- **⚠️ 诊断轮特有的口径提醒（`BUG-016` 轮新增）**：`data/ashare.db` 里的学习类表
  （`opportunity_decision_snapshot` / `opportunity_outcome_label`）**今日才由 `RSH-026` 迁移建立**
  （PR #13 于 **09:58** 合并、PR #14 于 **10:20** 合并）⇒ **不能用「表为空」反推「循环没跑」**：
  运行中的后端无 `--reload`，未必带该段代码。**判「链路是否在跑」要看事件面
  （`alert_event` 的 `triggered_at` 时间线）**，不要看新表的行数。
- **工作区现场**：`git status --porcelain` 为空。接手偏差修复涉及的跟踪文件已随 PR #19 交付；
  后端全量门禁期间产生的当日 LHB 数据经历史惯例、JSON 格式与敏感字段核验后，按已入库系列的新成员一并提交。
  ⚠️ **处置判据（今后遇到 `??` 一律照此，**勿凭印象**）**：判据是「**同系列历史文件的跟踪状态**」，
  一条 `git log --diff-filter=A -- <同目录>` 即可查清 ——
  ① 有入库记录 ⇒ 属**已入库系列的新成员** ⇒ 按同惯例提交（`chore(data)` / `chore(evolution)`）；
  ② 无记录且可再生产 ⇒ 补 `.gitignore` 规则（按实际落盘路径写），**不删文件**；
  ③ 命中 `api_key` / `token` / `password` 等 ⇒ **绝不入库**（红线 4）。
  ⚠️ **本条纠正了一个已发生的误判（勿重蹈）**：上述目录下的文件曾被登记为
  「运行期产物、尚未纳入版本控制」「不得提交」⇒ 直接后果是**工作区连续多轮不干净**
  （违反用户 2026-09-16 明确的「交接的时候工作区必须是要干净的」）。
  实测 `git log --diff-filter=A` 显示**同目录同命名模式的历史文件全部已跟踪**，
  且以专门提交进主分支（`985f486` / `dbadeef` / `1064898`）⇒ **系误判**，已按惯例入库。
  **纪律：「看起来像运行产物」不构成「不该入库」的证据。**
  ⚠️ 本节仍**刻意不写具体文件名（不是省略，是纪律）**：新增的 `docs/` 路径若在被 `git add`
  之前就写进本文，B 项判定面（`git ls-files`）会立即把它当死链 ⇒ **CI docs job 红而本地恒绿**。
  2026-09-16 实测踩到：本条原先写死两个 `docs/` 下的未跟踪文件名 ⇒ **CI `B 死链 2 处`**。
  边界：B 项**只认 `docs/` 面的引用** ⇒ 写未跟踪的 docs 路径**必红**、非 docs 路径**不红**
  （所以"本地没红"证明不了什么）。**修法是改文案，不是登记豁免**（豁免会让下一次换个文件重演）。
- **交付状态（2026-09-16）**：PR #18 的上一轮 11 个提交与 PR #19 的接手审阅偏差修复均已进入
  `origin/master`；本地与远程功能分支均在交付后删除，不存在只留在本地的提交。
  ⚠️ **收尾硬判据（交接前必跑，两条都要过）**：
  `git status --porcelain` 为空 **且** `git log --oneline origin/master..HEAD | wc -l` = 0。
  **本地 commit ≠ 已交付**——只要没合并，本文描述的东西在远端 `master` 上就不存在，
  接手者按本文去找必然对不上。**常规 PR 合并属授权内动作，不是"停下来等用户"的理由。**

## 2 条目一览（与账本 §6.0-H 逐字对应）

| 任务 ID | 状态 | 日期 | 一句话 |
|---|---|---|---|
| `IMP-041` | 🟡 实施中 | 2026-09-17 | 后端测试夹具装载与 CI 分钟治理 |
| `BUG-021` | ✅ 闭环 | 2026-09-17 | 持仓计划测试写入隔离 |
| `GOV-012` | 🟡 客户端已实现 · 平台待批 | 2026-09-17 | 精确发布证据与平台边界 |
| `GOV-017` | ✅ 闭环 | 2026-09-17 | 发布与提交判据纠偏 |
| `GOV-018` | 🟡 实施中 | 2026-09-17 | 平台目录退出：清单、恢复与分用途迁移 |
| `BUG-019` | ✅ 闭环 | 2026-09-17 | 预算共享判据的**「判别窗口 ≈ 噪声」**⇒ **真偶发**（与 `BUG-018` 的到期型**定性相反**）：残值 `0.4s` vs 整份 `0.5s` 只差 `0.1s`，而 `granted` 量**墙钟实际耗时**、抖动 `0.08~0.12s` ⇒ 判别力被淹没。**两份 diff 均为空** ⇒ 非本轮引入，是 `BUG-007` 的残余。修法**只拉大两端距离**（预算 `0.5→1.0`、慢源吃 `0.1→0.6`、阈值 `0.95→0.8×`，分离度 **1.25×→2.5×**）；注入自证 **1/1** 报红、还原 `sha256` 逐字一致；修后 **20/20 全绿** |
| `BUG-018` | ✅ 闭环 | 2026-09-17 | 台账归因测试的**到期型**缺陷：夹具日期写死 `2026-09-12`，而 `tracking_review_stats(5)` 的 `cutoff` **相对运行日** ⇒ **到期日 = 09-16**。CI **重跑跨过北京午夜（= UTC 16:00）** 后判红，且**失败项与首跑不同** ⇒ 不可套用「重跑即绿」。**只翻时钟、代码零改动**即复现（证伪实验）；修法 = **同源钉死时钟** + 把**到期日两侧**都断言的契约用例（注入自证 **2/2**）。同族 4 候选**全部定性**，无第二颗炸弹 |
| `IMP-038` | ✅ 闭环 | 2026-09-16 | 逐笔成交接入 **TDX 降级备源**（原登记「东财通而空」实为**完全不可用**：3/3 WAF 快速失败、端点恒 502；链上另 3 源全是 `return []` 占位 ⇒ 单点归零）。新增 `app/market/tdx_tick.py`（手动分页 + 长连接单例 + `bs_flag` 与东财**相反**的映射）+ 两个消费方共用降级链；**三向交叉验证** Σvol 26,243 手 ↔ fuyao 日线 26,235.24 手 + 盘后 8 笔；**注入自证 7/7（后端）+ 1/1（前端）**、**UI 实测**（口径行 / 北京时区 / 量纲）⇒ 顺带修掉量列**恒渲染 0** 的真实缺陷；收尾修正「**没数据 ≠ 取数失败**」判据（`trades_failure_detail` 白名单 ⇒ 路由只在**真故障**时 502） |
| `GOV-016` | ✅ 闭环 | 2026-09-16 | `ths.py` docstring 把**未实现**能力（跌停池 / 财务三表 / 估值 / 全市场导出）列为已具备 ⇒ 读者以为已接入。能力清单重构为「已实现 / 未接入 / 已用但不在本 Provider」三段（含 7 条**精确**端点路径）+ **+1 例**双向守卫（「未接入」栏 ⇄ 生产代码零命中），**注入自证 4/4 报红** |
| `BUG-017` | ✅ 闭环 | 2026-09-16 | **`doc-health` 跳过谓词的目录形态锚点少算一层** ⇒ PR #19 强提交 `.workbuddy/skills/*` 后 `.workbuddy` 首次进入判定面、门② 却因"父目录在"放行 ⇒ **CI `docs` job 恒红（本地恒绿）**。改为三级判据（锚点随形态变 + `git check-ignore` 定夺"能不能进检出"）⇒ 干净检出 `rc 1→0` |
| `IMP-035` | ✅ 闭环 | 2026-09-16 | 用户七问交付：通知中心新增「资讯 / 事件」tab（与盘面页同源、浏览面不计未读）· 判读模型写死 `deepseek-v4-flash` · 重跑进化 · 盘中提醒**按标的去重** · **猎场闸门读时重算**（降级三态）· `KB-STOCK-37`；同轮补齐 `LEVEL_STYLE` 缺失的 `L3` 键（契约守卫抓出） |
| `RSH-003` | 🟡 部分闭环 | 2026-09-16 | 候选因子「入池前结构新颖性筛查」切片交付（三判据 + CLI + 18 例）；**四候选真实库实测**：`willr20`/`cmo20` 论证重复（+1.000）· `kurt20` 提示冗余（0.765）· `skew20` 无冗余证据（0.523）；同轮修掉**薄样本配对劫持择优结论**与**CLI 参数被静默丢弃**两处缺陷。**切片 2（同日）**：补指数平滑原语（EMA/Wilder，含「成对权重」陷阱守卫）+ 实测 `ppo20`/`adx14` —— `ppo20` 提示冗余（+0.779）· `adx14` 无冗余证据（+0.309）；抓出**窗口函数在 `WHERE` 后求值**与**名次列 `FILTER` 形同虚设**两处缺陷 |
| `RSH-027` | 🟡 部分闭环 | 2026-09-16 | 场景化 KB 路由（四场景逐行对应蓝图 §5）+ 引用三态快照 + 覆盖度恒等式；同轮修掉议程第八路**静默漏 16/178 条**的偏差 |
| `IMP-034` | ✅ 闭环 | 2026-09-16 | 提醒链路过期断言**实为 10 处**（非登记的 5 处），逐处按现实改写；`notifications` 三个死函数连同其用例删除 |
| `BUG-016` | 🟡 已取证 · 子项③已交付 · ①/②待拍板 | 2026-09-16 | 空态根因链已量化；**子项③「让为什么空可见」已交付**（四态 `diagnostics` + 前端空态渲染 + 5 路注入自证）；档位门 ①/② 仍待拍板 |
| `IMP-033` | ✅ 闭环 | 2026-09-16 | 通知抽屉行体改为**开该股详情**（与悬浮球 / 猎场同落点），判读全文改由新增「判读」入口保全 |
| `IMP-031` | ✅ 闭环 | 2026-09-16 | AI 判读气泡点开**就地打开该股详情弹窗**（不再跳告警页）；同轮梳理出提醒链路断点清单 |
| `GOV-015` | ✅ 闭环 | 2026-09-16 | 账本「未完成档 ⇄ 闭环记录」一致性守卫（`doc-health` Q 项）；顺带把滞留 A 档的 `BUG-014` 销账 |
| `GOV-014` | ✅ 闭环 | 2026-09-16 | 建立交接明细层与双向索引守卫；注入自证抓出并修掉守卫的两处判据盲区，流程已固化为技能 |
| `RSH-026` | 🟡 部分闭环 | 2026-09-16 | 个股机会学习闭环第一批已交付，并完成独立验收轮（抓出并修掉 1 处 schema 分叉） |
| `BUG-014` | ✅ 闭环 | 2026-09-16 | 两处迁移把表建到默认库 ⇒ 全新库缺 5 张表；已改 `op.get_bind()` 并加两条守卫 |

## BUG-019 预算共享判据的「判别窗口 ≈ 噪声」⇒ 真偶发（闭环）
- **账本**：`docs/retro-and-gaps.md` §6.0 `BUG-019` ｜ **日期**：2026-09-17 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：本分支后端**全量门禁**抓到第 3 项红 =
  `tests/test_provider_budget.py::test_remaining_budget_is_shared_across_sources`
  （另两项 = `BUG-018` + 本轮自造的 KB 索引格式回归，均已修）。
  验收标准 = **先定性**（本 PR 真回归 / 已登记偶发 / 新缺陷），再决定修不修、以及能否满足 §6.5 条件 3。
- **结论先行**：**真偶发**（纯随机、不随时间恶化）——与同日 `BUG-018` 的**到期型**（到点起永久红）
  **定性相反**。**判据本身有缺陷**（判别力被噪声淹没）⇒ **修判据**，
  且修法是**拉大两端距离**而非放宽阈值。
- **根因（可量化）**：该用例判「预算是**总量**而非每源配额」，依据是 `Stub.granted_seconds`
  记下的「每次调用实际被给到的时长」。但 `granted` 量的是**墙钟实际耗时**
  （`Stub._run` 的 `finally` 在 `wait_for` 取消时也记）⇒ 抖动实测 **0.08~0.12s**；
  而原参数（预算 `0.5s` / 慢源吃 `0.1s`）下**残值 `0.4` 与整份 `0.5` 只差 `0.1s`**
  ⇒ **判别窗口 < 抖动**。`BUG-007`（2026-09-15）当年给的「5× 余量」保护的是
  `elapsed < budget*2`（**总耗时**），**不是**这条判据的判别窗口 —— **余量给错了对象**。
- **归属判定（机械）**：`git diff origin/master..HEAD` 与工作区 diff 对
  `app/data_providers/composite.py` / `tests/test_provider_budget.py` **两份均为空**
  ⇒ **非本轮引入**，是 `BUG-007` 的**残余**。
- **取证**：全量那轮 `s1 被给到 0.5186s（预算 0.50s）`；隔离复跑 **20 轮红 1 轮**
  （`0.480s`，紧贴阈值 `0.475`）⇒ 合计 **25 轮 2 红 ≈ 8%**。
- **改动**：`backend/tests/test_provider_budget.py` —— 预算 `0.5→1.0s`、慢源吃掉 `0.1→0.6s`
  （残值 `0.4` vs 整份 `1.0`，**分离度 1.25×→2.5×**）、阈值 `0.95×→0.8×预算`
  （健康侧实测上限 `≤0.55`、缺陷侧 `≈1.0`，**两侧各留 ≥0.25s** = 实测抖动的 2 倍以上），
  并在 docstring 写明**「余量属于哪两个量」**。
  **刻意不改**：生产代码（`composite.py` 一行未改）· 判别逻辑（仍只看 `granted` 与预算的关系）·
  `elapsed < budget*2` 这个宽松上界（收紧它虽能多一个检测器，但会自造新偶发）。
- **注入自证 1/1**：把「每源各发一份完整预算」注回 `_call_serial`
  （`_attempt(..., time.monotonic() + self.budget_for(method))`）⇒ 报
  `s1 被给到 1.00s（预算 1.00s）`、**判据变红**；还原后 `sha256` **逐字一致**（`b30396fd…`）。
- **机械证据**：修后**隔离 20/20 全绿**（修前 20 轮 1 红）；`tests/test_kb_routing.py` **18 passed**
  （KB 索引行的格式回归修后——该回归由**本轮自己**引入，见下条「附带发现」）。
- **附带发现（同轮自造并自修，记账以示闭环）**：`docs/kb/00-INDEX.md` 的 `KB-ENG-56` 行日期栏写成
  `2026-09-12 / 09-17`，而解析器 `_INDEX_ROW_RE` 要求日期栏是**严格单日期**（`\d{4}-\d{2}-\d{2}`）
  ⇒ 该行落进 `unparsed_rows`，`test_kb_routing.py` 两例判红。**修法 = 日期栏留单值**，
  形态二的日期改写在摘要里。⚠️ **教训**：解析器的「列语义」也是判据，
  **手写表格行前先读解析器**（本例的守卫原本只防"静默漏行"，正好抓住了我的手写）。
- **门禁**：见 §1 门禁基线（同轮实测）。
- **遗留与下一步**：① 同族纪律已入 `KB-ENG-94` **形态二**（新增可迁移判据
  **「判别窗口 / 实测抖动 ≥ 5」**、并强调必须写明"余量是哪两个量之间的"）；
  ② **`BUG-010` 同族但未修**：前端 `markdown-view.test.tsx` 渲染全部 `docs/**` md、预算固定 `5000ms`，
  实测 **3403ms**（余量 **1.47×**）而 `docs/` 只增不减 —— 属**真实压力增长**而非噪声，
  修法（提高 timeout / 改按文件粒度断言）涉及判据放宽，**留给用户拍板**。

## BUG-018 台账归因测试的到期型缺陷：夹具日期写死 × 窗口相对运行日 ⇒ CI 重跑跨午夜判红（闭环）
- **账本**：`docs/retro-and-gaps.md` §6.0 `BUG-018` ｜ **日期**：2026-09-17 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：PR #20 的 CI `backend` job **重跑**失败，失败项 = `tests/test_watch_ledger_kind.py::test_by_kind_aggregation - KeyError: '时事/消息'`。验收标准 = **查清它属「本 PR 真回归 / 另一已登记偶发 / 新缺陷」三者中的哪一个**，并按 §6.5 决定本 PR 能否合并。
- **结论先行**：**三者都不是「偶发」**——它是**新缺陷**，且是**到期型**（不是随机的）：**到某一天起永久变红**。**必须修掉**，否则 CI 恒红、§6.5 条件 3 无从满足。
- **根因（可算，不是推测）**：
  - `app/picks/watch_ledger.py:215` ⇒ `cutoff = beijing_now().date() - timedelta(days=days - 1)`——**相对运行日**；
  - 用例夹具日期写死 `today = "2026-09-12"` ⇒ **到期日 = 09-12 + (5−1) = 2026-09-16**（最后有效日）。
  - ⚠️ **北京午夜 = UTC 16:00**，CI 跑 UTC ⇒ 这条边界**跨在单次 run 内部**：
    - 首跑 `35090781899`：北京 09-16 19:31 启动 ⇒ cutoff `09-12` ⇒ 夹具在窗内 ⇒ **该用例绿**（它的失败项是已登记的 `BUG-012`）；
    - 重跑 `35114959478`：后端 job `15:35:13Z` 启动、断言落在 **`16:21:06Z` = 北京 09-17 00:21:06**（**翻页后 21 分钟**）⇒ cutoff `09-13` ⇒ 滑出 ⇒ **判红**。
  - ⇒ **同一提交、两次运行、两种结论，唯一变量是墙钟日期**。这也解释了「重跑换了一个失败项」这一最迷惑人的事实。
- **归属判定（机械）**：`git diff 9364ef0..HEAD -- backend/tests/test_watch_ledger_kind.py backend/app/picks/watch_ledger.py` **为空**（本 PR 既未改该测试、也未改其实现）⇒ **非本 PR 引入**；但**必须在本 PR 内修**（不修则 CI 无解）。
- **证伪实验（只翻时钟、**一行代码都没改**）**：真实时钟（09-17 ⇒ cutoff `09-13`）⇒ `by_kind` **桶全空**；伪造时钟 `2026-09-16 23:00`（cutoff `09-12`）⇒ **四个桶齐全**（`技术面` / `时事/消息` / `题材共振` / `（未标注）`）⇒ 成因 = **日期窗口**，不是代码。
- **改动**：
  - `backend/tests/test_watch_ledger_kind.py`：① 模块头补**时钟纪律**（凡断言 `tracking_review_stats` 必须同源钉死时钟）；
    ② `test_by_kind_aggregation` **钉死时钟**（`monkeypatch.setattr("app.picks.watch_ledger.beijing_now", lambda: FROZEN_NOW)`，范式沿用 `tests/test_meta_review.py`）；③ **新增** `test_window_cutoff_expiry_is_pinned` —— 把**到期日两侧**都断言（`09-16 23:59` 命中 / `09-17 00:00` 滑出）。
  - **刻意不改**：**不动生产代码**（`watch_ledger.py` 一行未改——窗口口径正确）；**不改**成 `today = beijing_now().date()` 派生夹具日期（那只是把「到期」换成「跨零点写入/读取」的新竞态，治不了病根）。
- **机械证据**：修复前 `1 failed / 1 passed`（与 CI 栈**逐字相同**：`tests/test_watch_ledger_kind.py:68: KeyError: '时事/消息'`）；修复后 **3 passed**。
- **注入自证 2/2（均实跑变红，还原后 `sha256` 逐字一致）**：
  - `[A]` 摘掉时钟钉 ⇒ 复现 CI 原始失败 `KeyError: '时事/消息'`；
  - `[B]` **动口径、不动测试**（生产 `days - 1` → `days`）⇒ 新增契约用例**点名失败**并打印「09-17 00:00：cutoff=09-13 ⇒ 09-12 滑出窗口」⇒ 证明该契约用例**承重**，不是摆设。
- **同族审计（按**机制级**判据收窄，**不泛扫**）**：判据 = 只找「窗口起点取自**全局时钟** **且** 签名**无**注入参数（`now`/`asof`/`today`/`trade_date`）」的函数。候选 4 处，逐项定性 ⇒ **无第二颗到期炸弹**：
  - `meta_review._week_start_bj` —— **已有注入口**，且测试已注入（`KB-ENG-56` **形态一首例**，2026-09-14 已修）；
  - `evolution_probes._alert_probe(…, now)` —— `now` **显式注入**（测试传 `datetime(2026, 9, 16, 9)`）；
  - `akshare_ext` 的 45 天窗 —— 仅作**请求参数**（`start_date=`）传给外部接口，**无任何测试断言其取值**，也不对测试数据做过滤；
  - `theme_catalog_service` 的 TTL cutoff —— 测试用 `utcnow() - timedelta(days=1)`，**相对**时钟、不写死日期；
  - `leader_archive` / `intraday_monitor` 的 `today - offset` —— **无测试调用者**。
- **⚠️ 两种「机械化排查」试过、都**不成立**（已写入 `KB-ENG-56` 形态二，避免后来人重复造）**：
  1. **时钟前推扫描**（`sitecustomize` 在解释器启动时替换 `bjtime` 三函数 + `db.utcnow`，**+42 天**保星期不变以分离"星期相关"噪声）⇒ **假阳性生成器**：钩子只覆盖 `app.*`，而**测试代码 / 夹具数据 / 文件时间戳仍走真实时钟**，造出「测试按 09-17、被测按 10-29」的错配。**对照实验（决定性）**：钩子在场但 `CLOCK_FWD_DAYS=0` ⇒ `test_api.py` **13 passed**；前推后 `test_health` 的 `provider` 由 `mock` 变 `chain(ths→tencent→eastmoney→sina)`——与日期**无因果关系**。
  2. **按「函数名 ∩ 日期字面量」静态交叉** ⇒ `get`/`run`/`add`/`__init__` 等**通用名**把匹配面撑到 **71 个测试文件**，**纯噪声**。
- **遗留与下一步**：① `BUG-010`（`markdown-view.test.tsx` 预算 5000ms 余量仅 1.47×，`docs/` 只增不减）仍需与 `BUG-008` 同族一并排期；② 建议把 `AGENTS.md` §1 的 `--basetemp=/tmp/pytest-basetemp` 改为**按会话隔离**（本轮再次踩到两个 pytest 进程共用 basetemp 互删 `tmp_path`）；③ 无其他遗留。

## IMP-038 逐笔成交接入 TDX 降级备源（闭环）
- **账本**：`docs/retro-and-gaps.md` §6.0 `IMP-038` ｜ **日期**：2026-09-16 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：登记原文为「东财 `push2his` 在本机**通而空**」，要求「落地前必须实测
  字段口径 / 单次返回条数与时间窗 / 与东财『大单』分档是否可比」，并警告**不得混拼**异源口径。
  验收 = ① 实测现状与候选源能力；② 逐笔端点从**恒 502** 恢复可用；③ 口径（方向、量纲、
  时间戳）**可分辨且诚实**，不得把备源数据冒充原口径。
- **改动**（分支 `codex/hunting-dynamic-and-notif-tabs`）：
  ⚠️ **分支名已落定**：本项与 `GOV-016` / `BUG-017` 是同一工作区并行交付的三批改动，
  **统一由该分支承载**——**没有**新建 `codex/data-source-audit`（原稿的分支设想作废）。
  `GOV-016` 账本行的分支名曾误记为 `codex/data-source-audit`，已按实际订正。
  - **新增** `backend/app/market/tdx_tick.py` —— TDX 逐笔适配层：手动分页（每页 ≤1000、
    后取的页 `insert(0,…)`）+ 模块级长连接单例（`RLock`，复用 21.8ms vs 每次新建 616.9ms）
    + `bs_flag` 映射 + 北交所市场段判定 + **真 UTC** 时间戳。
  - **新增** `backend/tests/test_tdx_tick.py`（**55 例**）—— 五组语义判据（时间戳 / 方向 /
    分页 / 降级链 / **故障分类**），全部不触网。
  - `backend/app/api/routes/market_quotes.py` —— `/trades/{symbol}` 改走降级链，`meta.trades_source`。
  - `backend/app/assistant/tools/market.py` —— `_t_trades` 共用同一条链（**"都失败"与"都为空"文案分离**）。
  - **收尾修正（本轮第二次真红，全量门禁才抓到）**：`tdx_tick.trades_failure_detail()`
    新增 + 两个消费方改用（详见下"机械证据"末条）。
  - `backend/app/core/config.py` —— 新增 `trades_tdx_fallback_enabled`（默认 `True`）；
    `backend/tests/conftest.py` 置 `false`（**测试不得触网**）；**同步 `.env.example`**
    （`tests/test_env_docs.py::test_env_example_covers_every_setting` 守卫，**本轮第三次真红**）。
  - `backend/app/services/provider_capabilities.py` —— `get_trades` 条目补记 TDX 备源 +
    单点清单节加「逐笔已不是单点」说明（**链内注册表语义不变**，`single_point_methods()` 仍列 eastmoney）。
  - `backend/tests/test_event_loop_no_block.py` —— `GUARDED` 新增 `tdx_tick` 调用点（防后人去掉 `to_thread`）。
  - **前端**：`components/detail/book-trades-view.tsx`（口径行 + 量纲修正）·
    `lib/format.ts`（`SOURCE_LABELS` 补 `tdx`/`tdx_m1`）· 新增
    `components/detail/book-trades-view.test.tsx`（**7 例**）· `lib/format.test.ts` 补 2 断言。
  - **刻意没动**：`get_capital_flow`（资金流仍在 sina，不碰聚合资金口径）· 东财
    `normalizer.normalize_trade` 的伪 UTC（**属口径变更**，见下"遗留"）· `tdx_kline.py`
    的 `_tdx_market`（其调用面是日K/分钟K，不在本项范围）。
- **机械证据**：
  - **现状实测**：`EastmoneyProvider.get_trades('600519')` **3/3 抛 `ProviderError`**
    （`Server disconnected without sending a response.`，0.12–0.22s）；线上 `GET /api/trades/600519`
    改前 **HTTP 502**（`tencent: empty; sina: empty; eastmoney: Server disconnected…; ths: empty`）。
  - **修后**：`HTTP 200`，`rows=30`，`meta.trades_source="tdx"`；**稳态延迟 0.023s**
    （实测序列 1.01s → 0.186 → 0.144 → 0.023 → 0.023 → 0.023 → 0.024s；链上 3 个空占位
    3 次失败后进 60s 熔断冷却 ⇒ 与设计预期一致）。
  - **数据质量三向交叉验证**：9/16 600519 —— fuyao 日线 2,623,524 股 = **26,235.24 手** ·
    TDX `get_tick_chart` Σvol **26,235 手** · TDX `get_transactions` Σvol **26,243 手**
    （= 26,235 + 盘后 8 笔）· 收盘竞价 15:00:03 price **1258.00** ✓。9/15：13,761.72 ↔ 13,762 / 13,767 ✓。
  - **粒度实证**：全日 3867 行 = 3867 个唯一时间点；秒位只落 3 的倍数；相邻时间差众数 **3s**（3217/3866）
    ⇒ 「3 秒快照聚合」有据（不是照抄库文档）。
  - **方向交叉实证**：价格上行段 `bs_flag=0` 占 **121/138** ⇒ 确认 `0=买`（与东财相反）。
  - **性能**：单页 100 笔 median **20.1ms** · 单页 1000 笔 27–40ms · 全天 4 页 150–300ms；
    建连 median **616.9ms** vs 复用 **21.8ms**（28×）。
  - **覆盖率边界**：920819 有数据；430047 / 830799 实测返 **0 行**（非报错）；
    判错市场**不报错只返空**（600519 传 SZ 返 0 行）。
  - **收尾修正实测（判据归位）**：改前两个消费方都写 `if detail:` ⇒ 备源关闭时
    `detail = "chain: empty; tdx: disabled"` **非空** ⇒ 把"没数据"讲成"取数失败"
    （`test_trades_empty_says_unavailable` 判红）。改后 `trades_failure_detail()`
    取白名单补集：空/未启用 ⇒ `""`（不报故障），真异常 ⇒ 故障片段。
    路由层同步：**只有真故障才 502**；没数据 ⇒ **200 + 空列表 + `meta.trades_detail`**。
  - **HTTP 端到端复验（重启 8000 后）**：`GET /api/trades/600519?limit=3` → **200**，
    `data[0].source = "tdx"`、`ts = "2026-09-16T07:07:45Z"`（= 北京 15:07:45，**真 UTC** ✓）；
    延迟序列 **4.29s**（上一轮 TDX 连接超时被丢弃 ⇒ 本次重建）→ **0.026s** → **0.022s**
    （**连接复用生效**）。`GET /api/trades/830799` → 502，detail 只含故障片段
    （`tdx: empty` 被正确过滤，**不再混进 502 文案**）。
    ⚠️ 取证期间 TDX 服务器一度 **`121.37.207.165:7709 timed out`**（8s 超时）——
    **外部条件，非代码回归**；重试即恢复，且这条路径正好实测了"协议级错误丢弃单例、下次重建"。
  - **测试触网（假绿）实测**：全量首跑 2 红（`test_depth_tools.py`），真因是
    **测试桩返回 `[]`/dict ⇒ 链被判失败 ⇒ 降级到 TDX ⇒ 真的连上服务器拿到 200 行真数据**
    ⇒ 用例"过"了。修法 = `settings.trades_tdx_fallback_enabled` 开关 + `_row_source()` 容 dict
    —— **不是改断言**。
- **注入自证**：后端 **7/7 实跑变红**（`.workbuddy/artifacts/imp-038/injection_selfcheck.py`）——
  `[①]` 时间戳回退成东财式伪 UTC · `[②]` 方向表照搬东财 · `[③]` 让库自动分页 ·
  `[③b]` 手动分页但 `append` 而非 `insert(0,…)`（库内错误分页的真实形态）·
  `[④]` 降级链把"都失败"与"都为空"混为一谈 ·
  `[⑤]` 助手故障分类退回 `if detail:` · `[⑥]` 路由退回 `if not rows and detail:`（⑤⑥ = 收尾补，
  正是本轮真红过的那两版实现）。前端 **1/1**（口径写死成「3 秒快照聚合」⇒ 东财分支判红）。
  八路还原后 `sha256` **逐字一致**、复跑全绿。
  ⚠️ 脚本按**注入点所在文件**分别跑对应用例（判据分散在 3 个文件里，只跑一个文件会漏掉 ⑤⑥）；
  且支持 `--backend-only` —— 前端那路要跑 vitest，与本仓"两道全量不并发"纪律冲突，故可拆两次执行。
- **UI 实测（agent-browser 文本通道，非推理）**：`/workbench?symbol=600519&rt=trades` 实际渲染 ——
  口径行 `口径：通达信 3 秒快照聚合 · 共 30 笔（时间升序，量：手）`；表格首行
  `14:56:00 | 1,258.50 | 18 | B`、末行 `15:24:55 | 1,258.00 | 1 | ·` ⇒ **北京时间正确**
  （真 UTC 决策的端到端验证）、**量不再恒为 0**。
  ⚠️ **顺带修掉一处真实显示缺陷**：量列原用 `fmtVolume`（按"后端统一为股"÷100 转手），
  而 `Trade.volume` **本身就是手** ⇒ **每行渲染成 0**；此前不可见只因该端点一直 502。
- **门禁**：见 §1 置顶基线。
- **遗留与下一步**：
  - ⚠️ **东财备源的 `normalize_trade` 用"伪 UTC"**（`wall.replace(tzinfo=utc)`，其单测
    `test_normalizer.py` 钉住 `t.ts.hour == 9`）⇒ 若东财恢复可用，UI 上会把 09:30 显示成 **17:30**。
    **属口径变更（改它 = 改既有断言）**，按纪律**未擅动**，需拍板。当前东财恒失败，不影响实际观感。
  - ⚠️ **盘中实时性未验证**：取证在 20:20 之后（盘后），需下一交易日盘中复测刷新延迟。
  - ⚠️ **TDX 是直连旁路**：不进 composite 熔断/预算/health 视图（与 `sync_marketdb.py` 同族，
    见 `IMP-037`）。若要纳入治理，需把 TDX 做成**链内 provider**（改动面 = 注册表 + 链装配 +
    单点清单语义，属另一项）。
  - ⚠️ **逐笔列表「最新在最下」**：表格按时间升序渲染（与 `pos=-100` 时代的既有行为一致），
    盘中打开需滚到底才见最新一笔。**属交互改进、非本项缺口**，按「改进先提后做」未擅动。
  - ⚠️ **TDX 单例无心跳 / 无空闲回收**：`_get_client` 建的是 `MacClient(timeout=…)`
    （**未传** `auto_reconnect` / `heartbeat_interval`），只在"协议级异常"时丢弃单例；
    若服务端**静默半死**（socket 在但不应答），下一次调用要等满 `DEFAULT_TIMEOUT=8s`
    才重建（取证时实测过一次 `121.37.207.165:7709 timed out`，重试即恢复）。
    ⚠️ 与仓内既有 TDX 用法**不同**：`tdx_kline.py` / `minute_backfill.py` 都是
    `with MacClient() as client:`（每次新建，即 616.9ms 那档）——本层刻意改为模块级复用。
    加心跳/空闲 TTL 属改进项，**未登记为任务**（无实际痛点证据，先记录不立项）。

## GOV-016 `ths.py` docstring 能力承诺 > 实现：能力清单重构为「已实现 / 未接入」两栏（闭环）
- **账本**：`docs/retro-and-gaps.md` §6.0 `GOV-016` ｜ **日期**：2026-09-16 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：`app/data_providers/ths.py` 模块 docstring 声明能力含「涨停/**跌停**/炸板池、
  连板天梯、龙虎榜、**财务三表、估值**、集合竞价、异动、热榜、**全市场导出**」，逐项核对代码后
  **四者无对应方法**（`get_limit_down_pool` 从未实现；`scripts/sync_marketdb.py` 的直连不属本文件）。
  验收 = 能力清单**与代码逐条对得上**，且未实现项**保留可查**（不删——删掉就丢了「官方有、我们没用」）。
- **改动**：`backend/app/data_providers/ths.py` **只改 docstring、0 行代码变更**——能力清单拆为三段：
  「**已实现**」（与下方方法一一对应）/「**官方有端点但本项目未接入**」（**7 个精确端点路径**）/
  「**已用但不在本 Provider**」（`prices/snapshot` ← `scripts/sync_marketdb.py` 直连）。
  `backend/tests/test_provider_capabilities.py` **+1 例**守卫（该文件本就是「能力注册表 ⇄ 代码」防漂移位）。
  **刻意没动**：`get_limit_down_pool` 等**一律不补实现**——那属 `IMP-037` 待拍板的口径变更，不在本项范围。
- **机械证据**：AST 实测 `ThsFuyaoProvider` **公共方法 21 个**（`aclose` + 20 个 `get_*`）、私有 4 个；
  `hasattr(cls, "get_limit_down_pool")` 实测 **False**。7 个未接入端点在 `app/**/*.py` 的
  **非 docstring 字符串字面量**中实测 **0 命中**——`meta/tickers/list`（已用）与
  `api/routes/market_stock.py` 的 `@router.get("/financials/{symbol}")` 是**同前缀不同路径**，不误伤
  （⇒ 故 docstring 里的 `financials/*` 通配必须改成 4 条精确路径，否则守卫无法机械判定）。
- **注入自证 4/4（全部实跑变红，非假设）**：`[A]` 把 `special-data/limit-down-pool` 接进生产代码
  ⇒ 红并**点名文件**；`[B]` 从「未接入」栏删掉 `valuations/snapshot` ⇒ 红；`[C]` 改掉栏标题
  ⇒ 红（判定面消失）；`[D]` 把未接入端点**同时写进「已实现」栏** ⇒ 红。还原后 `sha256` 逐字一致、复绿。
  ⚠️ **首版 `[C]` 未红 = 守卫真有盲区**：结构断言原用**裸子串** `"未接入" in doc`，而正文 ⚠️ 说明里
  同样含「已实现 / 未接入」字样 ⇒ 改掉栏标题后**照样通过**。改为**锚定条目首行** + **按栏归属判定**
  后 `[C]`/`[D]` 才红——与 `GOV-010`/`GOV-014` 同族：**判据取窄 = 摆设**。
- **门禁**：见 §1 最新门禁基线（后端 `pyflakes` **0** · `doc-health` **全部通过**；本轮后端 collect **+1**）。
- **遗留与下一步**：未接入端点是否接线 + 三入口收敛 = `IMP-037`（**待批**，含口径变更）。无其他未做项。

## BUG-017 `doc-health` 跳过谓词的目录形态锚点少算一层 ⇒ CI `docs` job 恒红（闭环）
- **账本**：`docs/retro-and-gaps.md` §6.0 `BUG-017` ｜ **日期**：2026-09-16 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：PR #20 的 CI `docs (doc-health)` job 红，而**本机同一条命令全绿**。
  验收 = ① 定性「是 master 存量红还是本轮引入」并给出机械证据；② 修掉根因且**不削守卫覆盖面**；
  ③ 干净检出上端到端 `rc 1→0`；④ 把"为什么没人钉住它"补成常驻钉子。
- **改动**：
  - `scripts/doc-health.py` —— `_in_checkout_universe` 的**门② 锚点随形态变**（文件形态取**父目录**、
    目录形态取**它自己**），并新增第三级：锚点不在检出里时用 `_is_gitignored`（`git check-ignore`）
    定夺「**能不能**进检出」。**刻意没动**门①（顶层段）与 `ANCHOR_*` / F 项登记表。
  - `backend/tests/test_doc_health_memory_index.py` —— 新增 **6 例**（构造 `tops/dirs` 直测判定函数）
    + 1 例真实仓库端到端；文件由 **14 → 20** 例。**刻意没动**原有 14 例（它们跑在无 git 回退路径上，
    语义正确）。
  - `docs/kb/09-verification-pitfalls.md` 新增 [[KB-ENG-111]]（43 行，≤60 硬门）；
    `docs/kb/00-INDEX.md` 登记该条；`docs/INDEX.md` §0.3 症状反查「本地全绿、CI 红」补指针。
  - `AGENTS.md` §1 门禁头行同轮同步。
- **机械证据**（可复算）：
  - 根因链：`git ls-files | grep -c '^\.workbuddy'` = **2**（PR #19 强提交的 2 个技能文件）
    ⇒ `.workbuddy` ∈ `_tracked_tops()` **且** ∈ `_tracked_dirs()` ⇒ 门① 放行、门②（父目录 `.workbuddy` 在）
    也放行 ⇒ `.workbuddy/**` 任意子路径被判定为"检出里可能有"。
  - 对照：`fa35c2c`（08:13 master，三 job 绿）与 `9364ef0`（PR #19 合并，docs+backend 双红）之间
    `git diff fa35c2c 9364ef0 -- scripts/doc-health.py` **只有两处文案**（逐行确认不碰判定逻辑）
    ⇒ 差别只可能在**数据面**。
  - 干净检出复现（`git worktree add --detach /tmp/ci-sim HEAD`）⇒ `[FAIL] N 6 处指针失效` +
    `[FAIL] O 幽灵条目 4 条`，**10 条明细全部带尾斜杠**、文件形态**一处未红**。
  - 修复后同一检出、同一 HEAD：`[OK] N 0 处` / `[OK] O 0 条` / `结论：全部通过`，退出码 **1 → 0**。
  - ⚠️ **取证陷阱（本轮踩到）**：`ROOT` 由 `__file__` 推导 ⇒
    `cd /tmp/ci-sim && python3 <主仓绝对路径>/scripts/doc-health.py` 判的是**主仓**（那里 4 个目录都在
    ⇒ `幽灵条目 0 条`，**假绿**）；必须用**检出内部的脚本副本**。
  - 判据真值表（13 例）：`.workbuddy/{memory,artifacts,trash,reports}/` → **不判**；
    `.workbuddy/skills/` → **判**（真在检出里）；`data/picks/` → **不判**；
    `scripts/no-such-dir/` → **判**（非 gitignored 的缺席目录 = 真问题）。13 次判定合计 **0.260s**
    ⇒ 不引入可感性能开销。
- **注入自证（4/4 报红，逐路只动一处）**：`[A]` 门② 回退父目录 ⇒ `test_dir_pointer_anchor_is_itself` 红；
  `[B]` 撤掉第三级（改成"缺席即不判"）⇒ `test_absent_dir_is_still_judged_when_not_ignored` 红
  —— **这条是「修判据」与「撤守卫」的分界位**；`[C]` 目录形态去掉尾斜杠 ⇒ 尾斜杠钉红；
  `[D]` 判定面换回文件系统口径 ⇒ 真实仓库端到端钉红。还原后与备份**逐字相同**、20 例全绿。
- **门禁**：后端 **collect 3372（3296 passed / 76 skipped / 0 failed / 416.23s，8000 在跑）**、
  `pyflakes` **0**、`doc-health` **全部通过**、`tsc` **0**、`eslint` **0 / 0**、
  前端 **643 项 / 69 文件**（本地与 `TZ=UTC` 逐字一致；本轮未改前端）。
- **遗留与下一步**：① master 的 `backend` job 另有一处**偶发红**（两项 SQLite/共享状态用例），
  已在 §1 记录并给出"同树绿 ⇒ 非本轮引入"的证据，**未修**（属既有 `BUG-008`/`BUG-010` 同族观察面）；
  ② `event-panel.tsx` 的第三份 `FOUR_STYLE` 与「守卫不扫 `components/`」的判据盲区仍挂在
  `IMP-036`（A 档）；③ 本轮**未**新建 worktree 之外的临时产物，`/tmp/ci-sim` 与 `/tmp/ci-green`
  完成后清理。

## IMP-035 用户七问交付：资讯/事件 tab · 模型写死 · 提醒去重 · 猎场闸门读时重算（闭环）
- **账本**：`docs/retro-and-gaps.md` §6.0 `IMP-035` ｜ **日期**：2026-09-16 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：用户当日七问逐条给结论并落地。验收 = ①七问**每条有可复算/可观察证据**
  （不凭推理）；②落地项有守卫 + **注入自证**；③门禁全绿。**「今日已错过的盘中机会不可追补」
  如实声明、不做假回填**（回填一个不存在的历史提醒 = 伪造证据）。
- **改动**（按问编号）：
  - **Q1 通知中心**：先确认**已停推**新闻/事件（`IMP-028` 收口 `stock_opportunities_only`）⇒ 新增
    「资讯 / 事件」tab（`components/notifications/event-feed.tsx`）。展示口径（四级分类/三级影响力配色、
    点击落点）抽到 **`apps/web/lib/event-view.ts` 共享**，**与盘面页事件标签同源同一端点**
    （`GET /api/events/impact`）；**抽屉是窄栏但落点必须一致，版式各自决定** ⇒ 该模块**刻意不提供 JSX**。
    **浏览面不计未读**（事件不是"我的提醒"，混进未读会让红点失真）。
  - **Q3 模型写死**：`review_llm_model` 默认 `deepseek-v4-flash`（`backend/app/core/config.py`），
    配 7 例单源守卫（`backend/tests/test_llm_model_single_source.py`）。
    实测：deepseek **3.339s / rc=0**；glm-5.3 **挂起至 SIGTERM(137)** ——
    ⚠️ **两者 stderr 都带 `unrecognized_model`** ⇒ **警告文本与成败正交，不能拿它判可用性**。
  - **Q4 进化重跑**：`议程完成：status=executed items=4`（全 B 类），48s，`llm_used=1/8`；
    产物 `docs/evolution/2026-09-16.md`（该目录为**跟踪目录**，故随本轮入库）。
  - **Q5 盘中提醒未提示**：根因 = `_already_recent` 按 **`rule_id`** 去重 ⇒ 当日 121 条 `pre_limit`
    （=121 只**不同标的**）**只逃出 1 条**；已按用户选择「两处都改」修：去重键改**标的** +
    DB 侧 `real_symbol_only` 修窗口截断（实测 `count 89→200`）。
  - **Q7 闸门动态化（核心）**：根因是**读时重算 vs 落库冻结**——`_live_style_routing` 每次读取重算相位，
    而 `gate` / `market_phase` 来自 **09:26 落库** ⇒ 出现「chip 显示实时高潮、闸门仍按生成时刻退潮」
    **两个相反结论并排**。修法：引擎新增 **`sentiment.gate_inputs` 结构化出口**（四项本就算过，
    读侧**零额外网络调用**；第六个消费方仍共用 **60s 共享情绪槽**，**不新开槽**）+
    `picks.py` 的 `_live_gate` / `_attach_gates`；落库值标 `gate_source="stored"`、实时重算值放
    `meta.gate_live`。**降级三态** `gate_source ∈ {stored, live, unavailable}` ——
    **不得把 `stand_aside=False` 凭空造出**（那等于把"不知道"伪装成"安全"）。
    前端 `lib/picks-gate.ts` 纯函数（`gateActive` / `gateDrift` / `gateInputLines` / `compareGates` /
    `clockOf`）+ `StandAsideBanner` 动态对照；`gateDrift` 差异枚举
    `none|cleared|newly_triggered|reasons_changed|unavailable`，**只有 `gate_source==="live"` 才做差异判断**。
  - **Q6 方法论**：`KB-STOCK-37`（情绪高涨 ≠ 上车机会：**先分"接力真假"、再分"钱扩散还是收缩"**）；
    「拉证券横盘」**明标待验证假设**并写明**本系统当前没有这条检测**（不当既有能力）。
  - **门禁抓出的缺口（同轮修）**：新增 `lib/event-view.ts` 触发跨端契约「防遗漏」守卫 ⇒ 登记
    `FOUR_STYLE`（← `events.impact.FOUR_LABEL`）与 `LEVEL_STYLE`（← `events.ranking.LEVEL_REASON`）；
    登记时暴露**前端 `LEVEL_STYLE` 缺 `L3` 键**——后端分级全集恒为 `L1/L2/L3`，默认只是**过滤**不发
    （`include_l3` 默认 False）⇒ 补 `L3` 键，否则开启该参数即**无色徽标**。
    **「默认不渲染」不构成豁免理由**（豁免 = 放行一个真缺口）。
- **机械证据**：后端 collect **3366**（**3290 passed / 76 skipped / 0 failed / 301.48s**）；
  前端 **643 项 / 69 文件**（本地与 `TZ=UTC` 逐字一致）。Δ 全部机械归因，见 **§1 门禁基线**块。
- **注入自证**（三处，**均正确报红**）：
  ① `gateDrift` 去掉 `gate_source` 来源守卫 ⇒ 旧后端形态（复核缺失、传进来的是落库对象）被判成
  「盘中**新增**触发」——**与事实正好相反**；② 降级说明停渲染 ⇒ 报红；
  ③ `LEVEL_STYLE` 移除 `L3` 键 ⇒ 契约守卫报「event-view.ts 里没有覆盖后端全集 `['L1','L2','L3']` 的字典」。
- **UI 实测**（agent-browser 文本 + 几何量，**不靠推理代替观察**）：
  · 通知中心 `rowCount=30` / `notification-actions=30` / `notification-judgment=30`；标签 =
    「个股机会」「资讯 / 事件」；切到事件态 `notification-row=0`、`unreadDots=0`（未读不被污染）。
  · **「行情与判读一体化、放标签下方右边」以几何量确证**：操作组与其**标签行同一行**
    （`sameRowAsLabel=true`）、位于**主体下方**（`belowBody=true`）、**右对齐**（`ml-auto`，右间距 11px）。
    ⚠️ **首测取错选择器**（卡片内有两个 `mt-1`，`querySelector` 取到**正文段**而非标签行）
    ⇒ 一度得出 `sameRow=false` 的**错误结论**；改用「操作组的父节点」为基准才正确
    —— **量布局必须锚在所测元素自己的容器上，别用同 class 的首匹配**。
  · 事件 tab：`event-feed-row=30`、`L2` 徽标**有样式**（非空 className）；
    猎场 `stand-aside-cleared` + `role="status"`：生成时「退潮」晋级率 8%（历史 9 分位）
    ⇄ 当前「高潮」36%（98 分位）/ 炸板率 11%（2 分位）。
- **门禁**：后端 **3366（3290 passed / 76 skipped / 0 failed）** / 前端 **643·69** / `tsc` **0** /
  `eslint` **0 error / 0 warn** / `pyflakes` **0** / `doc-health` **全部通过**。
- **遗留与下一步**：① **`IMP-036`**（A 档）：`components/event-panel.tsx` 仍保留**第三份**
  `FOUR_STYLE`，且契约守卫的扫描面只覆盖 `apps/web/lib/*.ts`、**不覆盖 `components/`** ⇒ 该守卫对组件目录
  **存在判据盲区**；② **`RSH-028`**（C 档·等窗）：「越不信越拉 / 拉证券横盘」类主力反向操作的**盘面判据**
  未实现（`KB-STOCK-37` 已标待验证假设），需样本；
  ③ 通知中心「未读」只统计 `_NOTIF_KINDS`（资讯/事件为浏览面不计未读）——这是**设计口径不是缺陷**；
  ④ 今日盘中**已错过**的机会**不可追补**（已向用户明示，不伪造历史提醒）。

## RSH-003 候选因子「入池前结构新颖性筛查」切片（本项仍开放）
- **账本**：`docs/retro-and-gaps.md` §6.0 `RSH-003` ｜ **日期**：2026-09-16 ｜ **状态**：🟡 部分闭环
- **缺口与验收标准**：`RSH-003` 的施工口径已由用户拍板为 **novelty-first**（`RSH-024` 销账：
  「不盲目堆积指标」，优先 ADX/PPO，明显同族项 parked），但**此前没有任何可复算的"结构新颖性"判据**——
  剩余指标只能靠人工印象决定"要不要转正"。本切片把"新颖性"落成**三个可复算判据**，
  验收边界 = **判据可复算 + 档位可复现 + 能对"换皮指标"说"不"**；
  **不做** 计权 / 准入判定 / 改 `FACTORS`（那些属 `evaluate` 的 IC/ICIR/分层/覆盖率那一整套）。
- **改动**：
  - `backend/app/factors/novelty.py`（**新，只读证据层**）：三判据 —— ①逐日**截面秩相关**
    （`rank_corr`，同一次扫描算出候选 × 既有池全部配对的逐日相关）；②前 K **头部集合重合**
    （`topk_overlap`，前 `TOPK_FRAC=20%` 且 ≥`TOPK_MIN=10` 只）；③近邻**三分位组内条件 IC**
    （`conditional_ic`，`COND_GROUPS=3`，**刻意不参与判档**——阈值属口径问题须人工拍板）。
    四态输出：`duplicate`（|ρ| ≥ `DUP_RANK_CORR=0.999`，**是数学结论**）/ `redundant_hint`
    （≥ `IC_CORR_DEDUP=0.70`，沿用既有常量**不新造**）/ `distinct` / `insufficient_sample`
    （有效截面日 < `MIN_DAYS_FOR_VERDICT=30`，与 `MIN_LABELS_FOR_VERDICT` 同族纪律）。
    **口径同源**：复用 `evaluate` 的 `_base_cte` / `HORIZONS_EXEC` / `IC_CORR_DEDUP` / `MIN_CROSS_SECTION`
    （用例 `test_base_cte_identity_is_shared_with_evaluate` 钉死是**同一个函数对象**）。
  - `backend/scripts/factor_novelty.py`（**新，只读 CLI**）：四候选**分两类职责**，不是"再加四个指标"——
    `willr20` / `cmo20` = **机制自证**（分别与既有 `rsv20` / `sump20` 呈**仿射**关系，
    判据若抓不到它们就是失效的）；`kurt20` / `skew20` = **判别力对照**（池内 40 项无任何高阶矩
    ⇒ 若判据把"结构上真新"的也判重，说明它在**一律判重**）。
    ⚠️ **`cci20` 刻意不做**：DuckDB 的 `mad()` 是**中位绝对偏差**，TA-Lib 的 MD 是**平均绝对偏差**
    （同名不同物）⇒ 照抄即**静默换口径**（数值看着合理、语义已变），正确实现需给 base 链加一层，
    属**框架扩展**（已记入账本遗留项）。
  - `backend/tests/test_factor_novelty.py`（**新，18 例**）：合成小仓（40 只 × 70 交易日 / 含
    `daily_k_adj`）⇒ **不依赖真实 marketdb**、秒级可跑；覆盖仿射自证 / 反序（|ρ|=1 但头部零重合）
    / 判别力对照 / 薄样本劫持 / **CLI 接线**五类。
  - `docs/kb/09-verification-pitfalls.md` + `docs/kb/00-INDEX.md`：新增 [[KB-ENG-107]]（按 |ρ| 择优前
    必须先设样本门槛）与 [[KB-ENG-108]]（命令行里有 ≠ 被测对象收到了）；**顺带补上
    `KB-ENG-104` / `KB-ENG-105` 两行缺失的索引**（有定义、索引表无行）。
  - **切片 2（指数平滑能力 + PPO / ADX 实测）**：
    - `backend/app/factors/smoothing.py`（**新，原语层**）：`ema` / `wilder` 两种指数平滑的**窗口 SQL 生成器**
      —— `EMA_i = c·EMA_{i-1} + a·x_i`（`c = 1−a`），落成**闭式** `EMA_i = Σz/Σv`（`z_j = x_j·(1/c)^rrel_j`、
      `v_j = (1/c)^rrel_j`），**不写递归 CTE**。`alpha_for` 按 kind 取值（`ema → 2/(n+1)`、`wilder → 1/n`），
      **不设默认兜底**（kind 拼错必须抛错，不许静默给一个值）。截断窗 `SMOOTHING_WINDOW_BARS=400` +
      溢出护栏 `MAX_SAFE_LOG_EXPONENT=600.0`（本库 `max_bars=2435` ⇒ `ema12` 407 / `ema26` 187 / `wilder14` 180）。
      **核心正确性条件是「成对权重」**：指数必须取「该行距**当前行**的距离」；误取「距**帧首行**的距离」
      会让满帧下偏移恒为常数 ⇒ 权重全相等 ⇒ **静默退化成等权 SMA**（数值合理、不报错、单看产物看不出来）。
    - `backend/scripts/factor_novelty.py`：接上 `PanelExtension` 扩展钩子 —— `ppo20` =
      `100·(ema12 − ema26)/NULLIF(ema26,0)` 对 `close_adj`；`adx14` = Wilder 四级链
      （±DM → Wilder 平滑 → DI → DX → 再 Wilder 平滑），共 **12 个 CTE**、末级 `adx_s2_s`。
      多级链**每次换 `tag`**、第二级 `source` = 第一级末级名。**无扩展时 `novelty.py` 逐字不变**
      （`_PANEL_SQL_BASELINE_SHA256` 冻结哈希守卫）。
    - `backend/tests/test_smoothing.py`（**新，32 例**）：合成仓（不依赖真实 marketdb）+ **独立 Python
      朴素递归参照**逐点比对。覆盖：精确性（~1e-9）· **陷阱回归**（错误形态偏差 ~8.15 vs 正确形态 ~1e-15，
      量级可断言）· 溢出护栏 · warmup 语义 · 缺失值 · 入参守卫 · 多级链 · 扩展钩子集成。
    - `docs/kb/09-verification-pitfalls.md` + `docs/kb/00-INDEX.md`：新增 [[KB-ENG-109]]（静默退化）
      与 [[KB-ENG-110]]（窗口求值位置 / 名次列 `FILTER` 形同虚设 / `corr` 零方差返回 NaN）；编号闭包连续。
  - **刻意没动**：`FACTORS`（**零新增、零计权**）、一切**推送 / 交易口径**；
    `skew20` 即便判 `distinct` 也**不自动转正**（下文「`distinct` ≠ 准入」）。
- **机械证据**：
  - **真实库实测**（默认口径：4 候选 × 既有池 40 项 × 回看 250 交易日 ⇒ **249 个截面日**；
    T+1 收盘进 / T+5 收盘出；单日成熟样本门槛 30。数字取自产物 JSON 的 `meta` / `nearest`）：
    `willr20` → **`duplicate`**（最近邻 `rsv20`，秩相关均值 **+1.000** / 中位 +1.000 / 有效日 249）
    · `cmo20` → **`duplicate`**（最近邻 `sump20`，**+1.000 / +1.000** / 249）
    · `kurt20` → **`redundant_hint`**（最近邻 `wvma20`，**+0.765 / +0.772** / 230）
    · `skew20` → **`distinct`**（最近邻 `cord20`，**+0.523 / +0.548** / 230）。
    ⚠️ **勿把"头部重合率均值"当成"秩相关中位"**：`kurt20` / `skew20` 的头部重合均值是
    **0.675 / 0.490**，与秩相关中位（**0.772 / 0.548**）**不是同一个量**——
    本轮曾把前者标成"中位"，已订正（四个数字都取自产物 JSON，勿凭记忆转述）。
  - **两条机制自证恰好命中**（本切片最强的判据有效性证据）：`willr20` 是 `rsv20` 的**仿射换皮**
    （WILLR = 100·rsv20 − 100）、`cmo20` 是 `sump20` 的仿射换皮（CMO = 200·sump20 − 100）
    ⇒ 两者**不可能提供新信息**（此结论与阈值怎么选**无关**）。
  - **意外发现（对"不盲目堆积指标"的直接支撑）**：**高阶矩不是天然的新信息源**——
    池内虽无任何高阶矩，`kurt20` 仍与 `wvma20` 秩相关 **0.765**（≥ 0.70 提示线）；
    即"指标族里没有、故必然新颖"这个直觉**不成立**，必须实测。
  - **产物**（证据面，可复跑）：`.workbuddy/artifacts/rsh-003/novelty_report_20260916.json`
    （本切片 CLI 的完整报告，含逐配对 `n_days` / `verdict_eligible` / `thin_pairs`）与
    `eval_report_20260914.json`（既有）。
  - **耗时（修复 CLI 丢参后的两次对照，同一真实库；160 个 `corr` 列）**：
    回看 250 交易日（249 个截面日）**295s**（CLI 外部计时 297s）vs `--lookback 8`（7 个截面日）
    **47.7s** ⇒ **缩短回看窗确实能省时间（约 6×）**，成本随"窗口内截面日数"近似线性、
    窗口链（`lvl1`~`lvl3`）是固定底价。⚠️ 前一版把它**写反**了（"与 `--lookback` 基本无关"），
    见下文「遗留」的订正；    两次运行的 `meta.lookback_days` 分别是 250 / 8（**已核对，不是丢参**）。
  - **切片 2 真实库实测**（同一 CLI / 默认口径；产物
    `.workbuddy/artifacts/rsh-003/novelty_report_20260916_ppo_adx.json`，`meta` 实录
    `lookback_days=250` / `horizon=5` / `n_candidate_days=249` / `n_incumbents=40` /
    `extension.source="adx_s2_s"` / `n_ctes=12` / `elapsed_sec=282.8`）：
    `ppo20` → **`redundant_hint`**（最近邻 `mom20`，秩相关均值 **+0.778535** / 中位 **+0.791143** / 有效日 249；
    头部重合均值 +0.7825 / 中位 +0.792776）·
    `adx14` → **`distinct`**（最近邻 `std20`，**+0.308566** / **+0.316292** / 有效日 249；
    头部重合均值 +0.335281 / 中位 +0.326844）。两者 `thin_pairs` 与 `uncompared` **均为空**。
    ⚠️ **`ppo20` 的冗余来自"动量族"而不是"用了 EMA"**：次近邻 `sump20` +0.764596、`beta20` +0.764579、
    `mom60` +0.645324 —— 即 PPO 与既有动量指标的**共线是内容层面的**，不能归因成平滑方式。
    ⚠️ **`adx14` 与既有 `atr14` 仅 +0.236064**（次近邻 `rsqr20` +0.250696、`range20` +0.226507）——
    ADX 的信息在 **DI 的定向差**，`atr14` 只有波动**幅度**；两者同源（都含 TR）但**不同物**，与设计预期一致。
  - **切片 2 机制自证 5/5**（探针 `.workbuddy/artifacts/rsh-003/component_probe.py` v2，**26.7s**，产物
    `component_probe.json`）：`wilder_atr|atr14_w` = **+0.973489**（249 日 / **0 NULL 日** ⇒
    "可平滑、但**未退化**成等权"）· `ppo20|ppo20_eq` = **+0.812190**（224 日 / 25 NULL 日）·
    **正对照 `atr14_w_a|atr14_w` = +1.000000（机器校验，精确命中）** ·
    边界效应探针 `avg_tr14_ctrl|atr14_w` = **+0.999174**（236 日 / 13 NULL 日）。
    ⚠️ **正对照不是装饰**：它证明"测量通路本身能输出 1"⇒ 负对照的 <1 读数才可信（见下 KB-ENG-109 同族纪律）。
  - **切片 2 耗时**：主跑 `--lookback 250 --horizon 5` = **282.8s**（CLI 内计），与切片 1 的 295s 同量级 ⇒
    **再证"列数是主导成本项、成本随窗口内截面日数近似线性"**（切片 2 只加 2 候选，而未变慢）。
- **注入自证**：
  - **① 薄样本劫持（判据真 bug）**：把 `nearest` 退回 `pairs[0]`（按 |ρ| 直接取第一）
    ⇒ `test_thin_pair_cannot_become_the_nearest_neighbor` **真红**，且**首条**报错直指
    「最近邻被样本不足的配对劫持」（断言顺序**刻意**把核心断言排在前置条件之后、|ρ| 比较之前
    ——否则注入后会先停在 `assert 0.9999 > 0.9999` 这种读不出病因的地方）⇒ [[KB-ENG-107]]。
  - **② 参数静默丢弃（CLI 真 bug）**：把 `parse_args(argv)` 改回 `parse_args(argv or [])`
    ⇒ `test_cli_reads_sys_argv` **2.2s 变红**，报错直指「用的是**模块默认库**而非 argv 给的库」
    ⇒ [[KB-ENG-108]]。
  - **③ 判据不冗余**：`neg_mom20` = −1·`mom20` ⇒ |ρ| = 1 但**头部零重合**，
    用例断言两个判据给出**不同**结论 ⇒ 证明头部重合不是秩相关的复述。
  - ⚠️ **诚实标注：下面这条不是注入自证，是测试自身的失效（当年漏网的成因）**——
    合成仓原先把伪随机噪声的种子初始化在**股票循环之外** ⇒ 噪声在**同一日对全截面同值**
    ⇒ `mom5/mom10/mom20/mom60` 与任何动量候选的秩相关**全为 1**，"最近邻是谁"退化成并列里的
    任意一项（实测被判成 `mom5`），「能否认出被仿射的那一个」这条判据**失去检验力**。
    改为**每只股票一条独立噪声流**（种子按序号偏移、幅度 0.02）后消失——**未放宽任何断言**。
    ⇒ 教训：**造得"看起来会红"的夹具，可能只是在测自己的噪声源**。
  - **切片 2 注入自证（`test_smoothing.py`，4/4 逐条命中）**：
    ① 把指数取成"距**帧首行**的距离"（即上文那个陷阱形态）⇒ **判红 8 项**
    （含结构守卫 `test_prep_offset_is_partition_wide_not_frame_local` 与陷阱回归本身）；
    ② 让分母**不随 `src` 置空**（`v` 恒为 1）⇒ 红 3 项；③ 把 warmup 放宽为 `rrel >= 1` ⇒ 红 7 项；
    ④ 把溢出护栏阈值由 600 放大到 1e9 ⇒ 红 2 项。
    ⚠️ 四处注入均先断言 **`mut != orig`**：证明"注入确实改到了判定面上的那串 SQL"——
    否则"红了"也可能只是改坏了别处，与守卫无关（[[KB-ENG-105]] 同族纪律）。
  - **切片 2 探针自身的两次假红（已修正，勿回退）**：
    ① v1 的"内联同式复刻"正对照实测 **0.999174** 而非 0.9999 ⇒ 机械取证定位到
    `n_days = 236 = 249 − 13`，而 13 恰为 14 根窗的 warmup —— 即 **DuckDB 的窗口函数在 `WHERE`
    之后求值**、输入行集只有回看窗内的行（[[KB-ENG-110]] 第一条）。修法：该探针**降级为"边界效应探针"**，
    另外建立**真正的机器校验**（同一表达式以两个别名各算一遍 ⇒ ρ 必须**精确** = 1.000000，实测命中）。
    ② v2 首跑的边界效应判据读出 `n_null_days=0` 而报红 ⇒ 根因是 **DuckDB 的 `corr` 在输入零方差时
    返回 NaN 而不是 NULL**，`v is None` 判空**恒假**（[[KB-ENG-110]] 第三条）⇒ 改为 NaN-aware 判空。
- **门禁（切片 2 收尾实测；2026-09-16，后端跑时 8000 在跑）**：后端 **collect 3339 / 3263 passed /
  76 skipped / 0 failed**（**240.74s**；收尾复跑 **237.56s**）· `pyflakes` **0** · `doc-health` **全部通过** ·
  `tsc` **0** · `eslint` **0 error / 0 warn** · 前端 **608 项 / 67 文件**（本地与 `TZ=UTC`
  **逐字一致**；`apps/web/` 本轮**零改动** ⇒ 与上轮基线相同）。
  ⚠️ **Δ 机械归因（切片 2 较切片 1 的 3306 / 3230 ⇒ +33 collect = +33 passed / ±0 skipped，非覆盖率虚增）**：
  `+32` = 新文件 `tests/test_smoothing.py`（32 例，**全 passed**）；`+1` = `test_import_lint.py`
  分层参数化新增一个**业务层**模块 `app/factors/smoothing.py`（实测用例名
  `test_business_layer_never_imports_api[-factors/smoothing.py]` 存在）。
  **非业务层模块数未变** ⇒ 实测 `tests/test_import_lint.py` = **194 passed / 74 skipped**，
  `74 = 76 − 2` 与上轮**逐字相同**（[[KB-ENG-97]]：跳过数不变 = 无覆盖丢失）。
  （切片 1 的对应归因：+19 = `+18` 新文件 `tests/test_factor_novelty.py` + `+1` 业务层 `novelty.py`。）
- **遗留与下一步**：
  - **`skew20` 是否转正待用户拍板**：`distinct` 只表示"**无明显的冗余证据**"，
    **不等于应当准入**（准入仍需过 `evaluate` 的 IC/ICIR/分层/覆盖率那一整套，且须过样本门）。
  - **`kurt20` 建议不单独入池**（0.765 ≥ 0.70 提示线）。
  - **`cci20` 需框架扩展**（base 链加一层 `avg(|x − avg(x)|)`）后才可正确实现。
  - ✅ **ADX / PPO 已实测（切片 2，2026-09-16）**：`ppo20` = `redundant_hint`（+0.779，与**动量族**共线）、
    `adx14` = `distinct`（+0.309）。**`distinct` ≠ 准入** ⇒ 是否转正**仍待用户拍板**。
  - **待拍板：`adx14` / `ppo20` 的档位**。`adx14` 即便判 `distinct`，转正仍须过 `evaluate` 的
    IC/ICIR/分层/覆盖率**整套** + 样本门；`ppo20` 若要采用，需先说明"与 `mom20` 共线 +0.779"为何可接受。
  - **待拍板：`_rank_corr_pass` 的 `FILTER` 修法**（[[KB-ENG-110]] 第二条）：当前 `FILTER` 过滤的是
    `percent_rank()` 的**输出**，而 `percent_rank()` **对 NULL 行返回非空**（实测 `1.0`，
    `count(pr)=4` vs `count(x)=3`）⇒ 该过滤**形同虚设**、结论**偏保守**（可能**漏判**冗余）。
    改为引用**原始列**即可修好，但这会**重算既有 4 候选的 ρ** ⇒ 属**口径变更**，须用户拍板后再动。
  - **条件 IC 的阈值**（多小算"无贡献"）属**口径问题、须人工拍板**；本模块只并排摆出数字、
    不做自动断言（这也是它不参与判档的原因）。
  - ✅ **已订正（勿回退）**：「缩小 `--lookback` 省不了时间」**结论反了**——实测 250 → **295s**、
    8 → **47.7s**（约 6×）。原结论是从代码推出来的（"`date_ms >= cutoff` 只是事后过滤，
    下推不到窗口里"），该推理**只对窗口链成立、对 `corr` 段不成立**；且当时"支持"它的那次
    "实测"是**假证据**（`--lookback 8` 从未被解析）。⇒ 源码注释 / 账本 / 本条**三处已同步订正**。
  - **后续扩候选仍须分批**：160 列已 **295s**（切片 2 加 2 候选**并未变慢** —— 印证"列数是主导成本项"），
    成本随窗口内截面日数近似线性 ⇒ 可先用 `--lookback` 小窗做初筛。
  - ⚠️ **多级链的 warmup 不自动叠加（切片 2 实测语义，勿想当然）**：第二级只要求**当前行**有值，
    起点 = `max(本级 window, 前级首个有值行)`。若要求**两段都排满**，必须由候选表达式**显式置空**
    （本切片 `adx14` 即写 `CASE WHEN cnt >= 2×14 = 800 THEN adx END`）。想当然以为"链上会自动叠加"，
    会让开头若干根 ADX 建在只有**一级**平滑的 DI 上 —— **数值合理、不报错**（[[KB-ENG-109]] 同族）。

## RSH-027 场景化 KB 路由与引用记录（切片 1 已交付，本项仍开放）
- **账本**：`docs/retro-and-gaps.md` §6.0 `RSH-027` ｜ **日期**：2026-09-16 ｜ **状态**：🟡 部分闭环
- **缺口与验收标准**：蓝图 §5 要求「按盘前/事件/盘中/复盘/进化调用指定 KB，并记录 `kb_ids` 与冲突依据」，
  并附三条禁令（示例不得作硬规则 / 不得越过交易硬门 / 不进入个股收益打分）+ 一条**诚实注记**：
  「用有/无 KB 影子对照证明增益，**否则仅保留解释价值**」（原文自述当前**尚未证明**能提高选股结果）。
  本切片的验收边界 = **路由契约 + 引用记录 + 引用校验 + 覆盖度自证**；
  **不做检索本身、也不做消融**（消融须等样本，属切片 2）。
- **改动**：
  - `backend/app/picks/kb_routing.py`（**新，676 行**）：四场景 ⇄ 蓝图 §5 **逐行对应**的路由表 +
    唯一索引解析器 + 覆盖度恒等式 + 三态引用快照 + 三道红线守卫。
  - `backend/app/services/evolution.py`：`_collect_knowledge_base()`（**议程第八路**）由
    **自建私有正则**改为**复用**唯一解析器，并把 `coverage` 自证暴露到议程 —— **偏差修复**。
  - `backend/app/models/opportunity_learning.py` + `backend/migrations/versions/c5d2f8a3b7e1_snapshot_kb_citations.py`（**新，51 行**）：
    `opportunity_decision_snapshot` 增 `kb_ids` / `kb_refs` 两列（`down_revision = b4f1a7c2e9d3`；
    **刻意不建索引** = 少一处 schema 分叉面；全程 `op.*`，遵 `BUG-014` 教训）。
  - `backend/app/picks/opportunity_learning.py`：写入侧 `build_intraday_records` / `build_notification_records` /
    `archive_intraday_pipeline` / `archive_notification_pipeline` 一律经 `snapshot_citations()` 快照；
    读侧 `replay_run` 回读两列、`learning_summary` 增 `kb_ref_states` 计数。
  - `backend/app/api/routes/picks_intraday.py`：新增只读端点 `GET /api/picks/kb-routing`（带 300s 缓存）。
  - `backend/tests/test_kb_routing.py`（**新，416 行 / 18 例**）：10 组守卫，每组对应一个具体失效方式。
  - `docs/kb/09-verification-pitfalls.md` + `docs/kb/00-INDEX.md`：新增 [[KB-ENG-104]] 与索引行。
  - **刻意没动**：一切**推送 / 交易口径**（`IMP-028` 通知口径、撮合与风控、`RSH-026` 既有标签语义）；
    `enters_scoring` **全部为 `False`** —— **不宣称 KB 已入模**（原文未证增益，宣称即谎报）。
- **机械证据**：
  - **路由表 ⇄ 蓝图原文逐行比对**：用例从 `docs/summary/system-final-blueprint.md` **现场解析**该表
    （不抄一份副本），逐字比对场景标签与册/状态约束 ⇒ 4/4 对应。
  - **偏差量化（本条最实质的证据）**：旧私有正则匹配 **162** / 唯一实现 **178**；差集 **16** 条，
    成因 **`4 + 12` 无余数** —— `📎` 不认 → `KB-STOCK-01/02/03/04`（4）；
    状态列多词备注 → `KB-STOCK-27/29/30/31/32/33/34/35/36`（9）+ `KB-ENG-79`/`KB-ENG-82`（2）
    + `KB-DEC-003`（1）。**`old_not_new == []`** ⇒ 新解析器是旧面的**严格超集**（无新增误判）。
    ⚠️ 漏掉的恰是**最经过实证的一批**（`KB-STOCK-27` 三倍态战法**实测否决**、`KB-ENG-79`
    `ntile()` 无 tie-break **连跑 3 次 3 个结论**、`KB-DEC-003` 是「❌ 部分取代」= **勿回退信号**）。
  - **第二处偏差**：册前缀写死四册 ⇒ 第五册 `KB-REPO` 不可见、`| KB-REPO-* |` 册级行连"像索引行"都不算。
    该行由**朴素统计 179 vs 解析 178 差 1** 才发现 —— 「**只差 1 行**」正是最容易放过的信号。
  - **覆盖度恒等式（本切片的机械判据）**：
    `candidate_rows == total + len(book_level_rows) + len(unparsed_rows)`。
    实测 `unparsed_rows == ()` / `unknown_books == ()` / `coverage_identity_holds == True`。
    ⇒ **断言面从「我认得的」扩到「表里所有的」**；只断言 `unparsed == 0` **是自证不足**
    （册级行会被前缀判据静默排除，连"未解析"都不进）。
  - **端点实证**（后端 8000 在跑，`curl --noproxy '*'`）：`GET /api/picks/kb-routing` ⇒
    `index.coverage_identity_holds: true`、`unparsed_rows: []`、`unknown_books: []`、
    `book_level_rows: ["KB-REPO"]`、`registered_books` **5 册**、`scoring_books: [KB-STOCK, KB-TRADE]`、
    `known_statuses` **5 档** + 四场景路由表（`pre_open_event` / `intraday_pick` /
    `post_close_review` / `system_evolution`，`enters_scoring` **全 `false`**）。
    ⚠️ **报数字必须带取数时刻**：本条 `KB-ENG-104` **自身入库**使
    `total` **178 → 179**、`candidate_rows` **179 → 180**、`by_status.✅` **163 → 164**
    （其余档位不变：`🔶:5 / ⏳:4 / ❌:2 / 📎:4`）—— 恒等式不变；不说时刻会被当成回归。
  - **迁移实测**：真实库 `alembic_version = c5d2f8a3b7e1`（已应用）⇒ `opportunity_decision_snapshot`
    **20 列**（含 `kb_ids` / `kb_refs`）；`test_db_migrations.py` **5 passed**（生产 / alembic 新建 /
    模型**三方同形**）。
  - **端到端落库实证（本轮最强的一条，非"未抛异常"）**：真实库现有 **2 行** `stage=notification` 快照
    （`run_id` 两个，2026-09-16 05:00/05:01 UTC），其 `kb_ids = "[]"` 且
    `kb_refs = {"state":"not_consulted","status":{},"support":[],"conflict":{}}` ——
    ⚠️ **该值与迁移的 `server_default='{}'` 不同** ⇒ 只可能来自 `snapshot_citations()`；
    且 `state` 既非 `legacy` 亦非空 ⇒ **证明确实跑的是新写入路径**（不是列存在、值仍是默认）。
    这正是 `RSH-026` 验收轮缺的那一刀（那轮只证到 `state=ready` ⇒ **不足以证明 INSERT**）。
  - **三态引用的必要性（为什么不能只存 ID 列表）**：`kb_ids == "[]"` **区分不了「没引」与「全被驳回」**
    ⇒ 必须并列一个 `kb_refs` 状态映射（`not_consulted` / `cited` / `rejected`）；迁移前的旧行标
    **`legacy`**、**不猜**成 `not_consulted`（猜 = 把"无证据"写成"证据表明没有"）。
  - **两处初版偏差自查纠正（据蓝图原文，非推理）**：**(a)** 初版把盘中约束写成**册过滤**（只放
    `KB-TRADE`），而原文是**状态过滤**（「已落地或试验中的交易纪律」）⇒ 按册过滤会连带挡掉
    `KB-STOCK-07/11/12/13/21` 等 **✅ 交易纪律**；**(b)** 初版给盘中 `enters_scoring=True` 属
    **未验证即宣称入模** ⇒ 改为全 `False` + `ablation_evidence` 必填 +
    `assert_scoring_admission_is_evidence_gated()` 机制化拦截。
- **注入自证**：**7 项真红 → 恢复全绿**。靶点 = 把状态列正则退回旧口径（删 `📎`），
  判红的是 `test_kb_routing.py` 中依赖状态解析的 7 条（含 `📎` 不得作硬规则、覆盖度恒等式、
  被漏条目的逐条回归）。另三路结构性守卫（放宽状态限 / 塞 `KB-DEC` 进打分册 /
  无证据置 `enters_scoring=True`）各有用例钉住，且别名守卫打在**源码真实字面量**上
  （正则从 `opportunity_learning.py` 抓取，不抄副本）。
  ⚠️ **另有一条「真实命中 > 人工注入」**：`doc-health` **P 交接索引**在本轮**真的判红过**
  （`账本索引已登记 RSH-027，但 docs/handoff.md 无 '## RSH-027' 条目`）——
  这是该守卫上线后的首次真实阳性，无需再造合成注入（[[KB-ENG-102]]）。
- **门禁（实测回填，勿凭记忆）**：
  - 后端 `pytest --basetemp=/tmp/… --junitxml=…` ⇒ **collect 3265 / 3189 passed / 76 skipped / 0 failed**
    （**213.74s**；8000 在跑。⚠️ `pyproject.toml` addopts 已含 `-q`，**不可再叠 `-q`** ⇒ 取数走 junitxml）。
  - **Δ 归因自洽**：较上值 `3245 / 3169` ⇒ **Δ = +20 collect = +20 passed / ±0 skipped**：
    `+18` = 新文件 `test_kb_routing.py`；`+1` = 新 GET 端点进入全量冒烟参数化
    （实测 `…[/api/picks/kb-routing-get-params91]`）；`+1` = `test_import_lint.py` 新增**业务层**模块参数化
    （**HEAD 干净检出实测 190/74 ⇒ 本轮 191/74**）。
    **非业务层模块数未变** ⇒ `skipped(76) − 2 = 74` 与上轮**逐字相同**（[[KB-ENG-97]]）。
  - 目标文件单跑：`test_kb_routing.py` **18 passed / 0 skipped** · `test_db_migrations.py` **5** ·
    `test_opportunity_learning.py` **14** · `pyflakes app tests scripts` **0**。
  - `doc-health` **全部通过**（P 项 9 条目双向闭包 · Q 项 0 冲突）· 前端 `apps/web/` **零改动** ⇒ 沿用 **603/67**。
  - ⚠️ **本轮 `doc-health` J 项真红过一次**（新文件未 `git add` 却被 handoff 点名）⇒ 见 §1 现场第三条实例。
  - **干净检出复验（= 模拟 CI 检出，`git worktree add --detach /tmp/ci-sim-027 <commit>`）**：
    `doc-health` **全部通过**；文档类守卫 8 文件 **96 passed**（`test_doc_health_*` 6 份 +
    `test_cmd_guidance_guard` + `test_doc_status_truthfulness`）⇒ **无「本地绿 / CI 红」**。
    ⚠️ 检出须取自**提交**而非 `HEAD` 工作树（J 项判定面 = `git ls-files`）。
- **遗留与下一步**：**切片 2 = 有/无 KB 影子消融**（候选召回 / Precision@K / 净期望），
  当前 `verdict` 只能恒为 `insufficient_sample`（可成交样本远低于
  `opportunity_learning.MIN_LABELS_FOR_VERDICT = 30`）⇒ 与 `RSH-026` 剩余部分（purged walk-forward、
  校准与 Champion/Challenger 影子晋级）**同因阻塞：等样本积累**，硬跑即「用不足样本装判据」。
  本切片**不含检索实现**（`kb_routing` 只给"允许引哪些"，不给"怎么找"）——
  若要接检索，须另立任务并先明确"检索失败/无命中"如何留痕。

## IMP-034 提醒链路过期断言与死代码清理（闭环）
- **账本**：`docs/retro-and-gaps.md` §6.0 `IMP-034` ｜ **日期**：2026-09-16 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：`IMP-028` 把通知中心收敛为「只推个股买点」后，**留下两类残留**：
  ① 全仓仍有多处注释自称「通知中心」/「不经 LLM 判读」——**断言与实现脱钩**；
  ② `notifications._daily_pick_item` / `_news_items` 已被端点弃用却仍有测试在测——**测试守着一个够不到的落点**。
  验收标准：断言**逐处按现实改写**（非删注释）；死代码**删除须连同其测试**，不留虚假覆盖信心。
- **改动**：
  - `backend/app/picks/pre_limit_radar.py`（2 处：模块 docstring 落点段 + `:236` 行内注释；另 `:147` `pre_limit_sweep` docstring）
  - `backend/app/picks/position_engine.py:275`（开仓留痕落点）
  - `backend/app/picks/exit_engine.py`（2 处：`_notify` docstring + `:470` 降级留痕）
  - `backend/app/services/alert_triage.py:351`（方向级事件去向）
  - `backend/app/api/routes/picks.py:292`（信号健康预警接线）
  - `backend/app/review/service.py:199`（同上）
  - `backend/app/core/config.py:104`（`notifications_news_min_score` 已空转）
  - `backend/app/api/routes/notifications.py`（**净删 127 行**死代码 + 4 个失效导入；docstring 补清理说明）
  - `backend/tests/test_notifications.py`（**净删 53 行**：1 用例 + 4 夹具）
  - `backend/tests/test_event_loop_no_block.py`（删 `GUARDED_ROUTES` 中已无对应代码的 1 条目 + 3 处注释同步）
  - `docs/summary/pick-signal-chain.md`（§G4 5→10 处并标已处理 · §G5 标已删除 · §6 两条 P1 标已实施 · §7.1 升级为实机证据）
  - **刻意没动**：`morning_brief._daily_plan` 的两处失效导入（属 `BUG-009`，**另属一项**）；`notifications` 端点响应体（`policy` / `news_min_score` 字段保留，旧客户端兼容）；`signal_health.py:217`（经核**仍成立**，不误删）；`_NOTIF_RULE_NAMES` 白名单（口径属 `IMP-028`，不在本项范围）。
- **机械证据**：
  - **断言侧实为 10 处，非登记的 5 处**。三类去向经代码取证钉死：`notifications.py:56/88/91/124`（只收 `__picks_buy_point__` + `kind=="buy_point"` + 有效 symbol/name）；`watcher.py:942`（`append_alert(target, …)`，target 来自 `brief_for_today()` ⇒ **当日简报 `alerts[]`**）+ `watcher.py:1005` 起 `repo.record_trigger(rule.id, …)`（watcher 系统规则，规则名**不在**白名单）；前端 `app/hunting/page.tsx:238/611`（「盘中提醒」）+ `lib/api/alerts.ts:91`（`/api/alerts/events` ⇒ 控制台「提醒与告警」）；方向级 → `components/market/events-tab.tsx:110` ← `/api/events/impact`。
  - **两处「疑似失效」经核为真、刻意保留**：`picks.py:292` 的「自动 action_items」确实接线（`review/service.py:149-151` → `review/strategy_health.py:168 build_signal_health_action_item`，`priority="P0" if drift else "P1"`；`review/synthesis.py` 内**无** `health` 引用 ⇒ 该链路**只能**来自这里，判据唯一）；`signal_health.py:217`「通知中心按规则名分流」机制未变。
  - **死代码可删性判据**：`notifications.py` 端点 `:184` 仅 `items = alert_items` ⇒ 三函数**无任何调用点**；`pyflakes app tests scripts` = **0**（删前删后均为 0，故不能只靠 pyflakes 判定 —— 它是**模块级**的，认不到「函数存在但无人调用」）。
  - **`notifications_news_min_score` 全仓只有 2 处引用**（`config.py:106` 定义 + `notifications.py:161` 回显）⇒ 确认**无过滤消费**。
- **注入自证**：无新增守卫（本轮为**订正 + 清理**轮）。**但门禁提供了两处真实阳性注入的等价物**（**先真红、后修**，非先绿后补断言）：
  - ① `test_event_loop_no_block.py::test_route_sync_io_calls_are_offloaded[notifications::store.list_events#5]` **真红**：报 `app/api/routes/notifications.py 找不到 asyncio.to_thread(store.list_events, active_only=False, limit=80) ⇒ 该调用点未走 to_thread` —— 即**守卫在替一个已删代码报警**。修法 = 删条目（7 条其余登记项与裸调用判据**未动**）。
  - ② `test_cmd_guidance_guard.py::test_repo_has_no_reload_guidance` **真红**：命中 `retro-and-gaps.md:163` 与本文件 `:99` 的「起栈未加 `--reload`」措辞（判据 = 同一行**同时**出现「起服务命令锚点」与 `--reload` 且无禁用标记）。这是 **`BUG-016` 轮遗留**（该轮只跑 7 个 `test_doc_*`、**未跑全量** ⇒ 文案红漏检）。修法 = **改文案**（本意在「未热重载」，命令锚点非必要信息），**不加豁免标记**（守卫 docstring 明写「把标记塞进豁免 = 判据失效」）。
- **门禁（实测回填，勿凭记忆）**：
  - 后端 `pytest --basetemp=/tmp/… --junitxml=…` ⇒ **collect 3245 / 3169 passed / 76 skipped / 0 failed**（8000 在跑；`pyproject.toml` addopts 已含 `-q`，**不可再叠 `-q`**，取数走 junitxml）。
  - **Δ 归因自洽**：较上值 `3247 / 3171` ⇒ **Δ = −2 collect = −2 passed / ±0 skipped**，两项各 −1：删 `test_daily_pick_item_ts_is_real_generation_time`、`GUARDED_ROUTES` 少一条参数化项。**非业务层模块数未变** ⇒ `skipped(76) − 2 = 74` 与上轮**逐字相同**（`[[KB-ENG-97]]`：跳过数不变即无覆盖率丢失）。
  - `pyflakes app tests scripts` **0** · `doc-health` **全部通过** · 前端 `apps/web/` **零改动**（`git status --short` 核对）⇒ 沿用上轮 **603 项 / 67 文件**，未复跑。
- **遗留与下一步**：无新增遗留。本项的**同族未清项**（登记范围外、本轮未动，**属不同授权面**）：`summary/pick-signal-chain.md` §6 的两条 **P0 建议**（`verdict` 提升为通知中心统一闸门 / 家族 B 纳入 `dispatch_alert`）——二者**均改变推送口径**，按「改进先提后做」**待拍板**。另 `BUG-016` 的可做子项（空态暴露候选数/最高档/否决原因）亦待拍板，见 `§BUG-016`。

## BUG-016 通知中心「三跳全空」根因链（可做子项③已交付，档位门①/②待拍板）
- **账本**：`docs/retro-and-gaps.md` §6.0 `BUG-016` ｜ **日期**：2026-09-16 ｜ **状态**：🟡 已取证 · **可做子项③已交付** · 档位门 ①/② 待拍板
- **缺口与验收标准**：用户问「为什么消息通知一个也没有呢,新闻也没有在里面」。
  验收标准 = **分清「设计口径 / 数据如实为空 / 系统侧失效」三种成因**，并给出可复现证据；
  **不以猜测结案**。可做部分（空态可诊断）**不改推送口径**。
- **结论（三层，机制不同）**：
  1. **新闻不在通知里是设计** —— `IMP-028` 收敛后 `_NOTIF_RULE_NAMES` 只留 `BUY_POINT_RULE`，
     端点 `notifications.py:315` 为 `items = alert_items`；`_news_items` / `_daily_pick_item` 成死代码（归 `IMP-034`）。
     新闻实际入口 = 盘面页**「事件」标签**（`market/events-tab.tsx` ← `/api/events/impact`）。
  2. **个股机会 0 条 = 如实为空，且历史上从未有 1 条** —— 买点链第一道必要条件
     `tier ∈ EXEC_TIERS = {executable, strong}` 在 **13 天 / 51 只候选上 0 命中**。
  3. **无法区分「设计生效」与「阈值不可达」** —— 0 命中本身没有区分度 ⇒ 需拍板（见下）。
- **机械证据（全部只读、可复跑）**：
  · `alert_event` 内 `kind LIKE '%buy_point%'` = **0 / 1997**（跨度 `2026-09-02 11:01:34` → `09-16 11:29:51`，覆盖 11 个交易日）
  · `alert_rule` = 5 条（`__picks_watcher__` / `__ths_reason_sentinel__` / `__sentiment_monitor__` / `__llm_gateway_probe__` / `__signal_health__`），**无 `__picks_buy_point__`**
  · `daily_pick_set` 13 天 51 只候选：档位 `None×30` + `observe×21`，**`executable`/`strong` = 0**；`score` 区间 **30.8–63.8**（线：`EXECUTABLE_SCORE=60` / `STRONG_SCORE=75`）；`buy_range` 非空仅 3 只（全部 09-14，且同为 `observe` 档）
  · 今日唯一候选 `603162`：`tier=observe` + `observation_only=True` + `buy_range=None` + `gate.stand_aside=True`（相位「退潮」、`strip_buy_range=True`）⇒ 四重否决
  · `GET /api/notifications` ⇒ `{"items":[],"count":0,"policy":"stock_opportunities_only","errors":null}`（**正常空，非故障**）
  · `GET /api/events?limit=3` ⇒ **3 条**（新闻数据在）
  · 前置逐一排除：`trade_calendar.json` 243 天且**含今天**（`source=official`）⇒ 日历门不成立；
    实测 `in_trading_window(10:00/11:00)=True`；`_today_picks_payload()` ⇒ 1 条 ⇒ 三道前置门**均不成立**
  · 归档路径可用性：对**生产库副本**（`/tmp`）调用 `archive_notification_pipeline` ⇒ `inserted=1`，`evidence.gate_reason="快照无现价（不臆造）"`，`data_state=unknown`
- **⚠️ 本轮自我更正（必须保留）**：曾把 `opportunity_decision_snapshot` **0 行**读作「买点循环未跑到判定阶段」。
  **该推断不成立**：该表由 `RSH-026` 首批迁移 `7d4e2c9a6b1f` 建立，而其 PR #13 于**今日 09:58** 才合并、
  批次二迁移 `b4f1a7c2e9d3` 于 **10:20** 合并；后端进程起栈时**未加 `--reload`** ⇒ 晨盘时段运行中的后端
  **未必带这段代码** ⇒ **0 行不能证明循环没跑**。同族教训：**把「无证据」读成「证据表明没有」**。
- **✅ 可做子项③ 已交付（2026-09-16）——「让为什么空看得见」**（**不改任何推送口径**）：
  · `backend/app/picks/notification_diagnostics.py`（**新**）：状态机**四态 + 一降级、互斥且穷尽** ——
    `no_pick_set`（上游空）/ `no_run`（链路未跑到判定）/ `ran_rejected`（跑了但全被否）/ `ran_eligible`（有通过却仍空 = 真异常）/ `unavailable`（读库失败）。
    ⚠️ **`no_run` 与 `ran_rejected` 必须分开**：前者要查调度与时段，后者要看原因；合并即把「无证据」写成「证据表明没有」（本项自我更正过的同一类错）。
  · 端点 `GET /api/notifications`：**仅在 `items` 为空时**附 `data.diagnostics`（`asyncio.to_thread` 包裹；诊断自身抛错也**回 dict**`state="unavailable"`，**不回 `None`** —— 否则「诊断坏了」与「有通知所以不诊断」在响应里**同形**）。
    **响应不变式**：`items` 空 ⇒ `diagnostics` 必为 dict；非空 ⇒ 必为 `None`。
  · 前端：`notification-drawer.tsx` 的 `NotificationEmptyState`（替换原来那句「盘中暂无通过多维筛选的个股机会」）+ `lib/api/alerts.ts` 的 `NotificationDiagnostics` 类型；
    **只在整份 payload 为空时**展示诊断（「本时段空、别时段有」不算空态，那只是切到了没内容的 tab）。
  · **两条读法纪律写进 note**（由**实测**驱动，不是免责声明）：① `reasons` 取**最新一拍**（实测同一只票 13:15「置信档 observe 不足」→ 13:21「快照无现价（不臆造）」）⇒ **不是全天分布**；
    ② 判定**按门顺序短路**（`快照无现价` → `置信档` → 红线 → 闸门 → 买区 → 涨停区）⇒ **「原因没提档位」不等于「档位已通过」**。
    ⚠️ 这两条恰好都指向本项**待拍板的档位门**，误读一次就会把 ①/② 拍错方向。
- **机械证据（③ 部分，全部实测）**：
  · 真库 `GET /api/notifications` ⇒ `state=ran_rejected` / `polls=26` / `by_decision={rejected:1}`（**按 symbol 去重，不是记录数**）/ `top_tier=observe` / 逐股原因「快照无现价（不臆造）」(603162)。
  · **实际渲染取证**（agent-browser 文本通道，非推理）：抽屉显示「本时段无通知：候选全部被否决」+ note 全文 + 「候选 1 只 · 最高档 observe · 判定 26 拍 · 2026-09-16 · 诊断于 13:26」+ 逐股一行（含代码）。
  · **反引号那处是渲染实测才发现的**：只剥 `**` 时界面会原样显示反引号 —— 读代码时它看起来「已经处理了 Markdown」。
- **注入自证 5/5 全红后还原（`diff -q` 逐字一致）**：
  · 后端 [A] 合并 `no_run`/`ran_rejected` ⇒ **2 红**；[B] 取消按 symbol 去重（改回按记录数）⇒ **2 红**。
  · 前端 [A′] 去掉「整份 payload 为空」判据 ⇒ **1 红**；[B′] 不剥 Markdown 标记 ⇒ **1 红**；[C′] 只剥星号不剥反引号 ⇒ **1 红**。
  · 接线守卫的必要性（[[KB-ENG-100]]）：纯函数用例**不经过 HTTP 层**，端点里那三行接线被删，17 项纯函数用例**照样全绿** ⇒ 另补 4 项端点级守卫（空态真调用并挂上 / 非空态**刻意不调用** / 抛错显式降级 / 两分支不变式）。
- **门禁（实测回填）**：后端 **collect 3287 / 3211 passed / 76 skipped / 0 failed**（253.61s，8000 在跑）；
  **Δ +22 机械归因** = +17（新 `tests/test_notification_diagnostics.py`）+ 4（接线守卫）+ 1（新业务模块 `picks/notification_diagnostics.py` 进 `test_import_lint` 分层参数化）；
  `skipped 76` 不变（`74 = 76 − 2` 逐字同 ⇒ 无新增非业务层文件）。
  前端 **608 passed / 67 文件**（本地与 `TZ=UTC` **一致**）⇒ Δ **+5 用例 / ±0 文件**。
  `tsc` 0 · `eslint` 0 · `pyflakes app tests scripts` 0 · `doc-health` 全部通过（含**干净检出**复验）。
- **顺带修掉两处环境隐患（本轮实测踩到，均已入册）**：
  · [[KB-ENG-105]]：**同一批次内对同一文件的两处编辑会静默丢一处**（「工具报成功 ≠ 落盘」）—— 本轮撞**两次**，
    两次都靠**随后的判据**（用例失败 / 读回文本）才发现；两次若按「只是改文案所以不用跑」处理，会留下「旧行为 + 新文档」的自洽组合，**门禁全绿也抓不到**。
  · [[KB-ENG-106]]：`lsof -ti tcp:<port>` 收的是**所有持有该端口 fd 的进程（含客户端）** ⇒ 用 `lsof -ti tcp:8000 | xargs kill -9` 重启后端会**连带打死 Next dev server**（本轮撞**三次**）。
    实测口径差：`lsof -ti tcp:8000` ⇒ **4 pid**（uvicorn + Next dev + 2 worker）；加 `-sTCP:LISTEN` ⇒ **1 pid**。`AGENTS.md` §1 的按端口 kill 命令已补 `-sTCP:LISTEN`。
- **遗留与下一步**：
  · **待拍板（涉交易信号口径，本轮仍未动）**：① 保持 `{executable, strong}` 门并在界面明示「可能长期不触发」；
    ② 下调档位门（**改推送口径，风险 = 推噪音**）；③ 已按 ③ 交付可诊断面 ⇒ **本轮起据实评估有了数据来源**，但 ①/② 仍须用户拍板。
  · 死代码清理已归 `IMP-034`（闭环）；通知口径本身不改（`IMP-028` 是设计决策）。

## IMP-033 提醒落点统一为个股详情弹窗（判读全文改由「判读」入口保全）

- **账本**：`docs/retro-and-gaps.md` §6.0 `IMP-033` ｜ **日期**：2026-09-16 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：同一条个股提醒里，**点行体**开的是通用判读弹窗、**点「行情 ↗」**开的是
  该股详情弹窗 ⇒ **一行两个入口落两个落点**；而悬浮球（`openSymbolDetail`）与猎场（`StockLink`）
  早已统一到**个股详情**（[[KB-ENG-92]]「个股在任何页面就地弹窗」）。
  本项要消除的正是这类"**同一行点正文与点代码弹出不同内容**"。
  验收标准（账本原文）：**以实际渲染为准**（`agent-browser` 文本通道读弹窗内容），
  并补「**同一 symbol 三处入口落点相同**」的用例。
- **改动**：仅 **1 个源文件 + 1 个新测试文件**（纯前端）。
  · `components/notifications/notification-drawer.tsx` —— ① 行体 `onClick` 改为
    `if (item.symbol) openSymbolDetail({ symbol }) else openDetail(judgmentPayload(item))`；
  ② 新增**单一构造函数** `judgmentPayload(item)`（行体与「判读」入口共用，防两处口径漂移）；
  ③ 行右侧新增「判读」入口（`data-testid=notification-judgment`，置于行体 `<button>` **之外**、
    与 `StockLink` 并列——嵌进 `<button>` 是非法 HTML 且点击语义互吞）；
  ④ 文件头补「落点口径」说明段。
  · **刻意没动**：`symbol-detail-modal.tsx` 的**既有全局落点设计**（见下「口径澄清」）、
    后端任何文件、`detail-modal` 的通用弹窗语义。
- **机械证据**：
  · 前端全量 **603 项 / 67 文件**（较上轮 `598/66` **+5 / +1**，增量**恰等于**本项新增用例文件）。
  · 通知目录单跑 **10 passed**（`notification-drawer.test.tsx` 5 + `notification-row-landing.test.tsx` 5）。
  · UI 实测 4 项（`agent-browser` 文本/DOM 通道，非推理）：
    ① 工作台点行体 ⇒ `?symbol=603330`、右栏渲染「天洋新材 SH.603330 9.50 +5.56%」；
    ② 工作台点「行情 ↗」⇒ 先切到 `600105` 做**对照**，点后回到 `603330` ⇒ **与行体同落点**；
    ③ `/market` 点行体 ⇒ `[data-testid=symbol-detail-modal]`、`aria-label="个股详情 603330"`，
       **URL 停在 `/market` 不变**（未跳转）；
    ④ 「判读」⇒ 通用弹窗含「分类 确认 / 评分 78 / AI 判读（建议关注）：放量突破前高，量比 2.3」。
  · 层级取证：`document.elementFromPoint(640,400)` 命中的元素 **属于个股弹窗、不属于抽屉**
    ⇒ 弹窗确实盖在抽屉之上（不是被遮罩挡住而"看起来没反应"）。
- **注入自证**：3 路，读数走 vitest `--reporter=json`（**精确条数 + 点名**，不用子串猜）。
  `[A]` 行体改回无条件 `openDetail` ⇒ **2 红**；`[B]` 摘掉 `data-testid="notification-judgment"`
  ⇒ **2 红**；`[C]` 无代码也强行开个股弹窗 ⇒ **1 红**。
  每路先断言「靶点被改动」（`needle in original` 且 `mutated != original`）；还原后 `sha256`
  逐字一致、复跑全绿。**未放宽任何判据、未 skip、未删用例**。
- **门禁**：前端 `tsc --noEmit` **0** · `eslint` **0 error / 0 warn** ·
  `vitest` 本地时区 **603/67（602 passed · 1 failed）**、`TZ=UTC` **逐字一致 603/67**（⇒ 新增用例
  **不含时区敏感断言**）· 后端 pytest **3171 passed / 76 skipped / 0 failed**（collect 3247；
  与上一轮**逐字相同**——本轮无后端代码与后端测试改动）· `pyflakes app tests scripts` **0** ·
  `doc-health` **全部通过（19 项，0 待处理）**。
  ⚠️ **后端 pytest 曾红过一轮，根因是「门禁输入面 ≠ 代码面」的第二次命中**：
  `doc-health` J 项 + `test_doc_health_anchors.py::test_real_repo_has_no_dead_doc_anchor`
  报 `docs/handoff.md:88 → notification-row-landing.test.tsx（全仓不存在）`——
  J 的判定面是 `_repo_basenames()`，取自 **`git ls-files`**（`doc-health.py:914-932`）、
  比对按 **basename**（`:1042`）⇒ **本轮新建、尚未 `git add`** 的测试文件在本地**就是"不存在"**。
  **修法 = `git add` 该文件**（提交后在 CI 里当然存在），**不是改文档措辞、更不是登记豁免**；
  复跑后 `doc-health` 与后端全量**双绿**。⚠️ 这与 [[KB-ENG-95]]（本地绿 / CI 红）**方向相反、
  根因相同**——都是「判定面 = git 跟踪清单」在**本地工作区 ≠ 检出内容**时的两种表现；
  已补记进 [[KB-ENG-102]]。**通例：新增文件与引用它的文档在同一轮时，先 `git add` 再跑门禁。**
  ⚠️ 那 **1 failed 是 `BUG-010`（D 档观察项），不是本轮回归**：
  用例 = `components/agent/markdown-view.test.tsx::docs/ 下全部 md 均可渲染完成`（固定 5s 墙钟）。
  **归因（对照跑，非推理）**：单独跑该文件 **10 passed / 2170ms**（其中 docs 渲染用例 **1897ms**，
  阈值 5000ms）⇒ 余量充足；失败只出现在**全量并发**时，宿主实测 **load 39.16 / 8 核**。
  按原判**不改判据、不放宽阈值**（同 `BUG-010` 行与 `AGENTS.md` 门禁段）。
- **口径澄清（须让接手者知道，别当成 bug）**：在 `/workbench` 上 `openSymbolDetail` **按既有
  全局设计**走**右栏页内切换**（`router.replace`，不叠弹窗）而非弹窗——依据是
  `symbol-detail-modal.tsx` 头注：右栏本就是**同一份** `StockDetailPanel`，再叠一层冗余，
  且会让「左栏点自选 = 切右栏」与「搜索框选股 = 弹窗」两条路径**行为分叉**。
  ⇒ 工作台上的表现是「**不跳转 + 就地看到该股详情**」，**弹窗形态出现在非工作台页面**；
  用户诉求的「不跳转到提醒页面」在两种页面形态下**均满足**。
  若要求工作台也强制弹窗，属**跨模块 UI 口径变更**（会与全站其余入口分叉）⇒ **另立拍板项**。
- **遗留与下一步**：`IMP-034`（提醒链路过期断言与死代码清理）——本轮**刻意未顺手改**，
  避免把"清死代码"与"改落点"混成一锅（其中 `morning_brief._daily_plan` 的两处失效导入
  另属 `BUG-009`）。另：`IMP-031` / 本项共同梳理出的链路断点见 `summary/pick-signal-chain.md`。

## IMP-031 AI 判读气泡点击就地打开个股详情弹窗（不再跳转提醒页）

- **账本**：`docs/retro-and-gaps.md` §6.0 `IMP-031` ｜ **日期**：2026-09-16 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：悬浮球「AI 判读提醒」气泡的主按钮原为 `router.push("/agent?tab=alerts")`
  ⇒ 用户**正看着某个页面**时点一条「`603330` · 某某 · 理由」的提醒，会被**连根拔到告警页**；
  而气泡正文已经说明是哪只股。**验收标准**：点个股提醒 ⇒ **就地弹出该股详情弹窗、且不跳页**；
  无代码的判读不静默失败；多条时不因收敛主按钮而丢失「看全量」的能力。
- **改动**：
  - `apps/web/components/assistant/floating-assistant.tsx` —— 主按钮改
    `if (b.symbol) openSymbolDetail({ symbol: b.symbol })`，无 symbol 才回退告警页（防御性分支）；
    新增 `data-testid="assistant-alert-all"` 的「全部 N 条」入口（`bubbles.length > 1` 时出现）；
    同步修 3 处过期文案（气泡注释 / 文件头 docstring 的「实体跳转」条目）。
  - `apps/web/components/assistant/floating-assistant-alert-bubble.test.tsx`（**新建，5 例**）。
  - `apps/web/components/hunting/intraday-sections.tsx` —— 1 处过期 title 文案
    （「点击进工作台看该股详情」→「点击看该股详情」：2026-09-15 详情弹窗化后前者已不成立）。
  - **刻意没动**：告警页 `/agent?tab=alerts` 本身、后端 `pending_bubbles` 口径、下发通道与判读逻辑。
- **机械证据**：
  - 前端门禁 `vitest` **598 passed / 66 files**（较上轮 593/65 **+5 项 / +1 文件**，恰等于新测试文件；
    本地时区与 `TZ=UTC` **均 598/66**）；`tsc --noEmit` **0 error**；`eslint .` **0 error / 0 warn**。
  - 气泡数据源实测路径：`floating-assistant.tsx:277` → `getAgentBubbles(5)` →
    `GET /api/agent/triage/pending`（`routes/agent.py:100`）→ `alert_triage.pending_bubbles`；
    轮询间隔 **30s**，失败静默清空（不打扰）。
- **注入自证 2/2**（脚本 `/tmp/inject_bubble_click.py`，改后 `sha256` 逐字还原并复绿）：
  - `[A]` 主按钮改回**无条件** `router.push` ⇒ **2 条红**，点名「打开」「两条路」；
  - `[B]` 去掉无 symbol 的兜底分支 ⇒ **1 条红**，点名「反向对照」。
  - ⚠️ **首版脚本两个 bug 导致「假自证」，已沉淀教训**：① vitest 汇总行含 ANSI 转义 ⇒ 正则读不到
    （加 `NO_COLOR=1` + `strip_ansi()`）；② 判据写成「子串出现在整份输出里」而输出**同时含通过与失败
    用例名** ⇒ 恒真。改为**从 `Tests\s+(\d+)\s+failed` 取计数、并要求红的条数与点名都精确符合预期**。
- **门禁**：
  - 前端 `tsc --noEmit` **0 error**；`eslint .` **0 error / 0 warn**。
  - `vitest run`：**598 项 / 66 文件**，其中 **597 passed / 1 failed**。那 1 failed =
    `BUG-010`（D 档已知墙钟敏感项，**非本轮引入**，归因见 `§1 现场`）。
    增量 `+5 项 / +1 文件` **恰等于**本轮新增用例数 ⇒ **新增 5 条全部通过**。
    ⚠️ 本条**不写成「全绿」**：如实记录既定事实，避免下一位把已知项误读成回归。
  - 后端 `app/**` 本轮**未改动**，本轮修复后本地全量复跑 **3171 passed / 76 skipped / 0 failed**
    （202.05s，8000 在跑）。⚠️ 但 **「没改 `.py`」≠「后端门禁不会红」**——见下方 CI 判红。
  - **CI 判红与修复（真回归，不是假红）**：PR #16 首跑 `35050416236` 的 **backend job 红**，
    唯一失败 = `tests/test_doc_status_truthfulness.py::test_summary_status_claims_point_to_ledger`，
    报 `docs/summary/pick-signal-chain.md: 命中 ['未做'] 但无 §6.0 指针`。
    该守卫即 `GOV-002` **判据 2**（结构约束：`docs/summary/*.md` 谈状态 ⇒ 必须带账本 §6.0 指针），
    其**输入是文档** ⇒ **只改文档照样让后端红**。
    **根因 = 本轮门禁口径取窄**：据「后端零改动」推定「不必跑后端门禁」，漏掉"后端有一类扫 `docs/` 的守卫"。
    **修法 = 补指针**（按该守卫既定惯例在文档头部加权威指针，并说明本文的「未做」指**取证边界**），
    **不改守卫、不登记豁免、不放宽判据**。commit `8e55b73`；同时把该纪律写进 `AGENTS.md` §1 防复发。
    ✅ **该守卫的这次真实判红本身就是最强的「注入自证」**——它确实抓得住这个形态，无需再造合成注入。
    复验：本地 `7 passed` ＋ **干净检出**（`/tmp/ci-sim`）`7 passed`；`doc-health` 本地与干净检出**双验全通过**。
  - `doc-health` **19 项全部通过**（P 交接索引 5 条双向闭包 / Q 档位一致 0 冲突），
    并已用**干净检出**（`git worktree add --detach /tmp/ci-sim HEAD`）复验 ⇒ 判定面 = CI 检出内容。
- **遗留与下一步**（本项顺带产出的发现，**均已另登记、未在本项动手**）：
  - 本修正使悬浮球落点为 `symbol` 弹窗，而**通知抽屉行体仍是 `generic`** ⇒ 同类功能落点不一致 ⇒ **`IMP-033`**。
  - 链路梳理结论落在 **`docs/summary/pick-signal-chain.md`**（新编号 **SM-08**）：
    `append_alert` 存在**两个家族**（经 `dispatch_alert` 入事件流 / 引擎直写晨报不入事件流）⇒
    猎场「盘中提醒」是**混合视图** ⇒ **`IMP-032`**；判读 `verdict` **只作用于悬浮球、不作用于通知中心**
    ⇒「已降噪」仍被推送 ⇒ **`BUG-015`**；另有 5 处过期断言 + 2 个死代码函数（带 3 个测试）⇒ **`IMP-034`**。
  - ⚠️ `BUG-015` / `IMP-032` 均属**口径变更**（通知口径 / 事件 kind 与 push_policy），按「改进先提后做」**留 B 档待批**。

## GOV-015 账本档位一致性守卫（含 `BUG-014` 销账）

- **账本**：`docs/retro-and-gaps.md` §6.0 `GOV-015` ｜ **日期**：2026-09-16 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：接手第一轮按账本「状态档 → 优先级 → 编号」挑任务时，`BUG-014`
  （A 档 P1 里编号最小）排在首位；但它的**代码修复与两条守卫**早在 `80c6428` 就已交付。
  实查发现它**同时处于三种状态**：A 可做档仍列为「可做 P1」（行文还是「**修法**：…」的待办语气）·
  F 闭环登记档**没有它** · `§6.0-H` 已标 `✅ 闭环`。⇒ **已闭环项留在未完成档 = 谎报可做**，
  且因排序规则，**发现成本被推给下一个执行者**。
  验收 = ①把它销账（移入 F 档，**号码保留**）；②把这一类失真**变成机检**，且守卫**注入后必须真的变红**。
- **改动**：`scripts/doc-health.py` 新增 **Q 档位一致性**（`ledger_stage_tables` /
  `ledger_handoff_index_status` / `check_ledger_stage_consistency` + `main()` 接入与明细打印）；
  `docs/retro-and-gaps.md` 把 `BUG-014` 行从 A 档**移入** F 档并补闭环证据，新增 `GOV-015` 行与
  `§6.0-H` 索引行；`docs/handoff.md` 本条 + §1 现场 + §2 一览；新增自证测试
  `backend/tests/test_doc_health_ledger_stages.py`；`test_doc_health_anchors.py` 的中性打桩表补一项
  （**不补即被该文件的 AST 反查守卫点名报红**，见下）。**刻意没动**：D/E 档与 B 档的既有划销行、
  任何任务状态口径、任何代码/数据行为。
- **判据（双向，两个方向对应两种真实漏法）**：
  ① **谎报可做** —— 已闭环（F 档有行 **或** `§6.0-H` 标 ✅）却仍在 A–E 档**未划销**；
  ② **闭环记录不完整** —— `§6.0-H` 标 ✅ 闭环但 F 档无该行（即本次 `BUG-014` 的形态；
  只判方向一会漏掉「A 档删干净了、F 档忘了加」这种半成品销账）。
  豁免：行内**划销**（`~~`，B 档已有 3 例）＝在案留痕；`🟡 部分闭环` **不算闭环**
  （部分闭环按规则**应留在原档**，如 `RSH-026`，把它判红会逼人做假账）。
- **机械证据**：修账本**之前**跑 `doc-health` ⇒ `[FAIL] Q 档位一致性`，**恰好两条、两条都点名
  `BUG-014`**（方向一 + 方向二），且 `RSH-026` **未**被误报；修账本**之后**复跑 ⇒ `[OK ]`、
  `结论：全部通过`。全表复核（脚本枚举 §6.0 五档 + F 档 + `§6.0-H`）：修复前**冲突总数 = 1
  （仅 `BUG-014`）**，修复后 **0**。
- **守卫自证（4 路注入 + 1 条实现期缺陷）**：`[A]` 把已闭环项塞回 A 档 ⇒ 方向一判红并点名；
  `[B]` 删掉 F 档该行（只留 `§6.0-H` 的 ✅）⇒ 方向二判红并点名；
  `[C]` 把 A 档标题改名 ⇒ **保险丝**判红（"守卫没有判定面" ≠ "账本没问题"）；
  `[D]` 摘掉 `§6.0-H` 小节 ⇒ 保险丝判红。另：`test_doc_health_anchors.py` 的 **AST 反查守卫**
  在我新增 `check_ledger_stage_consistency` 而**未登记进 `_NEUTRAL`** 时**真命中并点名**
  （`main() 调用了未打桩的检查 [...]`）—— 这是 `GOV-010` 那颗钉第二次发挥作用。
  ⚠️ **实现期抓出的一个真缺陷**：`§6.0-H` 索引表紧跟 F 档、形态同为 `| ID | … |`，
  首版**没收扫描边界** ⇒ 索引行被当成"F 档行的延续"、索引里每个 ID 都变成闭环记录 ⇒
  `RSH-026`（H 里是 `🟡 部分闭环`）被**误判**成已闭环。已加边界并在自证文件里钉住该形态。
- **门禁（全部实测回填，2026-09-16）**：
  · 后端 `pytest --basetemp=/tmp/pytest-basetemp` ⇒ **3171 passed / 76 skipped / 0 failed**（collect **3247**，340.5s）。
    增量机械核验：`3247 − 3235 = **+12**`，**恰等于**新文件 `test_doc_health_ledger_stages.py` 的
    `--collect-only` 计数 **12**；`test_doc_health_anchors.py` 同轮**只补 `_NEUTRAL` 一项、零新增用例**；
    `skipped` 不变（本轮**未新增 `app/` 非业务层模块**，`scripts/`+`tests/` 不进该口径）。
  · 前端 `tsc --noEmit` = 0 error；`eslint .` = 0 error / 0 warn；
    `vitest run` **593 passed / 65 files**，`TZ=UTC` 复跑 **593/65**（两者一致）。
  · `python3 scripts/doc-health.py` = **全部通过**（`P 交接索引` 4 条全登记且带反链；`Q 档位一致性`
    未完成档 69 行 ⇄ 闭环记录 F 档 24 条 + `§6.0-H`，**0 处冲突**）。
  · `pyflakes app tests scripts` = 0。
  ⚠️ **一条被判读为"非回归"的红（记账于此，防下一位误判）**：后端全量与前端全量**并发**跑时，
  `components/agent/markdown-view.test.tsx::docs/ 下全部 md 均可渲染完成` **4 轮中 3 轮**报
  `Test timed out in 5000ms`（592/593）；**解除并发后 ×3 全绿**。这正是账本 **`BUG-010`**（D 档观察项）
  记录的形态——判据挂在**墙钟**上、与 `BUG-008` 同族。**不单点放宽超时**（那只是把阈值往上挪），
  本次仅**补记触发条件**（CPU 竞争）与门禁实操建议（两道全量**不要并发**；CI 两个 job 独立 runner 不受影响）。
- **遗留与下一步**：无新遗留。账本 A 档现存最高优先级为 **`RSH-027`**（场景化 KB 路由与消融，P1）；
  `OPS-001` 仍属**未确证的保险措施**（复测须真实盘中冷启动对照，见 §6.7-C3）。

## RSH-026 个股机会学习闭环（两批已交付，本项仍开放）

- **账本**：`docs/retro-and-gaps.md` §6.0 `RSH-026` ｜ **日期**：2026-09-16 ｜ **状态**：🟡 部分闭环（本项**仍开放**）

### 第二批：成本后净收益 + 可成交性判定 + 样本门禁（2026-09-16）

- **缺口与验收标准**：第一批的 `return_pct` 是**毛收益**（决策时点价 → D0 收盘、零成本），
  却被当作可评估的收益；封板买不到的机会与"零收益"混在一起；且「样本不足不得转正」
  在账本里**只是注释文案、无任何代码强制力**。验收 = ①成本口径**同源于**模拟交易引擎、
  不得另立费率；②不可成交**不得记 0**；③毛/净期望并列时**分母必须同源**（否则差额读成负成本）；
  ④样本不足时**代码必须拒绝给结论**；⑤既有 `label_trade_date` 四键返回契约与
  `return_pct` 语义**零变更**（既有测试对其用全等断言）。
- **改动**：`picks/opportunity_learning.py` 新增 `round_trip_net_pct`（净收益定义式）/
  `assess_fill_state`（可成交性）/ `opportunity_scorecard`（度量层）+ 常量 `COST_MODEL_VERSION`
  / `MIN_LABELS_FOR_VERDICT`；`models/opportunity_learning.py` 加 3 列（`fill_state` / `cost_pct`
  / `net_return_pct`）；迁移 `b4f1a7c2e9d3`（`down_revision = 7d4e2c9a6b1f`，全程 `op.*`）；
  `picks_intraday.py` 加 `GET /api/picks/opportunity-scorecard`；测试 7 例。
  **刻意没动**：`return_pct` 语义、既有四键返回契约、选股逻辑与 `ALGO_VERSION`、
  以及 `paper/engine.py` 的费率常量本身（本模块只**消费**它）。
- **机械证据**：成本按 10 万元名义本金折整手 —— 实测 10.0→10.5 元：`qty=10000`、
  成本率 **0.1058%**、净 **4.89%** < 毛 **5.00%**；**平盘（毛 0.00%）净收益 −0.10%**
  （"看似保本实则亏成本"的可证伪点）；同分母差额 **0.11pp ≈ 成本率**。
  **三方同形**：用 `test_db_migrations.py::_schema_drift` **直接跑生产库**（非推理）⇒
  两张表 drift 逐项为空；生产库版本 `b4f1a7c2e9d3`（应用前已做在线一致备份）。
  门禁增量机械核验：新增 `def test_` **7** / 删除 **0**，collect **3227 → 3235**
  （**+8 = 7 新用例 + 1 新 GET 端点自动进全量冒烟参数化**），skipped **76 不变**。
- **注入自证（4/4 全部判红并点名，还原后复绿）**：`[A]` 可成交性判据改恒真；
  `[B]` **成本未真的扣**（净收益直接赋毛收益、成本记 0）——⚠️ 首轮判绿，见下；
  `[C]` 样本门禁下限降为 0；`[D]` 删同分母毛期望 `gross_on_fillable_pct`。
- ⚠️ **注入自证抓出的判据盲区（首轮 `[B]` 判绿）**：原有用例只测**纯函数**
  （`round_trip_net_pct` 的数值对不对），**没有守卫「`label_trade_date` 真的调用它并落库」的接线**——
  把接线换成 `net = ret, cost = 0.0` 时，纯函数用例**全绿**。已补接线守卫
  `test_label_trade_date_actually_wires_net_return_into_the_row`（判据取**关系式**
  `net < gross` / `cost > 0` / 与独立复算相等，非硬编码数值）⇒ 该路随即判红。
  **教训：纯函数测过 ≠ 接线正确**；注入靶点必须落在**被判对象的通路上**，
  否则测的是「零件合格证」而不是「整机通电」。
- **门禁**：后端 `3159 passed / 76 skipped / 0 failed`（182.33s，8000 在跑）；前端
  `593 passed / 65 文件`（本轮前端**零改动**）；`tsc 0` / `eslint 0`；`pyflakes 0`；
  `doc-health` 全部通过。
- **遗留与下一步**（留在账本 `RSH-026` 行，**不另立**）：purged walk-forward、
  Champion/Challenger 影子晋级（二者均**需样本积累**，当前可成交样本远低于下限）。
  ⚠️ **`d0_close` 标签本身受 T+1 约束而不可实现**——当日买入当日不可卖 ⇒ 它只衡量
  **信号方向**；「可实现收益」需改用 D1 开盘入场口径，**属独立任务、未在本批做**。

### 第一批 + 独立验收轮（2026-09-16）

- **缺口与验收标准**：选股主线的准确率提升缺**可回放的证据**——决策当时的事实没有逐股留档、

  结果没有独立标签，因此「改进了没有」无法机械判定。验收 = ①候选/硬门/精排/通知四层逐股
  point-in-time 快照可离线重放；②结果标签独立成表、覆盖率可算；③**缺价不得造 0**；
  ④被过滤与 `unknown` 标的同样留证（否则"没被选中"的原因永久丢失）。
- **改动**：新增 `backend/app/models/opportunity_learning.py`（两张 ORM）、
  `backend/app/picks/opportunity_learning.py`（归档 / 重放 / 标签 / 覆盖率）、
  迁移 `7d4e2c9a6b1f`、`backend/tests/test_opportunity_learning.py`（7 例）；
  接入 `picks_intraday.py`（两层端点 + `decision_evidence`）、`buy_point.py`（通知链归档，
  `asyncio.to_thread` + 记日志，**学习证据不得阻塞通知**）、`review_intraday.py`（盘后写 `d0_close` 标签）、
  `tradability.py`（逐股 `audit` 事实，修掉原先 `continue` 的静默丢证）。**刻意没动**：选股逻辑与
  `ALGO_VERSION` 一律未改（纯证据层，零口径变更）。
- **机械证据**：alembic 链 head 唯一（`7d4e2c9a6b1f`）、bases（`91f8ea3c3a3e`）；全链实测
  落库 5 行 → 同 run 重跑 `inserted=0`（幂等）→ `d0_close` 标签 **+5.00%** → 离线重放
  `mismatches=0` → `label_coverage=1.0` → 四层阶段键齐备。真实 app 集成：
  `GET /api/picks/intraday-opportunities` 的 `decision_evidence.state=ready`、`_candidate_audit`
  **未泄漏进响应**、`/opportunity-learning` 快照数与实际落库逐字一致、未知 `run_id` **404**。
  ⚠️ **诚实声明**：集成跑在 09:00 盘前、当日无候选 ⇒ 归档 **0 行**，`ready` 只证明未抛异常、
  **不证明 INSERT**；INSERT 由上述全链探测补上（走同一 `archive_records`）。
- **注入自证**：`test_db_migrations.py::test_opportunity_learning_tables_match_their_models`
  在「模型写成唯一索引」的形态下会断红（该形态即修复前的真实形态）。
- **门禁**：后端 `3150 passed / 76 skipped / 0 failed`；新增 7 例见上。
- **遗留与下一步**（均留在账本 `RSH-026` 行，**不另立**）：purged walk-forward、
  手续费/滑点/不可成交成本、Precision@K / 净期望 / 校准、Champion/Challenger 影子晋级。
  ⚠️ 同轮发现的全库 schema 偏差（27 张公共表里 11 张）已登记 `GOV-013`，**不在本项范围**。

## BUG-014 迁移把表建到默认库而非迁移目标库（已闭环）

- **账本**：`docs/retro-and-gaps.md` §6.0 `BUG-014` ｜ **日期**：2026-09-16 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：`6f2ab91c4d70`（theme 三表）与 `c8d51f2e9a4b`（real_position 两表）在
  迁移里用 `get_engine()` 取**默认库** engine 建表，而 `run_migrations(engine)` 的契约是
  「在**传入的 engine** 这条连接上建全库」（`backend/app/core/migrations.py` docstring 明文承诺
  "不会把表建到别处"）。验收 = ①全新库必须含这 5 张表；②改法只改「建在哪条连接上」，
  **表结构与内容零变化**（⇒ 对已应用该迁移的生产库零影响）；③补上能防复发的守卫。
- **改动**：`migrations/versions/6f2ab91c4d70_theme_catalog_tables.py` 与
  `c8d51f2e9a4b_real_position_tables.py` 的 `upgrade()` / `downgrade()` 改走 `op.get_bind()`；
  `c8d51f2e9a4b` 的 `downgrade()` 同时去掉自开的 `engine.connect()` + `commit()`（会在 alembic
  事务外另开连接）。两文件均留注释说明「为什么不能这么写」与**同族历史**（第 3、4 例）。
  **刻意没做**：不改表结构、不改迁移链、不回写生产库。
- **机械证据（修复前后同判据对照）**：跑 `run_migrations` 到全新临时库 ——
  **修复前 30 张表（缺 `theme` / `theme_member` / `theme_override` / `real_trade` /
  `real_position_override`）**，**修复后 35 张表（5 张全在）**，其余表集合逐字相同；
  全程**默认库 `data/ashare.db` 表集合零变化**（无副作用泄漏）。升降往返亦通过：
  `upgrade 6f2ab91c4d70 → downgrade 3a73e4416725`、`upgrade c8d51f2e9a4b → downgrade 9c4d7e2a1b3f`
  两路均「建得出、拆得掉」。
- **注入自证（2/2）**：把 `6f2ab91c4d70` 的 `op.get_bind()` 改回 `get_engine()` ⇒
  `test_fresh_database_created_at_baseline` 与 `test_migration_scripts_never_use_the_default_engine`
  **同时断红**（实测 `2 failed`），判定面确实覆盖该缺陷形态。
- **守卫（2 条，判据不同源）**：① **行为回归**——全新库必须含上述 5 表
  （`test_db_migrations.py::test_fresh_database_created_at_baseline`）；
  ② **结构守卫**——扫描 `migrations/versions/*.py` 的 **AST**，禁止任何 `get_engine()` 调用
  （`test_migration_scripts_never_use_the_default_engine`）。⚠️ **必须用 AST 而非正则**：
  本仓 4 个迁移文件的**解释性注释里字面写着 `get_engine()`**（正在说明"为什么不能这么写"），
  字符串扫描会把这 4 处误判为违规、守卫一上线就红，随后必然被放宽掉。
- **门禁**：后端 `3151 passed / 76 skipped / 0 failed`（较 3150 增量 +1 = 新增 AST 守卫；
  5 张表断言并入既有用例、不新增用例数）；`pyflakes 0`；`doc-health` 全部通过。
- **遗留与下一步**：无。同域的全库 schema 偏差清账是独立任务，见账本 `GOV-013`。

## GOV-014 交接机制：任务 ⇄ 账本 §6.0-H ⇄ 明细条目的双向索引（含守卫自证抓出的两处盲区）

- **账本**：`docs/retro-and-gaps.md` §6.0 `GOV-014` ｜ **日期**：2026-09-16 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：交接只靠对话历史 ⇒ 换人 / 隔天接手必须回溯上下文，且「谁做到哪」没有唯一出口。
  验收 = ① 有**明细层**（做了什么 / 凭什么算完 / 门禁多少 / 还剩什么，字段固定）；
  ② 账本清单层与明细层**双向索引**；③ 该闭包由门禁**机械判红**（只写在 md 里的规则会自我淘汰）；
  ④ 守卫自身经**注入自证**；⑤ 流程**固化为技能**（不用说就知道、可快速执行）。
- **改动**：① 新建 `docs/handoff.md`（§0 七字段模板 / §1 现场 / §2 条目一览 / 逐任务条目）；
  ② 账本新增 **§6.0-H 交接索引**小节；③ `docs/INDEX.md` 编目 `AG-07` + 主题路由 `T5` + 任务动线补指针；
  ④ `scripts/doc-health.py` 新增 **`P 交接索引`** 项（双向闭包 + 反链 + 三道 fail-loud 保险丝）；
  ⑤ 新建技能 `ashare-task-handoff`；修正技能 `ashare-ledger-continue` 的**两处过期内容**
  （仍教「推 `develop` → 开 PR 到 master」⇒ 已改 `codex/*` 分支 + 满足 6 条件自动合并；
  「先回报、**用户确认后再动手**」⇒ 与用户的**连续执行授权**矛盾）。
  **刻意没动**：选股 / 交易 / 撮合逻辑一行未改；§6.0 既有任务行未改。
- **守卫的两个真实盲区（由注入自证抓出，非推演）**：首轮四路注入里**两路判绿**——即那两路是摆设。
  根因是**同一类错**：判据用「词边界 `\b`」与「子串包含」表达"是同一个东西"，而 `-` 既是任务 ID
  的分隔符、又是非词字符、又是小节名 `§6.0-H` 的合法字符 ⇒ 多一个后缀仍被认作命中：
  · `## BUG-014-X` 里 `\b` 在 `4` 与 `-` 之间成立 ⇒ 仍算「条目存在」，且 `ho_missing` / `ho_unindexed`
    走**同一套解析** ⇒ **两路同时失守**；· 回链写成 `§6.0-HHH` 仍"包含" `§6.0` ⇒ 真丢回链反而判绿。
  **改法**：一律后向断言 `(?!\[\w-\])`，**禁 `\b`、禁 `in`**。顺带修报告层：保险丝原把哨兵串塞进
  差异列表 ⇒ 打印出「账本索引已登记 （小节缺失），但 handoff 无该条目」这种自相矛盾的话 ⇒
  改为**第 4 个返回值专载保险丝说明**并单独打印（**fail-loud 文案必须比正常分支更清楚**）。
  另修注入脚本自身：`[B]` 首版 `replace("§6.0-H", …, 1)` 打的是**文件头部**那处 `§6.0-H`，
  **根本没碰条目正文**——「注入了」≠「注到了判定面上」⇒ 每路注入先 `assert mut != orig`。
- **机械证据**：`doc-health` **P 交接索引** `[OK ]`（`账本 §6.0-H ⇄ docs/handoff.md 双向闭包（条目 3 条，
  全部登记且带反链）`）；全套 `doc-health` 全部通过；`pytest tests/test_doc_health_anchors.py
  tests/test_doc_health_tables.py` 全绿；KB 条目 `KB-ENG-99` 已入库（`C-KB 条目超长 0 条`、
  `G KB 孤儿 0 条`）。**守卫三道保险丝**（handoff 文件缺失 / 账本 §6.0-H 小节缺失 / 条目数为 0）
  **判红不跳过**。
- **注入自证（4/4，全部判红并点名，还原后复绿）**：`[A]` `## BUG-014` → `## BUG-014-X` ⇒
  `ho_missing` 点名；`[B]` 删条目回链 ⇒ `ho_noback` 点名；`[C]` 账本加幽灵行 `OPS-999` ⇒
  `ho_unindexed` 点名；`[D]` 小节改名 `6.0-HHH` ⇒ fail-loud 判红且给出保险丝专用文案。
- **门禁**：后端 `3151 passed / 76 skipped / 0 failed（189s，8000 在跑）`（8000 在跑前提）；前端 `593 passed / 65 文件（`tsc 0` / `eslint 0`）`；`pyflakes 0`；`doc-health` 全部通过。
- **遗留与下一步**：无。（后续每个任务都按本条目同一形态登记：账本 §6.0-H 一行 + 本文件一条。）

## BUG-020 行情身份与指数路径已修，失败语义仍待闭环
- **账本**：`docs/retro-and-gaps.md` §6.0 `BUG-020` ｜ **日期**：2026-09-17 ｜ **状态**：🟡 部分闭环
- **本轮题材切片（2026-09-17）**：`codex/theme-member-write-guard`；从最新 master 建工作区后复用 PR #24，按当时 §1 用户额度约束保留本地提交；最新授权已恢复，现与 IMP-041 集中交付。
- **缺口与验收**：HTTP 200 业务错误被解析成空集，可能删除旧成员并推进 synced_at；失败必须保留两者，有效增删与空集仍需正确写入。
- **改动**：fetch_members 在写事务前校验 code、数据就绪时间戳、成员数组和有效唯一代码；sync_stale_members 仅返回成功写入项，失败不计成功或空成分，分别记录日志。
- **机械证据**：独立 SQLite 文件夹具，修改前 18 failed / 2 passed，修后相关 64 passed；覆盖错误码、缺项、坏成员、重复、同步时间不变、合法空集和失败后恢复。
- **注入自证**：将服务完整回退到 `8b155a7` 原实现，20 条测试中 18 failed / 2 passed；还原后 SHA-256 与注入前一致，证据见本批 artifacts/verification。
- **门禁**：完整本地验收见 §1；无生产库写入或服务启动，HTTP 响应由隔离夹具提供；远端 CI 尚未触发，不将本地通过记为已交付。
- **遗留与下一步**：响应没有成员总数，无法识别上游未声明的截断；时间戳就绪不代表历史成分有效期。本 ID 仍待指数缺失/时间戳质量与多窗口生产复验。

**此前跌停切片（PR #24）：**
- **本轮跌停切片（2026-09-17）**：`codex/limit-down-failure-semantics`；先更新最新 origin/master 建工作区，再复用首批 PR #23 的测试隔离与发布工具，发布前重新集成主干。
- **缺口与验收**：HTTP 200 / rc=102 被当空池，腾讯/新浪占位又遮住传输失败；失败须抵达 HTTP 502，合法空池仍成功，预算耗尽不得报空。
- **改动**：删除两个未实现空桩并同步能力表；东财校验业务码/结构/完整计数/代码唯一性；跌停链复用共享 deadline、熔断与成功来源统计，不接第二源、不改阈值。
- **机械证据**：23 项新增反例先全红；修复后扩充预算与非空备源用例，相关六文件 79 项通过。接口隔离实例验证 502；无生产服务启动。
- **真实取样**：10:40:20 北京时间，成功/尝试 1/1，rc=0、tc=0、pool=[]，0.161s；响应时间不等于源时间，qdate 不能证明请求日期，单次不推断全天 SLA。
- **注入自证**：原实现 23 项失败；再单独恢复旧东财解析器，整链业务失败回归精确判红，修复文件按 SHA-256 还原；证据保存在本批 artifacts/verification。
- **门禁**：本批完整门禁见 §1；发布严格按 §6.5 复核准确提交及实际 CI，不能以专项绿替代。
- **遗留与下一步**：本 ID 仍开放：题材失败写保护、指数部分缺失与时间戳质量、生产实例及多窗口复验；下一切片优先题材写保护。

**此前身份修复（PR #21 已合入；以下为当时分支记录）：**
- **缺口与验收标准**：股票与指数撞码不得串值，指数使用正确能力端点，BJ 市场保真；真实源失败不得冒充空池。
- **改动**：`backend/app/data_providers/tencent.py`、`sina.py`、`ths.py`，修身份/路由/量纲，不调整主备等级、交易阈值或通知偏好。另固定 `backend/tests/test_watch_ledger_kind.py` 测试时钟：9月12日固定样本在9月17日被真实五日窗口排除；原master隔离实测同样1 failed/1 passed，业务代码与分桶断言不变。
- **机械证据**：北京 23:26 盘后只读取样；腾讯混合批次串值、新浪指数串股、THS 错路径业务码 1002；正确 THS 指数路径 3/3 成功。详见 `docs/data-source-comparison.md` §12。
- **注入自证**：`backend/tests/test_provider_security_identity.py` 初批修改前 10 failed / 7 passed；修后真实复验又发现新浪沪市指数成交量差 100 倍，新增单位判据在修单位前 2 failed / 19 passed。全部修后该文件 21 passed，相关五文件 67 passed；深市指数与个股量纲不变。
- **门禁**：后端3360 collect（3284 passed/76 skipped/0 failed，248.83s，8000在跑、起始load 5.36/6.76/7.16）；较3339基线+21全来自新测试，既有台账测试只固定时钟、用例数不变。前端本地608/67（55.88s）、UTC608/67（55.06s），maxWorkers=1；tsc/eslint/pyflakes/构建通过。doc-health仅N/O未通过（6处指针/4条目录），原master干净检出相同，脚本与INDEX均未改；不造目录/改守卫掩盖，待既有PR #20的BUG-017修复集成后复验。未合并/未部署。
- **遗留与下一步**：本 ID 保留跌停失败/合法空集、题材失败写保护、指数部分缺失及运行实例复验；未修改生产数据或重启现有服务。
- **⚠️ 编号撞车与改号（2026-09-17，集成 PR #21 时发现）**：本项**原登记为 `BUG-018`**
  （2026-09-16 账本 P0 档），与**另一工作区**在 PR #20 中登记、且**已合入 `master`** 的
  「台账归因测试的**到期型**缺陷」**撞号**。两者是**不同的缺陷**，共用同一 ID 会让
  `doc-health` 的 **P 交接索引**（账本 §6.0-H ⇄ 本文件条目**双向闭包**）与
  **L 任务 ID 指针**无法区分指向谁——即"索引看着齐、实际指错条目"。
  **处置 = 本 PR 侧改号为 `BUG-020`**，而**不是**改已合并侧，理由：
  ① 已合并侧已进 `master`，被 `AGENTS.md`、账本 §6.0-F/§6.0-H、
  `docs/kb/09-verification-pitfalls.md`（`KB-ENG-112`）、`docs/kb/00-INDEX.md`
  多处引用，且已 **✅ 闭环**——改它等于**重开一个已闭环项**（违反"号码保留、永不复用"
  的初衷：该 ID 的既有引用会全部失效）；
  ② 本 PR 尚未合并，改号范围**封闭在本 PR 内**。
  **改号范围（4 个文件、8 处）**：本文件（本条目标题 + `账本` 回链 + `IMP-040` 条目内引用 2 处）
  · 账本 §6.0（预警行 + A 档登记行 2 处 + §6.0-H 行）· `docs/data-source-comparison.md`
  （§0 后续状态入口 + §12 执行状态，各 1 处）。
  ⚠️ **可迁移纪律**：多工作区并行开发时**任务 ID 是共享命名空间**——登记新号前应先
  `git grep -ohE "BUG-[0-9]{3}" origin/master -- docs/ AGENTS.md | sort -u` 取**最大号 +1**，
  否则撞号只会在**合并时**才暴露（本次即如此，且两边都已写了一整套文档引用）。

## IMP-040 数据源能力与最小成本接入评估
- **账本**：`docs/retro-and-gaps.md` §6.0 `IMP-040` ｜ **日期**：2026-09-17 ｜ **状态**：🟡 部分闭环
- **缺口与验收标准**：各业务环节有来源/备源/质量/时效/权限/消费者证据；未实测项明确，不能把 API 数量或适配库数量当净价值。
- **改动**：`docs/data-source-comparison.md` §12 增加本轮纠偏、分工与准入，历史 §0–§11 明示日期边界；完整 Markdown 附件与脱敏测量交付于本次任务。
- **机械证据**：THS 财务指标 3/3 可达，推翻“未打通”；东财 periods=2 实返 103 行；两源营业总收入/营业收入不同，禁止直接混换。
- **注入自证**：本项为评估，不新增运行能力；正确性复现归 `BUG-020`，不重复计算覆盖。
- **门禁**：随 `BUG-020` 的同树完整检查；暂无生产性能/收益提升结论。
- **遗留与下一步**：按能力建立真实上游共享预算、点时字段契约、冷热分工和盘中对照，免费候选先验证许可/语义/独立性；去重衔接其它数据源任务时以 §6.0 当前版本为准。

## GOV-012 客户端发布核验与平台保护边界
- **账本**：`docs/retro-and-gaps.md` §6.0 `GOV-012` ｜ **日期**：2026-09-17 ｜ **状态**：🟡 客户端已实现 · 平台待批
- **缺口与验收标准**：发布须绑定当前 master、PR HEAD、实际集成树及最新 attempt 全部必需 Actions job，未知不得当绿；合并后复验主干。
- **改动**：只读发布核验脚本与行为测试，复用现有三份 CI job。平台保护设置仍待批，不改仓库权限、套餐或可见性。
- **机械证据**：开工 master `4052c80a4479ac41038c09b3c1a273ca88e77ece`；Actions run `35167371281` attempt 1 三 job success；protected=false。PR #4 冲突，不合并。
- **注入自证**：合成缺陷覆盖空/缺项/重复、旧 SHA、取消/跳过、主干漂移和阻塞审查；实测见收尾回填。
- **门禁**：本批完整实测见 §1；合并前仍须对实际 PR 最新 HEAD 运行发布核验，不能拿单测代替远端 CI。
- **遗留与下一步**：Checks API 不可读时记录 Actions API 的核验边界；平台保护仍不能宣称完成。

## GOV-017 提交前后判据纠偏
- **账本**：`docs/retro-and-gaps.md` §6.0 `GOV-017` ｜ **日期**：2026-09-17 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：暂存的 A/M/D 是预期；提交前查未暂存漂移与暂存正文，提交后核工作区干净和 commit 文件集。
- **改动**：同步 AGENTS、handoff、KB-ENG-113 与迁移后的两份技能；不使用全量暂存吸收无关资产。
- **机械证据**：本批正常暂存 A/M/D 与未暂存 diff 是两种独立状态；按精确文件清单暂存后核正文，提交后再核任务工作区。
- **注入自证**：无独立软件守卫；使用真实 Git 状态验证命令语义。
- **门禁**：本批完整实测见 §1，最终提交另经 GitHub 三份必需 CI。
- **遗留与下一步**：本项指引修复已完成；本批提交与 PR 按 §6.5 交付，平台保护仍归 `GOV-012`，余目录退出归 `GOV-018`。

## GOV-018 平台目录退出与恢复
- **账本**：`docs/retro-and-gaps.md` §6.0 `GOV-018` ｜ **日期**：2026-09-17 ｜ **状态**：🟡 实施中
- **缺口与验收标准**：项目依赖平台专属入口和未跟踪资产；按 MIG-0–5 清单逐项提炼，恢复可验证后按用途迁移，未知不删，不操作用户全局目录。
- **改动**：本批提炼两份项目技能、报告/CSS/完整 OpenAPI 对照工具；切换 patch 归档、四份研究脚本、回收入口、文档索引与 N 门禁；完整消费者与迁移依据见 `docs/platform-directory-migration.md`。
- **机械证据**：本机实盘 3,253 条（1,644 文件、1,608 目录、1 符号链接）；2 tracked、3 untracked、3,248 ignored。完整私有清单及恢复包在原项目 `artifacts/backups/platform-exit-20260917-0938/`；3,253 条回读哈希吻合，包 SHA-256 `5eefaf19fdbf7555f22d90d7920415bfe0ceb944739ae895d52794d2613e0355`。
- **注入自证**：报告不前进与恢复覆盖两路真实行为注入各精确判红，原代码哈希还原；5 个恢复样本（3 文件、1 目录、1 链接，含最大文件）哈希全等，链接未被跟随。完整 OpenAPI 两侧 170 paths、正文零差异。
- **门禁**：本批完整实测见 §1；完整私有清单不入 Git，仅发布迁移决策与消费者映射；两份技能格式验证通过。
- **遗留与下一步**：其余本机技能、一次性工具、历史日志/报告及旧适配软链仍保留；须逐项核独有内容与许可、消除旧入口后再作 MIG-3/5 恢复式清理。当前只完成首批依赖切换，不把整个目录退出或生产通知闭环记成完成。

## BUG-021 持仓计划测试写入隔离
- **账本**：`docs/retro-and-gaps.md` §6.0 `BUG-021` ｜ **日期**：2026-09-17 ｜ **状态**：✅ 闭环
- **缺口与验收标准**：全量门禁通过却在独立工作区默认数据目录产生 71 字节当日空计划；测试不得覆盖同路径生产计划。
- **改动**：复用 conftest 既有数据隔离区，纠正 data_path_isolation 的可写登记，并覆盖真实 save/load 入口。
- **机械证据**：开工无该文件，全量后出现 date/decisions/exits/peaks 四字段；未启动生产服务。测试产物按可恢复方式回收，不发布。
- **注入自证**：先把登记改为可写，让现有守卫暴露未隔离路径，再补实际隔离并复跑。
- **门禁**：相关 36 项通过；全量 3445 passed / 76 skipped / 0 failed（189.42s），前提见 §1。复查默认目录未重建当日计划，工作区无新数据产物。
- **遗留与下一步**：只修已实测写入口，不把所有只读数据路径无差别重定向。

## IMP-041 后端测试夹具装载与 CI 分钟治理
- **账本**：`docs/retro-and-gaps.md` §6.0 `IMP-041` ｜ **日期**：2026-09-17 ｜ **状态**：🟡 实施中
- **缺口与验收标准**：用户要求剩余额度优先支撑方案交付；PR #24 后端 job 600s，其中安装约 26s、pytest 约 561s。须以等价数据和不减门禁降低实际 CI 分钟。
- **改动**：三份测试的 15 处逐行装载替换为测试专用 Arrow 批量装载；保留原表结构与全部输入。CI 去除重复 -q，增加慢项与 JUnit 输出，三 job 与全量触发不减。
- **机械证据**：9 类实际夹具逐表结构/行数/排序后全部值哈希相同；完整测试 AST 除 15 处装载及导入外相同，88 项相关测试全通过。主夹具两表各 8,100 行，交错对照预热后批量 0.070–0.108s、旧方式 3.514–4.308s；首次批量 1.047s，不能忽略初始化。完整 CI 节省仍待云端实测，证据放 artifacts/verification。
- **注入自证**：在真实装载器丢弃末行，行数判据如期报红；还原前后 SHA-256 一致。另以旧 Git 提交的完整夹具生成全部基线，而非只比较新实现自身。
- **门禁**：完整本地实测见 §1：3566 collect、3490 passed、76 skipped、0 failed，141.26s；前端本地/UTC 各 650/70、静态/构建/文档通过。保留三项 required jobs、PR 与 master 触发、全量 pytest/vitest/构建。
- **遗留与下一步**：先本地验收后集中云端实测；剩余预算与阶段交付随实际消耗更新，完整融合方案仍按原账本开放项推进。
