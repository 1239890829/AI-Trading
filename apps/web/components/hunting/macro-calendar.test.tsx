import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MacroCalendar } from "@/components/hunting/intraday-sections";
import type { MorningBrief } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（同 theme-card.test.tsx）
afterEach(cleanup);

function brief(over: Partial<MorningBrief> = {}): MorningBrief {
  return {
    brief_date: "20260910",
    generated_at: "2026-09-10T08:40:00+08:00",
    trigger: "schedule",
    engine_version: "v1",
    env: {
      phase: "发酵",
      promo_percentile: 73,
      bands_source: "percentile",
      pool_date: "2026-09-09",
      is_trading_day: true,
    },
    missing: [],
    directions: [],
    alerts: [],
    ...over,
  };
}

const EVENTS = [
  {
    region: "中国",
    label: "CPI",
    event: "中国8月CPI年率(%)",
    time: "09:30",
    actual: "0.8",
    forecast: "0.8",
    previous: "0.5",
    importance: 2,
    line: "09:30 中国·CPI（公布 0.8）　中国8月CPI年率(%)",
  },
];

describe("宏观日历（P1-8）", () => {
  it("渲染后端给好的 line 文案 + 只渲染不拼接", () => {
    render(<MacroCalendar brief={brief({ macro_events: EVENTS })} />);
    // 用子串断言：line 内含全角空格，getByText 默认会做空白归一化
    expect(screen.getByText(/09:30 中国·CPI（公布 0\.8）/)).toBeTruthy();
    expect(screen.getByText(/今日宏观日历（1 项/)).toBeTruthy();
  });

  it("渲染非农先验提醒文案", () => {
    render(<MacroCalendar brief={brief({ macro_note: "宏观日历：今晚 20:30 美国9月非农公布。" })} />);
    expect(screen.getByText(/今晚 20:30 美国9月非农公布/)).toBeTruthy();
  });

  it("三态：源不可得（null）整块不渲染，不谎称「今日无事件」", () => {
    const { container } = render(<MacroCalendar brief={brief({ macro_events: null, macro_note: null })} />);
    expect(container.textContent).toBe("");
  });

  it("三态：空数组是「今日确无高信号事件」的真信息，显式说明", () => {
    render(<MacroCalendar brief={brief({ macro_events: [] })} />);
    expect(screen.getByText(/今日无 CPI\/PPI\/GDP/)).toBeTruthy();
  });
});
