/** TradeForm 组件渲染测试。
 *
 * 存在的理由：纯函数单测证明不了「组件真的用了正确的函数」。
 * parseNum 的单测早就绿了，但 TradeForm 是否调用它、预估金额是否算对、
 * 提交时传出去的价格是多少——只有渲染+交互测试能证明。
 * 千分位截断 P0（下单价变 1 元）正是这类盲区。 */
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

// ------------------------------------------------------------------ R19 时序与草稿
//
// 两个独立缺陷，都在「异步结果 / 外部输入」与「用户意图」的边界上：
// ① 手填限价被行情 tick 冲掉（原实现用 `symbol:price` 作重填 key，每拍都算"变了"）；
// ② 预检结果没绑订单签名，旧回包能覆盖新结论（实测 1000 股判拒后，100 股旧"允许"
//    回包把按钮重新点亮）。
// 判据刻意包含「真正提交出去的载荷」——只断言 DOM 状态证明不了它提交的是什么。

describe("TradeForm · R19 手填限价不被 tick 覆盖", () => {
  it("手填后连续 tick 不变价；显式点「使用现价」才回到现价", async () => {
    const { rerender } = render(<TradeForm symbol="600519" price={9.5} />);
    const priceInput = () => screen.getByLabelText("价格") as HTMLInputElement;
    expect(priceInput().value).toBe("9.50");

    // 用户点进价格框并**照着现价敲一遍**（9.50 == 现价）——这正是实测复现路径：
    // React 对值未变化的 change 事件去重，故"进入输入框"才是可靠的接管信号
    fireEvent.focus(priceInput());
    fireEvent.change(priceInput(), { target: { value: "9.50" } });
    // 连续两拍行情更新（1Hz 节奏下的常态）
    rerender(<TradeForm symbol="600519" price={10.1} />);
    expect(priceInput().value).toBe("9.50"); // ← 旧实现此处会变成 10.10
    rerender(<TradeForm symbol="600519" price={10.4} />);
    expect(priceInput().value).toBe("9.50");
    // 锁定状态对用户可见（否则"为什么不跟着现价走"无从解释）
    expect(screen.getByText(/已锁手填价/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "使用现价" }));
    expect(priceInput().value).toBe("10.40");
    await waitFor(() => expect(screen.queryByText(/已锁手填价/)).toBeNull());
  });

  it("未编辑过的草稿仍跟随现价刷新（首次回填语义不变）", () => {
    const { rerender } = render(<TradeForm symbol="600519" price={9.5} />);
    expect((screen.getByLabelText("价格") as HTMLInputElement).value).toBe("9.50");

    rerender(<TradeForm symbol="600519" price={10.1} />);
    expect((screen.getByLabelText("价格") as HTMLInputElement).value).toBe("10.10");
    expect(screen.queryByText(/已锁手填价/)).toBeNull();
  });

  it("换标的时草稿作废，以新标的现价重新起手", () => {
    const { rerender } = render(<TradeForm symbol="600519" price={9.5} />);
    fireEvent.focus(screen.getByLabelText("价格"));
    fireEvent.change(screen.getByLabelText("价格"), { target: { value: "9.50" } });

    rerender(<TradeForm symbol="600036" price={12.0} />);
    expect((screen.getByLabelText("价格") as HTMLInputElement).value).toBe("12.00");
    expect(screen.queryByText(/已锁手填价/)).toBeNull();
  });

  it("提交的是手填价本身（核对真正的下单载荷）", async () => {
    const { rerender } = render(<TradeForm symbol="600519" price={9.5} />);
    fireEvent.focus(screen.getByLabelText("价格"));
    fireEvent.change(screen.getByLabelText("价格"), { target: { value: "9.50" } });
    rerender(<TradeForm symbol="600519" price={10.1} />);
    await waitRiskReady();

    fireEvent.click(screen.getByRole("button", { name: "买入 600519" }));
    await waitFor(() => expect(placePaperOrder).toHaveBeenCalledWith("600519", "buy", 9.5, 100));
  });
});

