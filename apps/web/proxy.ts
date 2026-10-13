import {NextResponse, type NextRequest} from "next/server";

/** Next handles an external rewrite before filesystem Route Handlers, including WS upgrades. */
export function proxy(request: NextRequest) {
  if (request.nextUrl.pathname !== "/backend/ws/quotes" || request.headers.get("upgrade")?.toLowerCase() !== "websocket") return NextResponse.next();

  // Forward the original subprotocol unchanged, including its deliberate absence
  // for backend local mode. Only the backend owns credential/posture validation.
  // Never inject the frontend's service credential into a visitor's WS.
  try {
    const target = new URL(process.env.BACKEND_ORIGIN || "http://127.0.0.1:8000");
    if (!["http:", "https:"].includes(target.protocol) || target.username || target.password || target.search || target.hash) throw new Error("invalid backend origin");
    target.pathname = `${target.pathname.replace(/\/$/, "")}/ws/quotes`;
    target.search = request.nextUrl.search;
    return NextResponse.rewrite(target);
  } catch {
    return NextResponse.json({detail: "行情推送的后端地址配置无效"}, {status: 503});
  }
}

export const config = {matcher: "/backend/ws/quotes"};
