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

## 规划端点（Phase 6+）

`/ws/order-book` `/ws/trades` `/ws/market` `/ws/alerts` `/ws/paper-trading`
（消息结构沿用 type/seq/ts/data 约定。）
