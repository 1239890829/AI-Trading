import {afterEach, describe, expect, it} from "vitest";
import {cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import {ValidationContent} from "./validation-panel";
import type {ValidationReport, ValidationVersionGroup} from "@/lib/api/validation";

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

function comparedReport(): ValidationReport {
  const a = {...report.entries[0],return_pct:0,method_version:"同名方法",feature_version:"f1"};
  const b = {...report.entries[1],id:"intraday:2026-10-13:600127",name:"盘中样本",scope:"intraday" as const,
    state:"recorded",return_pct:-5,method_version:"同名方法",feature_version:"f1"};
  const unknown = {...report.entries[1],id:"daily:2026-10-14:600128",symbol:"600128",name:"旧版待核",
    date:"2026-10-14",method_version:null,feature_version:null};
  const groups: ValidationVersionGroup[] = [a,b,unknown].map(entry => {
    const observed = entry.return_pct == null ? 0 : 1;
    return {key:JSON.stringify([entry.scope,entry.method_version,entry.feature_version]),scope:entry.scope,
      method_version:entry.method_version,feature_version:entry.feature_version,
      version_state:entry.method_version ? "recorded" : "missing",entry_ids:[entry.id],
      selection:{count:1,observed,missing:1-observed,mean_pct:entry.return_pct,median_pct:entry.return_pct},
      daily:[{date:entry.date,count:1,observed,mean_pct:entry.return_pct,median_pct:entry.return_pct}],
      coverage:{versions:[],basis:"原版本覆盖",reasons:[{state:observed ? "observed" : "result_missing",label:observed ? "已有同口径结果" : "结果待补",count:1}]},
      window:{first_entry_date:entry.date,last_entry_date:entry.date,observed_start:observed ? entry.date:null,
        observed_end:observed ? entry.date:null,observed_days:observed}};
  });
  return {...report,entries:[a,b,unknown],version_entries:[a,b,unknown],version_groups:groups,
    selection:{count:3,observed:2,missing:1,mean_pct:-2.5,median_pct:-2.5}};
}

describe("original version A/B observation",()=>{
  it("shows both summaries and switches statistics, plots, rows and coverage together",()=>{
    render(<ValidationContent report={comparedReport()}/>);
    expect(within(screen.getByLabelText("A 原观察摘要")).getAllByText("0.00%")).toHaveLength(2);
    expect(within(screen.getByLabelText("B 原观察摘要")).getAllByText("-5.00%")).toHaveLength(2);
    expect(screen.getByText(/两组实际覆盖日不同/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"查看 A"}));
    expect(screen.getByRole("button",{name:/金健米业，/})).toBeTruthy();
    expect(screen.queryByRole("button",{name:/盘中样本，/})).toBeNull();
    expect(within(screen.getByRole("region",{name:"入选后平均涨跌幅"})).getByRole("button",{name:"10-12 0.00%，查看记录"})).toBeTruthy();
    expect(screen.getByRole("button",{name:"已有同口径结果 1"})).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"查看 B"}));
    expect(screen.queryByRole("button",{name:/金健米业，/})).toBeNull();
    expect(screen.getByRole("button",{name:/盘中样本，/})).toBeTruthy();
    expect(within(screen.getByRole("region",{name:"入选后平均涨跌幅"})).getByRole("button",{name:"10-13 -5.00%，查看记录"})).toBeTruthy();
  });

  it("keeps unknown versions and missing results separate from zero, without filtering actual fills",()=>{
    const data=comparedReport();
    render(<ValidationContent report={data}/>);
    fireEvent.change(screen.getByRole("combobox",{name:"版本 B"}),{target:{value:data.version_groups![2].key}});
    fireEvent.click(screen.getByRole("button",{name:"查看 B"}));
    expect(screen.getByRole("button",{name:/旧版待核，/})).toBeTruthy();
    expect(screen.getByText(/原版本未完整记录/)).toBeTruthy();
    expect(screen.getByRole("button",{name:"结果待补 1"})).toBeTruthy();
    expect(screen.getByRole("region",{name:"入选后平均涨跌幅"}).querySelectorAll(".validation-point")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button",{name:"实际买入"}));
    expect(screen.queryByRole("combobox",{name:"版本 A"})).toBeNull();
    expect(screen.getByText("0.0%")).toBeTruthy();
    expect(screen.getByRole("button",{name:/金健米业/})).toBeTruthy();
  });

  it("rechecks A/B identity after date, horizon or response changes",()=>{
    const data=comparedReport();
    const view=render(<ValidationContent report={data}/>);
    fireEvent.click(screen.getByRole("button",{name:"查看 A"}));
    view.rerender(<ValidationContent report={{...data,horizon:"d3_close"}}/>);
    expect(screen.getByRole("button",{name:"全部原入选"}).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button",{name:"查看 A"}));
    view.rerender(<ValidationContent report={{...data,horizon:"d3_close",version_groups:data.version_groups!.slice(1)}}/>);
    expect(screen.getByText(/所选版本已不在当前范围/)).toBeTruthy();
    expect(screen.getByRole("button",{name:"全部原入选"}).getAttribute("aria-pressed")).toBe("true");
  });
});
