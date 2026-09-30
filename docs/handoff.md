# 当前交接：RSH-027 正文引用与有限对照

> 唯一任务状态在 [W05/RSH-027](stages/w05-agents.md#rsh-027)。上轮 RSH-030 盲标工具由 PR #188 合并到 `674a14039d2e243d082d370b36ccfb39139036a1`，不重复预标或伪造独立 human gold。

权威入口：现役 Jev 蓝图 [docs/ai/jev-integration.md](ai/jev-integration.md)，开放世界长期发现 [docs/ai/continuous-evolution.md](ai/continuous-evolution.md)，总方案 v9.13 在 [docs/implementation-plan.md](implementation-plan.md)。当前累计要求 U01–U55。

## 1. 现场、模式与门序

本轮从干净 `master@674a14039d2e243d082d370b36ccfb39139036a1` 同步远端、读取现行入口并运行 selector：G0–G4 无 actionable blocker，普通 fallback 选 G3/RSH-027；同门无竞争项，硬依赖 BUG-025 已完成、效果前置无。只实施本任务；不跨门开工 IMP-045 或 RSH-031。

**U50 降级授权回执**：用户 2026-09-24 授予本机 Codex 的 `DEGRADED_FULL_CONTROL` 仍有效，直到明确撤销。作者负责 U49 反证和 exact-HEAD `DegradedRelease`，不得声称独立 Review；完整本地门禁、三项 required CI、`release_check.py`、post-merge CI 与分支清理不降低。授权不覆盖付费数据/模型、真实通知、真实券商、部署或生产库。

## 2. 本轮证据与边界

分支 `codex/rsh027-kb-provenance`，工程提交 `8eb677155e9897091967f66fe44d541875091405`，边界补丁 `0c06d482843993be151d6ae7e497c4cddbacc359`。现行 `kb_routing` 唯一解析器增加按场景/ID取正文：来源行、文本与文件/条目/片段hash、索引身份版本、完整性；助手 `kb` 消费同一片段。v2快照拒绝ID-only或正文/场景/版本漂移，实际取回放 `retrieved`，过长索引标题显式标截断，超过工具预算不返回残缺回执；`support=[]`/`relation=unverified`，不能声称模型已理解或采用。历史v1不回写，也不能追认为正文已读。现行选股调用仍默认未引用，不改评分、阈值或金融硬门。

固定8项盘前/盘中/盘后显式编号对照：无KB材料0/8、索引标题1/8、正文6/8；2项拒绝、1项无新增。它只验证字面信息覆盖，非独立标注、语义准确率、候选召回或交易收益。任务SHA-256 `35d52e7b7b41c0711e2e21abd473245c5484432a8cb0a6d5d8ef1def916a0c66`，协议及可执行命令在[总蓝图§5.1](summary/system-final-blueprint.md#51-正文引用协议与有限对照2026-09-30rsh-027)。本机忽略 `artifacts/runs/rsh027-kb-20260930/body-audit.json` 保留全文回执。37项股票知识都能解析；KB-STOCK-32附记留在正文，KB-STOCK-15纠正“无过拟合”，保留原来源、历史数值与失效域。

**U49 主动审计回执 / 作者反证**：正常模式需要独立 Preflight/Review；本轮降级由作者核ID-only伪引用、正文缺失/重复、非法UTF-8、场景状态、文本/hash/位置篡改、版本漂移、代码块伪标题、分页尾部反例、回放不重读现行知识、TTL陈旧正文、多页重复IO与阅读接口同前缀越界；这些实际失败窗口均有行为测试。检索回执不能证明语义支持，当前材料不能满足股票效果主张。本轮无独立 reviewer。

**本地门禁**：定向246 passed，正文/账本/文档守卫251 passed；预算补丁后相关227 passed，pyflakes通过。后端最终代码全量4498 passed/81 skipped（127.64s），在无部署缓存的独立干净检出复验；首批4497/81在155.91s完成，因新增预算行为测试另跑最终全量。前端tsc/eslint通过，78文件/736测试在默认与UTC各通过（43.14s/43.27s），Next16.3.3构建通过，doc-health/workspace hygiene/public scan通过。准确PR/HEAD结果在本轮发布回执给出。Jev Review基线/复评对应当前代码，performance 6.5→7.4；分数仅工程辅助证据，无继续改动依据。CI只在完整本地证据后触发一批，账户Actions剩余分钟未取得，不启用付费。

本地实际后端/前端生产服务未重启，本轮未部署。

**传播核对**：正文协议/对照与效果边界更新总蓝图§5.1；唯一状态/恢复条件更新W05；Jev候选边界更新Jev蓝图；知识纠错更新原册；细功能审计X34和INDEX指针更新；本页记录现场。总方案v9.13的KB仅解释、效果另验原则未变；AGENTS、Skills、领域注册表、产品导航、猎场策略和现行权限/金融硬门不适用，无新模型/数据源/策略准入。

## 3. 恢复点与下一门

工程子范围完成，RSH-027改为 `待条件`：需要实际问题/当时可见材料/claim↔片段与独立支持/冲突/不足标签，才能比较无KB/现行检索/Jev候选的语义增量；选股增量另需时点与交易效果证据。缺条件不重复8项文本覆盖求效果。关闭助手KB入口可恢复原助手工具，保留v1/v2快照和拒绝证据，不恢复ID-only认证。当前不因词语覆盖调用Jev或付费模型、修改生产权重。

重新运行 selector 后 ordinary fallback 到G4：首选IMP-045，其次IMP-046、IMP-025、IMP-019；G3其余任务等待各自真实条件。G3→G4复核没有把RSH-030独立gold或RSH-031价格/路径研究限制改成准入通过；G4的工具化仍按各自范围与效果前置，本轮仅报告下一候选。

- **当前主门**：G4
- **主切片首选**：IMP-045
- **当前门候选顺位**：IMP-045 → IMP-046 → IMP-025 → IMP-019
