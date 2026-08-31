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
  official: [{ theme_code: "885995.TI", theme_name: "粮食概念", source: "ths_official" }],
  attribution: [{ theme_name: "粮食概念", date: "2026-08-28" }],
};

describe("ThemeChipsRow", () => {
  it("双源并列渲染，来源徽标与跳转链接齐全", () => {
    render(<ThemeChipsRow themes={fixture} />);

    // 同一题材双源各渲染一个 chip（官方成分 + 涨停归因），互不覆盖
    const chips = screen.getAllByText("粮食概念");
    expect(chips.length).toBe(2);
    expect(chips[0].closest("a")?.getAttribute("href")).toBe("/themes?focus=%E7%B2%AE%E9%A3%9F%E6%A6%82%E5%BF%B5");
    expect(screen.getByText("官方成分")).toBeTruthy();
    expect(screen.getByText("涨停归因")).toBeTruthy();
  });

  it("点击 chip 携带 focus 参数跳题材看板", () => {
    render(<ThemeChipsRow themes={fixture} />);
    const link = screen.getAllByRole("link")[0] as HTMLAnchorElement;
    fireEvent.click(link);
    expect(link.getAttribute("href")).toContain("focus=");
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
