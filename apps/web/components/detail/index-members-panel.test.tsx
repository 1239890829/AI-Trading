import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {IndexMembersPanel} from "./index-members-panel";
const read = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({getIndexMembers: read}));
const snapshot = (symbol: string, name = "沪深300") => ({symbol, name, source: "ths", snapshot_at: "2026-10-15T07:29:07+00:00", items: Array.from({length: 61}, (_, n) => ({symbol: String(600000 + n), name: `成分${n}`, market: "SH"}))});
beforeEach(() => {read.mockReset(); read.mockImplementation(async symbol => snapshot(symbol));});
afterEach(cleanup);

describe("指数成分列表", () => {
  it("默认按全返回成员的有限涨幅降序，升序仍把未知置尾并保留同值源顺序", async () => {
    const values = [null, -2, 0, 3, 3, NaN, Infinity];
    read.mockResolvedValue({...snapshot("sh000300"), items: values.map((change_pct, n) => ({symbol: `60000${n}`, name: `成员${n}`, market: "SH", change_pct,
      quote_source: "tencent", source_clock: "2026-10-23T10:00:00+08:00", quote_quality: "high"})),
      quote_snapshot: {source: "tencent", source_clock: "2026-10-23T10:00:00+08:00", observed_at: "2026-10-23T10:00:01+08:00", quality: "high", known_count: 4, total: 7}});
    render(<IndexMembersPanel symbol="sh000300" active onSelect={() => {}}/>);
    await screen.findByText("7 只");
    const names = () => screen.getAllByTitle(/查看成员.*盘口/).map(row => row.textContent);
    expect(names().map(name => name?.slice(0, 3))).toEqual(["成员3", "成员4", "成员2", "成员1", "成员0", "成员5", "成员6"]);
    expect(screen.getByRole("button", {name: "涨跌幅降序"}).getAttribute("aria-pressed")).toBe("true");
    expect(within(screen.getByTitle("查看成员2盘口")).getByText("0.00%")).toBeTruthy();
    expect(within(screen.getByTitle("查看成员0盘口")).getByText("--")).toBeTruthy();
    const ascending = screen.getByRole("button", {name: "涨跌幅升序"}); ascending.focus();
    fireEvent.click(ascending, {detail: 0});
    expect(document.activeElement).toBe(ascending);
    expect(ascending.getAttribute("aria-pressed")).toBe("true");
    expect(names().map(name => name?.slice(0, 3))).toEqual(["成员1", "成员2", "成员3", "成员4", "成员0", "成员5", "成员6"]);
    expect(screen.getByText(/涨跌幅已知 4 \/ 7/)).toBeTruthy();
    expect(screen.getByText(/全部返回成员排序/)).toBeTruthy();
    expect(read).toHaveBeenCalledTimes(1);
  });

  it("先全返回集合排序再分页搜索，成员数与行情已知分母不随筛选变化", async () => {
    const data = snapshot("sh000300");
    read.mockResolvedValue({...data, items: data.items.map((row, n) => ({...row, change_pct: n})),
      quote_snapshot: {source: "sina", source_clock: null, observed_at: "2026-10-23T10:00:01+08:00", quality: "stale", known_count: 61, total: 61}});
    render(<IndexMembersPanel symbol="sh000300" active onSelect={() => {}}/>); await screen.findByText("61 只");
    expect(screen.getAllByTitle(/查看成分.*盘口/)[0].getAttribute("title")).toBe("查看成分60盘口");
    fireEvent.click(screen.getByRole("button", {name: "下一页"}));
    expect(screen.getAllByTitle(/查看成分.*盘口/)[0].getAttribute("title")).toBe("查看成分30盘口");
    fireEvent.change(screen.getByRole("searchbox"), {target: {value: "600000"}});
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(1);
    expect(screen.getByText("1 / 1")).toBeTruthy();
    expect(screen.getByText("61 只")).toBeTruthy();
    expect(screen.getByText(/涨跌幅已知 61 \/ 61/)).toBeTruthy();
    expect(screen.getByText(/源时刻未提供/)).toBeTruthy();
    expect(screen.getByText(/观察于.*10:00:01.*源时刻未提供/)).toBeTruthy();
    expect(read).toHaveBeenCalledTimes(1);
  });
  it("只读取成员一次，分页/搜索不发行情请求，点成员交给工作台切换", async () => {
    const select = vi.fn();
    render(<IndexMembersPanel symbol="sh000300" active onSelect={select}/>);
    await screen.findByText("61 只");
    expect(screen.getAllByTitle(/查看成分.*盘口/)).toHaveLength(30);
    fireEvent.click(screen.getByRole("button", {name: "下一页"}));
    expect(screen.getByTitle("查看成分30盘口")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox"), {target: {value: "600060"}});
    fireEvent.click(screen.getByTitle("查看成分60盘口"));
    expect(select).toHaveBeenCalledWith("600060");
    expect(read).toHaveBeenCalledTimes(1);
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
