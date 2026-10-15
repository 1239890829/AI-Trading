import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  it("selects one research object at a time while retaining every condition and starting no extra read", async () => {
    const data = payload();
    const first = { symbol: "600127", name: "金健米业", state: "observing", routes: ["public_event"],
      first_seen: "2026-09-29T10:00:00", source_as_of: "2026-09-29T10:00:00", source: "fixture", pct: 10,
      stale: true, expired: false, reasons: ["第一依据", "第二依据", "第三依据", "第四依据不得省略"], unknowns: ["业务直接受益未验"],
      entry_state: "无法假定参与", invalidation: "事件撤回", next_check: "续强", event_refs: [], outcomes: {} };
    data.cards = [first, {...first, symbol: "600825", name: "新华传媒", reasons: ["第二对象原依据"]}];
    vi.mocked(getLeaderResearch).mockResolvedValue(data);
    render(<LeaderResearchPanel />);
    await screen.findByRole("link", {name:"金健米业 600127"});
    expect(screen.getByLabelText("600127研究简述").textContent).toContain("第一依据");
    expect(screen.queryByText("第四依据不得省略")).toBeNull();
    fireEvent.click(screen.getAllByRole("button",{name:"研究完整依据"})[0]);
    expect(screen.getByText("第四依据不得省略")).toBeTruthy();
    expect(screen.getByText("业务直接受益未验")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button",{name:"研究完整依据"})[1]);
    expect(screen.getAllByText("第二对象原依据").length).toBeGreaterThan(0);
    expect(screen.queryByRole("tab")).toBeNull();
    expect(getLeaderResearch).toHaveBeenCalledTimes(1);
  });

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
  it("retains every same-date research reason and outcome on refresh failure without order controls", async () => {
    const data = payload();
    data.cards = [{ symbol: "600127", name: "金健米业", state: "observing", routes: ["public_event"],
      first_seen: "2026-09-29T10:00:00", source_as_of: "2026-09-29T10:00:00", source: "fixture", pct: 10,
      stale: true, expired: false, reasons: ["正向事件关联"], unknowns: ["业务直接受益未验"],
      entry_state: "封板附近，无法假定参与", invalidation: "事件撤回", next_check: "续强",
      event_refs: [], outcomes: { d1: { state: "missing", target_date: "2026-09-30", reference_change_pct: null } } }];
    vi.mocked(getLeaderResearch).mockResolvedValue(data);
    render(<LeaderResearchPanel />);
    fireEvent.click(await screen.findByRole("button", {name: "研究完整依据"}));
    expect(within(screen.getByLabelText("入池依据").parentElement!).getByText(/无法假定参与/)).toBeTruthy();
    expect(within(screen.getByLabelText("入池依据").parentElement!).getByText(/业务直接受益未验/)).toBeTruthy();
    expect(screen.getByText(/D1：缺收盘数据/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /建仓|买入/ })).toBeNull();
    vi.mocked(getLeaderResearch).mockRejectedValue(new Error("offline"));
    fireEvent(document, new Event("visibilitychange"));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("上次"));
    expect(within(screen.getByLabelText("入池依据").parentElement!).getByText(/无法假定参与/)).toBeTruthy();
    expect(within(screen.getByLabelText("入池依据").parentElement!).getByText(/业务直接受益未验/)).toBeTruthy();
    expect(screen.getByText(/D1：缺收盘数据/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /建仓|买入/ })).toBeNull();
    vi.mocked(getLeaderResearch).mockResolvedValue(payload("2026-09-27"));
    fireEvent.change(screen.getByLabelText("研究日期"), {target: {value: "2026-09-27"}});
    expect(screen.queryByText(/无法假定参与/)).toBeNull();
    expect(await screen.findByText(/2026-09-27 · 0 只/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});


it("separates event relationships, source metadata and real clocks without inventing direct benefit", async () => {
  const data = payload();
  data.cards = [{symbol:"600127", name:"金健米业", state:"observing", routes:["public_event"], first_seen:"2026-10-13T09:40:00+08:00", source_as_of:"2026-10-13T09:39:58+08:00", source:"fixture-quote", pct:3.5, stale:false, expired:false, reasons:["已有观察"], unknowns:["实际受益待核"], entry_state:"仅参考", invalidation:"消息撤回则重新核对", next_check:"核对更新", outcomes:{}, event_refs:[{title:"财政补贴原消息", source:"fixture-news", published_at:"来源发布时间未记录", available_at:"2026-10-13T09:35:00+08:00", basis:"补贴仅限满足申报条件的企业；公司未确认获补，不代表直接受益。", link_kind:"theme"}, {title:"未知关系材料", source:"fixture-other", published_at:"未记录", available_at:"未记录", basis:"不得补推公司关联", link_kind:"unknown"}]}];
  vi.mocked(getLeaderResearch).mockResolvedValue(data);
  render(<LeaderResearchPanel />);
  fireEvent.click(await screen.findByRole("button",{name:"研究完整依据"}));
  expect(screen.getByText("仅题材成员关联，未证明个股受益",{exact:false})).toBeTruthy();
  expect(screen.getByText("关联类型待核",{exact:false})).toBeTruthy();
  expect(screen.getByText("补贴仅限满足申报条件的企业；公司未确认获补，不代表直接受益。")).toBeTruthy();
  const time=screen.getAllByRole("region",{name:"事件时点"})[0];
  expect(time.textContent).toContain("来源发布时间未记录");expect(time.textContent).toContain("2026-10-13 09:35:00");expect(time.textContent).not.toContain("fixture-news");
  expect(getLeaderResearch).toHaveBeenCalledTimes(1);
  vi.mocked(getLeaderResearch).mockResolvedValue({...data,version:"next",cards:[{...data.cards[0],event_refs:[...data.cards[0].event_refs].reverse()}]});
  fireEvent(document,new Event("visibilitychange"));await waitFor(()=>expect(getLeaderResearch).toHaveBeenCalledTimes(2));
  await waitFor(()=>expect(screen.getByRole("button",{name:"研究完整依据"}).getAttribute("aria-expanded")).toBe("false"));
  fireEvent.click(screen.getByRole("button",{name:"研究完整依据"}));expect(screen.getByText("不得补推公司关联")).toBeTruthy();
});
