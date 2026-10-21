import {afterEach, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import type {EventInterpretationRef, NotificationItem} from "@/lib/api";
import {NotificationDetail} from "./notification-detail";

const reads = vi.hoisted(() => ({company: vi.fn(), research: vi.fn()}));
vi.mock("@/components/detail/attribution-evidence", () => ({StockAttributionPanel: (props: object) => {
  reads.company(props); return <p>本股公司资料只读入口</p>;
}}));
vi.mock("@/components/detail/tradingagents-research-panel", () => ({TradingAgentsResearchPanel: (props: object) => {
  reads.research(props); return <p>本股完整研究只读入口</p>;
}}));
afterEach(() => {cleanup(); vi.clearAllMocks();});

function notice(over: Partial<NotificationItem> = {}): NotificationItem {
  return {id:"selection-17", category:"opportunity", label:"入选观察", session:"intraday", ts:"2026-10-13T01:32:00Z",
    title:"核对原入选记录", body:"原选择理由；公司尚未确认新增订单。", symbol:"600127", url:null, score:null,
    validity:"消息撤回即失效。", references:[{event_id:17,kind:"selection",selection_source:"intraday",trade_date:"2026-10-13",
      source_id:"intraday:A",source_version:"run-A",source_as_of:"2026-10-13T01:31:59Z",recorded_at:"2026-10-13T01:32:03Z",
      evidence:{pick_basis:"原选择理由；公司尚未确认新增订单。",related_event_refs:[{event_id:888,version_id:22,observation_id:19,
        available_at:"2026-10-13T01:30:00Z",state:"active"} satisfies EventInterpretationRef]}}], ...over};
}
function mount(item: NotificationItem, open = true) {
  return render(<NotificationDetail item={item} open={open} onClose={vi.fn()} onOpenNews={vi.fn()}/>);
}

it("binds the company reader to saved financial-event references, never the notification ID or run ID", () => {
  mount(notice());
  const props = reads.company.mock.lastCall![0];
  expect(props).toMatchObject({symbol:"600127", concise:true, selectionContext:true, responseAsOf:"2026-10-13T01:31:59Z",
    recordedRefs:[{event_id:888,version_id:22,observation_id:19,state:"active"}]});
  expect(props.selectedAt).toBeUndefined();
  expect(reads.research.mock.lastCall![0]).toMatchObject({symbol:"600127",compact:true,allowStart:false});
  expect(screen.getByText("消息撤回即失效。")).toBeTruthy();
  expect(screen.queryByText(/原判定 #17/)).toBeNull();
});

it.each([
  notice({references:[]}),
  notice({category:"news",references:[]}),
  notice({category:"risk",references:[]}),
  notice({symbol:null}),
  notice({references:[{...notice().references![0],kind:"buy_point"}]}),
])("does not expand ordinary news, risks, references or unrelated conditions into company/model work: $category", item => {
  mount(item);
  expect(reads.company).not.toHaveBeenCalled();
  expect(reads.research).not.toHaveBeenCalled();
});

it("does not start any reader while closed and uses one original context when multiple selection versions coexist", () => {
  const original=notice();
  const second={...original.references![0],event_id:18,source_version:"run-B",source_as_of:"2026-10-13T02:00:00Z",
    evidence:{pick_basis:"后续选择理由",related_event_refs:[{event_id:888,version_id:33,observation_id:29,state:"active"}]}};
  const item=notice({references:[...original.references!,second]});
  const page=mount(item,false);
  expect(reads.company).not.toHaveBeenCalled();
  page.rerender(<NotificationDetail item={item} open onClose={vi.fn()} onOpenNews={vi.fn()}/>);
  expect(reads.company.mock.lastCall![0].recordedRefs).toEqual(original.references![0].evidence.related_event_refs);
  expect(reads.company.mock.lastCall![0].responseAsOf).toBe(original.references![0].source_as_of);
  fireEvent.click(screen.getByRole("button",{name:"完整依据与研究记录"}));
  expect(screen.getByText(/版本：run-A/)).toBeTruthy();
  expect(screen.getByText(/版本：run-B/)).toBeTruthy();
  expect(screen.getByText("后续选择理由")).toBeTruthy();
});

it("keeps unknown original clocks unknown and rejects unbound financial-event identities", () => {
  const ref=notice().references![0];
  mount(notice({references:[{...ref,source_as_of:"2026-10-13 09:31:59",evidence:{related_event_refs:[{event_id:17,version_id:null,observation_id:19,state:"active"}]}}]}));
  expect(reads.company.mock.lastCall![0]).toMatchObject({recordedRefs:[],responseAsOf:undefined});
  expect(reads.company.mock.lastCall![0].selectedAt).toBeUndefined();
});
