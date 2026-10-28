import {afterEach,beforeEach,expect,it,vi} from "vitest";
import {getResearchClusters} from "./api/themes";
import {clearDeviceSessionReads} from "./api/internal";
import {clusterFixture} from "@/test/research-clusters-fixture";
beforeEach(()=>{vi.useFakeTimers();vi.setSystemTime(new Date("2026-10-28T02:00:00Z"));clearDeviceSessionReads();});
afterEach(()=>{clearDeviceSessionReads();vi.useRealTimers();vi.unstubAllGlobals();});
it("shares only unfinished same-session reads; evidence opens require no extra API",async()=>{
  let finish!:(response:Response)=>void;
  const fetcher=vi.fn(()=>new Promise<Response>(resolve=>{finish=resolve;}));vi.stubGlobal("fetch",fetcher);
  const first=getResearchClusters(),second=getResearchClusters();
  expect(fetcher).toHaveBeenCalledTimes(1);
  finish(new Response(JSON.stringify({data:clusterFixture()})));
  expect((await first).version).toBe((await second).version);
  const next=getResearchClusters();expect(fetcher).toHaveBeenCalledTimes(2);finish(new Response(JSON.stringify({data:clusterFixture()})));await next;
});
it("explicit saved query keeps date and version, rejects same-day current backfill",async()=>{
  const value=clusterFixture();
  const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({data:value})));vi.stubGlobal("fetch",fetcher);
  await expect(getResearchClusters({date:"20261028",historical:true,version:value.version!})).rejects.toMatchObject({code:"bad_research_clusters"});
  expect(fetcher.mock.calls[0][0]).toBe(`/backend/api/themes/research-clusters?date=2026-10-28&version=${value.version}&historical=true`);
});
it("different dates never join and malformed identities fail closed",async()=>{
  const fetcher=vi.fn(async(url:string)=>{
    const date=url.includes("2026-10-27")?"2026-10-27":"2026-10-28";
    const value=clusterFixture(date);value.reference_mode="saved";
    if(date==="2026-10-28")value.clusters[0].branches[0].official_identity!.source="unrelated";
    return new Response(JSON.stringify({data:value}));
  });vi.stubGlobal("fetch",fetcher);
  const first=getResearchClusters({date:"2026-10-27",historical:true});
  const second=getResearchClusters({date:"2026-10-28",historical:true});
  expect((await first).trade_date).toBe("2026-10-27");
  await expect(second).rejects.toMatchObject({code:"bad_research_clusters"});
  expect(fetcher).toHaveBeenCalledTimes(2);
});
it("default read accepts server calendar-resolved past saved observation, never past current or future",async()=>{
  const value=clusterFixture("2026-10-27");value.reference_mode="saved";
  const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({data:value})))
    .mockResolvedValueOnce(new Response(JSON.stringify({data:{...value,reference_mode:"current"}})))
    .mockResolvedValueOnce(new Response(JSON.stringify({data:clusterFixture("2026-10-29")})));
  vi.stubGlobal("fetch",fetcher);
  expect((await getResearchClusters()).reference_mode).toBe("saved");
  await expect(getResearchClusters()).rejects.toMatchObject({code:"bad_research_clusters"});
  await expect(getResearchClusters()).rejects.toMatchObject({code:"bad_research_clusters"});
});
