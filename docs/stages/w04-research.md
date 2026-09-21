# W04 点时研究与策略验证

> 定位：最终融合方案的阶段任务文档；由 [总账 §6.0](../retro-and-gaps.md#60-阶段索引) 唯一索引。任务状态只在本页更新。
> 调度：本页 W 编号只表示领域归属；实际施工必须按 [总账 §5.9](../retro-and-gaps.md#59-阶段门优先级与跨阶段治理) 的“阶段门 → 门禁角色 → P0/P1/P2 → 门内序 → 硬依赖/效果前置”。页面上下顺序不是施工授权。

- **阶段目标**：标签、成本、股票池、消融和前向证据齐备后才讨论策略优势。
- **依赖边界**：W00；真实样本。不要求依赖阶段整体清零，按对应接口/证据切片判断。
- **排期**：本领域含 P0 证据真实性与 P1 研究验证；实际主切片顺序只按总账 §5.9 的阶段门治理计算，研究效果受效果前置约束。

## BUG-028

**潜伏扫描的未来数据污染与点时截断**

- **状态**：已完成
- **优先级**：P0
- **阶段门**：G0
- **门内序**：10
- **门禁角色**：阻断
- **依赖**：无
- **效果前置**：无
- **方案依据**：v9.3提前发现必须点时正确；hunting-decision-design §4/7/8。
- **范围**：backend/app/picks/lurk_pool.py 的_scan/scan_lurk_pool及backend/tests/test_lurk_pool.py与真实API消费者；不改策略阈值制造赢家。
- **验收**：同一历史前缀追加/修改未来bar不改变既有确认；短历史不用负索引；asof参与SQL上界与证券规则时点，不只是陈旧注记；空库、未成熟、NaN/重复日期、复权及较新确认语义有正反例。
- **证据**：PR #52 / 代码提交 `c0e4673` 把负索引、SQL 上界和 recent 语义同片修复；新增 `lurk-point-in-time-v2`、`probe_ms`、`requested_asof` 与交易日 lag。修前 `test_lurk_pool` 15 项中 9 项真实判红（短历史借未来、尾部反向改早期确认、asof 仍读未来、空表崩溃、首个而非最近确认、重复日期/NaN、自然日 recent、历史创业板规则）；修后 lurk 专项 17/17（含历史 asof 后未来行突变不影响结果），通过 price-limit/degradation/event-loop 联合回归共 85 passed / 2 skipped。真实 378MiB marketdb（截至 2026-09-18）同输入全量：旧版 142、新版 162，交集 127，其中 125 个 confirm_ms 完全一致；把新 `_scan` 放进旧“55 自然日”取数窗后 0 只具备完整 40 根量能前史，证明旧池整体不能再作严格前瞻证据。历史 `asof=2026-09-10` 实测旧版仍返回 `trade_date=2026-09-18`，新版返回 `2026-09-10`。本片未改 30% 振幅、1.2/1.3/0.7 量能或试盘涨幅等策略阈值。
- **下一步**：BUG-028、BUG-026 已闭环；BUG-020 代码缺口已修但生产盘中验收为 `待条件`，当前不属可行动 G0 阻断项，因此主门按 §5.9 计算到 G1；BUG-029 已闭环，下一阻断项为 IMP-006。潜伏形态是否有稳定收益仍须走 RSH-026 / IMP-020 的效果证据，不能把 point-in-time 正确性写成策略有效性。
- **恢复**：旧输出和旧统计仅作历史故障证据，明确标记“pre-v2 / 非严格 point-in-time”，不得恢复为研究基线；若 v2 回退，只能回退本片代码并保持旧结果失去前瞻资格的事实。
- **步骤**：已完成：①40 根量能前史硬门并禁止负索引；②`asof`/当前日进入 SQL 上界；③按 55 个真实交易日取前史、按交易日判 recent；④重复日期/NaN fail-closed；⑤最近确认取最新并记录试盘时点；⑥试盘日调用历史板块涨跌停规则；⑦真实库同输入重算并区分“正确性修复”与“策略阈值变化”。

## BUG-026

**机会评分卡的样本单位、时间窗和收益身份纠正**

- **状态**：已完成
- **优先级**：P0
- **阶段门**：G0
- **门内序**：20
- **门禁角色**：阻断
- **依赖**：无
- **效果前置**：无
- **方案依据**：主方案 §3.3、§9、§14；实施校准 v9.1 §4
- **范围**：backend/app/picks/opportunity_learning.py、backend/app/models/opportunity_learning.py、backend/tests/test_opportunity_learning.py 及实际评分卡/API消费者；复用归档表，不重建流水线。
- **验收**：一只股票多阶段/重复刷新不能冒充多只独立机会；Top-K 按声明的单轮/时点及唯一标的计算；horizon、样本单位、费用版本可见；D0 扣费代理不标作可实现净收益；NaN/缺数不算有效样本。
- **证据**：PR #53 / 代码提交 `ad71f4b` 闭环样本身份与指标身份。修前同一股票 9 次刷新 × 4 阶段的 36 条已标审计行被直接算成 `labeled=36 / fillable=36 / Top-K=5 / net_positive_observed`，足以错误越过 30 条门槛；修后同一输入保留 `audit_rows=36`，但明确拆为 `runs=9 / run×symbol opportunities=9 / symbols=1 / symbol×trade_date samples=1`，`repeated_labeled_rows=35`、`fillable=1`、`verdict=insufficient_sample`，Top-K 只取最新或显式指定的单一 rank run，并在截 K 前按 symbol 去重。scorecard 现显式过滤 horizon/strategy_version/feature_version，暴露 cost model 与 raw/run/opportunity/symbol/day 五层分母；NaN/Inf/非正收盘价不再落成有效标签；不能证明当前成本版本的旧行不进入 D0 成本代理。历史 `net_return_pct` 保留兼容列名，但 `d0_close` 明确为不可实现的成本调整代理，A 股 T+1 下不得叫可实现净收益。新增 7 类 BUG-026 回归，专项 21/21；相关 API/缓存/导入回归全绿；全后端 4061 tests collected，完整 pytest exit 0，最终独立重跑 `pyflakes app tests` 亦 exit 0。无数据库迁移、旧归档行不删除、不改策略阈值，前端无直接 scorecard 消费者。
- **下一步**：BUG-026 已闭环；后续 BUG-020 代码缺口已修但生产盘中验收为 `待条件`，当前不计可行动 G0 阻断项，因此主门为 G1；BUG-029 已闭环，下一阻断项为 IMP-006。D1/D3/D5、MFE/MAE、真实可交易退出与跨日效果仍归 RSH-026 / IMP-020，本片不据 D0 代理声称策略有效。
- **实施步骤**：已完成：①盘点实际消费者，仅后端 `/opportunity-scorecard` 路由直接消费；②公开 raw row、run、run×symbol opportunity、symbol、symbol×day 五层分母；③原始归档 append-only，统计层按 symbol×trade_date 去重；④Top-K 先固定单一 run/as-of、再按 symbol 去重、再截 K；⑤显式过滤 horizon/策略/特征版本，并以已持久化版本证据限制成本代理；⑥NaN/Inf/非正价格 fail-closed；⑦D0 毛变化与成本调整代理均明确非可实现收益，合法跨日标签继续由 RSH-026 版本化补齐。
- **效果说明**：分母正确后仍需独立交易日/事件簇、成本/可成交及 OOS；30 条不是证明优势的通用门槛。
- **恢复**：旧快照与审计行完整保留；指标口径版本化，旧 API 兼容时必须带限制，不靠恢复误导指标回滚。
- **分工**：ChatGPT 已完成取证、修复、全量回归与 API 兼容收口；后续独立审核只核证据和边界，不重复开发。本片不替代 RSH-026 的跨日合法交易标签建设。

## BUG-027

**研究准入拒绝缺失、非有限和未成熟证据**

- **状态**：已完成
- **优先级**：P0
- **阶段门**：G0
- **门内序**：80
- **门禁角色**：非阻断
- **依赖**：无
- **效果前置**：无
- **方案依据**：主方案 §9.2、§14；实施校准 v9.1 §4
- **范围**：backend/app/research/strategy_verify.py、verify_registry.py、services/decision_ledger.py、两个原核验脚本和原测试文件。仅修成熟度/数值与直接消费者身份，不改研究SQL、策略参数、费用数值或历史报告。
- **验收**：n5=0 保持零，不回退成总样本；NaN/Infinity、缺必需指标、未验条款不得取得完整准入；n+pending 与样本单位一致；现有有效指标正例仍可解释通过。
- **证据**：e6f2917 基点反例已证明旧缺陷；修复后零成熟、非有限/缺项/越界数值与配置阈值均失败关闭，登记册保留机器条款/待终审。集成 head 3e657c6 经独立审阅：本地后端全量 3926 passed / 80 skipped、pyflakes 0，前端 692/692（本地与 TZ=UTC）、tsc/eslint/build 全绿；PR #39 GitHub CI run #340 的 backend/frontend/docs 三项 required check 全绿。
- **下一步**：本缺陷本体闭环，不重复实现；未运行真实数据研究或改写存量报告。年度 raw 收益代理、股本回推历史及未 purge 切分继续归 IMP-020，不借本项完成声称研究有效性已证明。
- **实施步骤**：①字段存在性与零值分开，旧总数保留legacy_total身份；②计数/比例/费用有限性与范围校验；③缺中性指标、年度或涨停代理归unchecked，完整有效正例才机器pass；④机器pass仍review_required，两个脚本落完整gate，登记册和决策台账透传限制；⑤真实DuckDB及隔离存储/消费回归，原语义错误测试改正而非删除；⑥年度raw收益代理、股本回推历史及未purge切分仍归IMP-020，不借本修复声称有效OOS。
- **恢复**：保持旧报告可读并标版本/历史身份；限制不可信结论继续参与准入，不删除否证结果。
- **分工**：本轮ChatGPT按用户授权临时代执行；作者验证不等于独立审核，Codex恢复后核本片真实差异和证据，不重复开发或批量销账。

## IMP-049

**多情境候选、进入条件与知识证据契约**

- **状态**：待条件
- **优先级**：P1
- **阶段门**：G2
- **门内序**：10
- **门禁角色**：阻断
- **依赖**：BUG-028, BUG-029, BUG-026, IMP-006
- **效果前置**：无
- **方案依据**：用户本轮选股核心要求；v9.3 基线 + v9.8/U47 增量；hunting-decision-design全文，产品闭环§3–6。
- **范围**：复用picks的盘前、lurk/rps、intraday/relay/radar、gate/执行与events/KB接口；先明确统一读模型，再实现驱动/结构/角色/环境/时点/执行域的开放情境，不另建并行评分系统。
- **验收**：观察、触发、可执行与实际 shadow fill 分离；本片各情境有成立/反例/缺失，多假设冲突及未知情境保留；旧盘前/盘中口径保留；无有效进入区间不伪造；同一机会和 decision version 可流至 UI/通知/IMP-053 shadow/复盘；盘中可随新事实生成后续版本，早盘未通过不等于全天永久拒绝；相同前缀不变，独立证据不重复加分。
- **证据**：本轮已读候选与卡片接口：盘中以题材集中、临板与涨幅排序为主，潜伏及持仓波浪未形成统一候选解释。新机制为设计，未实施也未证明策略增益。
- **下一步**：满足以下条件再实施：硬依赖已完成、v9.4 产品/猎场设计继续作为 U47 的现行规划方向、总账阶段门计算轮到 G2，并由既有“一次继续只领一个切片”流程授权本任务的具体纵切。条件满足后先复用已经可用的实现，按全部KB/因子/策略映射核缺口；依据价值和数据选纵切做无AI对照，不以四类或固定顺序遗漏其他方法。UI夹具可提前，但不能宣称真实数据已通。
- **实施步骤**：①登记指标/字段/时间/来源及候选分母；②抽共用数据快照和读模型，GET不执行研究/落业务记录；③复用召回并增加独立场景路径；④统一hard gate、有效窗口与反证；⑤真实消费者和失败/恢复对照；⑥仅研究验证通过的增量获准切换，模型不自动晋级。
- **恢复**：先影子双读，同源逐条对账；可以回退到上个可靠读模型，但历史事实和拒绝/失效记录不得丢失；无关权限及参数不变。

## RSH-026

**点时样本、结果标签与全市场分母**

- **状态**：部分完成
- **优先级**：P1
- **阶段门**：G3
- **门内序**：10
- **门禁角色**：阻断
- **依赖**：BUG-026
- **效果前置**：BUG-020, IMP-006
- **方案依据**：主方案 §9、W04
- **范围**：延续候选/硬门/精排/通知快照；补 D0/D1/D3/D5、MFE/MAE、漏选/负例/弃权/未成交与可见时间；U47 将首见参考轨、可执行触发轨与实际 shadow fill 轨分开，并增加后续涨停/首次封板的结果标签及提前量，但结果标签不得反向参与当时特征。
- **验收**：股票池、公司行动与版本点时正确；标签独立，分母包含未入选与失败，复盘能回指决定。
- **证据**：既有 append-only 快照、离线重放与 D0 标签沿用；PR #69 已闭环新写入全漏斗 outcome identity，PR #70 已补 D1/D3/D5 交易日 horizon，PR #71（merge `7df3f286`，post-merge CI #507 全绿）已补 D0 决策后 Tencent 1m MFE/MAE、涨停∪炸板 ever-hit 与连续交易分钟 time-to-limit。PR #71 后定位到旧部署长期库：173MB SQLite / revision `d2e4a6b8c0f1` / 156,108 snapshot / 63 D0 outcome；63 条恰好覆盖全部 selected/actionable，历史缺的是 156,045 条 rejected/unknown denominator identity。当前子片新增显式离线 legacy recovery：默认 dry-run、写入前 schema gate、仅本地 exact-date marketdb、不外呼、apply 前一致性备份；identity 与 D0 label 均支持有界 batch。真实库只读 dry-run 已复现 156,045 missing / selected missing=0；一致性副本迁移到 head 后恢复为 156,108/156,108 D0 identity，最终 `150,903 labeled / 5,192 unknown / 12 deferred / 1 pending`；unknown 主要因 reference 缺失，未标项只因本地 close 缺口保留。二次 apply `inserted=0/repaired=0` 且 `noop=true/backup=null`。真实副本 D0 补齐使 DB 由 173MB 增至 267MB，进一步暴露 future-horizon 放大风险：生产 EOD 已改为只为 selected/actionable 建 D1/D3/D5 identity，full-funnel future 结果留显式离线研究；真实副本 9/16–9/18 的 154,106 source snapshot 仅产生 61×3=183 条 selected future identity。existing 查询按 trade_date join，避免 8 万级 snapshot ID 展开成巨型 SQLite `IN`；selected-only snapshot 本身也改为 SQL 侧过滤，真实 81,672 行日的 no-op 准备从约 1.82s 降至 0.57s。该结论是当时副本验证阶段的边界；PR #76 发布后真实运行库已按独立备份→schema 迁移→dry-run→apply→幂等复核的受控流程恢复，当前 revision=`d9e4c2b7a1f6`。公司行动子片已由 PR #74 闭环 `qfq-ref-v1.raw-anchor`：同源 raw/qfq 决策日因子、精确目标日 qfq close、旧 basis 版本隔离与缺 bar fail-closed。append-only revision 子片已由 PR #75 闭环：最终真实副本新增 48 条 current revision；从原始长期库读取 48 条 terminal labeled 的全部原始 base 列，再按相同 id 与恢复后副本逐列比较得到 `matched=48 / identical=True`，证明 revision 未覆写旧证据；9/16 v1/v1 current sample 从 0 恢复到 28，但 denominator 仍 incomplete；二次 apply `noop=true/backup=null`。
- **下一步**：PR #69/#70/#71/#72/#74/#75/#76/#78 与账本 PR #73 已闭环；#78 merge=`2576b9cd`、PR CI #521 与 post-merge CI #522 全绿。真实长期工作区已同步到 #78：部署前今日仅有 195 条 `notification/rejected`，candidate/hard_gate/rank 仍为 0；新 backend 启动为 30/30 scheduler，首个 durable snapshot 后 `opportunity-evidence` 连续两拍真实失败，错误为共享 builder 局部 `snapshot_as_of` 字符串遮蔽同名函数，且 THS 已出现 429。cursor 未推进，证明失败可重试/可见机制生效；为避免每 30 秒重跑重型 builder 放大请求，生产立即以 `ASHARE_PICKS_OPPORTUNITY_EVIDENCE_ENABLED=0` 临时降级重启，现为 29 running / 1 disabled，其余链正常。当前 hotfix 将局部值改为 `snapshot_as_of_text`，并新增直接执行真实 `_build_opportunities_uncached` 的非空 theme/participant 回归：linkage 继续收到源 UTC snapshot ISO，archive 收到北京 naive fact-time，防止同名遮蔽与函数对象误传。hotfix 发布、真实无-GET 自动归档验收通过前，RSH-026 仍不得关闭；actual shadow fill/entry-capture 继续由 IMP-053 拥有，且不进入 IMP-020 效果结论或自动回写生产权重。
- **恢复**：保留原始样本和旧标签版本；错误派生标签另版重算。
- **开工前置**：原始样本采集/标签协议可立即设计；不因整套执行快照尚未统一而停止积累。先标明现有数据的来源和不合格范围。
- **效果说明**：相关 BUG-020 与 IMP-006 的输入/快照契约可信；标签成熟且有独立样本，才能讨论效果。
- **实施步骤**：①盘已有 snapshot/run/stage/symbol 唯一键；②保存当时股票池及漏选/拒绝/弃权/未成交分母；③D0方向与可合法退出的 D1/D3/D5 标签分轨，加入标签/成本/成交/交易规则版本；④公司行动、暂停交易和晚到数据不以最新值倒填；⑤同一对象修订另版保留，不覆写历史；⑥测试单次重放及重复运行不增统计信心；⑦量数据增长后才做不丢证据的压缩。
- **采样偏差**：区别候选、合格、静默、曝光、点击及模拟动作；未提醒/未点击不标负收益。保留不扩外推面的影子对照和被拒绝原因，当前名单不可反向决定历史股票池；用户反馈不充当独立市场标签。
- **早发现指标**：首次观察/触发/可模拟执行/实际 fill 分别计时；保留误报漏报、未成熟、拒绝和未成交分母；若后续出现首次涨停，报告 `time_to_limit`、触发前后 MFE/MAE、可成交率、从 reference 到 fill 的价格漂移及与简单基线的 entry-capture 差异。未来最低价、后续最高价/涨停只可作结果标签，禁止倒推“当时本可买在最低点”。金健案例未给区间不造故事，示例不入盲测。历史成分/规则/公告和知识版本按当时可见，不用今天补齐数据倒推过去。


## IMP-020

**成本后验证、消融与晋级纪律**

- **状态**：待执行
- **优先级**：P1
- **阶段门**：G3
- **门内序**：20
- **门禁角色**：阻断
- **依赖**：BUG-027, RSH-026
- **效果前置**：无
- **方案依据**：主方案 §9、W04、§14
- **范围**：统一成本/可成交、purged walk-forward、OOS/前向、试验全集、窗口稳健性、校准与 Champion/Challenger。
- **验收**：无泄露与幸存偏差；保留负结果与多次尝试分母；规则分不冒充概率，样本不足保留研究态；reference-price 反事实、可执行触发与实际 shadow fill 分开比较，只有 fill 后按同版成本/退出得到的结果可称模拟交易净收益。
- **证据**：旧 ntile 修复和多窗口扫描已完成；IMP-026 的旧版本结论需复核，不能盲目沿用旧 40 因子数字。
- **下一步**：先冻结现版样本/口径与预算，再选一项有决策用途的对照；旧 BUG-011 的确定性候选按现 SQL 核验。
- **恢复**：不自动上线参数；版本化归档与 review_required 保留，研究错误不污染生产。
- **实施步骤**：①按真实可用数据冻结基线、标签、成本、K、时间分组与停止规则；②核 strategy_verify 的当前股本回推历史和 split_sample，近似只用于探索；③purge/embargo 与预处理只用训练信息；④单规则族消融并登记所有尝试/否证；⑤同版本候选影子比较，禁止多参数变更污染；⑥晋级审批与安全回退分开，复用 experiments/agent_params 的可追溯记录。
- **开工前置**：实验协议/离线夹具不等待样本齐备；数值结论须通过 RSH-026 的数据与成熟度条件。
- **场景与小功能**：开放情境与各候选阈值预注册，不以示例类数封版、标签重叠隔离；净增益、注意力成本和尾险共同评估。回放播放/暂停/速度/滑条及日期选择不能看未来；统计时间窗/样本数/收益身份必须跟当前选择一致，工程与效果分开。


## RSH-030

**Jev 语义特征、人工金标准与额度节省实证**

- **状态**：部分完成
- **优先级**：P1
- **阶段门**：G3
- **门内序**：30
- **门禁角色**：非阻断
- **依赖**：无
- **效果前置**：无
- **方案依据**：docs/ai/jev-integration.md §29。
- **范围**：固定事件队列、独立人工标签、规则/Jev/现有 LLM/fallback 对照，以及 TypeSafe 与更贵模型 token/调用量、延迟和质量的同条件度量；不把 agreement 写成 accuracy。
- **验收**：v1 240 条 human.category/certainty/actionable 独立完成且通过 strict human validation；verifier 另有独立 claim/evidence 人工集；生产阈值、额度节省或选股增益只允许由可复算 A/B / walk-forward 得出。
- **证据**：PR #33 建立 verifier/gold-set 工具，PR #34 完成固定队列 Jev 预标注与审核优先级并均已合入 `master`。240 条固定队列当前 human 仍 0/240；规则/Jev agreement 为 category 55.42%、certainty 77.92%、actionable 57.50%，仅用于安排人工审核。当前极保守 actionable cascade 在该平衡队列上无节省证据。
- **下一步**：独立完成 240 条人工标注和 verifier 人工样本，再比较规则、Jev、DeepSeek 与 fallback；同时为 RSH-031 按市场状态/事件类型/板高分层设计历史涨停域人工 anchor，Jev 只写独立 prediction。长期 token A/B、选股/做T及涨停语义特征 walk-forward/消融保持开放。
- **恢复**：预标注永不回写 human；任何实验失败只停用对应 Jev 增量，不覆盖原始队列、规则结果或否证证据。

## RSH-031

**历史涨停、强势连板、龙头形成机制与起爆前识别研究**

- **状态**：待执行
- **优先级**：P1
- **阶段门**：G1
- **门内序**：70
- **门禁角色**：非阻断
- **依赖**：无
- **效果前置**：RSH-026, IMP-020, RSH-030
- **方案依据**：[历史涨停/龙头研究蓝图](../research/limit-up-dragon-research.md)；hunting-decision-design §2/4/7/8；Jev 蓝图 §21/26/30；用户 U43。
- **范围**：按历史交易制度建立全量涨停/炸板/临板与匹配对照；以市场→板块/概念→题材/分支→催化→个股→角色/演变的层级对象分析首板、强连板、空间板、弱市穿越、断板反包、趋势化、二/三浪与再启动。研究消息/公告、技术、相对强弱、资金代理、情绪、指数/双创、题材梯队及异常板块拉升的真假持续性；不只收集赢家。
- **验收**：样本及题材成员 point-in-time，正负分母完整；探索段与最近 untouched holdout 分离，污染案例不计盲测胜利；候选机制预注册并做 matched baseline、purged walk-forward/embargo、ablation 与 multiple-testing 纪律；报告首板前/封板前/二板前 Recall、Precision@K/PR-AUC、first_seen/first_actionable 提前量、`time_to_limit`、龙头/最大板高排序、题材持续性、假启动、不可成交、fill rate、成本/MFE/MAE、假阳性与漏选，并比较早期可执行入场与简单相对强弱/突破基线。后续最低价/涨停价只作 outcome，不允许作为过去买点特征或把“理论最低可买”当可实现收益；按市场状态/板块制度/市值/事件类分层，负结果和窄域结论保留。
- **Jev 分工**：历史新闻/公告/涨停原因做离线 bounded MapReduce，抽取事件类型、新颖度、确定性、公司/题材直接性、产业链分支、来源/冲突、叙事拥挤和相位一致性；确定性召回后可做 second-stage rerank；自动归因用 Universal Verification 核 claim↔evidence。Jev 不直接预测涨停、不使用未来收益造特征、不改交易硬门/策略权重、不以 confidence 冒充胜率。
- **证据**：现有 theme-sentiment、theme-prediction、hunting-decision、KB37项及 strategy-registry 已含梯队、角色、消息、弱市穿越、结构转换和失败样本的碎片知识；本轮审计确认缺少统一的历史全样本研究宇宙、失败对照、从旧到新的盲测链及向猎场准入的单一研究协议。本文/蓝图只是设计，不是效果证据。
- **下一步**：Phase 0 的数据盘点、事件宇宙与描述性 atlas 可在效果前置未完成时先行：先冻结历史覆盖、板块制度、涨停/炸板定义、历史题材成员、新闻/公告/涨停原因可见时点、竞价/人气/资金/分钟覆盖、污染案例和最近保留段；随后从较早一批历史事件生成 event universe + matched controls + evidence bundle。正式特征/战法增益必须等待 RSH-026/IMP-020；Jev 小样本遵守 RSH-030 的人工 anchor/独立 prediction，不批量烧调用。
- **回流边界**：通过 RSH-026/IMP-020 的增量只进入既有因子/策略/情境登记，并由 IMP-049 作为 Opportunity/scenario evidence 的 challenger/shadow 消费；达到可执行条件后的交易真实性由 IMP-053 独立 hunting-shadow 验证。不建第二涨停选股器，不绕过统一机会身份、可成交/风控硬门和发布准入。
- **停止/重开**：历史文本无可靠可见时间、成员只能用当前名单回填、最近 holdout 失效、优势只来自幸运小桶、扣成本/可成交后消失或不优于简单相对强弱/题材广度基线时停止扩展；保留否证与重开条件，未来新数据/市场状态/方法提供明确反证时再开。
- **恢复**：Phase 0 只新增版本化研究资产与元数据，不覆盖旧样本/标签；Jev prediction 与 human 分离。任一实验失败只停用对应候选或 shadow 增量，保留原 event universe、对照、否证和旧猎场基线，不以恢复旧错误口径作为回滚。

## RSH-003

**候选方法与因子的增量实验**

- **状态**：待条件
- **优先级**：P2
- **阶段门**：G3
- **门内序**：50
- **门禁角色**：非阻断
- **依赖**：RSH-026, IMP-020
- **效果前置**：无
- **方案依据**：主方案 §7.1、§9.4、§15
- **范围**：只研究有具体假设的召回/特征/排序/风控/退出增量；余 TA-Lib、HMM 等不按目录补齐。
- **验收**：预注册插入点、基线、成本和退出条件；结构新颖不等于有效，OOS/前向增益不足则退出。
- **证据**：已完成 novelty-first、EMA/Wilder 原语和部分候选实测；冗余结果保留，不能当准入证据。
- **下一步**：满足以下条件再实施：先修影响实验可信度的窗口/NULL 样本问题；只有真实样本和可证伪假设齐备才跑候选。
- **恢复**：保留基线权重和否证记录；实验不自动影响推荐或模拟资金。
- **原定义覆盖**：高紧旗形、涨停洗盘、RPS/海龟/均线突破、停机坪/MA250等逐个核原定义和消费者；原登记“等8策略”未展开者保留未读，不编造、不算全部覆盖。61种形态是候选库范围，不是61项上线承诺；每项含适用域、反例、数据与净收益才开实验。


## 已交付基线

- BUG-002 的 ntile 确定化、RSH-001/002 因子与多窗口、GOV-001 版本复核机制已交付；不等于策略有净优势。

旧编号、退出理由和原文恢复入口见 [历史处置表](../archive/ledger-transition-20260917.md)。本节只留仍支撑本阶段的成果，不保存逐轮长日志。
