import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * 策略健康度面板（#11）。
 *
 * 三条渲染纪律，失守任何一条都会**误导用户**：
 * 1. 「判不出」（insufficient/thin/no_pipeline/…）绝不能看起来像"健康"；
 * 2. `basis` 口径必须显示（market_neutral 与 absolute 不可互相解释）；
 * 3. 核验结论缺失时如实说"尚无产物"，不得留白让人以为已验证。
 */
const healthMock = vi.fn();
const registryMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getStrategyHealth: () => healthMock(),
  getStrategyRegistry: () => registryMock(),
}));

const { StrategyHealthTab } = await import("./strategy-health-tab");

afterEach(() => {
  cleanup();
  healthMock.mockReset();
  registryMock.mockReset();
});

describe("策略健康 · 独立读取", () => {
  const health = { strategies: [{ strategy_key: "k", name: "独立健康结果", status: "ok" }] };
  it("慢登记册不挡健康结果，核验尚未返回不能解释为未跑过", async () => {
    healthMock.mockResolvedValue(health);
    registryMock.mockReturnValue(new Promise(() => {}));
    render(<StrategyHealthTab />);
    expect(await screen.findByText(/独立健康结果/)).toBeTruthy();
    expect(screen.getByText(/登记册正在读取，核验结论未知/)).toBeTruthy();
    expect(screen.queryByText(/未跑过核验/)).toBeNull();
  });

  it("登记册失败不清健康主体，不能声称没有核验产物", async () => {
    healthMock.mockResolvedValue(health);
    registryMock.mockRejectedValue(new Error("registry offline"));
    render(<StrategyHealthTab />);
    expect(await screen.findByText(/独立健康结果/)).toBeTruthy();
    expect(screen.getByText(/核验登记册加载失败/)).toBeTruthy();
    expect(screen.queryByText(/未跑过核验/)).toBeNull();
  });

  it("健康刷新失败保留上次结果，恢复后清错误，旧登记册晚回不覆盖新版", async () => {
    let old!: (items: unknown[]) => void;
    healthMock.mockResolvedValueOnce(health).mockRejectedValueOnce(new Error("health offline")).mockResolvedValue(health);
    registryMock.mockResolvedValueOnce([]).mockReturnValueOnce(new Promise(resolve => { old = resolve; }))
      .mockResolvedValue([{ key: "k", verification: { available: true, verdict: "current verification" } }]);
    render(<StrategyHealthTab />);
    await screen.findByText(/独立健康结果/);
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(await screen.findByText(/策略健康加载失败.*上次读取结果/)).toBeTruthy();
    expect(screen.getByText(/独立健康结果/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(await screen.findByText("current verification")).toBeTruthy();
    await act(async () => old([{ key: "k", verification: { available: true, verdict: "old verification" } }]));
    expect(screen.queryByText("old verification")).toBeNull();
    expect(screen.queryByText(/health offline/)).toBeNull();
  });
});

function stub(health: unknown, registry: unknown[] = []) {
  healthMock.mockResolvedValue(health);
  registryMock.mockResolvedValue(registry);
}

describe("StrategyHealthTab", () => {
  it("把「判不出」标注出来，不与健康混同", async () => {
    stub(
      {
        strategies: [{ strategy_key: "daily_picks", name: "每日精选", status: "insufficient",
                       basis: "market_neutral", lifecycle: "active" }],
        counts: { total: 1, evaluable: 0, attention: 0 },
        caveat: "insufficient/thin/no_pipeline 均为「判不出」，不是 ok",
    });
    render(<StrategyHealthTab />);
    await waitFor(() => expect(screen.getByText(/每日精选/)).toBeTruthy());
    // 必须出现"判不出"字样——否则用户会以为一切正常
    // 精确断言徽标本身：全文匹配会被 caveat 里的"判不出"掩盖（注入验证抓到过）
    expect(screen.getByTestId("status-badge-insufficient").textContent).toContain("判不出");
    expect(screen.queryByText("健康")).toBeNull();
  });

  it("有判定的状态显示对应徽标（不带判不出字样）", async () => {
    stub({
        strategies: [{ strategy_key: "k", name: "某策略", status: "ok", basis: "absolute" }],
        counts: { total: 1, evaluable: 1, attention: 0 },
      }, []);
    render(<StrategyHealthTab />);
    await waitFor(() => expect(screen.getByText(/某策略/)).toBeTruthy());
    const badge = screen.getByTestId("status-badge-ok");
    expect(badge.textContent).toContain("健康");
    expect(badge.textContent).not.toContain("判不出");
  });

  it("显示 basis 口径（两种口径不可互相解释）", async () => {
    stub({ strategies: [{ strategy_key: "k", name: "某策略", status: "ok",
                               basis: "market_neutral" }] }, []);
    render(<StrategyHealthTab />);
    await waitFor(() => expect(screen.getByText(/market_neutral/)).toBeTruthy());
  });

  it("核验结论缺失时如实说明，不留白", async () => {
    stub({ strategies: [{ strategy_key: "k", name: "某策略", status: "ok" }] }, [{ key: "k", verification: { available: false, reason: "尚无核验产物" } }]);
    render(<StrategyHealthTab />);
    await waitFor(() => expect(screen.getByText(/尚无产物/)).toBeTruthy());
  });

  it("核验结论存在时展示 verdict 与超期标记", async () => {
    stub({ strategies: [{ strategy_key: "k", name: "某策略", status: "ok" }] }, [{ key: "k", verification: { available: true, stale: true, verdict: "reject",
                                             headline: "五步全通过 ⇒ reject" } }]);
    render(<StrategyHealthTab />);
    // headline 里同样含 reject ⇒ 用 getAll
    await waitFor(() => expect(screen.getAllByText(/reject/).length).toBeGreaterThan(0));
    expect(screen.getByText(/产物超期/)).toBeTruthy();
  });

  it("接口失败时给出可见错误，不静默空白", async () => {
    healthMock.mockRejectedValue(new Error("网络异常"));
    registryMock.mockResolvedValue([]);
    render(<StrategyHealthTab />);
    await waitFor(() => expect(screen.getByText(/加载失败/)).toBeTruthy());
  });
});
