import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QuoteStrip } from "./quote-strip";
import type { Quote } from "@/types/market";

function quote(limitUp: number | null): Quote {
  return {
    symbol: "sh000001",
    market: "SH",
    name: "上证指数",
    price: 3952.13,
    prev_close: 3949.91,
    change: 2.22,
    change_pct: 0.06,
    limit_up_price: limitUp,
    source: "tencent",
    quality: "high",
    quality_reasons: [],
    received_at: "2026-09-22T08:14:01Z",
    data_timestamp: "2026-09-22T08:14:01Z",
  };
}

afterEach(() => {cleanup(); vi.unstubAllGlobals(); vi.useRealTimers();});

describe("QuoteStrip 限价展示", () => {
  it("午间时段保留在固定槽，拒旧和备用读取失败另行告知并可打开完整诊断", () => {
    const paused = {...quote(4344.9), quality: "stale" as const, quality_reasons: ["source_time_regress_ignored"],
      data_timestamp: "2026-10-13T11:30:00+08:00", display_session: {phase: "closed" as const, trade_date: "2026-10-13", as_of: "2026-10-13T12:20:00+08:00", trade_day_result: true}};
    const view = render(<QuoteStrip quote={paused} inWatchlist={false} onAdd={() => {}} connectionStatus="error" issue={{label: "保留可信值", title: "旧源钟被拒绝"}} diagnostics={<p>原始拒旧与备用请求失败记录</p>}/>);
    const slot = view.container.querySelector("[data-quote-status-slot]");
    expect(slot?.textContent).toContain("午间休息");
    expect(screen.getByRole("img", {name: "行情提示：保留可信值；备用读取失败"})).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "查看行情问题"}));
    expect(screen.getByRole("dialog", {name: "行情诊断"}).textContent).toContain("原始拒旧与备用请求失败记录");
  });
  it("真实质量与恢复共用固定槽，异常入口复用诊断抽屉，正常不留诊断按钮", () => {
    const raw = {...quote(4344.9), quality: "stale" as const, quality_reasons: ["source_time_regress_ignored"]};
    const before = JSON.stringify(raw);
    const view = render(<QuoteStrip quote={raw} inWatchlist={false} onAdd={() => {}} connectionStatus="live" issue={{label: "保留可信值", title: "旧快照已忽略，保留较新报价"}} diagnostics={<p>原源钟与拒旧依据</p>}/>);
    const slot = view.container.querySelector("[data-quote-status-slot]");
    expect(slot?.className).toContain("w-[6.4em]");
    expect(screen.getByRole("button", {name: "查看行情问题"}).className).not.toContain("bg-");
    expect(screen.queryByText("已连接")).toBeNull();
    expect(screen.getByRole("button", {name: "查看行情问题"}).title).toContain("WebSocket");
    expect(screen.getByRole("button", {name: "查看行情问题"}).getAttribute("data-quality")).toBe("stale");
    fireEvent.click(screen.getByRole("button", {name: "查看行情问题"}));
    expect(screen.getByRole("dialog", {name: "行情诊断"}).textContent).toContain("原源钟与拒旧依据");
    view.rerender(<QuoteStrip quote={{...raw, quality: "high", quality_reasons: []}} inWatchlist={false} onAdd={() => {}} connectionStatus="live"/>);
    expect(view.container.querySelector("[data-quote-status-slot]")).toBe(slot);
    expect(screen.queryByRole("button", {name: "查看行情问题"})).toBeNull();
    expect(screen.getByText("正常")).toBeTruthy();
    expect(JSON.stringify(raw)).toBe(before);
  });

  it.each([-1, 0, null])("无效涨停价 %s 显示 --，不把占位值冒充真实价格", (limitUp) => {
    render(<QuoteStrip quote={quote(limitUp)} inWatchlist={false} onAdd={() => {}} hideWatchlist />);
    expect(screen.getByText("涨停").parentElement?.textContent).toContain("--");
    expect(screen.queryByText("-1.00")).toBeNull();
  });

  it("有效涨停价正常显示", () => {
    render(<QuoteStrip quote={quote(4344.9)} inWatchlist={false} onAdd={() => {}} hideWatchlist />);
    expect(screen.getByText("涨停").parentElement?.textContent).toContain("4,344.90");
  });

  it("手机指标收起立即不可聚焦，退出壳保留，快速重开继续显示最新行情", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("matchMedia", (query: string) => ({matches: query.includes("max-width"), addEventListener: () => {}, removeEventListener: () => {}}));
    const view = render(<QuoteStrip quote={quote(4344.9)} inWatchlist={false} onAdd={() => {}} hideWatchlist />);
    expect(screen.queryByRole("region", {name:"行情详细指标"})).toBeNull();
    const toggle = screen.getByRole("button", {name:"行情指标"});
    fireEvent.click(toggle);
    const region = screen.getByRole("region", {name:"行情详细指标"});
    fireEvent.click(toggle);
    expect(screen.queryByRole("region", {name:"行情详细指标"})).toBeNull();
    expect(region.closest("[inert]")).toBeTruthy();
    view.rerender(<QuoteStrip quote={{...quote(4344.9), high:4000}} inWatchlist={false} onAdd={() => {}} hideWatchlist />);
    fireEvent.click(toggle);
    await act(async () => vi.advanceTimersByTime(200));
    expect(screen.getByRole("region", {name:"行情详细指标"})).toHaveProperty("textContent", expect.stringContaining("4,000.00"));
  });
});


describe("QuoteStrip field origin", () => {
  it("shows supplementary PE source and source time while preserving the primary quote attribution", () => {
    const row = {...quote(4344.9), source: "ths", pe_ttm: 18,
      field_sources: {pe_ttm: {source: "tencent", data_timestamp: "2026-09-22T08:14:00Z", received_at: "2026-09-22T08:14:01Z"}}};
    const view = render(<QuoteStrip quote={row} inWatchlist={false} onAdd={() => {}}/>);
    const pe = screen.getByText("PE").parentElement!;
    expect(pe.title).toContain("腾讯");
    expect(pe.title).toContain("2026-09-22 16:14:00");
    expect(view.container.querySelector(".quote-source")?.textContent).toContain("同花顺");
    expect(screen.getByText("涨停").parentElement?.title).toContain("同花顺");
  });
  it("native field without a source clock says unknown instead of using reception time", () => {
    render(<QuoteStrip quote={{...quote(4344.9), pe_ttm: 18, data_timestamp: null}} inWatchlist={false} onAdd={() => {}}/>);
    expect(screen.getByText("PE").parentElement?.title).toContain("截至 未知");
    expect(screen.getByText("PE").parentElement?.title).not.toContain("16:14");
  });
});
