import type { NextRequest } from "next/server";

/**
 * 后端反向代理：**运行时**读取 BACKEND_ORIGIN。
 *
 * 为什么不用 `next.config.ts` 的 `rewrites()`：
 * rewrites 在**构建期**求值并烘进产物，`next start` 不会重新读取环境变量。
 * 实测——以 `BACKEND_ORIGIN=http://127.0.0.1:8999` 启动的生产服务器仍然打到
 * 构建期默认的 8000 端口。那意味着换后端主机必须重新构建，部署场景不可接受。
 *
 * Route Handler 每次请求都在服务端求值，改环境变量重启即生效。
 * 唯一代价：Next 的 rewrite 不代理 WebSocket 升级，WS 需要显式配
 * NEXT_PUBLIC_WS_BASE 或用前置反代（连接失败时 useQuoteStream 自动降级轮询）。
 */
const DEFAULT_BACKEND = "http://127.0.0.1:8000";

async function proxy(req: NextRequest) {
  const backend = process.env.BACKEND_ORIGIN || DEFAULT_BACKEND;
  const path = req.nextUrl.pathname.replace(/^\/backend/, "");
  const target = `${backend}${path}${req.nextUrl.search}`;

  const headers = new Headers(req.headers);
  // host 必须删掉，否则后端按 Next 自己的 Host 处理（CORS 校验会错）
  headers.delete("host");
  // 长度由 fetch 依据实际 body 重算，透传旧值会不一致
  headers.delete("content-length");
  // 客户端送来的写鉴权头一律丢弃，只认服务端环境变量（防伪造/防误配）
  headers.delete("x-api-token");

  // 写接口鉴权（B6）：token 只在**服务端**持有并注入，绝不下发浏览器。
  //
  // 历史缺陷（2026-09-14 修复）：`lib/api.ts` 曾用 `NEXT_PUBLIC_API_TOKEN` 携带该头，
  // 而 `NEXT_PUBLIC_*` 由 Next **构建期内联**成客户端 bundle 里的字面量 ⇒ 任何访客
  // 「查看网页源码」即可取得唯一写保护凭据；该头还会进浏览器历史与反代访问日志。
  // 归位后浏览器只发同源 `/backend/...`，token 由本层附加。
  //
  // 未配置 = 不带头，与后端 `require_write_token` 的 opt-in 语义对称（本地零影响）。
  if (req.method !== "GET" && req.method !== "HEAD") {
    const token = process.env.ASHARE_API_TOKEN;
    if (token) headers.set("x-api-token", token);
  }

  const init: RequestInit & { duplex?: string } = {
    method: req.method,
    headers,
    redirect: "manual",
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch (e) {
    return Response.json(
      { detail: `后端不可达（${backend}）：${(e as Error).message}`, code: "backend_unreachable" },
      { status: 502 },
    );
  }

  const out = new Headers(upstream.headers);
  // 长度/编码交给响应框架处理，透传会与实际 body 不符
  out.delete("content-encoding");
  out.delete("content-length");
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

// 每次请求都必须重新求值，不能缓存
export const dynamic = "force-dynamic";

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
