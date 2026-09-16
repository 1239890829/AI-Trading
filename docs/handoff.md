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

- **当前分支**：`master`。接手审阅偏差修复已由
  `codex/fix-handoff-review-deviations` 经 PR #19 合并，功能分支在交付后删除。
- **已交付基线**：以最新 `origin/master` 为准；交付后
  `git log --oneline origin/master..HEAD | wc -l` 实测 **0**。
- **服务**：后端 8000（单实例；**绝不用 `--reload`**，原因见 `AGENTS.md` §6.1）、前端 3000。
- **门禁基线（`RSH-003` 切片 2 收尾实测，接手时可直接对照）**：后端 **collect 3339
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
