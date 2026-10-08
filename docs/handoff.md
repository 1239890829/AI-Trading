# 当前交接：行情口径与免费备源补强

> 总方案v9.13；U01–U55；状态仅W00/IMP-079；Jev入口 ai/jev-integration.md；数据证据入口data/data-source-comparison.md。

外部持续演进入口为 docs/ai/continuous-evolution.md，候选不自动准入。

## 1. 现场、模式与范围

用户2026-10-08在数据源比较后明确“开始吧”，授权IMP-079及内部切片。master基线f821e7d3，独立分支codex/data-source-strengthening。

- **当前主门**：G1
- **本轮主任务**：IMP-079
- **主切片首选**：IMP-079

**U50 降级授权回执**：2026-09-24 Codex DEGRADED_FULL_CONTROL未撤销；作者=发布操作者，非独立Review。完整本地门禁、required CI、exact-HEAD DegradedRelease和release_check继续强制。

## 2. U49 主动审计回执 / 作者Preflight

当前master干净并同步。selector无其它活动候选；本轮为用户直接指定任务。核报价/分钟/盘口各自单位、复权/日期边界、HTTP身份和分页、最新尾部与全天遍历的预算差异、缓存跨日及缺包语义。已确认科创板分钟累计量也是股，盘口显示仍是手，不能对所有字段同样改乘数。当前TDX快路径保留，新HTTP后备不增加健康主路径开销。离线日线归档不写生产marketdb、不回填前向窗口。

## 3. 执行与验证

代码提交d3e82fba。科创量归一化、北京日期、腾讯历史分段/同源备域、HTTP分笔第三路、TDX离线归档已交付实现；API与助手共用成交备链，页面标口径/日期/陈旧，新闻保持现役源。

- 后端：完整4651 passed/83 skipped（Python3.11.12，CI同款四worker）；之后发现ZIP测试夹具写墙钟时间导致hash偶发差异，固定夹具时间后相关26 passed。最后生产逻辑与全量通过版本一致。
- 前端：96文件/830用例默认时区与UTC各通过；tsc、eslint、pyflakes、生产构建通过（本机Node24.14.0；CI另核Node22）。doc-health、public scan、workspace hygiene通过；协调文档更新后再核。
- Jev辅助复评已完成。部分低置信、无具体位置的建议经作者按真实失败路径核查；最后输入范围变动，标量分数不作为质量增量或独立Review证据。实际边界有日志、API detail、行质量与CLI manifest；不额外堆健康注册表。
- 8000后端已正常重启，无reload；3000新生产构建已运行，后端凭据仅注入前端服务进程，不写浏览器/文件/日志。3000真实历史2024-01-02至05返回4条THS，688981最新50笔仍走TDX；工作台成交页已显示通达信3秒聚合及2026-10-08源日期。直抓HTTP最新50笔及两日日线包证明可运行，不证明长期SLA。
- 当前发布步骤：推送PR，exact-HEAD DegradedRelease，required三job及release_check通过才合并；随后核master CI并清理功能分支。作者自审非独立审核，不能把正在等待的CI写成成功。
- 保留脱敏紧凑日志、原始免费源核验与hash在artifacts/runs/data-source-strengthening-20261008；无新clone/worktree/依赖。已结束且可再生的本轮pytest沙箱收尾清理，在线业务库与UI恢复包保留。

## 3.1 作者有界反证

核单位交叉污染、首日丢失、复权混合、空源冒充空市场、跨日/未来时间、分页尾部变化与预算、缓存副本、归档部分写/修改hash、后台测试触网、两处消费者时间丢失。发现的具体缺陷已修复并复验；HTTP聚合非L2、历史包非点时、TDX已有日期限制及旁路health治理边界保留说明。没有把当前免费端点可达性或Jev评分当成策略收益证据。

## 4. 传播与恢复

现有Provider/API体系不重建；单位与来源边界回填数据源说明、比较及能力备注。总方案/INDEX/AGENTS/Skills不新增规则；任务归原数据owner。原UI恢复包保持原状。
