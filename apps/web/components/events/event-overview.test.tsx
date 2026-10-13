import {cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import {afterEach, expect, it} from "vitest";
import type {EventSummary} from "@/lib/api";
import {EventOverview} from "./event-overview";

afterEach(cleanup);
const event: EventSummary = {id:18,title:"粮价上涨，但公司原材料成本上升",summary:"原文保留：需审批，累计补贴不得超过30%。",url:null,source:"eastmoney",source_tier:3,published_at:null,fact_kind:"fact",certainty:"proposed",category:"corporate",half_life_hours:48,source_symbol:"600127",is_active:false,judge_status:"judged",interpretation_ref:{event_id:18,version_id:22,observation_id:20,state:"withdrawn",available_at:"2026-10-13T09:32:00+08:00"},directions:[
  {target_type:"theme",target:"农业",direction:1,strength:2,chain:"题材原记录",basis:"仅题材关联",matched_by:"name"},
  {target_type:"symbol",target:"600127",direction:-1,strength:1,chain:"成本压力原记录",basis:"尚需核对业务",matched_by:"source"},
]};

it("makes source essentials and each scoped target basis readable without selecting nodes", () => {
  const original=JSON.stringify(event);
  render(<EventOverview event={event} symbol="600127"/>);
  expect(screen.queryByRole("tabpanel")).toBeNull();
  expect(within(screen.getByLabelText("消息要点摘录")).getByText(event.summary!)).toBeTruthy();
  expect(screen.getByRole("tab",{name:"题材 · 农业"}).textContent).toContain("题材线索");
  expect(screen.getByRole("tab",{name:"个股 · 600127"}).textContent).toContain("规则负向线索");
  expect(screen.getByRole("tab",{name:"个股 · 600127"}).textContent).toContain("尚需核对业务");
  expect(screen.getByText("未记录，未用系统时间补填")).toBeTruthy();
  expect(screen.getByText("事件已非活跃，原记录仅供核对。")).toBeTruthy();
  expect(screen.getByText("解释已撤回，不能当作有效依据。")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab",{name:"个股 · 600127"}));
  expect(within(screen.getByRole("tabpanel")).getByText(event.directions[1].basis,{selector:"blockquote"})).toBeTruthy();
  expect(JSON.stringify(event)).toBe(original);
});

it("shows only declared targets and clears old evidence when a different event has no associations", () => {
  const view=render(<EventOverview event={event} symbol="600127"/>);
  fireEvent.click(screen.getByRole("tab",{name:"题材 · 农业"}));
  expect(screen.getByRole("tabpanel").textContent).toContain("不能将题材方向套用于该股");
  view.rerender(<EventOverview event={{...event,id:19,title:"另一条消息",directions:[]}}/>);
  expect(screen.queryByRole("tabpanel")).toBeNull();
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.getByText("没有已记录的题材或个股关联。不能从标题自动推导受益股。")).toBeTruthy();
  expect(screen.queryByText("市场响应已确认")).toBeNull();
});


it("retires selected evidence when the same event's associated targets reorder", () => {
  const view = render(<EventOverview event={event} symbol="600127"/>);
  fireEvent.click(screen.getByRole("tab", {name:"题材 · 农业"}));
  expect(screen.getByRole("tabpanel").textContent).toContain("题材原记录");
  view.rerender(<EventOverview event={{...event,directions:[...event.directions].reverse()}} symbol="600127"/>);
  expect(screen.queryByRole("tabpanel")).toBeNull();
  expect(screen.getByRole("tab",{name:"个股 · 600127"}).getAttribute("aria-selected")).toBe("false");
});
