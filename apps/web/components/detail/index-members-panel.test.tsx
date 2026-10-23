import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {IndexMembersPanel} from "./index-members-panel";
const read = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({getIndexMembers: read}));
const snapshot = (symbol: string, name = "沪深300") => ({symbol, name, source: "ths", snapshot_at: "2026-10-15T07:29:07+00:00", items: Array.from({length: 61}, (_, n) => ({symbol: String(600000 + n), name: `成分${n}`, market: "SH"}))});
let sentinelTop = 5000;
function scrollMore() {
  sentinelTop = 200;
  fireEvent.scroll(screen.getByLabelText("指数成分股列表"));
  sentinelTop = 5000;
}
beforeEach(() => {
  read.mockReset(); read.mockImplementation(async symbol => snapshot(symbol)); sentinelTop = 5000;
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(function(this: HTMLElement) {
    return this.getAttribute("aria-label") === "指数成分股列表" ? this.querySelectorAll('button[title$="盘口"]').length * 34 + 20 : 0;
  });
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function(this: HTMLElement) {return this.getAttribute("aria-label") === "指数成分股列表" ? 260 : 0;});
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
    const top = this.dataset.testid === "index-members-sentinel" ? sentinelTop : 0;
    return {top, bottom: top + 1, left: 0, right: 100, width: 100, height: 1, x: 0, y: top, toJSON: () => ({})};
  });
});
afterEach(() => {cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); Object.defineProperty(document, "visibilityState", {configurable: true, value: "visible"});});

