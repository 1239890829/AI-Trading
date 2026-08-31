import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { EventPanel } from "./event-panel";
import { getEvents, getEventStocks, type EventSummary } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getEvents: vi.fn(),
    getEventStocks: vi.fn(),
  };
});

const mockedGetEvents = vi.mocked(getEvents);
const mockedGetEventStocks = vi.mocked(getEventStocks);

const fixture: EventSummary[] = [
  {
    id: 3,
    title: "长鑫 LPDDR6 全球首发量产",
    url: null,
    source: "东财",
    source_tier: 3,
    published_at: "2026-08-31 09:00:00",
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
  },
];

describe("EventPanel", () => {
  it("渲染事件标题、分类/确定性/来源分级徽章与方向 chip（含题材聚焦链接）", async () => {
    mockedGetEvents.mockResolvedValue(fixture);

    render(<EventPanel />);
    await screen.findByText("长鑫 LPDDR6 全球首发量产");

    expect(screen.getByText("公司")).toBeTruthy();
    expect(screen.getByText("已落地 · 事实")).toBeTruthy();
    expect(screen.getByText("来源 3/5")).toBeTruthy();

    // 方向 chip：存储芯片 利好 → /tape?tab=themes&focus=存储芯片（L9 联动）
    const chip = screen.getByText("存储芯片").closest("a");
    expect(chip?.getAttribute("href")).toBe("/tape?tab=themes&focus=%E5%AD%98%E5%82%A8%E8%8A%AF%E7%89%87");
    expect(screen.getByText("利好")).toBeTruthy();
  });

  it("展开标的池后拉取成分并生成详情链接", async () => {
    mockedGetEvents.mockResolvedValue(fixture);
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

    render(<EventPanel />);
    await screen.findByText("长鑫 LPDDR6 全球首发量产");

    fireEvent.click(screen.getByText("标的池 ↗"));
    await waitFor(() => expect(screen.getByText(/600171/)).toBeTruthy());
    const link = screen.getByText(/600171/).closest("a");
    expect(link?.getAttribute("href")).toBe("/workbench?symbol=600171");
    expect(screen.getByText(/不构成买卖建议/)).toBeTruthy();
  });

  it("无活跃事件时空态提示；加载失败显示错误", async () => {
    mockedGetEvents.mockResolvedValueOnce([]);
    const { unmount } = render(<EventPanel />);
    await screen.findByText(/暂无活跃事件/);
    unmount();

    mockedGetEvents.mockRejectedValueOnce(new Error("后端失联"));
    render(<EventPanel />);
    await screen.findByText(/后端失联/);
  });
});
