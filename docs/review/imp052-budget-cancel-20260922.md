# IMP-052 · Agent 预算、统一 usage 与取消终态审计（2026-09-22）

> 性质：`G4 / IMP-052` 最后一纵切的工程审计证据。任务状态唯一写在 `docs/stages/w05-agents.md`；本文不是第二账本。
> 基点：`master@65b7922763057e12bc34a70a331d87ffa12d894a`；隔离分支 `chatgpt/imp052-budget-cancel`；模式 `DEGRADED_FULL_CONTROL`。

## 1. 开工前真实缺口

开工前预算与取消并不是同一事实系统：

- `evolution._budget_status()` 通过 `AgentAudit` / `AgentTask` **先计数、后调用**，两个进程可同时看到最后一个额度并双双发起模型请求；重启或换任务也没有原子预留事实。
- Jev 只有进程内 metrics + metadata JSONL；议程、C 类提案、告警 LLM 又各自用局部计数，无法从一个后台查询面回答「今天调用了什么、实际 token 是否已知、重试几次」。
- provider 未返回 token、请求超时或进程在模型调用后死亡时，旧逻辑没有统一的 `unknown` 用量语义，存在把未知消费误当 0 的风险。
- `cancel_task()` 在当前进程找不到 asyncio handle 时会直接把数据库写成 `canceled`；如果真正执行者在另一个进程，它仍可继续运行并产生副作用，状态会撒谎。
- task/model 的输入、输出、wall-time、retry 上限没有一个统一、可执行的 Agent policy；C 类每日 1 次同样基于 audit 计数而非原子 slot。

## 2. 权限与预算矩阵

| 能力 | 当前 authority / 预算事实 | 失败语义 |
|---|---|---|
| C 类代码能力 | 仍为纯 patch 提案；应用内不得 apply/test/commit/merge/push | 预检不合格不占自治 task；真正进入提案尝试后占 `autonomy_task + autonomy_llm + code_proposal` |
| 参数晋级 | 继承上一纵切：独立 promotion token + candidate/baseline/shadow digest + repo artifact SHA + 一次性事务消费 | 无批准、漂移、过期、重复、证据文件变化均 fail-closed |
| 自主模型调用 | `agent_resource_usage` 按北京日 + `autonomy_llm` + numbered slot 唯一约束；默认 8/day | 两进程抢最后 slot 只能一个成功；started 后 token unknown 会阻断当日后续自主模型调用 |
| 自主改进动作 | `autonomy_task` numbered slot；默认 3/day | A/B/C 真正尝试才占；data-health 事实留痕不占自治改进额度 |
| C 提案独立日上限 | `code_proposal` slot；默认 1/day | 失败尝试也占，避免重试刷模型；部署日旧 `code.propose` 不被重启清零 |
| 普通业务 LLM | 不偷占 autonomy 8 次；写统一 metadata telemetry | usage 不可得则 `usage_known=false`，不估算、不填 0 |
| Jev | 保留原 metrics/JSONL；生产 lifespan 注册唯一 metadata-only unified sink 到同一表 | 成功结果若 durable sink 写失败则返回 `usage_accounting_failed`、不得采用；原 Jev telemetry 仍保留，state/questions 不进入 receipt |
| Agent task 取消 | DB 持久 `cancel_requested_at`；owner 轮询/本地 handle 接收取消 | 请求方无 owner handle 时**不**写 canceled；owner 确认停止或重启对账后才进入 canceled |
| Agent task 超时 | `agent_task_timeout_seconds` 默认 600s | `failed / TaskTimeout`，与用户取消分开 |

## 3. `agent_resource_usage` 契约

新增 Alembic `b5c9e7a2d4f1` 与模型 `AgentResourceUsage`。预算型记录用 `(budget_date, scope, slot)` 唯一约束，SQLite 事务负责跨进程竞争；telemetry 的 `slot=NULL` 不消耗自治额度。

