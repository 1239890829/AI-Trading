import { NextResponse } from "next/server";

/**
 * WebSocket 子协议凭据（R22，2026-09-15）。
 *
 * ## 为什么需要这个端点（以及它为什么是安全的）
 *
 * HTTP 面浏览器**从不持有** token：`app/backend/[...path]/route.ts` 在服务端注入。
 * WS 面做不到——浏览器的 `WebSocket` 构造器**不允许设置自定义请求头**，唯一可用的
 * 通道是 URL 查询串（已被本项目刻意关闭：会进浏览器历史 / Referer / 反代日志）
 * 或**子协议**（它是请求头，不进 URL）。
 *
 * 且子协议还有个硬约束：客户端提议了子协议、而服务端**一个都没选**时，浏览器会
 * 主动判定连接失败（RFC 6455 §4.1 / WHATWG）。⇒ **浏览器必须自己知道凭据**，
 * 没法像 HTTP 那样由代理单方面附加。故本端点就是那个"服务端到浏览器"的投递口。
 *
 * 三件事让它的暴露面不比 HTTP 面更大：
 * 1. **不进构建产物**——本文件在服务端运行时读 `process.env`，与 `NEXT_PUBLIC_*`
 *    的构建期内联完全不同（后者是历史缺陷，见 `lib/env-secrecy.test.ts`）。
 * 2. **不进日志/历史**——返回值只在内存里被传给 `new WebSocket(url, [...])`，
 *    不进 URL、不落 localStorage / cookie。响应显式 `no-store`。
 * 3. **不在约定里冒充明文**——返回的是 `base64url` 编码后的子协议（RFC 6455 要求
 *    子协议值必须是 HTTP token 字符集，base64 的 `=` 会让浏览器在构造器处抛异常）。
 *
 * ## 残留风险（必须知道，别当成"已解决"）
 *
 * 能打开前端的访客可以取得该凭据，进而**绕过前端直连后端端口**。所以它保护的是
 * "后端端口不对未授权者开放"，**不是"区分前端访客的身份"**——真正区分用户需要
 * 会话/身份层，那是 R22 明确"不要求现在做"的部分。配套纪律：后端端口只绑回环
 * （`docker-compose.prod.yml` 已如此）。
 *
 * ## 未配置 token 时
 *
 * 返回 `{subprotocol: null}` ⇒ 客户端不带子协议连接 ⇒ 与后端 `local` 姿态对称
 * （本地开发零摩擦，行为与加固前逐字一致）。
 */
export const dynamic = "force-dynamic";

export async function GET() {
  const token = process.env.ASHARE_API_TOKEN;
  return NextResponse.json(
    { subprotocol: token ? `ashare-token.${Buffer.from(token, "utf8").toString("base64url")}` : null },
    { headers: { "cache-control": "no-store" } },
  );
}
