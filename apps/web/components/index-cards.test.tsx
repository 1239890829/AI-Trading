import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { IndexCards } from "@/components/index-cards";
import type { Quote } from "@/types/market";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

const baseAudit = { source: "ths", quality: "high" as const, quality_reasons: [], received_at: "t0" };

function idx(symbol: string, name: string, market: string, price: number): Quote {
  return { ...baseAudit, symbol, name, market, price, change_pct: 0.5 };
}

const indices = [
  idx("000001", "上证指数", "SH", 3300.5),
  idx("399001", "深证成指", "SZ", 10500.2),
  idx("399006", "创业板指", "SZ", 2100.8),
];

describe("IndexCards 指数点击交互", () => {
  it("点击指数卡 → 回调收到带市场前缀的详情 symbol（与自选股点击同一交互）", () => {
    const onSelect = vi.fn();
    render(<IndexCards indices={indices} onSelect={onSelect} />);
    fireEvent.click(screen.getByTitle(/上证指数/));
    expect(onSelect).toHaveBeenCalledWith("sh000001");
    fireEvent.click(screen.getByTitle(/深证成指/));
    expect(onSelect).toHaveBeenCalledWith("sz399001");
    fireEvent.click(screen.getByTitle(/创业板指/));
    expect(onSelect).toHaveBeenCalledWith("sz399006");
  });

  it("已带前缀的指数 symbol（腾讯源形态）原样透传，不二次加前缀", () => {
    const onSelect = vi.fn();
    render(<IndexCards indices={[idx("sh000001", "上证指数", "SH", 3300.5)]} onSelect={onSelect} />);
    fireEvent.click(screen.getByTitle(/上证指数/));
    expect(onSelect).toHaveBeenCalledWith("sh000001");
  });

  it("收起仍按市场代码显示上证、科创50、创业板，乱序和源名称变化不改变选择", () => {
    const onSelect = vi.fn();
    render(<IndexCards indices={[
      idx("000300", "沪深300", "SH", 3900.2),
      idx("sz399006", "创业板指数", "SZ", 2100.8),
      idx("sh000688", "上证科创板50成份指数", "SH", 1000.6),
      indices[1],
      indices[0],
    ]} selected="sh000688" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /指数.*收起/ }));
    expect(screen.getByText("上证指数")).toBeTruthy();
    expect(screen.getByText("科创50")).toBeTruthy();
    expect(screen.getByText("创业板指")).toBeTruthy();
    expect(screen.queryByText("沪深300")).toBeNull();
    expect(screen.queryByText("深证成指")).toBeNull();
    const star = screen.getByTitle(/科创50 · 来源/);
    expect(star.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(star);
    expect(onSelect).toHaveBeenCalledWith("sh000688");
    fireEvent.click(screen.getByRole("button", { name: /指数.*展开/ }));
    expect(screen.getByText("沪深300")).toBeTruthy();
    expect(screen.getByText("深证成指")).toBeTruthy();
  });

  it("收起缺失基准明确显示未返回，不拿其他市场同代码或另一指数代替", () => {
    const onSelect = vi.fn();
    render(<IndexCards indices={[
      indices[0], indices[1], idx("000688", "其他市场同代码", "SZ", 88),
    ]} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /指数.*收起/ }));
    const missingStar = screen.getByTitle("科创50 · 本轮未返回该指数") as HTMLButtonElement;
    const missingGrowth = screen.getByTitle("创业板指 · 本轮未返回该指数") as HTMLButtonElement;
    expect(missingStar.disabled).toBe(true);
    expect(missingGrowth.disabled).toBe(true);
    expect(missingStar.textContent).toContain("未返回");
    expect(screen.queryByText("88.00")).toBeNull();
    expect(screen.queryByText("未开盘")).toBeNull();
    fireEvent.click(missingStar);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("收起保留旧行情的质量告知，不把过期基准静音成正常", () => {
    render(<IndexCards indices={[{
      ...indices[0], quality: "stale", quality_reasons: ["index_batch_missing"],
    }]} />);
    fireEvent.click(screen.getByRole("button", { name: /指数.*收起/ }));
    expect(screen.getByText("3,300.50")).toBeTruthy();
    expect(screen.getByTitle("本轮未返回该指数，展示旧行情").textContent).toBe("过期");
  });

  it("未传 onSelect 时卡片渲染但不报错（静默降级为纯展示）", () => {
    render(<IndexCards indices={indices} />);
    expect(screen.getByText("3,300.50")).toBeTruthy(); // fmt 带千分位
  });

  it("指数漏返回时保留旧价格，并明确显示过期及缺失原因", () => {
    render(<IndexCards indices={[{
      ...indices[0], quality: "stale", quality_reasons: ["index_batch_missing"],
    }]} />);
    expect(screen.getByText("3,300.50")).toBeTruthy();
    expect(screen.getByTitle("本轮未返回该指数，展示旧行情").textContent).toBe("过期");
    expect(screen.queryByText("正常")).toBeNull();
  });

  it("选中态：selected 匹配的指数卡获得高亮（aria/title 无关，验证样式类切换）", () => {
    const { container } = render(<IndexCards indices={indices} selected="sz399001" onSelect={vi.fn()} />);
    const active = container.querySelector("button.bg-zinc-200\\/80");
    expect(active).not.toBeNull();
    expect(active?.getAttribute("title")).toContain("深证成指");
  });

  it("质量徽标：正常显示「正常」、low/medium 瞬态不出、持久问题常显（2026-09-14 口径统一）", () => {
    const mk = (quality: string, reasons: string[]) =>
      ({ ...baseAudit, symbol: "000001", name: "上证指数", market: "SH", price: 3300.5, change_pct: 0, quality, quality_reasons: reasons }) as Quote;
    // baseline 的默认档就是 high ⇒ 正常必须可见（隐藏它会让「正常」与「字段缺失」不可辨）
    const high = render(<IndexCards indices={[mk("high", [])]} />);
    expect(high.container.textContent).toContain("正常");
    high.unmount();
    // 盘中瞬时 low（如 change_pct_mismatch 下一拍即恢复）不显示徽标——显示就闪
    const low = render(<IndexCards indices={[mk("low", ["change_pct_mismatch"])]} />);
    expect(low.container.textContent).not.toContain("可疑");
    low.unmount();
    // 持久 stale（过期）必须显示，让用户知道数据陈旧
    const stale = render(<IndexCards indices={[mk("stale", ["refresh_failed"])]} />);
    expect(stale.container.textContent).toContain("过期");
  });
});
