import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ReviewTab } from "./review-tab";
import type { ReviewReportDetail, ReviewReportSummary } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 alerts-tab.test.tsx 注释）
afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getReviewReports: vi.fn(),
    getReviewEffectiveness: vi.fn(),
    getReviewReport: vi.fn(),
    updateActionItemStatus: vi.fn(),
  };
});

const mocked = await import("@/lib/api");

const SUMMARY: ReviewReportSummary = {
  review_id: "RV-20260901-214950",
  trade_date: "20260901",
  methodology_version: "v1",
  model_actual: "rules",
  model_degraded: false,
  summary: "委托 0 笔；数据缺失 2 处；P0 改进项 1 条",
  gap_count: 2,
  action_item_count: 1,
  generated_at: "2026-09-01T13:49:50",
};

const EFFECT = {
  by_category: {
    data: { total: 30, confirmed: 0, reverted: 0, rejected: 0, pending: 30, adoption_rate: 0, revert_rate: null },
  },
  suggestions: [],
};

function detailWith(itemId: string, status = "pending"): ReviewReportDetail {
  return {
    review_id: SUMMARY.review_id,
    trade_date: SUMMARY.trade_date,
    generated_at: SUMMARY.generated_at,
    methodology_version: "v1",
    model: { requested: "rules", actual: "rules", degraded: false },
    dimensions: [],
    action_items: [
      {
        id: itemId,
        title: "补齐阻断级数据缺失：breadth, sentiment",
        category: "data",
        priority: "P0",
        expected_impact: "恢复被阻断维度的复盘结论",
        evidence: "breadth(snapshot_service): 全市场快照尚未就绪",
        target: "snapshot_service.breadth_payload",
        proposed_change: "检查 provider 健康状态与快照服务是否就绪",
        status,
        resolution_note: "",
      },
    ],
    meta_insights: [],
    summary: SUMMARY.summary,
  };
}

function setup(itemId = "42", status = "pending") {
  vi.mocked(mocked.getReviewReports).mockResolvedValue([SUMMARY]);
  vi.mocked(mocked.getReviewEffectiveness).mockResolvedValue(EFFECT);
  vi.mocked(mocked.getReviewReport).mockResolvedValue(detailWith(itemId, status));
  vi.mocked(mocked.updateActionItemStatus).mockResolvedValue({
    id: Number(itemId) || 0,
    review_id: SUMMARY.review_id,
    trade_date: SUMMARY.trade_date,
    title: "补齐阻断级数据缺失：breadth, sentiment",
    category: "data",
    priority: "P0",
    target: "snapshot_service.breadth_payload",
    proposed_change: "检查 provider 健康状态与快照服务是否就绪",
    status: "confirmed",
    resolution_note: "",
    resolved_at: "2026-09-01T14:00:00",
  });
}

it("keeps observations, judgments and every gap condition distinct without changing management permissions",async()=>{
  setup();
  const report=detailWith("42");
  // Historical report payloads can contain structured gaps despite the string-only current API type.
  const legacyGap = {field:"source_time",reason:"发布时间未知",impact:"不能回填时点",severity:"P0"} as unknown as string;
  report.dimensions=[{key:"data",title:"数据窗口",status:"blocked",findings:["仅取得 12.36 亿元的单源记录。"],judgements:["尚不能判断可靠性。"],gaps:[legacyGap]}];
  vi.mocked(mocked.getReviewReport).mockResolvedValue(report);
  render(<ReviewTab allowDispose/>);
  expect(await screen.findByText("仅取得 12.36 亿元的单源记录。")).toBeTruthy();
  expect(screen.getByText("尚不能判断可靠性。")).toBeTruthy();
  expect(screen.getByText("还有 1 项资料或验证缺口")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"数据窗口完整检查记录"}));
  expect(screen.getByLabelText("原始判断").textContent).toContain("尚不能判断可靠性。");
  expect(screen.getByLabelText("缺口与限制").textContent).toMatch(/发布时间未知.*不能回填时点.*P0/);
  expect(screen.getByRole("button",{name:"确认"})).toBeTruthy();
  expect(mocked.updateActionItemStatus).not.toHaveBeenCalled();
});

