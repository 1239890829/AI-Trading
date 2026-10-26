import { act, renderHook } from "@testing-library/react";
import { useCallback, useSyncExternalStore } from "react";
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
  cred.value = ["ashare-ticket.fixture"];
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
    cred.value = ["ashare-ticket.YWJj"];
    const { unmount } = renderHook(() => useQuoteStream(["600519"]));
    await settle();
    expect(FakeWS.instances).toHaveLength(1);
    expect(FakeWS.instances[0].protocols).toEqual(["ashare-ticket.YWJj"]);
    expect(FakeWS.instances[0].url).toContain("/ws/quotes?symbols=600519");
    unmount();
  });

  it("未获得短效凭据时不建立无授权连接，并在退避后重新申请", async () => {
    cred.value = [];
    const {unmount} = renderHook(() => useQuoteStream(["600519"]));
    await settle();
    expect(FakeWS.instances).toHaveLength(0);
    cred.value = ["ashare-ticket.recovered"];
    await act(async () => {await vi.advanceTimersByTimeAsync(2100);});
    expect(FakeWS.instances).toHaveLength(1);
    expect(FakeWS.instances[0].protocols).toEqual(["ashare-ticket.recovered"]);
    unmount();
  });

  it("断线重连后仍带凭据（凭据是连接期常量，不因重连丢失）", async () => {
    cred.value = ["ashare-ticket.YWJj"];
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
    expect(FakeWS.instances.at(-1)?.protocols).toEqual(["ashare-ticket.YWJj"]);
    unmount();
  });

  it("取凭据期间卸载 ⇒ 不得再建 socket（await 窗口的竞态）", async () => {
    cred.value = ["ashare-ticket.YWJj"];
    const { unmount } = renderHook(() => useQuoteStream(["600519"]));
    // 不 settle，直接卸载：此时 connect() 正挂在 await 上
    unmount();
    await settle();
    expect(FakeWS.instances).toHaveLength(0);
  });
});

