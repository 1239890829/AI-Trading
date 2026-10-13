import {act, cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {DetailReadStatus} from "./read-status";

afterEach(() => {cleanup(); vi.useRealTimers();});
const state = {pending: false, loaded: true, error: null, readAt: "2026-10-13T07:00:30Z"};

it("keeps read/source clocks in a drawer, retains short status and returns focus after close", async () => {
  vi.useFakeTimers();
  render(<DetailReadStatus label="K线" identity="600519:kline" state={state} status={{label: "时间待核", warning: true, quality: "stale"}} diagnostics={<p>REST基座 · 源钟2026-10-13 15:00:00</p>} />);
  expect(screen.getByRole("status").textContent).toBe("时间待核");
  expect(screen.getByRole("status").getAttribute("data-quality")).toBe("stale");
  expect(screen.queryByText(/非源时刻/)).toBeNull();
  expect(screen.queryByText(/REST基座/)).toBeNull();
  const opener = screen.getByRole("button", {name: "K线诊断"}); opener.focus();
  fireEvent.click(opener);
  const drawer = screen.getByRole("dialog", {name: "K线诊断"});
  expect(drawer.className).toContain("motion-drawer");
  expect(within(drawer).getByText(/2026-10-13 15:00:30.*非源时刻/)).toBeTruthy();
  fireEvent.click(within(drawer).getByText(/REST基座/));
  expect(screen.getByRole("dialog", {name: "K线诊断"})).toBe(drawer);
  fireEvent.click(within(drawer).getByRole("button", {name: "关闭"}));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(opener);
  await act(async () => {vi.advanceTimersByTime(200);});
});

it("an error outranks a closed label, keeps retry visible and explains retained data on demand", () => {
  const retry = vi.fn();
  render(<DetailReadStatus label="分钟" state={{...state, error: "合成读取失败"}} onRetry={retry} status={{label: "已收盘"}} />);
  expect(screen.getByRole("alert").textContent).toBe("分钟读取失败 · 保留结果");
  expect(screen.queryByText("已收盘")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "重新读取分钟"})); expect(retry).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", {name: "分钟诊断"}));
  expect(within(screen.getByRole("dialog")).getByText(/仅供参考；本次状态未确认/)).toBeTruthy();
});

it("a different security or inactive consumer closes diagnostics and does not silently reopen", () => {
  const props = {label: "分钟", state, diagnostics: <p>合成源钟</p>};
  const view = render(<DetailReadStatus {...props} identity="600519:minute" />);
  fireEvent.click(screen.getByRole("button", {name: "分钟诊断"}));
  view.rerender(<DetailReadStatus {...props} identity="000001:minute" />);
  expect(screen.queryByRole("dialog")).toBeNull();
  view.rerender(<DetailReadStatus {...props} identity="600519:minute" />);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "分钟诊断"}));
  view.rerender(<DetailReadStatus {...props} identity="600519:minute" active={false} />);
  expect(screen.queryByRole("dialog")).toBeNull();
});
