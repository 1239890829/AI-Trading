# 当前交接：IMP-088 首次加入记录的身份与时点显示纠偏

> 总方案v9.13；累计U01–U55；状态归[W07/IMP-088](stages/w07-simplification.md#imp-088)，字段语义归[猎场§4.4](product/hunting-decision-design.md#44-首次加入观察与影子用途imp-0882026-10-09)。用户2026-10-12直接纠偏选股时间、参考价与时点依据，不恢复REA、Jev、Decisions或KEV。

现役语义蓝图[semantic-decisions](ai/semantic-decisions.md)；外部能力仍依[开放世界持续演进](ai/continuous-evolution.md)筛选。

## 1. 现场、授权与最新验收

- **当前主门**：G4
- **主切片首选**：IMP-019
- **本轮用户直派整改**：IMP-088的G2呈现缺陷修补，工程已完成；以上两项为selector剩余候选，不自动施工第二业务任务。
- **施工基点**：刷新origin/master后从`177cb88ec42313a88931e3eb7ac52740f1e02bb6`创建codex/selection-entry-clarity，复用干净隔离工作树。主代码提交`fab25cdf2870956311c44f30d5ce9f6fd07843c9`，仅4个业务/测试文件。
- **上一批发布**：[PR #261](https://github.com/1239890829/AI-Trading/pull/261)经准确HEAD DegradedRelease/release_check合并为177cb88e，post-merge run38213885402三job completed/success；原分支已清。BUG-033跟价与全域读取修补保留，IMP-019长期实源SLO仍为部分完成，不重复实施。
- **本轮实源取证**：只读数据库与原GET确认当日每日名单0只、10条盘中ranked均有selection-entry-v1；所取盘中参考区有recorded事实，其余缺首次合格归档，不能把参考集合冒充真实入选。详细源响应私有存于忽略artifacts，未调用模型、生成或执行端点。
- **本轮实施**：原梯队补reference_only展示身份，统一PickCard按参考/每日/盘中分源显示。参考缺归档展示原因，原入选后封板保留原时间价；同股主卡展示两条独立首次事实。盘中quote_as_of实为整轮行情快照钟，显示名修正；每日仍为逐股报价源钟。没有用现价、首封或读取钟补填历史。
- **当前生产现场**：最近已核8000 PID9218、3000 PID9247，Next build kQDj1nWEDFtL08tts-ZqI仍服务PR #261；本轮新版本待准确HEAD发布后正常切换。既有鉴权、真实数据、源龄过期保护保持。当前时点不冒称新代码已运行。
- **完整门禁**：后端5019 passed/81 skipped/1原有Starlette弃用提示（195.90s），pyflakes通过；前端默认/UTC各117文件1199 passed（102.69s/91.19s），tsc/eslint/生产构建通过。文档/账本/环境守卫204项通过；doc-health/卫生/公开扫描通过。完整后端与完整前端串行，前端单worker。
- **UI验收**：新生产构建在隔离3190读取现役8000原GET，桌面1280及390/320浏览器预览已验参考缺项、原时间价、展开快照依据与长版本断行；页面无横向溢出，记录区域宽度/scrollWidth为395/395、330/330、260/260。每日同股双源仅有行为夹具证据，今日真实每日名单为空。不是手机硬件或全站SLO验收。
- **U50 降级授权回执**：2026-09-24 DEGRADED_FULL_CONTROL未撤销；作者=发布操作者，不声称独立发布Review。仍须当前PR/HEAD DegradedRelease、required CI、release_check、post-merge CI与分支清理。

## 2. U49 主动审计回执 / 作者Preflight、反证与传播核对

逐一核实际写入与投影、参考身份、容量门、首次提交钟、两源同股归并、旧版/读取失败、partial有效字段、来源错配、无时区与非法数值、查看依据的事件冒泡和窄屏长版本。团队反证未发现阻断缺陷；团队检查不替代当前模式发布回执或手机实屏。

产品闭环、猎场§4.4、细功能审计§42、W07与handoff同步纠正显示语义。总方案、plan-registry、AGENTS/Skills、INDEX和语义模型蓝图不适用新机制：没有新增业务事实源、API合同、规则、采样节拍、模型准入或页面，只修正已有字段标签与卡片身份呈现。来源优先级、选股评分/容量、通知路由、权限及模拟硬门保持。

## 3. 恢复、运行收口与下一步

恢复按本轮Git差异；不回滚业务库或重新生成历史首次时点。原编译版本在切换前另存唯一恢复包，正常停止确认归属的旧进程，无reload。实际部署版本与服务PID/BUILD_ID以本轮忽略证据runtime/activation.json及准确HEAD发布记录为准；没有收到成功回执不得重试覆盖备份。

当前缺归档或未保存逐股源钟仍明确未知。新入选读侧可先于下一持久快照归档出现；GET只投影既有事实，不为消除未知而写入样本、重选或发通知。此修补不证明未来无缺项、可成交或策略收益；新缺陷回原owner，长期SLO归IMP-019。
