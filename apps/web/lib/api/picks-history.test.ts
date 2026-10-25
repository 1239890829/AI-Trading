import {beforeEach,describe,expect,it,vi} from "vitest";
import {getStrategyMethodRun,getStrategyMethodRuns,type SavedMethodRunIdentity} from "./picks";
const read=vi.hoisted(()=>vi.fn());
vi.mock("./internal",()=>({getJson:read,getJsonArray:vi.fn(),sendJson:vi.fn()}));
const identity:SavedMethodRunIdentity={method:"daily_picks",run_id:"42",trade_date:"2026-10-22",source_version:"original+source",content_version:"a".repeat(64),strategy_version:"method-old",state:"saved"};
beforeEach(()=>{read.mockReset();read.mockResolvedValue({data:{state:"saved"}});});
describe("saved selection history API",()=>{
  it("sends the original date, source, method version, content digest and requested page",async()=>{
    const controller=new AbortController();await getStrategyMethodRun(identity,{offset:200,limit:200,signal:controller.signal});
    const [path,,options]=read.mock.calls[0];const url=new URL(path,"http://localhost");
    expect(url.pathname).toBe("/api/picks/method-runs/daily_picks/42");
    for(const [key,value] of Object.entries({trade_date:identity.trade_date,source_version:identity.source_version,strategy_version:identity.strategy_version,content_version:identity.content_version,offset:"200",limit:"200"}))expect(url.searchParams.get(key)).toBe(value);
    expect(options.signal).toBe(controller.signal);expect(url.searchParams.has("source_version_missing")).toBe(false);
  });
  it("sends missing source state without inventing or dropping a version binding",async()=>{
    await getStrategyMethodRun({...identity,source_version:null});
    const url=new URL(read.mock.calls[0][0],"http://localhost");
    expect(url.searchParams.get("source_version_missing")).toBe("true");expect(url.searchParams.has("source_version")).toBe(false);
  });
  it("keeps the same date and version filters while continuing the original index scope",async()=>{
    await getStrategyMethodRuns({method:"intraday_watch",trade_date:"2026-10-22",source_version_missing:true,strategy_version:"saved-method",offset:30,limit:30});
    const url=new URL(read.mock.calls[0][0],"http://localhost");
    expect(url.pathname).toBe("/api/picks/method-runs");
    expect(Object.fromEntries(url.searchParams)).toEqual({method:"intraday_watch",limit:"30",offset:"30",trade_date:"2026-10-22",source_version_missing:"true",strategy_version:"saved-method"});
  });
});
