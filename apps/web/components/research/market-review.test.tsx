import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act,cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MarketReview, reviewSegmentBound } from "./market-review";
import {getThemeCatalogCompletion} from "@/lib/api/themes";
import type { ReviewData, ReviewProvenance, ReviewReportDetail } from "@/lib/api";
import {clusterFixture} from "@/test/research-clusters-fixture";

vi.mock("@/lib/api/themes",async()=>({...await vi.importActual<typeof import("@/lib/api/themes")>("@/lib/api/themes"),getThemeCatalogCompletion:vi.fn()}));
vi.mock("@/components/stock-link", () => ({StockLink: ({symbol,children}: {symbol: string;children: ReactNode}) => <a href={`/workbench?symbol=${symbol}`}>{children}</a>}));
vi.mock("@/lib/api", async () => ({...await vi.importActual<typeof import("@/lib/api")>("@/lib/api"),getReviewReport: vi.fn(),getOfficialCatalog:vi.fn(),getThemesCatalog:vi.fn()}));
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
function savedOutlook(review_state?: "saved" | "unknown", reviewed_at?: string | null): NonNullable<ReviewData["outlook"]> {
  return {state:"saved",trade_date:"2026-10-23",source:"picks.morning_brief",generated_at:"2026-10-23T08:30:00+08:00",missing:[],reason:"原保存简报",directions:[{direction:"电力",logic:"原产业逻辑",basis:"原政策依据",entry_mode:"观察",entry_basis:"原参与范围",trigger_conditions:["原确认条件"],falsify_conditions:["原失效条件"],review:{outcome:"未确认",actual_pct:0,note:"成交额条件未满足"},review_state,reviewed_at}]};
}
function auction(value: Record<string, number | null> = {total:4,judged:3,unknown:1,median_pct:0,weak_share:0,strong_share:1/3}) {
  return {trade_date:"2026-10-23",pool_date:"2026-10-22",items:[],summary:value,caveats:["一只源日期未核，保留在未判定分母"]};
}

