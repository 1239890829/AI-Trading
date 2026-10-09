import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * 判读/规则提醒气泡的点击、会话展示与确认语义。
 *
 * ## 被守的是什么
 * 气泡展示的是**一只具体的股票**（判读只对个股告警产生），而原实现点「查看」走
 * `router.push("/agent?tab=alerts")` —— 把用户从当前页面连根拔走，落到一个需要再找
 * 那条记录、再点一次才看到股票的列表页。用户指令：**点开直接看这只股的详情弹窗**。
 *
 * ## 为什么必须有反向对照
 * 把「查看」改成无条件弹窗是**错的**：气泡的兜底分支（判读无 symbol）原本靠跳告警页
 * 仍能看到记录；若无条件弹窗，这类条目会变成一个点了没反应的死按钮。
 * 所以本文件同时钉住反向：**无 symbol ⇒ 仍走告警页**。
 *
 * ## 为什么还要钉「全部 N 条」
 * 气泡每批只主动展示一条。收敛主按钮时若把通往告警页的唯一入口一并去掉，
 * 第 2 条起就再无入口 —— 那是「修好一处、丢一处」。故多条时必须仍有全量入口。
 */

// `vi.mock` 会被提升到文件顶部，引用的 spy 必须用 `vi.hoisted` 造，
// 否则报 "Cannot access 'push' before initialization"（既有测试踩过）。
const { push, openSpy, closeSpy } = vi.hoisted(() => ({
  push: vi.fn(),
  openSpy: vi.fn(),
  closeSpy: vi.fn(),
}));

// setup.ts 已全局桩了 next/navigation（但每次调用返回**新的** vi.fn ⇒ 外部拿不到 spy）。
// 本文件覆写该 mock，换取可断言的 push。
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getAgentBubbles: vi.fn(async () => [] as unknown[]),
    ackAgentTriage: vi.fn(async () => true),
  };
});

import { FloatingAssistant } from "@/components/assistant/floating-assistant";
import { SymbolDetailCtx } from "@/components/detail/symbol-detail-context";
import { ackAgentTriage, getAgentBubbles } from "@/lib/api";

const mockedBubbles = vi.mocked(getAgentBubbles);
const mockedAck = vi.mocked(ackAgentTriage);

/** 一条判读提醒（形状照 `AgentBubble`，只列断言用得到的字段并给默认值）。 */
function bubble(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    event_id: 11,
    verdict: "notify",
    reason: "放量突破前高",
    name: "贵州茅台",
    model: "llm",
    acked: false,
    symbol: "600519",
    trigger_value: null,
    threshold: null,
    created_at: "2026-09-16 10:00:00",
    ...over,
  };
}

beforeEach(() => {
  localStorage.clear();
  push.mockClear();
  openSpy.mockClear();
  closeSpy.mockClear();
  mockedBubbles.mockReset();
  mockedBubbles.mockResolvedValue([] as never);
  mockedAck.mockReset();
  mockedAck.mockResolvedValue(true);
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ data: null }), { status: 200 })));
});

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup(); // vitest 未开 globals ⇒ RTL 自动清理不注册，必须显式
});

/**
 * 包 `SymbolDetailCtx.Provider`：不包则 `useSymbolDetail()` 取到默认 **noop** context
 * ⇒ 点击什么也不会发生，而断言「openSpy 未被调用」在**两种情形下都为真**，
 * 用例会变成永远绿的摆设（这正是 noop context 的隐蔽之处，见 `stock-events.test.tsx`）。
 */
function renderBubble(ui: React.ReactElement) {
  render(
    <SymbolDetailCtx.Provider value={{ open: openSpy, close: closeSpy }}>{ui}</SymbolDetailCtx.Provider>,
  );
}

/** 渲染 → 等气泡出现（挂载即拉取，`findBy*` 已含重试）。 */
async function setup(bubbles: ReturnType<typeof bubble>[]) {
  mockedBubbles.mockResolvedValue(bubbles as never);
  renderBubble(<FloatingAssistant />);
  return await screen.findByTestId("assistant-alert-bubble");
}

/** 用真实资源 hook 的回可见刷新驱动下一次读取，避免等待30秒或重写轮询实现。 */
async function pollAgain() {
  const calls = mockedBubbles.mock.calls.length;
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
  await waitFor(() => expect(mockedBubbles.mock.calls.length).toBeGreaterThan(calls));
}