记录只保存 metadata：scope/purpose/provider/model、attempts、timeout、输入/输出字符数、provider 返回的 input/output token、状态与安全错误类型。**不保存 prompt、messages、Jev state/questions、模型正文或凭据**。

生命周期：

`reserved → started → succeeded | failed | canceled | unknown`

只有从未 `start` 的旧 reservation 才能按短 TTL 回收；started 后即使进程死亡也不能自动退款，因为外部请求可能已经发生。自主模型 scope 如果发现当日 started/terminal 且 `usage_known=0`，后续 reserve 直接 `usage_unknown` fail-closed。

`GET /agent/resource-usage` 只读返回当天 summary 与 metadata receipts，用于后台审计；普通前台不新增调试/预算页面。

## 4. 输入 / 输出 / 时间 / 重试预算

默认 policy（均可由正式 `ASHARE_*` 配置覆盖）：

- autonomous LLM 8/day；autonomous tasks 3/day；C proposal 1/day；
- model input 高水位 250,000 chars；output 高水位 150,000 chars；
- model timeout ≤180s；额外 retry ≤2；Agent task wall-time ≤600s；
- never-started reservation TTL 300s。

输入在外部调用前校验；非流式输出在采用前校验。Assistant SSE 在每轮发送前核输入，并在每个 delta 发给用户**之前**累计输出字符，越界立即关闭底层 `ChatStream`，不把超限 delta 发送给用户。

Jev 原有最多 3 次 attempt 被统一 policy 约束为 `1 + min(2, agent_model_max_retries)`，并回执真实 attempts；timeout 超过全局 Agent policy 时网络前拒绝。

## 5. 取消与真实终态

旧跨进程错误语义：调用取消接口的进程若没有 `_HANDLES[task_id]`，直接把 DB 改为 canceled，但真实 owner 可能仍在跑。

当前契约：

1. cancel API 先持久化 `cancel_requested_at`，写 `task.cancel.request`；
2. **queued** 且本进程 handle 尚未启动时可直接 cancel，handle done callback 确认真正未执行后写 `canceled`；
3. **running** 任务不再调用 `Task.cancel()` 强杀：Python 无法终止已经运行在 `asyncio.to_thread()` 的同步 worker，强杀只会让 asyncio owner 结束而线程继续，造成假 terminal；
4. running owner 在 ReviewService/handler 的 bounded stage 之间执行 cooperative checkpoint。收到 cancel intent 后，不再启动下一 stage；当前已经开始、不可中断的同步/外部步骤先真实 drain，再由 checkpoint 写 `canceled`。期间 API/前端保持 `running + cancel_requested_at` /「取消中…」；
5. wall-time 也使用同一 cooperative deadline：deadline 已过但 in-flight stage 未结束时仍保持 running；stage drain 后才写 `failed / TaskTimeout`。不会出现 DB 先 terminal、后台线程仍继续的窗口；
6. 服务重启对账：残留 queued/running 且已有 cancel intent → `canceled`；无 intent → `failed / Interrupted`。

因此这里的“取消”是**可证明的 cooperative drain**，不是 OS 线程强杀。已经完成或已经开始的不可中断步骤及其真实用量不会回滚；一旦 checkpoint 观察到取消/超时，不再启动后续阶段。这样「请求取消」「确认停止」「进程崩溃」「超时」和“in-flight 尚在 drain”不会混用一个终态。

## 6. 部署日不重置旧额度

新表第一次上线时如果从空表开始，会把当天已经发生的旧 `agenda.generate / triage.llm / code.propose` 与已执行议程动作忘掉，相当于服务重启后凭空恢复预算。

迁移因此只在**首次建表**时 backfill 当天历史：

- 上述旧模型 audit → `autonomy_llm / state=unknown / usage_known=0`；
- 旧 `code.propose` 同时保留 `code_proposal` 日上限；
- 当日 AgentAgenda 已执行/已提案的 A/B/C（data-health 除外）→ `autonomy_task`。