describe("market review dated evidence",()=>{
  it("reads the embedded original cluster version without mutating it or fetching a current replacement",()=>{
    const value=report();value.data!.market.research_clusters=clusterFixture("2026-10-23");
    const before=JSON.stringify(value);render(<MarketReview report={value}/>);
    expect(screen.getByText("研究簇 · 原记录")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:/人形机器人产业链/}));
    expect(screen.getByText("共同驱动仍为候选")).toBeTruthy();
    expect(JSON.stringify(value)).toBe(before);
    expect(api.getReviewReport).not.toHaveBeenCalled();
  });
  it.each(["2026-10-23T16:00:00+08:00","invalid"])("rejects future or unverifiable cluster clocks without leaving the rejected payload visible: %s",clock=>{
    const value=report();value.data!.market.research_clusters=clusterFixture("2026-10-23");
    if(clock==="invalid")value.generated_at=clock;
    else value.data!.market.research_clusters.observed_at=clock;
    render(<MarketReview report={value}/>);
    expect(screen.queryByRole("button",{name:/人形机器人产业链/})).toBeNull();
    expect(screen.queryByText(/当时未记录研究簇/)).toBeNull();
    expect(screen.getByText(/已存研究簇未通过日期或证据核对/)).toBeTruthy();
  });
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
    expect(screen.queryByText("本报告没有可展示的精选条目；不证明该日零入选。")).toBeNull();
    expect(screen.getByRole("link",{name:/美的集团/}).getAttribute("href")).toContain("symbol=000333");
    expect(screen.getByText("逐股归因未记录，不能当作成功或失败。")).toBeTruthy();
    expect(screen.getByText("已保存成交状态").textContent).toBe("已保存成交状态 0");
    expect(api.getReviewReport).not.toHaveBeenCalled();
  });
  it("does not turn matching legacy pick dates and empty items into proof of zero selections",()=>{
    const value=report();value.trade_date="20261022";value.data!.trade_date="20261022";value.data!.market.trade_date="20261022";
    delete value.data!.market.contract_version;delete value.data!.market.provenance;
    value.data!.picks={trade_date:"20261022",combo_date:"2026-10-22",items:[],reviews:[],gaps:[]};
    render(<MarketReview report={value}/>);
    expect(screen.getByText("本报告没有可展示的精选条目；不证明该日零入选。")).toBeTruthy();
    expect(screen.queryByText("本日已保存的精选名单为空。")).toBeNull();
    expect(screen.queryByText("当日持久入选")).toBeNull();
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
    expect(screen.getByText(/原数据源按日保存的资金Top50样本/)).toBeTruthy();
    expect(screen.getByText(/不是已核同日的全市场领涨/)).toBeTruthy();
    expect(screen.getByText("强行业")).toBeTruthy();
    expect(reviewSegmentBound(value.data,"sector_boards",value.trade_date)).toBe(false);
  });
  it("shows source-bound auction coverage, genuine zero and unknown without another request",()=>{
    const value=report();value.data!.market.auction_premium=auction();
    value.data!.market.provenance!.auction_premium={...provenance(),state:"partial"};
    render(<MarketReview report={value}/>);
    const section=within(screen.getByLabelText("昨日涨停股竞价承接"));
    expect(section.getByText("可判定 / 原样本").parentElement?.textContent).toBe("可判定 / 原样本3 / 4");
    expect(section.getByText("未判定").parentElement?.textContent).toBe("未判定1");
    expect(section.getByText("竞价溢价中位数").parentElement?.textContent).toBe("竞价溢价中位数0.00%");
    expect(section.getByText("弱 / 强溢价").parentElement?.textContent).toBe("弱 / 强溢价0.00% / 33.33%");
    expect(section.getByText(/2026-10-22/)).toBeTruthy();
    expect(section.getByText(/一只源日期未核/)).toBeTruthy();
    expect(api.getReviewReport).not.toHaveBeenCalled();
  });
  it("keeps unverified saved auction values separate from same-day facts and refuses mismatched provenance",()=>{
    const value=report();value.data!.market.reported_values={auction_premium:auction()};
    value.data!.market.provenance!.auction_premium={...provenance(),state:"unverified",source_trade_date:null,source_as_of:null,reason:"原汇总缺逐行来源日"};
    const view=render(<MarketReview report={value}/>);
    const reference=within(screen.getByLabelText("原竞价采集参考"));
    expect(reference.getByText(/来源日期未核/)).toBeTruthy();
    expect(reference.getByText("3 / 4")).toBeTruthy();
    expect(screen.queryByLabelText("昨日涨停股竞价承接")).toBeNull();
    value.data!.market.provenance!.auction_premium={...provenance("2026-10-22"),state:"mismatch",requested_trade_date:"2026-10-23",reason:"来源日期不符"};
    view.rerender(<MarketReview report={value}/>);
    expect(screen.queryByLabelText("原竞价采集参考")).toBeNull();
    expect(screen.queryByText("3 / 4")).toBeNull();
    expect(screen.getByText("竞价承接：来源日期不符")).toBeTruthy();
  });
  it("does not turn entirely unknown auction observations into zero premium",()=>{
    const value=report();value.data!.market.auction_premium=auction({total:3,judged:0,unknown:3,median_pct:null,weak_share:null,strong_share:null});
    value.data!.market.provenance!.auction_premium={...provenance(),state:"partial"};
    render(<MarketReview report={value}/>);
    const section=within(screen.getByLabelText("昨日涨停股竞价承接"));
    expect(section.getByText("竞价溢价中位数").parentElement?.textContent).toBe("竞价溢价中位数未判定");
    expect(section.getByText("弱 / 强溢价").parentElement?.textContent).toBe("弱 / 强溢价未判定 / 未判定");
    expect(section.queryByText("0.00%")).toBeNull();
  });
  it("rejects impossible auction summary counts while keeping a visible reason",()=>{
    const value=report();value.data!.market.auction_premium=auction({total:3,judged:3,unknown:1,median_pct:8,weak_share:0,strong_share:1});
    value.data!.market.provenance!.auction_premium=provenance();
    render(<MarketReview report={value}/>);
    expect(screen.getByText("竞价承接：原汇总计数不一致，未展示溢价数值。")).toBeTruthy();
    expect(screen.queryByText("+8.00%")).toBeNull();
  });
  it("rejects a different auction body date even when the section provenance claims verified",()=>{
    const value=report();value.data!.market.auction_premium={...auction(),trade_date:"2026-10-22"};
    value.data!.market.provenance!.auction_premium=provenance();
    render(<MarketReview report={value}/>);
    expect(screen.getByText("竞价承接：原汇总日期与报告日期不符，未展示溢价数值。")).toBeTruthy();
    expect(screen.queryByLabelText("昨日涨停股竞价承接")).toBeNull();
    expect(screen.queryByText("3 / 4")).toBeNull();
  });
  it("shows the verified board sample scope and original identities without inventing missing fund flow",()=>{
    const value=report();value.data!.market.sector_board_summary={industry:{source:"ths",ranking_basis:"source_snapshot_change_pct",universe_count:90,snapshot_count:86,candidate_count:20,verified_count:2,missing_count:4,limit:20,reason:"同花顺行业源快照候选，非全集强弱结论"}};
    value.data!.market.sector_boards!.industry=[
      {board_code:"881001.TI",name:"原行业甲",change_pct:2,main_net_yi:null,source:"ths",source_trade_date:"2026-10-23",source_as_of:"2026-10-23T15:00:00+08:00",binding_state:"verified"},
      {board_code:"881002.TI",name:"原行业乙",change_pct:-1,main_net_yi:0,source:"ths",source_trade_date:"2026-10-23",source_as_of:"2026-10-23T15:00:00+08:00",binding_state:"verified"},
      {board_code:"881003.TI",name:"异日行业",change_pct:9,main_net_yi:5,source:"ths",source_trade_date:"2026-10-22",source_as_of:"2026-10-22T15:00:00+08:00",binding_state:"verified"},
    ];
    render(<MarketReview report={value}/>);
    const section=within(screen.getByLabelText("行业与概念主线"));
    expect(section.getByText(/已核验 2 条/)).toBeTruthy();
    expect(section.getByText(/原目录 90 条/)).toBeTruthy();
    expect(section.getByText(/非全市场/)).toBeTruthy();
    expect(section.getByText("原行业甲")).toBeTruthy();expect(section.getByText(/881001.TI/)).toBeTruthy();
    expect(section.queryByText("异日行业")).toBeNull();
    expect(section.getByText("未提供")).toBeTruthy();expect(section.getByText("+0.0亿")).toBeTruthy();
    fireEvent.click(section.getByRole("button",{name:"跌幅优先"}));
    expect(section.getAllByRole("listitem")[0].textContent).toContain("原行业乙");
  });
  it("accepts an independently date-labelled official daily row without inventing an intraday clock",()=>{
    const value=report();value.data!.market.provenance!.sector_boards={...provenance(),state:"partial",source_as_of:null};
    value.data!.market.sector_board_summary={industry:{source:"ths_official_kline",ranking_basis:"daily_change_pct",universe_count:90,snapshot_count:86,candidate_count:20,verified_count:1,missing_count:4}};
    value.data!.market.sector_boards!.industry=[{board_code:"881001.TI",name:"原行业日线",change_pct:2,main_net_yi:null,source:"ths_official_kline",source_trade_date:"2026-10-23",source_date_ms:Date.parse("2026-10-23T00:00:00+08:00"),source_as_of:null,binding_state:"partial"}];
    render(<MarketReview report={value}/>);
    const section=within(screen.getByLabelText("行业与概念主线"));
    expect(section.getByText("原行业日线")).toBeTruthy();
    expect(section.getByText("881001.TI · 同花顺")).toBeTruthy();
    expect(section.getByText("未提供")).toBeTruthy();
    expect(section.queryByTitle(/来源时刻/)).toBeNull();
  });
  it.each([
    {label:"wrong official date tag",extra:{source_date_ms:Date.parse("2026-10-22T00:00:00+08:00"),source_as_of:null}},
    {label:"timezone-free clock",extra:{source_as_of:"2026-10-23T15:00:00"}},
    {label:"future clock",extra:{source_as_of:"2026-10-23T16:00:00+08:00"}},
    {label:"different source",extra:{source:"other"}},
  ])("rejects a board with $label even when its parent section says verified",({extra})=>{
    const value=report();value.data!.market.sector_board_summary={industry:{source:"ths",snapshot_count:1,universe_count:1,candidate_count:1,verified_count:1,missing_count:0}};
    value.data!.market.sector_boards!.industry=[{board_code:"881001.TI",name:"未核原行",change_pct:9,main_net_yi:null,source:"ths",source_trade_date:"2026-10-23",source_as_of:"2026-10-23T15:00:00+08:00",binding_state:"verified",...extra}];
    render(<MarketReview report={value}/>);
    const section=within(screen.getByLabelText("行业与概念主线"));
    expect(section.queryByText("未核原行")).toBeNull();
    expect(section.getByText(/1 条原行的日期或来源凭据不符/)).toBeTruthy();
  });
  it("labels the daily breadth basis and coverage while leaving missing limit counts unknown",()=>{
    const value=report();value.data!.market.provenance!.breadth={...provenance(),state:"partial",source_as_of:null};
    value.data!.market.breadth={up:4000,down:1500,flat:67,total:5567,total_amount:null,basis:"复权日线对照，非官方实时涨跌幅",amount_basis:"原始同日成交额仅有0笔有效记录，未汇总为0",source_day:"2026-10-23",coverage:{verified_count:5567,universe_count:5568,missing_count:1}};
    render(<MarketReview report={value}/>);
    const section=within(screen.getByLabelText("盘面事实"));
    expect(section.getByText(/复权日线对照，非官方实时涨跌幅 · 已核 5,567 \/ 5,568 家，未核 1 家/)).toBeTruthy();
    expect(section.getByText("涨停 / 跌停").parentElement?.textContent).toBe("涨停 / 跌停未记录 / 未记录");
    expect(section.getByText("成交额").parentElement?.textContent).toBe("成交额--");
    expect(section.getByText("成交额口径：原始同日成交额仅有0笔有效记录，未汇总为0")).toBeTruthy();
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
    const value=report();value.data!.outlook=savedOutlook();
    render(<MarketReview report={value}/>);
    expect(screen.getByText("原确认条件")).toBeTruthy();expect(screen.getByText("原失效条件")).toBeTruthy();
    expect(screen.getByText(/已存后验回看（记录时点未核）：未确认 · 板块 0.00%/)).toBeTruthy();
    expect(screen.getByText(/不是本次新生成的明日预测/)).toBeTruthy();
    expect(api.getReviewReport).not.toHaveBeenCalled();
  });
  it("shows the independently saved outlook review clock in Beijing time",()=>{
    const value=report();value.data!.outlook=savedOutlook("saved","2026-10-23T07:20:00Z");
    value.data!.outlook.directions[0].trigger_conditions=null;value.data!.outlook.directions[0].falsify_conditions=null;
    render(<MarketReview report={value}/>);
    expect(screen.getByText(/已存后验回看（回看于 2026-10-23 15:20）：未确认 · 板块 0.00%/)).toBeTruthy();
    expect(screen.queryByText(/已存后验回看（记录时点未核）/)).toBeNull();
    expect(screen.getByText("原记录未列确认条件。")).toBeTruthy();expect(screen.getByText("原记录未列失效条件。")).toBeTruthy();
    expect(api.getReviewReport).not.toHaveBeenCalled();
  });
  it.each([
    {label:"missing review clock",state:"saved" as const,at:undefined},
    {label:"null review clock",state:"saved" as const,at:null},
    {label:"invalid review clock",state:"saved" as const,at:"not-a-time"},
    {label:"timezone-free review clock",state:"saved" as const,at:"2026-10-23T15:20:00"},
    {label:"unverified clock despite a timestamp",state:"unknown" as const,at:"2026-10-23T07:20:00Z"},
  ])("keeps the review moment unknown for $label",({state,at})=>{
    const value=report();value.data!.outlook=savedOutlook(state,at);
    render(<MarketReview report={value}/>);
    expect(screen.getByText(/已存后验回看（记录时点未核）：未确认/)).toBeTruthy();
    expect(screen.queryByText(/已存后验回看（回看于/)).toBeNull();
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


it("复盘只显示原冻结行业映射，原标签及未映射仍保留，不读今日目录",()=>{
  const value=report();
  value.data!.market.themes=[];value.data!.market.provenance!.themes={...provenance(),state:"unverified",source_trade_date:null};
  value.data!.market.reported_values={themes:[
    {theme:"汽车零部件",catalog_mapping_state:"exact",exact_catalog:{code:"881126.TI",name:"汽车零部件",source:"ths_official",basis:"exact_name",kind:"industry",catalog_version:"a".repeat(64),observed_at:"2026-10-23T15:29:59+08:00"}},
    {theme:"具身智能",catalog_mapping_state:"unmapped",exact_catalog:null},
    {theme:"原始旧标签"},
  ]};
  render(<MarketReview report={value}/>);
  expect(screen.getByText("原采集标签 · 当日归属未核")).toBeTruthy();
  expect(screen.getByTitle(/同花顺正式行业 · 汽车零部件 · 881126.TI/)).toBeTruthy();
  expect(screen.getByText("具身智能")).toBeTruthy();
  expect(screen.getByText("原标签 · 无正式目录对应")).toBeTruthy();
  expect(screen.getByText("原标签 · 当时未记录映射")).toBeTruthy();
  expect(api.getOfficialCatalog).not.toHaveBeenCalled();expect(api.getThemesCatalog).not.toHaveBeenCalled();
});
it("报告完成后才观察到的目录映射不升级成原报告身份",()=>{
  const value=report();
  value.data!.market.themes=[{theme:"机器人概念",catalog_mapping_state:"exact",exact_catalog:{code:"885517.TI",name:"机器人概念",source:"ths_official",basis:"exact_name",kind:"concept",catalog_version:"a".repeat(64),observed_at:"2026-10-24T12:00:00+08:00"}}];
  render(<MarketReview report={value}/>);
  expect(screen.getByText("机器人概念")).toBeTruthy();expect(screen.queryByText("同花顺 · 885517.TI")).toBeNull();
  expect(api.getOfficialCatalog).not.toHaveBeenCalled();
});


it("旧报告只旁列当前同名参考，可信冻结映射优先且不借当前指标",()=>{
  const value=report();
  value.data!.market.themes=[{theme:"汽车零部件"},{theme:"冷链物流",catalog_mapping_state:"exact",exact_catalog:{code:"885502.TI",name:"冷链物流",source:"ths_official",basis:"exact_name",kind:"concept",catalog_version:"b".repeat(64),observed_at:"2026-10-23T15:29:59+08:00"}}];
  value.current_theme_catalog={scope:"current_directory_reference",catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00",items:[
    {theme:"汽车零部件",catalog_mapping_state:"exact",exact_catalog:{code:"881126.TI",name:"汽车零部件",source:"ths_official",basis:"exact_name",kind:"industry",catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00"}},
    {theme:"冷链物流",catalog_mapping_state:"exact",exact_catalog:{code:"885502.TI",name:"冷链物流",source:"ths_official",basis:"exact_name",kind:"concept",catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00"}},
  ]};
  const before=JSON.stringify(value.data);
  render(<MarketReview report={value}/>);
  expect(screen.getByText("当前目录同名参考，非当时归属 · 881126.TI")).toBeTruthy();
  expect(screen.getByText("同花顺 · 885502.TI")).toBeTruthy();
  expect(screen.queryByText("当前目录同名参考，非当时归属 · 885502.TI")).toBeNull();
  expect(JSON.stringify(value.data)).toBe(before);
  expect(api.getOfficialCatalog).not.toHaveBeenCalled();
});


it("目录慢读完成后自动更新独立参考，不手刷或重取完整报告",async()=>{
  const key="e".repeat(32),version="a".repeat(64);
  const value=report();value.data!.market.themes=[{theme:"汽车零部件"},{theme:"具身智能"}];
  value.current_theme_catalog={scope:"current_directory_reference",catalog_version:null,observed_at:null,completion_key:key,items:["汽车零部件","具身智能"].map(theme=>({theme,catalog_mapping_state:"unavailable" as const,exact_catalog:null,catalog_mapping_context:{reason:"catalog_read_pending",completion_key:key}}))};
  const before=JSON.stringify(value);
  let finish!: (value: Awaited<ReturnType<typeof getThemeCatalogCompletion>>) => void;
  vi.mocked(getThemeCatalogCompletion).mockReturnValueOnce(new Promise(resolve=>{finish=resolve;}));
  render(<MarketReview report={value}/>);
  await waitFor(()=>expect(getThemeCatalogCompletion).toHaveBeenCalledTimes(1));
  await act(async()=>finish({scope:"current_directory_resolution",completion_key:key,state:"available",catalog_version:version,observed_at:"2026-10-28T00:00:00+00:00",identities:{汽车零部件:{code:"881126.TI",name:"汽车零部件",source:"ths_official",basis:"exact_name",kind:"industry",catalog_version:version,observed_at:"2026-10-28T00:00:00+00:00"}},ambiguous:[],reason:null}));
  expect(await screen.findByText("当前目录同名参考，非当时归属 · 881126.TI")).toBeTruthy();
  expect(screen.getByText("当前目录无同名对应")).toBeTruthy();
  expect(api.getReviewReport).not.toHaveBeenCalled();expect(api.getOfficialCatalog).not.toHaveBeenCalled();
  expect(JSON.stringify(value)).toBe(before);
});
