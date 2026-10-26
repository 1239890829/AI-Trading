import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DiscoveryDetail, type DiscoveryDetailSelection, type DiscoveryDetailTarget } from "./discovery-detail";
import { getBoardFlowMembers, getSinaBoardConstituents, getConceptDetail, type SinaBoardConstituentsPayload, type ConceptDetail } from "@/lib/api";

vi.mock("@/lib/api", () => ({ getBoardFlowMembers: vi.fn(), getSinaBoardConstituents: vi.fn(), getConceptDetail: vi.fn() }));
vi.mock("@/components/stock-link", () => ({
  StockLink: ({symbol, children, className}: {symbol: string; children: React.ReactNode; className?: string}) =>
    <a href={`/workbench?symbol=${symbol}`} className={className}>{children}</a>,
}));

const theme: DiscoveryDetailTarget = {kind: "theme", code: "309001", name: "主题甲"};
const board: DiscoveryDetailTarget = {kind: "board", code: "new_dlhy", name: "行业甲", catalogKind: "industry", source:"sina"};
const selection: DiscoveryDetailSelection = {date: "2026-10-23", loaded: true, entries: []};
function concept(code: string, values: {symbol: string; name: string; change: number | null}[]): ConceptDetail {
  return {code, name: `目录${code}`, total: values.length, limit_up_count: 0, tag_groups: [], meta_note: "成分夹具",
    members: values.map(value => ({symbol: value.symbol, name: value.name, change_pct: value.change,
      price: 10, turnover_rate: null, float_market_cap_yi: null, limit_up: false, break_count: null,
      seal_amount: null, boards: null, reason: null, tags: []}))};
}
function boardMembers(code: string, overrides: Partial<SinaBoardConstituentsPayload> = {}): SinaBoardConstituentsPayload {
  return {category: code, name: "行业甲", kind: "industry", available: true, source: "sina_board_funds",
    source_as_of: null, received_at: "2026-10-26T20:00:00+08:00", coverage: {total: 1, returned: 1, complete: true},
    degraded: [], rows: [{symbol: "600001", name: "正式成分", price: null, change_pct: 0, net_inflow_yi:null,r0_net_yi:null,source_as_of: null}], ...overrides};
}

