import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProductionOperations } from "./production-operations";
vi.mock("@/lib/api", () => ({getWatcherState: vi.fn(async () => ({})), generatePicks: vi.fn(), generatePickReview: vi.fn(), generateMorningBrief: vi.fn(), runWatcherBeat: vi.fn(), runIntradayReview: vi.fn()}));
const api = await import("@/lib/api");
afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.clearAllMocks();});
describe("maintenance command boundary", () => {
  it("reads switches separately from running state and retains unknown timing plus raw audit without producing writes",async()=>{
    vi.mocked(api.getWatcherState).mockResolvedValueOnce({active:false,enabled:true,brief_exists:false,beat_count:2,note:"未启动，不代表开关关闭。"});
    render(<ProductionOperations/>);
    expect(await screen.findByText("运行标记：未运行")).toBeTruthy();
    expect(screen.getByText("启用开关：已启用")).toBeTruthy();
    expect(screen.getByLabelText("运行时点").textContent).toContain("未记录");
    fireEvent.click(screen.getByRole("tab",{name:"原始 JSON"}));
    expect(screen.getByLabelText("节拍原始 JSON").textContent).toContain('"brief_exists": false');
    expect(api.generatePicks).not.toHaveBeenCalled();
    expect(api.runWatcherBeat).not.toHaveBeenCalled();
  });
  it("reading and canceling confirmation never produce writes", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<ProductionOperations />);
    await waitFor(() => expect(api.getWatcherState).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", {name: "重新生成组合"}));
    expect(api.generatePicks).not.toHaveBeenCalled();
    expect(api.runWatcherBeat).not.toHaveBeenCalled();
  });
  it("serializes pending commands and marks transport failure unknown, without automatic retry", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let reject!: (e: Error) => void;
    vi.mocked(api.generatePicks).mockImplementation(() => new Promise((_, r) => {reject = r;}));
    render(<ProductionOperations />);
    fireEvent.click(screen.getByRole("button", {name: "重新生成组合"}));
    fireEvent.click(screen.getByRole("button", {name: "执行一次节拍"}));
    expect(api.generatePicks).toHaveBeenCalledTimes(1);
    expect(api.runWatcherBeat).not.toHaveBeenCalled();
    expect(screen.getByRole("button", {name: "重新生成组合"}).getAttribute("aria-busy")).toBe("true");
    expect(screen.getByRole("button", {name: "执行一次节拍"}).getAttribute("aria-busy")).toBe("false");
    expect(screen.queryByText(/已返回。原始回执/)).toBeNull();
    reject(new Error("响应丢失"));
    expect(await screen.findByText(/未确认完成：响应丢失/)).toBeTruthy();
    expect(screen.getByRole("button", {name: "重新生成组合"}).getAttribute("aria-busy")).toBe("false");
    expect(api.generatePicks).toHaveBeenCalledTimes(1);
  });
});
