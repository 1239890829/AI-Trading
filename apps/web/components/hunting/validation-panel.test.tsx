import {afterEach, describe, expect, it} from "vitest";
import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {ValidationContent} from "./validation-panel";
import type {ValidationReport} from "@/lib/api/validation";

afterEach(cleanup);
export const report: ValidationReport = {
  start:"2026-10-12",end:"2026-10-15",scope:"all",horizon:"d1_close",
  selection:{count:2,observed:1,missing:1,mean_pct:3,median_pct:3},
  actual:{denominator:1,win_rate:0,mean_pct:-2,median_pct:-2,wins:0,losses:1,flat:0,open:1,excluded:1,legacy_fills:2,exposure_trade_days:null,episode_trade_days:null},
  entries:[{id:"daily:2026-10-12:600127",date:"2026-10-12",symbol:"600127",name:"金健米业",scope:"daily",state:"recorded",selected_at:"2026-10-12T10:02:00+08:00",reference_price:10,reference_change_pct:1,return_pct:3,outcome_state:"labeled",version:"v1",reason:"原入选依据：粮食安全催化，等待承接确认",conditions:["催化失效则重新核对"],selection_refs:[],observation_calendar_days:3},
    {id:"daily:2026-10-13:600127",date:"2026-10-13",symbol:"600127",name:"金健米业",scope:"daily",state:"missing",selected_at:null,reference_price:null,reference_change_pct:null,return_pct:null,outcome_state:"no_matching_archive",version:null,reason:"原记录缺项",conditions:[],selection_refs:[],observation_calendar_days:2}],
  episodes:[{id:1,symbol:"600127",name:"金健米业",opened_at:"2026-10-12T10:03:00+08:00",open_date:"2026-10-12",closed_at:"2026-10-13T10:03:00+08:00",close_date:"2026-10-13",quantity:0,cost:1000,proceeds:980,buy_fee:0,sell_fee:0,selection_ref:"daily:2026-10-12:600127",state:"closed",return_pct:-2,holding_trade_days:2,holding_calendar_days:2,fill_ids:[1,2],issues:[]}],
  daily:[{date:"2026-10-12",count:1,observed:1,mean_pct:3,median_pct:3},{date:"2026-10-13",count:1,observed:0,mean_pct:null,median_pct:null}],
  win_curve:[{date:"2026-10-13",episode_id:1,denominator:1,win_rate:0,mean_pct:-2,median_pct:-2}],cohorts:[],
  basis:{actual:"人工记录未经券商核验",selection:"入选不是成交",coverage:"原记录缺项不倒填",improvement:"建议经人工确认和验证"},
};

describe("selection validation reading", () => {
  it("does not draw a connecting segment through an invalid archived result", () => {
    const daily = [
      {...report.daily[0],date:"2026-10-12",mean_pct:1},
      {...report.daily[0],date:"2026-10-13",mean_pct:NaN},
      {...report.daily[0],date:"2026-10-14",mean_pct:2},
    ];
    render(<ValidationContent report={{...report,daily}}/>);
    const plot=screen.getByRole("region",{name:"入选后平均涨跌幅"});
    expect(plot.querySelectorAll(".validation-point")).toHaveLength(2);
    expect(plot.querySelectorAll(".validation-line")).toHaveLength(0);
  });
  it("appends records on scroll without pagination or losing the expanded evidence", () => {
    const entries=Array.from({length:96},(_,i) => ({...report.entries[0],id:`test:${i}`,name:`样本${i}`}));
    render(<ValidationContent report={{...report,entries}}/>);
    const first=screen.getByRole("button",{name:/^样本0，/});
    fireEvent.click(first);
    expect(screen.getAllByRole("button",{name:/^样本\d+，/}).length).toBeLessThan(96);
    fireEvent.scroll(window);fireEvent.scroll(window);
    expect(screen.getAllByRole("button",{name:/^样本\d+，/})).toHaveLength(96);
    expect(first.getAttribute("aria-expanded")).toBe("true");
    expect(screen.queryByRole("button",{name:"上一页"})).toBeNull();
    expect(screen.queryByRole("button",{name:"下一页"})).toBeNull();
  });
  it("keeps cross-day same stock and opens original entry without another data request", () => {
    render(<ValidationContent report={report}/>);
    expect(screen.getAllByRole("button",{name:/金健米业/})).toHaveLength(2);
    fireEvent.click(screen.getAllByRole("button",{name:/金健米业/})[0]);
    expect(screen.getByText(/原入选依据：粮食安全/)).toBeTruthy();
    expect(screen.getByText("2026-10-12 10:02:00")).toBeTruthy();
    expect(screen.queryByText("0.00%")).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"10-12 1.00只，查看记录"}));
    expect(screen.getAllByRole("button",{name:/金健米业/})).toHaveLength(1);
  });
  it("shows zero win rate as real zero, separate from positive selection observation", () => {
    render(<ValidationContent report={report}/>);
    fireEvent.click(screen.getByRole("button",{name:"实际买入"}));
    expect(screen.getByText("0.0%")).toBeTruthy();
    expect(screen.queryByText("+3.00%")).toBeNull();
    fireEvent.keyDown(screen.getByRole("button",{name:"10-13 0.00%，查看记录"}),{key:"Enter"});
    expect(screen.getByRole("heading",{name:"金健米业 · 成交回合"})).toBeTruthy();
  });
});


it("shows the saved closing-basis gap without inventing a return or an execution result",()=>{
  const entry={...report.entries[0],return_pct:null,outcome_state:"pending",outcome_date:"2026-10-13",outcome_reason:"等待决策日 qfq日收/raw日收价格基准"};
  render(<ValidationContent report={{...report,entries:[entry]}}/>);
  fireEvent.click(screen.getByRole("button",{name:/金健米业，/}));
  expect(screen.getByText(/结果缺口：等待决策日 qfq日收\/raw日收价格基准/)).toBeTruthy();
  expect(screen.getByText(/目标收盘 2026-10-13/)).toBeTruthy();
  expect(screen.queryByText("0.00%")).toBeNull();
});
