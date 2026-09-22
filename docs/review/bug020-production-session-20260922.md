# BUG-020 生产完整交易会话验收 — 2026-09-22

> 目标：用当前 production provider 配置覆盖盘前→上午→午间→下午→收盘，并包含一次受控后端重启/恢复；核验 REST/WS/health、source_rejections、source event time / received time、缺失/恢复和 current-value 单调接纳。生产会话只做观察与受控服务重启，不向真实 current cache 注入伪造晚到/非法报价；恶意/非法输入仍由既有隔离契约测试证明，真实生产以跨时点观测证明 current/source-time 不倒退。

> **定位 / 摘要**：本文件是 BUG-020 的一次性生产完整会话证据，不承担第二账本。它按盘前、开盘、上午、午休、下午与收盘检查点记录真实 provider/REST/WS/SQLite/scheduler/snapshot 行为；任务状态仍以 `docs/stages/w00-foundation.md` 为唯一权威，最终放行以本页“最终判定”和 PR/CI 证据共同成立为准。

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
| 11:19–11:21 | 上午后段 | PASS / 上游 Sina 缺页被 fail-closed（见下） |
| 11:47–11:53 | 午休早段 | PASS / cadence 与 market_closed 语义通过（见下） |
| 12:35–12:38 | 午间后段补采样 | PASS / market_closed + idle cadence 复验（见下） |
| 13:30 | 下午首检 | PASS / 午休→下午恢复（见下） |
| 14:30 | 下午后段 | PASS / 真实 source-time 拒绝→保留 current→自愈（见下） |
| 15:10–15:30 | 收盘后 | PASS / market_closed + idle cadence + current 单调接纳（见下） |

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

## 10:40–10:54 验收中发现的真实缺口与最小修复

- **缓存年龄未映射到行级 quality**：重启后 WS 订阅断开，600519 退出 1Hz 轮询池，但对象仍留在 Hub current cache。10:40 连续三次 REST 读取中，上证指数 event time 从 02:40:54 持续推进，而 600519 停在 02:37:09；后者仍返回 quality=high。根因是 Quote.freshness() 已能按 data_timestamp 判年龄，但 QuoteHub.get_quotes() 直接返回缓存对象，读取层没有应用年龄语义。修复新增只读 _visible_quote：超过 Hub stale_after 时返回 quality=stale / quote_age_exceeded 的深拷贝，**不修改共享 current、不增加网络请求**；下一条可信观测仍可正常推进缓存。
- **board_surge 生产循环持续异常**：旧进程日志从 09:35 到 10:33 多次出现 board surge beat failed，堆栈固定为 seal_sequence() 对 provider 返回的 Pydantic LimitUpRecord 调 .get()。历史单测只覆盖 dict 夹具，漏掉真实 provider 类型。修复让字段读取同时兼容 dict 与对象属性，并新增真实 LimitUpRecord 回归。
- **全市场快照缺页仍被标 ready**：10:39–10:43 生产新浪快照连续只有 5466 rows，10:44 又恢复 5566；恰少 100 行与单页 page_size 一致，但旧代码只要求 rows >= stock-count 的 90%，因此会把缺整页的市场宽度保存并标 ready。修复把 stock-count 作为硬分母：rows 与 market+symbol 唯一身份数都必须精确等于 count，否则整轮 ProviderError，沿用已有最近可信值/降级链，不发布部分市场。
- **pytest 本机生产凭据渗透**：定向 test_api 初次运行时，全会话 socket 硬门抓到 open.feishu.cn 的真实 DNS 尝试。CI 环境通常无生产飞书凭据，本机 .env 却有，因此旧测试存在环境依赖。修复在 conftest 导入 app 前显式清空五个飞书目标/凭据环境变量；飞书专项测试继续自行构造桩，不改变生产配置。
- 修复后相关后端组合回归（quote_hub / board_surge / board_surge_phase2 / quotes endpoint / notifier_feishu / notifications / notification_outbox）全部通过；pyflakes 通过；前端 QualityBadge 新增 quote_age_exceeded 人话提示回归后 8/8 通过；git diff --check 通过。
- 10:54 时 PID=53605 仍加载修复前代码，因此随后必须做一次**修复部署重载**并用真实 REST/WS 复验；该动作的结果见下一节。由于 10:30 自动执行器此前已造成额外 restart，修复重载单独记为缺陷部署，不伪称整日只有一次进程切换。

