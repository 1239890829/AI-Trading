import {afterEach,describe,expect,it,vi} from "vitest";
import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {ExecutionContent} from "./execution-validation";
import {ValidationContent} from "./validation-panel";
import type {ExecutionReport,ValidationReport} from "@/lib/api/validation";
import * as api from "@/lib/api/validation";

afterEach(() => {cleanup();vi.restoreAllMocks();});
const report:ValidationReport={start:"2026-10-12",end:"2026-10-13",scope:"all",horizon:"d1_close",selection:{count:0,observed:0,missing:0,mean_pct:null,median_pct:null},actual:{denominator:1,win_rate:0,mean_pct:-2,median_pct:-2,wins:0,losses:1,flat:0,open:0,excluded:0,legacy_fills:0,exposure_trade_days:null,episode_trade_days:null},entries:[],episodes:[],daily:[],win_curve:[],cohorts:[],basis:{actual:"人工记录",selection:"入选观察",coverage:"缺项未知",improvement:"人工确认"}};
export const execution:ExecutionReport={start:"2026-10-12",end:"2026-10-13",basis:"成交只认原委托单；执行尝试不与委托相加",limitation:"仅内部模拟，不是真实买入胜率。",accounts:[{scope:"shadow",label:"每日精选验证",orders:1,filled:1,pending:0,rejected:0,cancelled:0,unknown:0},{scope:"hunting_shadow",label:"机会执行验证",orders:1,filled:0,pending:0,rejected:1,cancelled:0,unknown:0}],orders:[{id:1,scope:"shadow",symbol:"600127",side:"buy",submitted_at:"2026-10-12T09:26:00+08:00",state:"filled",price:10,filled_price:10,quantity:100,fee:5,reason:""},{id:2,scope:"hunting_shadow",symbol:"600127",side:"buy",submitted_at:"2026-10-12T10:00:00+08:00",state:"rejected",price:10,filled_price:null,quantity:100,fee:null,reason:"capital_slots_full"}],attempts:[{id:"a",date:"2026-10-12",symbol:"600127",state:"rejected",order_id:2,order_state:"rejected",reason:"capital_slots_full",issue:"",decision_id:"d",snapshot_id:"s"}],runs:[{date:"2026-10-12",state:"executed",reason:"有运行留痕，成交以委托单为准",pick_date:"2026-10-11"},{date:"2026-10-13",state:"unknown",reason:"无可读运行留痕，不能判断是否执行",pick_date:null}]};

describe("internal execution consolidation",() => {
  it("shows existing fills and unknown run evidence without trading actions",() => {
    render(<ExecutionContent report={execution}/>);
    expect(screen.queryByRole("button",{name:/下单|快速建仓|重跑/})).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:/600127/}));
    expect(screen.getByText("5.00 元")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:/10-13/}));
    expect(screen.getByText("无可读运行留痕，不能判断是否执行")).toBeTruthy();
  });
  it("filters original orders by result without changing the account facts",() => {
    render(<ExecutionContent report={execution}/>);
    fireEvent.click(screen.getByRole("button",{name:/未通过 0/}));
    expect(screen.queryByRole("button",{name:/600127/})).toBeNull();
    expect(screen.getByText("该结果下没有委托，可点选同一结果取消筛选。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:/未通过 0/}));
    expect(screen.getByRole("button",{name:/600127/})).toBeTruthy();
    expect(screen.queryByRole("button",{name:"下一页"})).toBeNull();
  });
  it("loads execution only after selecting its view and keeps actual denominator",async () => {
    const read=vi.spyOn(api,"getExecutionValidation").mockResolvedValue(execution);
    render(<ValidationContent report={report} maintenance/>);
    expect(read).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button",{name:"执行对照"}));
    await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
    expect(screen.getByText("执行对照 · 内部模拟结果")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"实际买入"}));
    expect(screen.getByText("0.0%")).toBeTruthy();
    expect(read).toHaveBeenCalledTimes(1);
  });
});


it("does not offer or request internal execution in the user view", () => {
  const read = vi.spyOn(api, "getExecutionValidation");
  render(<ValidationContent report={report}/>);
  expect(screen.queryByRole("button", {name:"执行对照"})).toBeNull();
  expect(read).not.toHaveBeenCalled();
});
