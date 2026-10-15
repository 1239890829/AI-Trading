import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RealPositionPanel } from "@/components/detail/real-position-panel";
import { useRealPositions } from "@/hooks/use-real-positions";
import { createRealTrade, deleteRealPosition, getRealPositions, overrideRealPosition, type RealPositionsPayload } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks(); // mock 调用计数跨测试累积，不清会把上一例的调用算到本例头上
});

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getRealPositions: vi.fn(),
    createRealTrade: vi.fn(),
    overrideRealPosition: vi.fn(),
    deleteRealPosition: vi.fn(),
    deleteRealTrade: vi.fn(),
  };
});

vi.mock("@/lib/api/validation", () => ({getValidationEntries: vi.fn(async () => [{id:"daily:2026-08-31:603118",scope:"daily",selected_at:"2026-08-31T09:35:00+08:00",reference_price:18.4}])}));
async function linkSelection() {
  await act(async () => {await Promise.resolve();});
  fireEvent.change(screen.getByLabelText("准确成交时间"), {target:{value:"10:00:00"}});
  const trigger = screen.getByRole("button",{name:"原入选：请选择"});
  fireEvent.keyDown(trigger,{key:"ArrowDown"});
  await act(async () => {if(vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(0);});
  fireEvent.click(screen.getByRole("menuitemradio",{name:/精选/}));
}

beforeEach(() => {vi.useRealTimers();});

const mockedGet = vi.mocked(getRealPositions);
const mockedCreate = vi.mocked(createRealTrade);

vi.mock("@/lib/market-hours", async (original) => ({
  ...await original<typeof import("@/lib/market-hours")>(),
  isTradingSession: () => true,
}));

const payload = (rows: Partial<RealPositionsPayload["items"][number]>[] = []): RealPositionsPayload => ({
  items: rows.map((r) => ({
    symbol: "603118",
    name: "共进股份",
    quantity: 1000,
    avg_cost: 18.479,
    cost_total: 18479,
    last_price: 18.6,
    day_change_pct: 0.65,
    market_value: 18600,
    unrealized_pnl: 121,
    unrealized_pct: 0.65,
    realized_pnl: 0,
    overridden: false,
    trade_count: 1,
    last_traded_at: "2026-08-31",
    ...r,
  })),
  cleared: [],
  total: { market_value: 18600, cost_total: 18479, unrealized_pnl: 121, realized_pnl: 0 },
  count: rows.length,
});

describe("RealPositionPanel 真实持仓", () => {
  it("只读旁览完整保留持仓与汇总，没有记账、修正或删除入口", async () => {
    mockedGet.mockResolvedValue(payload([{overridden: true}]));
    const view = render(<RealPositionPanel readOnly />);
    await screen.findByText("共进股份");
    expect(screen.getByText(/总市值/)).toBeTruthy();
    expect(screen.getByText("已修正")).toBeTruthy();
    expect(screen.queryByLabelText("实际成交价")).toBeNull();
    for (const name of ["记账", "改", "删", "保存"]) expect(screen.queryByRole("button", {name})).toBeNull();
    expect(mockedCreate).not.toHaveBeenCalled();
    expect(vi.mocked(overrideRealPosition)).not.toHaveBeenCalled();
    expect(vi.mocked(deleteRealPosition)).not.toHaveBeenCalled();
    view.rerender(<RealPositionPanel />);
    expect(screen.getByLabelText("实际成交价")).toBeTruthy();
    expect(screen.getByTitle("手动修正数量/总成本")).toBeTruthy();
  });

  it("记账必须用表单里填的实际成交价（18.479），不传现价——需求 1 命门", async () => {
    mockedGet.mockResolvedValue(payload([]));
    mockedCreate.mockResolvedValue(undefined);
    render(<RealPositionPanel symbol="603118" currentPrice={18.6} currentName="共进股份" />);
    const priceInput = screen.getByLabelText("实际成交价") as HTMLInputElement;
    await waitFor(() => expect(priceInput.value).toBe("18.6")); // 默认带现价
    fireEvent.change(priceInput, { target: { value: "18.479" } }); // 用户改成实际成交价
    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "1000" } });
    await linkSelection();
    fireEvent.click(screen.getByText("记账"));
    await waitFor(() => expect(mockedCreate).toHaveBeenCalled());
    const arg = mockedCreate.mock.calls[0][0];
    expect(arg.fill_price).toBe(18.479); // 按填入价，绝不按现价
    expect(arg.quantity).toBe(1000);
    expect(arg.side).toBe("buy");
  });

  it("展示字段：数量/摊薄成本/市值/浮动盈亏/已实现盈亏 + 手动修正徽标", async () => {
    mockedGet.mockResolvedValue(payload([{ overridden: true, unrealized_pnl: 121, unrealized_pct: 0.65 }]));
    render(<RealPositionPanel />);
    await waitFor(() => expect(screen.getByText("共进股份")).toBeTruthy());
    const t = document.body.textContent ?? "";
    expect(t).toContain("18.48"); // 摊薄成本 18.479 按两位小数展示
    expect(t).toContain("已修正"); // 卡片化（2026-09-01）后徽标文案缩短
    expect(t).toContain("不构成买卖建议");
  });

  it("汇总行：总市值/总成本/浮动盈亏/累计已实现", async () => {
    mockedGet.mockResolvedValue(payload([{ realized_pnl: 0 }]));
    render(<RealPositionPanel />);
    await waitFor(() => expect(screen.getByText(/总市值/)).toBeTruthy());
    const t = document.body.textContent ?? "";
    expect(t).toContain("累计已实现");
  });

  it("数量非法（非正整数）→ 拒绝提交且不调 API", async () => {
    mockedGet.mockResolvedValue(payload([]));
    render(<RealPositionPanel symbol="603118" currentPrice={18.6} />);
    fireEvent.change(screen.getByLabelText("实际成交价"), { target: { value: "18.479" } });
    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "0" } });
    fireEvent.click(screen.getByText("记账"));
    await waitFor(() => expect(screen.getByText("数量必须为正整数（股）")).toBeTruthy());
    expect(mockedCreate).not.toHaveBeenCalled();
  });
});

