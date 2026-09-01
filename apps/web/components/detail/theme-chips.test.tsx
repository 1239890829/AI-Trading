import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import { ThemeChipsRow } from "./theme-chips";
import type { StockThemes } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

const fixture: StockThemes = {
  symbol: "000019",
  official: [{ theme_code: "885995.TI", theme_name: "粮食概念", source: "ths_official", theme_chg_1d: 1.23 }],
  attribution: [{ theme_name: "粮食概念", date: "2026-08-28" }],
};

describe("ThemeChipsRow", () => {
  it("双源并列渲染，跳转链接与涨停归因徽标齐全", () => {
    render(<ThemeChipsRow themes={fixture} />);

    // 同一题材双源各渲染一个 chip（官方成分 + 涨停归因），互不覆盖
    const chips = screen.getAllByText("粮食概念");
    expect(chips.length).toBe(2);
    expect(chips[0].closest("a")?.getAttribute("href")).toBe("/tape?tab=themes&focus=%E7%B2%AE%E9%A3%9F%E6%A6%82%E5%BF%B5");
    expect(screen.getByText("涨停归因")).toBeTruthy();
  });

  it("官方 chip 内嵌板块当日涨跌幅徽标", () => {
    render(<ThemeChipsRow themes={fixture} />);
    expect(screen.getByText("+1.23%")).toBeTruthy();
  });

  it("点击 chip 携带 focus 参数跳题材看板", () => {
    render(<ThemeChipsRow themes={fixture} />);
    const link = screen.getAllByRole("link")[0] as HTMLAnchorElement;
    fireEvent.click(link);
    expect(link.getAttribute("href")).toContain("focus=");
  });

  it("多题材按涨跌幅降序排前，超出部分折叠", () => {
    const many: StockThemes = {
      symbol: "600105",
      official: [
        { theme_code: "1", theme_name: "弱题材", source: "ths_official", theme_chg_1d: -1.5 },
        { theme_code: "2", theme_name: "强题材", source: "ths_official", theme_chg_1d: 5.6 },
        { theme_code: "3", theme_name: "中题材", source: "ths_official", theme_chg_1d: 0.8 },
        { theme_code: "4", theme_name: "缺值题材", source: "ths_official", theme_chg_1d: null },
        ...Array.from({ length: 6 }, (_, i) => ({
          theme_code: `x${i}`,
          theme_name: `其他题材${i}`,
          source: "ths_official",
          theme_chg_1d: 0.1 * i,
        })),
      ],
      attribution: [],
    };
    render(<ThemeChipsRow themes={many} />);

    // 默认只展示 6 条，且强题材排第一、缺值的排在全部有值之后（被挤出 Top6）
    expect(screen.getByText("强题材")).toBeTruthy();
    expect(screen.queryByText("弱题材")).toBeNull();
    expect(screen.queryByText("缺值题材")).toBeNull();
    // 折叠按钮给出剩余数量
    expect(screen.getByText(/＋\d+ 个/)).toBeTruthy();
    // 展开后全部可见
    fireEvent.click(screen.getByText(/＋\d+ 个/));
    expect(screen.getByText("缺值题材")).toBeTruthy();
    expect(screen.getByText("弱题材")).toBeTruthy();
    expect(screen.getByText("-1.50%")).toBeTruthy();
  });

  it("无归属时零占用（渲染 null）", () => {
    const { container } = render(
      <ThemeChipsRow themes={{ symbol: "000019", official: [], attribution: [] }} />
    );
    expect(container.textContent).toBe("");
    expect(screen.queryByText("题材归属")).toBeNull();
  });

  it("themes 为 null（加载中/失败）同样零占用", () => {
    const { container } = render(<ThemeChipsRow themes={null} />);
    expect(container.textContent).toBe("");
  });
});