describe("指数成分列表", () => {
  it("压缩常驻说明，键盘可读取完整来源口径，Escape后焦点留在入口", async () => {
    render(<div style={{height: 260, width: 260}}><IndexMembersPanel symbol="sh000300" active onSelect={() => {}}/></div>);
    await screen.findByText("61 只");
    expect(screen.queryByText(/全部返回成员排序/)).toBeNull();
    const evidence = screen.getByRole("button", {name: "查看数据来源与排序口径"});
    evidence.focus(); fireEvent.focus(evidence);
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toContain("全部返回成员排序");
    expect(tooltip.textContent).toContain("未知置后");
    expect(tooltip.textContent).toContain("同花顺");
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(30);
    fireEvent.keyDown(evidence, {key: "Escape"});
    await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());
    expect(document.activeElement).toBe(evidence);
  });

  it("仅激活且文档可见时每60秒读取已有快照，成功和失败均保留查询、方向、已显示范围和焦点", async () => {
    vi.useFakeTimers();
    read.mockImplementation(async symbol => {const data = snapshot(symbol); return {...data, items: data.items.map((row, n) => ({...row, change_pct: n / 10}))};});
    Object.defineProperty(document, "visibilityState", {configurable: true, value: "visible"});
    const view = render(<IndexMembersPanel symbol="sh000300" active onSelect={() => {}}/>);
    await act(async () => {await vi.advanceTimersByTimeAsync(0);});
    fireEvent.click(screen.getByRole("button", {name: "涨跌幅升序"}));
    fireEvent.change(screen.getByRole("searchbox"), {target: {value: "成分"}});
    scrollMore();
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(60);
    const input = screen.getByRole("searchbox"); input.focus();
    read.mockRejectedValueOnce(new Error("refresh failed"));
    await act(async () => {await vi.advanceTimersByTimeAsync(60_000);});
    expect(read).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("status").textContent).toContain("保留上次列表");
    expect(screen.getByTitle("查看成分30盘口")).toBeTruthy();
    expect((input as HTMLInputElement).value).toBe("成分");
    expect(screen.getByRole("button", {name: "涨跌幅升序"}).getAttribute("aria-pressed")).toBe("true");
    expect(document.activeElement).toBe(input);
    await act(async () => {await vi.advanceTimersByTimeAsync(60_000);});
    expect(read).toHaveBeenCalledTimes(3);
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(60);
    expect(screen.queryByRole("status")).toBeNull();
    expect(document.activeElement).toBe(input);
    view.rerender(<IndexMembersPanel symbol="sh000300" active={false} onSelect={() => {}}/>);
    await act(async () => {await vi.advanceTimersByTimeAsync(120_000);});
    expect(read).toHaveBeenCalledTimes(3);
    Object.defineProperty(document, "visibilityState", {configurable: true, value: "hidden"});
    view.rerender(<IndexMembersPanel symbol="sh000300" active onSelect={() => {}}/>);
    await act(async () => {await vi.advanceTimersByTimeAsync(120_000);});
    expect(read).toHaveBeenCalledTimes(3);
    await act(async () => {
      Object.defineProperty(document, "visibilityState", {configurable: true, value: "visible"});
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(read).toHaveBeenCalledTimes(4);
    expect(screen.getByTitle("查看成分30盘口")).toBeTruthy();
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(60);
  });

  it("默认按全返回成员的有限涨幅降序，升序仍把未知置尾并保留同值源顺序", async () => {
    const values = [null, -2, 0, 3, 3, NaN, Infinity];
    read.mockResolvedValue({...snapshot("sh000300"), items: values.map((change_pct, n) => ({symbol: `60000${n}`, name: `成员${n}`, market: "SH", change_pct,
      quote_source: "tencent", source_clock: "2026-10-23T10:00:00+08:00", quote_quality: "high"})),
      quote_snapshot: {source: "tencent", source_clock: "2026-10-23T10:00:00+08:00", observed_at: "2026-10-23T10:00:01+08:00", quality: "high", known_count: 4, total: 7}});
    render(<IndexMembersPanel symbol="sh000300" active onSelect={() => {}}/>);
    await screen.findByText("7 只");
    const names = () => screen.getAllByTitle(/查看成员.*盘口/).map(row => row.getAttribute("title")?.replace(/^查看|盘口$/g, ""));
    expect(names()).toEqual(["成员3", "成员4", "成员2", "成员1", "成员0", "成员5", "成员6"]);
    expect(screen.getByRole("button", {name: "涨跌幅降序"}).getAttribute("aria-pressed")).toBe("true");
    expect(within(screen.getByTitle("查看成员2盘口")).getByText("0.00%")).toBeTruthy();
    expect(within(screen.getByTitle("查看成员0盘口")).getByText("--")).toBeTruthy();
    const negative = screen.getByTitle("查看成员1盘口").querySelector<HTMLElement>('span[aria-hidden="true"]')!;
    expect(Number.parseFloat(negative.style.getPropertyValue("--change-width"))).toBeCloseTo(100 / 3);
    expect(Number.parseFloat(negative.style.getPropertyValue("--change-start"))).toBeCloseTo(50 - 100 / 3);
    expect(screen.getByTitle("查看成员0盘口").querySelector('span[aria-hidden="true"]')).toBeNull();
    const ascending = screen.getByRole("button", {name: "涨跌幅升序"}); ascending.focus();
    fireEvent.click(ascending, {detail: 0});
    expect(document.activeElement).toBe(ascending);
    expect(ascending.getAttribute("aria-pressed")).toBe("true");
    expect(names()).toEqual(["成员1", "成员2", "成员3", "成员4", "成员0", "成员5", "成员6"]);
    expect(screen.getByText("已知 4/7")).toBeTruthy();
    const detail = screen.getByRole("button", {name: "查看数据来源与排序口径"}); detail.focus(); fireEvent.focus(detail);
    expect((await screen.findByRole("tooltip")).textContent).toContain("全部返回成员排序");
    expect(read).toHaveBeenCalledTimes(1);
  });

  it("先全返回集合排序再下拉加载，排序和搜索复位首批，成员数与行情已知分母不随筛选变化", async () => {
    const data = snapshot("sh000300");
    read.mockResolvedValue({...data, items: data.items.map((row, n) => ({...row, change_pct: n})),
      quote_snapshot: {source: "sina", source_clock: null, observed_at: "2026-10-23T10:00:01+08:00", quality: "stale", known_count: 61, total: 61}});
    render(<IndexMembersPanel symbol="sh000300" active onSelect={() => {}}/>); await screen.findByText("61 只");
    expect(screen.getAllByTitle(/查看成分.*盘口/)[0].getAttribute("title")).toBe("查看成分60盘口");
    const list = screen.getByLabelText("指数成分股列表");
    list.scrollTop = 700;
    scrollMore();
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(60);
    expect(screen.getAllByTitle(/查看成分.*盘口/)[0].getAttribute("title")).toBe("查看成分60盘口");
    const ascending = screen.getByRole("button", {name: "涨跌幅升序"}); ascending.focus(); fireEvent.click(ascending);
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(30);
    expect(screen.getAllByTitle(/查看成分.*盘口/)[0].getAttribute("title")).toBe("查看成分0盘口");
    expect(list.scrollTop).toBe(0); expect(document.activeElement).toBe(ascending);
    scrollMore();
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(60);
    list.scrollTop = 700;
    fireEvent.change(screen.getByRole("searchbox"), {target: {value: "600000"}});
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(1);
    expect(list.scrollTop).toBe(0);
    expect(screen.getByLabelText("已显示成分股数量").textContent).toBe("已显示 1 / 1");
    fireEvent.change(screen.getByRole("searchbox"), {target: {value: ""}});
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(30);
    expect(screen.getByText("61 只")).toBeTruthy();
    expect(screen.getByText("已知 61/61")).toBeTruthy();
    expect(screen.getByText(/旧观察.*新浪.*观察 10:00:01/)).toBeTruthy();
    const detail = screen.getByRole("button", {name: "查看数据来源与排序口径"}); detail.focus(); fireEvent.focus(detail);
    expect((await screen.findByRole("tooltip")).textContent).toMatch(/观察于.*10:00:01.*源时刻未提供/);
    expect(read).toHaveBeenCalledTimes(1);
  });
  it("只读取成员一次，下拉逐批追加到全部成员且无页码按钮，搜索不发行情请求", async () => {
    const select = vi.fn();
    render(<IndexMembersPanel symbol="sh000300" active onSelect={select}/>);
    await screen.findByText("61 只");
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(30);
    expect(screen.queryByRole("button", {name: "下一页"})).toBeNull();
    expect(screen.queryByRole("button", {name: "上一页"})).toBeNull();
    scrollMore();
    expect(screen.getByTitle("查看成分30盘口")).toBeTruthy();
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(60);
    scrollMore();
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(61);
    expect(screen.getByText("已全部加载（61 只成分股）")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox"), {target: {value: "600060"}});
    fireEvent.click(screen.getByTitle("查看成分60盘口"));
    expect(select).toHaveBeenCalledWith("600060");
    expect(read).toHaveBeenCalledTimes(1);
  });
  it("隐藏时不因保留列表的滚动追加，重新激活后继续原显示范围", async () => {
    const view = render(<IndexMembersPanel symbol="sh000300" active onSelect={() => {}}/>);
    await screen.findByText("61 只");
    scrollMore(); expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(60);
    view.rerender(<IndexMembersPanel symbol="sh000300" active={false} onSelect={() => {}}/>);
    scrollMore(); expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(60);
    view.rerender(<IndexMembersPanel symbol="sh000300" active onSelect={() => {}}/>);
    await act(async () => {});
    scrollMore(); expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(61);
  });
  it("切换指数不展示旧成员，旧请求迟到不能覆盖新指数", async () => {
    let finish!: (v: ReturnType<typeof snapshot>) => void;
    read.mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    const view = render(<IndexMembersPanel symbol="sh000300" active onSelect={() => {}}/>);
    await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
    view.rerender(<IndexMembersPanel symbol="sz399006" active onSelect={() => {}}/>);
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    await act(async () => {finish(snapshot("sh000300", "旧指数"));});
    expect(screen.queryByText("旧指数")).toBeNull();
    expect(screen.getByText("61 只")).toBeTruthy();
  });
  it("A→B→A仍只认最新A请求，等待及失败不借旧指数排名，查询焦点保留", async () => {
    const finish: ((value: ReturnType<typeof snapshot>) => void)[] = [];
    read.mockImplementation(() => new Promise(resolve => {finish.push(resolve);}));
    const view = render(<IndexMembersPanel symbol="sh000300" active onSelect={() => {}}/>);
    await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
    const input = screen.getByRole("searchbox"); input.focus();
    view.rerender(<IndexMembersPanel symbol="sz399006" active onSelect={() => {}}/>);
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    await act(async () => {finish[1](snapshot("sz399006", "B指数"));});
    view.rerender(<IndexMembersPanel symbol="sh000300" active onSelect={() => {}}/>);
    await waitFor(() => expect(read).toHaveBeenCalledTimes(3));
    expect(screen.queryByText("B指数")).toBeNull(); expect(screen.getByText("正在读取成分股…")).toBeTruthy();
    await act(async () => {finish[0](snapshot("sh000300", "旧A指数"));});
    expect(screen.queryByText("旧A指数")).toBeNull();
    await act(async () => {finish[2](snapshot("sh000300", "当前A指数"));});
    expect(screen.getByText("当前A指数")).toBeTruthy(); expect(document.activeElement).toBe(input);
    expect(read).toHaveBeenCalledTimes(3);
  });
  it("失败不冒充空列表，隐藏不读取，恢复可重试", async () => {
    read.mockRejectedValueOnce(new Error("fixture"));
    const view = render(<IndexMembersPanel symbol="sh000300" active={false} onSelect={() => {}}/>);
    expect(read).not.toHaveBeenCalled();
    view.rerender(<IndexMembersPanel symbol="sh000300" active onSelect={() => {}}/>);
    await screen.findByText("成分股读取失败");
    expect(screen.getByText("暂不可用")).toBeTruthy();
    expect(screen.queryByText("读取中")).toBeNull();
    expect(screen.queryByText("没有匹配的成分股")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "重试"}));
    await screen.findByText("61 只");
    expect(read).toHaveBeenCalledTimes(2);
  });
});
