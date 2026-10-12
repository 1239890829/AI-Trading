import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LonghuFlowCard, MainFlowHistoryChart, MainTurnBars } from "./fund-tab";
import type { FlowHistoryDay, TurnoverHistoryDay } from "@/lib/api";
import { getLonghu } from "@/lib/api";
vi.mock("@/lib/api", async (original) => ({ ...await original<typeof import("@/lib/api")>(), getLonghu: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("reads positive, negative, zero and missing main flow without fabricating a missing bar", () => {
  const days = [20, -80, 0, null].map((main, index) => ({date: `2026-10-${12 - index}`, main, super_: null, big: null, mid: null, small: null} as FlowHistoryDay));
  const view = render(<MainFlowHistoryChart days={days} />);
  const chart = screen.getByTestId("main-flow-history");
  const points = within(chart).getAllByRole("button", {name: /^2026-10-/});
  expect(points).toHaveLength(4);
  expect(points[0].querySelector("[data-sign=positive]")).toBeTruthy();
  expect(points[1].querySelector("[data-sign=negative]")).toBeTruthy();
  expect(points[2].querySelector(".fund-history-bar")).toBeNull();
  expect(points[3].querySelector(".fund-history-bar")).toBeNull();
  fireEvent.click(points[1]);
  expect(within(chart).getByText("-80.0亿")).toBeTruthy();
  view.rerender(<MainFlowHistoryChart days={days.map(d => ({...d}))} />);
  expect(points[1].getAttribute("aria-pressed")).toBe("true");
  fireEvent.focus(points[3]);
  expect(within(chart).getByRole("status").textContent).toContain("--");
});

it("keeps every turnover date available for touch or keyboard reading without printing all amounts in the plot", () => {
  const days = [100, null, 0].map((total_yi, index) => ({date: `2026-10-${12 - index}`, total_yi, diff_yi: null} as TurnoverHistoryDay));
  const {container} = render(<MainTurnBars days={days} />);
  const points = screen.getAllByRole("button", {name: /^2026-10-/});
  expect(points).toHaveLength(3);
  expect(points[1].querySelector(".fund-history-bar")).toBeNull();
  fireEvent.click(points[1]);
  expect(container.querySelector(".fund-history-detail")?.textContent).toContain("--");
  fireEvent.focus(points[0]);
  expect(container.querySelector(".fund-history-detail")?.textContent).toContain("100亿");
});

it("keeps dense histories readable when the plot height changes", () => {
  const days = Array.from({length: 20}, (_, i) => ({date: `2026-09-${String(30-i).padStart(2, "0")}`, main: i % 2 ? -100 : 100, super_: null, big: null, mid: null, small: null} as FlowHistoryDay));
  const {container, unmount} = render(<MainFlowHistoryChart days={days} />);
  const ticks = [...container.querySelectorAll(".fund-history-tick")].filter(t => t.textContent);
  expect(ticks).toHaveLength(5);
  expect(ticks[0].textContent).toBe("09-30");
  expect(ticks[4].textContent).toBe("09-11");
  for (const bar of container.querySelectorAll<HTMLElement>(".fund-history-bar")) {
    expect(bar.style.height.endsWith("%")).toBe(true);
    expect(parseFloat(bar.style.height)).toBeLessThanOrEqual(50);
  }
  unmount();
  const turnover = render(<MainTurnBars days={days.slice(0, 10).map(d => ({date:d.date, total_yi:100, diff_yi:0} as TurnoverHistoryDay))} />);
  expect([...turnover.container.querySelectorAll(".fund-history-tick")].filter(t => t.textContent)).toHaveLength(5);
  expect(turnover.container.querySelector<HTMLElement>(".fund-history-bar")?.style.height).toBe("90%");
});

describe("龙虎榜回退读取生命周期", () => {
  it("退出后不继续启动下一历史日请求", async () => {
    let fail!: (reason: Error) => void;
    vi.mocked(getLonghu).mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject; }))
      .mockRejectedValue(new Error("offline"));
    const view = render(<LonghuFlowCard fallbackDates={["2026-10-09", "2026-10-08"]} />);
    await waitFor(() => expect(getLonghu).toHaveBeenCalledTimes(1));
    view.unmount();
    await act(async () => fail(new Error("offline")));
    expect(getLonghu).toHaveBeenCalledTimes(1);
  });
  it("同数量日期替换也建立新读取身份，旧失败链不能继续", async () => {
    let fail!: (reason: Error) => void;
    vi.mocked(getLonghu).mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject; }))
      .mockResolvedValueOnce([]);
    const view = render(<LonghuFlowCard fallbackDates={["2026-10-08"]} />);
    await waitFor(() => expect(getLonghu).toHaveBeenCalledTimes(1));
    view.rerender(<LonghuFlowCard fallbackDates={["2026-10-09"]} />);
    await waitFor(() => expect(getLonghu).toHaveBeenCalledTimes(2));
    await screen.findByText(/此次读取未返回可计入当日/);
    await act(async () => fail(new Error("offline")));
    expect(getLonghu).toHaveBeenCalledTimes(2);
  });
});
