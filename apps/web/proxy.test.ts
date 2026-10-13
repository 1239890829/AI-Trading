import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {NextRequest} from "next/server";
import {config, proxy} from "./proxy";

const offered = "ashare-token.Zml4dHVyZS10b2tlbg";
const request = (url = "http://frontend.local/backend/ws/quotes?symbols=600127%2C002343", headers: HeadersInit = {upgrade: "websocket", "sec-websocket-protocol": offered}) => new NextRequest(url, {headers});
beforeEach(() => {vi.stubEnv("ASHARE_API_TOKEN", "fixture-token"); vi.stubEnv("BACKEND_ORIGIN", "http://127.0.0.1:8000");});
afterEach(() => {vi.unstubAllEnvs();});

it("rewrites only the quote upgrade path and leaves all ordinary HTTP processing unchanged", () => {
  expect(config.matcher).toBe("/backend/ws/quotes");
  for (const req of [request(undefined, {}), request("http://frontend.local/backend/api/quotes"), request("http://frontend.local/backend/ws/quotes/other")]) {
    const response = proxy(req);
    expect(response.headers.get("x-middleware-next")).toBe("1");
    expect(response.headers.get("x-middleware-rewrite")).toBeNull();
  }
});

it("uses the configured backend at request time and preserves the offered credential and symbols without putting credentials into the URI", () => {
  const req = request();
  const original = req.headers.get("sec-websocket-protocol");
  const first = proxy(req);
  expect(first.headers.get("x-middleware-rewrite")).toBe("http://127.0.0.1:8000/ws/quotes?symbols=600127%2C002343");
  vi.stubEnv("BACKEND_ORIGIN", "https://backend.example/base");
  const next = proxy(req);
  expect(next.headers.get("x-middleware-rewrite")).toBe("https://backend.example/base/ws/quotes?symbols=600127%2C002343");
  expect(req.headers.get("sec-websocket-protocol")).toBe(original);
  expect(next.headers.get("x-api-token")).toBeNull();
  expect(next.headers.get("x-middleware-rewrite")).not.toContain(offered);
  expect(next.headers.get("x-middleware-rewrite")).not.toContain("fixture-token");
});

it("preserves a credential-free local handshake instead of inventing frontend auth policy", () => {
  vi.stubEnv("ASHARE_API_TOKEN", "");
  const req = request(undefined, {upgrade: "websocket"});
  const response = proxy(req);
  expect(response.headers.get("x-middleware-rewrite")).toBe("http://127.0.0.1:8000/ws/quotes?symbols=600127%2C002343");
  expect(req.headers.get("sec-websocket-protocol")).toBeNull();
  expect(response.headers.get("x-api-token")).toBeNull();
});

it("does not replace absent or unrecognized credentials with the configured service token", () => {
  const headerVariants: HeadersInit[] = [{upgrade: "websocket"}, {upgrade: "websocket", "sec-websocket-protocol": "another-protocol"}];
  for (const headers of headerVariants) {
    const req = request(undefined, headers);
    const original = req.headers.get("sec-websocket-protocol");
    const response = proxy(req);
    expect(response.headers.get("x-middleware-rewrite")).not.toBeNull();
    expect(req.headers.get("sec-websocket-protocol")).toBe(original);
    expect(response.headers.get("x-api-token")).toBeNull();
    expect(response.headers.get("x-middleware-rewrite")).not.toContain("fixture-token");
  }
});

it("never lets a visitor parameter choose an upstream host or path", () => {
  const response = proxy(request("http://frontend.local/backend/ws/quotes?symbols=600127&url=https%3A%2F%2Fother.example%2Fsecret"));
  const target = new URL(response.headers.get("x-middleware-rewrite")!);
  expect(target.origin).toBe("http://127.0.0.1:8000");
  expect(target.pathname).toBe("/ws/quotes");
  expect(target.searchParams.get("symbols")).toBe("600127");
});

const credentialedOrigin = new URL("http://backend.example");
credentialedOrigin.username = "invalid-user";
credentialedOrigin.password = "invalid-password";
it.each(["file:///tmp/private", credentialedOrigin.toString(), "not-a-url"])("fails closed for an invalid configured origin %s", origin => {
  vi.stubEnv("BACKEND_ORIGIN", origin);
  const response = proxy(request());
  expect(response.status).toBe(503);
  expect(response.headers.get("x-middleware-rewrite")).toBeNull();
});
