import {beforeEach, describe, expect, it, vi} from "vitest";
import {createSelectionValidationReader, type ValidationReport} from "./api/validation";
import {versionObservation} from "./validation-read";
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

describe("scope-bound original version projection",()=>{
  function fixture(): ValidationReport {
    const base={date:"2026-10-12",method_version:null,feature_version:null};
    const daily={...base,id:"daily",scope:"daily",return_pct:0};
    const intraday={...base,id:"intraday",scope:"intraday",return_pct:5};
    const group=(scope:string,id:string,value:number)=>({key:scope,scope,method_version:null,feature_version:null,
      entry_ids:[id],selection:{count:1,observed:1,missing:0,mean_pct:value,median_pct:value},daily:[],coverage:{}});
    return {scope:"all",selection:{count:1},entries:[daily],version_entries:[daily,intraday],
      actual:{denominator:2},episodes:[{id:1}],version_groups:[group("daily","daily",0),group("intraday","intraday",5)]} as unknown as ValidationReport;
  }
  it("selects joint identities without copying one source's result into another or changing fills",()=>{
    const source=fixture();
    const daily=versionObservation(source,"daily")!;
    const intraday=versionObservation(source,"intraday")!;
    expect(daily.selection.mean_pct).toBe(0);
    expect(intraday.selection.mean_pct).toBe(5);
    expect(daily.entries[0].scope).toBe("daily");
    expect(intraday.entries[0].scope).toBe("intraday");
    expect(intraday.actual).toBe(source.actual);
    expect(intraday.episodes).toBe(source.episodes);
    expect(source.entries).toHaveLength(1);
  });
  it("rejects missing rows, wrong scope and mixed feature identities",()=>{
    const source=fixture();
    expect(versionObservation({...source,scope:"daily"},"intraday")).toBeNull();
    expect(versionObservation({...source,version_entries:[]},"daily")).toBeNull();
    source.version_groups![0].feature_version="unrelated";
    expect(versionObservation(source,"daily")).toBeNull();
    expect(versionObservation(source,"absent")).toBeNull();
  });
});
