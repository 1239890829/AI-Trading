# 当前交接：原版技能职责与控件重设计

> 总方案v9.13；U01–U55；状态仅W07；Jev入口 ai/jev-integration.md；证据入口 product/ui-skill-redesign-20261007。

## 1. 现场、模式与范围

用户明确派工 IMP-076，基线 f3116d98790a56ff042a25c0e6f8b1fedda5f293（PR #230），分支 codex/ui-skill-led-redesign。

- **当前主门**：G2
- **本轮主任务**：IMP-076
- **主切片首选**：IMP-076

**当前门候选顺位**：仅交付 IMP-076。

**U50 降级授权回执**：2026-09-24 Codex DEGRADED_FULL_CONTROL 未撤销；作者=发布操作者，不是独立 Review。仍须完整门禁、准确 HEAD DegradedRelease、required CI、release_check、合并后 CI。

## 2. U49 主动审计回执 / 作者 Preflight 与反证

从同步的干净 master 起步。按需求/蒸馏治理→交互→原站设计量测→impeccable/taste/finesse设计→工艺与GSAP→独立设计/检测评估→响应式与状态→发布执行。原版技能逐一保留职责，项目适配不独占视觉。来源无法唯一确认或浏览器受限单列，不能伪称全部视觉核验。

反证：关闭按钮与普通操作分开；小可见面不缩小触点；数据红绿与真实缺失语义保留；市场范围不是内容tab；搜索IME/慢请求、焦点恢复、键盘和快速开关需验证。另发现并修复搜索结果打开详情后丢焦、方向键缺失、情绪依据仅title、涨停行缺原生键盘入口。

## 3. 当前实现与验证

烟晶暖银双主题、宽行手机搜索、共享图标/关闭按钮、Hugeicons 按图标入口导入、Radix 下落筛选与市场视角已实现。实现提交 `6e790d4e`。本轮后端4619 passed/83 skipped，前端默认/UTC各825；tsc/eslint/pyflakes/npm ci/正式构建通过。41入口PC/手机82图、20断点检查、Canvas与弹层行为；Jev携baseline复评及fresh设计收尾ship。准确HEAD发布正在执行；不能复用 IMP-075 的发布结论。生产构建预览 4202 已启动并实屏核对菜单，后端仅指向 GET 合成夹具 4204，不是实盘服务；图册在4205。最终文档回归173通过；补正导航减少动态规则后再次生产构建通过。

## 4. 恢复与传播

依 docs/ai/continuous-evolution.md 核原始来源、反证与退出，候选不自动准入。

当前活动恢复包 artifacts/recovery/ui-current-20261007/source.tar.gz 已验 SHA-256，远程标签 ui-current-saved-20261007。旧活动恢复包按 safe-trash 可恢复迁出，旧活动标签已删；普通 Git 历史保留。

总方案、plan-registry、INDEX、产品闭环、细功能审计、W07、project design-taste 已同步。AGENTS/业务owner/模型准入不变。详细来源、测试与截图存忽略的 artifacts/runs/ui-skill-redesign-20261007。