## 11:00–11:02 hotfix 生产验收

- hotfix 已以 HEAD=a43f330 启动到 PID=73633；启动后 30/30 scheduler running，SQLite integrity_check=ok / 39 tables。
- 严格全市场分母门首次真实命中：11:00 Sina 再次只返回 5466/5566，系统不再把 98.2% 覆盖冒充 ready，而是 market_snapshot=unavailable、last_error=sina snapshot incomplete: rows=5466 unique=5466 expected=5566。11:02:35 上游恢复后自动回到 ready / 5566 rows / consecutive_failures=0，证明 fail-closed 与自愈同时成立。
- 缓存年龄门做了真实订阅→断开→老化→重订阅闭环。600519 在 03:00:54 为 high；断开后超过 stale_after=10s，REST 保留同一 price/current 但正确变为 stale / quote_age_exceeded，而 sh000001 同时继续 high/实时推进。重新订阅首帧仍诚实显示旧 stale，下一帧 600519 恢复 high，event time 从 03:00:54 推进到 03:01:09，未倒退。
- board_surge 用当日真实 limit-up pool 做只读验证：pool_len=23，provider 返回类型=LimitUpRecord；seal_sequence 可直接处理真实对象并生成“内蒙新华 09:34（5板）”。hotfix 日志当前 board_surge_error_count=0。
- BUG-020 平台定时自动续跑已停用并复核为 disabled；后续本日检查只走人工单写，避免再次出现任务状态与本机动作不同步、重复 restart/commit 的竞态。

## 11:19–11:21 上午后段复核

- hotfix 进程稳定为 PID=73633，cwd=backend；SQLite integrity_check=ok / 39 tables，scheduler 30/30 running，Hub consecutive_failures=0 / last_error=null。
- 600519 在未订阅状态下已超过 stale_after：连续三次 REST 都保留 price=1257.70 / data_timestamp=03:14:57Z，但正确返回 stale / quote_age_exceeded；同期 sh000001 持续推进。重新建立真实 WS 订阅后，首帧仍诚实显示旧 stale，下一帧即恢复 high，并从 03:14:57 推进到 03:19:48，随后继续到 03:19:51；6 帧均 regressed_vs_seen=false。
- 严格 Sina 分母门再次真实命中：上游一轮返回 5466/5566，系统保留上一份 5566 行 snapshot，不写入部分市场；11:21 时 snapshot 因旧可信值已超过 180s 窗口明确变 stale，last_error=sina snapshot incomplete: rows=5466 unique=5466 expected=5566。该状态属于上游退化的诚实暴露，不回退旧 90% 假成功规则。
- 新 hotfix 日志自启动以来 board_surge_error_count=0；未再出现 LimitUpRecord.get 异常。
- runtime-selector 隔离回归 6/6 通过；11:20 当前真实账本 static/effective 均为 G0/BUG-020，conditions=[]，证明任务进入“进行中”后不会因过 09:15 错误退回 G4/IMP-052。
- 自动续跑已再次停用并复核 is_enabled=false；此前已启动实例的回写已结束。后续只保留当前人工 single-writer。
- 对自动执行实例留下的 5439ad8 与 d2d442a 已独立审阅：前者仅补真实 hotfix 运行证据；后者把 runtime-selector 测试从可变真实账本状态隔离为固定夹具，并补“进行中条件任务过窗口仍保持 G0”契约，均 KEEP。
- doc-health、public-repo scan、workspace hygiene、git diff --check 全部通过。

## 11:11–11:28 缺失/恢复与上午收尾

