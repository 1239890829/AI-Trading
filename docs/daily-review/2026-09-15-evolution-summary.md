# 进化总结报告 · 2026-09-15（周二）

交易日确认：✅ `watch-ledger.trade_date=2026-09-15`，未触发"非交易日结束"。
飞书：17:27:29 发送成功 `om_x100b65a4b6dca8b4b3f1734eaefd0f7`（当日唯一一条）。

## 1 采集结果（只读，后端 127.0.0.1:8000）

| 来源 | 结果 |
|---|---|
| `/api/agent/agenda` | `id=6 / status=executed`，15:45:52→15:47:31（99s），3 项 B 类全部 executed |
| `/api/picks/watch-ledger` | trade_date=2026-09-15，total 87 / settled 69 / tracking 18；success 30 / fail 19 / flat 20；win_rate **0.6122**、avg_pnl_pct **-0.65** |
| `/api/picks/signal-health` | 11 组 / 48 样本，win_rate 0.75、mean_excess +1.3681；`status=drift` |
| `/api/agent/experiments` | `data=[]`、`shadow_queue=[]`（0 条） |
| agenda `inputs.data_health` | `n_issues=1`（theme_members_fresh 40/390） |

## 2 关键派生指标

- 成功单均值 **+1.78%**（n=30）／失败单均值 **-3.88%**（n=19）／flat 均值 -1.24%（n=20）
  ⇒ **盈亏比 0.46**（09-11 0.43、09-14 0.43）
- 最差单 **-11.02%**；layer 分布 pre_limit 69 / watch_no_entry 18；
  非 `watch_no_entry` 的未清算行 **0 条**（无回填异常）
- `cusum.mu0 = 1.3681` 与窗口 `mean_excess` **完全相等** → 自指基线未修，drift 告警不可信

## 3 结论

1. **09-14 遗留「议程卡 ready / items 全 pending」今日消失**：3 项 B 类全部 executed，
   产出落在 `docs/evolution/2026-09-15.md`（知识沉淀，不改代码不改参数）。
2. **胜率与盈亏比继续背离**：胜率 61.2% 看似健康，盈亏比 0.46 连续三日原地踏步 → 出场纪律问题。
3. **CUSUM 自指基线仍未修**（mu0 取自被评估窗口），首次出组结论不可直接用。

## 4 明日关注（结转）

- **自治开关默认已翻转为 False**：今日工作区把 `agent_autonomy_enabled` / `agent_code_change_enabled`
  默认值由 `True` 改为 `False`，且 `backend/.env` 未显式设置（实测 `Settings()` 读回 `False/False`）。
  17:23 重启后进程已按新默认运行 ⇒ **下一交易日议程可能退回「只生成不执行」**（= 09-14 表象）。
- 盈亏比 0.46 连续三日零改善：查止损/跟踪止盈是否让亏损单跑满。
- theme_members_fresh 40/390（昨日 80/390，已减半），观察是否继续收敛。
- 实验 0 条、影子队列 0；`plan_alignment.missing=hmm_regime` 连续 3 日未落地。
