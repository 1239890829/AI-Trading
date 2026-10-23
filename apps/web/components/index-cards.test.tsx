import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
  it("同一零轴只用已加载的有限涨跌幅，未知不绘条也不冒充零涨幅", () => {
    render(<IndexCards indices={[
      {...indices[0],change_pct:2}, idx("sh000688","科创50","SH",1000), {...indices[2],change_pct:-1},
    ]}/>);
    const up = screen.getByTitle(/上证指数 · 来源/).querySelector('[aria-hidden="true"]') as HTMLElement;
    const down = screen.getByTitle(/创业板指 · 来源/).querySelector('[aria-hidden="true"]') as HTMLElement;
    expect(up.style.getPropertyValue("--index-range-width")).toBe("50%");
    expect(up.style.getPropertyValue("--index-range-start")).toBe("50%");
    expect(down.style.getPropertyValue("--index-range-width")).toBe("25%");
    expect(down.style.getPropertyValue("--index-range-start")).toBe("25%");
    expect(screen.getByLabelText("指数涨跌幅共同零轴").textContent).toContain("2.00%");
    cleanup();
    render(<IndexCards indices={[{...indices[0],change_pct:Number.NaN}]}/>);
    const missing = screen.getByTitle(/上证指数 · 来源/);
    expect(within(missing).getByText("--")).toBeTruthy();
    expect(missing.querySelector('[aria-hidden="true"] b')).toBeNull();
    expect(missing.textContent).not.toContain("NaN");
  });

  it("悬停与聚焦只联动已有来源和双时钟，明确点击才切换证券", () => {
    const select = vi.fn();
    const dated = indices.map((quote,i) => ({...quote, source:i===2?"tencent":"ths", data_timestamp:`2026-10-23T10:00:0${i}+08:00`,received_at:`2026-10-23T10:00:1${i}+08:00`}));
    render(<IndexCards indices={dated} onSelect={select}/>);
    const evidence = () => within(screen.getByLabelText("当前指数来源与时点"));
    fireEvent.mouseEnter(screen.getByTitle(/创业板指 · 来源/));
    expect(evidence().getByText("10:00:02", {exact:false})).toBeTruthy();
    expect(evidence().getByText("10:00:12", {exact:false})).toBeTruthy();
    expect(select).not.toHaveBeenCalled();
    fireEvent.focus(screen.getByTitle(/上证指数 · 来源/));
    expect(evidence().getByText("10:00:00", {exact:false})).toBeTruthy();
    expect(select).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTitle(/上证指数 · 来源/));
    expect(select).toHaveBeenCalledExactlyOnceWith("sh000001");
  });
  it("点击指数卡 → 回调收到带市场前缀的详情 symbol（与自选股点击同一交互）", () => {
    const onSelect = vi.fn();
    render(<IndexCards indices={indices} onSelect={onSelect} />);
    fireEvent.click(screen.getByTitle(/上证指数/));
    expect(onSelect).toHaveBeenCalledWith("sh000001");
    fireEvent.click(screen.getByRole("button", { name: /指数.*展开/ }));
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
    expect(screen.getByRole("button", { name: /指数.*展开/ }).getAttribute("aria-expanded")).toBe("false");
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
    expect(screen.getByText("3,300.50")).toBeTruthy();
    expect(screen.getByTitle(/本轮未返回该指数，展示旧行情/).textContent).toBe("本轮缺项");
  });

  it("未传 onSelect 时卡片渲染但不报错（静默降级为纯展示）", () => {
    render(<IndexCards indices={indices} />);
    expect(screen.getByText("3,300.50")).toBeTruthy(); // fmt 带千分位
  });

  it("展开与退出保留常用卡身份；收起的其它指数立刻退出操作范围，快速反向不被旧计时清掉", async () => {
    vi.useFakeTimers();
    try {
      const {container} = render(<IndexCards indices={indices} />);
      const primary = screen.getByTitle(/上证指数 · 来源/);
      fireEvent.click(screen.getByRole("button", {name:/指数.*展开/}));
      expect(screen.getByTitle(/上证指数 · 来源/)).toBe(primary);
      expect(screen.getByRole("button", {name:/深证成指/})).toBeTruthy();
      fireEvent.click(screen.getByRole("button", {name:/指数.*收起/}));
      expect(screen.queryByRole("button", {name:/深证成指/})).toBeNull();
      expect(container.querySelector(".index-extras")?.hasAttribute("inert")).toBe(true);
      fireEvent.click(screen.getByRole("button", {name:/指数.*展开/}));
      await act(async () => vi.advanceTimersByTime(200));
      expect(screen.getByRole("button", {name:/深证成指/})).toBeTruthy();
      expect(screen.getByTitle(/上证指数 · 来源/)).toBe(primary);
      fireEvent.click(screen.getByRole("button", {name:/指数.*收起/}));
      await act(async () => vi.advanceTimersByTime(160));
      expect(container.querySelector(".index-extras")).toBeNull();
    } finally {vi.useRealTimers();}
  });

  it("指数漏返回时保留旧价格，并明确显示过期及缺失原因", () => {
    render(<IndexCards indices={[{
      ...indices[0], quality: "stale", quality_reasons: ["index_batch_missing"],
    }]} />);
    expect(screen.getByText("3,300.50")).toBeTruthy();
    expect(screen.getByTitle(/本轮未返回该指数，展示旧行情/).textContent).toBe("本轮缺项");
    expect(screen.queryByText("正常")).toBeNull();
  });

  it("选中态：selected 匹配的指数卡获得高亮（aria/title 无关，验证样式类切换）", () => {
    const { container } = render(<IndexCards indices={indices} selected="sz399001" onSelect={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /指数.*展开/ }));
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
    expect(stale.container.textContent).toContain("刷新异常");
  });
});
