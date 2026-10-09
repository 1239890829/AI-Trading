import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getQuotes } from "@/lib/api";
import type { Quote } from "@/types/market";
import { useQuoteStream } from "@/hooks/use-quote-stream";

/**
 * R22（2026-09-15）：行情 WS 连接必须携带**子协议凭据**，且不破坏既有的
 * 重连 / 降级链路。
 *
 * 为什么单开一条而不是并进 `lib/ws-credential.test.ts`：那边守的是"凭据怎么取
 * （缓存 / 失败重试）"，这边守的是"取到之后**怎么用**"——`connect()` 为取凭据
 * 变成了 async，多了一个 await 点，而 await 期间组件可能已卸载。这个新引入的
 * 窗口只能靠钩子级测试钉住（`closed` 判定若漏掉，会创建一个无人回收的 socket）。
 */

const cred = vi.hoisted(() => ({ value: [] as string[], calls: 0 }));
vi.mock("@/lib/ws-credential", () => ({
  wsSubprotocols: async () => {
    cred.calls += 1;
    return cred.value;
  },
}));

vi.mock("@/lib/api", () => ({
  wsBase: () => "ws://test.local/backend",
  getQuotes: vi.fn(async () => []),
}));

class FakeWS {
  static instances: FakeWS[] = [];
  static OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readonly url: string;
  readonly protocols: string[] | undefined;

  constructor(url: string, protocols?: string[]) {
    this.url = url;
    this.protocols = protocols;
    FakeWS.instances.push(this);
  }

  close() {
    this.readyState = 3;
  }

  send(_message?: string) {
    /* noop */
  }
}

/** 让 `connect()` 里那个 `await wsSubprotocols()` 结算。 */
async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  FakeWS.instances = [];
  cred.value = [];
  cred.calls = 0;
  vi.stubGlobal("WebSocket", FakeWS);
  vi.useFakeTimers();
  vi.mocked(getQuotes).mockReset().mockResolvedValue([]);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("行情 WS 的子协议凭据（R22）", () => {
  it("配置了凭据 ⇒ 建连时带上子协议", async () => {
    cred.value = ["ashare-token.YWJj"];
    const { unmount } = renderHook(() => useQuoteStream(["600519"]));
    await settle();
    expect(FakeWS.instances).toHaveLength(1);
    expect(FakeWS.instances[0].protocols).toEqual(["ashare-token.YWJj"]);
    expect(FakeWS.instances[0].url).toContain("/ws/quotes?symbols=600519");
    unmount();
  });

  it("未配置凭据 ⇒ 建连不带第二个参数（本地 develop 零摩擦，不引入空协议数组）", async () => {
    cred.value = [];
    const { unmount } = renderHook(() => useQuoteStream(["600519"]));
    await settle();
    expect(FakeWS.instances).toHaveLength(1);
    // 关键：`new WebSocket(url, [])` 与 `new WebSocket(url)` 在浏览器里
    // **不等价**——传空数组会让服务端收到空的 Sec-WebSocket-Protocol。
    expect(FakeWS.instances[0].protocols).toBeUndefined();
    unmount();
  });

  it("断线重连后仍带凭据（凭据是连接期常量，不因重连丢失）", async () => {
    cred.value = ["ashare-token.YWJj"];
    const { unmount } = renderHook(() => useQuoteStream(["600519"]));
    await settle();
    expect(FakeWS.instances).toHaveLength(1);

    // 模拟服务端断开 → 重连（retry=1 ⇒ 退避 2s）
    await act(async () => {
      FakeWS.instances[0].onclose?.();
      await vi.advanceTimersByTimeAsync(2100);
    });
    await settle();
    expect(FakeWS.instances.length).toBeGreaterThanOrEqual(2);
    expect(FakeWS.instances.at(-1)?.protocols).toEqual(["ashare-token.YWJj"]);
    unmount();
  });

  it("取凭据期间卸载 ⇒ 不得再建 socket（await 窗口的竞态）", async () => {
    cred.value = ["ashare-token.YWJj"];
    const { unmount } = renderHook(() => useQuoteStream(["600519"]));
    // 不 settle，直接卸载：此时 connect() 正挂在 await 上
    unmount();
    await settle();
    expect(FakeWS.instances).toHaveLength(0);
  });
});

const quote = (symbol: string, price: number) => ({symbol, price, quality: "high"} as Quote);
async function degrade() {
  await settle();
  for (const delay of [2_000, 4_000]) {
    await act(async () => { FakeWS.instances.at(-1)!.onclose?.(); await vi.advanceTimersByTimeAsync(delay); });
  }
  await act(async () => { FakeWS.instances.at(-1)!.onclose?.(); });
}

