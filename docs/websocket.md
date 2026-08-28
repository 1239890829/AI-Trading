# WebSocket 协议

## `/ws/quotes`（已实现）

**连接**：`ws://127.0.0.1:8000/ws/quotes?symbols=600519,000001`（缺省订阅全部自选）。

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

## 前端行为约定（hooks/use-quote-stream.ts）

- 优先 WS；断线指数退避重连；连续 3 次失败降级为 REST 轮询（5s），恢复后切回 WS。
- 每 15s 发送心跳 ping。

## 规划端点（Phase 6+）

`/ws/order-book` `/ws/trades` `/ws/market` `/ws/alerts` `/ws/paper-trading`
（消息结构沿用 type/seq/ts/data 约定。）
