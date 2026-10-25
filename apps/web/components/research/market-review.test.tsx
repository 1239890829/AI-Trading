import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MarketReview, reviewSegmentBound } from "./market-review";
import type { ReviewProvenance, ReviewReportDetail } from "@/lib/api";

vi.mock("@/components/stock-link", () => ({StockLink: ({symbol,children}: {symbol: string;children: ReactNode}) => <a href={`/workbench?symbol=${symbol}`}>{children}</a>}));
vi.mock("@/lib/api", async () => ({...await vi.importActual<typeof import("@/lib/api")>("@/lib/api"),getReviewReport: vi.fn()}));
const api = await import("@/lib/api");
afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

function provenance(day="2026-10-23"): ReviewProvenance {
  return {state:"verified",source:"saved_source",source_as_of:`${day}T15:00:00+08:00`,received_at:`${day}T15:00:03+08:00`,source_trade_date:day,requested_trade_date:day,reason:"原来源日期与报告日相符"};
}
function report(): ReviewReportDetail {
  return {
    review_id:"review-23",trade_date:"20261023",generated_at:"2026-10-23T15:30:00+08:00",methodology_version:"v1",model:{actual:"rules"},summary:"原报告摘要",action_items:[],meta_insights:[],
    dimensions:[{key:"market",title:"市场环境研判",status:"ok",findings:["原市场观察"],judgements:["等待后续确认"],gaps:[]}],
    data:{trade_date:"20261023",collected_at:"2026-10-23T15:30:00+08:00",market:{trade_date:"20261023",contract_version:"date_bound_v1",provenance:{indices:provenance(),breadth:provenance(),sentiment:provenance(),themes:provenance(),sector_boards:provenance()},indices:[{symbol:"sh000001",name:"上证指数",close:3200,change_pct:0,binding_state:"verified",source:"saved_source"}],breadth:{up:0,down:20,flat:0,total:20,limit_up:0,limit_down:2,total_amount:0},sentiment:{phase:"分歧"},themes:[],sector_boards:{industry:[{board_code:"i1",name:"强行业",change_pct:2,main_net_yi:0},{board_code:"i2",name:"弱行业",change_pct:-1,main_net_yi:null},{board_code:"i3",name:"缺涨幅行业",change_pct:null,main_net_yi:null}],concept:[{board_code:"c1",name:"原概念",change_pct:1,main_net_yi:3}]},gaps:[]},trading:{trade_date:"20261023",contract_version:"date_bound_v1",provenance:{orders:provenance(),account:provenance(),positions:provenance()},account:{name:"主模拟",cash:0},positions:[],orders:[],trade_count:0,gaps:[]},picks:{trade_date:"20261023",combo_date:"2026-10-23",items:[{symbol:"000333",name:"美的集团",score:51.8,themes:["家电"],observation_only:true,basis:"原主营与盈利依据"}],reviews:[],gaps:[]}},
  };
}

