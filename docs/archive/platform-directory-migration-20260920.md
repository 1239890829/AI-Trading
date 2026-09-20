# 平台目录迁移证据与恢复方式

> 定位：最终融合方案 MIG-0–5 的迁移依据与验收明细；任务状态唯一归
> `docs/retro-and-gaps.md` §6.0 `GOV-018`，本文件不另立施工清单。
> 上游：`docs/INDEX.md`、用户批准的最终融合方案 v9 §19；下游：项目技能、工具与运行写入方。

2026-09-17 后续规则更新：下表记录首次提炼时的历史分工；现行两技能已随 GOV-019 改为总账索引阶段页、任务单点状态与 handoff 当前现场，旧双向任务明细和多表同步已退出。目录本身仍按原恢复清单逐项处理。

## 1 恢复点与范围

2026-09-17 从最新 `origin/master` `4052c80` 建立独立 `codex/fusion-foundation`。
原项目两个平台目录通过 `scripts/audit/platform_assets.py` 实盘盘点；没有假定 Git 包含本机资产。
清单含 3,253 条：1,644 文件、1,608 目录、1 符号链接。Git 分类为 2 个跟踪文件、
3 个未跟踪目录、3,248 个忽略条目；后者不是“可随意删除”。符号链接只存链接文字，不跟随。

完整私有清单及恢复包保存在原项目忽略的 `artifacts/backups/platform-exit-20260917-0938/`。
恢复包 SHA-256：`5eefaf19fdbf7555f22d90d7920415bfe0ceb944739ae895d52794d2613e0355`。
每条记录包含旧路径、类型、字节数、权限、内容哈希、Git 状态、恢复成员名、用途、消费者、
许可、目标及处置字段；未审项目保留明确的待审值。回读 3,253 条哈希后再盘源树，变化即拒绝认定成功。
该恢复点建立时未移除原件；已提炼资产由恢复包及 Git 历史保障回退，未审资产仍原地保留。
仅恢复包验证不构成删除许可。用户全局目录、其它项目与外部快捷入口不在本批修改范围。

## 2 首批内容提炼与消费者

旧路径在下表作为迁移来源记录，不是现役调用指令。其余本地技能、一次性 codemod、研究报告和历史日志
仍需逐项审查独有内容、许可及消费者后归位；未知资产仅阻塞自身清理，不能宣称整个目录退出完成。

| 原资产 | 新落点 | 提炼与消费者依据 |
|---|---|---|
| 旧 `.workbuddy/skills/ashare-ledger-continue/SKILL.md` | `skills/ashare-ledger-continue/SKILL.md` | 保留两处指针、真实 Git 状态、授权内接续及门禁；历史重复说明归既有 KB |
| 旧 `.workbuddy/skills/ashare-task-handoff/SKILL.md` | `skills/ashare-task-handoff/SKILL.md` | 保留唯一账本、双向交接、七字段与 P/Q 失败边界；取消旧平台日志写入要求 |
| 旧 `.workbuddy/tools/md-report-html.py` | `scripts/reports/md-report-html.py` | 项目报告渲染；修复非表格竖线导致的不前进循环、链接属性/协议注入及标题参数 |
| 旧 `.workbuddy/tools/md-html-parity.py` | `scripts/reports/md-html-parity.py` | 保留文字、结构、转义竖线、列数、文件时序的独立检查，不冒充完整 Markdown 标准实现 |
| 旧 `.workbuddy/tools/report_style.css` | `scripts/reports/report_style.css` | 保留报告样式与相邻文件路径；缺资源直接失败，取消隐藏缺包的内置样式副本 |
| 旧 `.workbuddy/tools/openapi-contract-diff.py` | `scripts/audit/openapi-contract-diff.py` | 保留双检出比较思路，改为独占临时基线和完整 OpenAPI 内容比较；不复用或强删未知工作区 |

上述资产为项目自建流程与工具，未导入未核验的第三方技能、依赖或许可证。迁移前后摘要由
本批恢复目录中的 `first-batch-decisions.json` 记录，目标哈希随本轮最终文件核验更新。
旧审计的目录数量不能替代本轮完整清单，更不能代替对独有内容的逐文件阅读。