describe("ReviewTab 改进项处置闭环", () => {
  it("可见性重读期间保持草稿但暂停处置，失败后恢复仍保留同身份草稿", async () => {
    setup();
    render(<ReviewTab allowDispose />);
    fireEvent.click(await screen.findByRole("button", { name: "驳回" }));
    const note = screen.getByPlaceholderText("驳回理由（必填）");
    fireEvent.change(note, { target: { value: "仍需核实源时间" } });
    let finishList!: (value: ReviewReportSummary[]) => void;
    let failDetail!: (reason: Error) => void;
    vi.mocked(mocked.getReviewReports).mockImplementationOnce(() => new Promise(resolve => { finishList = resolve; }));
    vi.mocked(mocked.getReviewReport).mockImplementationOnce(() => new Promise((_resolve, reject) => { failDetail = reject; }));
    fireEvent(document, new Event("visibilitychange"));
    await waitFor(() => expect(mocked.getReviewReport).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "提交" })).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("button", { name: "提交" }));
    expect(mocked.updateActionItemStatus).not.toHaveBeenCalled();
    expect(note).toHaveProperty("value", "仍需核实源时间");
    await act(async () => finishList([SUMMARY]));
    expect(screen.getByRole("button", { name: "提交" })).toHaveProperty("disabled", true);
    await act(async () => failDetail(new Error("offline")));
    expect(screen.getByRole("button", { name: "提交" })).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("button", { name: "重试正文" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "提交" })).toHaveProperty("disabled", false));
    expect(screen.getByPlaceholderText("驳回理由（必填）")).toHaveProperty("value", "仍需核实源时间");
  });

  it("旧重读晚回不能解锁仍在校准的新重读", async () => {
    setup();
    let finishMutation!: (value: Awaited<ReturnType<typeof mocked.updateActionItemStatus>>) => void;
    vi.mocked(mocked.updateActionItemStatus).mockImplementationOnce(() => new Promise(resolve => { finishMutation = resolve; }));
    render(<ReviewTab allowDispose />);
    fireEvent.click(await screen.findByRole("button", { name: "确认" }));
    await waitFor(() => expect(mocked.updateActionItemStatus).toHaveBeenCalledTimes(1));
    let finishOld!: (value: ReviewReportDetail) => void;
    let finishNew!: (value: ReviewReportDetail) => void;
    vi.mocked(mocked.getReviewReport)
      .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
      .mockImplementationOnce(() => new Promise(resolve => { finishNew = resolve; }));
    fireEvent(document, new Event("visibilitychange"));
    await waitFor(() => expect(mocked.getReviewReport).toHaveBeenCalledTimes(2));
    await act(async () => finishMutation({
      id: 42, review_id: SUMMARY.review_id, trade_date: SUMMARY.trade_date,
      title: "补齐阻断级数据缺失：breadth, sentiment", category: "data", priority: "P0",
      target: "snapshot_service.breadth_payload", proposed_change: "检查 provider 健康状态与快照服务是否就绪",
      status: "confirmed", resolution_note: "", resolved_at: "2026-09-01T14:00:00",
    }));
    await waitFor(() => expect(mocked.getReviewReport).toHaveBeenCalledTimes(3));
    await act(async () => finishOld(detailWith("42")));
    expect(screen.getByRole("button", { name: "确认" })).toHaveProperty("disabled", true);
    expect(screen.getByRole("status").textContent).toContain("正在核对当前处置状态");
    expect(screen.queryByRole("alert")).toBeNull();
    await act(async () => finishNew(detailWith("42")));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认" })).toHaveProperty("disabled", false));
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("报告重跑复用 id 但行动项身份变更时不继承旧处置草稿", async () => {
    setup();
    render(<ReviewTab allowDispose />);
    fireEvent.click(await screen.findByRole("button", { name: "驳回" }));
    fireEvent.change(screen.getByPlaceholderText("驳回理由（必填）"), { target: { value: "仅适用于原缺口" } });
    const changed = detailWith("42");
    changed.action_items[0] = { ...changed.action_items[0], title: "新报告的风控核验", category: "risk" };
    vi.mocked(mocked.getReviewReport).mockResolvedValue(changed);
    fireEvent(document, new Event("visibilitychange"));
    await screen.findByText("新报告的风控核验");
    expect(screen.queryByPlaceholderText("驳回理由（必填）")).toBeNull();
    expect(mocked.updateActionItemStatus).not.toHaveBeenCalled();
  });

  it("同日期刷新失败保留报告正文与列表，旧处置状态只读", async () => {
    setup();
    render(<ReviewTab allowDispose />);
    await screen.findByText("补齐阻断级数据缺失：breadth, sentiment");
    vi.mocked(mocked.getReviewReports).mockRejectedValue(new Error("offline"));
    vi.mocked(mocked.getReviewReport).mockRejectedValue(new Error("offline"));
    fireEvent(document, new Event("visibilitychange"));
    await waitFor(() => expect(screen.getAllByRole("alert").some(node => node.textContent?.includes("上次"))).toBe(true));
    expect(screen.getByText("补齐阻断级数据缺失：breadth, sentiment")).toBeTruthy();
    expect(screen.getByRole("button", { name: /20260901.*委托/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认" })).toHaveProperty("disabled", true);
    expect(mocked.updateActionItemStatus).not.toHaveBeenCalled();
  });
  it("改进项带处置入口，点击「确认」即提交并刷新", async () => {
    setup();
    render(<ReviewTab allowDispose />);

    const btn = await screen.findByRole("button", { name: "确认" });
    fireEvent.click(btn);

    // 守卫三元组必须随请求携带：报告重跑后 id 会漂移（rowid 复用），
    // 裸 id 处置会静默挂到不相干的改进项上，后端靠三元组拒绝陈旧寻址
    await waitFor(() => {
      expect(mocked.updateActionItemStatus).toHaveBeenCalledWith(
        expect.objectContaining({
          id: "42",
          trade_date: "20260901",
          category: "data",
          title: "补齐阻断级数据缺失：breadth, sentiment",
        }),
        "confirmed",
        "",
      );
    });
    // 处置后要刷新有效性统计，否则右侧采纳率停留在旧值
    await waitFor(() => {
      expect(mocked.getReviewEffectiveness).toHaveBeenCalledTimes(2);
    });
  });

  it("驳回必须填理由——未填时提交按钮不可用", async () => {
    setup();
    render(<ReviewTab allowDispose />);

    fireEvent.click(await screen.findByRole("button", { name: "驳回" }));

    const submit = await screen.findByRole("button", { name: "提交" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByPlaceholderText("驳回理由（必填）"), {
      target: { value: "该缺口是偶发，不值得改" },
    });
    expect((submit as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(submit);
    await waitFor(() => {
      expect(mocked.updateActionItemStatus).toHaveBeenCalledWith(
        expect.objectContaining({
          id: "42",
          trade_date: "20260901",
          category: "data",
          title: "补齐阻断级数据缺失：breadth, sentiment",
        }),
        "rejected",
        "该缺口是偶发，不值得改",
      );
    });
  });

  it("旧版报告的临时编号不可寻址，不暴露处置按钮", async () => {
    setup("AI-3f2a9c11");
    render(<ReviewTab allowDispose />);

    expect(
      await screen.findByText(/来自旧版报告，无数据库主键/),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认" })).toBeNull();
  });

  it("已处置的改进项显示状态与撤销入口", async () => {
    setup("42", "applied");
    render(<ReviewTab allowDispose />);

    expect(await screen.findByText("已登记实施")).toBeTruthy();
    // 已实施就不再重复提供「已实施」按钮，但可撤销
    expect(screen.queryByRole("button", { name: "登记实施" })).toBeNull();
    expect(screen.getByRole("button", { name: "撤销处置" })).toBeTruthy();
  });
});

describe("ReviewTab 只读与请求身份", () => {
  it("date filtering preserves the selected report and does not generate or fetch other reports", async () => {
    setup();
    vi.mocked(mocked.getReviewReports).mockResolvedValue([SUMMARY,{...SUMMARY,review_id:"other",trade_date:"20260831",summary:"另一份报告"}]);
    render(<ReviewTab/>);
    await screen.findByText("补齐阻断级数据缺失：breadth, sentiment");
    fireEvent.change(screen.getByRole("searchbox",{name:"查找日期或原摘要"}),{target:{value:"20260831"}});
    expect(screen.queryByRole("button",{name:/20260901.*委托/})).toBeNull();
    expect(screen.getByText("补齐阻断级数据缺失：breadth, sentiment")).toBeTruthy();
    expect(mocked.getReviewReport).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button",{name:"清除筛选"}));
    expect(screen.getByRole("button",{name:/20260901.*委托/})).toBeTruthy();
  });
  it("a successful response with a different report date is not presented under the requested date",async()=>{
    setup();render(<ReviewTab focusDate="2026-09-02"/>);
    expect(await screen.findByText("返回报告日期与所选日期不符，未展示该返回。")).toBeTruthy();
    expect(screen.queryByText("补齐阻断级数据缺失：breadth, sentiment")).toBeNull();
  });
  it("ordinary research is read-only, adoption is not presented as trading performance", async () => {
    setup(); vi.mocked(mocked.updateActionItemStatus).mockClear();
    render(<ReviewTab />);
    expect(await screen.findByText(/管理处置在系统维护/)).toBeTruthy();
    expect(screen.queryByRole("button", {name: "确认"})).toBeNull();
    expect(screen.queryByText("改进项处置统计（非交易效果）")).toBeNull();
    expect(mocked.getReviewEffectiveness).not.toHaveBeenCalled();
    expect(mocked.updateActionItemStatus).not.toHaveBeenCalled();
  });
  it("late success and failure for A cannot replace B", async () => {
    setup();
    const second = {...SUMMARY, review_id: "RV-B", trade_date: "20260902", summary: "B报告"};
    vi.mocked(mocked.getReviewReports).mockResolvedValue([SUMMARY, second]);
    let rejectA!: (error: Error) => void;
    vi.mocked(mocked.getReviewReport).mockImplementation(date => date === SUMMARY.trade_date ? new Promise((_, reject) => {rejectA = reject;}) : Promise.resolve({...detailWith("43"), trade_date: second.trade_date, review_id: "RV-B", summary: "B正文"}));
    render(<ReviewTab />);
    await waitFor(() => expect(mocked.getReviewReport).toHaveBeenCalledWith(SUMMARY.trade_date));
    fireEvent.click(screen.getByRole("button", {name: /B报告/}));
    expect(await screen.findByText("B正文")).toBeTruthy();
    rejectA(new Error("迟到A错误"));
    await waitFor(() => expect(screen.queryByText(/迟到A错误/)).toBeNull());
    expect(screen.getByText("B正文")).toBeTruthy();
  });
  it("a missing requested date never silently falls back to a different report", async () => {
    setup(); vi.mocked(mocked.getReviewReport).mockRejectedValue(new mocked.ApiError(404, "无报告", "not_found"));
    render(<ReviewTab focusDate="2026-09-28" />);
    expect(await screen.findByText("指定日期尚无报告")).toBeTruthy();
    expect(mocked.getReviewReport).toHaveBeenCalledWith("20260928");
    expect(mocked.getReviewReport).not.toHaveBeenCalledWith(SUMMARY.trade_date);
  });
  it("free-text amounts and dates do not become unverified stock links", async () => {
    setup(); const report = detailWith("42"); report.summary = "金额 600127 日期 202609";
    report.meta_insights = [{dimension: "其他", observation: "金额 600127 日期 202609", effectiveness: "未知", evidence: "原文", suggestion: "待核"}];
    vi.mocked(mocked.getReviewReport).mockResolvedValue(report);
    render(<ReviewTab />);
    await screen.findAllByText("金额 600127 日期 202609");
    expect(document.querySelector('a[href*="symbol=600127"]')).toBeNull();
  });
});


it("implementation registration requires three evidence fields and keeps the note", async () => {
  setup(); render(<ReviewTab allowDispose/>);
  fireEvent.click(await screen.findByRole("button", {name:"登记实施"}));
  fireEvent.change(screen.getByPlaceholderText(/实施与验证证据/), {target:{value:"修复已完成"}});
  expect((screen.getByRole("button", {name:"提交"}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("实施引用（任务 / PR / 提交）"), {target:{value:"PR #12"}});
  fireEvent.change(screen.getByLabelText("已上线版本 / 已生效配置"), {target:{value:"release-abc"}});
  fireEvent.change(screen.getByLabelText("复测记录与结论"), {target:{value:"离线检查通过；实盘效果待观察"}});
  fireEvent.click(screen.getByRole("button", {name:"提交"}));
  await waitFor(() => expect(mocked.updateActionItemStatus).toHaveBeenCalledWith(expect.any(Object),"applied","修复已完成", {
    implementation_ref:"PR #12",release_version:"release-abc",verification_ref:"离线检查通过；实盘效果待观察"
  }));
});
