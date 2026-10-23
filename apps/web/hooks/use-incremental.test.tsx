import {afterEach, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {useIncremental} from "./use-incremental";
import {IncrementalSentinel} from "@/components/ui/incremental-sentinel";

const items = ["第一条", "第二条", "第三条", "第四条", "第五条", "第六条"];
function List({active, mounted = true, manual = false}: {active?: boolean; mounted?: boolean; manual?: boolean}) {
  const {shown, visible, sentinelRef, loadMore} = useIncremental(items, {pageSize: 2, step: 2, active});
  return mounted && <div data-testid="scroll-root">{shown.map(item => <p key={item}>{item}</p>)}
    <IncrementalSentinel sentinelRef={sentinelRef} visible={visible} total={items.length} onLoadMore={manual ? loadMore : undefined}/>
  </div>;
}
function layout() {
  let top = 5000;
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(2000);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(300);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(() => ({top, bottom: top + 1, left: 0, right: 100, width: 100, height: 1, x: 0, y: top, toJSON: () => ({})}));
  return () => {top = 200;};
}
afterEach(() => {cleanup(); vi.restoreAllMocks();});

it("binds a late-mounted sentinel on activation and keeps appending the same local list on scroll", () => {
  const near = layout();
  const view = render(<List active={false} mounted={false}/>);
  view.rerender(<List active/>);
  expect(screen.queryByText("第三条")).toBeNull();
  near();
  fireEvent.scroll(screen.getByTestId("scroll-root"));
  expect(screen.getByText("第四条")).toBeTruthy();
  fireEvent.scroll(screen.getByTestId("scroll-root"));
  expect(screen.getByText("第六条")).toBeTruthy();
  expect(screen.getByText("已全部加载（6 条）")).toBeTruthy();
});

it("unbinds immediately while inactive even when a retained exit still contains the sentinel, then rebinds on reopen", () => {
  const near = layout();
  const view = render(<List active/>);
  const root = screen.getByTestId("scroll-root");
  view.rerender(<List active={false}/>);
  near();
  fireEvent.scroll(root);
  expect(screen.queryByText("第三条")).toBeNull();
  view.rerender(<List active/>);
  expect(screen.getByText("第四条")).toBeTruthy();
  fireEvent.scroll(root);
  expect(screen.getByText("第六条")).toBeTruthy();
});

it("keeps default activation and offers an optional focusable local fallback without changing legacy sentinels", () => {
  const near = layout();
  const view = render(<List/>);
  expect(screen.queryByRole("button", {name: "继续加载"})).toBeNull();
  near(); fireEvent.scroll(screen.getByTestId("scroll-root"));
  expect(screen.getByText("第四条")).toBeTruthy();
  view.rerender(<List manual/>);
  const more = screen.getByRole("button", {name: "继续加载"});
  more.focus();
  expect(document.activeElement).toBe(more);
  fireEvent.click(more);
  expect(screen.getByText("第六条")).toBeTruthy();
  expect(screen.queryByRole("button", {name: "继续加载"})).toBeNull();
});

it("rebinds to the inner list when an initial fill first makes that list scrollable", () => {
  function GrowingList({resetKey}: {resetKey: string}) {
    const {shown, visible, sentinelRef} = useIncremental(items, {pageSize: 2, step: 2, resetKey});
    return <div data-testid="growing-list">{shown.map(item => <p key={item} data-row>{item}</p>)}
      <IncrementalSentinel sentinelRef={sentinelRef} visible={visible} total={items.length}/>
    </div>;
  }
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(function(this: HTMLElement) {
    return this.dataset.testid === "growing-list" ? this.querySelectorAll("[data-row]").length * 40 + 20 : 0;
  });
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function(this: HTMLElement) {
    return this.dataset.testid === "growing-list" ? 120 : 0;
  });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
    const root = this.parentElement;
    const top = root?.dataset.testid === "growing-list" ? 20 + root.querySelectorAll("[data-row]").length * 40 - root.scrollTop : 0;
    return {top, bottom: top + 1, left: 0, right: 100, width: 100, height: 1, x: 0, y: top, toJSON: () => ({})};
  });
  const view = render(<GrowingList resetKey="first"/>);
  const list = screen.getByTestId("growing-list");
  // Two rows fit. The one initial fill adds two more and creates inner overflow.
  expect(screen.getByText("第四条")).toBeTruthy();
  expect(screen.queryByText("第五条")).toBeNull();
  expect(list.scrollHeight).toBeGreaterThan(list.clientHeight);
  list.scrollTop = 60;
  fireEvent.scroll(list); // Element scroll does not bubble to the old window owner.
  expect(screen.getByText("第六条")).toBeTruthy();
  expect(screen.getByText("已全部加载（6 条）")).toBeTruthy();
  list.scrollTop = 0;
  view.rerender(<GrowingList resetKey="next"/>);
  expect(screen.getByText("第四条")).toBeTruthy();
  expect(screen.queryByText("第五条")).toBeNull();
  list.scrollTop = 60;
  fireEvent.scroll(list);
  expect(screen.getByText("第六条")).toBeTruthy();
});
