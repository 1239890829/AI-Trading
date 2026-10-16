/** Server-only pairing bridge. No long-lived server credential leaves Next. */
import { NextRequest, NextResponse } from "next/server";

export const DEVICE_COOKIE = "ashare_device";
export const NO_STORE = { "cache-control": "no-store" };

export function requestOrigin(req: NextRequest): string | null {
  // Next's URL can contain the listener (0.0.0.0), not the browser's LAN Host.
  // Direct LAN deployment trusts the actual Host, never X-Forwarded-Host.
  const host = req.headers.get("host") ?? req.nextUrl.host;
  if (!/^[a-z\d.:[\]-]+$/i.test(host)) return null;
  try {
    const url = new URL(`${req.nextUrl.protocol}//${host}`);
    return ["http:", "https:"].includes(url.protocol) && url.hostname ? url.origin : null;
  } catch {return null;}
}

export function sameOrigin(req: NextRequest): boolean {
  const expected = requestOrigin(req);
  if (!expected) return false;
  const origin = req.headers.get("origin");
  const site = req.headers.get("sec-fetch-site");
  if (site === "cross-site" || site === "same-site") return false;
  if (origin && origin !== expected) return false;
  return req.method === "GET" || req.method === "HEAD" || origin === expected;
}

export function accessDenied(status = 401, detail = "device_pairing_required") {
  return NextResponse.json({ detail, code: detail }, { status, headers: NO_STORE });
}

async function smallBody(req: NextRequest): Promise<string | null> {
  if (Number(req.headers.get("content-length")) > 2048) return null;
  const reader = req.body?.getReader();
  if (!reader) return "{}";
  const chunks: Uint8Array[] = [];
  let size = 0;
  let timedOut = false;
  const timer = setTimeout(() => {timedOut = true; void reader.cancel().catch(() => {});}, 5000);
  try {
    for (;;) {
      const {done, value} = await reader.read();
      if (timedOut) throw new Error("access_input_timeout");
      if (done) break;
      size += value.byteLength;
      if (size > 2048) {await reader.cancel(); return null;}
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.byteLength;}
    return new TextDecoder().decode(bytes);
  } finally {clearTimeout(timer); reader.releaseLock();}
}

export async function accessBridge(req: NextRequest, action: string, method = req.method) {
  if (!sameOrigin(req)) return accessDenied(403, "same_origin_required");
  const token = process.env.ASHARE_API_TOKEN;
  if (!token) return accessDenied(503, "device_access_requires_server_token");
  if (["devices", "manage", "ws-ticket"].includes(action) && !req.cookies.get(DEVICE_COOKIE)?.value) return accessDenied();
  const headers = new Headers({
    "content-type": "application/json", "x-api-token": token,
    "x-ashare-access-bridge": "1", "x-ashare-origin": requestOrigin(req)!,
    "x-ashare-session": req.cookies.get(DEVICE_COOKIE)?.value ?? "",
  });
  try {
    let body: string | undefined;
    if (method === "POST") {
      if (action === "ws-ticket" || action === "logout") body = "{}";
      else {
        const input = await smallBody(req);
        if (input === null) return accessDenied(413, "access_input_too_large");
        body = input;
      }
    }
    const upstream = await fetch(`${process.env.BACKEND_ORIGIN || "http://127.0.0.1:8000"}/api/access/${action}`, {
      method, headers, body, cache: "no-store", redirect: "error", signal: AbortSignal.any([req.signal, AbortSignal.timeout(5000)]),
    });
    const data = await upstream.json();
    const secret = data.session_token;
    delete data.session_token;
    const response = NextResponse.json(data, { status: upstream.status, headers: NO_STORE });
    if (upstream.ok && typeof secret === "string") response.cookies.set(DEVICE_COOKIE, secret, {
      httpOnly: true, sameSite: "strict", secure: req.nextUrl.protocol === "https:", path: "/", maxAge: 30 * 86400,
    });
    if (upstream.ok && action === "logout") response.cookies.set(DEVICE_COOKIE, "", {httpOnly: true, sameSite: "strict", path: "/", maxAge: 0});
    return response;
  } catch {
    return accessDenied(503, "device_access_unavailable");
  }
}
