# 进化总结 · 2026-09-18（每日唯一一条飞书推送原文）

- 生成时间：2026-09-18 18:4x CST
- 交易日确认：watch-ledger trade_date=2026-09-18（与今日一致）✅
- 议程：`id=9 / status=executed`，15:45:18 创建，2 项 B 类全部 executed
- 数据来源：全部只读，后端 http://127.0.0.1:8000（health 200）

## 推送原文

见下方「报告正文」一节。

## 证据表

| 项 | 值 | 来源 |
|---|---|---|
| 议程 | id=9 / executed / 2 项 B 类（无 C 类 ⇒ 零代码变更） | `GET /api/agent/agenda` |
| 议程产出 | `docs/evolution/2026-09-18.md`（3087 B，15:45 落盘） | 落盘文件 |
| 台账 | total 172 / settled 136 / tracking 36；success 68 / fail 33 / flat 35 | `GET /api/picks/watch-ledger` stats |
| 胜率 | 67.33%（分母 68+33，排除 35 条 flat） | 同上 |
| 平均盈亏 | +0.95%（连续第二日正值，昨日 +0.66%） | 同上 |
| 盈亏比（表观） | 1.16 = 成功 +4.13% / 失败 −3.57% | rows 按 verdict 现算 |
| 盈亏比（剔除离群） | **0.51**（剔除 N沈鼓集团 601091 +159.06% 单笔；成功均值降至 +1.82%） | 同上，敏感性检验 |
| 成功单中位数 | +2.14%（均值 4.13% 被离群值拉高） | 同上 |
| 最差单 | −8.29% | 同上 |
| signal_health | 13 组 / 51 样本，win_rate 0.7451，mean_excess +0.9753，status=drift | `GET /api/picks/signal-health` |
| CUSUM | mu0=0.9753 **恰等于**窗口 mean_excess（自指，连续 5 日未修），s_max 14.63 / threshold 1.5 | 同上 / agenda inputs |
| data_health | **n_issues=0**，9 项全 ok（昨日 2） | agenda `inputs.data_health` |
| theme_members_fresh | 0/391（昨日 120/391 → 已清零） | 同上 |
| marketdb | 378 MB，22h 前更新，滞后 1 个交易日（阈值 3 内） | 同上 |
| trade_calendar | 243 天，末日 2026-09-18，source=official | 同上 |
| 实验 / 影子队列 | 0 / 0 | `GET /api/agent/experiments` |
| 快照 | `data/parquet/snapshots/20260918` 75 文件，末文件 103439@18:34（今日全天在跑） | 文件系统 |

## 关键判断

1. **盈亏比首次"破 1"是统计假象**：1.16 完全由新股 N沈鼓集团（601091）+159.06% 撑起；
   剔除后 0.51，与 09-11(0.43)/09-14(0.43)/09-15(0.46)/09-16(0.53) 同一水平 ⇒ 实质零改善。
   报告必须带敏感性口径，否则会把单一样本当成进化收益。
2. **连续两日"告警有、判据无"**：drift 告警（13 组中 3 天 n=1）与「梯队断层」（台账同类 7 条、
   applied 5 条、主题词 13→5）都只有计数、无可证伪判据 —— 今日两项 B 类均指向此，方向正确。
3. **data_health 归零是昨日修复落地的硬证据**：theme_members_fresh 从 120/391 到 0/391。
