// @vitest-environment node
import {NextRequest} from "next/server";
import {afterEach, expect, it, vi} from "vitest";
import {GET, POST} from "@/app/backend/[...path]/route";

afterEach(() => {vi.unstubAllGlobals(); vi.useRealTimers();});

it("浏览器中止传到上游，取消不误报为数据源不可达", async () => {
  const controller = new AbortController();
  const request = new NextRequest("http://test.local/backend/api/quotes?symbols=600127", {headers: {cookie: "ashare_device=fixture"}, signal: controller.signal});
  let upstreamSignal: AbortSignal | undefined;
  vi.stubGlobal("fetch", vi.fn((_target: string, init: RequestInit) => new Promise((_resolve, reject) => {
    upstreamSignal = init.signal ?? undefined;
    upstreamSignal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), {once: true});
  })));
  const result = GET(request);
  expect(upstreamSignal).toBe(request.signal);
  controller.abort();
  const response = await result;
  expect(upstreamSignal?.aborted).toBe(true);
  expect(response.status).toBe(499);
  expect(await response.json()).toMatchObject({code: "request_cancelled"});
});

it("SSE 保持独立长连接预算，超过 REST 8s 后仍透传正文与取消信号", async () => {
  vi.useFakeTimers();
  const controller = new AbortController();
  const request = new NextRequest("http://test.local/backend/api/assistant/chat", {method: "POST", body: "{}", headers: {cookie: "ashare_device=fixture", origin: "http://test.local"}, signal: controller.signal});
  let stream!: ReadableStreamDefaultController<Uint8Array>;
  let upstreamSignal: AbortSignal | undefined;
  vi.stubGlobal("fetch", vi.fn(async (_target: string, init: RequestInit) => {
    upstreamSignal = init.signal ?? undefined;
    return new Response(new ReadableStream<Uint8Array>({start(owner) {stream = owner;}}), {headers: {"content-type": "text/event-stream", "content-length": "999"}});
  }));
  const response = await POST(request);
  expect(response.headers.get("content-type")).toBe("text/event-stream");
  expect(response.headers.has("content-length")).toBe(false);
  await vi.advanceTimersByTimeAsync(30_000);
  expect(upstreamSignal).toBe(request.signal);
  expect(upstreamSignal?.aborted).toBe(false);
  const reader = response.body!.getReader();
  stream.enqueue(new TextEncoder().encode("data: complete\n\n"));
  expect(new TextDecoder().decode((await reader.read()).value)).toBe("data: complete\n\n");
  controller.abort();
  expect(upstreamSignal?.aborted).toBe(true);
  stream.close();
  await reader.cancel();
});
