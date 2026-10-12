import {act, cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {useState} from "react";
import {PickDetailModal, type PickDetailTarget} from "./pick-detail-modal";

vi.mock("@/components/picks/pick-card", () => ({
  PickCard: ({item}: {item: {symbol: string}}) => <button>依据 {item.symbol}</button>,
  SUB_LABELS: [], fromDailyPick: (item: unknown) => item, fromIntradayStock: (item: unknown) => item,
}));
afterEach(() => {cleanup(); vi.useRealTimers(); vi.unstubAllGlobals();});

it("closes commands and focus immediately while retaining only the inert exit shell", () => {
  vi.useFakeTimers();
  function Host() {
    const [target, setTarget] = useState<PickDetailTarget>(null);
    return <><button onClick={() => setTarget({kind: "top", item: {symbol: "600825"} as never})}>入选依据</button><PickDetailModal target={target} onClose={() => setTarget(null)} /></>;
  }
  render(<Host />);
  const trigger = screen.getByRole("button", {name: "入选依据"}); trigger.focus(); fireEvent.click(trigger);
  expect(screen.getByRole("button", {name: "依据 600825"})).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "关闭"}));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("button", {name:"依据 600825"})).toBeNull();
  expect(screen.getByText("依据 600825").closest("[data-exit-visual]")).toBeTruthy();
  expect(screen.getByTestId("pick-detail-modal").closest("[inert]")).toBeTruthy();
  expect(document.activeElement).toBe(trigger);
  act(() => vi.advanceTimersByTime(160));
  expect(screen.queryByTestId("pick-detail-modal")).toBeNull();
});

it("a different pick during exit cannot revive the old operations or the old timer", () => {
  vi.useFakeTimers();
  const {rerender} = render(<PickDetailModal target={{kind: "top", item: {symbol: "600825"} as never}} onClose={() => {}} />);
  rerender(<PickDetailModal target={null} onClose={() => {}} />);
  expect(screen.queryByRole("button", {name:"依据 600825"})).toBeNull();
  rerender(<PickDetailModal target={{kind: "top", item: {symbol: "600127"} as never}} onClose={() => {}} />);
  act(() => vi.advanceTimersByTime(200));
  expect(screen.getByRole("button", {name: "依据 600127"})).toBeTruthy();
  expect(screen.queryByText("依据 600825")).toBeNull();
});

it("reduced motion closes directly without keeping a visual shell or delaying focus", () => {
  vi.stubGlobal("matchMedia", () => ({matches:true, addEventListener:()=>{}, removeEventListener:()=>{}}));
  const view = render(<PickDetailModal target={{kind:"top", item:{symbol:"600825"} as never}} onClose={() => {}} />);
  view.rerender(<PickDetailModal target={null} onClose={() => {}} />);
  expect(screen.queryByTestId("pick-detail-modal")).toBeNull();
  expect(screen.queryByText("依据 600825")).toBeNull();
});