describe("market review dated evidence",()=>{
  it("requires source date and requested date, not merely the report label",()=>{
    const value=report();
    expect(reviewSegmentBound(value.data,"indices",value.trade_date)).toBe(true);
    value.data!.market.provenance!.indices.source_trade_date=null;
    expect(reviewSegmentBound(value.data,"indices",value.trade_date)).toBe(false);
    value.data!.market.provenance!.indices={...provenance(),requested_trade_date:"2026-10-22"};
    expect(reviewSegmentBound(value.data,"indices",value.trade_date)).toBe(false);
    value.data!.market.provenance!.indices={...provenance(),state:"partial"};
    expect(reviewSegmentBound(value.data,"indices",value.trade_date)).toBe(true);
  });
  it("keeps legacy collected prices out of same-day facts and retains their honest reader",()=>{
    const value=report();delete value.data!.market.contract_version;delete value.data!.market.provenance;
    render(<MarketReview report={value}/>);
    expect(screen.getByText(/旧报告未保存逐段来源时点/)).toBeTruthy();
    expect(within(screen.getByLabelText("盘面事实")).queryByText("3,200.00")).toBeNull();
    expect(screen.queryByRole("img",{name:/已记录涨跌分布/})).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"原始采集值与全部缺口"}));
    expect(screen.getByText(/上证指数 · 原采集值 3,200.00.*来源日期未核/)).toBeTruthy();
  });
  it("renders real zero and distinguishes zero orders from unknown or missing attribution",()=>{
    render(<MarketReview report={report()}/>);
    expect(screen.getByRole("img",{name:"已记录涨跌分布：上涨0家，下跌20家，平盘0家"})).toBeTruthy();
    expect(screen.getByText("0.00%")).toBeTruthy();
    expect(screen.queryByText("本日已保存的精选名单为空。")).toBeNull();
    expect(screen.getByRole("link",{name:/美的集团/}).getAttribute("href")).toContain("symbol=000333");
    expect(screen.getByText("逐股归因未记录，不能当作成功或失败。")).toBeTruthy();
    expect(screen.getByText("已保存成交状态").textContent).toBe("已保存成交状态 0");
    expect(api.getReviewReport).not.toHaveBeenCalled();
  });
  it("sorts only saved sector rows, keeps missing returns last, and switches category",()=>{
    render(<MarketReview report={report()}/>);
    const list=()=>within(screen.getByLabelText("行业与概念主线")).getAllByRole("listitem").map(x=>x.textContent);
    expect(list()[0]).toContain("强行业");expect(list()[2]).toContain("缺涨幅行业");
    fireEvent.click(screen.getByRole("button",{name:"跌幅优先"}));
    expect(list()[0]).toContain("弱行业");expect(list()[2]).toContain("缺涨幅行业");
    fireEvent.click(screen.getByRole("button",{name:"概念"}));
    expect(list()).toHaveLength(1);expect(list()[0]).toContain("原概念");
    expect(api.getReviewReport).not.toHaveBeenCalled();
  });
  it("shows an owner's dated sector sample as unverified reference, never as the full daily market",()=>{
    const value=report();value.data!.market.provenance!.sector_boards={...provenance(),state:"unverified",source:"board_flow.daily",source_as_of:null,reason:"存档缺逐行源钟"};
    value.data!.market.reported_values={sector_boards:value.data!.market.sector_boards};
    value.data!.market.sector_boards={industry:[],concept:[]};
    render(<MarketReview report={value}/>);
    expect(screen.getByText("板块采集参考")).toBeTruthy();
    expect(screen.getByText(/不是已核同日的全市场领涨/)).toBeTruthy();
    expect(screen.getByText("强行业")).toBeTruthy();
    expect(reviewSegmentBound(value.data,"sector_boards",value.trade_date)).toBe(false);
  });
  it("loads comparison only on request and refuses a different returned date",async()=>{
    const wrong=report();wrong.summary="错误日期的摘要";
    vi.mocked(api.getReviewReport).mockResolvedValue(wrong);
    render(<MarketReview report={report()} previousDate="20261022"/>);
    expect(api.getReviewReport).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button",{name:"对照上一份 · 20261022"}));
    await waitFor(()=>expect(api.getReviewReport).toHaveBeenCalledWith("20261022"));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent","对照报告日期不符，未使用该返回。");
    expect(screen.queryByText("错误日期的摘要")).toBeNull();
  });
  it("compares independently date-bound reports without inventing a consecutive trading day",async()=>{
    const previous=report();previous.trade_date="20261021";previous.data!.trade_date="20261021";previous.data!.market.trade_date="20261021";previous.data!.market.provenance!.breadth=provenance("2026-10-21");previous.data!.market.breadth.up=17;
    vi.mocked(api.getReviewReport).mockResolvedValue(previous);
    render(<MarketReview report={report()} previousDate="20261021"/>);
    fireEvent.click(screen.getByRole("button",{name:"对照上一份 · 20261021"}));
    const table=await screen.findByRole("table",{name:/20261021 → 20261023/});
    expect(within(table).getByText("17")).toBeTruthy();
    expect(screen.getByText(/不保证是相邻交易日/)).toBeTruthy();
  });
  it("does not reread a comparison while its reading perspective is hidden",async()=>{
    const value=report(), previous=report();previous.trade_date="20261022";
    vi.mocked(api.getReviewReport).mockResolvedValue(previous);
    const view=render(<MarketReview report={value} previousDate="20261022"/>);
    fireEvent.click(screen.getByRole("button",{name:"对照上一份 · 20261022"}));
    await waitFor(()=>expect(api.getReviewReport).toHaveBeenCalledTimes(1));
    view.rerender(<MarketReview report={value} previousDate="20261022" active={false}/>);
    fireEvent(document,new Event("visibilitychange"));
    expect(api.getReviewReport).toHaveBeenCalledTimes(1);
  });
  it("reads saved trigger, falsifier and actual review without generating a new outlook",()=>{
    const value=report();value.data!.outlook={state:"saved",trade_date:"2026-10-23",source:"picks.morning_brief",generated_at:"2026-10-23T08:30:00+08:00",missing:[],reason:"原保存简报",directions:[{direction:"电力",logic:"原产业逻辑",basis:"原政策依据",entry_mode:"观察",entry_basis:"原参与范围",trigger_conditions:["原确认条件"],falsify_conditions:["原失效条件"],review:{outcome:"未确认",actual_pct:0,note:"成交额条件未满足"}}]};
    render(<MarketReview report={value}/>);
    expect(screen.getByText("原确认条件")).toBeTruthy();expect(screen.getByText("原失效条件")).toBeTruthy();
    expect(screen.getByText(/已存后验回看（记录时点未核）：未确认 · 板块 0.00%/)).toBeTruthy();
    expect(screen.getByText(/不是本次新生成的明日预测/)).toBeTruthy();
    expect(api.getReviewReport).not.toHaveBeenCalled();
  });
  it("preserves the original selection binding and does not turn accepted into delivered",()=>{
    const value=report();value.data!.selection_journey={state:"saved",trade_date:"2026-10-23",source:"alert_event",reason:"按原入选身份连接",items:[{symbol:"000333",name:"美的集团",selection_source:"intraday",selection_ref:"selection-reference-original",source_version:"version-original",source_as_of:"2026-10-23T10:00:00+08:00",origin_event_id:42,basis:"原盘中入选依据",event_refs:[],notification:{in_app_recorded:true,outbox:[{id:9,channel:"in_app",state:"accepted",reason:"通道受理",accepted_at:"2026-10-23T10:00:02+08:00"}],delivery_state:"unknown",read_state:"unknown"},execution:{state:"rejected",reason:"价格不在原区间",order:null}}]};
    render(<MarketReview report={value}/>);
    expect(screen.getByText("价格不在原区间")).toBeTruthy();expect(screen.getByText("未核 / 未核")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"美的集团的原身份与通道记录"}));
    expect(screen.getByText("selection-reference-original")).toBeTruthy();expect(screen.getByText("version-original")).toBeTruthy();
    expect(screen.getByText(/in_app · accepted/)).toBeTruthy();
    expect(screen.queryByText("已送达")).toBeNull();expect(screen.queryByText("已读")).toBeNull();
  });
  it("labels current account values as collection snapshots and never borrows them for an earlier report date",()=>{
    const value=report();value.data!.trading.provenance!.account={...provenance(),state:"partial",source:"paper_account:main",source_trade_date:null};
    value.data!.trading.provenance!.positions={...provenance(),state:"partial",source:"paper_position:main",source_trade_date:null};
    const view=render(<MarketReview report={value}/>);
    fireEvent.click(screen.getByRole("button",{name:"采集时刻账户与持仓（不作历史收盘估值）"}));
    expect(screen.getByText("现金").parentElement?.textContent).toBe("现金0");
    expect(screen.getByText("该次采集的主模拟持仓为空。")).toBeTruthy();
    const historical={...value,trade_date:"20261022",data:{...value.data!,trade_date:"20261022",trading:{...value.data!.trading,trade_date:"20261022",provenance:{...value.data!.trading.provenance,account:{...value.data!.trading.provenance!.account,requested_trade_date:"2026-10-22"},positions:{...value.data!.trading.provenance!.positions,requested_trade_date:"2026-10-22"}}}}};
    view.rerender(<MarketReview report={historical}/>);
    expect(screen.queryByText("现金")).toBeNull();
    expect(screen.queryByText("该次采集的主模拟持仓为空。")).toBeNull();
  });
});