const quote = (symbol: string, price: number) => ({symbol, price, quality: "high"} as Quote);
type TestFeed = {
  subscribe(symbol: string, listener: () => void): () => void;
  getSnapshot(symbol: string): Quote | null | undefined;
};
function feedOf(stream: ReturnType<typeof useQuoteStream>): TestFeed | undefined {
  return (stream as ReturnType<typeof useQuoteStream> & {quoteFeed?: TestFeed}).quoteFeed;
}
function useFastQuote(feed: TestFeed | undefined, symbol: string, fallback?: Quote) {
  const subscribe = useCallback((listener: () => void) => feed?.subscribe(symbol, listener) ?? (() => {}), [feed, symbol]);
  const snapshot = useCallback(() => feed ? feed.getSnapshot(symbol) : fallback, [feed, symbol, fallback]);
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

describe("connection recovery contracts", () => {
  it("a handshake without a market frame is not a ready quote connection", async () => {
    const owner = renderHook(() => useQuoteStream(["600519"]));
    await settle(); const ws = FakeWS.instances[0];
    await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.();});
    expect(owner.result.current.status).toBe("connecting");
    expect(owner.result.current.quoteFeed.getSnapshot("600519")).toBeUndefined();
    expect(getQuotes).not.toHaveBeenCalled();
    owner.unmount();
  });

  it("mixed symbol failures and closed phases preserve raw quality without changing the connection", async () => {
    const owner = renderHook(() => useQuoteStream(["600519", "600127"]));
    await settle(); const ws = FakeWS.instances[0];
    const current = {...quote("600519", 100), source: "tencent", data_timestamp: "2026-10-14T05:33:00Z"};
    const paused = {...quote("600127", 8), quality: "stale", quality_reasons: ["market_closed", "refresh_failed"], source: "ths", data_timestamp: "2026-10-14T03:30:00Z"};
    await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.(); ws.onmessage?.({data: JSON.stringify({type: "stale", data: [current, paused]})});});
    expect(owner.result.current.status).toBe("live");
    expect(owner.result.current.quoteFeed.getSnapshot("600519")).toEqual(current);
    expect(owner.result.current.quoteFeed.getSnapshot("600127")).toEqual(paused);
    const failed = {...current, quality: "stale", quality_reasons: ["refresh_failed"]};
    await act(async () => {ws.onmessage?.({data: JSON.stringify({type: "snapshot", data: [failed]})});});
    expect(owner.result.current.status).toBe("live");
    expect(owner.result.current.quoteFeed.getSnapshot("600519")).toEqual(failed);
    expect(owner.result.current.quoteFeed.getSnapshot("600127")).toBeNull();
    owner.unmount();
  });

  it("REST success after a failed fallback read recovers its transport state and preserves source facts", async () => {
    const recovered = {...quote("600519", 101), quality: "stale", quality_reasons: ["quote_age_exceeded"], source: "tencent", data_timestamp: "2026-10-14T05:33:00Z"} as Quote;
    vi.mocked(getQuotes).mockRejectedValueOnce(new Error("synthetic read failure")).mockResolvedValueOnce([recovered]);
    const owner = renderHook(() => useQuoteStream(["600519"]));
    await degrade();
    expect(owner.result.current.status).toBe("error");
    await act(async () => {await vi.advanceTimersByTimeAsync(5000);});
    expect(owner.result.current.status).toBe("polling");
    expect(owner.result.current.quoteFeed.getSnapshot("600519")).toEqual(recovered);
    expect(getQuotes).toHaveBeenCalledTimes(2);
    owner.unmount();
  });

  it("opening and closing without a quote frame cannot reset the fallback failure count", async () => {
    const owner = renderHook(() => useQuoteStream(["600519"]));
    await settle();
    for (const delay of [2000, 4000, 0]) {
      const ws = FakeWS.instances.at(-1)!;
      await act(async () => {
        ws.readyState = FakeWS.OPEN; ws.onopen?.(); ws.onclose?.();
        if (delay) await vi.advanceTimersByTimeAsync(delay);
      });
    }
    expect(owner.result.current.status).toBe("polling");
    expect(getQuotes).toHaveBeenCalledTimes(1);
    expect(FakeWS.instances).toHaveLength(3);
    owner.unmount();
  });

  it("pong traffic without market frames does not renew the existing quote watchdog", async () => {
    const owner = renderHook(() => useQuoteStream(["600519"]));
    await settle(); const ws = FakeWS.instances[0];
    await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.();});
    for (let i = 0; i < 3; i += 1) {
      await act(async () => {ws.onmessage?.({data: JSON.stringify({type: "pong"})}); await vi.advanceTimersByTimeAsync(15_000);});
    }
    expect(ws.readyState).toBe(3);
    expect(owner.result.current.quoteFeed.getSnapshot("600519")).toBeUndefined();
    expect(getQuotes).not.toHaveBeenCalled();
    owner.unmount();
  });

  it("a valid full empty snapshot proves transport delivery but keeps each subscribed observation missing", async () => {
    const owner = renderHook(() => useQuoteStream(["600519"]));
    await settle(); const ws = FakeWS.instances[0];
    await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.(); ws.onmessage?.({data: JSON.stringify({type: "stale", data: []})});});
    expect(owner.result.current.status).toBe("live");
    expect(owner.result.current.quoteFeed.getSnapshot("600519")).toBeNull();
    expect(owner.result.current.quotes).toEqual({});
    owner.unmount();
  });
});

it("one owner delivers complete selected quotes immediately while its list stays throttled", async () => {
  const owner = renderHook(() => useQuoteStream(["600519", "000001"], {throttleMs: 3000}));
  await settle();
  const ws = FakeWS.instances[0];
  const first = {...quote("600519", 100), source: "tencent", data_timestamp: "2026-10-12T05:43:22Z", received_at: "2026-10-12T05:43:24Z", volume: 100, amount: 10_000};
  await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.(); ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [first]})});});
  const feed = feedOf(owner.result.current);
  const fast = renderHook(() => useFastQuote(feed, "600519", owner.result.current.quotes["600519"]));
  const slow = owner.result.current.quotes;
  const second = {...first, price: 101, volume: 200, amount: 20_100, data_timestamp: "2026-10-12T05:43:23Z", received_at: "2026-10-12T05:43:25Z", quality: "stale" as const, quality_reasons: ["refresh_failed"]};
  await act(async () => {await vi.advanceTimersByTimeAsync(1000); ws.onmessage?.({data: JSON.stringify({type: "stale", data: [second]})});});
  expect(fast.result.current).toEqual(second);
  expect(owner.result.current.quotes).toBe(slow);
  expect(owner.result.current.quotes["600519"].price).toBe(100);
  expect(owner.result.current.status).toBe("live");
  expect(FakeWS.instances).toHaveLength(1);
  await act(async () => {await vi.advanceTimersByTimeAsync(2000);});
  expect(owner.result.current.quotes["600519"]).toEqual(second);
  fast.unmount(); owner.unmount();
});

