// @vitest-environment node
import {NextRequest} from "next/server";
import {afterEach, expect, it, vi} from "vitest";
import {GET as proxy, POST as proxyPost} from "@/app/backend/[...path]/route";
import {GET as ticket} from "@/app/api/ws-credential/route";
import {POST as access} from "@/app/api/access/[action]/route";
import {accessBridge, sameOrigin} from "./device-access-server";

afterEach(() => {vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs();});
it("监听地址不是访客地址，LAN 配对与票据仍绑定真实页面来源", async () => {
  vi.stubEnv("ASHARE_API_TOKEN", "fixture-service");
  const fetcher = vi.fn(async (_url: string, _init: RequestInit) => Response.json({state:"pending"})); vi.stubGlobal("fetch",fetcher);
  const request = new NextRequest("http://0.0.0.0:3000/api/access/start", {method:"POST",headers:{host:"192.0.2.10:3000",origin:"http://192.0.2.10:3000","x-forwarded-host":"attacker.test"},body:'{"label":"phone"}'});
  expect((await accessBridge(request,"start")).status).toBe(200);
  expect((fetcher.mock.calls[0][1].headers as Headers).get("x-ashare-origin")).toBe("http://192.0.2.10:3000");
  const forged = new NextRequest("http://0.0.0.0:3000/api/access/start", {method:"POST",headers:{host:"192.0.2.10:3000",origin:"http://attacker.test","x-forwarded-host":"attacker.test"}});
  expect(sameOrigin(forged)).toBe(false);
});
it.each(["user@device.test", "device.test/path", "device.test?x", "device.test,attacker.test"])("拒绝畸形 Host %s", host => {
  expect(sameOrigin(new NextRequest("http://device.test/api/access/status",{headers:{host}}))).toBe(false);
});
function req(path: string, init: ConstructorParameters<typeof NextRequest>[1] = {}) {return new NextRequest(`http://device.test${path}`, init);}
it("未配对不回源，客户端伪造权限头不生效", async () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  expect((await proxy(req("/backend/api/quotes", {headers:{"x-ashare-session":"forged","x-api-token":"forged"}}))).status).toBe(401);
  expect(fetcher).not.toHaveBeenCalled();
});
it.each(["http://attacker.test", "null"])("跨源写入被拒绝 %s", async origin => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  expect((await proxyPost(req("/backend/api/watchlist",{method:"POST",headers:{cookie:"ashare_device=x",origin},body:"{}"}))).status).toBe(403);
  expect(fetcher).not.toHaveBeenCalled();
});
it("代理只信 httpOnly 会话，并去掉可伪造身份与 Cookie", async () => {
  vi.stubEnv("ASHARE_API_TOKEN", "fixture-service");
  const fetcher = vi.fn(async (_url: string, _init: RequestInit) => Response.json({data:[]})); vi.stubGlobal("fetch",fetcher);
  const response = await proxy(req("/backend/api/quotes",{headers:{cookie:"ashare_device=real;other=private","x-ashare-session":"forged","x-ashare-access-bridge":"1","x-forwarded-for":"127.0.0.1"}}));
  expect(response.status).toBe(200);
  const headers = fetcher.mock.calls[0][1].headers as Headers;
  expect(headers.get("x-ashare-session")).toBe("real"); expect(headers.get("x-api-token")).toBe("fixture-service");
  expect(headers.get("x-ashare-browser")).toBe("1"); expect(headers.has("cookie")).toBe(false); expect(headers.has("x-ashare-access-bridge")).toBe(false);
});
it("通用代理和公开桥都不能签发管理员初始化码", async () => {
  vi.stubGlobal("fetch", vi.fn());
  expect((await proxy(req("/backend/api/access/bootstrap-code",{headers:{cookie:"ashare_device=x"}}))).status).toBe(404);
  expect((await access(req("/api/access/bootstrap-code",{method:"POST"}),{params:Promise.resolve({action:"bootstrap-code"})})).status).toBe(404);
  expect(fetch).not.toHaveBeenCalled();
});
it("会话只写入 httpOnly strict Cookie，正文不含会话密钥", async () => {
  vi.stubEnv("ASHARE_API_TOKEN", "fixture-service");
  vi.stubGlobal("fetch",vi.fn(async () => Response.json({session_token:"opaque-session",state:"pending"})));
  const response = await accessBridge(req("/api/access/start",{method:"POST",headers:{origin:"http://device.test"},body:'{"label":"手机"}'}),"start");
  expect(await response.json()).toEqual({state:"pending"});
  const cookie=response.headers.get("set-cookie")!;
  expect(cookie).toContain("HttpOnly"); expect(cookie).toContain("SameSite=strict");
  expect(cookie).not.toContain("fixture-service"); expect(response.headers.get("cache-control")).toBe("no-store");
});
it("WS 端点只返回配对票据，未配对无长期密钥", async () => {
  vi.stubEnv("ASHARE_API_TOKEN","fixture-service");
  const fetcher=vi.fn(async (_url: string, _init: RequestInit) => Response.json({subprotocol:"ashare-ticket.once",expires_in:60})); vi.stubGlobal("fetch",fetcher);
  const denied=await ticket(req("/api/ws-credential")); expect(denied.status).toBe(401); expect(fetcher).not.toHaveBeenCalled();
  const response=await ticket(req("/api/ws-credential",{headers:{cookie:"ashare_device=x"}}));
  expect(await response.text()).not.toContain("fixture-service");
  expect(fetcher.mock.calls[0][0]).toContain("/api/access/ws-ticket");
});
it("分块超限和中断的配对正文不会回源", async () => {
  vi.stubEnv("ASHARE_API_TOKEN", "fixture-service");
  vi.stubGlobal("fetch", vi.fn());
  const large = req("/api/access/start", {method: "POST", headers: {origin: "http://device.test"}, body: "x".repeat(2049)});
  expect((await accessBridge(large, "start")).status).toBe(413);
  const broken = req("/api/access/start", {method: "POST", headers: {origin: "http://device.test"}, body: new ReadableStream({start(controller) {controller.error(new Error("disconnected"));}}), duplex: "half"});
  expect((await accessBridge(broken, "start")).status).toBe(503);
  expect(fetch).not.toHaveBeenCalled();
});
it("配对正文读取受五秒预算约束", async () => {
  vi.useFakeTimers(); vi.stubEnv("ASHARE_API_TOKEN", "fixture-service");
  vi.stubGlobal("fetch", vi.fn());
  const slow = req("/api/access/start", {method: "POST", headers: {origin: "http://device.test"}, body: new ReadableStream(), duplex: "half"});
  const result = accessBridge(slow, "start");
  await vi.advanceTimersByTimeAsync(5001);
  expect((await result).status).toBe(503);
  expect(fetch).not.toHaveBeenCalled();
});
