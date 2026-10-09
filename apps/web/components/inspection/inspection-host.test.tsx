import { useEffect } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InspectionHost, InspectionProvider } from "./inspection-host";
import { useInspection } from "./inspection-context";
import { useSurfaceScope } from "./surface-scope";
import { SymbolDetailModalHost, SymbolDetailProvider } from "@/components/detail/symbol-detail-modal";
import { useSymbolDetail } from "@/components/detail/symbol-detail-context";

const spies = vi.hoisted(() => ({ start: vi.fn(), stop: vi.fn(), stockStart: vi.fn(), stockStop: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/workbench",
  useRouter: () => ({replace: spies.replace}),
  useSearchParams: () => new URLSearchParams("symbol=000001&date=2026-10-09"),
}));
vi.mock("@/components/tape/limit-down-tab", () => ({LimitDownTab: ScopeReader}));
vi.mock("@/components/tape/themes-tab", () => ({ThemesTab: ScopeReader}));
vi.mock("@/components/stock-detail", () => ({StockDetailPanel: StockReader}));

function StockReader({symbol}: {symbol: string}) {
  const {open} = useInspection();
  useEffect(() => { spies.stockStart(symbol); return () => spies.stockStop(symbol); }, [symbol]);
  return <div><p data-testid="stock-probe">{symbol}</p><button onClick={() => open({kind: "themes", date: "2026-10-02", focus: "粮食"})}>核对证券题材</button><button onClick={() => open({kind: "themes", date: "2026-10-02", focus: "覆铜板"})}>核对另一题材</button></div>;
}

function ScopeReader() {
  const {searchParams} = useSurfaceScope();
  const {open} = useSymbolDetail();
  useEffect(() => { spies.start(searchParams.get("date")); return () => spies.stop(); }, [searchParams]);
  return <div data-testid="scope-reader"><p>{searchParams.get("date")}</p><p data-testid="scope-theme">{searchParams.get("focus")}</p><button onClick={() => open({symbol: "600127"})}>核对证券</button></div>;
}

function Controls() {
  const {open, close} = useInspection();
  const {open: openSymbol} = useSymbolDetail();
  return <><button onClick={() => open({kind: "limit-down", date: "2026-10-02"})}>核对原日跌停</button><button onClick={close}>关闭旁览</button><button onClick={() => openSymbol({symbol: "600519", preferModal: true})}>打开原证券</button><button onClick={() => openSymbol({symbol: "600127", preferModal: true})}>打开相同证券</button></>;
}