it("hidden WS retains its latest complete frame without React/feed delivery and resumes once", async () => {
  const visibility = vi.spyOn(document, "visibilityState", "get");
  const owner = renderHook(() => useQuoteStream(["600519"], {throttleMs: 3000}));
  await settle();
  const ws = FakeWS.instances[0];
  const first = {...quote("600519", 100), source: "tencent", volume: 100, amount: 10_000, data_timestamp: "2026-10-12T01:00:00Z", received_at: "2026-10-12T01:00:01Z"};
  await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.(); ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [first]})});});
  const before = owner.result.current.quotes;
  const listener = vi.fn();
  const unsubscribe = owner.result.current.quoteFeed.subscribe("600519", listener);
  await act(async () => {visibility.mockReturnValue("hidden"); document.dispatchEvent(new Event("visibilitychange"));});
  const latest = {...first, volume: 300, amount: 30_300, quality: "stale" as const, quality_reasons: ["refresh_failed"], data_timestamp: "2026-10-12T01:00:02Z", received_at: "2026-10-12T01:00:03Z"};
  await act(async () => {
    ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [{...first, price: 101}]})});
    ws.onmessage?.({data: JSON.stringify({type: "stale", data: [latest]})});
    await vi.advanceTimersByTimeAsync(4000);
  });
  expect(listener).not.toHaveBeenCalled();
  expect(owner.result.current.quotes).toBe(before);
  expect(owner.result.current.quoteFeed.getSnapshot("600519")).toEqual(first);
  expect(owner.result.current.status).toBe("live");
  await act(async () => {visibility.mockReturnValue("visible"); document.dispatchEvent(new Event("visibilitychange"));});
  expect(listener).toHaveBeenCalledTimes(1);
  expect(owner.result.current.quotes["600519"]).toEqual(latest);
  expect(owner.result.current.quoteFeed.getSnapshot("600519")).toEqual(latest);
  expect(owner.result.current.status).toBe("live");
  expect(getQuotes).not.toHaveBeenCalled();
  expect(FakeWS.instances).toHaveLength(1);
  unsubscribe(); owner.unmount();
});

