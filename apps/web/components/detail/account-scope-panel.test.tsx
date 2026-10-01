import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { AccountScopePanel } from "./account-scope-panel";
vi.mock("@/lib/api", () => ({getPaperAccount: vi.fn(async () => ({total: 100})), getPaperOrders: vi.fn(async () => [])}));
vi.mock("@/lib/api/internal", () => ({getJson: vi.fn(async () => ({data: {enabled:false, note:"未启用精选影子"}}))}));
const api = await import("@/lib/api"); const internal = await import("@/lib/api/internal");
afterEach(() => {cleanup();vi.clearAllMocks();});
describe("account identity", () => {
  it("manual records never fetch or fabricate account capital", () => {
    render(<AccountScopePanel account="manual" />);
    expect(screen.getByText(/非券商验证/)).toBeTruthy();
    expect(api.getPaperAccount).not.toHaveBeenCalled();expect(internal.getJson).not.toHaveBeenCalled();
  });
  it("changing from main to daily cannot retain main capital or orders", async () => {
    const {rerender} = render(<AccountScopePanel account="paper" />);
    expect(await screen.findByText(/账户总额 100/)).toBeTruthy();
    rerender(<AccountScopePanel account="daily" />);
    expect(await screen.findByText("未启用精选影子")).toBeTruthy();
    expect(screen.queryByText(/账户总额/)).toBeNull();
    expect(internal.getJson).toHaveBeenCalledWith("/api/picks/shadow");
  });
  it("hunting reads only its scope and preserves historical evidence while disabled", async () => {
    vi.mocked(internal.getJson).mockResolvedValueOnce({meta:{provider:"fixture",is_realtime:false,is_stale:false,last_success_refresh:null,generated_at:"2026-10-01T10:00:00+08:00"}, data: {enabled:false, note:"猎场影子未启用", status:"incomplete", opportunities:1, independent_decisions:1, closed_fills:0, counts:{filled:1}, net_return_pct:null, net_median_pct:null, win_rate:null, issues:[], runtime:{state:"not_loaded", as_of:null}, records:[{id:"a", symbol:"600127", state:"filled", reason:"accepted", filled_price:10, net_return_pct:null, decision_version:"v1", entry_order_id:1}]}});
    render(<AccountScopePanel account="hunting" />);
    expect(await screen.findByText("猎场影子未启用")).toBeTruthy();
    expect(screen.getByText(/结果未成熟/)).toBeTruthy();
    expect(screen.getByText(/已成交，待退出/)).toBeTruthy();
    expect(api.getPaperAccount).not.toHaveBeenCalled();
    expect(internal.getJson).toHaveBeenCalledWith("/api/paper/hunting-shadow");
    expect(screen.queryByText(/账户总额/)).toBeNull();
  });

});