- 11:11:43 发生一次真实宿主 DNS 短断：Sina / Tencent / Eastmoney / THS 的 quotes/indices 在同一秒内均失败，两轮 Hub refresh 明确记录 keeping last good data；因最后可信值年龄仍小于 stale_after，没有把 current 清空或用失败结果覆盖。网络恢复后 11:12 起正常推进。
- Sina 严格完整性门连续捕获：11:12 少 100 行（5466/5566）、11:14 少 200 行（5366/5566），均拒绝发布；11:18 恢复 5566，11:19 再少 100 行后，11:21/11:22/11:23 连续恢复。11:23 独立直拉同样为 5566 rows / 5566 unique。
- 取舍：**KEEP 严格 stock-count 分母 + 现有普通退避**。这类可识别缺页已在 2–4 分钟内自愈，且旧可信快照会由 ready→degraded/stale 诚实暴露；不把所有非限流失败都接入高成本全市场 fallback，避免一次异常就额外批量请求数千股票。
- 60 秒真实 WS 观察共 61 帧，600519 / sh000001 / 600105 / 603118 / 603228 全部 event-time regression=0。600519 首帧因断订阅后的旧缓存为 quote_age_exceeded，后续 60 帧均 high；其余标的只在源真实晚到时出现 1–3 帧 source_time_regress_ignored，并在下一可信观测自动恢复。
- 11:28 上午收尾：health=ok、Hub consecutive_failures=0 / last_error=null、index 6/6、source_rejections=0；snapshot=ready / 5566 rows / age≈13s；scheduler 30/30，hotfix PID=73633 持续运行，日志 ERROR=0、Traceback=0、board_surge_error_count=0。
- 主动扫描中另见 THS 429、个别 watchlist 瞬时缺失、deepseek-v4-flash 兼容警告等；它们均有现有 owner/熔断或与 BUG-020 无直接因果，本片不借机扩权修改。

## 11:39–11:45 午休 cadence 假 stale 修复

- 11:39 午休现场出现 health=degraded，但 Hub 本身 consecutive_failures=0、last_error=null、index 6/6、rejections=0；唯一异常是 snapshot freshness=stale。11:40 下一轮刷新后立即回 ready，说明不是数据源持续故障。
- 根因：MarketSnapshotService.run() 在非连续竞价时段明确用 IDLE_INTERVAL_SECONDS=240s 降频，但 freshness() 仍固定按 self.poll_interval×3 判定；生产 poll_interval=60s，所以午休每个周期的第 181–240 秒都会把“按设计尚未到下一拍”的正常快照误判 stale。现有 test_off_hours_window 通过把 svc.poll_interval 人工改成 240 来模拟休市，未覆盖生产真实路径。
- 修复：新增 _nominal_interval(live) 作为调度与 freshness 共用的设计 cadence；交易中仍用 poll_interval，午休/盘后用 IDLE_INTERVAL_SECONDS。freshness 窗口继续保持“当前 cadence ×3”的原意，不放宽盘中 180s 规则。
- 回归：午休生产形态 poll_interval=60s、age=300s 时 freshness(live=False)=ready 且 live=True=stale；age=800s 时 live=False 仍必须 stale。fallback 老化测试改为显式 live=True，避免宿主时钟决定结论。freshness + snapshot_availability 全组通过，pyflakes 与 diff-check 通过。
- 该修复只纠正时间语义，不改变全市场取数频率、严格 stock-count 分母、fallback 阈值或交易/策略行为。

## 11:47–11:53 午休生产复验与完整回归

- 最新 HEAD=28a2f7c 已部署到 PID=9077。启动后 SQLite integrity_check=ok / 39 tables、scheduler 30/30；QuoteHub 在午休明确进入 market_closed，REST/WS 均返回 stale / market_closed，WS 约 5s 保活，不再把午休当实时 1Hz。
- snapshot 11:48:21 首轮成功保存 5566 行。11:51:19 age=178.4s 仍 ready；11:51:46 age=205.1s 仍 ready、rows=5566、failures=0，直接命中旧实现会在 180s 后误判 stale 的反例区间。11:52:27 按 240s idle cadence 正常刷新 5566 行；11:53 age=46.1s / ready。
- /api/health 午休总体仍为 degraded，是因为 QuoteHub 的 market_closed 使 hub.is_stale=true；这是现有数据实时性语义，不是进程 liveness 失败。HTTP 200、Hub failures=0 / last_error=null，snapshot ready。KEEP 该语义，不把正常午休改成“实时 ok”。
- 最新 HEAD 的完整 backend pytest 已再次跑到 100% exit 0，随后 pyflakes app/tests/scripts 通过；午休两层定向回归（trade_calendar/QuoteHub/freshness/snapshot）也全部通过。
- 前端本片完整回归为 Vitest 73 files / 696 tests 全绿，TypeScript、CI 同款 npx eslint .、Next 16.3.3 production build 全绿。构建产生的 .next 约 60MB 已删除，next-env.d.ts 的自动生成差异已恢复；无构建副产物入账。
- 清理一个由旧执行实例遗留、无仓库 open-file 的阻塞 Python REPL；平台 BUG-020 自动续跑保持 disabled，当前只剩人工 single-writer。
- 对插入提交 bdf8eef 已独立审查并 KEEP：它把 QuoteHub 宽松窗口从连续 09:15–15:05 改为 09:15–11:35 / 12:55–15:05，补午休 market_closed 回归，与 snapshot idle cadence 修复互补而不重复。