describe("TradeForm · R19 预检结果绑定订单签名", () => {
  /** 手动控制每个预检请求何时返回，用来构造乱序。 */
  function deferredRisk() {
    const resolvers: Array<(r: OrderCheckResult) => void> = [];
    vi.mocked(checkOrderRisk).mockImplementation(
      () => new Promise<OrderCheckResult>((res) => { resolvers.push(res); })
    );
    return resolvers;
  }

  it("乱序回包：旧「允许」不得覆盖新「拒绝」（实测缺陷的精确复现）", async () => {
    const resolvers = deferredRisk();
    render(<TradeForm symbol="600519" price={100.0} />);

    // 第 1 单：100 股
    await waitFor(() => expect(resolvers.length).toBe(1), { timeout: 3000 });
    expect(vi.mocked(checkOrderRisk).mock.calls[0][0]).toMatchObject({ quantity: 100 });

    // 用户改成 1000 股 → 第 2 单
    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "1000" } });
    await waitFor(() => expect(resolvers.length).toBe(2), { timeout: 3000 });
    expect(vi.mocked(checkOrderRisk).mock.calls[1][0]).toMatchObject({ quantity: 1000 });

    // 第 2 单（1000 股）先回：拒绝
    await act(async () => { resolvers[1](BLOCKED); });
    const btn = screen.getByRole("button", { name: "买入 600519" }) as HTMLButtonElement;
    await waitFor(() => expect(btn.disabled).toBe(true));
    expect(screen.getByText(/单票仓位上限/)).toBeTruthy();

    // 第 1 单（100 股）后回：允许 —— **绝不能**因此点亮当前 1000 股的按钮
    await act(async () => { resolvers[0](OK); });
    expect(btn.disabled).toBe(true);
    expect(screen.queryByText("21,700 股")).toBeNull(); // 旧结果的"可买上限"也不得显示

    fireEvent.click(btn);
    expect(placePaperOrder).not.toHaveBeenCalled();
  });

  it("签名失配提交被拦：改数量后旧结论不参与放行", async () => {
    const resolvers = deferredRisk();
    render(<TradeForm symbol="600519" price={100.0} />);
    await waitFor(() => expect(resolvers.length).toBe(1), { timeout: 3000 });
    await act(async () => { resolvers[0](OK); }); // 100 股通过
    const btn = screen.getByRole("button", { name: "买入 600519" }) as HTMLButtonElement;
    await waitFor(() => expect(btn.disabled).toBe(false));

    // 改成 200 股：新预检尚未返回，此时提交必须被拦（不得复用 100 股的"允许"）
    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "200" } });
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByText(/预检结果与当前订单不一致/)).toBeTruthy());
    expect(placePaperOrder).not.toHaveBeenCalled();
  });

  it("方向切换同样使旧签名失效（买 → 卖）", async () => {
    const resolvers = deferredRisk();
    render(<TradeForm symbol="600519" price={100.0} />);
    await waitFor(() => expect(resolvers.length).toBe(1), { timeout: 3000 });
    await act(async () => { resolvers[0](OK); });
    const btn = () => screen.getByRole("button", { name: /600519$/ }) as HTMLButtonElement;
    await waitFor(() => expect(btn().disabled).toBe(false));

    fireEvent.click(screen.getByRole("button", { name: "卖出" }));
    fireEvent.click(btn());
    await waitFor(() => expect(screen.getByText(/预检结果与当前订单不一致/)).toBeTruthy());
    expect(placePaperOrder).not.toHaveBeenCalled();
  });

  it("旧请求失败不得清掉更新一轮的成功结论", async () => {
    const resolvers: Array<{ res: (r: OrderCheckResult) => void; rej: (e: Error) => void }> = [];
    vi.mocked(checkOrderRisk).mockImplementation(
      () => new Promise<OrderCheckResult>((res, rej) => { resolvers.push({ res, rej }); })
    );
    render(<TradeForm symbol="600519" price={100.0} />);
    await waitFor(() => expect(resolvers.length).toBe(1), { timeout: 3000 });

    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "200" } });
    await waitFor(() => expect(resolvers.length).toBe(2), { timeout: 3000 });

    // 新一轮先成功
    await act(async () => { resolvers[1].res(OK); });
    await waitFor(() => expect(screen.getByText("21,700 股")).toBeTruthy());

    // 旧一轮随后失败 —— 不得把上面的结论清掉
    await act(async () => { resolvers[0].rej(new Error("network")); });
    expect(screen.getByText("21,700 股")).toBeTruthy();
    expect((screen.getByRole("button", { name: "买入 600519" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
