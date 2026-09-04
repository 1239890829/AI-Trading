import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { EventPanel, StockPools } from "./event-panel";
import { getEventStocks, getImpactEvents, type ImpactEvent } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getImpactEvents: vi.fn(),
    getEventStocks: vi.fn(),
  };
});

const mockedGetImpactEvents = vi.mocked(getImpactEvents);
const mockedGetEventStocks = vi.mocked(getEventStocks);

function evt(over: Partial<ImpactEvent>): ImpactEvent {
  return {
    id: 3,
    title: "长鑫 LPDDR6 全球首发量产",
    url: null,
    source: "东财",
    source_tier: 3,
    published_at: "2026-09-04 09:00:00",
    fact_kind: "fact",
    certainty: "done",
    category: "corporate",
    half_life_hours: 72,
    source_symbol: null,
    is_active: true,
    directions: [
      {
        target_type: "theme",
        target: "存储芯片",
        direction: 1,
        strength: 2,
        chain: "「首发」直接利好该题材",
        basis: "命中利好词「首发」→ 利好",
      },
    ],
    four_category: "hot",
    four_label: "市场热点",
    impact_level: "L2",
    tags: ["业绩"],
    rank_score: 61.4,
    rank_reasons: ["题材共振：存储芯片 成分今日等权 +3.1%", "影响力 L2（事实+有标的链）"],
    rank_factors: { theme_best: { name: "存储芯片", chg_pct: 3.1 }, phase: "冰点", phase_weight: 0.7 },
    ...over,
  };
}

describe("EventPanel（盘面相关性 Top4 摘要，2026-09-04 任务④）", () => {
  it("渲染相关性分数、排序依据首条、四级分类与方向 chip（题材聚焦链接）", async () => {
    mockedGetImpactEvents.mockResolvedValue({
      count: 1, countsAll: { L1: 0, L2: 1, L3: 0 }, fourCounts: { hot: 1 }, tagCounts: {},
      items: [evt({})],
    });

    render(<EventPanel />);
    await screen.findByText("长鑫 LPDDR6 全球首发量产");

    // 相关性分数徽章（title 悬浮可追溯全部理由）
    expect(screen.getByText("61分")).toBeTruthy();
    // 排序依据首条可见，全部理由在 title
    expect(screen.getByText(/题材共振：存储芯片/)).toBeTruthy();
    // 四级分类
    expect(screen.getByText("市场热点")).toBeTruthy();
    // 方向 chip：存储芯片 利好 → 题材聚焦链接（L9 联动）
    const chip = screen.getByText("存储芯片").closest("a");
    expect(chip?.getAttribute("href")).toBe("/tape?tab=themes&focus=%E5%AD%98%E5%82%A8%E8%8A%AF%E7%89%87");
    expect(screen.getByText("利好")).toBeTruthy();
    // 完整列表入口指向事件 Tab
    const more = screen.getByText("完整列表 ↗").closest("a");
    expect(more?.getAttribute("href")).toBe("/market?tab=events");
  });

  it("只取相关性 Top4，多余事件不渲染", async () => {
    mockedGetImpactEvents.mockResolvedValue({
      count: 6, countsAll: {}, fourCounts: {}, tagCounts: {},
      items: [1, 2, 3, 4, 5, 6].map((i) => evt({ id: i, title: `事件${i}`, rank_score: 100 - i })),
    });

    render(<EventPanel />);
    await screen.findByText("事件1");
    expect(screen.getByText("事件4")).toBeTruthy();
    expect(screen.queryByText("事件5")).toBeNull();
    expect(screen.queryByText("事件6")).toBeNull();
  });

  it("无活跃事件空态；加载失败显示错误", async () => {
    mockedGetImpactEvents.mockResolvedValueOnce({
      count: 0, countsAll: {}, fourCounts: {}, tagCounts: {}, items: [],
    });
    const { unmount } = render(<EventPanel />);
    await screen.findByText(/暂无活跃事件/);
    unmount();

    mockedGetImpactEvents.mockRejectedValueOnce(new Error("后端失联"));
    render(<EventPanel />);
    await screen.findByText(/后端失联/);
  });

  it("StockPools 展开拉取成分并生成详情链接（事件 Tab 复用）", async () => {
    mockedGetEventStocks.mockResolvedValue([
      {
        target: "存储芯片",
        direction: 1,
        stocks: [
          { symbol: "600171", name: "上海贝岭" },
          { symbol: "000021", name: "深科技" },
        ],
      },
    ]);

    render(<StockPools eventId={3} />);
    await waitFor(() => expect(screen.getByText(/600171/)).toBeTruthy());
    const link = screen.getByText(/600171/).closest("a");
    // 2026-09-03 起跳转带 from 返回参数（workbenchUrlWithBack），断言前缀而非全等
    expect(link?.getAttribute("href")?.startsWith("/workbench?symbol=600171")).toBe(true);
    expect(screen.getByText(/不构成买卖建议/)).toBeTruthy();
  });
});
