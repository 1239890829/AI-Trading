import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StockEventsRow } from "./stock-events";
import { DetailModalProvider, useDetailModal } from "./detail-modal";
import type { EventSummary } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getEventsForSymbol: vi.fn(), getNewsContent: vi.fn() };
});

const mockedGet = vi.mocked((await import("@/lib/api")).getEventsForSymbol);
const mockedContent = vi.mocked((await import("@/lib/api")).getNewsContent);

const event: EventSummary = {
  id: 7,
  title: "沃什鹰派发言致美股与黄金大跌",
  url: "https://example.com/news/7",
  source: "财联社",
  source_tier: 4,
  published_at: "2026-08-31 09:00:00",
  fact_kind: "fact",
  certainty: "done",
  category: "statement",
  half_life_hours: 48,
  source_symbol: null,
  is_active: true,
  interpretation_ref: {
    event_id: 7, version_id: 12, observation_id: 9,
    available_at: "2026-08-31 09:02:00", state: "active",
  },
  directions: [
    { target_type: "theme", target: "黄金概念", direction: -1, strength: 2, chain: "", basis: "" },
  ],
};

// 2026-09-09：改用全站通用详情弹窗（Provider 单例），测试必须包 Provider，
// 否则 useDetailModal 取到的是默认 noop context，点击不会有任何反应。
function renderWithProvider(ui: React.ReactElement) {
  return render(<DetailModalProvider>{ui}</DetailModalProvider>);
}

function FeedEntry() {
  const { open } = useDetailModal();
  return <button onClick={() => open({ kind: "feed", title: "关联消息", symbol: "600519" })}>查看关联消息</button>;
}