## 12:35–12:38 午间后段补采样

- single-writer 复核：活动分支仍为 `chatgpt/bug020-session-20260922`，HEAD=`ae91e68`；采样前无其它 BUG-020 终端执行会话/写执行器，后端仍为既有 PID=9077，本轮**未 restart**、未领取 IMP-052 或其它业务切片。
- SQLite 使用生产库 `data/ashare.db` 复核：文件大小 320,843,776 bytes，`PRAGMA integrity_check=ok`，39 tables；scheduler 继续 30 total / 30 running / 0 dead。
- 12:36 `/api/health`：Hub `consecutive_failures=0 / last_error=null`、index 6/6、quotes/indices source_rejections 均为 0。总体 `status=degraded / is_stale=true` 仍仅由午休 `market_closed` 实时性语义导致，不是进程或上游持续故障。
- 午休 cadence 再次命中旧 bug 的反例：12:36 snapshot age=199.0s 时仍正确为 `ready`（旧实现会在 180s 后误判 stale）；12:37:17 自动刷新并保存新 5,566 行 Sina snapshot，12:38 health 显示 age=47.6s / `ready`、process-local saved_files=7、save failures=0。磁盘当日 snapshot 共 42 份，最近文件 `043717.parquet`。
- REST 600519 + sh000001 均明确返回 `stale / market_closed`，没有把午休最近值冒充实时；12:36 时 600519 保留 price=1255.6 / event time=03:48:12Z，上证指数 price=3958.64 / event time=04:05:00Z。
- 真实 WS 订阅连续采 5 帧（seq 607→611）：首帧后约 5 秒 cadence 保活，600519 event time 从 03:48:12Z 单调推进到 04:05:42Z 后保持，上证指数保持 04:05:00Z；所有帧 `regressed_vs_seen=false`，quality 始终 `stale / market_closed`，source_rejections 始终 0。received_at 持续推进，但 source event time 未被收到时间冒充。
- 本检查点未观察到新的缺失→恢复事件；既有 current 值在午休被保留且以 stale/market_closed 诚实暴露，未出现空值覆盖、event-time 倒退或伪实时。判定：**午间后段 PASS**，BUG-020 继续保持“进行中”，等待 13:30/14:30 下午检查与 15:10 收盘终验。

## 13:30–13:31 下午首检

- single-writer 再复核：活动分支仍为 `chatgpt/bug020-session-20260922`；13:30:57 与写报告前 13:32:22 两次检查均未发现其它 BUG-020 执行器、Git 写操作、SQLite 写探针或 `.git/*.lock`。后端继续使用既有 PID=9077，本轮**未 restart**、未领取 IMP-052 或其它业务切片。
- 13:31 `/api/health` 已完成午休→下午状态切换：`status=ok / is_stale=false`，Hub `consecutive_failures=0 / last_error=null`，index 6/6；quotes/indices `source_rejections=0`。scheduler 继续 30 total / 30 running / 0 dead。
- SQLite 使用生产库 `data/ashare.db` 只读复核：320,905,216 bytes，`PRAGMA integrity_check=ok`，39 tables。
- market snapshot 为 `ready / 5566 rows / source=sina`，age=51.3s、save failures=0、process-local `saved_files=15`；磁盘当日 snapshot 已从 12:38 的 42 份推进到 50 份，最近保存 `data/parquet/snapshots/20260922/052819.parquet`（北京时间 13:28:19）。全市场保存链持续前进。
- 午休→下午恢复是**异步但诚实**的：13:31:22 REST/WS 首帧中 sh000001 已恢复 `high`（event time=05:31:18Z），而未持续订阅的 600519 仍保留午休可信 current：price=1255.6、event time=04:05:42Z、`stale / market_closed`；系统没有把旧值伪装成实时，也没有清空 current。
- 建立真实 WS 订阅后的下一帧（seq 2989，约 1.07s 后）600519 自动恢复 `high`，price=1254.76、event time=05:31:21Z；随后 12 帧采样至 seq 2999，600519 event time 单调推进至 05:31:30Z，sh000001 从 05:31:18Z 单调推进至 05:31:30Z，`regressed_vs_seen=false` 全部成立。`received_at` 独立持续推进，没有被拿来替代 source event time。
- 12 帧内两标的始终有行，未出现空值/缺行覆盖；本检查点可见的恢复形态是 600519 `stale/market_closed → high`，且 quotes/indices source_rejections 全程为 0。没有发现新的生产缺陷，不触发代码修复。
- 判定：**13:30 下午首检 PASS**。午休旧 current 被保留并明确标 stale，下午真实数据到达后约 1 秒自愈为 high；current/source event time 无倒退，DB/scheduler/snapshot 全部健康。BUG-020 继续保持“进行中”，等待 14:30 下午后段与 15:10 收盘终验。


