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

// 2026-09-09：真实调用方都会传 date（EventStore.published_at 统一格式），
// 详情时间与列表同源（此前优先 content.published 源站时间导致两边对不上）。
const item = { title: "乳业概念涨2.47%", url: "https://finance.eastmoney.com/a/202609043865461886.html", date: "2026-09-04 16:46:00" };

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
    await screen.findByText("09-04 16:46"); // 时间与列表同字段同格式（eventTimeText）
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

// ---------------------------------------------------------------- 降级契约守卫

// 实体词典缓存的**降级契约**（2026-09-14 审查批次 B5）。
//
// ## 被守的是什么
// 词典（正文个股/题材链接化）是**增强层**：拉取失败必须降级为纯文本、绝不阻塞
// 正文渲染。但"降级"不等于"永久放弃"——修复前的实现把失败写成
// `entityDictCache = null`，而命中判定是 `entityDictCache !== undefined`。
// `null !== undefined` 成立 ⇒ **一次瞬时失败即被当成"已判定"**，词典在本次
// 会话内永不重试：后端恢复后，已打开的页面永远不再链接化。
//
// 这与后端 heatmap 行业映射的"一次失败=永久失败"是**同一类缺陷**
// （对照 `backend/tests/test_degradation_contracts.py` 不变量 1）。
// 它的表现是「页面照样开、正文照样渲染」，因此单看渲染结果发现不了。
//
// ## 注入验证（回退即红）
// ① catch 分支改回 `entityDictCache = null` ⇒ 第 1 条在"冷却过期后重试"一步失败
//   （仍为 null 且 fetch 调用数停在 1）；② 删掉 `if (!r.ok) throw` ⇒ 第 2 条失败
//   （503 被当成"空词典"永久缓存）。
describe("实体词典加载：瞬时失败不得变成永久失败", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const DICT = { stocks: [{ name: "贵州茅台", code: "600519" }], themes: ["白酒"] };
  const okJson = (payload: unknown) => ({ ok: true, status: 200, json: async () => payload });

  /** 缓存在模块作用域，用例之间必须各拿一块全新模块状态（否则互相污染）。 */
  async function loadFresh() {
    vi.resetModules();
    return (await import("./news-modal")).loadEntityDict;
  }

  it("失败只开冷却窗：冷却期内不重打，过期后自动重试并拿到词典", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        if (calls === 1) throw new Error("ECONNRESET");
        return okJson({ data: DICT });
      }),
    );
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
    const load = await loadFresh();

    // ① 首次失败 → 降级为 null（调用方按纯文本渲染，不阻塞正文）
    expect(await load()).toBeNull();
    expect(calls).toBe(1);

    // ② 冷却期内不重复打网络（否则每次开弹窗都白打一次失败请求）
    now.mockReturnValue(1_000_000 + 30_000);
    expect(await load()).toBeNull();
    expect(calls).toBe(1);

    // ③ 冷却过期 → **必须重试**。旧实现此处仍返回 null 且 calls 停在 1。
    now.mockReturnValue(1_000_000 + 61_000);
    expect(await load()).toEqual(DICT);
    expect(calls).toBe(2);
  });

  it("非 2xx 响应不得被当成「空词典」永久缓存", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        if (calls === 1) {
          // 503 且带合法 JSON：旧实现取不到 `j.data` ⇒ 缓存 null，
          // 语义上等价于"词典为空"，与"服务不可用"完全不是一回事。
          return { ok: false, status: 503, json: async () => ({ detail: "unavailable" }) };
        }
        return okJson({ data: DICT });
      }),
    );
    const now = vi.spyOn(Date, "now").mockReturnValue(5_000_000);
    const load = await loadFresh();

    expect(await load()).toBeNull();
    now.mockReturnValue(5_000_000 + 61_000);
    expect(await load()).toEqual(DICT);
  });

  it("成功结果永久复用（全站共用一份，不再打网络）", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        return okJson({ data: DICT });
      }),
    );
    const load = await loadFresh();

    expect(await load()).toEqual(DICT);
    expect(await load()).toEqual(DICT);
    expect(calls).toBe(1);
  });
});
