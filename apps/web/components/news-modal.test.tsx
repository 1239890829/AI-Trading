import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NewsModal } from "./news-modal";
import type { ArticleContent } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getNewsContent: vi.fn() };
});

const mockedContent = vi.mocked((await import("@/lib/api")).getNewsContent);

const item = { title: "乳业概念涨2.47%", url: "https://finance.eastmoney.com/a/202609043865461886.html" };

function contentFixture(overrides: Partial<ArticleContent> = {}): ArticleContent {
  return {
    kind: "news",
    title: "乳业概念涨2.47%，主力资金净流入16股",
    source_label: "证券时报网",
    published: "2026年09月04日 16:46",
    paragraphs: ["截至收盘，乳业概念上涨2.47%。"],
    blocks: [{ type: "p", text: "截至收盘，乳业概念上涨2.47%。" }],
    truncated: false,
    url: item.url,
    ...overrides,
  };
}

describe("NewsModal 正文块渲染（2026-09-04 排版升级）", () => {
  it("table 块渲染为真表格：表头 + 数据行按原文顺序", async () => {
    mockedContent.mockResolvedValue(
      contentFixture({
        blocks: [
          { type: "p", text: "资金流入榜如下：" },
          {
            type: "table",
            header: true,
            rows: [
              ["代码", "简称", "主力资金流量（万元）"],
              ["002385", "大北农", "113018.02"],
              ["603893", "瑞芯微", "92458.28"],
            ],
          },
        ],
      }),
    );
    render(<NewsModal item={item} onClose={() => {}} />);

    const body = await screen.findByTestId("news-modal-body");
    const table = body.querySelector("table");
    expect(table).toBeTruthy();
    expect(table!.querySelectorAll("thead th").length).toBe(3);
    expect(table!.textContent).toContain("大北农");
    expect(table!.textContent).toContain("113018.02");
    // 段落与表格同屏共存，顺序正确
    expect(body.textContent!.indexOf("资金流入榜")).toBeLessThan(body.textContent!.indexOf("大北农"));
  });

  it("数字列右对齐（font-mono），文本列左对齐", async () => {
    mockedContent.mockResolvedValue(
      contentFixture({
        blocks: [
          { type: "table", header: true, rows: [["简称", "涨跌幅"], ["大北农", "5.81"], ["瑞芯微", "10.00"]] },
        ],
      }),
    );
    render(<NewsModal item={item} onClose={() => {}} />);
    const body = await screen.findByTestId("news-modal-body");
    await waitFor(() => expect(body.querySelector("table")).toBeTruthy());
    const rows = body.querySelectorAll("tbody tr");
    expect((rows[0]!.children[1] as HTMLElement).className).toContain("font-mono");
    expect((rows[0]!.children[0] as HTMLElement).className).not.toContain("font-mono");
  });

  it("img 块渲染配图，加载失败降级为占位说明", async () => {
    mockedContent.mockResolvedValue(
      contentFixture({ blocks: [{ type: "img", src: "https://img.eastmoney.com/news/a.jpg" }] }),
    );
    render(<NewsModal item={item} onClose={() => {}} />);
    await screen.findByText("2026年09月04日 16:46"); // 正文已加载
    // alt="" 的 img 是 presentational（无 img role），用 DOM 查询
    const img = (await waitFor(() => {
      const el = document.body.querySelector("figure img");
      expect(el).toBeTruthy();
      return el as HTMLImageElement;
    })) as HTMLImageElement;
    expect(img.getAttribute("referrerpolicy")).toBe("no-referrer");
    fireEvent.error(img);
    expect(await screen.findByText(/\[配图未能加载/)).toBeTruthy();
  });

  it("blocks 为空（旧缓存响应）回退 paragraphs 渲染，不留白", async () => {
    mockedContent.mockResolvedValue(
      contentFixture({ blocks: [], paragraphs: ["旧版纯文本段落。"] }),
    );
    render(<NewsModal item={item} onClose={() => {}} />);
    expect(await screen.findByText("旧版纯文本段落。")).toBeTruthy();
  });
});
