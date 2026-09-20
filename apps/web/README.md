# apps/web — AShare AI Trader 前端

Next.js 15 (App Router) + React 19 + Tailwind CSS + lightweight-charts。

## 启动

```bash
npm install
npm run dev   # http://localhost:3000（后端需已在 8000 端口运行）
```

## 页面

| 路由 | 内容 |
|---|---|
| /workbench | 指数卡 + 自选股 + 个股详情（K线/五档/逐笔） |
| /market | 市场总览 + 市场宽度（涨跌/涨跌停家数、成交额） |
| /watchlist | 自选管理（增删） |
| /stock/[symbol] | 个股详情（K线/盘口/逐笔 标签页） |
| /limit-up | 涨停池（按连板排序，可按日期查询） |
| /longhu | 龙虎榜每日总览 |

## 结构

- `hooks/use-quote-stream.ts` — WebSocket 行情流（快照/增量/stale、心跳、断线重连、3 次失败降级 REST 轮询）
- `components/kline-chart.tsx` — lightweight-charts K 线（红涨绿跌）
- `components/panel.tsx` — 统一面板（强制展示数据来源/数据时间/质量标识）
- `lib/api.ts` — 后端 REST 客户端（`NEXT_PUBLIC_API_BASE` 默认 http://127.0.0.1:8000）
- `app/backend/[...path]/route.ts` + `lib/proxy-headers.ts` — 服务端反代，运行时从
  `ASHARE_API_TOKEN` 取凭据并附加到**全部**请求（浏览器侧不持有，见 `docs/system/api.md` §鉴权）
- `app/api/ws-credential/route.ts` + `lib/ws-credential.ts` — 同源运行时下发 WS 子协议凭据
  （浏览器不能给 WebSocket 设自定义请求头，故走 `Sec-WebSocket-Protocol`）

## 注意

- dev 运行时不要执行 `npm run build`（共用 `.next` 会互相破坏，见 docs/system/deployment.md 坑 3）。
- 所有数据展示强制携带来源与质量标识；mock 数据在页面上明确标注。
