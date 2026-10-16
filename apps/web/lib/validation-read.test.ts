import {beforeEach, describe, expect, it, vi} from "vitest";
import {createSelectionValidationReader, type ValidationReport} from "./api/validation";
const mocks = vi.hoisted(() => ({getJson: vi.fn()}));
vi.mock("./api/internal", () => ({getJson: mocks.getJson, getJsonArray: vi.fn()}));
beforeEach(() => mocks.getJson.mockReset());

describe("revision-checked validation reads", () => {
  it("reuses only confirmed content and accepts a corrected revision", async () => {
    const first = {entries: [{name:"旧记录"}]} as unknown as ValidationReport;
    const corrected = {entries: [{name:"更正"}]} as unknown as ValidationReport;
    const read = createSelectionValidationReader("2026-10-12","2026-10-12","d1_close","all");
    const signal = new AbortController().signal;
    mocks.getJson.mockResolvedValueOnce({data: first, meta:{revision:"a"}})
      .mockResolvedValueOnce({data:null,meta:{revision:"a",not_modified:true}})
      .mockResolvedValueOnce({data:corrected,meta:{revision:"b"}});
    expect(await read(signal)).toBe(first);
    expect(await read(signal)).toBe(first);
    expect(mocks.getJson.mock.calls[1][0]).toContain("known_revision=a");
    expect(await read(signal)).toBe(corrected);
    expect(mocks.getJson.mock.calls[2][2]).toEqual({signal});
  });
  it("never treats a failed or unrelated response as an unchanged success", async () => {
    const read = createSelectionValidationReader("a","b","d1_close","all");
    mocks.getJson.mockResolvedValueOnce({data:null,meta:{revision:"a",not_modified:true}});
    await expect(read(new AbortController().signal)).rejects.toThrow("统计版本不完整");
  });
  it("does not retain a completed response after cancellation", async () => {
    const read = createSelectionValidationReader("a","b","d1_close","all");
    const controller = new AbortController();
    controller.abort();
    mocks.getJson.mockResolvedValue({data:{entries:[]},meta:{revision:"a"}});
    await expect(read(controller.signal)).rejects.toThrow("Request canceled");
    await read(new AbortController().signal);
    expect(mocks.getJson.mock.calls[1][0]).not.toContain("known_revision");
  });
});
