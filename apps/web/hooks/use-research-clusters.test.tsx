import {afterEach,expect,it,vi} from "vitest";
import {act,cleanup,renderHook,waitFor} from "@testing-library/react";
import {useResearchClusters} from "./use-research-clusters";
import {getResearchClusters} from "@/lib/api/themes";
import {clusterFixture} from "@/test/research-clusters-fixture";
import type {ResearchClustersPayload} from "@/types/research-clusters";
vi.mock("@/lib/api/themes",async()=>({...await vi.importActual<typeof import("@/lib/api/themes")>("@/lib/api/themes"),getResearchClusters:vi.fn()}));
afterEach(()=>{cleanup();vi.resetAllMocks();vi.useRealTimers();});
function deferred(){let resolve!:(v:ResearchClustersPayload)=>void;const promise=new Promise<ResearchClustersPayload>(yes=>{resolve=yes;});return {promise,resolve};}
it("date/version switch rejects late prior reads and retires prior content immediately",async()=>{
  const first=deferred(),second=deferred();
  vi.mocked(getResearchClusters).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const view=renderHook(({day,version})=>useResearchClusters(day,{historical:true,version}),{initialProps:{day:"2026-10-27",version:"a".repeat(64)}});
  view.rerender({day:"2026-10-28",version:"b".repeat(64)});
  await act(async()=>first.resolve({...clusterFixture("2026-10-27"),reference_mode:"saved"}));
  expect(view.result.current.data).toBeUndefined();
  await act(async()=>second.resolve({...clusterFixture("2026-10-28"),reference_mode:"saved",version:"b".repeat(64)}));
  expect(view.result.current.data?.trade_date).toBe("2026-10-28");
  expect(view.result.current.data?.version).toBe("b".repeat(64));
  expect(getResearchClusters).toHaveBeenCalledTimes(2);
  expect(vi.mocked(getResearchClusters).mock.calls[0][1]?.signal?.aborted).toBe(true);
});
it("inactive reader starts no work and re-read failure retains explicitly stale observation",async()=>{
  const source=clusterFixture();vi.mocked(getResearchClusters).mockResolvedValueOnce(source).mockRejectedValueOnce(new Error("offline"));
  const view=renderHook(({active})=>useResearchClusters(undefined,{active}),{initialProps:{active:false}});
  expect(getResearchClusters).not.toHaveBeenCalled();
  view.rerender({active:true});await waitFor(()=>expect(view.result.current.data).toBe(source));
  act(()=>view.result.current.refresh());await waitFor(()=>expect(view.result.current.error).toBeTruthy());
  expect(view.result.current.data).toBe(source);
  expect(getResearchClusters).toHaveBeenCalledTimes(2);
});
it("Beijing midnight retires the default owner's old-day response on existing parent render without a new timer",async()=>{
  vi.useFakeTimers();vi.setSystemTime(new Date("2026-10-28T15:59:59Z"));
  const first=deferred(),second=deferred();vi.mocked(getResearchClusters).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const view=renderHook(()=>useResearchClusters());
  vi.setSystemTime(new Date("2026-10-28T16:00:01Z"));view.rerender();
  await act(async()=>first.resolve(clusterFixture("2026-10-28")));
  expect(view.result.current.data).toBeUndefined();
  await act(async()=>second.resolve(clusterFixture("2026-10-29")));
  expect(view.result.current.data?.trade_date).toBe("2026-10-29");
  expect(getResearchClusters).toHaveBeenCalledTimes(2);
});
