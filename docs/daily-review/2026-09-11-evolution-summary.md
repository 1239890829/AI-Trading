# 进化总结 · 2026-09-11（每日唯一飞书消息）

- 交易日确认：✅（watch-ledger `trade_date=2026-09-11`；交易日历 243 天，末日 2026-09-11，source=official）
- 发送：15:51:12 成功，identity=bot，`message_id=om_x100b650f61c0c0b8b49f836b47a2f88`，idempotency-key `evo-summary-20260911`，**当日仅此一条**。
- 议程：`id=4 / date=2026-09-11 / status=executed`，15:45:06 生成 → 15:46:00 完成（UTC 07:45），budget `llm 1/8`、`task 0/3`、`error=null`。

---

## 1. 发出的报告全文

```
【进化总结 · 2026-09-11】

▍What
1. 议程自动执行 1 项 B 类：把「题材梯队断层 × 高位股接续风险」写成带触发条件/适用边界的可证伪知识条目（⏳待验证），落 docs/evolution/2026-09-11.md；裁决不做自动化调参。
2. 数据健康 9 项哨兵全绿、n_issues=0：marketdb 由昨日停更 164h 恢复到 5 小时内更新，情绪指标库恢复当日更新。
3. 台账 99 条 / 68 条清算：胜率 63.0%（34 胜 20 负）、平均 -0.65%；昨日为 50.0% / -1.20%，胜率升且亏损收窄。

▍Why
1. 改进项 #123 指出 6 个题材梯队断层，但退潮相位样本仅 3 组 11 picks、超额转负（-1.73 / -7.58），不足以支撑调参，故降级为知识沉淀。
2. 昨日唯一数据健康问题已消失；此前「运行时文件陈旧比缺失更危险」的隐患解除。
3. 胜率 63% 却平均为负 = 赢小亏大：成功单均 +2.28%、失败单均 -5.36%（盈亏比 0.43），指向出场纪律而非选股命中率。

▍How
1. 议程 15:45 生成、15:46 完成（LLM 1/8、任务 0/3）；验证路径已定：10-20 个交易日内对比「断层题材高位股 vs 完整题材高位股」次日超额，差 >2pct 才升级 ✅，否则标 ❌ 留档防回退。
2. data_health 逐项核对：日历 243 天末日当日、快照 7 分钟前、题材成分 0 超时、情绪库滞后 1 日（阈值内）。
3. 信号健康按相位分组，9 组 44 picks 仍 insufficient，明确标注不作结论。

▍明日关注
1. 盈亏比 0.43 的修复：查止损/跟踪止盈是否让亏损单跑满（今日最差单 -14.7%）。
2. 1 条 pre_limit（920268 北交所 30% 涨停板）收盘价未回填未清算，确认是否北交所覆盖缺口（样本 1，待复核）。

不构成买卖建议。
```

22 行 / 858 字（≤25 行约束满足）。

---

## 2. 证据表（全部实测，只读）

### 2.1 议程 `/api/agent/agenda`
| 字段 | 值 |
|---|---|
| id / date / status | 4 / 2026-09-11 / **executed** |
| created_at → finished_at | 2026-09-11T07:45:06Z → 07:46:00Z（约 54 秒） |
| items | 1 条，class=B，status=executed |
| result | `docs/evolution/2026-09-11.md`（2 行，1804 B） |
| budget | llm_used 1 / 8；tasks_used 0 / 3；均未耗尽 |
| error | null |

item 关键证据：`current_phase=退潮`；近期退潮超额 09-10 `-1.728`(2 胜 3 负) / 09-11 `-7.58`(0 胜 1 负)；
早期退潮超额 08-31 `+7.293` / 09-01 `+4.698` / 09-02 `+6.56`——**同相位前后反转**，是本条目的核心可证伪点。
`tracking_pre_limit`：n=228 / judged=165 / win_rate 0.594 / avg_pnl_pct -0.8。

