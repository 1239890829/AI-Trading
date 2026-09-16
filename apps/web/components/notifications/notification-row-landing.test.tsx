import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { NotificationItem, NotificationsPayload } from "@/lib/api";
import { SymbolDetailCtx } from "@/components/detail/symbol-detail-context";

/**
 * 提醒**落点统一**（2026-09-16 `IMP-033`）。
 *
 * 缺口：同一个个股提醒，**点行体**开的是通用判读弹窗、**点「行情 ↗」**开的是该股详情弹窗
 * —— 一行里两个入口落到两个不同地方；而悬浮球（`openSymbolDetail`）与猎场（`StockLink`）
 * 早已统一到**个股详情弹窗**（[[KB-ENG-92]]「详情弹窗化」）。
 *
 * 本文件钉三件事：
 *  ① **有代码** ⇒ 行体开**该股详情弹窗**（与另两处同落点），**且不再开判读弹窗**；
 *  ② **判读全文没有因此丢失** ⇒ 行右侧「判读」入口仍能打开它
 *     （纪律来源：`IMP-031`「收敛主按钮不得以丢失能力为代价」）；
 *  ③ **反向对照** ⇒ 无代码的条目（如消息面）行体仍走通用弹窗，**不静默失败**。
 */

const spies = vi.hoisted(() => ({ detailOpen: vi.fn(), symbolOpen: vi.fn() }));

// 把 `useDetailModal` 换成**可断言的 spy**：本项的核心判据就是「开了哪个弹窗」。
// 靠渲染结果反推（同一个标题出现两次）既脆弱、又说不清到底是哪个弹窗被打开。
vi.mock("@/components/detail/detail-modal", async () => {
  const actual =
    await vi.importActual<typeof import("@/components/detail/detail-modal")>(
      "@/components/detail/detail-modal",
    );
  return {
    ...actual,
    useDetailModal: () => ({ open: spies.detailOpen, close: vi.fn() }),
  };
});

// jsdom 下 App Router 的 `Link` 依赖路由上下文，降级为普通 `<a>`（同
// `components/research/alerts-tab.test.tsx`）。**保留 `onClick`**——落点判据正是走它。
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    title,
    onClick,
  }: {
    href: string;
    children: React.ReactNode;
    title?: string;
    onClick?: React.MouseEventHandler<HTMLAnchorElement>;
  }) => (
    <a href={href} title={title} onClick={onClick}>
      {children}
    </a>
  ),
}));

let payload: NotificationsPayload;

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getNotifications: vi.fn(async () => payload),
    // 与 `notification-drawer.test.tsx` 同口径：只验渲染结果，服务端桩成不可达
    getNotificationReadState: vi.fn(async () => {
      throw new Error("no server in tests");
    }),
    saveNotificationReadState: vi.fn(async () => {
      throw new Error("no server in tests");
    }),
  };
});

import { NotificationBell } from "@/components/notifications/notification-drawer";
import { __resetPrefsCache } from "@/lib/notification-read";

const SYMBOL = "603330";

function item(over: Partial<NotificationItem> = {}): NotificationItem {
  return {
    id: "alert-1",
    category: "opportunity",
    label: "确认",
    session: "intraday",
    ts: "2026-09-16 10:35:00",
    title: "示例个股提醒",
    body: "AI 判读（建议关注）：放量突破前高，量比 2.3",
    symbol: SYMBOL,
    url: null,
    score: 78,
    ...over,
  };
}

function makePayload(items: NotificationItem[]): NotificationsPayload {
  return {
    items,
    count: items.length,
    generated_at: "2026-09-16 10:40:00",
    news_min_score: 60,
    policy: "stock_opportunities_only",
    errors: null,
  };
}

/** 渲染时必须**提供 `SymbolDetailCtx`**：不提供会取默认 noop context ⇒ 断言恒真、用例成摆设。 */
function renderBell() {
  return render(
    <SymbolDetailCtx.Provider value={{ open: spies.symbolOpen, close: vi.fn() }}>
      <NotificationBell />
    </SymbolDetailCtx.Provider>,
  );
}

async function openDrawer() {
  fireEvent.click(screen.getByRole("button", { name: /打开通知中心/ }));
  await waitFor(() => expect(screen.getByTestId("notification-list")).toBeTruthy());
}

const dots = () => screen.queryAllByTestId("notification-unread-dot");

