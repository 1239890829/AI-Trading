# 当前交接：RSH-030 独立盲标入口与外部样本条件

> 唯一任务状态在 [W04/RSH-030](stages/w04-research.md#rsh-030)。上一轮 BUG-009 由 PR #187 发布，合并与 post-merge CI 以 GitHub 为准；本轮不重复其实现。

权威入口：现役 Jev 蓝图 [docs/ai/jev-integration.md](ai/jev-integration.md)，开放世界长期发现 [docs/ai/continuous-evolution.md](ai/continuous-evolution.md)，总方案 v9.13 在 [docs/implementation-plan.md](implementation-plan.md)。当前累计要求 U01–U55。

## 1. 现场、模式与门序

本轮从干净的 `master@a37624e2c68e4ab0c915112c9bde63a52cef8fbb` 读取 AGENTS、INDEX、总方案 v9.13、Jev 蓝图、总账和 stage，运行 `scripts/ledger-runtime-selection.py --json`：G0–G4 无 actionable blocker，普通 fallback 选择 G3/RSH-030；同门候选其次 RSH-027。RSH-030 无硬依赖/效果前置，RSH-031 的语义效果前置仍由本任务承接，不因此跨门实施 RSH-031。

**U50 降级授权回执**：用户 2026-09-24 授予本机 Codex 的 `DEGRADED_FULL_CONTROL` 仍有效，直到明确撤销。作者负责 U49 反证和 exact-HEAD `DegradedRelease`，不得声称独立 Review；完整本地门禁、三项 required CI、`release_check.py`、post-merge CI 与分支清理不降低。授权不覆盖付费数据/模型、真实通知、真实券商、部署或生产库。

## 2. 本轮证据与边界

RSH-030 的原始 240 条队列已有规则答案和 Jev 预测，但 `human` 为 0/240。直接交原队列会泄露答案；用模型/作者代填则不满足独立人工金标准。本轮在 `codex/rsh030-blind-gold` 给现有 `jev_goldset.py` 增加盲标导出和原文/ID/指纹核对导入，打乱原先按规则类别排列的顺序，修复 `score --allow-partial` 将未标行计入指标分母的缺陷。工具只在离线研究侧工作，不改事件/猎场生产逻辑。固定队列 SHA-256 `fc8776d15f563b10b694b8108be84f24ad331c85b536045c9689018f194c3ef7`；本机忽略目录 `artifacts/runs/rsh030-blind-20260930/events_blind.jsonl` 有 240 行，SHA-256 `bbbf3c5389737b8e9904a4e9ddd6c6db65b53c1bb3ca4f95698aeaa1070ee785`。使用方式在 Jev 蓝图 §29.6。

**U49 主动审计回执 / 作者反证**：正常模式需要独立 Preflight/Review；本轮降级模式由作者核了规则/模型答案泄露、原队列分组暴露、篡改原文、漏行/重复 ID、半行标签和 partial score 假准确率。还以只读方式查本机 `data/ashare.db`：134 条 event observation、3 条 revision、0 条确认 withdrawal link；不能用这些稀少观察声称真实更正召回。原 36 条作者预标不作为独立 gold。尚未获得外部人工标注与 verifier/更正真实正负样本，所以不报 accuracy、省额或语义选股增益，也不调生产阈值。

**传播核对**：RSH-030 状态与恢复条件写 W04；盲标协议写 Jev 蓝图 §29.6；当前现场写本页。研究/生产语义和任务选择算法未变，实施方案、INDEX、AGENTS、Skills、产品/猎场与细功能审计不适用。代码完整本地门禁、准确 PR/HEAD、CI 与发布结果以本轮最终回执为准，不能用这份预提交现场代替发布证据。

## 3. 恢复点

RSH-030 静态改为 `待条件`：独立人工完成冻结 240 行且留下未看预测的过程说明后，才跑 strict validation 与同条件对照；真实 claim/evidence 和更正/撤回观察对出现后，另按题型冻结独立样本。缺条件时不再重复 Jev 预标或旧库扫描。RSH-031 价格与路径研究可在自身运行条件满足时独立继续，但新的语义特征不能以本轮工程结果冒充通过 RSH-030。合并后重新运行 selector 取下一唯一主任务，不在本轮自动开工。

- **当前主门**：G3
- **主切片首选**：RSH-027
- **当前门候选顺位**：RSH-027
