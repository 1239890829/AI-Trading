# WebSocket 协议

## 默认同源接入与直接连接

前端默认连接同源 `/backend/ws/quotes`。Next 16 的 `apps/web/proxy.ts` 仅对该精确路径的 WebSocket 升级做运行时外部 rewrite，目标沿既有 `BACKEND_ORIGIN`；普通 HTTP 仍使用 fetch 型 Route Handler。后端端口可继续只监听回环地址。

子协议凭据原样透传，代理不注入访问密钥。后端仍是认证事实源：已配置 token 时拒绝无/错凭据，合法 local 无token模式保留；shared 缺token仍拒绝启动。`NEXT_PUBLIC_WS_BASE` 可覆盖为可达的直接地址，但手机会忽略旧 localhost 覆盖，HTTPS 页面不能连接不安全的 ws 地址。

历史事故 `650d34c` 对应 fetch 型 Route Handler 不能升级的旧实现，不能据此认定所有 Next 代理方式均不支持 WS。当前源码与真实握手、子协议、pong/行情连续帧分别验收；未加载新产物前，旧3000仍可能握手失败。

## `/ws/quotes`（已实现）

**连接**：`ws://127.0.0.1:8000/ws/quotes?symbols=600519,000001&subscription_id=0`（缺省订阅全部自选；旧客户端省略代次时为0）。
**注意路径无 `/api` 前缀**（router 挂载时未带 prefix）。

**服务端 → 客户端**

```json
{ "type": "snapshot", "subscription_id": 0, "seq": 42, "ts": "2026-08-28T06:30:00Z", "data": [ /* Quote[] */ ] }
{ "type": "quotes",   "subscription_id": 0, "seq": 43, "ts": "...", "data": [ /* Quote[] */ ] }
{ "type": "stale",    "subscription_id": 0, "seq": 44, "ts": "...", "data": [ /* Quote[] */ ] }
{ "type": "pong",     "ts": "..." }
```

- `seq` 是Hub全局序号，其他订阅也会消耗；跳号本身不证明本连接丢包，当前客户端不据此额外重拉。
- 三类市场帧均为对应订阅范围的完整快照，空列表是明确缺项；不能把`quotes`误作增量再永久保留缺项旧价。
- `subscription_id`在帧入队时固化。客户端更新订阅即推进代次，只让当前代次帧裁决缺项；A→A+B甚至A→B→A的旧队列包均不能冒充新订阅回执。
- 每条 Quote 自带 `source / quality / quality_reasons / data_timestamp / received_at`。
- `stale`可来自源龄、部分缺项、读取失败或已确认暂停，按各Quote的原因及日历语境呈现；帧类型不抹去原质量，也不直接决定所有证券的交易阶段。

**客户端 → 服务端**

```json
{ "action": "ping" }
{ "action": "subscribe", "symbols": ["600519", "300750"], "subscription_id": 1 }
```

`subscribe`更新本连接订阅集并立即回推同代次新快照，原出站队列保持。代次为有界非负整数，不能用布尔值或非法值改写订阅；它只关联读取范围，不是鉴权凭据。源钟、队列上限、票据与连接权限不因代次改变。

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