it.each([
  {oldReason: "refresh_failed", newReason: null, expected: "live"},
  {oldReason: "market_closed", newReason: null, expected: "live"},
  {oldReason: "refresh_failed", newReason: "refresh_failed", expected: "live"},
  {oldReason: "market_closed", newReason: "market_closed", expected: "live"},
])("hidden reconnect restores $expected from its own snapshot after old $oldReason ($newReason)", async ({oldReason, newReason, expected}) => {
  const visibility = vi.spyOn(document, "visibilityState", "get");
  const owner = renderHook(() => useQuoteStream(["600519"], {throttleMs: 3000}));
  await settle();
  const oldSocket = FakeWS.instances[0];
  const first = quote("600519", 100);
  await act(async () => {
    oldSocket.readyState = FakeWS.OPEN; oldSocket.onopen?.();
    oldSocket.onmessage?.({data: JSON.stringify({type: "quotes", data: [first]})});
  });
  const before = owner.result.current.quotes;
  const listener = vi.fn();
  const unsubscribe = owner.result.current.quoteFeed.subscribe("600519", listener);
  await act(async () => {visibility.mockReturnValue("hidden"); document.dispatchEvent(new Event("visibilitychange"));});
  await act(async () => {
    oldSocket.onmessage?.({data: JSON.stringify({type: "stale", data: [{...first, quality: "stale", quality_reasons: [oldReason]}]})});
    oldSocket.close();
    oldSocket.onclose?.();
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(FakeWS.instances).toHaveLength(2);
  const nextSocket = FakeWS.instances[1];
  const latest = {...quote("600519", 102), quality: newReason ? "stale" : "high", quality_reasons: newReason ? [newReason] : [], volume: 300, amount: 30_600, data_timestamp: "2026-10-12T02:00:02Z"};
  await act(async () => {
    nextSocket.readyState = FakeWS.OPEN; nextSocket.onopen?.();
    nextSocket.onmessage?.({data: JSON.stringify({type: "snapshot", data: [latest]})});
  });
  expect(owner.result.current.quotes).toBe(before);
  expect(listener).not.toHaveBeenCalled();
  await act(async () => {visibility.mockReturnValue("visible"); document.dispatchEvent(new Event("visibilitychange"));});
  expect(owner.result.current.status).toBe(expected);
  expect(owner.result.current.quotes["600519"]).toEqual(latest);
  expect(owner.result.current.quoteFeed.getSnapshot("600519")).toEqual(latest);
  expect(listener).toHaveBeenCalledTimes(1);
  expect(getQuotes).not.toHaveBeenCalled();
  unsubscribe(); owner.unmount();
});

it("a hidden trailing frame and explicit missing observation reconcile before the next WS tick", async () => {
  const visibility = vi.spyOn(document, "visibilityState", "get");
  const owner = renderHook(() => useQuoteStream(["600519"], {throttleMs: 3000}));
  await settle(); const ws = FakeWS.instances[0];
  await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.(); ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("600519", 100)]})});});
  await act(async () => {await vi.advanceTimersByTimeAsync(1000); ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("600519", 101)]})});});
  await act(async () => {visibility.mockReturnValue("hidden"); document.dispatchEvent(new Event("visibilitychange")); await vi.advanceTimersByTimeAsync(4000);});
  expect(owner.result.current.quotes["600519"].price).toBe(100);
  await act(async () => {ws.onmessage?.({data: JSON.stringify({type: "quotes", data: []})});});
  await act(async () => {visibility.mockReturnValue("visible"); document.dispatchEvent(new Event("visibilitychange"));});
  expect(owner.result.current.quotes).toEqual({});
  expect(owner.result.current.quoteFeed.getSnapshot("600519")).toBeNull();
  owner.unmount();
});

it("the authoritative full subscription frame preserves A when only B changed between hidden frames", async () => {
  const visibility = vi.spyOn(document, "visibilityState", "get");
  const owner = renderHook(() => useQuoteStream(["600519", "600127"]));
  await settle(); const ws = FakeWS.instances[0];
  await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.();});
  await act(async () => {visibility.mockReturnValue("hidden"); document.dispatchEvent(new Event("visibilitychange"));});
  await act(async () => {
    ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("600519", 101), quote("600127", 8)]})});
    ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("600519", 101), quote("600127", 8.1)]})});
  });
  expect(owner.result.current.quotes).toEqual({});
  await act(async () => {visibility.mockReturnValue("visible"); document.dispatchEvent(new Event("visibilitychange"));});
  expect(owner.result.current.quoteFeed.getSnapshot("600519")).toEqual(quote("600519", 101));
  expect(owner.result.current.quoteFeed.getSnapshot("600127")).toEqual(quote("600127", 8.1));
  expect(Object.keys(owner.result.current.quotes)).toHaveLength(2);
  owner.unmount();
});

it("the immediate feed prunes removed subscriptions, rejects old frames and clears on stop", async () => {
  const owner = renderHook(({symbols}) => useQuoteStream(symbols, {throttleMs: 3000}), {initialProps: {symbols: ["600519"]}});
  await settle();
  const ws = FakeWS.instances[0];
  await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.(); ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("600519", 100)]})});});
  const feed = feedOf(owner.result.current);
  owner.rerender({symbols: ["000001"]});
  expect(feed?.getSnapshot("600519")).toBeUndefined();
  const fast = renderHook(() => useFastQuote(feed, "000001"));
  await act(async () => {
    ws.onmessage?.({data: JSON.stringify({type: "quotes", subscription_id: 1, data: [quote("000001", 20)]})});
    ws.onmessage?.({data: JSON.stringify({type: "stale", data: [quote("600519", 99)]})});
  });
  expect(fast.result.current).toEqual(quote("000001", 20));
  expect(feed?.getSnapshot("600519")).toBeUndefined();
  expect(FakeWS.instances).toHaveLength(1);
  owner.rerender({symbols: []});
  expect(fast.result.current).toBeUndefined();
  await act(async () => {ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("000001", 21)]})});});
  expect(feed?.getSnapshot("000001")).toBeUndefined();
  fast.unmount(); owner.unmount();
});

