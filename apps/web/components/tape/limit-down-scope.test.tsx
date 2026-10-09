import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InspectionScope } from "@/components/inspection/surface-scope";
import { LimitDownTab } from "./limit-down-tab";
import { getLimitDownPool } from "@/lib/api";
import type { LimitDownRecord } from "@/types/market";

vi.mock("next/navigation", () => ({useSearchParams: () => new URLSearchParams("date=20261009")}));
vi.mock("@/lib/api", () => ({getLimitDownPool: vi.fn()}));
vi.mock("@/components/stock-link", () => ({StockLink: ({children}: {children: React.ReactNode}) => children, useStockRowNav: () => () => () => {}}));
afterEach(() => {cleanup(); vi.clearAllMocks();});

describe("date scoped down pool", () => {
  it("does not apply an older response after switching the embedded observation date", async () => {
    let finishOld!: (rows: LimitDownRecord[]) => void;
    let finishNew!: (rows: LimitDownRecord[]) => void;
    vi.mocked(getLimitDownPool).mockImplementation(date => new Promise(resolve => {if (date === "2026-10-01") finishOld = resolve; else finishNew = resolve;}));
    const row = (name: string, date: string) => ({symbol: "600127", name, trade_date: date, price: 10, change_pct: -10}) as LimitDownRecord;
    const replace = vi.spyOn(window.history, "replaceState");
    render(<InspectionScope initialSearch="date=2026-10-01"><LimitDownTab /></InspectionScope>);
    await waitFor(() => expect(getLimitDownPool).toHaveBeenCalledWith("2026-10-01"));
    fireEvent.change(screen.getByLabelText("按日期查询："), {target: {value: "2026-10-02"}});
    await waitFor(() => expect(getLimitDownPool).toHaveBeenCalledWith("2026-10-02"));
    await act(async () => {finishNew([row("新日记录", "2026-10-02")]);});
    expect(await screen.findByText("新日记录")).toBeTruthy();
    await act(async () => {finishOld([row("旧日记录", "2026-10-01")]);});
    expect(screen.queryByText("旧日记录")).toBeNull();
    expect(screen.getByRole("heading", {name: "跌停池 · 2026-10-02"})).toBeTruthy();
    expect(replace).not.toHaveBeenCalled();
    replace.mockRestore();
  });

  it("retains the queried date for a valid empty pool", async () => {
    vi.mocked(getLimitDownPool).mockResolvedValueOnce([]);
    render(<InspectionScope initialSearch="date=20261002"><LimitDownTab /></InspectionScope>);
    expect(await screen.findByText("当日暂无跌停")).toBeTruthy();
    expect(screen.getByRole("heading", {name: "跌停池 · 20261002"})).toBeTruthy();
    expect((screen.getByLabelText("按日期查询：") as HTMLInputElement).value).toBe("2026-10-02");
  });
});
