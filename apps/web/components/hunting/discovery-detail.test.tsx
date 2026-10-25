import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DiscoveryDetail, type DiscoveryDetailSelection, type DiscoveryDetailTarget } from "./discovery-detail";
import { getBoardFlowMembers, getConceptDetail, type BoardFlowMembersPayload, type ConceptDetail } from "@/lib/api";

vi.mock("@/lib/api", () => ({ getBoardFlowMembers: vi.fn(), getConceptDetail: vi.fn() }));
vi.mock("@/components/stock-link", () => ({
  StockLink: ({symbol, children, className}: {symbol: string; children: React.ReactNode; className?: string}) =>
    <a href={`/workbench?symbol=${symbol}`} className={className}>{children}</a>,
}));

const theme: DiscoveryDetailTarget = {kind: "theme", code: "309001", name: "主题甲"};
const board: DiscoveryDetailTarget = {kind: "board", code: "BK001", name: "行业甲"};
const selection: DiscoveryDetailSelection = {date: "2026-10-23", loaded: true, entries: []};
function concept(code: string, values: {symbol: string; name: string; change: number | null}[]): ConceptDetail {
  return {code, name: `目录${code}`, total: values.length, limit_up_count: 0, tag_groups: [], meta_note: "成分夹具",
    members: values.map(value => ({symbol: value.symbol, name: value.name, change_pct: value.change,
      price: 10, turnover_rate: null, float_market_cap_yi: null, limit_up: false, break_count: null,
      seal_amount: null, boards: null, reason: null, tags: []}))};
}
function boardMembers(code: string, overrides: Partial<BoardFlowMembersPayload> = {}): BoardFlowMembersPayload {
  return {board_code: code, available: true, degraded: [], rows: [{symbol: "600001", name: "资金成员", price: null,
    change_pct: 0, main_net_yi: 0, super_net_yi: null, big_net_yi: null, main_net_ratio: null}], ...overrides};
}

beforeEach(() => {
  vi.mocked(getConceptDetail).mockResolvedValue(concept(theme.code, [
    {symbol: "600001", name: "上行成员", change: 2}, {symbol: "600002", name: "下行成员", change: -1},
    {symbol: "600003", name: "缺幅成员", change: null},
  ]));
  vi.mocked(getBoardFlowMembers).mockResolvedValue(boardMembers(board.code));
  // Keep the real incremental owner outside the viewport until a test explicitly scrolls it.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({top: 2000, bottom: 2100,
    left: 0, right: 250, width: 250, height: 100, x: 0, y: 2000, toJSON: () => ({})});
});
afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.resetAllMocks();});

