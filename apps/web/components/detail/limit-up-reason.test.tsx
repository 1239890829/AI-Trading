import {act, cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import {afterEach, describe, expect, it, vi} from "vitest";
import {getLimitUpPoolSnapshot, getStockAttribution, type LimitPoolSnapshot} from "@/lib/api";
import type {LimitUpRecord, Quote} from "@/types/market";
import {LimitUpReasonPanel} from "./limit-up-reason";
import {LimitReason} from "./limit-reason";

vi.mock("@/lib/api", () => ({getLimitUpPoolSnapshot: vi.fn(), getStockAttribution: vi.fn()}));
afterEach(() => {cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.mocked(getLimitUpPoolSnapshot).mockReset(); vi.mocked(getStockAttribution).mockReset();});
const quote = (symbol = "600825", date = "2026-10-09T08:00:00Z"): Quote => ({symbol, data_timestamp: date, source: "tencent", quality:"high", quality_reasons:[], received_at:date});
const pool = (reason: string | null = "拟收购财联社+重大资产重组", date = "2026-10-09", symbol = "600825"): LimitPoolSnapshot<LimitUpRecord> => ({trade_date:date, pool:[{symbol,reason,source:"ths",trade_date:date,quality:"high",quality_reasons:[],received_at:`${date}T08:00:00Z`}]});
const emptyPool = (): LimitPoolSnapshot<LimitUpRecord> => ({trade_date:"2026-10-09",pool:[]});
const refreshPool = async () => {await act(async () => {fireEvent(document, new Event("visibilitychange"));});};

describe("shared security detail limit-up attribution", () => {
  it("right-tab reading distinguishes first error, retry and valid missing coverage", async () => {
    vi.mocked(getStockAttribution).mockResolvedValue({schema_version: 1, symbol: "600825", count: 0, items: []});
    vi.mocked(getLimitUpPoolSnapshot).mockRejectedValueOnce(new Error("synthetic_failure")).mockResolvedValueOnce(emptyPool());
    render(<LimitUpReasonPanel symbol="600825" quote={quote()} standalone />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "涨停原因读取失败");
    expect(screen.queryByText(/本次资料未收录/)).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "重新读取涨停原因"}));
    expect(await screen.findByText(/未收录不表示未触板/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(getLimitUpPoolSnapshot).toHaveBeenCalledTimes(2);
  });
  it("supplier原句优先；只有原因为空才读既有来源主线索", async () => {
    vi.mocked(getLimitUpPoolSnapshot).mockResolvedValueOnce(pool()).mockResolvedValueOnce(pool(null));
    vi.mocked(getStockAttribution).mockResolvedValue({schema_version: 1, symbol: "600825", count: 0, items: [], primary_reason: {status: "unconfirmed", label: "暂无主要来源线索", reason: "来源未绑定", event_id: null, version_id: null, observation_id: null, relation: "unknown", timing_relation: "time_unknown", excerpt: null, qualifiers: [], fact_verified: false, causality_verified: false, interpretation_view: "current"}});
    render(<LimitUpReasonPanel symbol="600825" quote={quote()} standalone />);
    await screen.findByText("拟收购财联社+重大资产重组");
    expect(getStockAttribution).not.toHaveBeenCalled();
    await refreshPool();
    await screen.findByText("暂无主要来源线索");
    expect(getStockAttribution).toHaveBeenCalledTimes(1);
    expect(getStockAttribution).toHaveBeenCalledWith("600825", {poolDate: "2026-10-09", responseAsOf: undefined});
    expect(screen.queryByText("数据源未提供涨停原因")).toBeNull();
  });
  it("shows the source original with actual pool date and does not use concepts as fallback", async () => {
    vi.mocked(getLimitUpPoolSnapshot).mockResolvedValue(pool());
    render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    expect(await screen.findByText("拟收购财联社+重大资产重组")).toBeTruthy();
    expect(screen.getByText(/2026-10-09 · 同花顺/)).toBeTruthy();
    expect(screen.getByText(/非已核实的涨停因果/)).toBeTruthy();
  });
  it("keeps the same-day pool identity when the stock has opened and the cause is missing", async () => {
    vi.mocked(getLimitUpPoolSnapshot).mockResolvedValue(pool(null));
    render(<LimitUpReasonPanel symbol="600825" quote={{...quote(),price:8,high:10,limit_up_price:10}} />);
    expect(await screen.findByText("数据源未提供涨停原因")).toBeTruthy();
    expect(screen.getByText("涨停原因")).toBeTruthy();
    expect(screen.getByText(/2026-10-09 · 同花顺/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
  it.each<[string, LimitPoolSnapshot<LimitUpRecord>, string, Quote]>([
    ["stock outside the pool", pool(), "600127", quote("600127")],
    ["empty pool even when high has touched the limit", emptyPool(), "600825", {...quote(),price:8,high:10,limit_up_price:10}],
    ["wrong payload date", pool("旧日原因", "2026-10-08"), "600825", quote()],
    ["wrong row date", {...pool(),pool:[{...pool().pool[0],trade_date:"2026-10-08"}]}, "600825", quote()],
    ["wrong quote source date", pool(), "600825", quote("600825", "2026-10-08T08:00:00Z")],
    ["wrong quote symbol", pool(), "600825", quote("600127")],
    ["invalid quote date", pool(), "600825", quote("600825", "invalid")],
  ])("hides attribution for %s", async (_name, snapshot, symbol, currentQuote) => {
    vi.mocked(getLimitUpPoolSnapshot).mockResolvedValue(snapshot);
    await act(async () => {render(<LimitUpReasonPanel symbol={symbol} quote={currentQuote} />);});
    expect(screen.queryByText("涨停原因")).toBeNull();
    expect(screen.queryByText("拟收购财联社+重大资产重组")).toBeNull();
    expect(screen.queryByText("旧日原因")).toBeNull();
    expect(screen.queryByRole("button", {name:"展开核对"})).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("does not replace a missing source date with received_at or request all market news", async () => {
    render(<LimitUpReasonPanel symbol="600825" quote={{...quote(),data_timestamp:null}} />);
    expect(screen.queryByText("涨停原因")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(getLimitUpPoolSnapshot).not.toHaveBeenCalled();
  });
  it("hides attribution while membership is still unknown or the pool read fails", async () => {
    let fail!: (error: Error) => void;
    vi.mocked(getLimitUpPoolSnapshot).mockImplementationOnce(() => new Promise((_resolve, reject) => {fail=reject;}));
    render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    expect(screen.queryByText("涨停原因")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    await act(async () => {fail(new Error("源不可用"));});
    expect(screen.queryByText("涨停原因")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("hides a retained old member and closes its reading overlay after a refresh error", async () => {
    vi.mocked(getLimitUpPoolSnapshot).mockResolvedValueOnce(pool()).mockRejectedValueOnce(new Error("源不可用")).mockResolvedValueOnce(pool());
    render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    fireEvent.click(await screen.findByRole("button",{name:"查看全文"}));
    expect(screen.getByRole("dialog")).toBeTruthy();
    await refreshPool();
    // An inert exit image may retain its graphic title; no active source or action survives.
    expect(screen.queryByRole("button", {name: "查看全文"})).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    await refreshPool();
    await screen.findByRole("button",{name:"查看全文"});
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("opens full source in a reading overlay and closes without removing the quote summary", async () => {
    vi.mocked(getLimitUpPoolSnapshot).mockResolvedValue(pool());
    const {container} = render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    fireEvent.click(await screen.findByRole("button",{name:"查看全文"}));
    const dialog = await screen.findByRole("dialog",{name:"涨停原因 600825"});
    expect(within(dialog).getByText("拟收购财联社+重大资产重组")).toBeTruthy();
    expect(within(dialog).getByLabelText("交易日记录").textContent).toContain("2026-10-09");
    expect(within(dialog).getByText(/同花顺 · 数据源归因 · 因果待核/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button",{name:"关闭"}));
    await vi.waitFor(() => expect(screen.queryByRole("dialog",{name:"涨停原因 600825"})).toBeNull());
    expect(within(container).getByText("拟收购财联社+重大资产重组")).toBeTruthy();
  });
  it("drops a late response when the current stock changes", async () => {
    let finish!: (data: LimitPoolSnapshot<LimitUpRecord>) => void;
    vi.mocked(getLimitUpPoolSnapshot).mockImplementationOnce(() => new Promise(resolve => {finish=resolve;})).mockResolvedValueOnce(pool("当前股原因", "2026-10-09", "600127"));
    const {rerender} = render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    rerender(<LimitUpReasonPanel symbol="600127" quote={quote("600127")} />);
    await screen.findByText("当前股原因");
    await act(async () => {finish(pool());});
    expect(screen.queryByText("拟收购财联社+重大资产重组")).toBeNull();
    expect(screen.getByText("当前股原因")).toBeTruthy();
  });
  it("cancels a reading request after leaving a security, including returning to it", async () => {
    vi.mocked(getLimitUpPoolSnapshot).mockResolvedValue(pool());
    const {rerender} = render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    fireEvent.click(await screen.findByRole("button", {name:"查看全文"}));
    expect(screen.getByRole("dialog", {name:"涨停原因 600825"})).toBeTruthy();
    rerender(<LimitUpReasonPanel symbol="600127" quote={quote("600127")} />);
    await act(async () => {});
    expect(screen.queryByRole("button", {name: "查看全文"})).toBeNull();
    rerender(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    await screen.findByRole("button", {name:"查看全文"});
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("closes the original when membership exits and does not reopen when the member returns", async () => {
    vi.mocked(getLimitUpPoolSnapshot).mockResolvedValueOnce(pool()).mockResolvedValueOnce(emptyPool()).mockResolvedValueOnce(pool());
    render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    fireEvent.click(await screen.findByRole("button",{name:"查看全文"}));
    expect(screen.getByRole("dialog")).toBeTruthy();
    await refreshPool();
    expect(screen.queryByRole("button", {name: "查看全文"})).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    await refreshPool();
    await screen.findByRole("button",{name:"查看全文"});
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("closes the original on a source-day change and does not reopen on returning to that day", async () => {
    vi.mocked(getLimitUpPoolSnapshot).mockResolvedValueOnce(pool()).mockResolvedValueOnce(pool("新日原因", "2026-10-12")).mockResolvedValueOnce(pool());
    const {rerender} = render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    fireEvent.click(await screen.findByRole("button",{name:"查看全文"}));
    expect(screen.getByRole("dialog")).toBeTruthy();
    rerender(<LimitUpReasonPanel symbol="600825" quote={quote("600825", "2026-10-12T08:00:00Z")} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    await screen.findByText("新日原因");
    rerender(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    await screen.findByRole("button",{name:"查看全文"});
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("drops a late pool response from the previous quote source day", async () => {
    let finish!: (data: LimitPoolSnapshot<LimitUpRecord>) => void;
    vi.mocked(getLimitUpPoolSnapshot).mockImplementationOnce(() => new Promise(resolve => {finish=resolve;})).mockResolvedValueOnce(pool("新日原因", "2026-10-12"));
    const {rerender} = render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
    rerender(<LimitUpReasonPanel symbol="600825" quote={quote("600825", "2026-10-12T08:00:00Z")} />);
    await screen.findByText("新日原因");
    await act(async () => {finish(pool());});
    expect(screen.queryByText("拟收购财联社+重大资产重组")).toBeNull();
    expect(screen.getByText("新日原因")).toBeTruthy();
  });
  it("does not revive a canceled original when refreshed content later reverts", async () => {
    const {rerender} = render(<LimitReason reason="原文甲" compact />);
    fireEvent.click(screen.getByRole("button", {name:/涨停原因 · 查看原文/}));
    expect(screen.getByRole("dialog", {name:"涨停原因原文"})).toBeTruthy();
    rerender(<LimitReason reason="原文乙" compact />);
    rerender(<LimitReason reason="原文甲" compact />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("a missing source immediately stops its reader, retains the exit image and never reopens it implicitly", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("matchMedia", () => ({matches:false, addEventListener:()=>{}, removeEventListener:()=>{}}));
    vi.mocked(getStockAttribution).mockResolvedValue({schema_version:1, symbol:"600825", count:0, items:[]});
    const view = render(<LimitReason symbol="600825" reason="原文甲" compact />);
    fireEvent.click(screen.getByRole("button", {name:/涨停原因 · 查看原文/}));
    await act(async () => {});
    expect(getStockAttribution).toHaveBeenCalledTimes(1);
    view.rerender(<LimitReason symbol="600825" reason={null} compact />);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("button", {name:"涨停归因 · 证据核对"})).toBeNull();
    expect(document.querySelector("[data-exit-visual]")?.textContent).toContain("原文甲");
    await act(async () => { await vi.advanceTimersByTimeAsync(160); });
    expect(document.querySelector("[data-exit-visual]")).toBeNull();
    expect(getStockAttribution).toHaveBeenCalledTimes(1);
    view.rerender(<LimitReason symbol="600825" reason="原文甲" compact />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("keeps a full long original available to touch and keyboard, without fabricating a source", async () => {
    const reason = "自有消息面+".repeat(40);
    const {container} = render(<LimitReason reason={reason} compact />);
    fireEvent.click(screen.getByRole("button", {name: /涨停原因 · 查看原文/}));
    const dialog = await screen.findByRole("dialog", {name: "涨停原因原文"});
    expect(within(dialog).queryByRole("tabpanel")).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", {name:"完整原文"}));
    expect(within(dialog).getByLabelText("数据源归因原文完整原文").textContent).toContain(reason);
    expect(within(within(dialog).getByLabelText("数据源归因原文完整原文")).getByText(reason).textContent).toBe(reason);
    expect(within(dialog).getByText(/来源未记录 · 数据源归因 · 因果待核/)).toBeTruthy();
    expect(container.querySelector(".truncate")).toBeNull();
  });

  it("does not retain an open attribution overlay when another security has the same reason", () => {
    const view = render(<LimitReason symbol="600825" date="2026-10-09" reason="公司独立事件" compact />);
    fireEvent.click(screen.getByRole("button", {name: /涨停原因 · 查看原文/}));
    expect(screen.getByRole("dialog", {name: "涨停原因原文"})).toBeTruthy();
    view.rerender(<LimitReason symbol="600127" date="2026-10-09" reason="公司独立事件" compact />);
    expect(screen.queryByRole("dialog")).toBeNull();
    view.rerender(<LimitReason symbol="600825" date="2026-10-09" reason="公司独立事件" compact />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens the attribution body with the reason overlay and reads no evidence before opening", async () => {
    vi.mocked(getStockAttribution).mockResolvedValue({schema_version: 1, symbol: "600825", count: 0, items: []});
    render(<LimitReason symbol="600825" date="2026-10-09" reason="拟收购" compact />);
    expect(getStockAttribution).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name: /涨停原因 · 查看原文/}));
    await screen.findByText(/暂未找到关联事件/);
    expect(screen.getByRole("button", {name: "涨停归因 · 证据核对"}).getAttribute("aria-expanded")).toBe("true");
    expect(getStockAttribution).toHaveBeenCalledTimes(1);
    expect(getStockAttribution).toHaveBeenCalledWith("600825", {poolDate:"2026-10-09",responseAsOf:undefined});
  });
});

it("隐藏停止池读取并取消portal原文，恢复不自动重开", async () => {
  vi.useFakeTimers();
  vi.mocked(getLimitUpPoolSnapshot).mockResolvedValue(pool());
  const view = render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", {name: "查看全文"}));
  expect(screen.getByRole("dialog")).toBeTruthy();
  view.rerender(<LimitUpReasonPanel symbol="600825" quote={quote()} active={false} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.querySelector("[data-motion-state=closed] [data-exit-visual]")).toBeTruthy();
  await act(async () => {await vi.advanceTimersByTimeAsync(300_000);});
  expect(getLimitUpPoolSnapshot).toHaveBeenCalledTimes(1);
  view.rerender(<LimitUpReasonPanel symbol="600825" quote={quote()} active />);
  await act(async () => {});
  expect(getLimitUpPoolSnapshot).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("隐藏不能清掉源失败而复活旧原因，恢复的新读取完成前仍不展示", async () => {
  vi.mocked(getLimitUpPoolSnapshot).mockResolvedValueOnce(pool()).mockRejectedValueOnce(new Error("当前源失败"));
  const view = render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
  await act(async () => {});
  await refreshPool();
  expect(screen.queryByText("涨停原因")).toBeNull();
  view.rerender(<LimitUpReasonPanel symbol="600825" quote={quote()} active={false} />);
  expect(screen.queryByText("涨停原因")).toBeNull();
  let finish!: (snapshot: LimitPoolSnapshot<LimitUpRecord>) => void;
  vi.mocked(getLimitUpPoolSnapshot).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
  view.rerender(<LimitUpReasonPanel symbol="600825" quote={quote()} active />);
  expect(screen.queryByText("涨停原因")).toBeNull();
  await act(async () => {finish(pool());});
  expect(screen.getByText("涨停原因")).toBeTruthy();
});

it("keeps a previous same-day source label separate from a new pool without a reason", async () => {
  const snapshot = {...pool(null), pool:[{...pool(null).pool[0], source:"eastmoney"}], reason_observations:{"600825":[{symbol:"600825",trade_date:"2026-10-09",source:"ths",reason:"此前原句",content_hash:"hash",observed_at:"2026-10-09T13:33:00+08:00",received_at:"2026-10-09T13:33:00+08:00"}]}};
  vi.mocked(getLimitUpPoolSnapshot).mockResolvedValue(snapshot);
  render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
  expect(await screen.findByText("此前原句")).toBeTruthy();
  expect(screen.getByText(/此前来源观察.*不证明当前在板/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"查看全文"}));
  expect(await screen.findByRole("dialog")).toBeTruthy();
  expect(within(screen.getByRole("dialog")).getByText("此前原句")).toBeTruthy();
});

it("shows a retained source label without claiming current membership", async () => {
  vi.mocked(getLimitUpPoolSnapshot).mockResolvedValue({...emptyPool(),reason_observations:{"600825":[{symbol:"600825",trade_date:"2026-10-09",source:"ths",reason:"离池后的旧原句",content_hash:"hash",observed_at:"2026-10-09T13:33:00+08:00",received_at:null}]}});
  render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);
  expect(await screen.findByText("离池后的旧原句")).toBeTruthy();
  expect(screen.getByText(/不证明当前在板/)).toBeTruthy();
});

it("does not borrow a retained label from another date or security", async () => {
  vi.mocked(getLimitUpPoolSnapshot).mockResolvedValue({...emptyPool(),reason_observations:{"600825":[{symbol:"600127",trade_date:"2026-10-08",source:"ths",reason:"不应借用",content_hash:"hash",observed_at:"2026-10-08T13:33:00+08:00",received_at:null}]}});
  await act(async () => {render(<LimitUpReasonPanel symbol="600825" quote={quote()} />);});
  expect(screen.queryByText("不应借用")).toBeNull();
});
