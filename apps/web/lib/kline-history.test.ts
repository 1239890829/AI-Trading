import {describe, expect, it} from "vitest";
import type {Kline} from "@/types/market";
import {klineHistoryWindow, prependKlineHistory, refreshKlineHistory} from "./kline-history";

function row(day: string, close = 10): Kline {
  return {symbol:"600519",timeframe:"1d",ts:day,open:close,high:close,low:close,close,source:"tdx",price_basis:"qfq",quality:"high",quality_reasons:[],received_at:"2026-10-09T10:00:00+08:00"};
}
const window = {start:"2025-01-01",end:"2026-10-08"};
describe("同源日K历史合并", () => {
  it("bounds a 500-calendar-day window with overlap and never mutates source rows", () => {
    const current = [row("2026-10-01"),row("2026-10-08"),row("2026-10-09")];
    const frozen = JSON.stringify(current);
    const request = klineHistoryWindow(current);
    expect(Date.parse(`${request.boundary}T00:00:00Z`) - Date.parse(`${request.start}T00:00:00Z`)).toBe(500*86400_000);
    expect(request.end).toBe("2026-10-09");
    const result = prependKlineHistory(current,[row("2026-09-30"),row("2026-10-01")],"600519",window);
    expect(result.map(item=>item.ts)).toEqual(["2026-09-30","2026-10-01","2026-10-08","2026-10-09"]);
    expect(result[1]).toBe(current[0]);
    expect(JSON.stringify(current)).toBe(frozen);
  });
  it.each(["basis","source","identity","period","duplicate","order","overlap","no_overlap","outside","empty","no_older"])("rejects unsafe history %s without claiming all history is complete", failure => {
    const current = [row("2026-10-01"),row("2026-10-08")];
    let page = [row("2026-09-30"),row("2026-10-01")];
    if (failure === "basis") page.forEach(item=>{item.price_basis="unknown";});
    if (failure === "source") page.forEach(item=>{item.source="tencent";});
    if (failure === "identity") page[0].symbol="000001";
    if (failure === "period") page[0].timeframe="1w";
    if (failure === "duplicate") page=[row("2026-10-01"),row("2026-10-01")];
    if (failure === "order") page=page.reverse();
    if (failure === "overlap") Object.assign(page[1], {open:11,high:11,low:11,close:11});
    if (failure === "no_overlap") page=[row("2026-09-30")];
    if (failure === "outside") page=[row("2024-12-31"),row("2026-10-01")];
    if (failure === "empty") page=[];
    if (failure === "no_older") page=[row("2026-10-01")];
    const frozen=JSON.stringify(current);
    expect(()=>prependKlineHistory(current,page,"600519",window)).toThrow(failure === "overlap" ? "重叠OHLC已变化" : undefined);
    expect(JSON.stringify(current)).toBe(frozen);
  });
  it("refuses to request an unknown adjustment basis", () => {
    expect(()=>klineHistoryWindow([{...row("2026-10-01"),price_basis:undefined}])).toThrow("复权口径未知");
  });
  it("rejects positive prices whose high and low contradict the candle", () => {
    const bad={...row("2026-10-01"),open:10,close:10,high:8,low:11};
    expect(()=>klineHistoryWindow([bad])).toThrow("OHLC区间无效");
    expect(()=>prependKlineHistory([row("2026-10-01")],[row("2026-09-30"),bad],"600519",window)).toThrow("OHLC区间无效");
    expect(()=>refreshKlineHistory([row("2026-10-01")],[bad],"600519","2026-10-09")).toThrow("OHLC区间无效");
  });
  it.each(["low","stale","invalid"] as const)("does not use %s quotes for a comparable history series", quality => {
    const bad = {...row("2026-10-01"),quality};
    expect(()=>klineHistoryWindow([bad])).toThrow("来源质量不足");
    expect(()=>prependKlineHistory([row("2026-10-01")],[row("2026-09-30"),bad],"600519",window)).toThrow("来源质量不足");
    expect(()=>refreshKlineHistory([row("2026-10-01")],[bad],"600519","2026-10-09")).toThrow("来源质量不足");
  });
  it("keeps all loaded history while replacing only verified overlap and the current mutable session", () => {
    const current=[row("2026-09-01"),row("2026-10-08"),row("2026-10-09")];
    const latest=[row("2026-10-08"),row("2026-10-09",11)];
    const merged=refreshKlineHistory(current,latest,"600519","2026-10-09");
    expect(merged.map(item=>[item.ts,item.close])).toEqual([["2026-09-01",10],["2026-10-08",10],["2026-10-09",11]]);
    expect(refreshKlineHistory(merged,[row("2026-10-09",11),row("2026-10-12",12)],"600519","2026-10-12").at(-1)?.close).toBe(12);
    expect(merged[0]).toBe(current[0]);
    expect(current[2].close).toBe(10);
  });
  it.each(["basis","source","anchor","empty","no_overlap"])("preserves old history on latest %s revisions", failure => {
    const current=[row("2026-09-01"),row("2026-10-08"),row("2026-10-09")];
    let latest=[row("2026-10-08"),row("2026-10-09",11)];
    if(failure==="basis")latest.forEach(item=>{item.price_basis="none";});
    if(failure==="source")latest.forEach(item=>{item.source="other";});
    if(failure==="anchor")Object.assign(latest[0],{open:8,high:8,low:8,close:8});
    if(failure==="empty")latest=[];
    if(failure==="no_overlap")latest=[row("2026-10-12")];
    const frozen=JSON.stringify(current);
    expect(()=>refreshKlineHistory(current,latest,"600519","2026-10-09")).toThrow(failure === "anchor" ? "已收盘重叠OHLC修订" : undefined);
    expect(JSON.stringify(current)).toBe(frozen);
  });
});
