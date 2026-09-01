import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RealPositionPanel } from "@/components/detail/real-position-panel";
import { createRealTrade, getRealPositions, type RealPositionsPayload } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(() => {
  cleanup();
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

const mockedGet = vi.mocked(getRealPositions);
const mockedCreate = vi.mocked(createRealTrade);

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
  it("记账必须用表单里填的实际成交价（18.479），不传现价——需求 1 命门", async () => {
    mockedGet.mockResolvedValue(payload([]));
    mockedCreate.mockResolvedValue(undefined);
    render(<RealPositionPanel symbol="603118" currentPrice={18.6} currentName="共进股份" />);
    const priceInput = screen.getByLabelText("实际成交价") as HTMLInputElement;
    await waitFor(() => expect(priceInput.value).toBe("18.6")); // 默认带现价
    fireEvent.change(priceInput, { target: { value: "18.479" } }); // 用户改成实际成交价
    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "1000" } });
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
