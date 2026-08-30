/** TradeForm 组件渲染测试。
 *
 * 存在的理由：纯函数单测证明不了「组件真的用了正确的函数」。
 * parseNum 的单测早就绿了，但 TradeForm 是否调用它、预估金额是否算对、
 * 提交时传出去的价格是多少——只有渲染+交互测试能证明。
 * 千分位截断 P0（下单价变 1 元）正是这类盲区。 */
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TradeForm } from "./trade-form";
import { checkOrderRisk, placePaperOrder } from "@/lib/api";
import type { OrderCheckResult } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  checkOrderRisk: vi.fn(),
  placePaperOrder: vi.fn(),
}));

const OK: OrderCheckResult = {
  allowed: true,
  max_qty: 21_700,
  reasons: ["通过预检"],
  warnings: [],
  state: "震荡偏多",
};

const BLOCKED: OrderCheckResult = {
  allowed: false,
  max_qty: 0,
  reasons: ["单票仓位上限 25%：600519 买入后约占 39%（现持仓 259,480），本次最多可再买 0 股"],
  warnings: [],
  state: "震荡偏多",
};

// vitest 未开 globals，RTL 的自动 cleanup 不会注册，必须手动清理：
// 否则多个 TradeForm 实例会累积在 document.body，getByText 命中多个元素
afterEach(cleanup);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(checkOrderRisk).mockResolvedValue(OK);
  vi.mocked(placePaperOrder).mockResolvedValue({
    id: 1,
    status: "filled",
    filled_price: 1297.4,
    fee: 32.44,
  });
});

/** 等待 300ms 防抖的风控预检落地。
 *  该行出现即代表 riskCheck 已赋值——否则点击提交会因 `!riskCheck` 早退，
 *  测试会误判成"点击无效"。 */
async function waitRiskReady(label: string | RegExp = "风控可买上限") {
  await waitFor(() => expect(screen.getByText(label)).toBeTruthy());
}

describe("TradeForm", () => {
  it("千分位回填的价格被正确解析（回归：曾截断成 1，预估金额显示 100.00）", async () => {
    render(<TradeForm symbol="600519" price={1297.4} />);

    // 价格框回填的是 fmt()，即 "1,297.40"
    expect((screen.getByLabelText("价格") as HTMLInputElement).value).toBe("1,297.40");
    // 1297.4 × 100 = 129,740.00；parseFloat 截断时会是 100.00
    await waitFor(() => expect(screen.getByText(/129,740\.00/)).toBeTruthy());
  });

  it("提交给下单接口的是解析后的真实价格，不是千分位截断值", async () => {
    render(<TradeForm symbol="600519" price={1297.4} />);
    await waitRiskReady();

    const btn = screen.getByRole("button", { name: "买入 600519" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);

    await waitFor(() =>
      expect(placePaperOrder).toHaveBeenCalledWith("600519", "buy", 1297.4, 100)
    );
  });

  it("买入非 100 整数倍时禁用提交", async () => {
    render(<TradeForm symbol="600519" price={1297.4} />);
    await waitRiskReady();

    const btn = screen.getByRole("button", { name: "买入 600519" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);

    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "150" } });

    await waitFor(() => expect(btn.disabled).toBe(true));
  });

  it("风控不放行时禁用提交并展示拦截原因", async () => {
    vi.mocked(checkOrderRisk).mockResolvedValue(BLOCKED);
    render(<TradeForm symbol="600519" price={1297.4} />);
    await waitRiskReady();

    const btn = screen.getByRole("button", { name: "买入 600519" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByText(/单票仓位上限/)).toBeTruthy();

    fireEvent.click(btn);
    expect(placePaperOrder).not.toHaveBeenCalled();
  });

  it("展示风控可买上限（千分位）；卖出侧显示可卖数量（T+1）", async () => {
    render(<TradeForm symbol="600519" price={1297.4} />);
    await waitRiskReady();
    expect(screen.getByText("21,700 股")).toBeTruthy();

    // 卖出侧：max_qty 语义为可卖数量
    vi.mocked(checkOrderRisk).mockResolvedValue({ ...OK, max_qty: 0 });
    fireEvent.click(screen.getByRole("button", { name: "卖出" }));
    // 标签随 side 立即切换，但数值要等防抖后的新预检返回才更新，所以等值而不是等标签
    await waitFor(() => expect(screen.getByText("0 股")).toBeTruthy());
  });
});
