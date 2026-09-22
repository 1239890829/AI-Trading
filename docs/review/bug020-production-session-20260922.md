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
| 09:30 左右 | 开盘后 | 待采样 |
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

## 受控重启

- 状态：尚未执行。
- 原则：只执行一次，记录 restart 前最后可信 data_timestamp/price、PID、scheduler/health；重启后验证 DB integrity、30 scheduler、REST/WS 恢复、current 不倒退、不以空值覆盖旧可信值。

## 最终判定

**进行中。** 只有完整覆盖盘前、盘中、午间、下午、收盘并完成一次受控 restart/recovery 后，才允许把 BUG-020 改为“已完成”。
