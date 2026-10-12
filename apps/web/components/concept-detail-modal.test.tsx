import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConceptDetailModal } from "./concept-detail-modal";
import { getConceptDetail, type ConceptDetail } from "@/lib/api";

vi.mock("@/lib/api", () => ({ getConceptDetail: vi.fn() }));
vi.mock("@/components/stock-link", () => ({ useStockRowNav: () => () => () => {} }));
vi.mock("@/components/ui/modal-shell", () => ({
  ModalShell: ({ children, header }: { children: React.ReactNode; header: React.ReactNode }) => <section>{header}{children}</section>,
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const detail = (code: string, name: string): ConceptDetail => ({
  code, name, total: 1, limit_up_count: 0, meta_note: "fixture",
  tag_groups: [], members: [{ symbol: code, name, price: 20, change_pct: 1,
    limit_up: false, turnover_rate: null, float_market_cap_yi: null,
    boards: null, break_count: null, seal_amount: null, reason: null, tags: [] }],
});

describe("概念详情读取身份与无感刷新", () => {
  it("切换概念会复位细分与搜索条件", async () => {
    const initial = detail("A", "原成员");
    initial.tag_groups = [{ tag: "芯片", symbols: ["A"] }];
    initial.members[0].tags = ["芯片"];
    vi.mocked(getConceptDetail).mockResolvedValueOnce(initial).mockResolvedValueOnce(detail("B", "新成员"));
    const view = render(<ConceptDetailModal code="A" name="概念 A" onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "芯片 1" }));
    fireEvent.change(screen.getByPlaceholderText("搜代码/名称"), { target: { value: "原成员" } });
    view.rerender(<ConceptDetailModal code="B" name="概念 B" onClose={() => {}} />);
    await screen.findByText("新成员", { selector: "span" });
    expect(screen.getByPlaceholderText("搜代码/名称")).toHaveProperty("value", "");
    expect(screen.getByRole("button", { name: "全部成分 1" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("同概念失败保留筛选；成功结果不再有原标签时回全且不复活旧标签", async () => {
    const initial = detail("A", "可读成员");
    initial.tag_groups = [{ tag: "芯片", symbols: ["A"] }];
    initial.members[0].tags = ["芯片"];
    vi.mocked(getConceptDetail).mockResolvedValueOnce(initial).mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(detail("A", "可读成员")).mockResolvedValueOnce(initial);
    render(<ConceptDetailModal code="A" name="概念" onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "芯片 1" }));
    fireEvent.change(screen.getByPlaceholderText("搜代码/名称"), { target: { value: "可读成员" } });
    fireEvent(document, new Event("visibilitychange"));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "芯片 1" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByPlaceholderText("搜代码/名称")).toHaveProperty("value", "可读成员");
    fireEvent.click(screen.getByRole("button", { name: "重试概念读取" }));
    await screen.findByRole("button", { name: "全部成分 1" });
    await act(async () => {});
    expect(screen.queryByRole("button", { name: "芯片 1" })).toBeNull();
    expect(screen.getByText("可读成员", { selector: "span" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "全部成分 1" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent(document, new Event("visibilitychange"));
    await screen.findByRole("button", { name: "芯片 1" });
    expect(screen.getByRole("button", { name: "全部成分 1" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("切换概念后不接受旧概念晚回", async () => {
    let finish!: (value: ConceptDetail) => void;
    vi.mocked(getConceptDetail).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
      .mockResolvedValueOnce(detail("B", "新概念"));
    const view = render(<ConceptDetailModal code="A" name="旧概念" onClose={() => {}} />);
    view.rerender(<ConceptDetailModal code="B" name="新概念" onClose={() => {}} />);
    await screen.findByText("新概念", { selector: "span" });
    await act(async () => finish(detail("A", "旧概念")));
    expect(screen.queryByText("旧概念")).toBeNull();
  });
  it("同概念恢复失败保留成分并标明刷新失败，重试后恢复", async () => {
    vi.mocked(getConceptDetail).mockResolvedValueOnce(detail("A", "可读成分"))
      .mockRejectedValueOnce(new Error("source unavailable"))
      .mockResolvedValueOnce(detail("A", "恢复成分"));
    render(<ConceptDetailModal code="A" name="概念" onClose={() => {}} />);
    await screen.findByText("可读成分", { selector: "span" });
    fireEvent(document, new Event("visibilitychange"));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("上次"));
    expect(screen.getByText("可读成分", { selector: "span" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试概念读取" }));
    await screen.findByText("恢复成分", { selector: "span" });
    expect(screen.queryByRole("alert")).toBeNull();
  });
  it("首次失败明确反馈，不无限显示加载中", async () => {
    vi.mocked(getConceptDetail).mockRejectedValue(new Error("offline"));
    render(<ConceptDetailModal code="A" name="概念" onClose={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("offline"));
    expect(screen.queryByText("加载中…")).toBeNull();
  });
});
