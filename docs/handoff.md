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

- **分支**：`codex/learning-loop-and-handoff`（自 `origin/master` 创建；原名 `codex/stock-opportunity-learning-loop`，
  因本轮同时交付 `RSH-026` / `BUG-014` / `GOV-014` 三项而改名 —— **该分支当时零提交、远程无此分支**，改名零风险）。
- **基线**：`origin/master` = `148ec3851cf9da0f84fe243289a9d6a79f1dd4de`（PR #12 合并提交）。
- **服务**：后端 8000（uvicorn，单实例；**绝不用 `--reload`**，原因见 `AGENTS.md` §6.1）、前端 3000。
- **归属不明的既有未跟踪文件（非本轮产物，**保留、勿删**）**：
  `backend/data/lhb/20260915.json`、`backend/data/minute_decisions/scan-2026-09-15.json`、
  `docs/daily-review/2026-09-15-evolution-summary.md`、`docs/evolution/2026-09-15.md`。

## 2 条目一览（与账本 §6.0-H 逐字对应）

| 任务 ID | 状态 | 日期 | 一句话 |
|---|---|---|---|
| `GOV-014` | ✅ 闭环 | 2026-09-16 | 建立交接明细层与双向索引守卫；注入自证抓出并修掉守卫的两处判据盲区，流程已固化为技能 |
| `RSH-026` | 🟡 部分闭环 | 2026-09-16 | 个股机会学习闭环第一批已交付，并完成独立验收轮（抓出并修掉 1 处 schema 分叉） |
| `BUG-014` | ✅ 闭环 | 2026-09-16 | 两处迁移把表建到默认库 ⇒ 全新库缺 5 张表；已改 `op.get_bind()` 并加两条守卫 |

## RSH-026 个股机会学习闭环（第一批已交付 + 独立验收轮）

- **账本**：`docs/retro-and-gaps.md` §6.0 `RSH-026` ｜ **日期**：2026-09-16 ｜ **状态**：🟡 部分闭环（本项**仍开放**）
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