describe("AI 判读提醒气泡 · 点击语义", () => {
  it.each([
    ["临板 7.2%（距封板 -0.30pct）", "临板 7.2%（旧口径：距封板判定线 -0.30 个百分点）"],
    ["距实际涨停价还需上涨 2.33%", "距实际涨停价还需上涨 2.33%"],
  ])("提醒展示正确距离口径：%s", async (reason, displayed) => {
    await setup([bubble({ reason })]);
    expect(screen.getByTestId("assistant-alert-bubble").textContent).toContain(displayed);
  });

  it("点「查看详情」打开**该股**的详情弹窗，且不得跳转告警页", async () => {
    await setup([bubble()]);

    fireEvent.click(screen.getByTestId("assistant-alert-view"));

    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledWith({ symbol: "600519" });
    // 核心诉求：**不跳页**。只断言"弹窗开了"不足以守住它——旧实现在同一位置
    // 既可能弹窗又可能顺带 push，用户仍会被拔走。
    expect(push).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByTestId("assistant-alert-bubble")).toBeNull());
  });

  it("反向对照：无 symbol 的判读仍走告警页（不得退化成点了没反应的死按钮）", async () => {
    await setup([bubble({ symbol: "", name: "" })]);

    fireEvent.click(screen.getByTestId("assistant-alert-view"));

    expect(push).toHaveBeenCalledWith("/agent?tab=alerts");
    expect(openSpy).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByTestId("assistant-alert-bubble")).toBeNull());
  });

  it("多条：给出「全部 N 条」入口并进告警页（气泡只展示第一条）", async () => {
    await setup([bubble(), bubble({ id: 2, event_id: 12, symbol: "000001", name: "平安银行" })]);

    const all = screen.getByTestId("assistant-alert-all");
    expect(all.textContent).toContain("还有 1 条");

    fireEvent.click(all);
    expect(push).toHaveBeenCalledWith("/agent?tab=alerts");
    await waitFor(() => expect(screen.queryByTestId("assistant-alert-summary")).toBeNull());
  });

  it("单条：不出现「全部 N 条」（避免给单个提醒多余入口）", async () => {
    await setup([bubble()]);

    expect(screen.queryByTestId("assistant-alert-all")).toBeNull();
  });

  it("主按钮与全量入口是两条路：点主按钮不会顺带进告警页", async () => {
    // 前三条已分别覆盖两个按钮；这条钉住"两者互不牵连"——
    // 防将来把 onClick 写成「先弹窗再 push」这种留下双重跳转的实现。
    await setup([bubble(), bubble({ id: 2, event_id: 13 })]);

    fireEvent.click(screen.getByTestId("assistant-alert-view"));
    expect(openSpy).toHaveBeenCalledWith({ symbol: "600519" });
    expect(push).not.toHaveBeenCalled();

    await waitFor(() => expect(screen.queryByTestId("assistant-alert-bubble")).toBeNull());
    expect(mockedAck).toHaveBeenCalledWith(1);

    fireEvent.click(screen.getByTestId("assistant-alert-all"));
    expect(push).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledTimes(1); // 未二次弹窗
    await waitFor(() => expect(mockedAck).toHaveBeenCalledWith(2));
  });

  it("规则降级说明属于本条记录，不能冒充当前全局AI故障", async () => {
    await setup([bubble({ model: "llm_fallback", reason: "本次自主判读预算已用尽，使用规则处理" })]);
    expect(screen.getByText("规则提醒")).toBeTruthy();
    expect(screen.getByText("本条未经过AI判读")).toBeTruthy();
    expect(screen.queryByText("AI 判读提醒")).toBeNull();
    expect(screen.queryByText(/AI不可用/)).toBeNull();
  });

  it("相同id轮询、打开关闭聊天后不重复主动弹出，数量入口仍可查看且未伪造ack", async () => {
    await setup([bubble()]);
    fireEvent.keyDown(screen.getByTestId("assistant-ball"), { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "最小化" }));
    expect(screen.queryByTestId("assistant-alert-bubble")).toBeNull();
    expect(screen.getByTestId("assistant-alert-inbox").textContent).toContain("1 条提醒");
    await pollAgain();
    expect(screen.queryByTestId("assistant-alert-bubble")).toBeNull();
    expect(mockedAck).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("assistant-alert-inbox"));
    expect(screen.getByTestId("assistant-alert-bubble")).toBeTruthy();
  });

  it("同批其余提醒保留轻量入口，真正新id可主动展开一次", async () => {
    await setup([bubble(), bubble({ id: 2, name: "平安银行", symbol: "000001" })]);
    fireEvent.click(screen.getByRole("button", { name: "稍后" }));
    await pollAgain();
    expect(screen.queryByTestId("assistant-alert-bubble")).toBeNull();
    expect(screen.getByTestId("assistant-alert-inbox").textContent).toContain("2 条提醒");
    mockedBubbles.mockResolvedValue([bubble(), bubble({ id: 2 }), bubble({ id: 3, name: "新风险标的", symbol: "600127" })] as never);
    await pollAgain();
    expect(screen.getByTestId("assistant-alert-bubble").textContent).toContain("新风险标的");
    expect(mockedAck).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "稍后" }));
    await pollAgain();
    expect(screen.queryByTestId("assistant-alert-bubble")).toBeNull();
  });

  it("新id到达已有展开提醒时只更新数量，不替换用户正在读的内容", async () => {
    await setup([bubble({ reason: "正在读的提醒" })]);
    mockedBubbles.mockResolvedValue([bubble({ reason: "正在读的提醒" }), bubble({ id: 2, reason: "第二条新提醒" })] as never);
    await pollAgain();
    expect(screen.getByTestId("assistant-alert-bubble").textContent).toContain("正在读的提醒");
    expect(screen.getByTestId("assistant-alert-all").textContent).toContain("还有 1 条");
    expect(screen.queryByText(/第二条新提醒/)).toBeNull();
  });

  it("查看详情只确认对应id；ok:false保留记录并可重试，成功后旧读取不得恢复已确认id", async () => {
    mockedAck.mockResolvedValueOnce(false).mockResolvedValue(true);
    await setup([bubble()]);
    fireEvent.click(screen.getByTestId("assistant-alert-view"));
    expect(openSpy).toHaveBeenCalledWith({ symbol: "600519" });
    expect((await screen.findByRole("alert")).textContent).toContain("服务器未确认该提醒");
    expect(screen.getByTestId("assistant-alert-bubble")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试确认" }));
    await waitFor(() => expect(screen.queryByTestId("assistant-alert-bubble")).toBeNull());
    await pollAgain();
    expect(screen.queryByTestId("assistant-alert-summary")).toBeNull();
    expect(mockedAck.mock.calls.map(([id]) => id)).toEqual([1, 1]);
  });

  it("全部入口只确认当前批次，部分失败仅重试失败id，保留后来到达的新风险", async () => {
    let rejectSecond!: (error: Error) => void;
    mockedAck.mockImplementation((id) => id === 2 ? new Promise<boolean>((_resolve, reject) => { rejectSecond = reject; }) : Promise.resolve(true));
    await setup([bubble(), bubble({ id: 2 })]);
    fireEvent.click(screen.getByTestId("assistant-alert-all"));
    expect(push).toHaveBeenCalledWith("/agent?tab=alerts");
    await waitFor(() => expect(mockedAck).toHaveBeenCalledWith(2));
    mockedBubbles.mockResolvedValue([bubble({ id: 2 }), bubble({ id: 3, name: "后来新风险", symbol: "600127" })] as never);
    await pollAgain();
    await act(async () => { rejectSecond(new Error("确认接口离线")); });
    expect((await screen.findByRole("alert")).textContent).toContain("确认接口离线");
    expect(mockedAck).not.toHaveBeenCalledWith(3);
    mockedAck.mockResolvedValue(true);
    fireEvent.click(screen.getByRole("button", { name: "重试确认" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(mockedAck.mock.calls.map(([id]) => id)).toEqual([1, 2, 2]);
    expect(screen.getByTestId("assistant-alert-bubble").textContent).toContain("后来新风险");
  });

  it("读取失败保留已知提醒并给重试；恢复同id不能重新冒泡", async () => {
    await setup([bubble({ reason: "保留的提醒" })]);
    mockedBubbles.mockRejectedValueOnce(new Error("读取接口离线"));
    await pollAgain();
    expect(screen.getByTestId("assistant-alert-bubble").textContent).toContain("保留的提醒");
    expect(screen.getByText(/提醒刷新失败，保留上次列表/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "稍后" }));
    fireEvent.click(screen.getByRole("button", { name: "重试读取" }));
    await waitFor(() => expect(screen.queryByText(/提醒刷新失败/)).toBeNull());
    expect(screen.queryByTestId("assistant-alert-bubble")).toBeNull();
    expect(screen.getByTestId("assistant-alert-inbox")).toBeTruthy();
  });

  it("全部提醒导航失败不确认任何记录，原入口可重试", async () => {
    await setup([bubble(), bubble({ id: 2 })]);
    push.mockImplementationOnce(() => { throw new Error("页面入口暂不可用"); });
    fireEvent.click(screen.getByTestId("assistant-alert-all"));
    expect(screen.getByRole("alert").textContent).toContain("未确认提醒");
    expect(screen.getByTestId("assistant-alert-bubble")).toBeTruthy();
    expect(mockedAck).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("assistant-alert-all"));
    await waitFor(() => expect(screen.queryByTestId("assistant-alert-summary")).toBeNull());
    expect(mockedAck.mock.calls.map(([id]) => id)).toEqual([1, 2]);
  });

  it("读取重试与轮询不会并发；在途旧读取不能恢复刚确认的id", async () => {
    await setup([bubble()]);
    mockedBubbles.mockRejectedValueOnce(new Error("读取接口离线"));
    await pollAgain();
    let resolveReading!: (items: Awaited<ReturnType<typeof getAgentBubbles>>) => void;
    mockedBubbles.mockImplementationOnce(() => new Promise((resolve) => { resolveReading = resolve; }));
    const calls = mockedBubbles.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "重试读取" }));
    fireEvent.click(screen.getByRole("button", { name: "重试读取" }));
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(mockedBubbles.mock.calls.length).toBe(calls + 1);
    fireEvent.click(screen.getByTestId("assistant-alert-view"));
    await waitFor(() => expect(screen.queryByTestId("assistant-alert-bubble")).toBeNull());
    await act(async () => { resolveReading([bubble()] as never); });
    expect(screen.queryByTestId("assistant-alert-summary")).toBeNull();
    expect(mockedAck.mock.calls.map(([id]) => id)).toEqual([1]);
  });
});
