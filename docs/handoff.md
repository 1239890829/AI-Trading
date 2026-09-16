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
> 条目控制在一屏内；过程细节与长证据放 `.workbuddy/artifacts/`、逐日日志或测试文件本身，此处只留**结论 + 指针**。
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

- **分支**：`codex/alert-bubble-symbol-detail`（自 `origin/master` 创建；本轮交付 `IMP-031`）。
  上一轮分支 `codex/ledger-stage-consistency` 已随 PR #15 合并并**删除**（本地与远程均已清）。
- **基线**：`origin/master` = `662f9fa`（PR #15 合并提交）。
- **服务**：后端 8000（uvicorn，单实例；**绝不用 `--reload`**，原因见 `AGENTS.md` §6.1）、前端 3000。
- **门禁基线（本次收尾实测，接手时可直接对照）**：后端 **collect 3247（3171 passed / 76 skipped / 0 failed）**、
  前端 **598 项 / 66 文件 · 597 passed / 1 failed**、`eslint` **0/0**、`doc-health` **全部通过**。
  ⚠️ **那 1 failed 是 `BUG-010`（D 档观察项），不是回归**：用例 =
  `components/agent/markdown-view.test.tsx::docs/ 下全部 md 均可渲染完成`（固定 5s 墙钟）。
  本轮**决定性归因（对照跑）**：临时移出本轮新增的 2 个文件后，基线**同样 `1 failed / 592 passed`、同一用例同一形态**
  ⇒ 与新增文档/用例无关；增量 `597−592=5`、`66−65=1` **恰等于**新增用例数 ⇒ **新增用例全部通过**。
  该用例单独跑 **10/10 · 1000ms**（`docs/` 已 87 份 md，阈值 5000ms）。宿主实测 **load 28.16 / 8 核**。
  按原判**不改判据、不放宽阈值**（详见 `BUG-010` 行与 `AGENTS.md` 门禁段）。
  前端较上轮 593/65 的 **+5 / +1** 即本轮新增的气泡用例文件。
  ⚠️ **两道全量不要并发跑**：并发会因 CPU 竞争让 `markdown-view.test.tsx` 的 docs 全量渲染用例
  超时假红（账本 `BUG-010`，D 档不主动动）——**它的红不代表代码回归**。
- **归属不明的既有未跟踪文件（非本轮产物，**保留、勿删**）**：`git status --short` 里的 5 个 `??` 项，
  分布在 5 个目录 —— `backend/data/lhb/`、`backend/data/minute_decisions/`、
  `backend/data/position_plans/`、`docs/daily-review/`、`docs/evolution/`。
  **均为运行期产物、尚未纳入版本控制**，具体文件名请跑 `git status --short` 取。
  ⚠️ **此处刻意不写具体文件名（不是省略，是纪律）**：它们不在 git 里 ⇒ 写死即成为
  **门禁判定面上的死锚点**。2026-09-16 实测踩到：本条原先写死两个 `docs/` 下的未跟踪文件名
  ⇒ **CI docs job 红（B 死链 2 处）而本地恒绿** —— 与 [[KB-ENG-95]]「判定面必须等于 CI 检出内容」
  同族。**修法是改文案，不是登记豁免**（豁免会让下一次换个文件重演）。

## 2 条目一览（与账本 §6.0-H 逐字对应）

| 任务 ID | 状态 | 日期 | 一句话 |
|---|---|---|---|
| `IMP-031` | ✅ 闭环 | 2026-09-16 | AI 判读气泡点开**就地打开该股详情弹窗**（不再跳告警页）；同轮梳理出提醒链路断点清单 |
| `GOV-015` | ✅ 闭环 | 2026-09-16 | 账本「未完成档 ⇄ 闭环记录」一致性守卫（`doc-health` Q 项）；顺带把滞留 A 档的 `BUG-014` 销账 |
| `GOV-014` | ✅ 闭环 | 2026-09-16 | 建立交接明细层与双向索引守卫；注入自证抓出并修掉守卫的两处判据盲区，流程已固化为技能 |
| `RSH-026` | 🟡 部分闭环 | 2026-09-16 | 个股机会学习闭环第一批已交付，并完成独立验收轮（抓出并修掉 1 处 schema 分叉） |
| `BUG-014` | ✅ 闭环 | 2026-09-16 | 两处迁移把表建到默认库 ⇒ 全新库缺 5 张表；已改 `op.get_bind()` 并加两条守卫 |

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
