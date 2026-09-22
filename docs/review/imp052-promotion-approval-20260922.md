# IMP-052 参数晋级授权纵切审计（2026-09-22）

## 范围与基点

- 基点：`master@30457a85bb8db3986a77964dcde9f326e99b567a`。
- runtime selector：static/effective 均为 `G4 / IMP-052`，无条件任务抢占。
- 本纵切只处理“独立批准来源 → candidate/live-baseline/shadow-evidence/effect-artifact 绑定 → 一次性原子消费 → post-guard”。
- 不修改任何真实参数值、策略阈值或生产开关；usage/token、跨进程 quota、取消/超时/重试预算不并入本刀。

## U49 Preflight 与反证

开工时已有 `promote_shadow()` 全封死止险，但尚无可验证的研究候选晋级通道。反证重点：

1. 普通 `require_write_token` 在 local/空 token 下不能证明独立审批；模型 evidence 内的 `approved=true` 不能成为批准。
2. 人工 review 到 consume 之间，candidate、live baseline、shadow evidence 或效果 evidence 任一变化必须使批准失效。
3. 批准不得永久常驻、重复消费或被另一候选借用；撤销/过期/字段篡改均 fail-closed。
4. 晋级与既有 30 日退化守护不能出现“参数已生效但 experiment 没挂上”的部分事务。
5. durable DB commit 后 runtime override 刷新仍可能失败；返回值和 mutation task 必须反映这个真实部分成功，不能写成完整 succeeded。
6. effect artifact 不能只收任意字符串/hash；必须验证仓库内真实文件、当前 SHA-256、允许目录以及 symlink 解析后的真实路径。
7. 新增 GET 路由必须进入 endpoint smoke，不能因新 path 参数被静默跳过。

执行中检测到一次同 worktree 的未提交并发残留；检查时已无活动相关写进程。没有直接覆盖或删除这些内容，而是当作不可信候选补丁逐条审计。最终保留其中与本片目标一致的 24h TTL、supportive-shadow 门、post-guard experiment 原子挂接和 runtime reconciliation；修正 runtime refresh 失败的 mutation 终态为 `failed`。该事件不改变 single-writer 规则。

## 最终契约

- `ASHARE_AGENT_PROMOTION_TOKEN` 与普通 `ASHARE_API_TOKEN` 分权；默认空即关闭批准写入口，少于 32 字符或与普通 token 相同均 fail-closed。
- review package 冻结 exact candidate / live baseline / shadow evidence 三组 SHA-256；批准必须回显 exact digests。
- effect evidence 使用 `repo://` 仓库相对路径，只允许 `docs/review/`、`docs/research/`、`artifacts/`；审批和消费均现场重算文件 SHA-256，path traversal、缺文件、内容漂移和 symlink 跳出允许目录均拒绝。
- approval 最长 24h、可撤销、一次性消费；approval record 自身有 digest，字段篡改拒绝。
- promotion 同一事务执行 candidate CAS、approval consume、live-baseline CAS、参数覆盖写入与 post-guard experiment 创建；任一冲突整体回滚。
- post-commit runtime refresh 成功才记 `runtime_refreshed=true`；失败返回 `restart_required=true` 并把 mutation task 记为 failed。精确重试不重复写 DB/消费 approval/创建 experiment，只做 runtime reconciliation。
- 本契约证明的是批准主体边界、对象身份和证据版本完整性；**不把文件存在/hash 正确升级成研究效果已证明**。效果结论仍由原 research/validation owner 负责。

## 验证

- 专项反例：伪批准、review digest 漂移、candidate/evidence/baseline TOCTOU、approval 重放/双批准/篡改/过期/撤销、弱或共用 promotion token、effect 文件缺失/hash 漂移/path traversal/symlink escape、post-guard baseline 失败、runtime refresh 部分成功。
- 参数/实验/写鉴权/迁移 parity/GET endpoint smoke 联合回归：通过。
- Alembic：单 head `f4a2c8e1b6d3`。
- `pyflakes app tests scripts`：通过。
- backend 全量 pytest：最终重跑 100% 通过，exit 0。


## #100 合并后 U49 纠偏

PR #100 主体合并后继续按 U49 重新问“如果绕过 HTTP 路由，批准权限还成立吗？”，发现首版 `require_promotion_approval_token` 只在 FastAPI dependency 上校验：正常 HTTP 请求安全，但同进程内部代码若直接调用 `approve_shadow_promotion()` / `revoke_promotion_approval()`，service 本身并不要求专用凭据。该形态不满足“批准来源必须独立于候选/evaluator”的边界，因此不把 #100 绿灯当作永久结论。

后续修正把 promotion credential 判定收敛到 `core/auth.py::validate_agent_promotion_token`：默认空、少于 32 字符、与普通 API token 相同、凭据不匹配均 fail-closed；HTTP dependency 只读取 `X-Agent-Promotion-Token`，再由 `check_agent_promotion_token` 把同一 domain 判据映射为 HTTP 状态；service 创建/撤销批准也要求显式传入同一凭据并再次校验。新增 direct-service bypass 回归，证明绕过 HTTP 不能绕过批准 authority。此纠偏不改变 candidate/evidence/baseline/TTL/一次性消费/后置实验契约，也不修改任何真实参数值。

权限边界必须准确表述：`ASHARE_AGENT_PROMOTION_TOKEN` 是**同一服务进程内、独立于普通 API 写权限的高权限共享凭据**，不是 OS principal、登录用户身份或密码学隔离。它能防普通 write token、模型 evidence 与“忘传批准凭据”的内部调用直接创建/撤销批准；但能读取服务器配置的受信任代码仍在同一 trust boundary 内。因此该 secret 不得下发浏览器、不得进入模型/agenda/evaluator 输入，也不得把 `operator/promotion_token` 回执描述成已认证的独立自然人身份。

## 未完成

IMP-052 保持“部分完成”。下一纵切只处理：统一 usage/token 计账、跨进程累计 quota 与原子预留/恢复、取消传播、超时与重试预算、任务真实终态。不得因本片完成宣称完整 Agent 权限/预算系统已经交付。