describe("StockEventsRow", () => {
  it("渲染相关事件标题与方向，点击打开通用弹窗（正文 + 原文链接）", async () => {
    mockedGet.mockResolvedValue({ symbol: "600519", themes: [], count: 1, items: [event] });
    mockedContent.mockResolvedValue({
      kind: "news",
      title: event.title,
      source_label: "东方财富",
      published: null,
      paragraphs: ["正文第一段。"],
      blocks: [{ type: "p", text: "正文第一段。" }],
      truncated: false,
      url: event.url!, // 该用例的 event 必带 url，非空断言成立
    });

    renderWithProvider(<StockEventsRow symbol="600519" />);
    await screen.findByText(/沃什鹰派/);

    expect(screen.getByText("利空")).toBeTruthy();

    // 弹窗化交互：条目是按钮，点击打开通用详情弹窗（Portal 到 body）
    fireEvent.click(screen.getByRole("button", { name: /沃什鹰派/ }));
    const modal = await screen.findByRole("dialog");
    expect(modal).toBeTruthy();
    expect(modal.textContent).toContain("#12 · 生效 · 2026-08-31 09:02:00");
    await screen.findByText("正文第一段。");
    const link = screen.getByRole("link", { name: "打开原文" });
    expect(link?.getAttribute("href")).toBe("https://example.com/news/7");
    expect(link?.getAttribute("data-action")).toBe("secondary");
  });

  it("无相关事件时显示显式兜底（不再是零占用的静默消失）", async () => {
    mockedGet.mockResolvedValue({ symbol: "600519", themes: [], count: 0, items: [] });
    renderWithProvider(<StockEventsRow symbol="600519" />);
    await screen.findByText(/暂无与该股题材匹配的活跃事件/);
  });

  it("模型方向在个股关联事件列表中显式标为待验证假设", async () => {
    mockedGet.mockResolvedValue({ symbol: "600519", themes: [], count: 1,
      items: [{ ...event, judge_status_label: "待验证假设", directions: [
        { ...event.directions[0], matched_by: "llm_aux" },
      ] }] });
    renderWithProvider(<StockEventsRow symbol="600519" />);
    await screen.findByText(/沃什鹰派/);
    expect(screen.getByText("利空假设")).toBeTruthy();
    expect(screen.queryByText("利空")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /沃什鹰派/ }));
    expect((await screen.findByRole("dialog")).textContent).toContain("尚未经人工验证");
  });

  it("同一事件已判定也不把方向为0的关联行当作明确方向", async () => {
    mockedGet.mockResolvedValue({ symbol: "600519", themes: [], count: 1,
      items: [{ ...event, judge_status: "judged", directions: [{ ...event.directions[0], direction: 0 }] }] });
    renderWithProvider(<StockEventsRow symbol="600519" />);
    await screen.findByText("方向未明");
    expect(screen.getByText("已判定")).toBeTruthy();
    expect(screen.queryByText("待判")).toBeNull();
  });

  it("无方向行的事件仍展示过期状态与说明", async () => {
    mockedGet.mockResolvedValue({ symbol: "600519", themes: [], count: 1,
      items: [{ ...event, url: null, directions: [], judge_status: "expired", judge_reason: "已超过有效时限" }] });
    renderWithProvider(<StockEventsRow symbol="600519" />);
    await screen.findByText("已过期");
    fireEvent.click(screen.getByRole("button", { name: /沃什鹰派/ }));
    expect((await screen.findByRole("dialog")).textContent).toContain("已超过有效时限");
  });

  it("加载失败显示失败态与重试（不再是静默消失）", async () => {
    mockedGet.mockRejectedValue(new Error("boom"));
    renderWithProvider(<StockEventsRow symbol="600519" />);
    await screen.findByText("加载失败");
    expect(screen.getByText("重试")).toBeTruthy();
  });

  it("无 url 的事件也可点开（弹窗内给原文缺失兜底）", async () => {
    mockedGet.mockResolvedValue({
      symbol: "600519",
      themes: [],
      count: 1,
      items: [{ ...event, url: null }],
    });
    renderWithProvider(<StockEventsRow symbol="600519" />);
    await screen.findByText(/沃什鹰派/);
    fireEvent.click(screen.getByRole("button", { name: /沃什鹰派/ }));
    const modal = await screen.findByRole("dialog");
    expect(modal.textContent).toContain("原文链接缺失");
  });

  it("旧事件缺解释版本时明确显示未知", async () => {
    mockedGet.mockResolvedValue({ symbol: "600519", themes: [], count: 1,
      items: [{ ...event, interpretation_ref: null }] });
    renderWithProvider(<StockEventsRow symbol="600519" />);
    fireEvent.click(await screen.findByRole("button", { name: /沃什鹰派/ }));
    expect((await screen.findByRole("dialog")).textContent).toContain("历史解释版本未知");
  });

  it("指数（isIndex）：整行不渲染，且**不发请求**（后端对指数代码必拒 400）", async () => {
    mockedGet.mockReset();
    const { container } = renderWithProvider(<StockEventsRow symbol="sh000001" isIndex />);
    expect(container.textContent).toBe("");
    expect(mockedGet).not.toHaveBeenCalled();
    // 行内三态（加载中/失败/兜底）均不得出现
    expect(screen.queryByText("加载失败")).toBeNull();
    expect(screen.queryByText(/相关事件/)).toBeNull();
  });

  it("消息详情内的事件也保留模型假设与实际状态，展开后可读完整边界", async () => {
    mockedGet.mockResolvedValue({ symbol: "600519", themes: [], count: 1,
      items: [{ ...event, url: null, judge_status: "expired", judge_reason: "超过有效时限", directions: [
        { ...event.directions[0], matched_by: "llm_aux" },
      ] }] });
    renderWithProvider(<FeedEntry />);
    fireEvent.click(screen.getByText("查看关联消息"));
    await screen.findByText("利空假设");
    expect(screen.getByText("已过期")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /沃什鹰派/ }));
    expect((await screen.findByRole("dialog")).textContent).toContain("尚未经人工验证");
    expect(screen.getByRole("dialog").textContent).toContain("超过有效时限");
  });
});
