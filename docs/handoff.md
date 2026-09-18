# 当前交接：GOV-022 结果状态与断点留存原型

当前方案仍为 [v9.2](implementation-plan.md)；本片状态归 [W08/GOV-022](stages/w08-governance.md#gov-022)，用法和限制见 [协作规范§3.3](collaboration-workflow.md)。原Codex的21条要求对账保留在实施方案§8.1。

## 1. 当前现场与验收

- 工作区 `/tmp/ashare-plan-led-backlog`，当前分支 **`codex/collaboration-runtime-state`**，继承 `codex/shadow-review-required@f4f20a71b7684c83feb67ec50ad83d1f30fe9444`。业务基点仍为d2cd90e及其前序，父分支不动；先核真实HEAD/差异，不退回旧目录。
- 用户要求一次授权后连续推进，不逐项请求“继续”。前轮f4f20a7只有规则更新，并没有启动自动调度。本轮由ChatGPT在Codex无额度期间临时代执行首个运行原型，没有调用Codex或其它模型。
- 新增 `scripts/audit/collaboration_state.py` 与 `backend/tests/test_collaboration_state.py`；只负责准确请求/工作区/版本及thread/turn记录、终态接收、重复记录抑制和断点留存。不是模型执行器，也不是审核批准者。
- 本地专项44通过；完整后端 **3895 passed / 78 skipped**，0失败；有1项Starlette测试客户端弃用警告，未因此更改依赖。pyflakes、文档检查及最后相关回归129通过。前端源码未变，本轮没有重跑前端构建，不将旧结果报成新测。
- 另用真实临时Git仓库和四个独立CLI进程验证：reserved → running → awaiting_review，重新查询得到相同记录；输入为合成完成事件，不是真实Codex往返。待审记录的approval为null、web_request_sent为false。
- 进一步thread/read恢复核验写入被连接工具拦截，未应用、未换通道绕过。非协议类型status目前会触发TypeError，记录仍为running，显式recover后变uncertain；已留反例，是待修残余，不能称生产可用。
- 持久证据：原项目 `artifacts/runs/gov022-runtime-20260918-160818/`，含intake、原文备份、focused/full日志、cli-smoke、known-limits、verification、final-checks及delivery。当前精确提交以Git和delivery为准。

## 2. 当前能做与不能做

工具可接收App Server格式JSONL、将真实终态绑定到明确请求，并在进程退出后查询状态；请求受理、item完成、普通文字“通过”均不能冒充整个turn完成。
EOF或原驱动失联只记uncertain，不重新发送、不终止远端任务，也不宣称远端已经停止。恢复核验适配尚未落地，未知状态不能自行继续。
本地SQLite是运行传输现场，不是第二份任务账本；同一库内的并发/去重约束不是跨用户权限隔离。可信派工、审批来源、预算预留和真实控制通道仍须后续实现。
本轮没有启动常驻程序、浏览器自动发送、真实派工、网页唤醒、自动批准或下一任务；没有PR、主干合并或生产变更。原完整CI/独立审核条件不变，作者自检不等于独立审核。

## 3. 下一实际实现与恢复

先补并验证异常status的统一失败处理及thread/read精确恢复核验；再接App Server派发/完成回传和指定网页审核入口。保持准确ID/版本与断点，禁止重放旧“通过”或自动重发未知请求；接口验收前不承诺无人值守。
Bridge已由用户安装，先前Native Host合成接收成功；这不是本片新增证据，也不代表草稿/网页双向发送已通。软件运行不等于自动协作在运行。
以前的影子评估、C类纯提案、BUG-025和BUG-027成果全部继承，细节与历史回执从Git和所属阶段读取，不在本交接堆叠旧日志。BUG-026、BUG-022、GOV-018及持续成本复核仍保留。
本原型没有数据库迁移或应用运行路径；可停止使用该工具并回到原文件交接，保留运行库和失败日志。不要恢复危险自动转正，也不要删除前任成果。

## 4. Codex恢复提示

先只读核实际工作区、当前分支和HEAD，读AGENTS、本页、协作规范§3.2/§3.3及W08/GOV-022。原型仅记录结果，不是自动执行器；尤其不能把uncertain重发为新任务或伪造网页批准。
按最新一次启动授权推进网页派发范围，不要求用户每项重复说继续；但尚未接通的调度与送审程序必须实际实现并验收，不以文档更新代替。先检查本片已知残余和44项测试，不重建此前业务成果。
