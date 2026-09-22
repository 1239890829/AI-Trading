# BUG-020 生产完整交易会话验收 — 2026-09-22

> 目标：用当前 production provider 配置覆盖盘前→上午→午间→下午→收盘，并包含一次受控后端重启/恢复；核验 REST/WS/health、source_rejections、source event time / received time、缺失/恢复和 current-value 单调接纳。生产会话只做观察与受控服务重启，不向真实 current cache 注入伪造晚到/非法报价；恶意/非法输入仍由既有隔离契约测试证明，真实生产以跨时点观测证明 current/source-time 不倒退。

## 会话身份

- 日期：2026-09-22（A 股交易日；backend/data/trade_calendar.json 已覆盖）
- 开工：08:32 CST（Asia/Shanghai）
- 代码基线：master@c05abf793924bf7fbc4e4c93ae82aea6ec389bee
- 工作分支：chatgpt/bug020-session-20260922
- production provider：chain(ths→tencent→eastmoney→sina)
- 模式：DEGRADED_FULL_CONTROL
- 边界：本次只验 BUG-020；不领取 IMP-052，不改策略阈值/权重，不做真实交易。

## 检查点

| 时点 | 阶段 | 结果 |
|---|---|---|
| 08:32 | 盘前开工 | PASS（见下） |
| 09:30 左右 | 开盘后 | PASS / 观察到真实拒绝→恢复（见下） |
| 10:30/11:30 | 上午 | 待采样 |
| 12:30 | 午间 | 待采样 |
| 13:30/14:30 | 下午 | 待采样 |
| 15:10–15:30 | 收盘后 | 待采样/终验 |

## 08:32–08:35 盘前基线

- runtime selector：静态 G4/IMP-052，有效选择 **G0/BUG-020**；condition=A_SHARE_OBSERVABLE_SESSION，state=active，reason=observable_a_share_session_start。
- Git：HEAD == origin/master == c05abf793924bf7fbc4e4c93ae82aea6ec389bee；开工前无 open PR、仅主 worktree，运行 JSON 保留为未跟踪真实证据。
- SQLite：data/ashare.db 311,160,832 bytes；PRAGMA integrity_check=ok；39 tables。
- scheduler：30 total / 30 running / 0 dead。
- health：provider=chain(ths→tencent→eastmoney→sina)，consecutive_failures=0，last_error=null；index batch 6/6；source_rejections.quotes=0、indices=0。
- market snapshot：5564 rows，source=sina，last saved data/parquet/snapshots/20260922/002957.parquet；快照 freshness 自身为 ready，但盘前 health 整体为 degraded/is_stale=true，这是开市前时间边界，不记为采集失败。
- 真实 WS 600519,sh000001：
  - 上证指数：source=tencent，quality=stale / market_closed，received_at=2026-09-22T00:34:50.839107Z，data_timestamp=2026-09-21T08:14:00Z。
  - 贵州茅台：source=tencent，quality=stale / market_closed，received_at=2026-09-22T00:34:55.846346Z，data_timestamp=2026-09-21T08:14:37Z。
  - WS meta 同步暴露 index coverage 6/6 与 quotes/indices rejection=0。
- 解释：盘前真实帧清楚区分 **source event time (data_timestamp)** 与 **received time (received_at)**，没有把“今天收到的昨日行情”冒充为今天事件时间；旧值保持 stale/market_closed，符合 BUG-020 接纳边界。


## 09:30–09:31 开盘检查点

- single-writer：活动分支仍为 `chatgpt/bug020-session-20260922`，HEAD=`16d1d51b296232259fcc5d9c258d1c21d90d2d15`；0 个 open PR、1 个 worktree；BUG-020 仍为“进行中”，未领取 IMP-052 或其它业务切片。
- 09:30:19 health 已从盘前 `degraded/is_stale=true` 恢复为 `status=ok / is_stale=false`；provider 仍为 `chain(ths→tencent→eastmoney→sina)`，`consecutive_failures=0`、`last_error=null`，指数覆盖 6/6。
- SQLite 继续 `integrity_check=ok`、39 tables；scheduler 30/30 running；market snapshot 为 5,566 rows，`saved_files=95`，最新保存 `data/parquet/snapshots/20260922/012709.parquet`，snapshot freshness=`ready`，无保存失败。
- REST + 真实 WS 在开盘初始阶段出现**可解释的异步恢复**：09:30:19 时上证指数已为当日高质量值（`data_timestamp=09:30:03`），但 600519 仍保留昨日 `16:14:37` 的 `stale/market_closed` 可信旧值；两条通道展示一致，没有用空值或未知值覆盖旧 current。
- 09:30:39 复测，600519 已恢复为当日高质量值：price=1248.86，`data_timestamp=09:30:12`，`received_at=09:30:16.169632`；上证指数为 price=3959.61，`data_timestamp=09:30:36`。因此 600519 的 event time 从昨日值单调推进到今日值，未出现倒退。
- 09:30:54 真实源又产生一次可见拒绝：REST meta `source_rejections.indices.count=4`，reason=`source_time_regress_ignored`；上证指数行同时以 `quality=stale` 暴露该拒绝背景。到 09:31:09 再采样，指数恢复 `quality=high`、`data_timestamp=09:31:06`，rejections 回到 0；09:31:24 继续推进到 `data_timestamp=09:31:21`。
- 当前判定：**通过开盘检查点，但保留一个语义观察项继续跟踪**——批次/health freshness 可为 ready，而单个 requested row 在短暂恢复窗仍可能是 stale；行级 quality/reasons 正确暴露了这一点。现阶段不把它判成 BUG，因为 row-level contract 没有隐藏陈旧值，且 600519 在约 20 秒内自行恢复；后续上午/午间继续核消费者是否错误只看 batch freshness。
- 后端 PID 仍为 55303；当前尚未到 10:00–14:00 重启窗口，本检查点**未执行重启**。

## 受控重启

- 状态：尚未执行。
- 原则：只执行一次，记录 restart 前最后可信 data_timestamp/price、PID、scheduler/health；重启后验证 DB integrity、30 scheduler、REST/WS 恢复、current 不倒退、不以空值覆盖旧可信值。

## 最终判定

**进行中。** 只有完整覆盖盘前、盘中、午间、下午、收盘并完成一次受控 restart/recovery 后，才允许把 BUG-020 改为“已完成”。