describe("发现成分是原右栏只读参考", () => {
  it("uses only its source with a cancelable read, keeps missing clocks explicit and returns only to the original list", async () => {
    const original = vi.fn(), close = vi.fn();
    render(<DiscoveryDetail target={theme} selection={{...selection,
      entries: [{symbol: "600001", name: "上行成员", open: original}]}} onClose={close}/>);
    const list = await screen.findByRole("list", {name: "已读取成分"});
    expect(within(list).getAllByRole("listitem").map(row => within(row).getByRole("link").textContent))
      .toEqual(["上行成员", "下行成员", "缺幅成员"]);
    expect(getConceptDetail).toHaveBeenCalledTimes(1);
    expect(getConceptDetail).toHaveBeenCalledWith(theme.code, {signal: expect.any(AbortSignal)});
    expect(getBoardFlowMembers).not.toHaveBeenCalled();
    expect(screen.getByText(/目录归属与报价时点未核/)).toBeTruthy();
    expect(screen.getByText(/原名单日期：2026-10-23 · 代码相同 1 只/)).toBeTruthy();
    expect(screen.getByText(/仅代码对照，非当时归属/)).toBeTruthy();
    expect(screen.getByTitle("涨跌幅未提供")).toBeTruthy();
    expect(screen.getByRole("heading", {name: "主题甲"}).tabIndex).toBe(-1);
    expect(within(list).getByRole("link", {name: "上行成员"}).getAttribute("href")).toBe("/workbench?symbol=600001");
    expect(screen.queryByRole("button", {name: /研究|买入|模拟/})).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "返回原名单核对上行成员"}));
    expect(original).toHaveBeenCalledTimes(1);
    expect(getConceptDetail).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", {name: "返回环境"}));
    expect(close).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("discovery-detail").querySelector('[class*="overflow"]')).toBeNull();
  });

  it("sorts unknown values last in either direction and searches locally without another source read", async () => {
    render(<DiscoveryDetail target={theme} selection={selection} onClose={vi.fn()}/>);
    const list = await screen.findByRole("list", {name: "已读取成分"});
    fireEvent.click(screen.getByRole("button", {name: "涨跌幅降序，点击切换"}));
    expect(within(list).getAllByRole("link").map(row => row.textContent)).toEqual(["下行成员", "上行成员", "缺幅成员"]);
    const input = screen.getByRole("searchbox", {name: "查找成分"});
    fireEvent.compositionStart(input);
    fireEvent.change(input, {target: {value: "600001"}});
    fireEvent.compositionEnd(input);
    expect(within(list).getAllByRole("link")).toHaveLength(1);
    expect(within(list).getByRole("link").textContent).toBe("上行成员");
    fireEvent.change(input, {target: {value: "不匹配"}});
    expect(screen.getByText("当前搜索无匹配，可清除关键词。")).toBeTruthy();
    expect(getConceptDetail).toHaveBeenCalledTimes(1);
  });

  it("keeps an unread original selection unknown instead of claiming a missing selection", async () => {
    const original = vi.fn();
    render(<DiscoveryDetail target={theme} selection={{date: null, loaded: false,
      entries: [{symbol: "600001", name: "上行成员", open: original}]}} onClose={vi.fn()}/>);
    await screen.findByRole("list", {name: "已读取成分"});
    expect(screen.getByText("原名单日期：待读取")).toBeTruthy();
    expect(screen.queryByText(/原名单含该股|未入选|代码相同 0/)).toBeNull();
    expect(screen.queryByRole("button", {name: /返回原名单/})).toBeNull();
  });

  it("describes BK results as a limited sample, keeps its received clock separate and preserves a measured zero", async () => {
    vi.mocked(getBoardFlowMembers).mockResolvedValue(boardMembers(board.code, {
      delayed: true, updated_at: "2026-10-25T10:00:00+08:00", degraded: ["部分报价缺失"],
    }));
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    await screen.findByRole("link", {name: "资金成员"});
    expect(getBoardFlowMembers).toHaveBeenCalledWith(board.code, {signal: expect.any(AbortSignal)});
    expect(getConceptDetail).not.toHaveBeenCalled();
    expect(screen.getByText(/资金成员样本，非完整成分全集/)).toBeTruthy();
    expect(screen.getByText(/读取记录时间：.*不等于来源时刻。资金为延迟口径/)).toBeTruthy();
    expect(screen.getByText(/来源限制：部分报价缺失/)).toBeTruthy();
    expect(screen.getByText(/主力净额 0\.00 亿元/)).toBeTruthy();
    expect(screen.getByTitle("成分报价涨跌幅，来源时刻未核").textContent).toMatch(/0\.00%/);
  });

  it("does not promote unavailable BK rows into a current member list and can retry", async () => {
    vi.mocked(getBoardFlowMembers).mockResolvedValueOnce(boardMembers(board.code, {available: false, reason: "窗口未取得"}))
      .mockResolvedValueOnce(boardMembers(board.code));
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    expect((await screen.findByRole("alert")).textContent).toContain("成分来源暂不可用。窗口未取得");
    expect(screen.queryByRole("link", {name: "资金成员"})).toBeNull();
    expect(screen.queryByRole("searchbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "重试成分读取"}));
    await screen.findByRole("link", {name: "资金成员"});
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each(["theme", "board"] as const)("rejects the wrong %s identity instead of displaying another object's members", async kind => {
    if (kind === "theme") vi.mocked(getConceptDetail).mockResolvedValue(concept("OTHER", [{symbol: "600009", name: "错配成员", change: 1}]));
    else vi.mocked(getBoardFlowMembers).mockResolvedValue(boardMembers("OTHER"));
    render(<DiscoveryDetail target={kind === "theme" ? theme : board} selection={selection} onClose={vi.fn()}/>);
    expect((await screen.findByRole("alert")).textContent).toContain("返回成分身份不符");
    expect(screen.queryByRole("list", {name: "已读取成分"})).toBeNull();
    expect(screen.queryByRole("searchbox")).toBeNull();
  });

  it("hides retained members when a same-object refresh returns a mismatched identity", async () => {
    vi.mocked(getConceptDetail).mockResolvedValueOnce(concept(theme.code, [{symbol: "600001", name: "原成员", change: 1}]))
      .mockResolvedValueOnce(concept("OTHER", [{symbol: "600002", name: "错配成员", change: 2}]));
    render(<DiscoveryDetail target={theme} selection={selection} onClose={vi.fn()}/>);
    await screen.findByRole("link", {name: "原成员"});
    fireEvent(document, new Event("visibilitychange"));
    await screen.findByRole("alert");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("retains a same-scope failed read explicitly and recovers without inventing an empty result", async () => {
    vi.mocked(getConceptDetail).mockResolvedValueOnce(concept(theme.code, [{symbol: "600001", name: "原成员", change: 1}]))
      .mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(concept(theme.code, []));
    render(<DiscoveryDetail target={theme} selection={selection} onClose={vi.fn()}/>);
    await screen.findByRole("link", {name: "原成员"});
    fireEvent(document, new Event("visibilitychange"));
    expect((await screen.findByRole("alert")).textContent).toContain("成分刷新失败，保留上次读取");
    expect(screen.getByRole("link", {name: "原成员"})).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "重试成分读取"}));
    await screen.findByText(/本次返回没有成员记录/);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("aborts the departed source, resets local filters and rejects its late response", async () => {
    let finish!: (value: ConceptDetail) => void;
    vi.mocked(getConceptDetail).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}))
      .mockResolvedValueOnce(concept("309002", [{symbol: "600004", name: "新目录成员", change: 3}]));
    const view = render(<DiscoveryDetail target={theme} selection={selection} onClose={vi.fn()}/>);
    await waitFor(() => expect(getConceptDetail).toHaveBeenCalledTimes(1));
    const oldSignal = vi.mocked(getConceptDetail).mock.calls[0][1]?.signal;
    view.rerender(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    await screen.findByRole("link", {name: "资金成员"});
    expect(oldSignal?.aborted).toBe(true);
    fireEvent.change(screen.getByRole("searchbox"), {target: {value: "筛选中"}});
    view.rerender(<DiscoveryDetail target={{...theme, code: "309002"}} selection={selection} onClose={vi.fn()}/>);
    await screen.findByRole("searchbox");
    expect(screen.getByRole("searchbox")).toHaveProperty("value", "");
    await act(async () => finish(concept(theme.code, [{symbol: "600009", name: "旧回包", change: 100}])));
    expect(screen.queryByText("旧回包")).toBeNull();
  });

  it("does not read while inactive and drains a closed request without exposing controls", async () => {
    let finish!: (value: ConceptDetail) => void;
    vi.mocked(getConceptDetail).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    const view = render(<DiscoveryDetail target={theme} active={false} selection={selection} onClose={vi.fn()}/>);
    expect(getConceptDetail).not.toHaveBeenCalled();
    view.rerender(<DiscoveryDetail target={theme} selection={selection} onClose={vi.fn()}/>);
    await waitFor(() => expect(getConceptDetail).toHaveBeenCalledTimes(1));
    const signal = vi.mocked(getConceptDetail).mock.calls[0][1]?.signal;
    view.rerender(<DiscoveryDetail target={theme} active={false} selection={selection} onClose={vi.fn()}/>);
    expect(signal?.aborted).toBe(true);
    expect(screen.queryByTestId("discovery-detail")).toBeNull();
    fireEvent(document, new Event("visibilitychange"));
    await act(async () => finish(concept(theme.code, [{symbol: "600009", name: "关闭晚到", change: 5}])));
    expect(screen.queryByText("关闭晚到")).toBeNull();
    expect(getConceptDetail).toHaveBeenCalledTimes(1);
  });

  it("loads the complete returned range incrementally using its parent's scroll owner and a keyboard fallback", async () => {
    vi.mocked(getConceptDetail).mockResolvedValue(concept(theme.code, Array.from({length: 65}, (_, index) => ({
      symbol: String(600000 + index), name: `成员${index}`, change: 65 - index,
    }))));
    render(<div style={{overflowY: "auto"}}><DiscoveryDetail target={theme} selection={selection} onClose={vi.fn()}/></div>);
    const list = await screen.findByRole("list", {name: "已读取成分"});
    expect(within(list).getAllByRole("listitem")).toHaveLength(30);
    fireEvent.click(screen.getByRole("button", {name: "继续加载"}));
    expect(within(list).getAllByRole("listitem")).toHaveLength(60);
    fireEvent.click(screen.getByRole("button", {name: "继续加载"}));
    expect(within(list).getAllByRole("listitem")).toHaveLength(65);
    expect(screen.getByText("已全部加载（65 条成分）")).toBeTruthy();
    expect(getConceptDetail).toHaveBeenCalledTimes(1);
  });

  it.each([null, [null]])("does not make an invalid security identity clickable or turn malformed members %j into a valid empty result", async malformed => {
    vi.mocked(getConceptDetail).mockResolvedValueOnce(concept(theme.code, [{symbol: "sh000001", name: "无效成员", change: 1}]))
      .mockResolvedValueOnce({...concept(theme.code, []), members: malformed} as unknown as ConceptDetail);
    render(<DiscoveryDetail target={theme} selection={selection} onClose={vi.fn()}/>);
    await screen.findByText(/1 条证券身份无效，未展示/);
    expect(screen.getByText("返回记录缺少有效证券代码，未展示成员。")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    fireEvent(document, new Event("visibilitychange"));
    expect((await screen.findByRole("alert")).textContent).toContain("成分刷新失败");
  });
});
