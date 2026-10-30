import {afterEach, describe, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import type {SavedSelectionSummary} from "@/lib/api/picks";
import {SelectionRunSummary} from "./selection-run-summary";

const summary: SavedSelectionSummary = {state:"saved",scope:"recorded_inputs_not_all_market",input_total:290,saved_total:290,omitted_total:0,
  counts:{selected:2,score_rejected:22,not_evaluated:258,input_unavailable:0,excluded:8,unknown:0},
  saved_counts:{selected:2,score_rejected:22,not_evaluated:258,input_unavailable:0,excluded:8,unknown:0},candidate_capacity:40,deep_capacity:24,reason:"原分布待核"};
afterEach(cleanup);
describe("saved input partition",()=>{
  it("exposes unassessed capacity separately from score rejection and selects a concrete category",()=>{
    const change=vi.fn();render(<SelectionRunSummary summary={summary} context={undefined} value="selected" onChange={change}/>);
    const group=screen.getByRole("group",{name:"按原输入去向筛选"});
    expect(within(group).getByRole("button",{name:"已评分落选 22"})).toBeTruthy();
    fireEvent.click(within(group).getByRole("button",{name:"容量未评估 258"}));
    expect(change).toHaveBeenCalledWith("not_evaluated");
    expect(screen.getByText(/未评估不等于低分/)).toBeTruthy();
  });
  it("uses only saved subset counts for partial history, never the plausible original distribution",()=>{
    render(<SelectionRunSummary summary={{...summary,state:"partial",saved_total:2,omitted_total:288,
      saved_counts:{selected:1,score_rejected:0,not_evaluated:1,input_unavailable:0,excluded:0,unknown:0}}} context={undefined} value="selected" onChange={()=>{}}/>);
    expect(screen.getByText(/仅保存 2 \/ 290；缺失 288/)).toBeTruthy();
    expect(screen.getByRole("button",{name:"容量未评估 1"})).toBeTruthy();
    expect(screen.queryByRole("button",{name:"容量未评估 258"})).toBeNull();
  });
  it.each([NaN,-1,2.5,259])("refuses invalid or inconsistent denominator counts: %s",bad=>{
    render(<SelectionRunSummary summary={{...summary,counts:{...summary.counts,not_evaluated:bad}}} context={undefined} value="selected" onChange={()=>{}}/>);
    expect(screen.getByRole("button",{name:"容量未评估 —"})).toBeTruthy();
    expect(screen.getByText("原分布待核")).toBeTruthy();
  });
  it("keeps ranking context distinct from execution and preserves unknown routing",()=>{
    render(<SelectionRunSummary summary={summary} context={{state:"partial",market_phase:"冰点",style:"趋势防守",style_basis:"原权重偏移",routed:null,semantics:"saved_ranking_style_not_execution_strategy"}} value="selected" onChange={()=>{}}/>);
    expect(screen.getByText("排行风格：趋势防守").title).toBe("原权重偏移");
    expect(screen.getByText(/路由状态未记录 · 执行资格另核/)).toBeTruthy();
  });
  it("retains conflicting-history reasons and unavailable input sources even when saved counts add up",()=>{
    render(<SelectionRunSummary summary={{...summary,state:"partial",reason:"原身份冲突"}} audit={{state:"partial",source_states:{events:"unavailable",hot:"available"}}} context={undefined} value="unknown" onChange={()=>{}}/>);
    expect(screen.getByText("原身份冲突")).toBeTruthy();
    expect(screen.getByText(/原行已保存，分类一致性待核/)).toBeTruthy();
    expect(screen.getByText("事件来源 不可用 · 热榜来源 可用")).toBeTruthy();
    expect(screen.queryByText(/缺失 0/)).toBeNull();
  });
});