在真实生产 DB 的 `/tmp` 副本上实测：升级前当天有 1 个相关模型 audit、1 份议程；升级后得到 `autonomy_llm=1 unknown` + `autonomy_task=1 unknown`，`PRAGMA integrity_check=ok`。`f4a2c8e1b6d3 → b5c9e7a2d4f1 → f4a2c8e1b6d3 → b5c9e7a2d4f1` 往返均成功，生产 DB 未在该验证中修改。

因此正式上线当天，旧模型 token 不可追溯会让剩余 autonomous LLM fail-closed；第二个北京自然日自动进入纯新 ledger，不需要人工清零。

## 7. 来源默认值 vs 当前运行有效值

只核非敏感 Agent 配置，不读取任何密钥：上线前运行进程 PID `74562` 没有以下 `ASHARE_AGENT_*` 环境覆盖，主仓也没有对应 root/backend `.env` 覆盖，所以现有有效值等于源码默认值：autonomy=true、code_change=false、LLM=8/day、task=3/day。本切片新增的 I/O/timeout/retry/TTL 字段正式部署后也先按上述默认值工作；验收没有修改真实配置来制造通过。

## 8. U49 反例与修正

本片主动抓出的关键问题：

- check-then-call quota race → DB unique numbered slots；
- remote cancel 无 handle 却写 canceled → durable intent + owner confirmation；PR #103 合并后继续反证又发现 `Task.cancel()` 对 `to_thread()` 只取消 await、不杀线程 → running 改 cooperative checkpoints，新增 cancel/timeout 两组“线程未 drain 前状态必须仍 running”反例；
- C 类开关关闭/路径非法也占 task slot → task reservation 下沉到稳定基点之后；
- 部署/重启当天额度清零 → migration legacy backfill；
- 模型返回非字符串时 usage 代码先 `len(raw)` 改变业务错误分类 → 类型安全计账；
- Assistant 流结束后才检查 output 太晚 → delta 发送前实时高水位；
- Jev JSONL/metrics 成为第二套成本查询面 → 保留原运维 telemetry，同时由 lifespan 注册**唯一** metadata sink；去掉逐调用点重复计账，且成功 Jev 若 durable sink 失败则 fail-closed 不采用；Jev 也在网络前/采用前执行 Agent input/output 高水位；
- task/purpose hopping 可能被误认为新额度 → 同北京日/scope 全局 slot，专项反例证明换 task/purpose 仍耗同一预算；
- 测试间进程级 Jev sink 可能泄漏 → `_reset_metrics_for_tests()` 同时清 sink；
- meta-review output-budget 失败已先持久 failed receipt 时，异常路径不得再写第二条 receipt。

## 9. 验收与恢复

已完成的本地证据包括：预算并发/unknown/token/I-O/retry/timeout、任务取消/重启、Jev/DeepSeek/News/Event/Review/Meta/Assistant、迁移 parity 与真实 DB 副本 upgrade/downgrade；最终全 backend / frontend / repo gates 与发布证据以本分支最终 HEAD、PR exact-head DegradedRelease、required CI、`release_check`、post-merge master CI 为准。

恢复路径：回滚本提交与 `b5c9e7a2d4f1` 可移除新 usage 表与 cancel_requested_at 列；C 类仍保持纯提案，参数 promotion 的上一纵切安全门独立存在。不得通过删除 started usage 行、手工把 unknown token 改 0、或清 cancel intent 来“恢复”额度/任务状态。

## 10. 完成裁定

在最终工程门与发布门全绿的前提下，本片补齐 IMP-052 剩余预算/取消验收：统一 metadata usage/token 查询面、跨进程 quota 原子预留/恢复、unknown usage fail-closed、输入/输出/时间/重试预算、跨进程 cancel propagation、真实终态、源码默认与运行值核对、跨任务累计预算。结合前序 C 纯提案与 promotion authority 两纵切，并在 PR #103 post-merge U49 的 cancel/to_thread 真实性 follow-up 通过 exact-HEAD 发布门后，`IMP-052` 维持**已完成**；后续只按 U48/GOV-027 做机制生命周期复核，不再以“预算/取消未实现”为由保留 P0 阻断项。
