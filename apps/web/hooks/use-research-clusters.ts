"use client";

import {useResource} from "./use-resource";
import {getResearchClusters} from "@/lib/api/themes";
import {researchDate} from "@/lib/research-clusters";
import {bjToday} from "@/lib/market-hours";

/** One optional read per active view. Cards receive its value, never their own readers. */
export function useResearchClusters(date?: string, options: {active?: boolean; historical?: boolean; version?: string} = {}) {
  const day=date ? researchDate(date) : undefined;
  const dayKey=day??(options.active===false?"disabled":`current:${bjToday()}`);
  return useResource(signal=>getResearchClusters({date:day,historical:options.historical,version:options.version},{signal}),{
    key:JSON.stringify([dayKey,options.historical??false,options.version??null]),enabled:options.active??true,
    abortable:true,intervalMs:null,marketHours:false,refreshOnVisible:false,
  });
}
