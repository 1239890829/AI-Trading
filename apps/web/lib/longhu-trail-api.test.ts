import {afterEach, expect, it, vi} from "vitest";
import {getLonghuThemeTrail} from "./api/market";

afterEach(()=>vi.unstubAllGlobals());
const payload=()=>({days:["2026-09-01"],requested_date:"2026-09-01",anchor_date:"2026-09-01",expected_days:["2026-09-01"],coverage_state:"complete",empty_days:[],trail:[{concept:"历史标签",daily:[{date:"2026-09-01",net:0}],total:0,first:0,last:0}],degraded:[],note:"等分估算，非真实拆分"});

it("binds the cutoff and cancellation to the actual request without hiding real zero",async()=>{
  const fetcher=vi.fn(async(_url:RequestInfo|URL,_init?:RequestInit)=>Response.json({data:payload(),meta:{}}));
  vi.stubGlobal("fetch",fetcher);
  const controller=new AbortController();
  const data=await getLonghuThemeTrail(5,"2026-09-01",{signal:controller.signal});
  expect(String(fetcher.mock.calls[0][0])).toContain("days=5&date=2026-09-01");
  expect(fetcher.mock.calls[0][1]?.signal).toBeInstanceOf(AbortSignal);
  expect(data.trail[0].daily[0].net).toBe(0);
});

it.each(["20260901","2026-02-30","2026-9-01"])("rejects malformed cutoff %s before fetch",async day=>{
  const fetcher=vi.fn();vi.stubGlobal("fetch",fetcher);
  await expect(getLonghuThemeTrail(5,day)).rejects.toThrow(/截止日格式/);
  expect(fetcher).not.toHaveBeenCalled();
});

it.each([
  {...payload(),requested_date:null}, {...payload(),anchor_date:"2026-09-02"},
  {...payload(),days:["2026-09-02"]}, {...payload(),expected_days:["2026-09-02"]},
  {...payload(),trail:[{...payload().trail[0],daily:[{date:"2026-09-02",net:100}]}]},
])("rejects latest or wrong-window records returned for history",async data=>{
  vi.stubGlobal("fetch",vi.fn(async()=>Response.json({data,meta:{}})));
  await expect(getLonghuThemeTrail(5,"2026-09-01")).rejects.toThrow(/窗口身份/);
});

it("preserves default latest scope and rejects a historical envelope masquerading as default",async()=>{
  const fetcher=vi.fn(async(_url:RequestInfo|URL)=>Response.json({data:{...payload(),requested_date:null},meta:{}}));vi.stubGlobal("fetch",fetcher);
  await getLonghuThemeTrail();
  expect(String(fetcher.mock.calls[0][0])).not.toContain("date=");
  fetcher.mockImplementation(async()=>Response.json({data:payload(),meta:{}}));
  await expect(getLonghuThemeTrail()).rejects.toThrow(/窗口身份/);
});
