import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

/**
 * 「AI 判读提醒」气泡的**点击语义**（2026-09-16 用户指令）。
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
 * 气泡**只展示第一条**（`bubbles[0]`）。收敛主按钮时若把通往告警页的唯一入口一并去掉，
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
import { getAgentBubbles } from "@/lib/api";

const mockedBubbles = vi.mocked(getAgentBubbles);

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

describe("AI 判读提醒气泡 · 点击语义", () => {
  it("点「查看详情」打开**该股**的详情弹窗，且不得跳转告警页", async () => {
    await setup([bubble()]);

    fireEvent.click(screen.getByTestId("assistant-alert-view"));

    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledWith({ symbol: "600519" });
    // 核心诉求：**不跳页**。只断言"弹窗开了"不足以守住它——旧实现在同一位置
    // 既可能弹窗又可能顺带 push，用户仍会被拔走。
    expect(push).not.toHaveBeenCalled();
  });

  it("反向对照：无 symbol 的判读仍走告警页（不得退化成点了没反应的死按钮）", async () => {
    await setup([bubble({ symbol: "", name: "" })]);

    fireEvent.click(screen.getByTestId("assistant-alert-view"));

    expect(push).toHaveBeenCalledWith("/agent?tab=alerts");
    expect(openSpy).not.toHaveBeenCalled();
  });

  it("多条：给出「全部 N 条」入口并进告警页（气泡只展示第一条）", async () => {
    await setup([bubble(), bubble({ id: 2, event_id: 12, symbol: "000001", name: "平安银行" })]);

    const all = screen.getByTestId("assistant-alert-all");
    expect(all.textContent).toContain("2");

    fireEvent.click(all);
    expect(push).toHaveBeenCalledWith("/agent?tab=alerts");
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

    fireEvent.click(screen.getByTestId("assistant-alert-all"));
    expect(push).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledTimes(1); // 未二次弹窗
  });
});
