# 当前交接：IMP-085 板块资金流空间整改

> 总方案v9.13；U01–U55；任务唯一状态归W07/IMP-085；现役UI方案[原版技能与UI方案](product/ui-original-skills-plan-20261008.md) §9.10；Jev现役蓝图[jev-integration](ai/jev-integration.md)。IMP-084已由PR #243/#244完成发布，本轮不复用其历史绿色。

## 1. 现场、授权与版本

- **当前主门**：G2
- **本轮主任务**：IMP-085
- **主切片首选**：IMP-085
- **版本**：从最新origin/master `2212b6278ce9890a5be4047e9b92c0e759a7d73c`创建`codex/fund-flow-space`；依赖IMP-084已完成。
- **U50 降级授权回执**：用户2026-10-09直接要求修复市场→资金流向→板块资金流的空白与拥挤。2026-09-24 Codex `DEGRADED_FULL_CONTROL`授权未撤销；作者=发布操作者，非独立Review；准确HEAD DegradedRelease、完整适用门禁与required CI继续执行。
- **实现范围**：资金阅读区左右比例、板块卡容器列数、专属内外边距、名称/金额排列、手机阅读面宽度。保留B+C风格、原筛选/排序/增量加载/下钻/三个分析视图、来源/时间/质量，列表为纵向滚动所有者。后台、API、行情计算、AI预算/模型、交易风控/权限不变。
- **恢复**：保留既有ui-current-saved-20261008标签与artifacts/recovery/ui-current-20261008；本次未替换恢复副本。

## 2. U49 主动审计回执 / 作者Preflight与有界反证

原版Impeccable执行Layout/Adapt，原版Taste执行Scan→Diagnose→Fix；窄修复使用现役实现作为设计上下文。IAB实际1280×720测得资金容器1232px，板块480.74px、右侧735.25px；板块卡仅121.58px、内边距18px。整屏≥1280触发共享Masonry三列，与板块实际容器宽度冲突；依据artifacts/runs/fund-space-20261009/screenshots/before-1280.png。手机390px修改前截图同目录before-390.png。

反证范围为左右比例、共享卡壳边距、列数断点、长名称/金额、父子最小宽高、手机横向阅读面、列表增量sentinel、筛选/排序、下钻退出与分析视图；共享Masonry另有猎场消费者，本次只覆盖板块专属class，不全局调整。右侧分析保留其独立读数与容器滚动归属。源失败保留不可用提示，不用模拟结果填空白。

## 3. 当前验证与发布

代码提交`f982293f3af3e10f86adf0bc267de15c49c428bb`，协调文档另行提交；候选最终HEAD以PR回执为准。

本批最终源码的前端tsc/eslint、production build、doc-health/workspace-hygiene/public_repo_scan通过。完整Vitest默认与UTC均单worker、显式`--pool=threads`：100文件886 passed，344.48s/400.05s。此前fork池全量出现markdown corpus超时（另一次workspace超时），未改变用例、断言或超时；两次较早fork默认/UTC全量各886通过仅为补丁前基线，准确HEAD CI仍按仓库标准池核验。后端无代码变更，本地不重复其全量，required backend CI保留。Jev Review两次HTTP451，无baseline/复评分数，不能写通过。Impeccable最终检测结果为空；这是局部检测结果，不是全项目审计。

最终实际组件合成夹具验收：1280×720板块663.27px/分析552.73px、卡305.63px；390×844卡326px；320×640卡260px、列表131px；1280×600列表313px。四种视口页面根没有X/Y溢出；320px短屏通过叶级滚动读取卡片完整字段，120项增量末项与口径说明均可达，不宣称同帧全见。筛选、Enter下钻、Esc关闭与回焦、当前/历史/机构游资入口已操作。生产3000东财板块源中途失败，保留不可用/重试，成交摘要仍有实源；卡片极端内容截图明确为3101隔离合成夹具，不冒充真实行情。图表算法和读者未修改；真实手机、软键盘、触摸拖动、大缩放与全部质量状态的独立实屏未取得。

3000已加载最终production构建`uan4kQ7Mk6taV-q14tbiU`，前端PID40262、后端75041；三项修改源码SHA-256无漂移，市场页/代理health/后端health/LAN只读HTTP200。临时3101/3102夹具服务已正常停止并复查无监听，IAB临时视口已reset、临时标签为空。既有恢复包与tag保持。详见忽略产物`artifacts/runs/fund-space-20261009/`的browser-acceptance.json、runtime-release.json、final-source-manifest.json、screenshots及门禁日志。发布CI/准确HEAD回执/合并后核验尚待取得。

传播核对：W07/IMP-085、原版UI方案§9.10与本handoff承接同一授权切片；INDEX/总方案/AGENTS/领域蓝图/协作Skills不适用，因为没有新页面、文档入口、长期产品语义、模型、工具、权限或治理规则变化。

开放世界持续演进入口：[持续演进](ai/continuous-evolution.md)。外部候选仅提出验证，不自动安装或准入。

## 4. 新增用户要求的边界

用户明确选股保持简洁，复用现有实际入选结果，只保留消息通知一个主动出口；助手保留人工问答，AI仅在选股存在实际语义缺口时评估价值，不新建候选池或第二套评分。自设提醒、持仓风险与系统异常分别保留。全系统加载慢/过期另取跨模块请求、缓存与上游源证据，不通过隐藏stale修复。上述要求已获授权，但不属于本IMP-085布局diff；先完成本项发布，再登记通知消费收口和实测性能切片。当前没有改通知/选股业务。
