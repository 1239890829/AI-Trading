import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function withAttribution(base: ConceptDetail, tag = "完整归因"): ConceptDetail {
  return { ...base, tag_groups: [{ tag, symbols: base.members.map(row => row.symbol) }],
    members: base.members.map(row => ({ ...row, tags: [tag] })) };
}

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

  it("inactive exit shell stops visibility reads, removes member operations, and ignores late content", async () => {
    let finish!: (value: ConceptDetail) => void;
    vi.mocked(getConceptDetail).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}))
      .mockResolvedValueOnce(detail("A", "重新打开的成分"));
    const view = render(<ConceptDetailModal code="A" name="概念" onClose={() => {}} />);
    view.rerender(<ConceptDetailModal code="A" name="概念" active={false} onClose={() => {}} />);
    fireEvent(document, new Event("visibilitychange"));
    expect(getConceptDetail).toHaveBeenCalledTimes(1);
    expect(screen.queryByPlaceholderText("搜代码/名称")).toBeNull();
    await act(async () => finish(detail("A", "已关闭的晚到成分")));
    expect(screen.queryByText("已关闭的晚到成分")).toBeNull();
    view.rerender(<ConceptDetailModal code="A" name="概念" onClose={() => {}} />);
    await screen.findByText("重新打开的成分", {selector:"span"});
    expect(getConceptDetail).toHaveBeenCalledTimes(2);
  });
});

