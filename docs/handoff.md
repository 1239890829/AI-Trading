# 当前交接：BUG-020 单位纠偏工程交付与发布核验

> 总方案v9.13；累计U01–U55。阅读与WS整改[W07/IMP-093](stages/w07-simplification.md#imp-093)已由PR #270发布并运行；直接上游单位反例按[W00/BUG-020](stages/w00-foundation.md#bug-020)既有同根回归条款止险，不启动第二个产品优化任务。

现役语义判断依[semantic-decisions](ai/semantic-decisions.md)；外部能力依[开放世界持续演进](ai/continuous-evolution.md)筛选。

## 1. 现场、授权与最新验收

- **当前主门**：G4
- **主切片首选**：IMP-019
- **候选顺位与执行边界**：IMP-019；BUG-020单位工程整改关闭，本轮只完成其准确HEAD发布/单实例运行核验，不领取下一业务任务。
- **基点与分支**：origin/master `ea1e376ccdcbe313361e58d1040f6294f6a586d4`，`codex/limit-pool-unit-fix`；2026-10-13 fetch无漂移。主树未跟踪evolution与业务数据保留。
- **获准范围**：PR #270阅读与WS产出保留；当前仅纠正东方财富ZT价格单位、真实字段回归、429回退消费者与旧单位说明，不改选股/通知、TTL、轮频、源优先级或归档。
- **运行基线（可变）**：3000运行PR #270构建pjXxp4V2Z0WlEgJ4WGHPj/PID89120；8000现役单实例PID94616尚未加载单位修复，无reload。正式WS45秒三次pong且无中途断线；收盘stale保留。不为验收新增模型、通知或模拟交易。
- **当前证据**：PR #270准确HEAD 59f8fd7637a95c97c7cfea41b8bab0b6285b6f61、合并ea1e376c和post-master三job成功；阅读PC/320/390与实际原因弹层已验。新ZT反例真实provider及归一化先红2项；修后针对性46项通过。单位完整后端5229 passed/81 skipped、pyflakes与前端默认/UTC各1428 passed、tsc/ESLint/生产构建通过；准确HEAD发布与后端加载单独待验。
- **U50 降级授权回执**：2026-09-24 DEGRADED_FULL_CONTROL未撤销；作者自审和协作反证不冒充独立网页Review。每个PR准确HEAD另有DegradedRelease。

## 2. U49 主动审计回执 / Preflight

原版Impeccable Operate/Read→原版Taste Scan/Diagnose/Fix→Interaction Design状态与键盘→一次批量PC/320/390实屏→一批修复与确认→完整工程门/准确HEAD发布。保持现有深色视觉系统，不恢复旧融合技能权威。

已识别根因：ExplanationMap是线性steps且自动打开首段；NarrativeText/ArticleReader按长度分组而无资料概览；FactTimeline被用于方向、等级、价格等非时间指标；事件payload只透传一个方向并把结构摊成meta/body。整改将透传原事件快照、明示关联边、分离真实时间和普通指标，核心摘录与条件默认直接可读，完整长文可选深入。

反证范围：多标的不同方向不可互套；来源身份/撤回/时点不回填；数值、条件、否定和表格必须有完整出口；素材提取只是原文匹配不是事实核验；更换/移除对象不能保留旧节点；空字段不能画成已核因果；内部按钮不冒泡；阅读切换不产生新GET/模型/已读写入；性能不增加轮询/延TTL。

用户再次指出固定图形目录与逐节点查看不符合intelligent UI，本轮改为默认信息可读、内容决定布局；涨停原因同步。新增WS持续断线排查，先取握手/auth/URL/close-code证据，确认后最小修复，不以源降级冒充连接失败。

## 3. 验收与恢复

代码提交f73a9651：完整后端5227通过/81跳过/478.61s，pyflakes通过；前端默认与UTC各1428通过/132文件（112.40/171.55s），tsc、ESLint（既有1 warning/0 error）、Next16.3.3生产构建通过。文档守卫180项、卫生、公开扫描已通过；最终回填另复验文档门。原版设计反证的跨行连线、嵌套原因及长回复菜单问题均关闭；不是独立网页Review。PR #270准确HEAD发布及正式运行取证已完成，见§5。构建pjXxp4V2Z0WlEgJ4WGHPj的隔离3105接现役8000：30秒握手/子协议/snapshot/pong正常、6个stale帧、18标的、无中途断线，无凭据403。收盘数据仍stale，不掩盖源质量，也不证明未来交易窗无卡顿。正式应用PC/390原因弹层核完整原句、来源、交易日与374px容器；隔离真实公开新闻PC/320/390与助手展示夹具分开标注。3000已完成PR #270构建切换；该PR后端无代码改动，单位纠偏另按§5有条件激活。保留旧构建与PR恢复点，按本次Git差异回退阅读表现；源数据、已有归档、选股与消息硬门保留。不恢复REA/Jev/Decisions/KEV。既有INDEX和产品闭环§45沿本任务更新，无新业务页面/模型/数据源/权限，不产生新的重大准入决定。

## 4. 交易时段跟进边界

本机官方交易日历覆盖2026-10-13，盘前不能当连续交易证据。用户授权下建立仅当天09:35/13:35两次的线程跟进，每窗先冻结实际运行版本/负载，再只读观察5分钟；本机应用与前后端需在线。参数/脚本哈希与现场证据在忽略的`artifacts/runs/live-performance-20261013`，状态仍归W06；第二次后停用，不产生永久ping。

现有health/metrics/caches/providers/schedulers读面已盘前实发5次200，没有触发采集/模型；交易窗collector盘前拒绝且0请求。使用Hub批报价不主动回源/新增订阅；同股分钟/日K有界读取。保留额外观测请求、失败和unknown。滑动1h分位/count不能直接差分成窗口分母；缓存factory不等于供应商HTTP；图表meta属Hub，日K日期不是秒级源钟。StockDetail冷读可能自动调用摘要模型，不为测试额外激活。没有浏览器性能trace就不宣称刷新无卡顿。确认问题后再按完整工程/发布门最小修复，不改TTL/轮频/freshness、选股或通知规则。

## 5. 本轮运行反例的事实止险

PR #270已精确HEAD发布、post-master三job成功；3000已加载pjXxp4V2Z0WlEgJ4WGHPj，后端94616未重启。正式3000/本机LAN HTTP200，WS45秒三次pong/快照及源状态帧，无中途断线，无凭据403；收盘stale保留，不证明盘中SLO。

15:49:56 THS429→EM揭露旧ZT价格/100错：官方15:53:16 qdate20261013的002343 p7360应7.36，原输出73.60；THS/腾讯缓存一致7.36。代码与错误说明/测试同步修正/1000；来源工具输出摘录及实际provider failover回归先红2项，修后46项和完整门通过，准确HEAD发布/单实例后端激活待验；摘录不是完整upstream raw，不伪造rawhash。现有UI/WS保留，不回填旧归档或把行业补成原因；EM缺原因与67/70池差明确unknown。
