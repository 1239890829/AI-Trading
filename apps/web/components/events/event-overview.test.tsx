import {cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import {afterEach, expect, it} from "vitest";
import type {EventSummary} from "@/lib/api";
import {EventOverview} from "./event-overview";

afterEach(cleanup);
const event: EventSummary = {id:18,title:"粮价上涨，但公司原材料成本上升",summary:"原文保留：需审批，累计补贴不得超过30%。",url:null,source:"eastmoney",source_tier:3,published_at:null,fact_kind:"fact",certainty:"proposed",category:"corporate",half_life_hours:48,source_symbol:"600127",is_active:false,judge_status:"judged",interpretation_ref:{event_id:18,version_id:22,observation_id:20,state:"withdrawn",available_at:"2026-10-13T09:32:00+08:00"},directions:[
  {target_type:"theme",target:"农业",direction:1,strength:2,chain:"题材原记录",basis:"仅题材关联",matched_by:"name"},
  {target_type:"symbol",target:"600127",direction:-1,strength:1,chain:"成本压力原记录",basis:"尚需核对业务",matched_by:"source"},
]};

it("shows a short message and keeps expired objects distinct from current direction, without a graph", () => {
  const original=JSON.stringify(event);
  render(<EventOverview event={event} symbol="600127"/>);
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.getByLabelText("消息要点摘录").textContent).toContain(event.summary!);
  expect(screen.getByLabelText("关联 农业").textContent).toContain("原记录已失效");
  expect(screen.getByLabelText("关联 600127").textContent).toContain("尚需核对业务");
  expect(screen.getByText("原记录仅供复盘，不作为当前依据")).toBeTruthy();
  expect(screen.queryByText("题材原记录")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"完整判读与来源记录"}));
  expect(screen.getByText("题材原记录")).toBeTruthy();
  expect(screen.getByText("成本压力原记录")).toBeTruthy();
  expect(JSON.stringify(event)).toBe(original);
});
it("shows only supplied objects and retires open records when the event changes", () => {
  const view=render(<EventOverview event={event} symbol="600127"/>);
  fireEvent.click(screen.getByRole("button",{name:"完整判读与来源记录"}));
  expect(screen.getByText("题材原记录")).toBeTruthy();
  view.rerender(<EventOverview event={{...event,id:19,title:"另一条消息",directions:[]}}/>);
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.getByText("没有已记录的题材或个股关联。不能从标题自动推导受益股。")).toBeTruthy();
  expect(screen.queryByText("题材原记录")).toBeNull();
  expect(screen.queryByText("市场响应已确认")).toBeNull();
});
it("changing or reordering targets retires the old full record rather than applying its direction elsewhere", () => {
  const active={...event,is_active:true,interpretation_ref:{...event.interpretation_ref!,state:"active" as const}};
  const view=render(<EventOverview event={active} symbol="600127"/>);
  expect(screen.getByLabelText("关联 农业").textContent).toContain("题材线索");
  expect(screen.getByLabelText("关联 600127").textContent).toContain("规则负向线索");
  fireEvent.click(screen.getByRole("button",{name:"完整判读与来源记录"}));
  view.rerender(<EventOverview event={{...active,directions:[...event.directions].reverse()}} symbol="600127"/>);
  expect(screen.getByRole("button",{name:"完整判读与来源记录"}).getAttribute("aria-expanded")).toBe("false");
});
