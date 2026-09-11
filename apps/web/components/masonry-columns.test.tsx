import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MasonryColumns } from "@/components/masonry-columns";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（同 theme-card.test.tsx）
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function cards(n: number) {
  return Array.from({ length: n }, (_, i) => (
    <div key={i} data-testid={`card-${i}`}>
      卡片 {i}
    </div>
  ));
}

describe("卡片墙 · 结构与顺序（原生 CSS 多列）", () => {
  it("每张卡都有归宿且只渲染一次，DOM 顺序 = 传入顺序", () => {
    const { container } = render(<MasonryColumns>{cards(7)}</MasonryColumns>);

    const rendered = Array.from(container.querySelectorAll("[data-idx]"));
    expect(rendered.length).toBe(7);
    expect(screen.queryAllByTestId(/^card-/).length).toBe(7);
    // 列优先排布由浏览器完成，DOM 顺序必须保持源顺序（不再被 JS 重排打乱）
    expect(rendered.map((el) => el.getAttribute("data-idx"))).toEqual([
      "0", "1", "2", "3", "4", "5", "6",
    ]);
  });

  it("卡片不允许被列切断，且带行间距", () => {
    const { container } = render(<MasonryColumns gap="gap-2">{cards(3)}</MasonryColumns>);

    for (const el of Array.from(container.querySelectorAll<HTMLElement>("[data-idx]"))) {
      expect(el.style.breakInside).toBe("avoid");
      expect(el.style.marginBottom).toBe("8px"); // gap-2 → 8px
    }
    const root = container.querySelector<HTMLElement>('[data-testid="masonry"]')!;
    expect(root.style.columnGap).toBe("8px");
  });

  it("列数按断点配置（<768 单列 / <1280 双列 / ≥1280 三列）", () => {
    const { container } = render(<MasonryColumns>{cards(3)}</MasonryColumns>);
    const root = container.querySelector('[data-testid="masonry"]')!;
    for (const cls of ["columns-1", "md:columns-2", "xl:columns-3"]) {
      expect(root.className).toContain(cls);
    }
  });

  it("单卡 / 非数组 children 不报错", () => {
    expect(() => render(<MasonryColumns>{cards(1)}</MasonryColumns>)).not.toThrow();
    expect(screen.getByTestId("card-0")).toBeTruthy();

    expect(() =>
      render(
        <MasonryColumns>
          <div data-testid="only">唯一</div>
        </MasonryColumns>,
      ),
    ).not.toThrow();
    expect(screen.getByTestId("only")).toBeTruthy();
  });

  it("不做任何 JS 测量（结构性防回归：一旦重新引入测量就会重新引入闪烁）", () => {
    // 历史：v1~v5 均为「JS 测量 + 写回布局」的实现，连续踩出
    // 无限更新（Maximum update depth）/ 布局错位（有空洞却把卡排到下方）/ 反复换列闪烁。
    // 本用例把「不测量」锁成硬约束：谁能重新引入测量，谁就先让这条测试变红。
    const spy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect");
    render(<MasonryColumns>{cards(6)}</MasonryColumns>);
    expect(spy).not.toHaveBeenCalled();
  });
});
