# 当前交接：真实消费者动效库与提示组件准入

> 总方案v9.13；累计U01–U55；设计证据product/ui-audit-20261003§27；状态只在W07；Jev入口ai/jev-integration.md。

## 1. 现场、模式与范围

用户要求具体比较自写与现成组件，并授权实施；保留现版结构与配色，采用确有消费者的GSAP/Radix。分支codex/ui-library-motion，base 0ceaca675fc1d435eff5ca56fe5fe49a204d6a84（PR #222）。原恢复标签ui-current-saved-20261004保留，恢复时须修正玻璃过透。

- **当前主门**：G2
- **本轮主任务**：IMP-070
- **主切片首选**：IMP-070

**U50 降级授权回执**：2026-09-24本机Codex DEGRADED_FULL_CONTROL未撤销。作者=发布操作者，不是独立Review；每次发布仍须exact-HEAD DegradedRelease、required CI与release_check。

**当前门候选顺位**：本任务已完成实施验收，发布收尾后按最新master selector重算，不自动领取第二任务。

## 2. 实施与验证

**U49 主动审计回执 / 作者Preflight与反证**：核最新master、干净工作区、G2依赖与固定库许可；比对真实消费者、SSR/React生命周期、快速反向、resize、键盘立即可见、减少动态、卸载、Tooltip与inert/焦点/Portal层级及原Canvas/手机滚动。零面积候选不参与Flip；外层布局与内层3D分别管理；没有宽化守卫或改变业务/API/后台owner。

代码提交7b92d6e191e2bb929032c7aac316f95f319f07eb；协调文档为后续独立提交，最终发布回执/CI绑定候选HEAD。

- GSAP 3.15.0/Flip与@gsap/react 2.1.2接入候选外层重排及工具时间线；Radix Tooltip 1.2.16接入命令/浏览/主题三个真实入口。原3D内层、图表、业务/权限/数值更新保留。
- 后端最终4619 passed/83 skipped，pyflakes；前端默认与UTC各93文件814 passed，tsc/eslint/Next正式构建通过；npm ci、公共扫描、doc-health、hygiene通过。初次后端缺continuous-evolution指针、前端多余外部运行时CSS变量引用已修，未改守卫。
- 41入口×手机390/电脑1280共82新图，页级横向溢出0，另9张前后/专项图；320px提示碰撞与两模式、真实分时Canvas、快速反向、命令抽屉焦点及手机工具ESC另实测。实体手机GPU/60FPS/网络性能未验，不冒充收益。
- 全构建JS gzip合计增加56793字节（约55.5KiB），不是单页首屏传输。npm audit前后同为10项既有告警，新增依赖无新增项；现有jsdom engine warning记录不隐瞒。
- Jev携previousEvaluation复评，正确性6.9→8.2/可靠性7.1→8.2，作者自审不称独立Review。证据在忽略批次ui-library-motion-20261005，最终发布仍绑定准确HEAD。
- 4202正式前端接4204只读合成夹具，当前图册4206；旧4203/4205保留。没有启动生产后端、模型、调度、通知或交易。

## 3. 取舍、传播与恢复

保留头部导航、石墨冰蓝和现有业务。隔离只读合成夹具用于画面验收；不启动生产后端、调度、模型、通知或交易。恢复基线PR #222，库准入与取舍回填产品/UI审查/INDEX/W07/总方案与决策传播。

外部持续演进入口docs/ai/continuous-evolution.md保持；外部库许可/版本/消费者与退出按原准入规则核查。

总方案/产品§19/细功能§34/UI审查§27/INDEX/W07/plan-registry已同步；AGENTS/Skills/协作/架构/AI蓝图无新规则，不适用。协调文档回归88 passed。
