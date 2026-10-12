import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {CardEntryRow} from "./card-entries";
import {getEventsForSymbol, type EventInterpretationRef, type SelectionEntry} from "@/lib/api";

vi.mock("@/lib/api", () => ({getEventsForSymbol: vi.fn()}));
const open = vi.hoisted(() => vi.fn());
vi.mock("@/components/detail/detail-modal", () => ({useDetailModal: () => ({open})}));
vi.mock("@/components/inspection/inspection-context", () => ({useInspection: () => ({open: vi.fn()})}));
afterEach(() => {cleanup(); vi.clearAllMocks();});

it("keeps evidence reachable without polling every candidate or checking whether its feed is empty", () => {
  render(<><CardEntryRow symbol="600991" /><CardEntryRow symbol="600992" /></>);
  expect(getEventsForSymbol).not.toHaveBeenCalled();
  expect(screen.getAllByRole("button", {name: "依据"})).toHaveLength(2);
  fireEvent.click(screen.getAllByRole("button", {name: "依据"})[0]);
  expect(open).toHaveBeenCalledWith(expect.objectContaining({kind: "attribution", symbol: "600991"}));
  expect(getEventsForSymbol).not.toHaveBeenCalled();
});

it("passes exact archived references and first-entry facts, and cancels parent navigation", () => {
  const refs: EventInterpretationRef[] = [{event_id: 18, version_id: 20, observation_id: 19, available_at: "2026-10-12T09:31:00+08:00", state: "active"}];
  const selection: SelectionEntry = {state: "recorded", selected_at: "2026-10-12T09:32:00+08:00", reference_price: 8, reference_change_pct: 2, quote_as_of: "2026-10-12T09:31:59+08:00", source: "daily_generation", source_version: "original-version", semantics: "observation_only_not_fill"};
  const parent = vi.fn();
  const view = render(<div onClick={parent}><CardEntryRow symbol="600991" name="示例公司" eventRefs={refs} selectionEntry={selection} /></div>);
  fireEvent.click(screen.getByRole("button", {name: "依据"}));
  expect(parent).not.toHaveBeenCalled();
  expect(open).toHaveBeenLastCalledWith(expect.objectContaining({symbol: "600991", eventRefs: refs, selectionEntry: selection}));
  view.rerender(<CardEntryRow symbol="600992" />);
  fireEvent.click(screen.getByRole("button", {name: "依据"}));
  expect(open).toHaveBeenLastCalledWith(expect.objectContaining({symbol: "600992", eventRefs: undefined, selectionEntry: undefined}));
});
