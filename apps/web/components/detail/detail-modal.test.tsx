import {act, cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {DetailModalProvider, useDetailModal} from "./detail-modal";
import {getEventsForSymbol, getStockAttribution} from "@/lib/api";

vi.mock("@/lib/api", () => ({getEventsForSymbol: vi.fn(), getNewsContent: vi.fn(), getStockAttribution: vi.fn()}));
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

it("selection evidence keeps original entry facts beside current explanations", async () => {
  vi.mocked(getStockAttribution).mockResolvedValue({schema_version: 1, symbol: "600825", count: 0, items: []});
  function SelectionOpener() {
    const {open} = useDetailModal();
    return <button onClick={() => open({kind: "attribution", title: "原选股依据", symbol: "600825", selectionEntry: {state: "recorded", selected_at: "2026-10-12T09:32:00+08:00", reference_price: 8, reference_change_pct: 2, quote_as_of: "2026-10-12T09:31:59+08:00", source: "daily_generation", source_version: "v1", semantics: "observation_only_not_fill"}, eventRefs: [{event_id: 18, version_id: 20, observation_id: 19, available_at: "2026-10-12T09:31:00+08:00", state: "active"}]})}>打开选股依据</button>;
  }
  render(<DetailModalProvider><SelectionOpener /></DetailModalProvider>);
  fireEvent.click(screen.getByRole("button", {name: "打开选股依据"}));
  await screen.findByText(/暂未找到关联事件/);
  expect(screen.getByLabelText("原首次加入依据").textContent).toContain("09:32:00");
  expect(screen.getByLabelText("原首次加入依据").textContent).toContain("¥8.00");
  expect(screen.getByText("选股记录事件引用")).toBeTruthy();
  expect(screen.getByText(/原解释 #20/)).toBeTruthy();
  expect(screen.queryByText(/原文链接缺失/)).toBeNull();
  expect(getEventsForSymbol).not.toHaveBeenCalled();
});
