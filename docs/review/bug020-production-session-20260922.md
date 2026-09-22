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
| 10:30 | 上午 / restart | PASS（服务恢复；并发执行污染另记） |
| 11:30 | 上午后段 | 待采样 |
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


## 09:34–09:35 独立复核

- 为避免仅依赖 09:30 自动检查，本轮再次独立采样。SQLite 继续 integrity_check=ok、39 tables；health=ok、is_stale=false、provider 无连续失败，scheduler 30/30 running。
- REST /api/quotes?symbols=600519,sh000001 返回两行 quality=high、batch coverage=1.0、quotes/indices rejection=0：
  - 600519：price=1248.86，data_timestamp=2026-09-22T01:30:12Z，received_at=2026-09-22T01:30:16.169632Z。
  - sh000001：price=3956.69，data_timestamp=2026-09-22T01:34:45Z，received_at=2026-09-22T01:34:48.344261Z。
- 随后连续读取 8 个真实 WS 帧：600519 event time 从 01:30:12 → 01:34:45 → 01:34:48 → 01:34:51 → 01:34:54；sh000001 从 01:34:45 → 01:34:48 → 01:34:51。所有帧 regressed_vs_seen=false，未发现 source event time 倒退或低质量值覆盖当前可信值。
- 本检查点继续支持 09:30 结论：真实源发生过 source_time_regress_ignored 后，系统保留旧可信 current 并自行恢复；恢复后 source event time 持续单调推进。


## 开盘 freshness 语义复核

- 09:30 的“Hub/batch freshness=ready，但个别 requested row 短暂 stale”不是未登记语义：backend/tests/test_quote_hub.py 的 test_hub_freshness_is_not_degraded_by_partial_gap 明确锁定这一取舍，避免 200 只中漏 1 只就把整页判成非实时；逐标的 Quote.freshness / quality 才是个体权威。
- 前端真实消费者没有只靠 batch freshness 隐藏个体异常：market/index/detail 路径均直接渲染 QualityBadge(quality, quality_reasons)，use-quote-stream 在 stale/market_closed 帧也会覆盖现值并显示休市/过期状态。
- 因此当前结论为 KEEP：Hub freshness 表示链路/批次可用性，row quality 表示单标的可信度；两者维度不同。后续全天继续观察是否存在绕过 row quality 的关键消费者，若出现才转为 BUG。

## 10:32–10:37 上午检查与受控重启

- single-writer 代码现场仍为 chatgpt/bug020-session-20260922，0 个 open PR；本轮没有领取 IMP-052。
- 10:32:39 重启前后端 PID=55303，cwd=backend，启动命令为 backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000。SQLite integrity_check=ok / 39 tables，scheduler 30/30 running。
- 重启前 Hub 自身仍连续成功、index 6/6、quotes/indices rejection=0；但全市场 snapshot 正处于一次真实上游退化：5566 rows，freshness=degraded，Sina page 7 HTTP 502，consecutive_failures=1，旧成功快照仍被保留，没有伪造空市场。
- 10:33:03 重启前真实 REST/WS：600519 最新推进到 price=1258.10 / data_timestamp=02:33:03Z；sh000001=3963.67 / 02:33:03Z，均 quality=high，source event time 与 received time 分离。
- 本执行器只对原 PID=55303 发出一次 graceful TERM；原进程正常退出，随后按同一 cwd/uvicorn 方式启动 replacement PID=52785。该实例启动后立即恢复 30/30 scheduler 与 DB integrity，但冷启动阶段 market snapshot 短暂 unavailable；REST 首帧只返回 sh000001，没有用空 Quote 覆盖 600519。真实 WS 订阅后约 1 秒恢复 600519，并在 02:34:12→02:34:21 连续推进；期间真实出现 quotes source_time_regress_ignored=2，但所有已接纳 current 的 regressed_vs_seen=false。
- **执行污染说明**：10:34:27 PID=52785 又发生一次 graceful shutdown，10:34:38 出现稳定 PID=53605，cwd/uvicorn 命令相同，stdout/stderr 指向 /private/tmp/ashare-bug020-20260922-uvicorn.log。与此同时 /private/tmp 下出现另一组同名 BUG-020 pre/post 取证文件，证明 10:30 时段存在一个并发执行器与本执行器重叠。当前没有第二个 Git writer、没有 open PR，但 process-level single-writer 被短暂破坏；因此今天**不再重复 restart**，避免把验收变成人为抖动。该并发属于协作/调度卫生异常，不掩盖也不改写生产数据结果。
- 稳定实例 PID=53605 在 10:35:45 已恢复 market snapshot=ready / 5566 rows / saved_files=1；10:37 health=ok、Hub is_stale=false、source_rejections=0，scheduler 30/30。REST 恢复为 600519 price=1256.00 / data_timestamp=02:37:00Z，sh000001=3965.56 / 02:37:03Z；随后 6 个真实 WS 帧继续推进到 600519 02:37:09、sh000001 02:37:06，全部 regressed_vs_seen=false。
- 判定：**应用层 restart/recovery 数据契约通过**——DB、scheduler、REST/WS、snapshot 均恢复，冷启动缺失以“缺席→恢复”表达，没有空值冒充 current，恢复后的 source event time 也未倒退。执行层“恰好一次”被并发执行器污染，作为治理异常单独保留，不再通过额外重启重做。后续 11:30/午间/下午继续验证稳定性。

## 最终判定

**进行中。** 只有完整覆盖盘前、盘中、午间、下午、收盘并完成一次受控 restart/recovery 后，才允许把 BUG-020 改为“已完成”。
