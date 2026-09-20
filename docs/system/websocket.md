# WebSocket 协议

## ⚠️ 部署关键：WS 必须直连后端，不能走 Next 代理

`app/backend/[...path]/route.ts` 是 fetch 型代理，**不支持 WebSocket 升级**。
前端必须配置 `NEXT_PUBLIC_WS_BASE`（如 `ws://127.0.0.1:8000`）绕过代理直连后端，
否则 WS 握手失败/假成功，前端全部实时数据冻结（2026-09-01 实测事故，修复 `650d34c`）。
开发环境配置在 `apps/web/.env.local`（模板见 `.env.local.example`）；改 env 后需清 `.next` 重启。

## `/ws/quotes`（已实现）

**连接**：`ws://127.0.0.1:8000/ws/quotes?symbols=600519,000001`（缺省订阅全部自选）。
**注意路径无 `/api` 前缀**（router 挂载时未带 prefix）。

**服务端 → 客户端**

```json
{ "type": "snapshot", "seq": 42, "ts": "2026-08-28T06:30:00Z", "data": [ /* Quote[] */ ] }
{ "type": "quotes",   "seq": 43, "ts": "...", "data": [ /* 增量 Quote[] */ ] }
{ "type": "stale",    "seq": 44, "ts": "...", "data": [ /* quality=stale 的 Quote[] */ ] }
{ "type": "pong",     "ts": "..." }
```

- `seq` 单调递增；客户端检测 seq 跳变即触发快照重拉（丢包检测）。
- 每条 Quote 自带 `source / quality / quality_reasons / data_timestamp / received_at`。
- `stale` 消息表示上游失败，客户端必须把对应标的显示为“数据过期”。

**客户端 → 服务端**

```json
{ "action": "ping" }
{ "action": "subscribe", "symbols": ["600519", "300750"] }
```

`subscribe` 更新本连接订阅集并立即回推新快照。

**发送互斥纪律（2026-09-01 修复）**：服务端所有出站消息（hub 推送/pong/subscribe
快照）必须经**同一条出站队列**由单一 writer task 串行 `send_json`——Starlette
禁止并发 send，reader 里直接发 pong 会与 writer 推送撞车，令 writer 抛
RuntimeError 被静默吞掉 → 推送死亡而 ping 存活（连接假活，客户端全量冻结）。

## 前端行为约定（hooks/use-quote-stream.ts）

- 优先 WS；断线指数退避重连；连续 3 次失败降级为 REST 轮询（5s），恢复后切回 WS。
- 每 15s 发送心跳 ping。

## 规划端点（**未实现** · 按需启动）

现已实现并投用的只有 `/ws/quotes`（`backend/app/websocket/routes.py`）。以下五个为规划，**均未实现**：

`/ws/order-book` `/ws/trades` `/ws/market` `/ws/alerts` `/ws/paper-trading`
（消息结构沿用 type/seq/ts/data 约定。）

## 推送节奏与 30s 冻结根因复盘（2026-09-01 秒级化改造）

**节奏**：`ASHARE_POLL_INTERVAL_SECONDS=1.0`（默认）→ 交易时段 WS 推送周期 1s
（固定节奏：run() 扣除本轮刷新耗时再 sleep）；休市自动降频 5s 保活；瞬时刷新
失败（数据年龄 < `ASHARE_STALE_AFTER_SECONDS=10`）不标 stale 不广播，防止
"数据过期"闪烁。

**"约 30 秒才更新一次"的真因 = 两个 bug 叠加互掩**（实测复盘，勿重蹈）：

1. **后端 subscribe 换队列孤儿化 writer（主因）**：旧实现处理
   `{"action":"subscribe"}` 时 `hub.unsubscribe(旧队列) + hub.subscribe(新队列)`
   ——而 writer task 正 `await slot[0].get()` **parked 在旧队列上**。队列被换
   后 writer 永远等在孤儿队列（get() 的等待目标在 await 开始时就已绑定），
   hub 往新队列广播无人消费 → 推送静默死亡。前端自选集变化（loadBase 10s
   轮询 → symbols 数组新引用）必触发一次 subscribe → 必冻结。
   修复：`hub.update_symbols(queue, symbols)` **原地改订阅集，队列终身复用**。

2. **前端订阅 effect 依赖数组引用死循环（被 1 掩盖）**：订阅 effect 原依赖
   `[key, symbols]`，而 `symbols` 数组每次渲染都是新引用 → 每次渲染重发
   subscribe → 后端回快照 → setQuotes → 再渲染 → 死循环（Maximum update
   depth exceeded，页面白屏）。bug 1 把快照回执吞掉恰好掐断了这个循环，
   修 1 后循环立刻暴露。修复：依赖只留稳定字符串 `key`，发送内容读
   `symbolsRef`，`lastSentKey` 去重；onopen 时重置 lastSentKey。

**组合表现**：bug 1 冻结推送 + 前端 32s 心跳自愈重连 → 每次重连回推一条
快照 → 用户体感"约 30 秒更新一次"（每次看到的都是重连快照，不是实时流）。

**教训**：WS 改动必须用「真实页面 + mock 秒变数据源」做端到端验证——裸
socket 探针测不出这两类 bug（探针不发 subscribe、不经过 React 渲染）。