beforeEach(() => {
  localStorage.clear();
  __resetPrefsCache();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("通知中心：个股提醒的落点统一（IMP-033）", () => {
  it("点行体 ⇒ 开**该股详情弹窗**，且不再开判读弹窗", async () => {
    payload = makePayload([item()]);
    renderBell();
    await openDrawer();

    fireEvent.click(screen.getByText("示例个股提醒"));

    expect(spies.symbolOpen).toHaveBeenCalledTimes(1);
    expect(spies.symbolOpen).toHaveBeenCalledWith({ symbol: SYMBOL });
    // 关键：行体**不再**落到判读弹窗——否则「点正文与点股票码弹出不同内容」依旧成立
    expect(spies.detailOpen).not.toHaveBeenCalled();
  });

  it("行体与「行情 ↗」**同落点**：两次点击都开该股，payload 形态相同", async () => {
    payload = makePayload([item()]);
    renderBell();
    await openDrawer();

    fireEvent.click(screen.getByText(/行情/));
    expect(spies.symbolOpen).toHaveBeenNthCalledWith(1, { symbol: SYMBOL });

    fireEvent.click(screen.getByText("示例个股提醒"));
    expect(spies.symbolOpen).toHaveBeenNthCalledWith(2, { symbol: SYMBOL });
    expect(spies.symbolOpen).toHaveBeenCalledTimes(2);
    expect(spies.detailOpen).not.toHaveBeenCalled();
  });

  it("判读全文**仍然可达**：点「判读」开判读弹窗（带分类与评分），且不开个股弹窗", async () => {
    payload = makePayload([item()]);
    renderBell();
    await openDrawer();

    fireEvent.click(screen.getByTestId("notification-judgment"));

    expect(spies.detailOpen).toHaveBeenCalledTimes(1);
    const arg = spies.detailOpen.mock.calls[0][0] as {
      kind: string;
      symbol: string | null;
      body: string | null;
      meta: { label: string; value: string }[];
    };
    expect(arg.kind).toBe("generic");
    expect(arg.symbol).toBe(SYMBOL);
    expect(arg.body).toContain("AI 判读");
    expect(arg.meta).toEqual([
      { label: "分类", value: "确认" },
      { label: "评分", value: "78" },
    ]);
    expect(spies.symbolOpen).not.toHaveBeenCalled();
  });

  it("【反向对照】无代码的条目 ⇒ 行体仍走通用弹窗，不静默失败", async () => {
    payload = makePayload([
      item({ symbol: null, category: "news", label: "快讯", title: "某条快讯" }),
    ]);
    renderBell();
    await openDrawer();

    fireEvent.click(screen.getByText("某条快讯"));

    expect(spies.detailOpen).toHaveBeenCalledTimes(1);
    expect((spies.detailOpen.mock.calls[0][0] as { kind: string }).kind).toBe("event");
    expect(spies.symbolOpen).not.toHaveBeenCalled();
    // 无代码 ⇒ 不该出现依赖 symbol 的「判读」入口（否则点了会弹一只空股）
    expect(screen.queryByTestId("notification-judgment")).toBeNull();
  });

  it("「判读」入口同样计入已读（点完红点消失，未读不作为代价）", async () => {
    payload = makePayload([item()]);
    renderBell();
    await waitFor(() => expect(screen.getByTestId("notification-badge")?.textContent).toBe("1"));
    await openDrawer();
    expect(dots().length).toBe(1);

    fireEvent.click(screen.getByTestId("notification-judgment"));

    await waitFor(() => expect(dots().length).toBe(0));
    expect(screen.queryByTestId("notification-badge")).toBeNull();
  });
});

/**
 * 「行情 + 判读」**一体化**（2026-09-16 用户要求：「行情与判读应该为一体的，
 * 可以放标签下方右边」）。
 *
 * 改造前：卡片**外面**并排挂着两个各自带框的按钮（`行情 ↗` / `判读`），
 * 与卡片不对齐、视觉上是三块互不相干的东西。
 * 改造后：两者进卡片、共用一个容器与边框、以竖线分隔，置于标签行右侧。
 *
 * ⚠️ 为什么必须守**结构**而不只是"两个文本都在"：
 * 把这两个入口重新挪到卡片外，文本断言**照样全绿**（两者仍在页面上），
 * 但用户看到的就是原来那个"三块不对齐"的形态——判据必须钉住**归属容器**。
 */
describe("通知中心：行情/判读一体化（2026-09-16）", () => {
  it("两个入口同属**一个**操作组，且该组在卡片内（不是挂在卡片外）", async () => {
    payload = makePayload([item()]);
    renderBell();
    await openDrawer();

    const row = screen.getByTestId("notification-row");
    const actions = screen.getByTestId("notification-actions");

    // ① 一体化：同一个容器里同时装得下「行情」与「判读」
    expect(actions.textContent).toContain("行情");
    expect(actions.querySelector('[data-testid="notification-judgment"]')).toBeTruthy();
    expect(actions.querySelector("a[href]")).toBeTruthy(); // 行情是真链接（右键可新标签打开）
    // ② 位置：它在**卡片内部**（用户要的"标签下方右边"以卡片为坐标系才有意义）
    expect(row.contains(actions)).toBe(true);
    // ③ 只有**一个**操作组（不是每行渲染两组：卡片内一份 + 卡片外一份）
    expect(screen.getAllByTestId("notification-actions").length).toBe(1);
    // ④ 卡片内不得出现"按钮嵌按钮/链接"的非法结构（浏览器会拆标签、点击语义互相吞掉）
    expect(row.querySelector("button button")).toBeNull();
    expect(row.querySelector("button a")).toBeNull();
  });
});