it("REST fallback publishes through the same immediate feed before a pending list interval", async () => {
  // A longer list interval exposes this ingress independently of the 5s poll cadence.
  const owner = renderHook(() => useQuoteStream(["600519"], {throttleMs: 10_000}));
  await settle();
  const ws = FakeWS.instances[0];
  await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.(); ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("600519", 100)]})});});
  const fast = renderHook(() => useFastQuote(feedOf(owner.result.current), "600519"));
  const fallback = {...quote("600519", 101), quality: "stale" as const, quality_reasons: ["quote_age_exceeded"], source: "tencent", volume: 1000, amount: 101_000, data_timestamp: "2026-10-12T05:43:22Z", received_at: "2026-10-12T05:43:24Z"};
  vi.mocked(getQuotes).mockResolvedValueOnce([fallback]);
  await degrade();
  expect(fast.result.current).toEqual(fallback);
  expect(owner.result.current.quotes["600519"].price).toBe(100);
  expect(getQuotes).toHaveBeenCalledTimes(1);
  expect(owner.result.current.status).toBe("polling");
  fast.unmount(); owner.unmount();
});

it("a foreign REST response cannot blank the selected immediate quote and a later valid reply recovers", async () => {
  const owner = renderHook(() => useQuoteStream(["600519"], {throttleMs: 3000}));
  await settle(); const ws = FakeWS.instances[0];
  await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.(); ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("600519", 100)]})});});
  const fast = renderHook(() => useFastQuote(feedOf(owner.result.current), "600519"));
  vi.mocked(getQuotes).mockResolvedValueOnce([quote("000001", 1)]).mockResolvedValueOnce([quote("600519", 101)]);
  await degrade();
  expect(fast.result.current).toEqual(quote("600519", 100));
  expect(feedOf(owner.result.current)?.getSnapshot("000001")).toBeUndefined();
  await act(async () => {await vi.advanceTimersByTimeAsync(5000);});
  expect(fast.result.current).toEqual(quote("600519", 101));
  fast.unmount(); owner.unmount();
});

it("an explicit missing frame is distinct from no observation and unmount cancels pending delivery", async () => {
  const owner = renderHook(() => useQuoteStream(["600519"], {throttleMs: 3000}));
  await settle(); const feed = feedOf(owner.result.current);
  expect(feed?.getSnapshot("600519")).toBeUndefined();
  const fast = renderHook(() => useFastQuote(feed, "600519"));
  const ws = FakeWS.instances[0];
  await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.(); ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("600519", 100)]})});});
  await act(async () => {await vi.advanceTimersByTimeAsync(1000); ws.onmessage?.({data: JSON.stringify({type: "quotes", data: []})});});
  expect(fast.result.current).toBeNull();
  expect(owner.result.current.quotes["600519"].price).toBe(100);
  await act(async () => {await vi.advanceTimersByTimeAsync(2000);});
  expect(owner.result.current.observations).toEqual({"600519": null});
  expect(owner.result.current.quotes).toEqual({});
  owner.unmount();
  expect(fast.result.current).toBeUndefined();
  await act(async () => {await vi.advanceTimersByTimeAsync(3000); ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("600519", 200)]})});});
  expect(fast.result.current).toBeUndefined(); fast.unmount();
});
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
  expect(result.current.quoteFeed.getSnapshot("600519")).toBeUndefined();
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(getQuotes).toHaveBeenCalledTimes(1);
  vi.mocked(getQuotes).mockResolvedValueOnce([quote("600519", 101)]);
  await act(async () => { visibility.mockReturnValue("visible"); document.dispatchEvent(new Event("visibilitychange")); });
  expect(getQuotes).toHaveBeenCalledTimes(2);
  expect(result.current.quotes["600519"].price).toBe(101);
  expect(result.current.quoteFeed.getSnapshot("600519")?.price).toBe(101);
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
  expect(result.current.quoteFeed.getSnapshot("600519")).toBeUndefined();
  expect(result.current.quoteFeed.getSnapshot("000001")?.price).toBe(20);
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
  expect(result.current.quoteFeed.getSnapshot("600519")?.price).toBe(110);
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
  expect(send).toHaveBeenLastCalledWith(JSON.stringify({action: "subscribe", symbols: ["000001"], subscription_id: 1}));
  await act(async () => {
    ws.onmessage?.({data: JSON.stringify({type: "quotes", subscription_id: 1, data: [quote("000001", 20)]})});
    ws.onmessage?.({data: JSON.stringify({type: "stale", data: [quote("600519", 100)]})});
  });
  expect(result.current.quotes).toEqual({"000001": quote("000001", 20)});
  expect(result.current.status).toBe("live");
  unmount();
});

