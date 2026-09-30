# 当前交接：IMP-046 条件路由与真实执行证据

> 唯一状态在 [W05/IMP-046](stages/w05-agents.md#imp-046)。上轮 IMP-045 由 PR #190 合并至 `98669c7013d9ba6bee9c17286a8de94f4cf8d340`，其新理由配对与独立标签条件仍由原任务维护，不重复空集扫描。

权威入口：现役 [Jev 蓝图](ai/jev-integration.md)，[持续演进](ai/continuous-evolution.md)，总方案 v9.13 在 [实施方案](implementation-plan.md)。当前累计 U01–U55。

## 1. 现场、模式与门序

本轮从干净 `master@98669c7013d9ba6bee9c17286a8de94f4cf8d340` 同步远端并重算 selector：G0–G4 无 actionable blocker，普通 fallback 选 G4/IMP-046（P1/门内序30/非阻断），硬依赖与效果前置无。只领取本任务；RSH-031 的历史研究与后续主任务未在本轮另行开工。

**U50 降级授权回执**：用户 2026-09-24 的本机 Codex `DEGRADED_FULL_CONTROL` 仍有效，直到明确撤销。作者负责 U49 反证和 exact-HEAD `DegradedRelease`，不声称独立 Review；完整本地门禁、三项 required CI、release_check、post-merge CI 与分支清理继续强制。授权不扩大真实通知、券商、部署、生产库写入、付费或新模型/阈值准入。

## 2. 本轮交付与实测

分支 `codex/imp046-route-receipts`；工程提交 `d964c05fb4940602423f89cadac6a58b83fa3e92`。协调文档另提交；准确发布 HEAD/PR 归 `DegradedRelease`。

- 助手先按当前只读依赖过滤候选；明确数据请求、私人请求/页面、多轮历史回原集合。公共页面不外发 title/query/context；仅真实多组残余歧义进入原 Jev Noul，非法响应回原授权集合，不修改阈值或执行白名单。
- 分发器在缓存/handler 前核注册身份、请求依赖与签名，保留原各工具参数校验；回执区分拒绝、参数错误、返回、缓存和失败。建议可缩提示词，不能授予工具；returned 不代表源数据有效或回答正确。
- 每进程单路由 worker，无队列；忙时弃权。SSE `done` 先发送，取消屏蔽下真实 drain 再收连接与 metadata-only 审计；不把取消协程当成杀线程，不宣称 HTTP 清理零耗时。审计/telemetry 失败不破坏原答案。
- 复用 AgentAudit 与受鉴权 agent/audit：记录请求/registry 指纹、可用/未就绪候选、建议、实际提示采纳、分发与执行、回退、必要工具缺口/误否认线索及模型用量；不保存问题/历史/参数/工具正文/私有账户。human coverage 保持 null。做 T 助手查询只读既有记录，结算归原维护流程。

**真实条件与边界**：现有本地库只读查询新 action `assistant.route` 回执 0；配置读取 Jev enabled / assistant shadow，但不证明运行进程已加载新代码。本轮没有新增业务模型调用、重启服务或写生产库。全局 capability wrapper 与固定 JevRouter 的 pin/clean 一致；已有权限过滤 smoke 保留历史身份，未重复收费。复杂浏览器退回既有正常浏览器能力的规则未改。工程结果不证明独立必要工具覆盖、股票收益或净费用节省；同条件人工质量/费用对照仍是未来 cascade 效果采用条件。

**U49 主动审计回执 / 作者 Preflight 与反证**：核必要工具漏用、旧 used 把拒绝当实际执行、未就绪/未注册/签名、缓存先于拦截、私人标题/查询/历史外发、非法响应、无竞争/明确请求浪费调用、取消不杀同步线程、忙时积压、done/HTTP清理差异、审计失效及名义只读查询写库。上述真实边界均有定向行为反例；未借 Jev 扩交易或模型权限。作者自审与发布同人，最终仅按授权模式形成 DegradedRelease。

**本地门禁**：定向195 passed（3.34s），其后新增多轮历史弃权用例随全量验证；后端干净无部署缓存检出4533 passed/81 skipped（150.29s），全量 pyflakes通过。前端tsc/eslint通过，78文件736测试默认/UTC各通过（46.16s/47.75s），Next16.3.3生产构建通过，构建生成的next-env路径已按已知diff恢复。文档/selector/公共与卫生守卫最终121 passed（5.04s）；doc-health/workspace hygiene/public scan通过。曾因新hand-off标题缺机器约定“U49 主动审计回执”导致2项文档守卫失败，已修标题并原样重验，未改守卫。Jev Review baseline→最终对应工程提交：correctness 7.6→7.9、reliability 7.7→8.2，工具小幅变化归 unchanged；只是辅助作者判断，不冒充独立审核。已修畸形响应、telemetry失败、实际提示采纳与AnyIO取消窗口，没有继续为追分改动的依据。模型版本/费用未知，回执与JUnit只进忽略artifacts。

**传播核对**：Jev蓝图§6/§23.5、INDEX、W05单点状态、细功能X40与本页已更新。本次落实已批U51/P22，没有新模型/依赖、工具链、权限、策略或产品导航取舍；总方案/plan-registry/产品闭环/猎场/AGENTS/Skills/登记册无需变更，原只读和独立效果准入原则继续有效。文档只更新本轮事实，不重写旧smoke或RSH结论。

本轮仅在本地实现和隔离验证，生产服务未重启/部署。发布按准确HEAD的DegradedRelease、三项required CI与release_check，合并后核master CI及分支清理；临时检出/测试沙箱按GOV-026收口，只留紧凑回执，不删除业务数据。

## 3. 条件与下一候选

IMP-046 工程收口；持续维护仅在新版本真实会话、独立人工标签或新反例形成后收集质量、升级、tokens、总等待和费用，未满足效果条件不晋级cascade或恢复model/effort自动切换。新工程缺口由原owner按阶段门登记，不无限复跑空回执或历史smoke。

重算selector首选G4/IMP-025，其后IMP-019。本轮仅报告，不自动领取第二业务任务。

- **当前主门**：G4
- **主切片首选**：IMP-025
- **当前门候选顺位**：IMP-025 → IMP-019
