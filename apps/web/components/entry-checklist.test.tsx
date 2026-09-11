import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { EntryChecklist } from "@/components/entry-checklist";
import type { EntryChecklist as EntryChecklistData } from "@/lib/api";

// vi.mock 工厂会被提升到文件顶部，引用的变量必须经 vi.hoisted 提前初始化，
// 否则命中 TDZ（Cannot access 'xxx' before initialization）
const { getEntryChecklist } = vi.hoisted(() => ({ getEntryChecklist: vi.fn() }));
vi.mock("@/lib/api", () => ({ getEntryChecklist }));

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（同 theme-card.test.tsx）
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function data(over: Partial<EntryChecklistData> = {}): EntryChecklistData {
  return {
    symbol: "600540",
    name: "新赛股份",
    found: true,
    trade_date: "2026-09-10",
    theme: "商业航天",
    theme_stage_basis: "涨停家数+高度",
    health_note: "梯队完整",
    risks: [],
    role: "龙头",
    boards: 4,
    dragon_grade: "A",
    market_phase: "发酵",
    market_layer: { phase: "发酵", stage: null, blocked: false, note: "市场层未拦阻" },
    theme_layer: { phase: null, stage: "启动", blocked: false, note: "题材层未拦阻" },
    conditions: ["竞价高开 3% 以内", "回踩 5 日线不破"],
    avoid: ["一字板开盘"],
    invalidation: ["题材跌停家数 ≥ 3"],
    timing: "09:25 竞价 → 09:35 首个 5 分钟",
    missing: [],
    note: "上述为条件清单，信号未同时满足则不构成介入理由。",
    ...over,
  };
}

describe("介入条件清单 · 三层信号与三态（P1-13）", () => {
  it("按 symbol 拉取，渲染市场/题材层徽标与三段清单 + 时间窗口", async () => {
    getEntryChecklist.mockResolvedValue(data());
    render(<EntryChecklist symbol="600540" />);

    const box = await screen.findByTestId("entry-checklist");
    expect(getEntryChecklist).toHaveBeenCalledWith("600540");

    // 三层环境徽标
    expect(box.textContent).toContain("市场 发酵");
    expect(box.textContent).toContain("题材 启动");
    // 角色/高度/龙头评级附属徽标（前缀是维度「龙头评级」，取值来自后端）
    expect(box.textContent).toContain("龙头 · 4板");
    expect(box.textContent).toContain("龙头评级 A");

    // 三段清单
    expect(box.textContent).toContain("需同时满足的信号");
    expect(box.textContent).toContain("竞价高开 3% 以内");
    expect(box.textContent).toContain("回避项");
    expect(box.textContent).toContain("一字板开盘");
    expect(box.textContent).toContain("失效条件（出现即推翻判断）");
    expect(box.textContent).toContain("题材跌停家数 ≥ 3");

    // 时间窗口与免责
    expect(box.textContent).toContain("09:25 竞价 → 09:35 首个 5 分钟");
    expect(box.textContent).toContain("不构成介入理由");
  });

  it("评级徽标只写维度前缀：取值为「龙头相」时不重复（回归：曾渲染成「龙头相 龙头相」）", async () => {
    // 后端 DRAGON_GRADES 的取值本身就是「龙头相 / 强势候选 / 观察 / 杂毛·回避」，
    // 前缀若直接照抄首个取值就会与取值重复——前缀只能写维度「龙头评级」。
    getEntryChecklist.mockResolvedValue(data({ dragon_grade: "龙头相" }));
    render(<EntryChecklist symbol="600540" />);

    const box = await screen.findByTestId("entry-checklist");
    expect(box.textContent).toContain("龙头评级 龙头相");
    const hits = (box.textContent ?? "").match(/龙头相/g) ?? [];
    expect(hits).toHaveLength(1);
  });

  it("层缺值显式呈现「未判定」，不臆造中性值", async () => {
    getEntryChecklist.mockResolvedValue(
      data({
        market_layer: { phase: null, stage: null, blocked: false, note: "情绪未判定" },
        theme_layer: { phase: null, stage: null, blocked: false, note: "" },
      }),
    );
    render(<EntryChecklist symbol="600540" />);

    const box = await screen.findByTestId("entry-checklist");
    expect(box.textContent).toContain("市场 未判定");
    expect(box.textContent).toContain("题材 未判定");
  });

  it("层被拦阻时徽标标注「拦阻」，并追加上层说明", async () => {
    getEntryChecklist.mockResolvedValue(
      data({
        market_layer: { phase: "退潮", stage: null, blocked: true, note: "市场退潮，空仓闸门已触发" },
        theme_layer: { phase: null, stage: "退潮", blocked: true, note: "题材退潮不给买入范围" },
      }),
    );
    render(<EntryChecklist symbol="600540" />);

    const box = await screen.findByTestId("entry-checklist");
    expect(box.textContent).toContain("市场 退潮 · 拦阻");
    expect(box.textContent).toContain("题材 退潮 · 拦阻");
    expect(box.textContent).toContain("市场退潮，空仓闸门已触发");
    expect(box.textContent).toContain("题材退潮不给买入范围");
  });

  it("found=false 时明示个股层未判定（不在题材梯队），且不渲染角色徽标", async () => {
    getEntryChecklist.mockResolvedValue(
      data({ found: false, role: null, boards: null, dragon_grade: null }),
    );
    render(<EntryChecklist symbol="600540" />);

    const box = await screen.findByTestId("entry-checklist");
    expect(box.textContent).toContain("该标的当日不在题材梯队");
    expect(box.textContent).toContain("未判定");
    expect(box.textContent).not.toContain("龙头 · 4板");
  });

  it("missing 非空时显式列出未取到的输入（三态：不做中性假设）", async () => {
    getEntryChecklist.mockResolvedValue(data({ missing: ["封单额", "换手率"] }));
    render(<EntryChecklist symbol="600540" />);

    const box = await screen.findByTestId("entry-checklist");
    expect(box.textContent).toContain("未取到：封单额、换手率");
    expect(box.textContent).toContain("不做中性假设");
  });

  it("missing 为空时不渲染「未取到」行（无缺失就不占位）", async () => {
    getEntryChecklist.mockResolvedValue(data());
    render(<EntryChecklist symbol="600540" />);

    const box = await screen.findByTestId("entry-checklist");
    expect(box.textContent).not.toContain("未取到");
  });

  it("首次请求未返回时显示加载态", () => {
    getEntryChecklist.mockReturnValue(new Promise(() => {}));
    render(<EntryChecklist symbol="600540" />);
    expect(screen.getByText(/介入条件加载中/)).toBeTruthy();
  });

  it("请求失败时如实报错，不静默留白", async () => {
    getEntryChecklist.mockRejectedValue(new Error("network down"));
    render(<EntryChecklist symbol="600540" />);
    expect(await screen.findByText(/介入条件不可用：network down/)).toBeTruthy();
  });

  it("红线 3：输出为条件陈述，不含确定性买卖措辞", async () => {
    getEntryChecklist.mockResolvedValue(data());
    const { container } = render(<EntryChecklist symbol="600540" />);
    await screen.findByTestId("entry-checklist");

    const text = container.textContent ?? "";
    for (const banned of ["建议买入", "推荐买入", "可以买", "必涨", "稳赚", "满仓", "保证收益"]) {
      expect(text).not.toContain(banned);
    }
  });
});
