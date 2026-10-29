import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getPickReviews, getPicksHistory } from "@/lib/api";
import { DEFAULT_TIMEOUT_MS, clearDeviceSessionReads, getJson, sendJson } from "@/lib/api/internal";
import {CandidateDataUnavailableError, getIntradayTop} from "@/lib/api/picks";
import {getKlinePayload, getTradesPayload} from "@/lib/api/market";
import {getThemeCatalogCompletion} from "@/lib/api/themes";

/**
 * 回归（2026-09-10 实测事故）：`undefined is not an object (evaluating 'reviews.length')`。
 *
 * 现场表现是**页面自身逻辑崩**（错误边界显示 `Cannot read properties of undefined
 * (reading 'length')`），实际根因在接口层：`request()` 用 `.json().catch(() => ({}))`
 * 把「响应体不是 JSON」吞成空对象，`res.ok` 为真便直接 `return body` ⇒ `data` 是
 * `undefined`；而调用方的 `.catch(() => [])` **只兜「抛错」、兜不住「返回 undefined」**，
 * 于是崩溃点漂到离根因很远的地方。
 *
 * 这组用例把契约钉死：**200 但响应体不是 API Envelope（缺 `data` 键）必须显式抛错**，
 * 让调用方的 catch 生效、错误可见；`data: null` 是后端「无数据」的合法形态，归一为 `[]`。
 */

function stubFetch(status: number, rawBody: string, contentType = "application/json") {
  const fn = vi.fn(async () => new Response(rawBody, { status, headers: { "content-type": contentType } }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("api · Envelope 契约校验（2026-09-10 崩溃回归）", () => {
  it("keeps dated K-line pages distinct from the latest read and preserves declared price basis", async () => {
    const data={symbol:"600127",timeframe:"1d",bars:[{symbol:"600127",ts:"2024-01-02T00:00:00+08:00",price_basis:"qfq"}],trading_status:null};
    const fetch=stubFetch(200,JSON.stringify({data,meta:{}}));
    expect((await getKlinePayload("600127","1d",1000,{start:"2022-09-01",end:"2024-01-08"})).bars[0].price_basis).toBe("qfq");
    await getKlinePayload("600127","1d",120);
    const urls=fetch.mock.calls.map(call=>String((call as unknown[])[0]));
    expect(urls).toEqual(["/backend/api/kline/600127?timeframe=1d&limit=1000&start=2022-09-01&end=2024-01-08","/backend/api/kline/600127?timeframe=1d&limit=120"]);
  });
  it.each([200, 401])("配对更换后不交付旧会话响应或撤销新会话（%s）", async status => {
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => {finish = resolve;})));
    const expired = vi.fn(); window.addEventListener("ashare-access-expired", expired);
    try {
      const old = getJson("/api/real/positions").catch(error => error);
      clearDeviceSessionReads();
      finish(Response.json(status === 200 ? {data: [{private: "old"}]} : {detail: "expired"}, {status}));
      expect((await old).code).toBe("canceled");
      expect(expired).not.toHaveBeenCalled();
    } finally {window.removeEventListener("ashare-access-expired", expired);}
  });
  it("keeps a successful empty trades read separate from its source capability reason and source clock", async () => {
    const meta = {trades_source: "tdx", trades_detail: "该源当前未提供逐笔", source_timestamp: "2026-10-13T10:00:00+08:00"};
    stubFetch(200, JSON.stringify({data: [], meta}));
    expect(await getTradesPayload("600127")).toEqual({items: [], source: "tdx", empty_reason: "该源当前未提供逐笔", meta});
    stubFetch(502, JSON.stringify({detail: "源失败"}));
    await expect(getTradesPayload("600127")).rejects.toThrow("源失败");
  });
  it("正常 Envelope → 返回 data", async () => {
    stubFetch(200, JSON.stringify({ data: [{ date: "2026-09-10", symbol: "000785", name: "居然智家", verdict: "good", reason_category: "gone_well", excess_pct: -0.84, note: "" }], meta: {} }));
    const rows = await getPickReviews();
    expect(rows).toHaveLength(1);
    expect(rows[0].symbol).toBe("000785");
  });

  it("200 但响应体是 HTML（非 JSON）→ 抛 bad_envelope，而不是静默返回 undefined", async () => {
    stubFetch(200, "<!DOCTYPE html><html><body>not json</body></html>", "text/html");
    const err = await getPickReviews().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("bad_envelope");
    expect((err as ApiError).message).toContain("缺少 data 字段");
    // 关键：不能再是 undefined（那正是 224 / 650 行崩溃的输入）
    expect(err).not.toBeUndefined();
  });

  it("200 但 Envelope 里没有 data 键（如 {\"items\":[]}）→ 同样抛 bad_envelope", async () => {
    stubFetch(200, JSON.stringify({ items: [] }));
    const err = await getPickReviews().catch((e: unknown) => e);
    expect((err as ApiError).code).toBe("bad_envelope");
  });

  it("data 为 null（后端「无数据」的合法形态）→ 列表接口归一为 []，不崩", async () => {
    stubFetch(200, JSON.stringify({ data: null }));
    await expect(getPickReviews()).resolves.toEqual([]);
  });

  it("非 2xx → 沿用既有行为：抛后端 detail，不被 Envelope 校验吞掉", async () => {
    stubFetch(500, JSON.stringify({ detail: "服务器内部错误", code: "internal_error" }));
    const err = await getPickReviews().catch((e: unknown) => e);
    expect((err as ApiError).message).toBe("服务器内部错误");
    expect((err as ApiError).code).toBe("internal_error");
  });

  it("getPicksHistory 同样走归一（data:null → []）", async () => {
    stubFetch(200, JSON.stringify({ data: null }));
    await expect(getPicksHistory(10)).resolves.toEqual([]);
  });
});

