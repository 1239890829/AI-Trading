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
