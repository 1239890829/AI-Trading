import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getImpactEvents, type ImpactEvent } from "@/lib/api";
import { EventFeed } from "./event-feed";

vi.mock("@/lib/api", () => ({ getImpactEvents: vi.fn() }));
vi.mock("@/components/news-modal", () => ({ NewsModal: () => null }));
const detail = vi.hoisted(() => ({open: vi.fn()}));
vi.mock("@/components/detail/detail-modal", () => ({ useDetailModal: () => detail }));
vi.mock("@/components/detail/symbol-detail-context", async () => ({
  ...await vi.importActual<typeof import("@/components/detail/symbol-detail-context")>("@/components/detail/symbol-detail-context"),
  useSymbolDetail: () => ({ open: vi.fn() }),
}));
vi.mock("@/components/inspection/inspection-context", async () => ({
  ...await vi.importActual<typeof import("@/components/inspection/inspection-context")>("@/components/inspection/inspection-context"),
  useInspection: () => ({ open: vi.fn() }),
}));

type Result = Awaited<ReturnType<typeof getImpactEvents>>;
function result(title: string): Result {
  const event: ImpactEvent = {
    id: 1, title, url: null, summary: null, source: "eastmoney", source_tier: 3,
    published_at: "2026-10-12 10:20:00", fact_kind: "fact", certainty: "done",
    category: "other", half_life_hours: 48, source_symbol: null, is_active: true,
    directions: [], four_category: "policy", four_label: "国家政策", impact_level: "L1", tags: [],
  };
  return { count: 1, items: [event], countsAll: { L1: 1, L2: 0, L3: 0 }, fourCounts: {}, tagCounts: {} };
}
function deferred() {
  let resolve!: (value: Result) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Result>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function flush(ms = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

beforeEach(() => {
  detail.open.mockReset();
  vi.useFakeTimers();
  vi.mocked(getImpactEvents).mockReset().mockResolvedValue(result("默认事件"));
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("资讯事件 · 请求身份与无感刷新", () => {
  it("row metadata opens the same article once; direction links do not open it or count as notification reads", async () => {
    const payload = result("独立的财经事件");
    payload.items[0].directions = [{target_type: "theme", target: "存储芯片", direction: 1, strength: 1, basis: "公开消息", chain: ""}];
    vi.mocked(getImpactEvents).mockResolvedValue(payload);
    const ancestor = vi.fn();
    render(<div onClick={ancestor}><EventFeed active /></div>);
    await flush();
    fireEvent.click(screen.getByText("国家政策"));
    expect(detail.open).toHaveBeenCalledTimes(1);
    expect(detail.open.mock.calls[0][0]).toMatchObject({eventId: 1, title: "独立的财经事件"});
    fireEvent.click(screen.getByText("存储芯片"));
    expect(detail.open).toHaveBeenCalledTimes(1);
    expect(ancestor).not.toHaveBeenCalled();
    expect(getImpactEvents).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("event-feed-row").getAttribute("role")).toBeNull();
  });

  it("旧排序晚回不能覆盖新排序，切换期间不拿旧排序充数", async () => {
    const old = deferred();
    vi.mocked(getImpactEvents).mockReturnValueOnce(old.promise).mockResolvedValueOnce(result("最新排序"));
    render(<EventFeed active />);
    fireEvent.click(screen.getByRole("button", { name: "最新" }));
    await flush();
    expect(getImpactEvents).toHaveBeenLastCalledWith(false, 100, "time");
    expect(screen.getByText("最新排序")).toBeTruthy();
    await act(async () => old.resolve(result("旧相关排序")));
    expect(screen.getByText("最新排序")).toBeTruthy();
    expect(screen.queryByText("旧相关排序")).toBeNull();
  });

  it("已成功旧排序也不在新排序尚未完成时展示", async () => {
    const next = deferred();
    vi.mocked(getImpactEvents).mockResolvedValueOnce(result("原相关内容")).mockReturnValueOnce(next.promise);
    render(<EventFeed active />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "最新" }));
    expect(screen.queryByText("原相关内容")).toBeNull();
    await act(async () => next.resolve(result("新时间内容")));
    expect(screen.getByText("新时间内容")).toBeTruthy();
  });

  it("手动刷新时保留同排序内容；失败明确为上次结果，自动恢复清错误", async () => {
    const next = deferred();
    vi.mocked(getImpactEvents).mockResolvedValueOnce(result("原内容"))
      .mockReturnValueOnce(next.promise).mockResolvedValue(result("恢复内容"));
    const view = render(<EventFeed active />);
    await flush();
    view.rerender(<EventFeed active refreshToken={1} />);
    await flush();
    expect(screen.getByText("原内容")).toBeTruthy();
    await act(async () => next.reject(new Error("source offline")));
    expect(screen.getByText("原内容")).toBeTruthy();
    expect(screen.getByText(/下方为上次读取结果，当前资讯状态未确认/)).toBeTruthy();
    await flush(60_000);
    expect(screen.getByText("恢复内容")).toBeTruthy();
    expect(screen.queryByText(/当前资讯状态未确认/)).toBeNull();
  });

  it.each(["success", "failure"])("手动刷新后旧轮询 %s 晚回不污染新结果", async late => {
    const old = deferred();
    vi.mocked(getImpactEvents).mockResolvedValueOnce(result("初次内容"))
      .mockReturnValueOnce(old.promise).mockResolvedValueOnce(result("刷新结果"));
    const view = render(<EventFeed active />);
    await flush();
    await flush(60_000);
    view.rerender(<EventFeed active refreshToken={1} />);
    await flush();
    await act(async () => late === "success" ? old.resolve(result("迟到轮询")) : old.reject(new Error("old poll error")));
    expect(screen.getByText("刷新结果")).toBeTruthy();
    expect(screen.queryByText("迟到轮询")).toBeNull();
    expect(screen.queryByText(/old poll error/)).toBeNull();
  });

  it("恢复同时接到刷新 token 时只校准一次", async () => {
    const view = render(<EventFeed active={false} />);
    view.rerender(<EventFeed active refreshToken={1} />);
    await flush();
    expect(getImpactEvents).toHaveBeenCalledTimes(1);
    expect(screen.getByText("默认事件")).toBeTruthy();
  });

  it("关闭期间丢弃在途旧回包；恢复立即校准，关闭与卸载后停轮询", async () => {
    const old = deferred();
    vi.mocked(getImpactEvents).mockReturnValueOnce(old.promise).mockResolvedValueOnce(result("恢复后内容"));
    const view = render(<EventFeed active />);
    view.rerender(<EventFeed active={false} />);
    await act(async () => old.resolve(result("隐藏期间旧内容")));
    expect(screen.queryByText("隐藏期间旧内容")).toBeNull();
    await flush(120_000);
    expect(getImpactEvents).toHaveBeenCalledTimes(1);
    view.rerender(<EventFeed active />);
    await flush();
    expect(getImpactEvents).toHaveBeenCalledTimes(2);
    expect(screen.getByText("恢复后内容")).toBeTruthy();
    view.unmount();
    await flush(120_000);
    expect(getImpactEvents).toHaveBeenCalledTimes(2);
  });

  it("旧排序失败不能给新排序挂错误，首次真实失败能自动重试", async () => {
    const old = deferred();
    vi.mocked(getImpactEvents).mockReturnValueOnce(old.promise).mockRejectedValueOnce(new Error("current offline"))
      .mockResolvedValue(result("重试成功"));
    render(<EventFeed active />);
    fireEvent.click(screen.getByRole("button", { name: "最新" }));
    await flush();
    expect(screen.getByText(/current offline/)).toBeTruthy();
    await act(async () => old.reject(new Error("stale error")));
    expect(screen.queryByText(/stale error/)).toBeNull();
    await flush(60_000);
    expect(screen.getByText("重试成功")).toBeTruthy();
    expect(screen.queryByText(/current offline/)).toBeNull();
  });
});
