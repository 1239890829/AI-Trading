import {describe,expect,it} from "vitest";
import {clusterSelection,researchDate,unrecordedResearchClusters,validateResearchClusters} from "./research-clusters";
import {clusterFixture} from "@/test/research-clusters-fixture";

describe("research clusters contract and immutable selection",()=>{
  it("keeps official identities, source labels and genuine zero without mutating input",()=>{
    const value=clusterFixture(),before=JSON.stringify(value);
    expect(validateResearchClusters(value,{date:value.trade_date})).toBe(value);
    expect(value.clusters[0].branches[1].official_identity?.code).toBe("881126.TI");
    expect(value.clusters[1].branches[0].raw_name).toBe("“羊”字辈");
    const original=[{symbol:"000001",qualification:"original"},{symbol:"000002",qualification:"not_executable"}];
    expect(clusterSelection(original,value,"research-structure")).toEqual([original[0]]);
    expect(clusterSelection(original,value,"research-sheep")).toEqual([original[1]]);
    expect(clusterSelection(original,value,null)).toBe(original);
    expect(clusterSelection(original,value,"expired-id")).toBe(original);
    expect(JSON.stringify(value)).toBe(before);
    expect(original[1].qualification).toBe("not_executable");
  });
  it.each(["2026-02-30","bad","2026-13-01"])("rejects invalid calendar date %s",value=>expect(()=>researchDate(value)).toThrow());
  it("normalizes compact report date, retains explicit unrecorded instead of today's clusters",()=>{
    expect(researchDate("20261028")).toBe("2026-10-28");
    const value=unrecordedResearchClusters("20261023");
    expect(validateResearchClusters(value,{date:"2026-10-23",historical:true}).state).toBe("not_recorded");
  });
  it.each(["date","version","historical","board_return","identity","evidence_reference","duplicate_symbol","bad_link","missing_observed"])("rejects unsafe projection: %s",failure=>{
    const value=clusterFixture();
    const expected={date:value.trade_date,version:undefined as string|undefined,historical:false};
    if(failure==="date")expected.date="2026-10-27";
    if(failure==="version")expected.version="f".repeat(64);
    if(failure==="historical")expected.historical=true;
    if(failure==="board_return")(value.clusters[0] as unknown as {change_pct:number}).change_pct=3;
    if(failure==="identity")value.clusters[0].branches[0].official_identity!.name="错误板块";
    if(failure==="evidence_reference")value.clusters[0].branches[0].evidence_ids=["invented"];
    if(failure==="duplicate_symbol")value.clusters[0].stocks.push(value.clusters[0].stocks[0]);
    if(failure==="bad_link")value.clusters[0].evidence[0].url="javascript:alert(1)";
    if(failure==="missing_observed")value.observed_at=null;
    expect(()=>validateResearchClusters(value,expected)).toThrow(/日期、版本或证据结构不符/);
  });
});
