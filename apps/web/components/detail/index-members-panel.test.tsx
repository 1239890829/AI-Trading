import {act, cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {IndexMembersPanel} from "./index-members-panel";
const read = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({getIndexMembers: read}));
const snapshot = (symbol: string, name = "沪深300") => ({symbol, name, source: "ths", snapshot_at: "2026-10-15T07:29:07+00:00", items: Array.from({length: 61}, (_, n) => ({symbol: String(600000 + n), name: `成分${n}`, market: "SH"}))});
beforeEach(() => {read.mockReset(); read.mockImplementation(async symbol => snapshot(symbol));});
afterEach(cleanup);

describe("指数成分列表", () => {
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
