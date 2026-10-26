import {describe,expect,it} from "vitest";
import type {SavedMethodRunDetail,SavedMethodRunIdentity} from "@/lib/api/picks";
import {identityLocation,methodRunDay,methodRunIdentity,methodRunMatches,methodRunUrl,readMethodRunLocation} from "./method-run-location";

const identity:SavedMethodRunIdentity={method:"daily_picks",run_id:"42",trade_date:"2026-10-22",source_version:"saved-source",content_version:"a".repeat(64),strategy_version:"method-old",state:"saved"};
describe("saved method location",()=>{
  it("restores exact original identity and row while retaining the surrounding workspace",()=>{
    const initial=readMethodRunLocation("?area=research&tab=strategies","daily_picks");
    const location={...identityLocation(identity,initial),filter:"rejected" as const,row:"rejected:201"};
    const url=methodRunUrl("/agent","?area=research&tab=strategies&other=keep","daily_picks",location);
    const parsed=readMethodRunLocation(url.split("?")[1],"daily_picks");
    expect(url).toContain("other=keep");expect(url).toContain(`content_version=${identity.content_version}`);
    expect(methodRunIdentity("daily_picks",parsed)).toEqual(identity);
    expect(parsed.row).toBe("rejected:201");expect(parsed.filter).toBe("rejected");
  });
  it("preserves unknown source versions as missing state, never a fabricated version string",()=>{
    const original={...identity,source_version:null};
    const location=identityLocation(original,readMethodRunLocation("","daily_picks"));
    const url=methodRunUrl("/agent","","daily_picks",location);
    expect(url).toContain("source_version_missing=true");expect(new URLSearchParams(url.split("?")[1]).has("source_version")).toBe(false);
    expect(methodRunIdentity("daily_picks",readMethodRunLocation(url.split("?")[1],"daily_picks"))?.source_version).toBeNull();
  });
  it("does not reuse another method's batch or accept incomplete or impossible historical identity",()=>{
    const location=identityLocation(identity,readMethodRunLocation("","daily_picks"));
    const url=methodRunUrl("/agent","","daily_picks",location);
    expect(readMethodRunLocation(url.split("?")[1],"intraday_watch").runId).toBe("");
    expect(methodRunIdentity("daily_picks",{...location,contentVersion:""})).toBeNull();
    expect(methodRunIdentity("daily_picks",{...location,date:"2026-02-30"})).toBeNull();
    expect(methodRunIdentity("daily_picks",{...location,sourceVersion:"",missingSource:false})).toBeNull();
    expect(methodRunIdentity("daily_picks",{...location,missingSource:true})).toBeNull();
    expect(methodRunDay("20261022")).toBe("2026-10-22");
  });
  it("requires every page to match original method, run, date, source, content and offset",()=>{
    const detail={...identity,coverage:{offset:200,limit:200,possibly_truncated:false,history_semantics:"原范围"}} as SavedMethodRunDetail;
    expect(methodRunMatches(detail,identity,200)).toBe(true);
    for(const patch of [{run_id:"43"},{trade_date:"2026-10-23"},{source_version:null},{content_version:"b".repeat(64)},{strategy_version:"current"},{method:"intraday_watch"},{coverage:{...detail.coverage,offset:0}}])
      expect(methodRunMatches({...detail,...patch} as SavedMethodRunDetail,identity,200)).toBe(false);
  });
  it("manual research defaults to all saved rows and cannot inherit a selection-only filter",()=>{
    const method="reference:macd_golden_cross";
    expect(readMethodRunLocation("",method).filter).toBe("all");
    expect(readMethodRunLocation("?run_state=selected",method).filter).toBe("all");
    expect(readMethodRunLocation("?run_state=matched",method).filter).toBe("matched");
    expect(readMethodRunLocation("?run_state=matched","daily_picks").filter).toBe("selected");
  });
});
