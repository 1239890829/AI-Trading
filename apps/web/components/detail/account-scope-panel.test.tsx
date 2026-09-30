import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { AccountScopePanel } from "./account-scope-panel";
vi.mock("@/lib/api", () => ({getPaperAccount: vi.fn(async () => ({total: 100})), getPaperOrders: vi.fn(async () => [])}));
vi.mock("@/lib/api/internal", () => ({getJson: vi.fn(async () => ({data: {enabled:false, note:"未启用精选影子"}}))}));
const api = await import("@/lib/api"); const internal = await import("@/lib/api/internal");
afterEach(() => {cleanup();vi.clearAllMocks();});
describe("account identity", () => {
  it("manual and unadmitted hunting scopes do not fetch or fabricate account capital", () => {
    const {rerender} = render(<AccountScopePanel account="manual" />);
    expect(screen.getByText(/非券商验证/)).toBeTruthy();
    rerender(<AccountScopePanel account="hunting" />);
    expect(screen.getByText(/尚未准入/)).toBeTruthy();
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
});