### 2.2 台账 `/api/picks/watch-ledger`（stats）
```
trade_date 2026-09-11  total 99  settled 68  tracking 31
success 34  fail 20  flat 14  win_rate 0.6296  avg_pnl_pct -0.65
```
逐日历史（同一端点 history）：
| 日期 | total | settled | 胜 | 负 | 平 | 胜率 | 平均盈亏 |
|---|---|---|---|---|---|---|---|
| 2026-09-11 | 99 | 68 | 34 | 20 | 14 | 63.0% | -0.65 |
| 2026-09-10 | 132 | 106 | 39 | 39 | 28 | 50.0% | -1.20 |
| 2026-09-09 | 125 | 54 | 25 | 8 | 21 | 75.8% | -0.19 |
| 2026-09-08 | 1 | 1 | 1 | 0 | 0 | 100% | +10.05 |

盈亏结构（今日 68 条已清算自算）：成功单 34 条均 **+2.28%**、失败单 20 条均 **-5.36%** → 盈亏比 **0.43:1**；
区间 [+10.47%, -14.7%]。**胜率 63% 而平均为负，是"赢小亏大"的量化证据**，指向出场纪律（止损/跟踪止盈），不是选股命中率。

层分布：pre_limit 69 / watch_no_entry 30；watch_no_entry 无 entry_price，未清算属设计（30 条）。
**唯一异常未清算**：`id=328 / 920268 百迈科 / pre_limit / entry 42.0 @10:03:16 / limit_pct 30.0 / close_price=null`——北交所标的，样本 1，待复核是否类别覆盖缺口。

### 2.3 信号健康 `/api/picks/signal-health`
- status **insufficient**（9 组 / 44 picks < 门槛）；win_rate 0.75、good 33 / bad 11、mean_excess +1.2499。
- 今日行：`2026-09-11 / 退潮 / n=1 / 0 胜 1 负 / mean_excess -7.58`。
- 与 `/api/picks/today` 一致：今日仅 1 只入选（603162 海通发展，score 54.4，phase=退潮，style=趋势防守，offsets tech+0.06 / echelon-0.05）。

### 2.4 实验 `/api/agent/experiments`
- `data=[]`、`shadow_queue=[]` → **0 条**，无裁决可报。

### 2.5 数据健康（agenda `inputs.data_health`）
- `n_issues = 0`，9 项 check 全 ok：
  - trade_calendar 243 天，末日 2026-09-11，official
  - snapshot_parquet 最新 073805.parquet，7 分钟前
  - **marketdb 369 MB，5 小时前更新**（昨日为停更 164h）→ 库内最新 09-10，滞后 1 日（阈值 3）
  - alert_pipeline 24h 479 条 / 审计 7 条
  - theme_members_fresh 超 24h 未同步 0/390
  - disk_free 61.4 GB；snapshot_dirs 14 个交易日目录
  - sentiment_metrics 窗口尾 2026-09-10，滞后 1 日，共 248 天
  - position_monitor_read 两路正常

### 2.6 其余 agenda inputs
- triage_stats：total 653（notify 26 / ignore 623 / escalate 4）
- plan_alignment：landed 8 项，**missing 仍为 `hmm_regime`**（连续第 2 日）
- knowledge_base：total 111（✅102 / ⏳3 / 🔶5 / ❌1），pending：KB-STOCK-19 / KB-STOCK-25 / KB-TRADE-13
- d1_validation（tracking）：n=45，仍强 39 / 持稳 1 / 走弱 5，strong_rate 0.867
- prediction `available=false`（target_date 20260831）、factor_ic `available=false`（月度复核未接入）→ 报告中未采用

---

## 3. 本次观察与待办结转

1. **盈亏比 0.43 是今天最有价值的数字**：连续两日（09-10 50%/-1.20、09-11 63%/-0.65）呈现"胜率不低、平均为负"。
   单看胜率会失真，必须并列看成功/失败单均值。→ 明日关注 #1。
2. **marketdb 从 164h 停更恢复**（09-10 记的隐患已解除）；本次无任何 data_health issue，为改造后首次全绿。
3. **920268 收盘价未回填**：北交所（920 开头 / 30% 涨停板）疑似不在收盘价回填覆盖内；今日仅 1 条样本，
   不足以定性为类别缺口（KB-DEC-019：单例只算 1 个证据），列为待复核。
4. signal_health 仍 insufficient（9 组 < 10 门槛），再约 1 个交易日出组；当前不可当结论。
5. 实验 0 条、影子队列 0；`plan_alignment.missing=hmm_regime` 连续 2 日未落地。
