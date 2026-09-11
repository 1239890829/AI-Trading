# 进化总结报告 · 2026-09-10（飞书唯一推送原文）

- 交易日锚定：watch-ledger `trade_date=2026-09-10` = 今日 ✅；日历 official 243 天，末日 2026-09-10。
- 议程：15:45:28 自生成 → 15:47:15 executed，1 条 B 类；`meta.scheduler_status.last_tick_age_sec=45`（调度存活）。
- 发送：见文末。

## 飞书原文

```
【进化总结 · 2026-09-10】
▍What 今日做了什么
1. 进化议程恢复自动执行：15:45 自生成、15:47 完成，产出 1 条 B 类项并落地 KB 草案（docs/evolution/2026-09-10.md）；调度心跳 45 秒，昨日「静默未生成」故障已消除。
2. 台账结算链路打通，首次拿到真实战绩：132 条中 106 条已结算，胜率 50.0%（39 胜 / 39 负 / 28 平），平均盈亏 -1.20%。
3. 数据健康检查出 1 项隐患：marketdb 已 164 小时未更新（同步任务停跑）。

▍Why 为什么
1. 昨日议程静默失败（_LAST_SHADOW_DATE 崩溃 + 日志被禁用）修复后首次验证；台账昨日 125 条全 tracking、close_price 全 null，胜率长期「数据缺失」，今日为首个可信样本。
2. 胜率 50% 却平均 -1.2%，赢面与盈亏不对称——赚得少亏得多，问题更可能在出场纪律而非选股命中。
3. marketdb 是日报与回测的数据底座，停更 164 小时（较昨日记录又 +43 小时）会持续放大口径失真。

▍How 怎么做
1. 议程 B 类项经 LLM 裁决：复盘提的「避开梯队断层题材高位股」缺乏断层/非断层分层数据，故未改参数、未加选股守卫，降级为可证伪 KB 假设（≥10 交易日、走弱率差 ≥15pct 方成立）。
2. 验证方式：KB 条目落库标 ⏳ 待验证，此后每日按断层维度分层记录，样本达标再升级 A/C 类；信号健康仍为 insufficient（8 组 < 10 门槛），未据其下结论。
3. 实验裁决 0 条、无自动回滚；告警判读 249 条（notify 14 / escalate 1）保持收敛。

▍明日关注
1. 排查胜率 50% 与 -1.2% 的背离：优先看止损/跟踪止盈是否让亏损单跑满，出场纪律参数待复核。
2. marketdb 同步停跑已 164 小时，需确认 sync_marketdb.py 任务存活，否则日报与回测口径持续失真。

不构成买卖建议。
```

## 证据表

| 来源 | 关键值 |
|---|---|
| `/api/agent/agenda` | date=2026-09-10，status=executed，items=1（class B），created 07:45:28Z / finished 07:47:15Z；budget llm 1/8 |
| agenda item | class=B，status=executed，result 写入 `docs/evolution/2026-09-10.md`；evidence.quantified_support=无（未按断层分层） |
| `/api/picks/watch-ledger` | total 132 / settled 106 / tracking 26；success 39 / fail 39 / flat 28；win_rate 0.5；avg_pnl_pct -1.2 |
| `/api/picks/signal-health` | status=insufficient，groups 8（<10），win_rate 0.7674，mean_excess 2.3536；今日 退潮 n=5，good 2 / bad 3，mean_excess -1.728 |
| `/api/agent/experiments` | data=[] ，shadow_queue=[]（0 条） |
| agenda inputs.data_health | n_issues=1：marketdb 367MB、164 小时前更新（同步停跑）；其余 6 项 ok |
| agenda inputs.triage_stats | total 249，notify 14 / ignore 234 / escalate 1 |
| agenda inputs.plan_alignment | landed 8 项，missing=「hmm_regime」 |

## 今日两项「昨日缺陷已修复」的取证

1. **议程不再静默跳过**：昨日 `agent_agenda` 无今日行；今日行存在且自动生成（未手动触发），status 走到 executed。
2. **台账结算打通**：昨日 125 条全 `tracking`、`close_price` 全 null → 今日 132 条中 106 条 `settled`，含 `close_price`/`pnl_pct`/`verdict`
   （样例：600792 云煤能源 entry 5.39 / close 5.41 / pnl +0.37% / verdict=success）。
