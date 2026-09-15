import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SymbolDetailModalHost, SymbolDetailProvider } from "@/components/detail/symbol-detail-modal";
import { useSymbolDetail } from "@/components/detail/symbol-detail-context";

/**
 * 标的详情弹窗（2026-09-15 详情弹窗化）。
 *
 * 钉住的是**容器契约**，不是详情面板本身（面板另有 stock-detail.test.tsx）：
 * 开关、标题分型（个股/指数）、深链 tab 意图透传、切标的、以及
 * **工作台内回落为页内切换**这条关键分支。
 *
 * `StockDetailPanel` 被替换成探针：真面板会取数 + 建 WS + 实例化图表，
 * 在 jsdom 下既慢又不测本文件关心的东西。探针把收到的 props 暴露成 data-*。
 */

const nav = vi.hoisted(() => ({
  pathname: "/tape",
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace, back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => nav.pathname,
}));

vi.mock("@/components/stock-detail", () => ({
  StockDetailPanel: (props: { symbol: string; chartTab?: string; rightTab?: string }) => (
    <div
      data-testid="detail-panel"
      data-symbol={props.symbol}
      data-chart={props.chartTab ?? ""}
      data-right={props.rightTab ?? ""}
    />
  ),
}));

/** 测试宿主：提供 context + 渲染弹窗宿主（与 app/layout.tsx 的挂法一致）。 */
function Host() {
  const { open, close } = useSymbolDetail();
  return (
    <>
      <button type="button" data-testid="open-stock" onClick={() => open({ symbol: "600519" })}>
        stock
      </button>
      <button
        type="button"
        data-testid="open-index"
        onClick={() => open({ symbol: "sh000001" })}
      >
        index
      </button>
      <button
        type="button"
        data-testid="open-deeplink"
        onClick={() => open({ symbol: "600519", chartTab: "flow", rightTab: "trades" })}
      >
        deeplink
      </button>
      <button type="button" data-testid="close" onClick={close}>
        close
      </button>
    </>
  );
}

function renderHost() {
  return render(
    <SymbolDetailProvider>
      <Host />
      <SymbolDetailModalHost />
    </SymbolDetailProvider>,
  );
}

beforeEach(() => {
  nav.pathname = "/tape";
  nav.push.mockClear();
  nav.replace.mockClear();
});

afterEach(() => {
  cleanup();
});

describe("标的详情弹窗 · 开关与容器", () => {
  it("未打开时不渲染弹窗（不占位、不建面板）", () => {
    renderHost();
    expect(screen.queryByTestId("symbol-detail-modal")).toBeNull();
    expect(screen.queryByTestId("detail-panel")).toBeNull();
  });

  it("open 后渲染弹窗，并把标的交给同一个详情面板（复用而非复制）", () => {
    renderHost();
    fireEvent.click(screen.getByTestId("open-stock"));
    expect(screen.getByTestId("symbol-detail-modal")).toBeTruthy();
    expect(screen.getByTestId("detail-panel").getAttribute("data-symbol")).toBe("600519");
  });

  it("标题按标的分型：指数 ≠ 个股", () => {
    renderHost();
    fireEvent.click(screen.getByTestId("open-stock"));
    expect(screen.getByRole("dialog").getAttribute("aria-label")).toContain("个股详情");
    fireEvent.click(screen.getByTestId("close"));

    fireEvent.click(screen.getByTestId("open-index"));
    // 指数走同一组件（StockDetailPanel 内按 isIndexSymbol 收窄 tab），
    // 故这里钉的是**弹窗标题的语义**，避免指数被叫成"个股详情"
    expect(screen.getByRole("dialog").getAttribute("aria-label")).toContain("指数详情");
    expect(screen.getByTestId("detail-panel").getAttribute("data-symbol")).toBe("sh000001");
  });

  it("深链 tab 意图透传给面板（ct/rt 不被吞掉）", () => {
    renderHost();
    fireEvent.click(screen.getByTestId("open-deeplink"));
    const panel = screen.getByTestId("detail-panel");
    expect(panel.getAttribute("data-chart")).toBe("flow");
    expect(panel.getAttribute("data-right")).toBe("trades");
  });

  it("切换标的：同一个弹窗换内容，不叠第二层", () => {
    renderHost();
    fireEvent.click(screen.getByTestId("open-stock"));
    fireEvent.click(screen.getByTestId("open-index"));
    expect(screen.getAllByTestId("symbol-detail-modal")).toHaveLength(1);
    expect(screen.getByTestId("detail-panel").getAttribute("data-symbol")).toBe("sh000001");
  });

  it("关闭按钮 / Esc 都能关（Esc 与全站弹窗一致）", () => {
    renderHost();
    fireEvent.click(screen.getByTestId("open-stock"));
    // 2026-09-15 弹窗外壳统一后，关闭按钮是 × 图标（`aria-label="关闭"`），
    // 不再是文字按钮——按无障碍名查，而不是按可见文案查。
    fireEvent.click(screen.getByLabelText("关闭"));
    expect(screen.queryByTestId("symbol-detail-modal")).toBeNull();

    fireEvent.click(screen.getByTestId("open-stock"));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("symbol-detail-modal")).toBeNull();
  });

  it("点遮罩关闭，点面板内部不关（拖动/选中不误关）", () => {
    renderHost();
    fireEvent.click(screen.getByTestId("open-stock"));
    // 面板内部：不应关闭
    fireEvent.mouseDown(screen.getByRole("dialog"));
    expect(screen.queryByTestId("symbol-detail-modal")).not.toBeNull();
    // 遮罩本身：关闭
    fireEvent.mouseDown(screen.getByRole("presentation"));
    expect(screen.queryByTestId("symbol-detail-modal")).toBeNull();
  });
});

describe("标的详情弹窗 · 工作台内回落为页内切换", () => {
  it("已在 /workbench ⇒ 不弹窗，改为切换右栏标的（URL 是唯一真相源）", async () => {
    nav.pathname = "/workbench";
    renderHost();
    fireEvent.click(screen.getByTestId("open-stock"));

    expect(screen.queryByTestId("symbol-detail-modal")).toBeNull();
    await waitFor(() => expect(nav.replace).toHaveBeenCalledTimes(1));
    expect(nav.replace.mock.calls[0][0]).toBe("/workbench?symbol=600519");
    // 页内切换不产生历史噪音（lib/routing.ts 规则一）
    expect(nav.replace.mock.calls[0][1]).toEqual({ scroll: false });
  });

  it("工作台内深链意图一并带上（ct/rt 不丢）", async () => {
    nav.pathname = "/workbench";
    renderHost();
    fireEvent.click(screen.getByTestId("open-deeplink"));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledTimes(1));
    const url = String(nav.replace.mock.calls[0][0]);
    expect(url).toContain("symbol=600519");
    expect(url).toContain("ct=flow");
    expect(url).toContain("rt=trades");
  });

  it("非工作台页面 ⇒ 正常弹窗（回落只针对工作台）", () => {
    nav.pathname = "/market";
    renderHost();
    fireEvent.click(screen.getByTestId("open-stock"));
    expect(screen.getByTestId("symbol-detail-modal")).toBeTruthy();
    expect(nav.replace).not.toHaveBeenCalled();
  });
});
