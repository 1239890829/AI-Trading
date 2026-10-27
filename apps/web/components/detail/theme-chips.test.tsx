import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import { ThemeChipsRow } from "./theme-chips";
import type { StockThemes } from "@/lib/api";
import { InspectionContext } from "@/components/inspection/inspection-context";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(() => {cleanup(); vi.useRealTimers();});

const fixture: StockThemes = {
  symbol: "000019",
  official: [{ theme_code: "885995.TI", theme_name: "粮食概念", source: "ths_official", theme_chg_1d: 1.23 }],
  attribution: [{ theme_name: "粮食概念", date: "2026-08-28" }],
};

describe("ThemeChipsRow", () => {
  it("同名官方概念与未核身份的归因、公司标签分开，重复公司标签不重复", () => {
    render(<ThemeChipsRow themes={fixture} companyNames={["粮食概念", "粮食概念"]} />);

    // A matching name alone cannot transfer source identity or membership.
    const chips = screen.getAllByText("粮食概念");
    expect(chips.length).toBe(3);
    expect(chips[0].closest("a")?.getAttribute("href")).toBe("/hunting?view=discover&browse=catalog&disc_kind=concept&disc_code=885995.TI");
    expect(chips[0].closest("a")?.getAttribute("title")).toContain("官方成分 · 同花顺 885995.TI");
    for (const chip of chips.slice(1)) {
      expect(chip.closest("a")?.getAttribute("href")).toBe("/tape?tab=themes&focus=%E7%B2%AE%E9%A3%9F%E6%A6%82%E5%BF%B5");
      expect(chip.closest("a")?.getAttribute("title")).toContain("正式概念身份未核");
    }
    expect(screen.getByText("官方")).toBeTruthy();
    expect(screen.getByText("归因")).toBeTruthy();
    expect(screen.getByText("资料")).toBeTruthy();
  });

  it("官方 chip 内嵌板块当日涨跌幅徽标", () => {
    render(<ThemeChipsRow themes={fixture} />);
    expect(screen.getByText("+1.23%")).toBeTruthy();
  });

  it("正式 chip 保留代码导航，未核标签只按名字检索，不拦成同名旁览", () => {
    const open = vi.fn();
    render(<InspectionContext.Provider value={{open, close:vi.fn()}}><ThemeChipsRow themes={fixture} /></InspectionContext.Provider>);
    const links = screen.getAllByRole("link");
    fireEvent.click(links[0]);
    expect(links[0].getAttribute("href")).toContain("disc_code=885995.TI");
    expect(open).not.toHaveBeenCalled();
    fireEvent.click(links[1]);
    expect(open).toHaveBeenCalledWith(expect.objectContaining({kind:"themes",focus:"粮食概念"}));
  });

  it("同名不同正式代码的概念各自保留原身份", () => {
    render(<ThemeChipsRow themes={{...fixture, attribution:[], official:[fixture.official[0], {...fixture.official[0],theme_code:"886069.TI"}]}} />);
    expect(screen.getAllByRole("link").map(link=>link.getAttribute("href"))).toEqual([
      "/hunting?view=discover&browse=catalog&disc_kind=concept&disc_code=885995.TI",
      "/hunting?view=discover&browse=catalog&disc_kind=concept&disc_code=886069.TI",
    ]);
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

  it("expanded extra chips retain a collapse action and leave the keyboard/link scope immediately on close", () => {
    vi.useFakeTimers();
    const {container} = render(<ThemeChipsRow themes={{...fixture, attribution:[], official:Array.from({length:8}, (_, n) => ({theme_code:`88${n}`, theme_name:`题材${n}`, source:"ths_official"}))}} />);
    fireEvent.click(screen.getByRole("button", {name:"＋2 个"}));
    expect(screen.getByRole("link", {name:"题材7"})).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name:"收起"}));
    expect(screen.queryByRole("link", {name:"题材7"})).toBeNull();
    expect(container.querySelector(".theme-chips-extras")?.hasAttribute("inert")).toBe(true);
    act(() => vi.advanceTimersByTime(160));
    expect(container.querySelector(".theme-chips-extras")).toBeNull();
  });
});