describe("api · 完整响应预算", () => {
  function delayedBody(delay: number, status = 200, headerDelay = 0) {
    let signal!: AbortSignal;
    const fn = vi.fn(async (_url: string, init: RequestInit) => {
      signal = init.signal as AbortSignal;
      if (headerDelay) await new Promise(resolve => setTimeout(resolve, headerDelay));
      const stream = new ReadableStream({ start(controller) {
        const timer = setTimeout(() => {
          controller.enqueue(new TextEncoder().encode(JSON.stringify({ data: "done" })));
          controller.close();
        }, delay);
        signal.addEventListener("abort", () => {
          clearTimeout(timer);
          controller.error(new DOMException("aborted", "AbortError"));
        }, { once: true });
      } });
      return new Response(stream, { status });
    });
    vi.stubGlobal("fetch", fn);
    return { fn, signal: () => signal };
  }

  it("默认仍是 8000ms：已收到响应头但正文迟到时也超时", async () => {
    vi.useFakeTimers();
    const source = delayedBody(9_000);
    let outcome: unknown;
    void getJson("/slow-body").then(value => { outcome = value; }, error => { outcome = error; });
    await vi.advanceTimersByTimeAsync(7_999);
    expect(outcome).toBeUndefined();
    await vi.advanceTimersByTimeAsync(1);
    expect(DEFAULT_TIMEOUT_MS).toBe(8_000);
    expect(outcome).toMatchObject({ status: 0, code: "timeout" });
    expect(source.signal().aborted).toBe(true);
    expect(source.fn).toHaveBeenCalledTimes(1);
  });

  it("响应头与正文共用累计预算，收到头时不重新计时", async () => {
    vi.useFakeTimers();
    const source = delayedBody(500, 200, 600);
    let outcome: unknown;
    void getJson("/combined-budget", 1_000).then(value => { outcome = value; }, error => { outcome = error; });
    await vi.advanceTimersByTimeAsync(999);
    expect(outcome).toBeUndefined();
    await vi.advanceTimersByTimeAsync(1);
    expect(outcome).toMatchObject({ code: "timeout" });
    expect(source.signal().aborted).toBe(true);
  });

  it("完整正文在预算内返回，之后不会被残留计时器取消", async () => {
    vi.useFakeTimers();
    const source = delayedBody(7_999);
    const result = getJson("/within-budget");
    await vi.advanceTimersByTimeAsync(7_999);
    await expect(result).resolves.toMatchObject({ data: "done" });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(source.signal().aborted).toBe(false);
  });

  it("同步解析已超预算也不能返回成功", async () => {
    const now = vi.spyOn(performance, "now").mockReturnValue(0);
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => {
      now.mockReturnValue(8_001);
      return { data: "late parse" };
    } })));
    await expect(getJson("/parse-budget")).rejects.toMatchObject({ status: 0, code: "timeout" });
  });

  it("写请求正文超时不伪造成成功，也不自动重发", async () => {
    vi.useFakeTimers();
    const source = delayedBody(1_001);
    let outcome: unknown;
    void sendJson("/write", "POST", { action: "requested" }, 1_000)
      .then(value => { outcome = value; }, error => { outcome = error; });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(outcome).toMatchObject({ status: 0, code: "timeout" });
    expect(source.fn).toHaveBeenCalledTimes(1);
    expect(source.fn.mock.calls[0][1]).toMatchObject({ method: "POST", body: '{"action":"requested"}' });
  });

  it.each(["null", '"text"', "42"])("200 非对象正文 %s 仍是 bad_envelope", async raw => {
    stubFetch(200, raw);
    await expect(getJson("/bad-envelope")).rejects.toMatchObject({ status: 200, code: "bad_envelope" });
  });

  it("非 JSON 错误响应保留 HTTP 状态", async () => {
    stubFetch(503, "upstream unavailable", "text/plain");
    await expect(getJson("/unavailable")).rejects.toMatchObject({ status: 503, code: "http_503", message: "HTTP 503" });
  });

  it("响应头前断线仍是 network_error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("connection failed"); }));
    await expect(getJson("/network")).rejects.toMatchObject({ status: 0, code: "network_error" });
  });

  it("响应头前中止仍是 timeout", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new DOMException("aborted", "AbortError"); }));
    await expect(getJson("/abort")).rejects.toMatchObject({ status: 0, code: "timeout" });
  });
});