## 3 写入方、保护面与入口

| 消费者 | 新落点及行为 |
|---|---|
| C 类 patch 归档 | `artifacts/evolution-patches/`；原有只提案、隔离提交、不合并/不推送契约保持；artifacts 同样受禁改保护 |
| 四份研究脚本 | `artifacts/research/`；补首次写出所需的父目录创建；不运行旧实验、不切换研究结论 |
| 回收工具 | `scripts/safe-trash.sh` 调用 `scripts/safe_trash.py`；每条独立 ID、原路径/原因/哈希；拒绝项目外、链接父目录和恢复覆盖 |
| 报告与原始证据 | `artifacts/reports/`、`artifacts/logs/`；稳定知识提炼至现有 `docs/kb/` |
| 短入口 | AGENTS 内 project-entry 块；3000 字符预算、缺失/重复/反向/空块均判红 |
| 文档及任务 | `docs/INDEX.md`、账本 §6.0 和 `docs/handoff.md`；不建立第三份 MEMORY 权威文件 |

所有 `artifacts/` 默认忽略，恢复包、研究私有输出和凭据不发布。
`doc-health` N 项转查项目自有短入口，保留 B/N 分工、占位引用规则和 Git 检出判定面；
目录形态必须判目录自身、未知但非忽略路径照判。历史平台形态的合成回归夹具保留，防止因迁移删掉守卫。
命令扫描和死代码扫描跳过本地产物，仍扫描入库技能及复用工具。

## 4 可复跑验收

- 先跑 `backend/tests/test_platform_assets.py`：跟踪/未跟踪/忽略、外部及失效软链、源变化、禁止覆盖恢复点。
- 再跑 `backend/tests/test_platform_migration_tools.py`：回收往返、越界/覆盖/篡改拒绝，报告生成与反向缺字，
  OpenAPI 同计数下的 schema 变化，研究落点及 patch 归档隔离冒烟。
- 运行 `backend/tests/test_doc_health_memory_index.py`；保留既有负例并新增短入口边界，随后在干净检出跑 doc-health。
- 完整后端、前端、静态检查与构建仍以 `AGENTS.md` §1 为准；数字与运行前提只回填 handoff §1。

恢复采用独立空目录，不把包内符号链接当目录遍历。抽样恢复的文件、目录和链接须与清单逐项核对；
本轮已实际恢复 5 个样本（含最大文件）：3 文件、1 目录、1 链接，哈希全部吻合且未跟随链接。
整个旧目录移除前，还必须完成其余内容审阅、停止全部旧入口、关联文档清理及运行不重建验证。
不能把首批工具切换的通过写成 MIG-3/5 或生产通知可靠性已经完成。

## 5 盘后复盘入口提炼（2026-09-17）

本批状态仍归账本 §6.0 `GOV-018`，不建立并行清单。源文件为本机忽略的旧
`.workbuddy/skills/ashare-daily-review/SKILL.md`（170 行，11,312 字节），已完整阅读；
SHA-256 `c9c12f634bd28d730a869f8e75b95a3c3f5fde081f33e9e8c69f955b125c9b9d`，
与 §1 恢复包成员及清单一致。项目自建流程的有效内容按用途提炼，非整包改名。

| 源内容 | 处置与消费者 |
|---|---|
| 流程、归因、日历、bands 与回访 | 新 `skills/ashare-daily-review/SKILL.md` 路由至既有 SOP / checklist，不重复复制完整流程 |
| API 日期差异与 PATCH 守卫 | 复核当前路由后保留在新入口；说明用 `note`，身份三元组与回读必需 |
| 硬编码项目/平台解释器、另端口启动建议、pytest 后还原日历 | 取消失效命令；使用项目定位、实际实例诊断和隔离测试，同步 SOP / checklist |
| Serenity 方法论及旧第三方运行命令 | 方法论沿用 KB-STOCK-34；旧工具调用从现役入口撤下，包及嵌套 Git 仍原地保留，UZI 未审资产不移动 |
| 旧文件与入口 | 新入口随主干交付后，仅将上述已核哈希的旧单文件送入项目可恢复回收站；不删除相邻未知文件 |

