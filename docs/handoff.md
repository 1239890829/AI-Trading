# 当前交接：IMP-019 指数源龄与休市状态纠偏

> 总方案v9.13；累计U01–U55。阅读与WS整改[W07/IMP-093](stages/w07-simplification.md#imp-093)已由PR #270发布并运行；直接上游单位反例按[W00/BUG-020](stages/w00-foundation.md#bug-020)既有同根回归条款止险，不启动第二个产品优化任务。

现役语义判断依[semantic-decisions](ai/semantic-decisions.md)；外部能力依[开放世界持续演进](ai/continuous-evolution.md)筛选。

## 1. 现场、授权与最新验收

- **当前主门**：G4
- **主切片首选**：IMP-019
- **候选顺位与执行边界**：IMP-019；扩大清理及稳定性读链已由PR #272发布并加载。本轮接续用户报障的指数“报价超时”，不重做已交付切片。
- **基点与分支**：origin/master `a52418f1a15356364f76bf80e2a92ed058e61b2c`，`codex/quote-pause-state`；主树两份未跟踪evolution保留。
- **获准范围**：源龄标签、当前日历休市解释、接纳至共享发布的状态空窗，以及日历空/异常时旧closed阻断发布的直接失败路径。保持原模型、源序、预算、时效、完整性、选股公式及通知资格；不恢复Jev/Decisions/KEV/REA。
- **实际运行基线**：2026-10-13 19:23已激活PR #272/master a52418f1：8000单实例PID72810、3000生产构建E5xRZIqP3vcd_DjEyx7eU/PID72886。PR及post-master三job均成功；本轮纠偏尚未加载，准确HEAD发布和运行切换另录。
- **证据状态**：19:33–19:37指数域完整覆盖、当时诊断累计腾讯方法120进入/120返回，错误和预算耗尽均0；六指数源时点16:14–16:15，display_session为closed但部分返回只有quote_age_exceeded。方法计数不是HTTP次数。接纳至5秒共享发布间状态空窗已隔离复现；空/异常日历旧closed丢帧仅隔离实证，不宣称实盘发生。详见[报告§13](system/runtime-performance.md#13-2026-10-13-指数源龄与休市状态纠偏)。前批清理和工程证据仍归§12。
- **U50 降级授权回执**：2026-09-24 DEGRADED_FULL_CONTROL未撤销；作者自审和协作反证不冒充独立网页Review。每个PR准确HEAD另有DegradedRelease。

## 2. U49 主动审计回执 / Preflight

本轮只修真实源龄提示与阶段状态：先读取现役Provider方法诊断、源钟、display_session、覆盖/失败，再隔离复现接纳到共享发布的闭市空窗。原版Impeccable沿clarify/harden核异常优先及简明提示；不用营销页规则或替换当前视觉。

作者与协作者交叉反证补齐三处直接边界：闭市不能覆盖batch_missing/index_batch_missing；日历等待跨宽交易时段按完成时刻裁决；首次缺源钟不能显示正常。真实源拒绝/刷新失败/low/invalid优先保留，known_trade_days零新增外呼。旧盘中fixture显式固定连续交易阶段，空/异常/跨日unknown另由真实helper用例验证；纠正两条固化“休市盖掉缺项”的坏断言，不减断言或放宽阈值。

验收区分离线行为、实际组件、完整门、CI和运行生效。unknown不推断开市，休市不晋级实时；无新增源/模型请求、WS订阅、消息写操作或调度。反证和作者自审不是独立网页Review；当前PR另走准确HEAD DegradedRelease。

本批源龄纠偏完整门：后端5281 passed/81 skipped/119.87s、pyflakes通过；前端默认/UTC各1493通过/133文件、tsc、ESLint（0error/2个既有warning）、Next16.3.3生产构建通过。目标先红后绿及实际QualityBadge桌面/390/320另有证据，未冒充盘中SLO；准确PR/HEAD发布、post-master与运行核验另录。

## 3. 验收与恢复

代码提交f73a9651：完整后端5227通过/81跳过/478.61s，pyflakes通过；前端默认与UTC各1428通过/132文件（112.40/171.55s），tsc、ESLint（既有1 warning/0 error）、Next16.3.3生产构建通过。文档守卫180项、卫生、公开扫描已通过；最终回填另复验文档门。原版设计反证的跨行连线、嵌套原因及长回复菜单问题均关闭；不是独立网页Review。PR #270准确HEAD发布及正式运行取证已完成，见§5。构建pjXxp4V2Z0WlEgJ4WGHPj的隔离3105接现役8000：30秒握手/子协议/snapshot/pong正常、6个stale帧、18标的、无中途断线，无凭据403。收盘数据仍stale，不掩盖源质量，也不证明未来交易窗无卡顿。正式应用PC/390原因弹层核完整原句、来源、交易日与374px容器；隔离真实公开新闻PC/320/390与助手展示夹具分开标注。3000已完成PR #270构建切换；该PR后端无代码改动，单位纠偏另按§5有条件激活。保留旧构建与PR恢复点，按本次Git差异回退阅读表现；源数据、已有归档、选股与消息硬门保留。不恢复REA/Jev/Decisions/KEV。既有INDEX和产品闭环§45沿本任务更新，无新业务页面/模型/数据源/权限，不产生新的重大准入决定。

## 4. 交易时段跟进边界

本机官方交易日历覆盖2026-10-13，盘前不能当连续交易证据。用户授权下建立仅当天09:35/13:35两次的线程跟进，每窗先冻结实际运行版本/负载，再只读观察5分钟；本机应用与前后端需在线。参数/脚本哈希与现场证据在忽略的`artifacts/runs/live-performance-20261013`，状态仍归W06；第二次后停用，不产生永久ping。

现有health/metrics/caches/providers/schedulers读面已盘前实发5次200，没有触发采集/模型；交易窗collector盘前拒绝且0请求。使用Hub批报价不主动回源/新增订阅；同股分钟/日K有界读取。保留额外观测请求、失败和unknown。滑动1h分位/count不能直接差分成窗口分母；缓存factory不等于供应商HTTP；图表meta属Hub，日K日期不是秒级源钟。StockDetail冷读可能自动调用摘要模型，不为测试额外激活。没有浏览器性能trace就不宣称刷新无卡顿。确认问题后再按完整工程/发布门最小修复，不改TTL/轮频/freshness、选股或通知规则。

## 5. 本轮运行反例的事实止险

PR #270已精确HEAD发布、post-master三job成功；3000已加载pjXxp4V2Z0WlEgJ4WGHPj，后端94616未重启。正式3000/本机LAN HTTP200，WS45秒三次pong/快照及源状态帧，无中途断线，无凭据403；收盘stale保留，不证明盘中SLO。

15:49:56 THS429→EM揭露旧ZT价格/100错：官方15:53:16 qdate20261013的002343 p7360应7.36，原输出73.60；THS/腾讯缓存一致7.36。代码与错误说明/测试同步修正/1000；来源工具输出摘录及实际provider failover回归先红2项，修后46项和完整门通过，准确HEAD发布/单实例后端激活待验；摘录不是完整upstream raw，不伪造rawhash。现有UI/WS保留，不回填旧归档或把行业补成原因；EM缺原因与67/70池差明确unknown。

PR #271/075f49d0首次CI单项助手sources为空，其他5228 passed/81 skipped；本地同HEAD助手4worker56项通过。只读反例确证base id(stocks)缓存不保留名单引用，空名单地址被复用可漏识别新名单；CI未记录id，无法锁定唯一因果，不能冒称纯环境波动。改真实缓存身份并保留原断言，新HEAD另跑完整门与CI；旧075f49d0发布回执不得用于新版本。

缓存身份新增3项先红；修后连原助手59项通过，最终完整5232 passed/81 skipped/187.63s，前端默认/UTC各1428（171.13/153.30s）及构建通过。单份真实名单缓存用is核验，新名单退休旧名单；无O(n)内容副本。只读团队反证无新增阻塞，新HEAD另走准确CI/DegradedRelease/post-master与后端激活，不将旧回执转借。


## 6. 当前实施的反证与传播

根因来自本轮冻结的真实日志与隔离反例：整轮gather丢正常域、启动重复、旧图无错误出口、当日题材参照漏刷新、历史数据过度轮询和消息条件失真。修补按原owner完成，不引入独立事实库/架构平台。完整后端首轮2失败已保留：真实字段fixture与长观察TTL登记缺失；修复守卫而非豁免，完整复验另录。正常域合批抖动与新鲜low质量健康漏报也经反证整改。作者/协作者检查不是独立网页Review，当前PR须新准确HEAD DegradedRelease。

重大决策传播：system/runtime-performance、product/limit-up-attribution、ai/semantic-decisions、data/data-sources、W06与handoff同步既有方案；INDEX已有条目可达，无新增文档/业务页面。总方案v9.13/AGENTS/现役Skills无需改准入规则，理由是既有任务内工程纠错，未引入新模型/服务/评分/权限或重研究隔离。独立语义效果、人工作真值与真实交易性能仍不由工程测试替代。

当天上午实际5分钟证据保留，下午错过；自动跟进已PAUSED，盘后collect拒绝且0请求。没有新永久ping或历史回补。下一实盘验收须新运行版本和真实交易状态；不为测量主动生成选股、发/已读通知、调用冷摘要模型或模拟交易。


## 7. 本批验收包

后端首次完整2失败的原日志保留；补真实schema fixture与长观察TTL登记后完整5263 passed/81 skipped/109.80s，4个既有warnings。前端最后默认/UTC各133文件1465项通过，tsc、ESLint（0error/2个既有warning）、生产构建通过；文档守卫、卫生和公开扫描全部通过。实际MinuteChart绘制十个Canvas，指数叠加可见；读取失败后保留画面、重读清告警；默认完整原文/限制与链接可读。桌面1280、390、320无横向页面溢出，320短节点标题经容器查询展开图标/标题布局后保持可读。数据是隔离合成夹具、connect-src none，非实盘、真机FPS或原文事实验证。Impeccable context→optimize/harden→mechanical detect→实际状态/窄屏验收沿原技能职责，不强套营销样式。

恢复仅回退此PR源码及运行构建；进程内原因观察非持久历史，重启后缺失仍unknown。长期语义效果、源切换覆盖和开盘/断源浏览器SLO需同版本真实观察，不用有限行为测试造可用率或提速倍率。已有Dependencies PR仍独立维护，不混入本批源稳定性修补。
