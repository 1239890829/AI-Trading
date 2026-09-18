# 当前交接：IMP-046 的 C 类纯提案安全切片

> 方案仍为 [v9.2](implementation-plan.md)，原21条用户消息对账保留在§8.1。任务状态只在 [W05](stages/w05-agents.md)，长期分工见 [协作规范](collaboration-workflow.md)。

## 1. 当前现场与继承

- 用户继续指令授权ChatGPT在Codex额度不足期间临时代执行；本轮没有调用Codex、外部模型、真实行情或通知。作者自检不是独立审核。
- 工作区 `/tmp/ashare-plan-led-backlog`；当前分支 `codex/code-proposal-only`，继承 `codex/research-admission-integrity` 的完整基点 `61bb122a045ebfa38f4b9c9be6e7485efd2f9b73`，再向前包含BUG-025与方案治理成果。两个父分支都保留，不从旧master重新开发。
- 开工远端master为 `d4ebf94839b1cda33e94fe705a78cfb45e9925b2`，本片继承此基线。旧PR #4不动；原项目的四份未跟踪业务/复盘文件与运行服务不动。
- 本轮选择IMP-046的C类安全切片，范围是代码提案、实际状态与直接消费者；没有改变A/B参数、实验转正、费用、数据库schema或生产开关。

## 2. 已实现范围

- 代码开关仍默认关闭；开启也只允许文本补丁提案。移除应用内实际apply、工作树/分支创建、commit、宿主pytest/pyflakes、策略回放；不合并或推送。
- 保留原路径/白名单/受保护面与独立Git差异校验工具。读上下文前校验符号链接与文件存在，统一短路径；提案绑定完整HEAD/文件摘要，生成中发生变化则暂缓。
- 路径授权与 `git apply --check` 只检查文本适用性，不能证明代码正确、安全或测试通过。产物仅归档在 `artifacts/evolution-patches/`；归档失败不能返回proposed。
- 新C类条目为proposed，明确code_applied=False、gate_ran=False、merged=False、review_required=True。模型输入中的伪造结果字段不认证。
- 新旧C类条目都不自动把复盘改进项标applied；摘要区分proposed与执行，界面新提案显示待审、历史executed显示待复核。B类正常展示与回写保留。
- 生成前登记任务和code.propose审计，失败尝试也计现有每日上限，并兼容历史code.apply计数。生成/格式/归档失败正常回填终态；存储故障显式返回失败，不能保证不可写存储已完成回填。

## 3. 验证与证据

旧实现上的9项独立反例全部判红；测试在危险调用入口拦截，没有执行任何模型生成代码。相关后端107项通过、前端状态3项通过、改动模块pyflakes通过；最终全量、静态检查和提交版干净检出的实际结果见本批verification.json、test-summary.json与delivery.json，不将专项结果冒充全量。

发现一处旧测试的顺序依赖：A类单独跑时临时库缺alert_rule。原61bb122干净检出同一测试也失败；已在test_evolution的临时建表前显式注册其消费者模型，业务逻辑和断言不改，记录baseline-order.log。原C类测试ID保留，旧宿主执行/提交期待改为更强的不启动断言，不删除安全覆盖。

证据在原项目忽略的 `artifacts/runs/imp046-code-proposal-20260918/`：intake、before-files.tar、red、baseline-order、focused与UI日志、输入哈希和后续完整验证/交付回执。测试库、补丁与最小Git仓库均为独立夹具；没有对生产服务启用C能力。

最终完整本地结果：后端3883 collect = 3805 passed + 78 skipped，0失败；新增22项、原测试ID删除0。前端本地/UTC各684 passed/73文件；tsc、ESLint、pyflakes、Next构建和doc-health通过。全量检查后输入哈希一致，仅任务状态和交接结果回填再做相关复验；没有生产视觉/运行加载验收。

## 4. 未完成边界与交付

IMP-046尚未整体完成：参数影子转正仍需明确批准和完整效果证据；统一模型usage/token/重试/取消、跨进程配额预留、真正OS隔离运行器和能力就绪仍未验收。本片不引入隔离运行器，直接移除危险宿主执行路径。IMP-025的其它历史/任务中心消费面仍需核验，不能凭徽标修正全部销账。

BUG-026样本单位、BUG-022指数重复刻度、GOV-018平台目录退出以及持续CI节省仍保留。旧21条要求不重建第二账本，旧分钟余额不当现在余额。

本轮完整本地验证后只提交并推送功能分支，不创建PR、不改工作流、不绕CI合master。作者自检、独立审阅、云端CI、合并、生产加载分别记录；未实际发生的不写完成。没有生产数据迁移，恢复不能重新启用宿主执行，可保留静态提案或关闭C能力。

## 5. Codex恢复后的接续提示

```text
先读AGENTS、当前handoff、实施方案§8.1和W05/IMP-046。
工作区/tmp/ashare-plan-led-backlog现位于codex/code-proposal-only；父分支61bb122的BUG-027及e6f2917的BUG-025、方案成果均已继承，不重复开发。
本轮ChatGPT按用户授权临时代执行C类纯提案安全切片，模型调用为零；核最新完整SHA、实际diff、红绿和完整验证回执。
代码提案不应用、不执行、不提交；新proposed与历史executed都不能自动回写复盘applied。
先独立审阅本片，不生成作者自批；准确版本CI与审查未齐不得合并或上线。
下一安全切片优先核参数影子转正的批准/效果证据路径；完整预算/取消和其它P0分别保留，不扩展成新平台。
继续每批记录成本/重复运行和影响范围，不为节省删除断言，也不因云端额度不足停止安全的本地开发。
```