it("an edited manual record keeps its symbol and actual price when detail selection changes", () => {
  mockedGet.mockResolvedValue(payload([]));
  const view = render(<RealPositionPanel symbol="600127" currentPrice={10} />);
  fireEvent.change(screen.getByLabelText("实际成交价"), { target: { value: "9.8" } });
  view.rerender(<RealPositionPanel symbol="000001" currentPrice={15} />);
  expect((screen.getByLabelText("代码") as HTMLInputElement).value).toBe("600127");
  expect((screen.getByLabelText("实际成交价") as HTMLInputElement).value).toBe("9.8");
});

function SharedOwner() {
  const resource = useRealPositions();
  return <><output data-testid="owner-count">{resource.data?.count ?? "pending"}</output><button onClick={() => void resource.reload()}>刷新所有者</button><RealPositionPanel symbol="603118" currentPrice={18.6} realPositions={resource} /></>;
}
async function flush() { await act(async () => {}); }
async function advance(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
function clock() { vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-09T10:00:00+08:00")); }

describe("RealPositionPanel · 上游持仓读取复用", () => {
  it("只有所有者首读及15s轮询，刷新失败保留旧持仓，后续恢复", async () => {
    clock();
    mockedGet.mockResolvedValueOnce(payload([{}])).mockRejectedValueOnce(new Error("持仓刷新失败")).mockResolvedValue(payload([]));
    render(<SharedOwner />);
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("owner-count").textContent).toBe("1");
    await advance(15_000);
    expect(mockedGet).toHaveBeenCalledTimes(2);
    expect(screen.getByText("共进股份")).toBeTruthy();
    expect(screen.getByText("持仓刷新失败")).toBeTruthy();
    await advance(15_000);
    expect(mockedGet).toHaveBeenCalledTimes(3);
    expect(screen.getByTestId("owner-count").textContent).toBe("0");
    expect(screen.queryByText("共进股份")).toBeNull();
    expect(screen.queryByText("持仓刷新失败")).toBeNull();
  });

  it.each(["记账", "修正", "删除"])("%s后即时刷新同一所有者，左侧消费者与面板同步", async operation => {
    clock();
    mockedGet.mockResolvedValueOnce(payload([{}])).mockResolvedValue(payload([]));
    mockedCreate.mockResolvedValue(undefined);
    vi.mocked(overrideRealPosition).mockResolvedValue(undefined);
    vi.mocked(deleteRealPosition).mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<SharedOwner />);
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(1);
    if (operation === "记账") {
      fireEvent.change(screen.getByLabelText("数量"), { target: { value: "100" } });
      await linkSelection();
      fireEvent.click(screen.getByRole("button", { name: "记账" }));
    } else if (operation === "修正") {
      fireEvent.click(screen.getByRole("button", { name: "改" }));
      fireEvent.change(screen.getByLabelText("修正数量"), { target: { value: "200" } });
      fireEvent.change(screen.getByLabelText("修正总成本"), { target: { value: "3600" } });
      fireEvent.click(screen.getByRole("button", { name: "保存" }));
    } else fireEvent.click(screen.getByRole("button", { name: "删" }));
    await flush();
    expect(operation === "记账" ? mockedCreate : operation === "修正" ? vi.mocked(overrideRealPosition) : vi.mocked(deleteRealPosition)).toHaveBeenCalledTimes(1);
    expect(mockedGet).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("owner-count").textContent).toBe("0");
    expect(screen.queryByText("共进股份")).toBeNull();
  });

  it.each(["success", "failure"])("写后刷新先完成时，旧轮询晚到%s不能覆盖新结果或错误", async outcome => {
    clock();
    let resolve!: (data: RealPositionsPayload) => void;
    let reject!: (error: Error) => void;
    mockedGet.mockImplementationOnce(() => new Promise((done, fail) => {resolve = done; reject = fail;})).mockResolvedValue(payload([]));
    render(<SharedOwner />);
    fireEvent.click(screen.getByRole("button", { name: "刷新所有者" }));
    await flush();
    expect(screen.getByTestId("owner-count").textContent).toBe("0");
    await act(async () => { if (outcome === "success") resolve(payload([{}])); else reject(new Error("旧轮询失败")); });
    expect(screen.getByTestId("owner-count").textContent).toBe("0");
    expect(screen.queryByText("共进股份")).toBeNull();
    expect(screen.queryByText("旧轮询失败")).toBeNull();
  });

  it("上游加载/失败不启动备用读，取消上游后独立面板仍立即读取", async () => {
    clock();
    const realPositions = {data: null, error: "上游读取失败", reload: vi.fn(async () => {})};
    mockedGet.mockResolvedValue(payload([{}]));
    const view = render(<RealPositionPanel realPositions={realPositions} />);
    await advance(30_000);
    expect(mockedGet).not.toHaveBeenCalled();
    expect(screen.getByText("上游读取失败")).toBeTruthy();
    view.rerender(<RealPositionPanel />);
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(1);
    expect(screen.getByText("共进股份")).toBeTruthy();
  });
});