## 14:30–14:32 下午后段复核

- single-writer 复核：14:29:41 开始前活动分支为 `chatgpt/bug020-session-20260922`、HEAD=`ae91e68`；未发现其它 BUG-020 执行器、报告写进程、Git commit/merge/rebase/push、pytest 或 `.git` 写锁。后端仍是既有 PID=9077；14:32 再查 PID 未变。本轮**未 restart**、未领取 IMP-052 或其它业务切片。
- 14:30:19 `/api/health` 捕获到一次真实、可解释的瞬时退化：Hub `consecutive_failures=0 / last_error=null / is_stale=false`，但 index batch 暂为 4/6，缺 `399001`、`399006`，`source_rejections.indices=2` 且原因均为 `source_time_regress_ignored`；quotes rejection=0。market snapshot 同时仍为 `ready / 5566 rows / source=sina`、age=58.4s，没有把指数源拒绝扩大成全市场快照失败。
- 建立真实 WS `600519 + sh000001` 后，14:31:25 首帧中 index batch 已最迟恢复到 6/6、rejections=0。600519 因自 13:31 后未持续订阅，首帧诚实保持 `stale / quote_age_exceeded`：source=tencent、price=1254.76、`data_timestamp=05:31:30Z`、`received_at=05:31:33.254823Z`；sh000001 同帧为 `high`、`data_timestamp=06:31:21Z`。
- 下一帧约 0.66s 后，600519 自动恢复 `high`，price=1253.45，source event time 从 `05:31:30Z` 单调推进到 `06:31:24Z`，`received_at=06:31:26.518300Z`；sh000001 同步为 `high / 06:31:24Z`。随后共采 30 个真实 WS 帧至 14:31:54，两条 requested row 全程存在，600519 最终推进到 `06:31:51Z`、sh000001 最终推进到 `06:31:51Z`，所有帧 `regressed_vs_seen=false`。
- 观察窗口内又真实命中接纳门：14:31:52 一帧 `source_rejections.quotes=1 / source_time_regress_ignored`，已接纳的 600519 current 不倒退；14:31:53 index batch 瞬时降到 2/6（缺 `000001`、`000300`、`000688`、`000852`），`source_rejections.indices=4`，sh000001 仅把既有 `06:31:51Z` current 标为 `stale / source_time_regress_ignored`，没有接受更旧 event time。14:31:54 下一帧即恢复 6/6、rejections=0、sh000001 回到 `high`，仍保持 `06:31:51Z`，明确证明“拒绝旧源值→保留 current→恢复”而非 current 倒退。
- 14:32:09 收尾复核 `/api/health` 已回 `status=ok / is_stale=false`、index 6/6、quotes/indices rejections=0；scheduler 30 total / 30 running / 0 dead。生产 SQLite `data/ashare.db` 为 `integrity_check=ok`、39 tables。
- market snapshot 保存链继续前进：13:30 时磁盘当日 50 份，本次已到 61 份；最近已保存 `data/parquet/snapshots/20260922/062715.parquet`（北京时间 14:27:15），process-local `saved_files=26`、save failures=0；14:32 的内存快照仍 `ready / 5566 rows`，age=41.1s，说明采集与落盘都在持续推进。
- 本检查点没有发现新的生产缺陷，不触发代码修复。判定：**14:30 下午后段 PASS**。真实 source-time 回退被拒绝且对外显式降质，可信 current/source event time 没有倒退；短暂 index 缺失与 600519 订阅老化都按既有契约自动恢复。BUG-020 保持“进行中”，继续等待 15:10 收盘终验。

