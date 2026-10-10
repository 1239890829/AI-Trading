import {afterEach, describe, expect, it, vi} from "vitest";
import {getLimitDownPoolSnapshot, getLimitUpPool, getLimitUpPoolSnapshot} from "./api/market";

afterEach(() => {vi.unstubAllGlobals();});
const reply = (data: unknown) => new Response(JSON.stringify({data, meta: {}}), {status: 200});

describe("pool date identity and shared latest reads", () => {
  it("keeps response date even for an empty default pool and coalesces consumers", async () => {
    let finish!: (response: Response) => void;
    const fetcher = vi.fn((_url: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(resolve => {finish = resolve;}));
    vi.stubGlobal("fetch", fetcher);
    const snapshot = getLimitUpPoolSnapshot();
    const rows = getLimitUpPool();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe("/backend/api/limit-up");
    finish(reply({trade_date: "2026-10-09", pool: []}));
    expect(await snapshot).toEqual({trade_date: "2026-10-09", pool: []});
    expect(await rows).toEqual([]);
  });

  it("does not cache a failed read, and rejects a silent previous-date fallback", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({detail:"date unknown"}), {status: 503}))
      .mockResolvedValueOnce(reply({trade_date:"2026-10-09", pool: []}))
      .mockResolvedValueOnce(reply({trade_date:"2026-10-09", pool: []}));
    vi.stubGlobal("fetch", fetcher);
    await expect(getLimitUpPoolSnapshot()).rejects.toThrow("date unknown");
    expect((await getLimitUpPoolSnapshot()).trade_date).toBe("2026-10-09");
    await expect(getLimitUpPoolSnapshot("2026-10-10")).rejects.toThrow("日期与请求或记录不一致");
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("rejects mixed row dates for both pool kinds", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({trade_date:"2026-10-09", pool: [{trade_date:"2026-10-08"}]})));
    await expect(getLimitUpPoolSnapshot("20261009")).rejects.toThrow("日期与请求或记录不一致");
    await expect(getLimitDownPoolSnapshot("20261009")).rejects.toThrow("日期与请求或记录不一致");
  });
});
