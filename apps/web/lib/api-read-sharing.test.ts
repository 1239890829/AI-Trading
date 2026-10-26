import { afterEach, describe, expect, it, vi } from "vitest";
import { getJson, request, sendJson } from "@/lib/api/internal";
import { getWatchlist } from "@/lib/api/watchlist";
import { getLimitUpPoolSnapshot, getMarketOverview, getMinuteLine, getMinuteLineWithBaseline } from "@/lib/api/market";

function pendingFetch(obeyAbort = true) {
  const calls: { url: string; init: RequestInit; finish: (data: unknown, status?: number) => void; fail: () => void }[] = [];
  const fetcher = vi.fn((url: string, init: RequestInit) => new Promise<Response>((resolve, reject) => {
    calls.push({url, init, finish: (data, status = 200) => resolve(new Response(JSON.stringify({data}), {status})), fail: () => reject(new TypeError("offline"))});
    if (obeyAbort) init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), {once: true});
  }));
  vi.stubGlobal("fetch", fetcher);
  return {calls, fetcher};
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("safe read in-flight ownership", () => {
  it("上证叠加与同指数主图复用在途分钟原包，完成后仍重新校准", async () => {
    const source = pendingFetch();
    const overlay = getMinuteLine("sh000001");
    const chart = getMinuteLineWithBaseline("sh000001");
    const dispatched = source.fetcher.mock.calls.length;
    const points = [{ts: "2026-10-14T10:30:00+08:00", price: 4000, source: "tencent"}];
    source.calls.forEach(call => call.finish({symbol: "sh000001", points, vr_baseline_5m: [1]}));
    expect(await overlay).toEqual(points);
    expect((await chart).points).toEqual(points);
    expect(dispatched).toBe(1);
    const next = getMinuteLine("sh000001");
    expect(source.fetcher).toHaveBeenCalledTimes(2);
    source.calls[1].finish({symbol: "sh000001", points}); await next;
  });

  it("分钟人工fresh绕过旧共享原包，旧消费者仍能收到自己的结果", async () => {
    const source = pendingFetch();
    const old = getMinuteLine("sh000001");
    const current = getMinuteLineWithBaseline("sh000001", {fresh: true});
    const oldPoints = [{ts: "2026-10-26T10:00:00+08:00", price: 3000, source: "tencent"}];
    const newPoints = [{...oldPoints[0], price: 3030}];
    try {
      expect(source.fetcher).toHaveBeenCalledTimes(2);
      source.calls[1].finish({symbol: "sh000001", points: newPoints, vr_baseline_5m: null});
      expect((await current).points).toEqual(newPoints);
      expect(source.calls[0].init.signal?.aborted).toBe(false);
      source.calls[0].finish({symbol: "sh000001", points: oldPoints});
      expect(await old).toEqual(oldPoints);
    } finally {
      source.calls.forEach(call => call.finish({symbol: "sh000001", points: oldPoints}));
      await Promise.allSettled([old, current]);
    }
  });

  it("分钟包装取消一个共享消费者不伤另一位，最后离开才取消网络", async () => {
    const source = pendingFetch();
    const one = new AbortController(), two = new AbortController();
    const overlay = getMinuteLine("sh000001", {signal: one.signal}).catch(error => error);
    const chart = getMinuteLineWithBaseline("sh000001", {signal: two.signal}).catch(error => error);
    try {
      expect(source.fetcher).toHaveBeenCalledTimes(1);
      one.abort();
      await Promise.resolve();
      expect(source.calls[0].init.signal?.aborted).toBe(false);
      two.abort();
      expect(source.calls[0].init.signal?.aborted).toBe(true);
      expect(await overlay).toMatchObject({code: "canceled"});
      expect(await chart).toMatchObject({code: "canceled"});
    } finally {
      source.calls.forEach(call => call.finish({symbol: "sh000001", points: []}));
      await Promise.allSettled([overlay, chart]);
    }
  });

  it("整日增强资格池已有在途单飞，默认池与不同交易日不串包", async () => {
    const source = pendingFetch();
    const one = getLimitUpPoolSnapshot("2026-10-14", {includeBreaks: true});
    const two = getLimitUpPoolSnapshot("2026-10-14", {includeBreaks: true});
    const plain = getLimitUpPoolSnapshot("2026-10-14");
    const old = getLimitUpPoolSnapshot("2026-10-13", {includeBreaks: true});
    const dispatched = source.fetcher.mock.calls.length;
    source.calls.forEach(call => call.finish({trade_date: call.url.includes("2026-10-13") ? "2026-10-13" : "2026-10-14", pool: [], break_pool: [], coverage: {limit_up: {state: "success", error: null}, limit_break: {state: "success", error: null}}}));
    const result = await Promise.all([one, two, plain, old]);
    expect(dispatched).toBe(3);
    expect(result[0]).toEqual(result[1]);
    expect(result[3].trade_date).toBe("2026-10-13");
  });

  it.each(["/api/watchlist/600127", "/api/notifications/read-state"])("known write %s preserves an unrelated overview flight before and after completion", async path => {
    const source = pendingFetch();
    const first = getMarketOverview();
    const write = sendJson(path, path.includes("watchlist") ? "DELETE" : "PUT", {});
    const during = getMarketOverview();
    const duringCount = source.fetcher.mock.calls.length;
    source.calls[1].finish({}); await write;
    const after = getMarketOverview();
    const afterCount = source.fetcher.mock.calls.length;
    source.calls.filter(call => call.url.includes("/market/overview")).forEach(call => call.finish({indices: [], total_amount: 100, total_amount_freshness: null}));
    expect(await first).toEqual(await during);
    expect(await after).toEqual(await during);
    expect(duringCount).toBe(2);
    expect(afterCount).toBe(2);
  });

  it("workbench and chart overlay share a pure overview read without sharing mutable results", async () => {
    const source = pendingFetch();
    const workbench = getMarketOverview();
    const overlay = getMarketOverview();
    expect(source.fetcher).toHaveBeenCalledTimes(1);
    source.calls[0].finish({indices: [{symbol: "sh000001", price: 4000, data_timestamp: "2026-10-13T14:29:00+08:00"}], total_amount: 100, total_amount_freshness: null});
    const [left, right] = await Promise.all([workbench, overlay]);
    left.indices[0].price = 1;
    expect(right.indices[0].price).toBe(4000);
    expect(right.indices[0].data_timestamp).toBe("2026-10-13T14:29:00+08:00");
    const next = getMarketOverview();
    expect(source.fetcher).toHaveBeenCalledTimes(2);
    source.calls[1].finish({indices: [], total_amount: null, total_amount_freshness: null});
    await next;
  });
  it("three real watchlist getters share only an unfinished read and return independent nested values", async () => {
    const source = pendingFetch();
    const reads = [getWatchlist(), getWatchlist(), getWatchlist()];
    expect(source.fetcher).toHaveBeenCalledTimes(1);
    source.calls[0].finish([{symbol: "600127", group_name: "默认", details: {reason: "粮食"}}]);
    const [workbench, detail, heatmap] = await Promise.all(reads);
    (workbench[0] as unknown as {details: {reason: string}}).details.reason = "changed";
    expect(detail).toEqual(heatmap);
    expect((detail[0] as unknown as {details: {reason: string}}).details.reason).toBe("粮食");
    const next = getWatchlist();
    expect(source.fetcher).toHaveBeenCalledTimes(2);
    source.calls[1].finish([]); await next;
  });

  it("one caller leaves without aborting the other; the final caller cancels upstream", async () => {
    const source = pendingFetch();
    const one = new AbortController(), two = new AbortController();
    const first = getJson("/api/watchlist", 8000, {signal: one.signal}).catch(e => e);
    const second = getJson("/api/watchlist", 8000, {signal: two.signal}).catch(e => e);
    expect(source.fetcher).toHaveBeenCalledTimes(1);
    one.abort();
    expect(source.calls[0].init.signal?.aborted).toBe(false);
    expect(await first).toMatchObject({code: "canceled"});
    two.abort();
    expect(source.calls[0].init.signal?.aborted).toBe(true);
    expect(await second).toMatchObject({code: "canceled"});
  });

  it("last cancellation removes the entry before a new owner, and old finally cannot evict that new read", async () => {
    const source = pendingFetch(false);
    const caller = new AbortController();
    const old = getJson("/api/watchlist", 8000, {signal: caller.signal}).catch(e => e);
    caller.abort(); await old;
    const current = getWatchlist();
    expect(source.fetcher).toHaveBeenCalledTimes(2);
    source.calls[0].finish([{symbol: "old"}]);
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    const follower = getWatchlist();
    expect(source.fetcher).toHaveBeenCalledTimes(2);
    source.calls[1].finish([{symbol: "new"}]);
    expect(await current).toEqual([{symbol: "new"}]); expect(await follower).toEqual([{symbol: "new"}]);
  });

  it("manual fresh read bypasses a slow owner while preserving the other owner's result", async () => {
    const source = pendingFetch();
    const old = getWatchlist();
    const current = getJson("/api/watchlist", 8000, {fresh: true});
    expect(source.fetcher).toHaveBeenCalledTimes(2);
    source.calls[1].finish([{symbol: "new"}]); expect((await current).data).toEqual([{symbol: "new"}]);
    source.calls[0].finish([{symbol: "old"}]); expect(await old).toEqual([{symbol: "old"}]);
  });

  it("fresh watchlist read preserves an unrelated overview owner", async () => {
    const source = pendingFetch();
    const overviewA = getMarketOverview();
    const oldWatch = getWatchlist();
    const freshWatch = getWatchlist({fresh: true});
    const overviewB = getMarketOverview();
    const dispatched = source.fetcher.mock.calls.length;
    source.calls.forEach((call, i) => call.finish(call.url.includes("/market/overview")
      ? {indices: [], total_amount: 100, total_amount_freshness: null}
      : [{symbol: i === 1 ? "old" : "new"}]));
    const [left, old, current, right] = await Promise.all([overviewA, oldWatch, freshWatch, overviewB]);
    expect(dispatched).toBe(3);
    expect(left).toEqual(right);
    expect(old).toEqual([{symbol: "old"}]);
    expect(current).toEqual([{symbol: "new"}]);
  });

  it("fresh nonshared read does not discard an unrelated shared owner", async () => {
    const source = pendingFetch();
    const before = getMarketOverview();
    const ordinary = getJson("/api/notifications", 8000, {fresh: true});
    const after = getMarketOverview();
    const dispatched = source.fetcher.mock.calls.length;
    source.calls.forEach(call => call.finish(call.url.includes("/market/overview")
      ? {indices: [], total_amount: 100, total_amount_freshness: null} : {items: []}));
    await Promise.all([before, ordinary, after]);
    expect(dispatched).toBe(2);
  });

  it("fresh owner survives old owner cancellation and completion without crossing replies", async () => {
    const source = pendingFetch();
    const oldAbort = new AbortController(), freshAbort = new AbortController();
    const old = getWatchlist({signal: oldAbort.signal}).catch(e => e);
    const oldFollower = getWatchlist();
    const current = getWatchlist({fresh: true, signal: freshAbort.signal}).catch(e => e);
    const currentFollower = getWatchlist();
    expect(source.fetcher).toHaveBeenCalledTimes(2);
    oldAbort.abort();
    expect(await old).toMatchObject({code: "canceled"});
    expect(source.calls[0].init.signal?.aborted).toBe(false);
    expect(source.calls[1].init.signal?.aborted).toBe(false);
    source.calls[0].finish([{symbol: "old"}]);
    expect(await oldFollower).toEqual([{symbol: "old"}]);
    const lateFollower = getWatchlist();
    expect(source.fetcher).toHaveBeenCalledTimes(2);
    freshAbort.abort();
    expect(await current).toMatchObject({code: "canceled"});
    expect(source.calls[1].init.signal?.aborted).toBe(false);
    source.calls[1].finish([{symbol: "new"}]);
    expect(await currentFollower).toEqual([{symbol: "new"}]);
    expect(await lateFollower).toEqual([{symbol: "new"}]);
  });

  it.each(["failed", "timed out"] as const)("write %s isolates reads at start and at completion, even when commit is unknown", async outcome => {
    vi.useFakeTimers();
    const source = pendingFetch();
    const before = getWatchlist();
    const write = sendJson("/api/watchlist/600127", "DELETE", undefined, 500).catch(e => e);
    const during = getWatchlist();
    expect(source.fetcher).toHaveBeenCalledTimes(3);
    if (outcome === "failed") source.calls[1].fail(); else await vi.advanceTimersByTimeAsync(500);
    expect(await write).toMatchObject({code: outcome === "failed" ? "network_error" : "timeout"});
    const after = getWatchlist();
    expect(source.fetcher).toHaveBeenCalledTimes(4);
    source.calls[0].finish([{symbol: "before"}]); source.calls[2].finish([{symbol: "during"}]); source.calls[3].finish([]);
    await Promise.all([before, during, after]);
  });

  it("different parameters, auth headers, credentials and timeout budgets remain separate", async () => {
    const source = pendingFetch();
    const reads = [
      request("/api/watchlist?a=1", {headers: {Authorization: "context-a"}}, 8000),
      request("/api/watchlist?a=1", {headers: {Authorization: "context-b"}}, 8000),
      request("/api/watchlist?a=2", {headers: {Authorization: "context-a"}}, 8000),
      request("/api/watchlist?a=1", {headers: {Authorization: "context-a"}}, 9000),
      request("/api/watchlist?a=1", {headers: {Authorization: "context-a"}, credentials: "omit"}, 8000),
    ];
    expect(source.fetcher).toHaveBeenCalledTimes(5);
    source.calls.forEach((call, i) => call.finish(i));
    expect((await Promise.all(reads)).map(row => row.data)).toEqual([0, 1, 2, 3, 4]);
  });

  it("different deadlines cannot inherit the first owner's timeout", async () => {
    vi.useFakeTimers();
    const source = pendingFetch();
    const short = getJson("/api/watchlist", 100).catch(e => e);
    const long = getJson("/api/watchlist", 200);
    expect(source.fetcher).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(100);
    expect(await short).toMatchObject({code: "timeout"});
    expect(source.calls[1].init.signal?.aborted).toBe(false);
    source.calls[1].finish([]); await long;
  });

  it("does not share between server-side requests", async () => {
    vi.stubGlobal("window", undefined);
    const source = pendingFetch();
    const reads = [getWatchlist(), getWatchlist()];
    expect(source.fetcher).toHaveBeenCalledTimes(2);
    source.calls.forEach(call => call.finish([])); await Promise.all(reads);
  });

  it("a changed browser cookie context cannot join an earlier read", async () => {
    const source = pendingFetch();
    try {
      document.cookie = "read_scope_fixture=first; path=/";
      const first = getWatchlist();
      document.cookie = "read_scope_fixture=second; path=/";
      const second = getWatchlist();
      expect(source.fetcher).toHaveBeenCalledTimes(2);
      source.calls[0].finish([{symbol: "first"}]); source.calls[1].finish([{symbol: "second"}]);
      expect(await first).toEqual([{symbol: "first"}]); expect(await second).toEqual([{symbol: "second"}]);
    } finally {document.cookie = "read_scope_fixture=; Max-Age=0; path=/";}
  });

  it("failed reads are evicted, pre-aborted callers do no I/O and ordinary reads still cancel", async () => {
    const source = pendingFetch();
    const first = getWatchlist().catch(e => e); source.calls[0].fail();
    expect(await first).toMatchObject({code: "network_error"});
    const controller = new AbortController(); controller.abort();
    await expect(getJson("/api/watchlist", 8000, {signal: controller.signal})).rejects.toMatchObject({code: "canceled"});
    expect(source.fetcher).toHaveBeenCalledTimes(1);
    const retry = getWatchlist(); source.calls[1].finish([]); await retry;
    const other = new AbortController();
    const ordinary = request("/api/news/digest/600127", {signal: other.signal}, 8000).catch(e => e);
    other.abort(); expect(source.calls[2].init.signal?.aborted).toBe(true);
    expect(await ordinary).toMatchObject({code: "canceled"});
  });

  it.each(["/api/paper/account", "/api/paper/account?read_only=true", "/api/paper/positions", "/api/events/impact", "/api/market/minute-signals/600127", "/api/news/digest/600127"])("does not share non-allowlisted GET %s", async path => {
    const source = pendingFetch(); const reads = [getJson(path), getJson(path)];
    expect(source.fetcher).toHaveBeenCalledTimes(2);
    source.calls.forEach(call => call.finish([])); await Promise.all(reads);
  });
});
