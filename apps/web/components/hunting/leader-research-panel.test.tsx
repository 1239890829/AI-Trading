import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LeaderResearchPanel } from "./leader-research-panel";
import { getLeaderResearch, type LeaderResearchPayload } from "@/lib/api/picks";
vi.mock("@/lib/api/picks", () => ({ getLeaderResearch: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const payload = (date = "2026-09-29"): LeaderResearchPayload => ({
  trade_date: date, version: "test", state: "not_collected", cards: [],
  collector: { state: "not_started" },
  review: { state: "missing_close_census", universe_count: null, strong_count: null,
    trend_audit_count: null, missed: [], cooled_symbols: [] },
  disclaimer: "研究观察，不构成买卖建议。", outcome_basis: "不是成交收益或策略胜率",
});

describe("持续研究", () => {
  it("uncollected and failed data cannot appear as zero misses", async () => {
    vi.mocked(getLeaderResearch).mockResolvedValue(payload());
    render(<LeaderResearchPanel />);
    expect(await screen.findByText(/未采集不代表没有机会/)).toBeTruthy();
    expect(screen.getByText(/缺少当日收盘对照/)).toBeTruthy();
    vi.mocked(getLeaderResearch).mockRejectedValue(new Error("unavailable"));
    fireEvent.change(screen.getByLabelText("研究日期"), { target: { value: "2026-09-28" } });
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText(/该对照口径下未发现/)).toBeNull();
  });
  it("date change drops an old delayed response", async () => {
    let finish: (value: LeaderResearchPayload) => void = () => {};
    vi.mocked(getLeaderResearch).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
      .mockResolvedValueOnce(payload("2026-09-28"));
    render(<LeaderResearchPanel />);
    await waitFor(() => expect(getLeaderResearch).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText("研究日期"), { target: { value: "2026-09-28" } });
    expect(await screen.findByText(/2026-09-28 · 0 只/)).toBeTruthy();
    finish(payload("2026-09-29"));
    await waitFor(() => expect(screen.queryByText(/2026-09-29 · 0 只/)).toBeNull());
  });
  it("shows reasons, unknowns and no-entry without order controls", async () => {
    const data = payload();
    data.cards = [{ symbol: "600127", name: "金健米业", state: "observing", routes: ["public_event"],
      first_seen: "2026-09-29T10:00:00", source_as_of: "2026-09-29T10:00:00", source: "fixture", pct: 10,
      stale: true, expired: false, reasons: ["正向事件关联"], unknowns: ["业务直接受益未验"],
      entry_state: "封板附近，无法假定参与", invalidation: "事件撤回", next_check: "续强",
      event_refs: [], outcomes: { d1: { state: "missing", target_date: "2026-09-30", reference_change_pct: null } } }];
    vi.mocked(getLeaderResearch).mockResolvedValue(data);
    render(<LeaderResearchPanel />);
    expect(await screen.findByText(/无法假定参与/)).toBeTruthy();
    expect(screen.getByText(/业务直接受益未验/)).toBeTruthy();
    expect(screen.getByText(/D1：缺收盘数据/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /建仓|买入/ })).toBeNull();
  });
});
