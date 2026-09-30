import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FadeSwap } from "./loading";

afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("FadeSwap task identity", () => {
  it("replaces old controls immediately rather than leaving the previous task actionable", () => {
    vi.useFakeTimers();
    const oldAction = vi.fn(), newAction = vi.fn();
    const {rerender} = render(<FadeSwap swapKey="a"><button onClick={oldAction}>操作A</button></FadeSwap>);
    rerender(<FadeSwap swapKey="b"><button onClick={newAction}>操作B</button></FadeSwap>);
    expect(screen.queryByText("操作A")).toBeNull();
    fireEvent.click(screen.getByText("操作B"));
    expect(newAction).toHaveBeenCalledTimes(1);
    expect(oldAction).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
  it("rapid switches keep the latest task and same-key updates retain the input node/focus", () => {
    const {rerender} = render(<FadeSwap swapKey="a"><input aria-label="输入A" /></FadeSwap>);
    rerender(<FadeSwap swapKey="b"><input aria-label="输入B" /></FadeSwap>);
    rerender(<FadeSwap swapKey="a"><input aria-label="输入A" /></FadeSwap>);
    const input = screen.getByLabelText("输入A"); input.focus();
    rerender(<FadeSwap swapKey="a"><input aria-label="输入A" /><p>本轮更新</p></FadeSwap>);
    expect(screen.getByLabelText("输入A")).toBe(input);
    expect(document.activeElement).toBe(input);
    expect(screen.queryByLabelText("输入B")).toBeNull();
  });
});
