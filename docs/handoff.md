# 当前交接：IMP-045 shadow 发布顺序与原理由证据

> 唯一任务状态在 [W05/IMP-045](stages/w05-agents.md#imp-045)。上轮 RSH-027 由 PR #189 合并到 `ae5270b40ddd857840394ed6c3acad1d7cb16071`；正文覆盖不是语义效果，不重复旧取证。

权威入口：现役 Jev 蓝图 [docs/ai/jev-integration.md](ai/jev-integration.md)，开放世界长期发现 [docs/ai/continuous-evolution.md](ai/continuous-evolution.md)，总方案 v9.13 在 [docs/implementation-plan.md](implementation-plan.md)。当前累计要求 U01–U55。

## 1. 现场、模式与门序

本轮从干净 `master@ae5270b40ddd857840394ed6c3acad1d7cb16071` 同步远端并重跑 selector：G0–G4 无 actionable blocker，普通 fallback 选 G4/IMP-045（P1/门内序20/非阻断），同门随后 IMP-046、IMP-025、IMP-019。硬依赖无，RSH-030 是效果前置，不阻塞独立工程修正。只领取此任务，未跨门开工研究/路由任务。

**U50 降级授权回执**：用户 2026-09-24 授予本机 Codex 的 `DEGRADED_FULL_CONTROL` 仍有效，直到明确撤销。作者负责 U49 反证和 exact-HEAD `DegradedRelease`，不得声称独立 Review；完整本地门禁、三项 required CI、`release_check.py`、post-merge CI 与分支清理不降低。授权不覆盖真实通知、真实券商、部署、生产库写入、付费数据或新模型/阈值准入。

## 2. 本轮交付与实测

分支 `codex/imp045-shadow-isolation`；工程当前代码提交 `18bba4410ffb78ca6921b083fdbf90998eee02ac`，其前序包含 shadow 分离、取消 drain、三态 API 舍入边界与北京时间统一修正。精确发布 HEAD 和 PR 在本轮 `DegradedRelease` 给出。

- 告警原 LLM/规则 fallback 先落库并返回；每进程最多一个 shadow worker，无队列，忙时弃权、失败不覆盖原提醒，取消不把尚运行的同步线程算作结束。原 `AgentAudit` 留 ID/输入hash/原判读/建议/模型/延迟/未采纳与失败状态，不存原文或私人输入。
- 事件辅助先用原 CAS/版本条件提交原 DeepSeek 结果，再做影子对照；原整批失败语义保留。手动接口及下一辅助批次仍可能等待 adapter，独立规则提醒不依赖此等待；不宣称所有请求零延迟。
- 机会通知快照补存新闻理由、标题、精选生成时点与原事件引用。只读冻结命令分清原理由生成、通知与新原文可见时点，拒绝缺理由/缺身份/错版/未来原文，不回填旧快照，不生成 human 或模型建议，不写生产库、不改评分。
- 三态 verifier 仅采用 RSH-030 已观察的概率显示舍入上界，原值不归一化，超界继续拒绝；告警与三态 confidence 拒绝布尔值，告警概率还核范围/有限性/总和，非法响应回原路径。cascade/阈值未变，未获得准确率或股票效果准入。

**真实条件核查**：2026-09-30 10:25:07 北京时点，4 个多解释版本事件，带 `event_refs` 的历史机会快照 0 个，真实理由/新原文配对 0 个。忽略产物 `artifacts/runs/imp045-shadow-20260930/rationale-readiness.json` 的 packet hash 为 `83e518d960cf952197b5c15334d4b7cd1359f8a0dcecccbe8786439defe688c9`；命令/边界在 [Jev 蓝图 §29.7](ai/jev-integration.md#297-shadow-发布顺序与理由冻结2026-09-30--imp-045)。不能从 usage 元数据、事后股价或当前事件重建当时理由。RSH-030 240 条独立 gold 仍未完成，本轮没有作者/模型填标或新增业务模型调用。

**U49 主动审计回执 / 作者 Preflight 与反证**：核 shadow 前置等待、并发积压、取消假结束、fallback 丢比较、审计失败、版本/CAS、旧字段未知、新闻理由生成与通知时点混淆、未来原文、概率舍入过严、布尔值/非法概率误采纳和传播指针。上述实际窗口有行为反例；没有独立 reviewer，最终按当前模式写 `DegradedRelease`，不自产独立 Review。

**本地门禁**：定向最终161 passed，pyflakes通过。后端首批4507 passed/81 skipped/1 failed（138.96s），真实失败是新脚本自行定义 UTC+8，已改为权威 BJ_TZ；修后4509 passed/81 skipped（134.78s）。随后补齐原理由时点边界，全量4510 passed/81 skipped（122.73s）；数字响应边界补丁后最终全量4516 passed/81 skipped（121.06s）。前端tsc/eslint通过，78文件/736测试在默认与UTC各通过（45.31s/41.27s），Next16.3.3构建通过；文档/账本/公共扫描守卫122 passed，doc-health/workspace hygiene/public scan通过。全量用无部署缓存的干净检出，不读本机过期交易日历；没有为求绿修改守卫。Jev Review基线/最终复评对应交付代码：correctness 8.0→8.4、reliability 8.1→8.5，工具将小幅变化归 unchanged，仅辅助信息；无继续改动依据，不充当独立 Review 或效果证据。原回执保留在忽略 artifacts。CI在本地证据与协调文档完成后仅合批触发，账户Actions余额未取得，不启用付费。

实际后端/前端生产服务未重启，本轮未部署。临时干净检出与pytest沙箱属于本任务可再生产物；发布后清理检出，只留紧凑审计回执，不删除业务数据。

**传播核对**：Jev蓝图 §29.7 更新运行顺序/真实条件/命令，W05单点更新状态与恢复，细功能审计X41及INDEX更新，本页记录现场。总方案v9.13、产品闭环/猎场的解释→shadow→独立效果准入原则未变；AGENTS/Skills/领域登记册/导航无需变更，无新模型、数据源、权限或股票策略准入。已有运行开关仍默认关闭，不以工程发布自动常开 shadow。

## 3. 条件与下一候选

工程部分完成，IMP-045 为 `待条件`：需要新版本运行形成有原理由/原时点/引用的真实机会并遇到同事件新观察，冻结配对，取得独立理由变化与 claim/evidence 三态反例及 RSH-030 gold，才评估语义常开或晋级。U53–U55 共审入口还需真实消费者与题型增量，不提前做无效接线。条件未到不重复 0 配对扫描，不把接线测试算效果，关闭 Jev 模式回到原 LLM/规则路径，保留既有历史。

重跑 selector 首选 G4/IMP-046，随后 IMP-025、IMP-019。本轮仅报告，不自动领取下一业务任务。

- **当前主门**：G4
- **主切片首选**：IMP-046
- **当前门候选顺位**：IMP-046 → IMP-025 → IMP-019
