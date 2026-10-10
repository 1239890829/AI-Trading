import {act, cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import {afterEach, describe, expect, it, vi} from "vitest";
import {getLimitUpPoolSnapshot, type LimitPoolSnapshot} from "@/lib/api";
import type {LimitUpRecord, Quote} from "@/types/market";
import {LimitUpReasonPanel} from "./limit-up-reason";
import {LimitReason} from "./limit-reason";

vi.mock("@/lib/api", () => ({getLimitUpPoolSnapshot: vi.fn()}));
afterEach(() => {cleanup(); vi.mocked(getLimitUpPoolSnapshot).mockReset();});
const quote = (symbol = "600825", date = "2026-10-09T08:00:00Z") => ({symbol, data_timestamp: date, source: "tencent"}) as Quote;
const pool = (reason: string | null = "拟收购财联社+重大资产重组") => ({trade_date:"2026-10-09", pool:[{symbol:"600825",reason,source:"ths",trade_date:"2026-10-09"}]}) as LimitPoolSnapshot<LimitUpRecord>;

describe("shared security detail limit-up attribution", () => {
  it("shows the source original with actual pool date and does not use concepts as fallback", async () => {
    vi.mocked(getLimitUpPoolSnapshot).mockResolvedValue(pool());
    render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    expect(await screen.findByText("拟收购财联社+重大资产重组")).toBeTruthy();
    expect(screen.getByText(/2026-10-09 · 同花顺/)).toBeTruthy();
    expect(screen.getByText(/非已核实的涨停因果/)).toBeTruthy();
  });
  it("distinguishes missing cause, stock outside pool, and wrong quote date", async () => {
    vi.mocked(getLimitUpPoolSnapshot).mockResolvedValue(pool(null));
    const {rerender} = render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    expect(await screen.findByText("数据源未提供涨停原因")).toBeTruthy();
    rerender(<LimitUpReasonPanel symbol="600127" quote={quote("600127")} />);
    expect(await screen.findByText(/所查池未收录该股/)).toBeTruthy();
    rerender(<LimitUpReasonPanel symbol="600825" quote={quote("600825", "2026-10-08T08:00:00Z")} />);
    expect(await screen.findByText(/日期不同/)).toBeTruthy();
    expect(screen.queryByText("拟收购财联社+重大资产重组")).toBeNull();
  });
  it("does not replace a missing source date with received_at or request all market news", async () => {
    render(<LimitUpReasonPanel symbol="600825" quote={{symbol:"600825",received_at:"2026-10-09T08:00:00Z"} as Quote} />);
    expect(screen.getByText(/行情源日期待确认/)).toBeTruthy();
    expect(getLimitUpPoolSnapshot).not.toHaveBeenCalled();
  });
  it("shows a retryable failure rather than no reason", async () => {
    vi.mocked(getLimitUpPoolSnapshot).mockRejectedValueOnce(new Error("源不可用")).mockResolvedValueOnce(pool());
    render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    expect(await screen.findByText(/归因源读取失败/)).toBeTruthy();
    fireEvent.click(screen.getByText("展开核对"));
    fireEvent.click(await screen.findByRole("button",{name:"重试读取"}));
    expect((await screen.findAllByText("拟收购财联社+重大资产重组")).length).toBeGreaterThan(0);
  });
  it("opens full source in a reading overlay and closes without removing the quote summary", async () => {
    vi.mocked(getLimitUpPoolSnapshot).mockResolvedValue(pool());
    render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    fireEvent.click(await screen.findByRole("button",{name:"查看全文"}));
    const dialog = await screen.findByRole("dialog",{name:"涨停原因 600825"});
    expect(within(dialog).getByText("拟收购财联社+重大资产重组")).toBeTruthy();
    expect(within(dialog).getByText(/2026-10-09 · 同花顺/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button",{name:"关闭"}));
    await vi.waitFor(() => expect(screen.queryByRole("dialog",{name:"涨停原因 600825"})).toBeNull());
    expect(screen.getByText("拟收购财联社+重大资产重组")).toBeTruthy();
  });
  it("drops a late response when the current stock changes", async () => {
    let finish!: (data: LimitPoolSnapshot<LimitUpRecord>) => void;
    vi.mocked(getLimitUpPoolSnapshot).mockImplementationOnce(() => new Promise(resolve => {finish=resolve;})).mockResolvedValueOnce(pool());
    const {rerender} = render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    rerender(<LimitUpReasonPanel symbol="600127" quote={quote("600127")} />);
    await screen.findByText(/所查池未收录该股/);
    await act(async () => {finish(pool());});
    expect(screen.queryByText("拟收购财联社+重大资产重组")).toBeNull();
  });
  it("cancels a reading request after leaving a security, including returning to it", async () => {
    vi.mocked(getLimitUpPoolSnapshot).mockResolvedValue(pool());
    const {rerender} = render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    fireEvent.click(await screen.findByRole("button", {name:"查看全文"}));
    expect(screen.getByRole("dialog", {name:"涨停原因 600825"})).toBeTruthy();
    rerender(<LimitUpReasonPanel symbol="600127" quote={quote("600127")} />);
    await screen.findByText(/所查池未收录该股/);
    rerender(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    await screen.findByRole("button", {name:"查看全文"});
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("does not revive a canceled original when refreshed content later reverts", async () => {
    const {rerender} = render(<LimitReason reason="原文甲" compact />);
    fireEvent.click(screen.getByRole("button", {name:/涨停原因 · 查看原文/}));
    expect(screen.getByRole("dialog", {name:"涨停原因原文"})).toBeTruthy();
    rerender(<LimitReason reason="原文乙" compact />);
    rerender(<LimitReason reason="原文甲" compact />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("keeps a full long original available to touch and keyboard, without fabricating a source", async () => {
    const reason = "自有消息面+".repeat(40);
    const {container} = render(<LimitReason reason={reason} compact />);
    fireEvent.click(screen.getByRole("button", {name: /涨停原因 · 查看原文/}));
    const dialog = await screen.findByRole("dialog", {name: "涨停原因原文"});
    expect(within(dialog).getByText(reason)).toBeTruthy();
    expect(within(dialog).getByText(/来源未记录/)).toBeTruthy();
    expect(container.querySelector(".truncate")).toBeNull();
  });
});