beforeEach(() => {
  vi.mocked(getConceptDetail).mockResolvedValue(concept(theme.code, [
    {symbol: "600001", name: "上行成员", change: 2}, {symbol: "600002", name: "下行成员", change: -1},
    {symbol: "600003", name: "缺幅成员", change: null},
  ]));
  vi.mocked(getSinaBoardConstituents).mockResolvedValue(boardMembers(board.code));
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
    expect(getSinaBoardConstituents).not.toHaveBeenCalled();
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

  it.each([
    {target: theme, loaded: false, memberName: "上行成员"},
    {target: board, loaded: true, memberName: "正式成分"},
  ])("retries only the failed original selection beside readable $target.kind members and restores code matching", async ({target, loaded, memberName}) => {
    const original = vi.fn(), retry = vi.fn(), close = vi.fn();
    const failedSelection: DiscoveryDetailSelection = {...selection, loaded, readError: true, retry,
      entries: [{symbol: "600001", name: "原名单名称", open: original},
        {symbol: "600009", name: "名单其他股票", open: vi.fn()}]};
    const view = render(<DiscoveryDetail target={target} selection={failedSelection} onClose={close}/>);
    const list = await screen.findByRole("list", {name: "已读取成分"});
    const member = within(list).getByRole("link", {name: memberName});
    expect(screen.getByText("原名单日期：读取失败，未作对照")).toBeTruthy();
    expect(screen.queryByText(/原名单含该股|代码相同|未入选/)).toBeNull();
    expect(screen.queryByRole("button", {name: /返回原名单/})).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "重试原名单"}));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(original).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();

    view.rerender(<DiscoveryDetail target={target} selection={{...failedSelection, pending: true}} onClose={close}/>);
    const pending = screen.getByRole("button", {name: "正在重试原名单…"});
    expect(pending).toHaveProperty("disabled", true);
    fireEvent.click(pending);
    expect(retry).toHaveBeenCalledTimes(1);
    expect(screen.getByText("原名单日期：读取失败，未作对照")).toBeTruthy();
    expect(screen.getByRole("list", {name: "已读取成分"})).toBe(list);
    expect(within(list).getByRole("link", {name: memberName})).toBe(member);

    view.rerender(<DiscoveryDetail target={target} selection={{...failedSelection, loaded: true,
      readError: false, pending: false}} onClose={close}/>);
    expect(screen.getByText("原名单日期：2026-10-23 · 代码相同 1 只")).toBeTruthy();
    expect(screen.getAllByText("原名单含该股")).toHaveLength(1);
    expect(screen.queryByText(/读取失败，未作对照/)).toBeNull();
    expect(screen.queryByRole("button", {name: /重试原名单/})).toBeNull();
    expect(screen.queryByRole("button", {name: /研究|买入|模拟/})).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: `返回原名单核对${memberName}`}));
    expect(original).toHaveBeenCalledTimes(1);
    expect(retry).toHaveBeenCalledTimes(1);
    expect(getConceptDetail).toHaveBeenCalledTimes(target.kind === "theme" ? 1 : 0);
    expect(getSinaBoardConstituents).toHaveBeenCalledTimes(target.kind === "board" ? 1 : 0);
  });

  it("reads same-source Sina members independently of Eastmoney fund rankings and keeps absent quotes separate", async () => {
    vi.mocked(getSinaBoardConstituents).mockResolvedValue(boardMembers(board.code, {
      source_as_of: null, received_at: "2026-10-25T10:00:00+08:00", degraded: ["部分报价缺失"],
    }));
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    await screen.findByRole("link", {name: "正式成分"});
    expect(getSinaBoardConstituents).toHaveBeenCalledWith(board.code, "industry", {signal: expect.any(AbortSignal)});
    expect(getConceptDetail).not.toHaveBeenCalled();
    expect(screen.getByText(/新浪板块同源成分 · 全部净流入与主力口径分开/)).toBeTruthy();
    expect(screen.getByText(/读取完成：.*不等于来源时刻或历史归属/)).toBeTruthy();
    expect(screen.getByText(/覆盖 1 \/ 1 只 · 本次来源范围已读全/)).toBeTruthy();
    expect(screen.getByText(/来源限制：部分报价缺失/)).toBeTruthy();
    expect(screen.queryByText(/主力净额/)).toBeNull();
    expect(screen.getByTitle("成分报价涨跌幅，来源时刻未核").textContent).toMatch(/0\.00%/);
  });

  it("keeps legacy Eastmoney funds Top20 explicitly separate from complete membership",async()=>{
    vi.mocked(getBoardFlowMembers).mockResolvedValue({available:true,board_code:"BK0001",rows:[{
      symbol:"600009",name:"东财排行股",price:20,change_pct:1,main_net_yi:2,super_net_yi:null,big_net_yi:null,main_net_ratio:null,
    }],degraded:[],updated_at:"2026-10-26T15:00:00+08:00"});
    render(<DiscoveryDetail target={{kind:"board",source:"eastmoney",code:"BK0001",name:"旧板块",catalogKind:"concept"}} selection={selection} onClose={vi.fn()}/>);
    expect(await screen.findByRole("link",{name:"东财排行股"})).toBeTruthy();
    expect(screen.getByText("东财个股资金排行 Top20 · 不是成分全集")).toBeTruthy();
    expect(screen.getByRole("list",{name:"已读取资金排行"})).toBeTruthy();
    expect(screen.queryByText(/本次来源范围已读全|全部净流入/)).toBeNull();
    expect(getBoardFlowMembers).toHaveBeenCalledWith("BK0001",{signal:expect.any(AbortSignal)});
    expect(getSinaBoardConstituents).not.toHaveBeenCalled();
  });

  it("keeps source identity in the read scope and drops a late result from a departed source",async()=>{
    let finish!:(value:SinaBoardConstituentsPayload)=>void;
    vi.mocked(getSinaBoardConstituents).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    vi.mocked(getBoardFlowMembers).mockResolvedValue({available:false,board_code:"BK0001",rows:[],reason:"旧来源失败",degraded:[]});
    const view=render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    await waitFor(()=>expect(getSinaBoardConstituents).toHaveBeenCalledTimes(1));
    const signal=vi.mocked(getSinaBoardConstituents).mock.calls[0][2]?.signal;
    view.rerender(<DiscoveryDetail target={{...board,source:"eastmoney",code:"BK0001"}} selection={selection} onClose={vi.fn()}/>);
    expect((await screen.findByRole("alert")).textContent).toContain("资金排行来源暂不可用。旧来源失败");
    expect(signal?.aborted).toBe(true);
    await act(async()=>finish(boardMembers(board.code)));
    expect(screen.queryByRole("link",{name:"正式成分"})).toBeNull();
    expect(screen.queryByText(/成分来源暂不可用/)).toBeNull();
  });

  it("keeps whole flow and source-defined main flow separate and the daily date is not a source clock",async()=>{
    vi.mocked(getSinaBoardConstituents).mockResolvedValue(boardMembers(board.code,{rows:[{symbol:"600001",name:"双口径成员",price:null,change_pct:null,net_inflow_yi:1.25,r0_net_yi:-0.5,source_as_of:null}],daily_snapshot:{available:true,source_day:"2026-10-26",degraded:[]}}));
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    const row=(await screen.findByRole("link",{name:"双口径成员"})).closest("li")!;
    expect(row.textContent).toContain("全部净流入 1.25亿");
    expect(row.textContent).toContain("主力 -0.50亿");
    expect(screen.getByText(/日线核对日期 2026-10-26，不作为盘中来源时刻/)).toBeTruthy();
    expect(screen.getByText(/成分来源：新浪资金 · 来源时刻 未提供/)).toBeTruthy();
    expect(screen.queryByText(/主力净额/)).toBeNull();
  });

  it.each(["HTTPStatusError","ValueError"])("keeps complete members usable while a failed daily snapshot reports its own %s limitation",async reason=>{
    vi.mocked(getSinaBoardConstituents).mockResolvedValue(boardMembers(board.code,{daily_snapshot:{available:false,source_day:null,degraded:["新浪日线读取或校验失败",reason]}}));
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    expect(await screen.findByRole("link",{name:"正式成分"})).toBeTruthy();
    const status=screen.getByText(/日线核对未完成/);
    expect(status.getAttribute("role")).toBe("status");
    expect(status.textContent).toContain(`日线限制：新浪日线读取或校验失败；${reason}。`);
    expect(screen.getByText(/覆盖 1 \/ 1 只 · 本次来源范围已读全/)).toBeTruthy();
    expect(screen.queryByText(/日线核对日期未提供|成分来源暂不可用/)).toBeNull();
    expect(screen.getByRole("list",{name:"已读取成分"})).toBeTruthy();
  });

  it("keeps a dated snapshot's own limitations visible without treating its day as the source clock",async()=>{
    vi.mocked(getSinaBoardConstituents).mockResolvedValue(boardMembers(board.code,{daily_snapshot:{available:true,source_day:"2026-10-23",degraded:["日线日期不是接收当日，不能据此断言本次成员同日"]}}));
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    await screen.findByRole("link",{name:"正式成分"});
    expect(screen.getByText(/日线核对日期 2026-10-23，不作为盘中来源时刻/).textContent).toContain("日线日期不是接收当日，不能据此断言本次成员同日");
    expect(screen.getByText(/成分来源：新浪资金 · 来源时刻 未提供/)).toBeTruthy();
  });

  it("rejects another source even when it echoes the exact category and kind",async()=>{
    vi.mocked(getSinaBoardConstituents).mockResolvedValue({...boardMembers(board.code),source:"eastmoney_board_funds"} as unknown as SinaBoardConstituentsPayload);
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    expect((await screen.findByRole("alert")).textContent).toContain("返回成分身份不符");
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("does not pass an Eastmoney identifier through a Sina member API",async()=>{
    render(<DiscoveryDetail target={{...board,code:"BK0001"}} selection={selection} onClose={vi.fn()}/>);
    expect((await screen.findByRole("alert")).textContent).toContain("返回成分身份不符");
    expect(getSinaBoardConstituents).not.toHaveBeenCalled();
    expect(getBoardFlowMembers).not.toHaveBeenCalled();
  });

  it("does not promote unavailable Sina rows into a current member list and can retry", async () => {
    vi.mocked(getSinaBoardConstituents).mockResolvedValueOnce(boardMembers(board.code, {available: false, reason: "窗口未取得"}))
      .mockResolvedValueOnce(boardMembers(board.code));
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    expect((await screen.findByRole("alert")).textContent).toContain("成分来源暂不可用。窗口未取得");
    expect(screen.queryByRole("link", {name: "正式成分"})).toBeNull();
    expect(screen.queryByRole("searchbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "重试成分读取"}));
    await screen.findByRole("link", {name: "正式成分"});
    expect(getSinaBoardConstituents).toHaveBeenLastCalledWith(board.code, "industry", {signal:expect.any(AbortSignal),fresh:true});
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows true members with missing quotations and no invented per-stock fund fields",async()=>{
    vi.mocked(getSinaBoardConstituents).mockResolvedValue(boardMembers(board.code,{rows:[{symbol:"600001",name:"可核成分",price:null,change_pct:null,net_inflow_yi:null,r0_net_yi:null,source_as_of:null}],degraded:["成分已读，个股报价未取得"]}));
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    expect(await screen.findByRole("link",{name:"可核成分"})).toBeTruthy();
    expect(screen.getByTitle("涨跌幅未提供")).toBeTruthy();
    expect(screen.getByText(/覆盖 1 \/ 1 只 · 本次来源范围已读全/)).toBeTruthy();
    expect(screen.getByText(/成分已读，个股报价未取得/)).toBeTruthy();
    expect(screen.queryByText(/主力净额|个股资金 0|成分来源暂不可用/)).toBeNull();
  });

  it("keeps partial source coverage explicit instead of turning the returned member sample into a complete list",async()=>{
    vi.mocked(getSinaBoardConstituents).mockResolvedValue(boardMembers(board.code,{coverage:{total:3,returned:1,complete:false}}));
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    await screen.findByRole("link",{name:"正式成分"});
    expect(screen.getByText(/覆盖 1 \/ 3 只 · 来源覆盖尚不完整/)).toBeTruthy();
    expect(screen.queryByText(/本次来源范围已读全/)).toBeNull();
  });

  it("distinguishes a confirmed empty source from failure or unknown membership",async()=>{
    vi.mocked(getSinaBoardConstituents).mockResolvedValue(boardMembers(board.code,{coverage:{total:0,returned:0,complete:true},rows:[]}));
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    await screen.findByText(/本次返回没有成员记录/);
    expect(screen.getByText(/覆盖 0 \/ 0 只 · 本次来源范围已读全/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("loads a complete Sina member response beyond the old Top20 boundary through the existing incremental owner",async()=>{
    vi.mocked(getSinaBoardConstituents).mockResolvedValue(boardMembers(board.code,{coverage:{total:65,returned:65,complete:true},
      rows:Array.from({length:65},(_,index)=>({symbol:String(600000+index),name:`真实范围成员${index}`,price:null,change_pct:65-index,net_inflow_yi:null,r0_net_yi:null,source_as_of:null}))}));
    render(<div style={{overflowY:"auto"}}><DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/></div>);
    const list=await screen.findByRole("list",{name:"已读取成分"});
    expect(within(list).getAllByRole("listitem")).toHaveLength(30);
    fireEvent.click(screen.getByRole("button",{name:"继续加载"}));
    expect(within(list).getAllByRole("listitem")).toHaveLength(60);
    fireEvent.click(screen.getByRole("button",{name:"继续加载"}));
    expect(within(list).getAllByRole("listitem")).toHaveLength(65);
    expect(screen.getByText("已全部加载（65 条成分）")).toBeTruthy();
    expect(getSinaBoardConstituents).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("discovery-detail").querySelector('[class*="overflow"]')).toBeNull();
  });

  it("keeps a source-renamed board visible by exact code without inventing a cross-source alias",async()=>{
    vi.mocked(getSinaBoardConstituents).mockResolvedValue(boardMembers(board.code,{name:"来源现名"}));
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    await screen.findByRole("link",{name:"正式成分"});
    expect(screen.getByText(`成员来源名称：来源现名；代码保持 ${board.code}。`)).toBeTruthy();
    expect(screen.getByRole("heading",{name:"行业甲"})).toBeTruthy();
  });

  it("rejects a same-code wrong-kind member response and hides retained rows",async()=>{
    vi.mocked(getSinaBoardConstituents).mockResolvedValueOnce(boardMembers(board.code))
      .mockResolvedValueOnce(boardMembers(board.code,{kind:"concept"}));
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    await screen.findByRole("link",{name:"正式成分"});
    fireEvent(document,new Event("visibilitychange"));
    expect((await screen.findByRole("alert")).textContent).toContain("返回成分身份不符");
    expect(screen.queryByRole("list",{name:"已读取成分"})).toBeNull();
  });

  it("isolates same-code kind changes and drops late constituent results from the departed domain",async()=>{
    let finish!:(value:SinaBoardConstituentsPayload)=>void;
    vi.mocked(getSinaBoardConstituents).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}))
      .mockResolvedValueOnce(boardMembers(board.code,{kind:"concept",rows:[{symbol:"600002",name:"概念正式成分",price:null,change_pct:null,net_inflow_yi:null,r0_net_yi:null,source_as_of:null}]}));
    const view=render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    await waitFor(()=>expect(getSinaBoardConstituents).toHaveBeenCalledTimes(1));
    const signal=vi.mocked(getSinaBoardConstituents).mock.calls[0][2]?.signal;
    view.rerender(<DiscoveryDetail target={{...board,catalogKind:"concept"}} selection={selection} onClose={vi.fn()}/>);
    await screen.findByRole("link",{name:"概念正式成分"});
    expect(signal?.aborted).toBe(true);
    await act(async()=>finish(boardMembers(board.code)));
    expect(screen.queryByRole("link",{name:"正式成分"})).toBeNull();
    expect(screen.getByRole("link",{name:"概念正式成分"})).toBeTruthy();
  });

  it.each([{coverage:{total:1,returned:2,complete:true}}, {coverage:{total:3,returned:1,complete:true}},
    {rows:[{symbol:"600001",name:"重复1",price:null,change_pct:null,net_inflow_yi:null,r0_net_yi:null,source_as_of:null},{symbol:"600001",name:"重复2",price:null,change_pct:null,net_inflow_yi:null,r0_net_yi:null,source_as_of:null}],coverage:{total:2,returned:2,complete:true}}])("rejects malformed complete-member coverage instead of making it an empty success",async mutation=>{
    vi.mocked(getSinaBoardConstituents).mockResolvedValue(boardMembers(board.code,mutation));
    render(<DiscoveryDetail target={board} selection={selection} onClose={vi.fn()}/>);
    expect((await screen.findByRole("alert")).textContent).toContain("成分读取失败");
    expect(screen.queryByRole("list",{name:"已读取成分"})).toBeNull();
  });

  it.each(["theme", "board"] as const)("rejects the wrong %s identity instead of displaying another object's members", async kind => {
    if (kind === "theme") vi.mocked(getConceptDetail).mockResolvedValue(concept("OTHER", [{symbol: "600009", name: "错配成员", change: 1}]));
    else vi.mocked(getSinaBoardConstituents).mockResolvedValue(boardMembers("OTHER"));
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
    await screen.findByRole("link", {name: "正式成分"});
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
