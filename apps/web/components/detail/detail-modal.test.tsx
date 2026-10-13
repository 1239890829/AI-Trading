import {act, cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {DetailModalProvider, useDetailModal} from "./detail-modal";
import {getEventsForSymbol, getNewsContent, getStockAttribution, type EventSummary} from "@/lib/api";

vi.mock("@/lib/api", () => ({getEventsForSymbol: vi.fn(), getNewsContent: vi.fn(), getStockAttribution: vi.fn()}));
vi.mock("@/components/ui/modal-shell", () => ({ModalShell: ({children, open, onClose}: {children: React.ReactNode; open: boolean; onClose: () => void}) => <section aria-hidden={!open || undefined} inert={!open} data-testid="detail-shell">{open && <button onClick={onClose}>关闭详情</button>}{children}</section>}));
vi.mock("@/components/detail/capital-flow-panel", () => ({CapitalFlowPanel: () => null}));
vi.mock("@/components/detail/symbol-detail-context", () => ({useSymbolDetail: () => ({open: vi.fn()})}));
afterEach(() => {cleanup(); vi.resetAllMocks();});

function Openers() {
  const {open} = useDetailModal();
  return <>{["600519", "000001"].map(symbol => <button key={symbol} onClick={() => open({kind: "feed", title: symbol + "关联消息", symbol})}>打开 {symbol}</button>)}</>;
}
function host() {return render(<DetailModalProvider><Openers /></DetailModalProvider>);}

it("feed direction remains scoped to the requested stock across the click into detail", async () => {
  const event: EventSummary = {id: 8, title: "上游涨价，公司成本上升", url: null, source: "来源", source_tier: 3,
    published_at: "2026-10-12T09:00:00+08:00", fact_kind: "fact", certainty: "done", category: "corporate",
    half_life_hours: 48, source_symbol: "600519", is_active: true,
    interpretation_ref: {event_id: 8, version_id: 20, observation_id: 19, state: "active", available_at: "2026-10-12T09:01:00+08:00"},
    directions: [
      {target_type: "theme", target: "消费", direction: 1, strength: 2, chain: "原规则记录", basis: "题材命中", matched_by: "name"},
      {target_type: "symbol", target: "600519", direction: -1, strength: 2, chain: "成本压力", basis: "单股记录", matched_by: "source"},
    ]};
  const original = JSON.stringify(event);
  vi.mocked(getEventsForSymbol).mockResolvedValue({symbol: "600519", themes: ["消费"], count: 1, items: [event]});
  vi.mocked(getStockAttribution).mockResolvedValue({schema_version: 1, symbol: "600519", count: 0, items: []});
  host(); fireEvent.click(screen.getByRole("button", {name: "打开 600519"}));
  await screen.findByText("规则负向线索");
  expect(screen.queryByText("规则正向线索")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: /上游涨价，公司成本上升/}));
  expect(screen.queryByRole("tabpanel")).toBeNull();
  fireEvent.click(screen.getByRole("tab", {name: "个股 · 600519"}));
  expect(screen.getByText("规则负向线索")).toBeTruthy();
  expect(screen.getByText(/#20 · 生效/)).toBeTruthy();
  expect(JSON.stringify(event)).toBe(original);
});

it("a first feed failure shows retry and cannot claim a valid empty set", async () => {
  vi.mocked(getEventsForSymbol).mockRejectedValueOnce(new Error("feed-offline")).mockResolvedValueOnce({symbol: "600519", themes: [], count: 0, items: []});
  host(); fireEvent.click(screen.getByRole("button", {name: "打开 600519"}));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("关联事件读取失败"));
  expect(screen.queryByText(/关联事件 · 0 条/)).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "重试关联事件"}));
  expect(await screen.findByText(/关联事件 · 0 条/)).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull(); expect(getEventsForSymbol).toHaveBeenCalledTimes(2);
});

it("a different symbol cannot inherit the old feed failure or its late reply", async () => {
  let failOld!: (error: Error) => void;
  vi.mocked(getEventsForSymbol).mockReturnValueOnce(new Promise((_resolve, reject) => {failOld = reject;})).mockResolvedValueOnce({symbol: "000001", themes: [], count: 0, items: []});
  host(); fireEvent.click(screen.getByRole("button", {name: "打开 600519"})); await act(async () => {});
  fireEvent.click(screen.getByRole("button", {name: "打开 000001"})); await screen.findByText(/关联事件 · 0 条/);
  await act(async () => {failOld(new Error("old-feed-error"));});
  expect(screen.queryByRole("alert")).toBeNull(); expect(screen.queryByText(/old-feed-error/)).toBeNull();
  expect(screen.getByText(/关联事件 · 0 条/)).toBeTruthy();
});

