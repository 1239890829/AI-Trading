import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {CardEntryRow} from "./card-entries";
import {getEventsForSymbol} from "@/lib/api";

vi.mock("@/lib/api", () => ({getEventsForSymbol: vi.fn()}));
const open = vi.hoisted(() => vi.fn());
vi.mock("@/components/detail/detail-modal", () => ({useDetailModal: () => ({open})}));
vi.mock("@/components/inspection/inspection-context", () => ({useInspection: () => ({open: vi.fn()})}));
afterEach(() => {cleanup(); vi.clearAllMocks();});

it("retains capital inspection without a second message reader or candidate polling", () => {
  render(<><CardEntryRow symbol="600991" /><CardEntryRow symbol="600992" /></>);
  expect(getEventsForSymbol).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", {name: "关联消息"})).toBeNull();
  fireEvent.click(screen.getAllByRole("button", {name: "资金"})[0]);
  expect(open).toHaveBeenCalledWith(expect.objectContaining({kind: "capital", symbol: "600991"}));
  expect(getEventsForSymbol).not.toHaveBeenCalled();
});

it("keeps each capital inspection on its own stock and cancels parent navigation", () => {
  const parent = vi.fn();
  const view = render(<div onClick={parent}><CardEntryRow symbol="600991" name="示例公司" /></div>);
  fireEvent.click(screen.getByRole("button", {name: "资金"}));
  expect(parent).not.toHaveBeenCalled();
  expect(open).toHaveBeenLastCalledWith(expect.objectContaining({kind: "capital", symbol: "600991"}));
  view.rerender(<CardEntryRow symbol="600992" />);
  fireEvent.click(screen.getByRole("button", {name: "资金"}));
  expect(open).toHaveBeenLastCalledWith(expect.objectContaining({kind: "capital", symbol: "600992"}));
});
