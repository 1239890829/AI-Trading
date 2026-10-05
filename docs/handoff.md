# 当前交接：紧凑控件与连续选择重设计

> 总方案v9.13；累计U01–U55；状态仅W07；Jev入口ai/jev-integration.md；UI证据product/ui-audit-20261003§32。

## 1. 现场、模式与范围

用户明确派工IMP-075；基线c8b51e8c2a40f8b0944bdb3bf613ee1c3526eba9（PR #229），分支codex/ui-compact-motion。

- **当前主门**：G2
- **本轮主任务**：IMP-075
- **主切片首选**：IMP-075

**U50 降级授权回执**：2026-09-24本机Codex DEGRADED_FULL_CONTROL未撤销。作者=发布操作者，不是独立Review；须准确HEAD DegradedRelease、完整门禁、required CI与release_check。

**当前门候选顺位**：仅交付IMP-075。

## 2. U49 主动审计回执 / 作者Preflight与反证

已同步master，干净树起步，IMP-074已交付。顺序为需求/数据边界→交互→来源比较→视觉→动效→状态/Canvas/响应式审查。Design & Taste为主，设计6/动效4/密度7；finesse产品域与现役治理合并，项目禁止应用私有目录优先。明确派工已授权实施，不重复等待设计确认。

反证：44px点击目标不等于44px厚重底色；不同用途不得共用大亮底；工具折叠不藏必要反馈；导航位置稳定；真实业务身份/后台权限和金融红绿保留；快速切换、resize、IME、焦点、减弱动态必须正确。TypeSafe不新增运行期消费者，仅Jev代码复评。

## 3. 本轮实现与实测

紧凑32px可见键面/手机44px触点、连续细线选择、工具横向索引与单面板下展、研究正文全宽、深石墨/冷银阅读面完成。补修维护手机自然滚动、仓库手机纵向阅读、各筛选残留亮白、知识KB数对比、观察器生命周期与通知键盘。

当前实测：后端4619 passed/83 skipped（151.51s），pyflakes；默认/UTC前端95文件各823 passed（58.05s/57.94s），tsc/eslint/正式构建通过。41入口三呈现共123图、五消费者五尺寸共25检查；真实分时10 Canvas等宽、工具关闭回焦、搜索自动聚焦、通知键盘回焦、证据日期/候选重排均留证。实色扫描修正KB数后重验；占位/隐藏分支/渐变和Canvas文字不作认证。收尾文档/账本测试189 passed；实色3613项检查、4950项排除，0不足。仓库/知识选择丢焦2项先红后绿，定向8 passed，最终实页保留原按钮焦点。Jev baseline复评测试5.6→7.0，作者自审不是独立Review；准确HEAD发布继续核PR回执、三CI、release_check及post-merge。预览仅GET合成夹具4204；不执行生产任务、模型、通知或交易操作。

## 4. 恢复

依docs/ai/continuous-evolution.md核外部来源、适用性与退出；只提出有用途的采用，不扩大权限。

当前版source.tar.gz校验与恢复说明保存于忽略artifacts/recovery/ui-current-20261005，远程标签ui-current-saved-20261005。旧活动恢复包可恢复迁出，两旧标签已删除，普通Git历史保留。
