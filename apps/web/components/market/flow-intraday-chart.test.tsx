import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FlowIntradayChart } from "@/components/market/flow-intraday-chart";
import type { FlowIntradayPoint } from "@/lib/api";

/**
 * P0-4（2026-09-11）：分钟资金图悬停不再重算 5 条 SVG path。
 *
 * 本文件**不**直接断言"memo 生效"（那属于实现细节，测不到也不该测），
 * 而是钉住改造后**渲染结果逐字节不变**——这正是 memo 化最可能改坏的地方
 * （把 yPct/toPath 挪进 useMemo 时极易改错归一化基准或 M/L 折线前缀）。
 * 悬停路径本身单独验证：导引线与 tooltip 必须跟着指针走。
 */

const PT = (t: string, main: number | null, rest: number | null = null): FlowIntradayPoint => ({
  t,
  main,
  super_: rest,
  big: rest,
  mid: rest,
  small: rest,
});

/** jsdom 的 getBoundingClientRect 全为 0，会让命中测试提前返回 ⇒ 给一个 240px 宽的假盒子。 */
function stubRect(width = 240) {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0,
    top: 0,
    right: width,
    bottom: 100,
    width,
    height: 100,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect);
}

beforeEach(() => stubRect());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("FlowIntradayChart（P0-4 渲染契约）", () => {
  it("五档各一条 path，d 用 viewBox 百分比坐标（中轴 50%、上下留 4%）", () => {
    const { container } = render(<FlowIntradayChart items={[PT("09:30", 10), PT("09:31", -10)]} />);
    const paths = container.querySelectorAll("path");
    expect(paths).toHaveLength(5);

    // maxAbs=10 ⇒ yPct(10)=50-46=4；yPct(-10)=50+46=96
    // tradingSeqFromHHMM("09:30")=0、("09:31")=1（与后端 _sina_bar_seq 同口径）
    expect(paths[0].getAttribute("d")).toBe("M0.0,4.0 L1.0,96.0");
    // 其余档全为 null ⇒ 无坐标（保持既有行为：不臆造零点）
    expect(paths[1].getAttribute("d")).toBe(" ");
  });

  it("空数据不渲染（含 0 点时也不该出现 5 条空 path）", () => {
    const { container } = render(<FlowIntradayChart items={[]} />);
    expect(container.querySelector('[data-testid="flow-intraday-chart"]')).toBeNull();
  });

  it("悬停：导引线与 tooltip 跟随指针，且 path 的 d 不变", () => {
    const items = [PT("09:30", 10), PT("09:31", -10)];
    const { container } = render(<FlowIntradayChart items={items} />);
    const before = Array.from(container.querySelectorAll("path")).map((p) => p.getAttribute("d"));

    expect(screen.queryByTestId("flow-tooltip")).toBeNull();
    // clientX=120 ⇒ seq=120，最近的是 09:31（seq=1）
    fireEvent.mouseMove(container.querySelector(".cursor-crosshair")!, { clientX: 120 });

    expect(screen.getByTestId("flow-tooltip").textContent).toContain("09:31");
    const after = Array.from(container.querySelectorAll("path")).map((p) => p.getAttribute("d"));
    expect(after).toEqual(before); // 悬停只改覆盖层，不动曲线
  });

  it("mouseleave 立即移除当前点，最后提示退出160ms后移除，重入与切点保留同一节点", async () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<FlowIntradayChart items={[PT("09:30", 10), PT("15:00", -10)]} />);
      const box = container.querySelector(".cursor-crosshair")!;
      const paths = [...container.querySelectorAll("path")].map(path => path.getAttribute("d"));
      fireEvent.mouseMove(box, { clientX: 0 });
      const tip = screen.getByTestId("flow-tooltip");
      fireEvent.mouseMove(box, { clientX: 240 });
      expect(screen.getByTestId("flow-tooltip")).toBe(tip);
      expect(tip.textContent).toContain("15:00");
      expect(tip.getAttribute("data-motion-state")).toBe("open");
      const edgeTransform = tip.style.transform;
      fireEvent.mouseLeave(box);
      expect(tip.hasAttribute("inert")).toBe(true);
      expect(tip.getAttribute("aria-hidden")).toBe("true");
      expect(tip.getAttribute("data-motion-state")).toBe("closed");
      expect(tip.style.transform).toBe(edgeTransform);
      expect(box.querySelector(".inset-y-0")).toBeNull();
      await act(async () => vi.advanceTimersByTime(80));
      fireEvent.mouseMove(box, { clientX: 0 });
      expect(screen.getByTestId("flow-tooltip")).toBe(tip);
      expect(tip.textContent).toContain("09:30");
      expect(tip.hasAttribute("inert")).toBe(false);
      await act(async () => vi.advanceTimersByTime(80));
      expect(screen.getByTestId("flow-tooltip")).toBe(tip);
      fireEvent.mouseLeave(box);
      await act(async () => vi.advanceTimersByTime(159));
      expect(screen.getByTestId("flow-tooltip")).toBe(tip);
      await act(async () => vi.advanceTimersByTime(1));
      expect(screen.queryByTestId("flow-tooltip")).toBeNull();
      expect([...container.querySelectorAll("path")].map(path => path.getAttribute("d"))).toEqual(paths);
    } finally {vi.useRealTimers();}
  });

  it("减少动效时离开立即移除提示", () => {
    vi.stubGlobal("matchMedia", () => ({matches:true, addEventListener:vi.fn(), removeEventListener:vi.fn()}));
    const {container} = render(<FlowIntradayChart items={[PT("09:30", 10)]} />);
    const box = container.querySelector(".cursor-crosshair")!;
    fireEvent.mouseMove(box, {clientX:0});
    expect(screen.getByTestId("flow-tooltip")).toBeTruthy();
    fireEvent.mouseLeave(box);
    expect(screen.queryByTestId("flow-tooltip")).toBeNull();
  });
});
