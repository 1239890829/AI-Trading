# 进化总结 · 2026-09-14（周一）

> 自动化任务 `49881f07` 产出 · 每日唯一一条飞书消息（15:56:52 已发送，message_id `om_x100b654ec28f28b8b4a433057c584fc`）

## 1. 飞书推送全文

```
【进化总结 · 2026-09-14】
▍What 今日做了什么
1. 进化议程 15:45 自动生成（92 秒完成），产出 2 项：C 类「信号健康 drift 告警疑似检测器失真」、B 类「数据健康 theme_members_fresh 未过」；两项均 pending，今日无代码变更落地。
2. 台账 181 条 / 已清算 154：胜率 66.1%、平均盈亏 -0.39%（成功单 +1.87%、失败单 -4.30%，盈亏比 0.43）。
3. 信号健康首次出组：10 组 / 47 样本，窗口胜率 76.6%、平均超额 +1.74%，但 CUSUM 仍判 drift。
▍Why 为什么
1. drift 告警自指：CUSUM 基线 mu0 取自被评估窗口自身均值（1.7389），阈值 1.5 远小于日组噪声 ±5~7pp，导致「高胜率却报漂移」。
2. 胜率创新高但均值为负 = 赢小亏大；盈亏比 0.43 与 09-11 持平未改善，指向出场纪律而非选股。
3. 概念成分 80/390 超 24h 未同步，成分调整期个股归属会错（data_health 唯一 issue，n_issues=1）。
▍How 怎么做
1. 议程自动落库（id=5，15:45:54→15:47:26）；执行环节今日未触发，需下一轮确认是否被执行。
2. C 类验收：修复后同一 10 组窗口重算，健康窗口不应触发 drift；合成退化序列仍须告警。
3. B 类验收：数据哨兵下一轮（盘中 15 分钟一轮）theme_members_fresh 转 ok。
▍明日关注
1. 议程 2 项是否被执行，尤其 CUSUM 统计修复——当前告警不可信，直接影响进化输入可信度。
2. 盈亏比 0.43 连续两日未改善，需查止损/跟踪止盈是否让亏损单跑满（今日最差单 -11.25%）。
不构成买卖建议。
```

## 2. 证据表（全部实测，2026-09-14 15:45–15:56）

| 项 | 取值 | 来源 |
|---|---|---|
| 交易日 | `trade_date=2026-09-14` | `GET /api/picks/watch-ledger` data.trade_date |
| 议程 | `id=5 / status=ready`，创建 15:45:54、完成 15:47:26（92s），items 2 条 | `GET /api/agent/agenda` |
| 议程 item-1 | class=C，priority=1，status=**pending**，files=`app/picks/signal_health.py` + `tests/test_signal_health.py`，关联 review_item #125 | 同上 |
| 议程 item-2 | class=B，origin=data_health，status=**pending**，theme_members_fresh | 同上 |
| 台账 | total 181 / settled 154 / tracking 27；success 74、fail 38、flat 42；`win_rate=0.6607`、`avg_pnl_pct=-0.39` | `GET /api/picks/watch-ledger` stats |
| 台账拆解 | 成功单均值 **+1.87%**、失败单均值 **-4.30%**、flat 均值 -0.84% → 盈亏比 **0.43** | 本地对 rows 重算 |
| 最差单 | 688432 有研硅 -11.25%；603082 北自科技 -9.73%；002848 高斯贝尔 -9.35% | 同上 |
| 未清算分层 | 27 条**全部** `watch_no_entry`（无 entry_price，未清算是设计） | rows layer 分布 |
| 信号健康 | `status=drift`，groups=10、picks=47，win_rate 0.766、good 36 / bad 11、mean_excess +1.7389；cusum `mu0=1.7389 / s_max=17.1354 / threshold=1.5`（mu0 与窗口均值完全相等 = 自指基线） | `GET /api/picks/signal-health` |
| 今日相位 | 2026-09-14 phase=**修复**，n=3、good 3、mean_excess +6.14 | 同上 history |
| 数据健康 | `n_issues=1`：theme_members_fresh（官方成分超 24h 未同步 80/390）；其余 8 项 ok | agenda inputs.data_health |
| 实验 | `data=[]`（0 条） | `GET /api/agent/experiments` |
| 代码变更 | `git status --porcelain` 为空；`docs/evolution/` 末份仍是 09-11 | 本地 git |

## 3. 与近期趋势对照

| 日期 | 胜率 | 平均盈亏 | 盈亏比 | 备注 |
|---|---|---|---|---|
| 09-10 | 50.0% | -1.20% | — | 台账首次出真实战绩 |
| 09-11 | 63.0% | -0.65% | 0.43（+2.28 / -5.36） | 首次拆成功/失败均值 |
| 09-14 | **66.1%** | **-0.39%** | **0.43（+1.87 / -4.30）** | 胜率新高，盈亏比**零改善** |

结论：连续第三日「胜率上行 + 平均为负」，且盈亏比两日完全持平在 0.43 —— 改善只发生在胜率侧，
亏损单的绝对幅度同步下降但幅度相同，**出场纪律侧没有任何实质改进**。这是本次报告的核心判断。

## 4. 新发现（供账本登记）

1. **议程停在 `ready` 未执行**：与 09-09「静默未生成」不同，今日议程生成正常（92s、2 项），
   但 item 状态 12 分钟内始终 `pending`，议程 status 停留在 `ready`（09-10/09-11 为 `executed`）。
   调度器 tick 正常（34s 内），故**不是调度器挂**，是执行环节未触发或需人工推进 → **须查执行入口**。
2. **signal_health 首次出组即暴露检测器缺陷**：CUSUM `mu0` 直接取被评估窗口自身均值（1.7389），
   基线自指使漂移方向不可解释；阈值 1.5 相对日组噪声（±5~7pp）过小 → 健康窗口误报 drift。
   议程已自诊断到该问题（C 类），但**未执行**。
3. **data_health 整体已恢复**：marketdb（23h 前）、sentiment_metrics（滞后 1 个交易日）、trade_calendar（242 天，末日当日）
   均转 ok，唯一 issue 为 theme_members_fresh（09-08 的头号问题 sentiment 停更已不再出现）。