it("closing halts the feed consumer during exit and reopening the same symbol cannot accept its late result", async () => {
  vi.useFakeTimers();
  try {
    let finish!: (data: Awaited<ReturnType<typeof getEventsForSymbol>>) => void;
    vi.mocked(getEventsForSymbol).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}))
      .mockResolvedValueOnce({symbol:"600519", themes:[], count:0, items:[]});
    host(); fireEvent.click(screen.getByRole("button", {name:"打开 600519"}));
    fireEvent.click(screen.getByRole("button", {name:"关闭详情"}));
    expect(screen.getByTestId("detail-shell").hasAttribute("inert")).toBe(true);
    expect(screen.queryByText("关联事件加载中…")).toBeNull();
    fireEvent(document, new Event("visibilitychange"));
    expect(getEventsForSymbol).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", {name:"打开 600519"}));
    await act(async () => {});
    await act(async () => {finish({symbol:"600519", themes:["旧回复"], count:1, items:[{id:99, title:"已关闭会话的晚到事件", directions:[], url:null, source:"fixture", source_tier:3, published_at:"2026-10-12T09:00:00+08:00", fact_kind:"fact", certainty:"done", category:"corporate", half_life_hours:48, source_symbol:"600519", is_active:true}]}); vi.advanceTimersByTime(200);});
    expect(screen.getByText(/关联事件 · 0 条/)).toBeTruthy();
    expect(screen.queryByText(/已关闭会话的晚到事件/)).toBeNull();
    expect(getEventsForSymbol).toHaveBeenCalledTimes(2);
  } finally {vi.useRealTimers();}
});

it("selection evidence keeps original entry facts beside current explanations", async () => {
  vi.mocked(getStockAttribution).mockResolvedValue({schema_version: 1, symbol: "600825", count: 0, items: []});
  function SelectionOpener() {
    const {open} = useDetailModal();
    return <button onClick={() => open({kind: "attribution", title: "原选股依据", symbol: "600825", selectionEntry: {state: "recorded", selected_at: "2026-10-12T09:32:00+08:00", reference_price: 8, reference_change_pct: 2, quote_as_of: "2026-10-12T09:31:59+08:00", source: "daily_generation", source_version: "v1", semantics: "observation_only_not_fill"}, eventRefs: [{event_id: 18, version_id: 20, observation_id: 19, available_at: "2026-10-12T09:31:00+08:00", state: "active"}]})}>打开选股依据</button>;
  }
  render(<DetailModalProvider><SelectionOpener /></DetailModalProvider>);
  fireEvent.click(screen.getByRole("button", {name: "打开选股依据"}));
  await screen.findByText(/暂未找到关联事件/);
  expect(screen.getByLabelText("原首次加入依据").textContent).toContain("09:32:00");
  expect(screen.getByLabelText("原首次加入依据").textContent).toContain("¥8.00");
  expect(screen.getByText("选股记录事件引用")).toBeTruthy();
  expect(screen.getByText(/原解释 #20/)).toBeTruthy();
  expect(screen.queryByText(/原文链接缺失/)).toBeNull();
  expect(getEventsForSymbol).not.toHaveBeenCalled();
});


it("keeps legacy paragraph-only source responses readable from the new material overview", async () => {
  vi.mocked(getNewsContent).mockResolvedValue({kind:"news",title:"原报道",source_label:"来源",published:null,paragraphs:["政策尚未落地，最高补贴30%，需完成注册。"],blocks:[],truncated:false,url:"https://example.com/news"});
  function NewsOpener(){const {open}=useDetailModal();return <button onClick={()=>open({kind:"news",title:"原报道",url:"https://example.com/news"})}>打开旧格式原文</button>;}
  render(<DetailModalProvider><NewsOpener/></DetailModalProvider>);
  fireEvent.click(screen.getByRole("button",{name:"打开旧格式原文"}));
  await screen.findByRole("button",{name:"完整原文"});
  expect(screen.queryByText("正文抓取为空（源站未返回内容）")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"完整原文"}));
  expect(screen.getByLabelText("完整原文").textContent).toContain("政策尚未落地，最高补贴30%，需完成注册。");
  expect(getNewsContent).toHaveBeenCalledTimes(1);
});
