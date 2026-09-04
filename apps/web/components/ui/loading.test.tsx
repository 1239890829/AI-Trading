import { act } from "react";
import { render } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { FadeSwap } from "./loading";

describe("FadeSwap（tab 切换 fade 过渡）", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("初始渲染：in 相直接显示内容，无冻结节点", () => {
    const { container } = render(
      <FadeSwap swapKey="a">
        <div>内容A</div>
      </FadeSwap>,
    );
    expect(container.textContent).toContain("内容A");
    expect(container.querySelector(".anim-fade-in")).not.toBeNull();
  });

  it("swapKey 变化：先 out 相冻结旧内容，120ms 后换入新内容并回到 in 相", () => {
    const { container, rerender } = render(
      <FadeSwap swapKey="a">
        <div>内容A</div>
      </FadeSwap>,
    );
    rerender(
      <FadeSwap swapKey="b">
        <div>内容B</div>
      </FadeSwap>,
    );
    // out 相：旧内容仍在（淡出中），新内容未换入
    expect(container.textContent).toContain("内容A");
    expect(container.textContent).not.toContain("内容B");
    expect(container.querySelector(".anim-fade-out")).not.toBeNull();

    act(() => vi.advanceTimersByTime(120));
    // in 相：新内容出现，旧内容卸载
    expect(container.textContent).toContain("内容B");
    expect(container.textContent).not.toContain("内容A");
    expect(container.querySelector(".anim-fade-in")).not.toBeNull();
  });

  it("out 相期间快速切回原 key：恢复 in 相且内容不变", () => {
    const { container, rerender } = render(
      <FadeSwap swapKey="a">
        <div>内容A</div>
      </FadeSwap>,
    );
    rerender(
      <FadeSwap swapKey="b">
        <div>内容B</div>
      </FadeSwap>,
    );
    rerender(
      <FadeSwap swapKey="a">
        <div>内容A</div>
      </FadeSwap>,
    );
    expect(container.querySelector(".anim-fade-in")).not.toBeNull();
    // 已取消的定时器不得再触发换内容
    act(() => vi.advanceTimersByTime(300));
    expect(container.textContent).toContain("内容A");
    expect(container.textContent).not.toContain("内容B");
  });
});