it("intraday failure remains an error while a verified empty result is allowed", async () => {
  stubFetch(200, JSON.stringify({data: {items: [], read_state: "unavailable", empty_reasons: ["数据暂未就绪"]}}));
  await expect(getIntradayTop()).rejects.toMatchObject({code: "candidate_data_unavailable"});
  stubFetch(200, JSON.stringify({data: {items: [], read_state: "ready", empty_reasons: ["本轮条件未满足"]}}));
  await expect(getIntradayTop()).resolves.toMatchObject({items: [], read_state: "ready"});
});

it("keeps only bounded plain-text business reasons and the original read context on an unavailable candidate response", async () => {
  const context = {snapshot_state: "degraded", snapshot_as_of: "2026-10-27T14:53:27+08:00", linkage_failed: false,
    theme_gate_counts: {missing_catalog: 2}, missing_quote: 7};
  stubFetch(200, JSON.stringify({data: {items: [{symbol: "should-not-become-current"}], read_state: "unavailable",
    empty_reasons: [null, "", " 全市场\n数据未就绪\u0000 ", "全市场 数据未就绪", "官方成分对应关系待核", "x".repeat(500), "第四条不铺长文"], read_context: context}}));
  let error: unknown;
  try {await getIntradayTop();} catch (value) {error = value;}
  expect(error).toBeInstanceOf(CandidateDataUnavailableError);
  expect(error).toBeInstanceOf(ApiError);
  const unavailable = error as CandidateDataUnavailableError;
  expect(unavailable).toMatchObject({status: 503, code: "candidate_data_unavailable", context});
  expect(unavailable.reasons).toEqual(["全市场 数据未就绪", "官方成分对应关系待核", "x".repeat(160)]);
  expect(unavailable.message).not.toMatch(/[\u0000-\u001f\u007f]/);
  expect(unavailable).not.toHaveProperty("items");
});

