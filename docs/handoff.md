# 当前交接：RSH-030 关键词纠错与真实新闻有限续验

> 总方案v9.13；累计U01–U55；唯一研究状态归[W04/RSH-030](stages/w04-research.md#rsh-030)。现役语义蓝图[semantic-decisions](ai/semantic-decisions.md)。用户明确本批小规模验证，不影响选股与消息，不接Jev/Decisions。

## 1. 现场、授权与最新验收

- **本轮明确授权**：2026-10-11用户要求修LAB关键词误拒，验真实财经新闻分类/依据，确认价值后再考虑接入；不改选股/消息，KEV不接、Jev/Decisions不恢复。从最新master 1085a32c9fc64d17c1f384a31e63d3824caa2ea5复用干净独立工作区建立codex/news-claim-guard；旧6次与旧合成回执保留历史身份。
- **本轮结果**：validator v1.1、提示v1.0不变；专项74通过。原合成同一输出离线15/16→16/16，原hash不变；另冻结16条核过原页的短摘录，新2次调用全部字段/引用有效。作者分类15/16、状态16/16、适用方向13/13、关键引用16/16与分组26/26；人工gold=0，分类/传导准确率与选股增益未知。愿景类别、政策影响对象、临床多期外推及审计未知变否定等问题保留；摘录省略的信息不记模型漏读，也不当公司/全文未披露，不翻绿。
- **本轮实测**：输入4552/输出7215 tokens，18.731s/18.304s为8条批耗时，非稳定性能统计。实际网关身份/费用未知。证据在artifacts/runs/deepseek-real-news-followup-20261011，来源AI生成/WAF/转载/公告日期与unknown分别保留。有限调用已结束，不扩大采样、不调提示求通过。
- **本轮运行和工程**：仅scripts/tests及既有权威文档变更，生产app/前端/选股/消息/配置/调度diff=0；8000/3000仍是原PID 52476/8157，原backend degraded保留。完整后端4928 passed/81 skipped/1既有StarletteDeprecationWarning（162.70s），pyflakes通过；前端tsc/eslint、默认/UTC各112文件1012测试（70.22s/70.16s）与Next正式构建通过。doc-health/卫生/公开扫描通过，5份报告Markdown/HTML内容和结构逐项对账通过。专项74为全量子集，不相加；准确HEAD发布事实由本轮PR的DegradedRelease、required CI、release_check及合并后CI承载。新词法检查不需生产重启。

以下为已发布首批基线，不能当本轮完整门禁：

- **当前主门**：无（有限LAB工程/实测结束；父研究待独立人工条件，不重复调用）
- **主切片首选**：无（本轮只完成获准RSH-030有限LAB与发布，不领取第二业务任务）
- **U50 降级授权回执**：2026-09-24 DEGRADED_FULL_CONTROL未撤销；2026-10-11用户明确现有DeepSeek有限新闻验证，随后“费用不用考虑”取代本批原1美元上限。作者=发布操作者，不称独立Review；独立工程复查也不当人工gold。
- **版本与范围**：从最新master基线b3519d03bfc6ce86f833ca5e8c7e297d7d01e5aa建立codex/deepseek-news-lab独立工作区。新增研究脚本/测试与原权威文档，生产app、前端、选股、消息、配置及调度diff均0。
- **真实有限验证**：24条2026-10-09至11公开initial，冻结样本SHA-256 d17273294c6ca9e9295b3dd83228c9e035d7f1994bf80341907410f75d585c20。两个输入版本各3批8条，共6调用、48输出；每组24/24字段/身份/逐字引用校验有效。输入14079、输出19953 tokens；配置别名deepseek-v4-flash，不证明中转实际底层身份；实际账单未知，不记0。
- **效果边界**：新盲表human=0/24，旧队列human=0/240；没有旧提示同输入基线，不报准确率、生产改进或选股增益。各组3批耗时：标题P50/P95=14.782/17.093s，摘要17.120/25.723s；不当稳定生产或单条延迟。无前版，novelty全部unavailable。
- **完整本地门禁**：后端4895 passed/81 skipped/1既有StarletteDeprecationWarning（171.13s），pyflakes通过；前端tsc/eslint通过（7.36s/11.78s）、默认与UTC各112文件1012 passed（69.49s/68.60s），Next正式构建exit0（15.48s）。doc-health/卫生/公开扫描通过；报告Markdown/HTML逐条357/357、结构和时间对账通过。专项41是全量子集，不相加。首轮4893 passed/81 skipped/2失败只因未暂存新代码与旧交接门不一致，修正文档/精确暂存后全量通过，原守卫/断言未变。CI账户余额读取403，写未知，未开启付费。
- **发布依据**：同分支对应PR保存准确HEAD的DegradedRelease、required CI、release_check与合并后master CI；作者自查和只读工程复查均不冒充独立Review。主目录既有服务无新app代码需要重启，研究脚本的合入不成为生产模型准入。
- **运行**：主目录8000/3000既有服务保留，本轮无重启、业务库写入或生产模型调用；仅离线runner显式复用CLI，observe_health=False、临时cwd、禁工具/MCP/skills/hooks、无会话持久化。6次调用已结束，不开常驻shadow或重复续跑。
- **证据根**：artifacts/runs/deepseek-news-lab-20261011；samples/manifest、prepared/requests/blind/identity、live/receipts/started与report-v1。旧prepare的max_rows=30只属最初材料元数据，实际24样本/6请求；代码边界已统一24，原证据保留并独立记录更正，不覆盖回执。

## 2. U49 主动审计回执 / 作者Preflight与反证

先核消费者与人工准入：四分类仅由LAB消费，旧生产category/影响力字段不互转。冻结公开输入/提示hash；漏ID、重复/外ID、格式错、引用不符、超时与未尝试均保留分母，不偷换为中性或语义insufficient。已开始无终态回收为interrupted_unknown/费用未知；摘要盲标只给同输入组计质量；24样本与6调用上限一致。故障注入专项及单独只读工程复查未发现剩余阻断项，作者反证不冒充独立发布Review。

语义反例：拟收购方向+1/0不稳定；愿景表态在实质事件/例行间变化；混合事实/预测的单event_status不能给全部主张背书；两组insufficient=0不证明召回。保留原提示v1.0与48输出身份，不能先调提示再拿旧回执冒充新验证。纯行情6条/组均方向0、无新增产业催化，澄清与风险有事实/未知边界；这些仍是作者观察，不是human gold。

总方案/INDEX/plan-registry/semantic-decisions、AGENTS、W04与handoff同步。协作/Skills/门序/CI、产品/猎场/消息/UI与源切流不适用新规则，因没有改变这些权限或消费者。

## 3. 已发布退出事实与恢复条件

Jev退出归[W07/IMP-091](stages/w07-simplification.md#imp-091)：主体PR #255已合并0e485dae4028ad2d03625223cf1cb6f30d448ec5，post-master三job成功，文档回填已进当前基线。8000从退出代码加载，本机专属插件/skill/MCP/CLI与凭据已有私有恢复包；不再清理或恢复旧服务。历史人工gold/业务库/UI恢复包未删除。

本批有限工具、真实调用、报告与完整本地验证已完成；发布事实由准确PR/HEAD回执承载。父研究只在获得匹配输入的独立人工过程/标签等新证据后恢复，先人工独立判断再看模型。无新证据不再扩样或重复本队列，不影响普通修复，不接Jev/Decisions；未来生产消费须独立效果证据与另行批准，按[开放世界持续演进](ai/continuous-evolution.md)核真实消费者、同基线、反证和退出条件。
