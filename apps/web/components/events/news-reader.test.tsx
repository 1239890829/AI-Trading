import {afterEach, describe, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import type {ImpactEvent} from "@/lib/api";
import {NewsReader} from "./news-reader";

const calls=vi.hoisted(()=>({read:vi.fn(),history:vi.fn()}));
vi.mock("@/lib/api",()=>({getImpactEvents:calls.read,getEventHistory:calls.history}));
vi.mock("@/components/stock-link",()=>({StockLink:({symbol,children}:{symbol:string;children:React.ReactNode})=><a href={`/stock/${symbol}`}>{children}</a>}));
vi.mock("@/components/news-modal",()=>({NewsModal:({item}:{item:unknown})=>item?<div>原文已打开</div>:null}));
const base:ImpactEvent={id:1,title:"拟收购设备公司",summary:"董事会披露拟议交易，尚待批准。",url:null,source:"公司公告",source_tier:5,published_at:"2026-10-23 10:00:00",fact_kind:"fact",certainty:"proposed",category:"corporate",half_life_hours:48,source_symbol:"600000",is_active:true,directions:[],four_category:"hot",four_label:"市场热点",impact_level:"L2",tags:["公告"]};
afterEach(()=>{cleanup();calls.read.mockReset();calls.history.mockReset()});
function payload(){return {items:[{...base,selection_links:[{symbol:"600000",name:"示例公司",trade_date:"2026-10-23",scope:"daily",source_version:"v1",relation:"same_symbol",original_event_refs:[]}]},{...base,id:2,title:"国际利率决定",summary:"原始发布",source_symbol:null,impact_level:"L1"}],selectionContext:{state:"saved",items:[{symbol:"600000",trade_date:"2026-10-23"}],missing:[]},coverage:{limit:200,read_rows:200,possibly_truncated:true}}}
describe("同源快讯阅读",()=>{
 it("filters locally, keeps saved selection dates and distinguishes company phase from proven impact",async()=>{
  calls.read.mockResolvedValue(payload());render(<NewsReader/>);
  expect(await screen.findByText("拟收购设备公司")).toBeTruthy();
  expect(calls.read).toHaveBeenCalledWith(true,200,"time",true);
  fireEvent.click(screen.getByRole("button",{name:"关联入选"}));
  expect(screen.queryByText("国际利率决定")).toBeNull();expect(screen.getByText(/名单日期 2026-10-23/)).toBeTruthy();
  expect(screen.getByText(/示例公司.*同股关联/)).toBeTruthy();expect(calls.read).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button",{name:"全部快讯"}));fireEvent.click(screen.getByRole("button",{name:"重要"}));
  expect(screen.queryByText("拟收购设备公司")).toBeNull();expect(screen.getByText("国际利率决定")).toBeTruthy();
 });
 it("reads original revisions only on demand; filter and text actions do not repeat analysis",async()=>{
  calls.read.mockResolvedValue(payload());calls.history.mockResolvedValue({observations:[{id:1,title:"原披露",source:"官方",change_kind:"initial",source_published_at:null,available_at:"2026-10-23 10:00:01",summary:null,url:null},{id:2,title:"修订披露",source:"官方",change_kind:"revision",source_published_at:"2026-10-23 10:10:00",available_at:"2026-10-23 10:10:01",summary:null,url:null}],interpretations:[{id:1}]});
  render(<NewsReader/>);await screen.findByText("拟收购设备公司");expect(calls.history).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button",{name:"并购重组"}));
  fireEvent.click(screen.getByRole("button",{name:"连续记录与依据"}));expect(await screen.findByText("修订披露")).toBeTruthy();
  expect(screen.getByText(/同股关联不等于入选原因/)).toBeTruthy();expect(calls.history).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button",{name:"拟收购设备公司"}));expect(screen.getByText("原文已打开")).toBeTruthy();
 });
 it("does not fetch for a closed reader and does not turn relation failure into no relevant stocks",async()=>{
  calls.read.mockResolvedValue({...payload(),selectionContext:{state:"error",items:[],missing:["读取失败"]}});
  const view=render(<NewsReader active={false}/>);await waitFor(()=>expect(calls.read).not.toHaveBeenCalled());
  view.rerender(<NewsReader active/>);expect(await screen.findByRole("alert")).toHaveProperty("textContent",expect.stringContaining("关联入选读取失败"));
 });
 it("keeps an older cited version distinct from the current event and surfaces damaged selection coverage",async()=>{
  const value=payload();
  const link={symbol:"600000",name:"示例公司",trade_date:"2026-10-23",scope:"daily" as const,source_version:"v1",relation:"original_evidence" as const,reference_state:"different_version" as const,original_event_refs:[{event_id:1,version_id:2,observation_id:3}]};
  calls.read.mockResolvedValue({...value,items:[{...base,selection_links:[link]}],selectionContext:{state:"partial",items:[],missing:["每日名单损坏或格式未知"]}});
  render(<NewsReader/>);
  expect(await screen.findByText(/同事件不同版本/)).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain("每日名单损坏或格式未知");
  expect(screen.queryByText(/原版引用/)).toBeNull();
 });

});