it("订阅代次拒绝同socket旧scope的重叠帧及A→B→A旧帧，当前代次真实空帧仍明确缺项", async () => {
  const owner = renderHook(({symbols}) => useQuoteStream(symbols), {initialProps: {symbols: ["600519"]}});
  await settle(); const ws = FakeWS.instances[0];
  const emit = (subscription_id: number, data: Quote[]) => ws.onmessage?.({data: JSON.stringify({type: "snapshot", subscription_id, data})});
  await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.(); emit(0, [quote("600519", 100)]);});
  owner.rerender({symbols: ["600519", "600127"]});
  await act(async () => {emit(0, [quote("600519", 99)]);});
  expect(owner.result.current.quoteFeed.getSnapshot("600127")).toBeUndefined();
  expect(owner.result.current.quoteFeed.getSnapshot("600519")?.price).toBe(100);
  await act(async () => {emit(1, [quote("600519", 101), quote("600127", 8)]);});
  expect(owner.result.current.quotes["600127"].price).toBe(8);
  owner.rerender({symbols: ["600127"]});
  owner.rerender({symbols: ["600519", "600127"]});
  await act(async () => {emit(1, [quote("600519", 90), quote("600127", 7)]);});
  expect(owner.result.current.quoteFeed.getSnapshot("600519")).toBeUndefined();
  expect(owner.result.current.quoteFeed.getSnapshot("600127")?.price).toBe(8);
  await act(async () => {emit(3, []);});
  expect(owner.result.current.quoteFeed.getSnapshot("600519")).toBeNull();
  expect(owner.result.current.observations).toEqual({"600127": null, "600519": null});
  expect(FakeWS.instances).toHaveLength(1);
  owner.unmount();
});

it("legacy无id帧只属于初始URL订阅；切scope后不续行情watchdog，重连仍能兼容旧服", async () => {
  const owner = renderHook(({symbols}) => useQuoteStream(symbols), {initialProps: {symbols: ["600519"]}});
  await settle(); const ws = FakeWS.instances[0];
  await act(async () => {ws.readyState = FakeWS.OPEN; ws.onopen?.(); ws.onmessage?.({data: JSON.stringify({type: "snapshot", data: [quote("600519", 100)]})});});
  owner.rerender({symbols: ["600127"]});
  for (let count = 0; count < 3; count++) await act(async () => {
    await vi.advanceTimersByTimeAsync(15_000);
    ws.onmessage?.({data: JSON.stringify({type: "quotes", data: [quote("600127", 8)]})});
  });
  expect(owner.result.current.quoteFeed.getSnapshot("600127")).toBeUndefined();
  expect(ws.readyState).toBe(3);
  await act(async () => {ws.onclose?.(); await vi.advanceTimersByTimeAsync(2000);});
  const next = FakeWS.instances.at(-1)!;
  await act(async () => {next.readyState = FakeWS.OPEN; next.onopen?.(); next.onmessage?.({data: JSON.stringify({type: "snapshot", data: []})});});
  expect(owner.result.current.quoteFeed.getSnapshot("600127")).toBeNull();
  owner.unmount();
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
