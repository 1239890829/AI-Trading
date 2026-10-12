import { afterEach, describe, expect, it, vi } from "vitest";
import { getJson, request, sendJson } from "@/lib/api/internal";
import { getWatchlist } from "@/lib/api/watchlist";

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