it("does not present actual HTTP failures as successfully read business-unavailable evidence", async () => {
  stubFetch(500, JSON.stringify({detail: "upstream failed"}));
  let error: unknown;
  try {await getIntradayTop();} catch (value) {error = value;}
  expect(error).toBeInstanceOf(ApiError);
  expect(error).not.toBeInstanceOf(CandidateDataUnavailableError);
  expect(error).toMatchObject({status: 500});
});

describe("目录完成等待只承接已命名 owner",()=>{
  const key="c".repeat(32),version="a".repeat(64),clock="2026-10-28T00:00:00+00:00";
  const identity=()=>({code:"881126.TI",name:"汽车零部件",source:"ths_official",basis:"exact_name",kind:"industry",catalog_version:version,observed_at:clock});
  const payload=()=>({scope:"current_directory_resolution",completion_key:key,state:"available",catalog_version:version,observed_at:clock,identities:{汽车零部件:identity()} as Record<string,ReturnType<typeof identity>>,ambiguous:[],reason:null});

  it("保留原 15s owner 等待预算，正确读取行业并且只有一个 cache-only 请求",async()=>{
    vi.useFakeTimers();
    const fetcher=vi.fn<typeof fetch>(()=>new Promise<Response>(resolve=>setTimeout(()=>resolve(Response.json({data:payload()})),15_000)));
    vi.stubGlobal("fetch",fetcher);
    const work=getThemeCatalogCompletion(key);
    await vi.advanceTimersByTimeAsync(15_000);
    expect((await work).identities.汽车零部件.kind).toBe("industry");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(String(fetcher.mock.calls[0][0])).toContain(`/api/themes/catalog/completions/${key}`);
  });

  it.each(["scope","key","kind","name","version","clock","count"])("拒绝 %s 漂移，不将错包当正式身份",async fault=>{
    const data=payload();
    if(fault==="scope")data.scope="current_directory_reference";
    if(fault==="key")data.completion_key="d".repeat(32);
    if(fault==="kind")data.identities.汽车零部件.kind="other";
    if(fault==="name")data.identities.汽车零部件.name="其他名称";
    if(fault==="version")data.identities.汽车零部件.catalog_version="b".repeat(64);
    if(fault==="clock")data.identities.汽车零部件.observed_at="2026-10-28T00:01:00+00:00";
    if(fault==="count")data.identities=Object.fromEntries(Array.from({length:2001},(_,i)=>[String(i),identity()]));
    stubFetch(200,JSON.stringify({data}));
    await expect(getThemeCatalogCompletion(key)).rejects.toMatchObject({code:"bad_catalog_completion"});
  });

  it("不可用是终态，过期不重试，损坏 key 不发请求",async()=>{
    const fetcher=stubFetch(200,JSON.stringify({data:{...payload(),state:"unavailable",catalog_version:null,observed_at:null,identities:{},reason:"completion_wait_timeout"}}));
    expect((await getThemeCatalogCompletion(key)).state).toBe("unavailable");
    await expect(getThemeCatalogCompletion("not-a-key")).rejects.toMatchObject({code:"invalid_completion_key"});
    expect(fetcher).toHaveBeenCalledTimes(1);
    const expired=stubFetch(410,JSON.stringify({detail:"owner expired"}));
    await expect(getThemeCatalogCompletion(key)).rejects.toMatchObject({status:410});
    expect(expired).toHaveBeenCalledTimes(1);
  });
});