function renderHost() {
  return render(<InspectionProvider><SymbolDetailProvider><Controls /><SymbolDetailModalHost /><InspectionHost /></SymbolDetailProvider></InspectionProvider>);
}

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("旁览宿主生命周期与嵌套详情", () => {
  it("未打开不挂读者，原日期不借宿主页，关闭立即停止读者再退出外壳", async () => {
    renderHost();
    expect(screen.queryByTestId("scope-reader")).toBeNull();
    expect(spies.start).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("核对原日跌停"));
    await screen.findByTestId("scope-reader");
    expect(screen.getByText("2026-10-02")).toBeTruthy();
    expect(spies.start).toHaveBeenCalledWith("2026-10-02");
    fireEvent.click(screen.getByLabelText("关闭"));
    expect(screen.queryByTestId("scope-reader")).toBeNull();
    expect(spies.stop).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.queryByTestId("inspection-modal")).toBeNull());
    expect(spies.replace).not.toHaveBeenCalled();
  });

  it("工作台旁览内证券盖在列表上方，首个Escape仅关证券并还原列表焦点", async () => {
    renderHost();
    fireEvent.click(screen.getByText("核对原日跌停"));
    const trigger = await screen.findByText("核对证券");
    trigger.focus();
    fireEvent.click(trigger);
    await screen.findByTestId("stock-probe");
    expect(screen.getAllByRole("dialog")).toHaveLength(2);
    expect(spies.replace).not.toHaveBeenCalled();
    fireEvent.keyDown(window, {key: "Escape"});
    expect(screen.queryByTestId("stock-probe")).toBeNull();
    expect(screen.getByTestId("scope-reader")).toBeTruthy();
    expect(document.activeElement).toBe(trigger);
    fireEvent.keyDown(window, {key: "Escape"});
    expect(screen.queryByTestId("scope-reader")).toBeNull();
  });

  it.each(["打开原证券", "打开相同证券"])("证券→题材→再次证券（%s）重新置顶，Escape回题材并停止证券读取", async first => {
    renderHost();
    const origin = screen.getByText(first);
    origin.focus();
    fireEvent.click(origin);
    await screen.findByTestId("stock-probe");
    fireEvent.click(screen.getByText("核对证券题材"));
    const reader = await screen.findByTestId("scope-reader");
    const trigger = screen.getByText("核对证券");
    trigger.focus();
    fireEvent.click(trigger);
    await waitFor(() => expect(screen.getByTestId("stock-probe").textContent).toBe("600127"));
    const activeShell = screen.getByTestId("symbol-detail-modal").parentElement!;
    expect(activeShell.inert).not.toBe(true);
    expect(screen.getByTestId("inspection-modal").parentElement!.inert).toBe(true);
    expect(screen.getByTestId("symbol-detail-modal").contains(document.activeElement)).toBe(true);
    expect(spies.replace).not.toHaveBeenCalled();
    fireEvent.keyDown(window, {key: "Escape"});
    expect(screen.queryByTestId("stock-probe")).toBeNull();
    expect(spies.stockStop).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("scope-reader")).toBe(reader);
    expect(document.activeElement).toBe(trigger);
    expect(screen.getByTestId("inspection-modal").parentElement!.inert).not.toBe(true);
    fireEvent.keyDown(window, {key: "Escape"});
    expect(screen.queryByTestId("scope-reader")).toBeNull();
    expect(spies.stop).toHaveBeenCalledOnce();
    expect(document.activeElement).toBe(origin);
  });

  it("证券→题材→证券→另一题材→同证券持续置顶，返回保留原日期和最终题材", async () => {
    renderHost();
    const origin = screen.getByText("打开原证券");
    origin.focus();
    fireEvent.click(origin);
    await screen.findByTestId("stock-probe");
    fireEvent.click(screen.getByText("核对证券题材"));
    await screen.findByTestId("scope-reader");
    expect(screen.getByTestId("scope-theme").textContent).toBe("粮食");
    fireEvent.click(screen.getByText("核对证券"));
    await waitFor(() => expect(screen.getByTestId("stock-probe").textContent).toBe("600127"));
    fireEvent.click(screen.getByText("核对另一题材"));
    await waitFor(() => expect(screen.getByTestId("scope-theme").textContent).toBe("覆铜板"));
    expect(screen.getByTestId("inspection-modal").parentElement!.inert).not.toBe(true);
    expect(screen.getByTestId("symbol-detail-modal").parentElement!.inert).toBe(true);
    expect(screen.getByTestId("inspection-modal").contains(document.activeElement)).toBe(true);
    const finalReader = screen.getByTestId("scope-reader");
    const trigger = screen.getByText("核对证券");
    trigger.focus();
    fireEvent.click(trigger);
    await waitFor(() => expect(screen.getByTestId("symbol-detail-modal").parentElement!.inert).not.toBe(true));
    expect(screen.getByTestId("inspection-modal").parentElement!.inert).toBe(true);
    fireEvent.keyDown(window, {key: "Escape"});
    expect(screen.queryByTestId("stock-probe")).toBeNull();
    expect(spies.stockStop).toHaveBeenCalledTimes(3);
    expect(screen.getByTestId("scope-reader")).toBe(finalReader);
    expect(screen.getByTestId("scope-theme").textContent).toBe("覆铜板");
    expect(screen.getByText("2026-10-02")).toBeTruthy();
    expect(document.activeElement).toBe(trigger);
    fireEvent.keyDown(window, {key: "Escape"});
    expect(screen.queryByTestId("scope-reader")).toBeNull();
    expect(spies.stop).toHaveBeenCalledTimes(2);
    expect(document.activeElement).toBe(origin);
    expect(spies.replace).not.toHaveBeenCalled();
  });
});
