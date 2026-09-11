import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getPickReviews, getPicksHistory } from "@/lib/api";

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
  vi.unstubAllGlobals();
});

describe("api · Envelope 契约校验（2026-09-10 崩溃回归）", () => {
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