## 15:10–15:30 收盘终验

- single-writer 终验：活动分支保持 `chatgpt/bug020-session-20260922`，后端全过程仍为既有 PID=9077；未发现第二 BUG-020 writer、Git 锁或并发发布动作。本阶段**未 restart**、未领取 IMP-052、未向 production current cache 注入任何伪造数据。
- 15:10 后 `/api/health` 按收盘语义进入 `degraded / is_stale=true`，但 Hub `consecutive_failures=0 / last_error=null`，index 6/6、quotes/indices `source_rejections=0`。这是 `market_closed` 的实时性降级，不是 liveness 或 provider 持续故障。scheduler 全程 30 total / 30 running / 0 dead；生产 `data/ashare.db` 终验 `PRAGMA integrity_check=ok`、39 tables。
- 15:12:46–15:13:20 真实 WS 连续 8 帧中，600519 与 sh000001 始终存在且均为 `stale / market_closed`；600519 source event time 从 `06:31:51Z` 单调推进到 `07:12:42Z`、再到 `07:13:12Z`，sh000001 从 `07:12:00Z` 推进到 `07:13:00Z`，所有帧 `regressed_vs_seen=false`。`received_at` 独立推进，没有替代 source event time。
- 15:20 再验：snapshot 仍为 `ready / 5566 rows / source=sina`，save failures=0；REST 600519 与 sh000001 都明确 `stale / market_closed`，meta 说明“当前为最近交易日数据，不冒充实时”。磁盘当日 snapshot 已累计 69 份。
- 15:30:04 终点采样继续满足：Hub failures=0、index 6/6、source_rejections=0；snapshot `ready / 5566 rows`、age=218.7s、save failures=0，恰好再次证明收盘后按 240s idle cadence 计算 freshness，不会回到旧 180s 假 stale；磁盘当日 snapshot 70 份，最新 `data/parquet/snapshots/20260922/072218.parquet`。REST 600519 保留 `data_timestamp=07:13:12Z`，sh000001 为 `07:29:00Z`，均 `stale / market_closed`。
- 生产日志对当前 PID 的 `ERROR|Traceback|board surge beat failed` 计数为 0。14:39 的 Sina HTTP 502 与 14:41 的 5366/5566 缺页被严格完整性门拒绝，随后自行恢复；这进一步支持“失败不发布部分市场、旧可信值保留并自愈”的接纳契约。
- 全天证据链已完整覆盖盘前→开盘→上午→受控 restart/recovery→真实缺失/恢复→午休→下午→收盘。10:30 时段曾发生的并发执行器额外进程切换继续作为执行治理污染保留，不改写成“恰好一次”；应用层 DB/scheduler/REST/WS/snapshot 恢复契约已由后续稳定运行和全天检查点反复验证。
- 收口回归：完整 backend pytest 100% exit 0，随后 pyflakes exit 0；BUG-020 定向后端组 100% 通过；前端 Vitest 73 files / 696 tests 全绿，TypeScript、ESLint、Next 16.3.3 production build 全绿，QualityBadge 定向 8/8 通过。构建副产物已清理。`doc-health`、public-repo scan、workspace hygiene、`git diff --check` 均通过。

## 最终判定

**已完成。** 2026-09-22 的一次性生产完整交易会话验收满足 BUG-020 接纳条件：失败/缺失/晚到源值不覆盖可信 current，source event time 与 received time 分离，真实 source-time 回退被拒绝且可见，午休/收盘不冒充实时，缺失与上游退化均能 fail-closed 后恢复，受控重启后的数据链也稳定恢复。该结论只证明本日完整会话与既有隔离契约，不外推为长期 provider SLA；长期异常率与可用性仍由持续监控承担，不再无限期阻断阶段门。 完成状态写回后 runtime selector 于 15:33 重算为 static/effective **G4/IMP-052**；本轮只记录下一门，不执行下一业务切片。