关联入口为 AGENTS 短入口、INDEX 技能行、每日 SOP / checklist 和仓库跟踪 KB。
验收含技能格式、跟踪清单中的引用可达、门禁与干净检出；原件的回收、空目录检查、
恢复回读和最终哈希记录在本批忽略的 artifacts 回执，未回收前不能冒称旧入口已消失。

PR #28 主干检查通过后已执行上述回收：原件哈希与恢复副本一致，已核为空的旧父目录
同步退出，doc-health 通过且未重建旧入口。回执位于 artifacts/runs/index-completeness-20260917/verification/migration-retirement.json；相邻未审资产保留，整体状态仍见账本 §6.0 `GOV-018`。

## 6 导入环工具提炼（2026-09-17）

旧 `.workbuddy/tools/py-import-cycle-detect.py` 已全文阅读，271 行 / 10,267 字节，
SHA-256 `166fb841f325aad8ce1ef1c99ba31a0d833df391c76e930f6516ff048eec9716`，与 §1 恢复成员一致。
保留 SCC 和最短环路径方法，提炼至 `scripts/audit/import_cycles.py`；重写扫描入口，仅读项目包，
不遍历虚拟环境/软链。修正类体误归延迟、from 子模块遗漏、语法错误被跳过后报绿及延迟环退出码混淆。
两个 bootstrap 模块的现役指引同步更新；历史审计中的旧路径仍是来源记录。
工具只列静态候选，条件导入可能形成候选，动态导入与隐式包初始化边未覆盖，不能替代真实导入验证。
验收包括类体/函数体、相对与别名导入、最短路径、自环、语法错、虚拟环境及软链；干净检出同验。
原件只在新入口合入且主干通过后，经哈希复核送入项目可恢复回收站，再做恢复回读；不动相邻未知资产。
源清单、目标哈希和最终回收状态存 artifacts/runs/notification-outbox-20260917/verification，整体任务保持开放。

## 7 最终退休批（2026-09-20）

用户明确要求项目内不再保留任何应用专属目录。本批在旧恢复包基础上重新对源树做完整备份，
最终恢复点为本机忽略的 `artifacts/backups/platform-retirement-20260920-final/`：
- `recovery.tar.gz` SHA-256 `ed57f245ee440bd6b56607b5466ce605cb1d430a6862ac08fb227059cf373324`；
- 3,249 个 payload 逐条回读校验通过；恢复包约 31 MiB；
- 该包只作本机恢复证据，不发布、不成为现役入口。

最终处置不是整包改名：
- 18 个项目自建且仍可复用的 Skill 提炼到项目唯一 `skills/`；
- TDX 数据源四个可复跑探针迁到 `scripts/audit/data-source-probes/`；
- 数据源与仓库评估的少量独有 Markdown 证据迁到 `docs/archive/`；
- 已先行迁出的报告/OpenAPI/导入环工具继续以 `scripts/` 中现役版本为准；
- UZI/Serenity 两个第三方整仓不再复制，已提炼方法由 KB-STOCK-34、P2-35/P2-36 与 repo tracker 承接；
- 旧 memory/reports/trash、重复快照、一次性切分脚本与 `.workbuddy-ai` 指针不再作为活动资产迁移。

项目目录中立性改由 `scripts/workspace-hygiene.py` 守卫：应用专属状态/插件目录及其 `.gitignore`
隐藏规则都会失败；CI 与交接收尾均运行。旧 `scripts/audit/platform_assets.py` 与专属测试随迁移完成退出，
需要复盘迁移方法时从本归档文档与 Git 历史读取，不恢复旧平台目录。
2026-09-20 最终执行确认：删除前再次核验无运行进程引用且最终恢复包 SHA-256 一致；
主工作区 `.workbuddy`（约 77 MiB）与 `.workbuddy-ai`（约 24 KiB）随后物理删除，
`test ! -e` 双向确认通过。项目内其它应用插件元数据（GSAP 归档下的 Claude/Cursor
manifest）由同一提交删除，合并后以 `workspace-hygiene.py` 的全树扫描作为持续验收。
