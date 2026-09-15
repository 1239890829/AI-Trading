import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StockLink, useStockRowNav } from "@/components/stock-link";
import { SymbolDetailModalHost, SymbolDetailProvider } from "@/components/detail/symbol-detail-modal";

/**
 * 全站统一「个股链接」的**点击语义**（2026-09-15 详情弹窗化）。
 *
 * 改造要同时满足两件事，本文件就是钉这两件事：
 * 1. **点击 = 就地弹窗**（不再跳工作台，用户不丢当前页上下文）；
 * 2. **链接本体不变**（href 仍是工作台深链）——右键新窗口 / 中键 / 复制链接 /
 *    既有 `a[href^="/workbench?symbol="]` 断言继续有效。
 *
 * 第三条是修饰键：`⌘/Ctrl/Shift/Alt + 点击` 必须**不弹窗**，让浏览器按默认
 * 行为新标签打开（否则"想开新窗"的用户会拿到一个弹窗 + 原页不动）。
 *
 * ⚠️ 修饰键 / 中键用例运行时，控制台会出现若干条
 * `Not implemented: navigation to another Document` —— 那是 jsdom 对
 * "未被 preventDefault 的真实链接导航"的固有限制提示，恰好证明**默认行为被放行**
 * （正是要断言的语义）。它不是失败，不要为此把 `preventDefault` 加回去。
 */

const nav = vi.hoisted(() => ({ pathname: "/tape", push: vi.fn(), replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace, back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => nav.pathname,
}));

vi.mock("@/components/stock-detail", () => ({
  StockDetailPanel: (props: { symbol: string }) => (
    <div data-testid="detail-panel" data-symbol={props.symbol} />
  ),
}));

function RowConsumer({ symbol }: { symbol: string }) {
  const stockNav = useStockRowNav();
  return (
    <tr onClick={stockNav(symbol)} data-testid="row">
      <td>row</td>
    </tr>
  );
}

function renderWithProvider(ui: React.ReactNode) {
  return render(
    <SymbolDetailProvider>
      {ui}
      <SymbolDetailModalHost />
    </SymbolDetailProvider>,
  );
}

beforeEach(() => {
  nav.pathname = "/tape";
  nav.push.mockClear();
  nav.replace.mockClear();
});

afterEach(cleanup);

describe("StockLink · 点击弹窗、链接保留", () => {
  it("href 仍是工作台深链（新窗口/复制链接/既有断言不受影响）", () => {
    renderWithProvider(
      <StockLink symbol="600519">贵州茅台</StockLink>,
    );
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")?.startsWith("/workbench?symbol=600519")).toBe(true);
  });

  it("左键点击：打开弹窗，且不再导航到工作台", () => {
    renderWithProvider(
      <StockLink symbol="600519">贵州茅台</StockLink>,
    );
    fireEvent.click(screen.getByRole("link"));
    expect(screen.getByTestId("symbol-detail-modal")).toBeTruthy();
    expect(screen.getByTestId("detail-panel").getAttribute("data-symbol")).toBe("600519");
    // preventDefault 阻断了 Link 的默认导航 ⇒ 没有 push
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("修饰键点击：不弹窗（放行浏览器新标签行为）", () => {
    for (const modifier of ["ctrlKey", "metaKey", "shiftKey", "altKey"] as const) {
      cleanup();
      nav.push.mockClear();
      renderWithProvider(
        <StockLink symbol="600519">贵州茅台</StockLink>,
      );
      fireEvent.click(screen.getByRole("link"), { [modifier]: true });
      expect(screen.queryByTestId("symbol-detail-modal"), modifier).toBeNull();
    }
  });

  it("中键点击：不弹窗（同上）", () => {
    renderWithProvider(
      <StockLink symbol="600519">贵州茅台</StockLink>,
    );
    fireEvent.click(screen.getByRole("link"), { button: 1 });
    expect(screen.queryByTestId("symbol-detail-modal")).toBeNull();
  });

  it("指数走同一入口（StockLink 不区分个股/指数）", () => {
    renderWithProvider(
      <StockLink symbol="sh000001">上证指数</StockLink>,
    );
    fireEvent.click(screen.getByRole("link"));
    expect(screen.getByTestId("detail-panel").getAttribute("data-symbol")).toBe("sh000001");
  });
});

describe("useStockRowNav · 整行点击", () => {
  it("整行点击 = 弹窗（不再跳工作台）", () => {
    renderWithProvider(
      <table>
        <tbody>
          <RowConsumer symbol="300308" />
        </tbody>
      </table>,
    );
    fireEvent.click(screen.getByTestId("row"));
    expect(screen.getByTestId("detail-panel").getAttribute("data-symbol")).toBe("300308");
    expect(nav.push).not.toHaveBeenCalled();
  });
});