describe("概念基础成分与按需完整归因", () => {
  it("首次只读基础；慢完整归因不挡成分，回可见只更新基础", async () => {
    const base = detail("885517.TI", "基础成分");
    const full = deferred<ConceptDetail>();
    vi.mocked(getConceptDetail).mockImplementation((_code, options) =>
      options?.enrichment === "full" ? full.promise : Promise.resolve(base));
    render(<ConceptDetailModal code={base.code} name="机器人概念" onClose={() => {}} />);
    await screen.findByText("基础成分", { selector: "span" });
    expect(getConceptDetail).toHaveBeenCalledExactlyOnceWith(base.code);
    fireEvent.click(screen.getByRole("button", { name: "读取涨停归因" }));
    await waitFor(() => expect(getConceptDetail).toHaveBeenCalledTimes(2));
    expect(screen.getByText("基础成分", { selector: "span" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "读取归因中…" })).toHaveProperty("disabled", true);
    await act(async () => full.resolve(withAttribution(base)));
    expect(screen.getByRole("button", { name: "完整归因 1" })).toBeTruthy();
    fireEvent(document, new Event("visibilitychange"));
    await waitFor(() => expect(getConceptDetail).toHaveBeenCalledTimes(3));
    expect(vi.mocked(getConceptDetail).mock.calls.filter(([, options]) => options?.enrichment === "full")).toHaveLength(1);
  });

  it.each([false, true])("已完成的完整归因不能压过刷新后的基础成分，成员身份变化=%s", async identityChanged => {
    const base = detail("885517.TI", "原成分");
    const next = { ...base, members: [{ ...base.members[0], name: identityChanged ? "更新成分" : "原成分", change_pct: 3.25 }] };
    vi.mocked(getConceptDetail).mockResolvedValueOnce(base).mockResolvedValueOnce(withAttribution(base)).mockResolvedValueOnce(next);
    render(<ConceptDetailModal code={base.code} name="机器人概念" onClose={() => {}} />);
    await screen.findByText("原成分", { selector: "span" });
    fireEvent.click(screen.getByRole("button", { name: "读取涨停归因" }));
    await screen.findByRole("button", { name: "完整归因 1" });
    fireEvent(document, new Event("visibilitychange"));
    await screen.findByText("+3.25%");
    expect(screen.getByText(identityChanged ? "更新成分" : "原成分", { selector: "span" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "完整归因 1" })).toBeNull();
    if (identityChanged) expect(screen.queryByText("原成分", { selector: "span" })).toBeNull();
    expect(getConceptDetail).toHaveBeenCalledTimes(3);
  });

  it.each(["success", "failure"])("新基础先到时，旧完整归因晚回 %s 也不覆盖它", async outcome => {
    const base = detail("885517.TI", "原成分");
    const next = { ...base, members: [{ ...base.members[0], name: "更新成分" }] };
    const full = deferred<ConceptDetail>();
    vi.mocked(getConceptDetail).mockResolvedValueOnce(base).mockReturnValueOnce(full.promise).mockResolvedValueOnce(next);
    render(<ConceptDetailModal code={base.code} name="机器人概念" onClose={() => {}} />);
    await screen.findByText("原成分", { selector: "span" });
    fireEvent.click(screen.getByRole("button", { name: "读取涨停归因" }));
    await waitFor(() => expect(getConceptDetail).toHaveBeenCalledTimes(2));
    fireEvent(document, new Event("visibilitychange"));
    await screen.findByText("更新成分", { selector: "span" });
    await act(async () => {
      if (outcome === "success") full.resolve(withAttribution(base, "旧完整归因"));
      else full.reject(new Error("old full unavailable"));
    });
    expect(screen.getByText("更新成分", { selector: "span" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "旧完整归因 1" })).toBeNull();
    expect(screen.queryByText("原成分", { selector: "span" })).toBeNull();
    expect(getConceptDetail).toHaveBeenCalledTimes(3);
  });

  it("完整成员身份冲突可重读正式成分，且不会自动重复完整读取", async () => {
    const base = detail("885517.TI", "原成分");
    const next = { ...base, members: [{ ...base.members[0], name: "已变更成分" }] };
    vi.mocked(getConceptDetail).mockResolvedValueOnce(base).mockResolvedValueOnce(withAttribution(next)).mockResolvedValueOnce(next);
    render(<ConceptDetailModal code={base.code} name="机器人概念" onClose={() => {}} />);
    await screen.findByText("原成分", { selector: "span" });
    fireEvent.click(screen.getByRole("button", { name: "读取涨停归因" }));
    await screen.findByRole("alert");
    expect(screen.getByText("原成分", { selector: "span" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "完整归因 1" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重读正式成分" }));
    await screen.findByText("已变更成分", { selector: "span" });
    expect(getConceptDetail).toHaveBeenCalledTimes(3);
    expect(vi.mocked(getConceptDetail).mock.calls.filter(([, options]) => options?.enrichment === "full")).toHaveLength(1);
    vi.mocked(getConceptDetail).mockResolvedValueOnce(withAttribution(next, "新完整归因"));
    fireEvent.click(screen.getByRole("button", { name: "读取涨停归因" }));
    await screen.findByRole("button", { name: "新完整归因 1" });
    expect(getConceptDetail).toHaveBeenLastCalledWith(base.code, { enrichment: "full" });
  });

  it("概念A→B→A清除完整读取scope，旧完整晚回与返回A都不自动重读完整归因", async () => {
    const base = detail("885517.TI", "原概念成分");
    const next = detail("886069.TI", "新概念成分");
    const full = deferred<ConceptDetail>();
    vi.mocked(getConceptDetail).mockResolvedValueOnce(base).mockReturnValueOnce(full.promise).mockResolvedValueOnce(next).mockResolvedValueOnce(base);
    const view = render(<ConceptDetailModal code={base.code} name="机器人概念" onClose={() => {}} />);
    await screen.findByText("原概念成分", { selector: "span" });
    fireEvent.click(screen.getByRole("button", { name: "读取涨停归因" }));
    await waitFor(() => expect(getConceptDetail).toHaveBeenCalledTimes(2));
    view.rerender(<ConceptDetailModal code={next.code} name="人形机器人" onClose={() => {}} />);
    await screen.findByText("新概念成分", { selector: "span" });
    await act(async () => full.resolve(withAttribution(base, "旧概念归因")));
    expect(screen.queryByRole("button", { name: "旧概念归因 1" })).toBeNull();
    expect(screen.queryByText("原概念成分", { selector: "span" })).toBeNull();
    expect(screen.getByRole("button", { name: "读取涨停归因" })).toHaveProperty("disabled", false);
    expect(getConceptDetail).toHaveBeenCalledTimes(3);
    view.rerender(<ConceptDetailModal code={base.code} name="机器人概念" onClose={() => {}} />);
    await screen.findByText("原概念成分", { selector: "span" });
    expect(screen.queryByRole("button", { name: "旧概念归因 1" })).toBeNull();
    expect(screen.getByRole("button", { name: "读取涨停归因" })).toHaveProperty("disabled", false);
    expect(getConceptDetail).toHaveBeenCalledTimes(4);
    expect(vi.mocked(getConceptDetail).mock.calls.filter(([, options]) => options?.enrichment === "full")).toHaveLength(1);
  });
});
