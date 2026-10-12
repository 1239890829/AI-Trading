import {act, cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {DetailModalProvider, useDetailModal} from "./detail-modal";
import {getEventsForSymbol} from "@/lib/api";

vi.mock("@/lib/api", () => ({getEventsForSymbol: vi.fn(), getNewsContent: vi.fn()}));
vi.mock("@/components/ui/modal-shell", () => ({ModalShell: ({children}: {children: React.ReactNode}) => <section>{children}</section>}));
vi.mock("@/components/detail/capital-flow-panel", () => ({CapitalFlowPanel: () => null}));
vi.mock("@/components/detail/symbol-detail-context", () => ({useSymbolDetail: () => ({open: vi.fn()})}));
afterEach(() => {cleanup(); vi.resetAllMocks();});

function Openers() {
  const {open} = useDetailModal();
  return <>{["600519", "000001"].map(symbol => <button key={symbol} onClick={() => open({kind: "feed", title: symbol + "关联消息", symbol})}>打开 {symbol}</button>)}</>;
}
function host() {return render(<DetailModalProvider><Openers /></DetailModalProvider>);}

it("a first feed failure shows retry and cannot claim a valid empty set", async () => {
  vi.mocked(getEventsForSymbol).mockRejectedValueOnce(new Error("feed-offline")).mockResolvedValueOnce({symbol: "600519", themes: [], count: 0, items: []});
  host(); fireEvent.click(screen.getByRole("button", {name: "打开 600519"}));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("关联事件读取失败"));
  expect(screen.queryByText(/关联事件 · 0 条/)).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "重试关联事件"}));
  expect(await screen.findByText(/关联事件 · 0 条/)).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull(); expect(getEventsForSymbol).toHaveBeenCalledTimes(2);
});

it("a different symbol cannot inherit the old feed failure or its late reply", async () => {
  let failOld!: (error: Error) => void;
  vi.mocked(getEventsForSymbol).mockReturnValueOnce(new Promise((_resolve, reject) => {failOld = reject;})).mockResolvedValueOnce({symbol: "000001", themes: [], count: 0, items: []});
  host(); fireEvent.click(screen.getByRole("button", {name: "打开 600519"})); await act(async () => {});
  fireEvent.click(screen.getByRole("button", {name: "打开 000001"})); await screen.findByText(/关联事件 · 0 条/);
  await act(async () => {failOld(new Error("old-feed-error"));});
  expect(screen.queryByRole("alert")).toBeNull(); expect(screen.queryByText(/old-feed-error/)).toBeNull();
  expect(screen.getByText(/关联事件 · 0 条/)).toBeTruthy();
});