it("fallback never overlaps a slow request and schedules five seconds after completion", async () => {
  let resolve!: (quotes: Quote[]) => void;
  vi.mocked(getQuotes).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const {result, unmount} = renderHook(() => useQuoteStream(["600519"]));
  await degrade();
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(getQuotes).toHaveBeenCalledTimes(1);
  await act(async () => { resolve([quote("600519", 100)]); });
  expect(result.current.quotes["600519"].price).toBe(100);
  await act(async () => { await vi.advanceTimersByTimeAsync(4_999); });
  expect(getQuotes).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(getQuotes).toHaveBeenCalledTimes(2);
  unmount();
});

it("hidden fallback pauses, discards its old response and fetches immediately on visibility return", async () => {
  let resolve!: (quotes: Quote[]) => void;
  vi.mocked(getQuotes).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const visibility = vi.spyOn(document, "visibilityState", "get");
  const {result, unmount} = renderHook(() => useQuoteStream(["600519"]));
  await degrade();
  await act(async () => { visibility.mockReturnValue("hidden"); document.dispatchEvent(new Event("visibilitychange")); resolve([quote("600519", 100)]); });
  expect(result.current.quotes).toEqual({});
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(getQuotes).toHaveBeenCalledTimes(1);
  vi.mocked(getQuotes).mockResolvedValueOnce([quote("600519", 101)]);
  await act(async () => { visibility.mockReturnValue("visible"); document.dispatchEvent(new Event("visibilitychange")); });
  expect(getQuotes).toHaveBeenCalledTimes(2);
  expect(result.current.quotes["600519"].price).toBe(101);
  unmount();
});

it("switching symbols discards the old fallback reply and then requests the current symbols", async () => {
  let resolve!: (quotes: Quote[]) => void;
  vi.mocked(getQuotes).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const {result, rerender, unmount} = renderHook(({symbols}) => useQuoteStream(symbols), {initialProps: {symbols: ["600519"]}});
  await degrade();
  rerender({symbols: ["000001"]});
  vi.mocked(getQuotes).mockResolvedValueOnce([quote("000001", 20)]);
  await act(async () => { resolve([quote("600519", 100)]); });
  expect(getQuotes).toHaveBeenLastCalledWith(["000001"]);
  expect(result.current.quotes["600519"]).toBeUndefined();
  expect(result.current.quotes["000001"].price).toBe(20);
  unmount();
});

it("WS recovery and unmount prevent stale fallback replies from overwriting current quotes", async () => {
  let resolve!: (quotes: Quote[]) => void;
  vi.mocked(getQuotes).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const {result, unmount} = renderHook(() => useQuoteStream(["600519"]));
  await degrade();
  await act(async () => { await vi.advanceTimersByTimeAsync(8_000); });
  const ws = FakeWS.instances.at(-1)!;
  await act(async () => {
    ws.readyState = FakeWS.OPEN;
    ws.onopen?.();
    ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("600519", 110)]})});
    resolve([quote("600519", 100)]);
  });
  expect(result.current.quotes["600519"].price).toBe(110);
  expect(result.current.status).toBe("live");
  unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(getQuotes).toHaveBeenCalledTimes(1);
});

it("live symbol subscriptions stay on one socket and an old-symbol snapshot cannot replace the new result", async () => {
  const {result, rerender, unmount} = renderHook(({symbols}) => useQuoteStream(symbols), {initialProps: {symbols: ["600519"]}});
  await settle();
  const ws = FakeWS.instances[0];
  const send = vi.spyOn(ws, "send");
  await act(async () => { ws.readyState = FakeWS.OPEN; ws.onopen?.(); });
  rerender({symbols: ["000001"]});
  expect(FakeWS.instances).toHaveLength(1);
  expect(send).toHaveBeenLastCalledWith(JSON.stringify({action: "subscribe", symbols: ["000001"]}));
  await act(async () => {
    ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("000001", 20)]})});
    ws.onmessage?.({data: JSON.stringify({type: "stale", data: [quote("600519", 100)]})});
  });
  expect(result.current.quotes).toEqual({"000001": quote("000001", 20)});
  expect(result.current.status).toBe("live");
  unmount();
});

it("late messages from a replaced socket cannot overwrite a recovered connection", async () => {
  const {result, unmount} = renderHook(() => useQuoteStream(["600519"]));
  await settle();
  const old = FakeWS.instances[0];
  await act(async () => { old.onclose?.(); await vi.advanceTimersByTimeAsync(2_000); });
  const current = FakeWS.instances.at(-1)!;
  await act(async () => {
    current.readyState = FakeWS.OPEN; current.onopen?.();
    current.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("600519", 110)]})});
    old.onmessage?.({data: JSON.stringify({type: "stale", data: [quote("600519", 90)]})});
  });
  expect(result.current.quotes["600519"].price).toBe(110);
  expect(result.current.status).toBe("live");
  unmount();
});
