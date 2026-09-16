import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { ImpactEvent, NotificationItem, NotificationsPayload } from "@/lib/api";

/**
 * 通知中心「资讯 / 事件」tab（2026-09-16 用户需求①）。
 *
 * 背景：`IMP-028` 把通知中心收口成 `stock_opportunities_only` 之后，新闻与事件
 * **在通知中心彻底不可见**（实测生产库当日 `event_card` 798 条、通知中心 0 条）。
 * 本 tab 用**浏览面**补回可见性，且**明确不撤销收口**。
 *
 * 本文件钉四件事（都是"改坏了会静默变质"的那种）：
 *  ① **懒加载** —— 停在「个股机会」时**不发** `/api/events/impact` 请求。
 *     缺了它，抽屉一打开就白拉 100 条事件（且每 60s 一次）；
 *  ② **真的渲染出来** —— 切过去后 L1 徽标 / 标题 / 四类标签都在（不是只切了个高亮）；
 *  ③ **未读口径隔离** —— 资讯条目不进铃铛徽标：徽标仍**只**由个股机会驱动。
 *     这是本项最容易做错的地方：一旦资讯计入未读，`IMP-028` 的收口就被绕过了；
 *  ④ **点击不静默失败** —— 无原文链接的事件仍开通用详情弹窗（带 `eventId`）。
 */

const spies = vi.hoisted(() => ({
  detailOpen: vi.fn(),
  symbolOpen: vi.fn(),
  getImpactEvents: vi.fn(),
}));

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

// jsdom 下 App Router 的 `Link` / `useRouter` 依赖路由上下文
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
let eventItems: ImpactEvent[];

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getNotifications: vi.fn(async () => payload),
    getImpactEvents: (...args: unknown[]) => {
      spies.getImpactEvents(...args);
      return Promise.resolve({
        count: eventItems.length,
        countsAll: { L1: 1, L2: 1, L3: 7 },
        fourCounts: {},
        tagCounts: {},
        items: eventItems,
      });
    },
    getNotificationReadState: vi.fn(async () => {
      throw new Error("no server in tests");
    }),
    saveNotificationReadState: vi.fn(async () => {
      throw new Error("no server in tests");
    }),
  };
});

import { NotificationBell } from "@/components/notifications/notification-drawer";
import { EventFeed } from "@/components/notifications/event-feed";
import { SymbolDetailCtx } from "@/components/detail/symbol-detail-context";
import { __resetPrefsCache } from "@/lib/notification-read";

function event(over: Partial<ImpactEvent> = {}): ImpactEvent {
  return {
    id: 1,
    title: "示例事件标题",
    url: null,
    summary: null,
    source: "eastmoney",
    source_tier: 3,
    published_at: "2026-09-16 14:20:00",
    fact_kind: "fact",
    certainty: "done",
    category: "other",
    half_life_hours: 48,
    source_symbol: null,
    is_active: true,
    directions: [],
    four_category: "policy",
    four_label: "国家政策",
    impact_level: "L1",
    tags: ["行业"],
    ...over,
  };
}

function item(over: Partial<NotificationItem> = {}): NotificationItem {
  return {
    id: "alert-1",
    category: "opportunity",
    label: "个股机会",
    session: "intraday",
    ts: "2026-09-16 10:35:00",
    title: "某只个股买点",
    body: "正文",
    symbol: null,
    url: null,
    score: null,
    ...over,
  };
}

function makePayload(items: NotificationItem[]): NotificationsPayload {
  return {
    items,
    count: items.length,
    generated_at: "2026-09-16 14:40:00",
    news_min_score: 60,
    policy: "stock_opportunities_only",
    errors: null,
  };
}

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

async function switchToEvents() {
  fireEvent.click(screen.getByTestId("notification-mode-events"));
  await waitFor(() => expect(screen.getByTestId("event-feed-list")).toBeTruthy());
}

beforeEach(() => {
  localStorage.clear();
  __resetPrefsCache();
  payload = makePayload([]);
  eventItems = [
    event({ id: 1, title: "政策级事件", impact_level: "L1", four_label: "国家政策" }),
    event({
      id: 2,
      title: "题材级事件",
      impact_level: "L2",
      four_category: "hot",
      four_label: "市场热点",
      url: "https://example.com/a",
    }),
  ];
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("通知中心：资讯 / 事件 tab", () => {
  it("【不渲染即不请求】停在「个股机会」时抽屉里没有事件列表，切过去才请求", async () => {
    payload = makePayload([item({ title: "有提醒" })]);
    renderBell();
    await openDrawer();

    // 抽屉已打开、个股机会列表已渲染 —— 此时事件列表**不存在**
    expect(await screen.findByText("有提醒")).toBeTruthy();
    expect(screen.queryByTestId("event-feed-list")).toBeNull();
    expect(spies.getImpactEvents).not.toHaveBeenCalled();
    // 反向对照：切过去后必须请求（否则上面那条断言可以靠"永远不请求"平凡通过）
    await switchToEvents();
    await waitFor(() => expect(spies.getImpactEvents).toHaveBeenCalled());
  });

  it("【active=false 时不发请求】EventFeed 自身的契约，而非靠「没挂载」取巧", async () => {
    // ⚠️ 为什么必须单独测组件：上一条用例把 EventFeed 整块条件渲染掉了，
    // 于是**无论 `enabled` 传什么它都全绿** —— 实测把 `enabled: active` 注入成
    // `enabled: true`，上一条用例照过（判据盲区，见 `kb/09-verification-pitfalls.md`）。
    // 要真正钉住这个契约，必须让组件**挂载着但不激活**。
    const { unmount } = render(<EventFeed active={false} />);
    await waitFor(() => expect(screen.getByTestId("event-feed")).toBeTruthy());
    expect(spies.getImpactEvents).not.toHaveBeenCalled();
    unmount();

    // 反向对照：同一个组件、只把 active 翻成 true ⇒ 必须请求
    render(<EventFeed active />);
    await waitFor(() => expect(spies.getImpactEvents).toHaveBeenCalled());
  });

  it("切到资讯 tab ⇒ 渲染事件条目（影响力徽标 / 分类 / 标题）", async () => {
    renderBell();
    await openDrawer();
    await switchToEvents();

    expect(await screen.findByText("政策级事件")).toBeTruthy();
    expect(screen.getByText("题材级事件")).toBeTruthy();
    // 两级的徽标都要在（L1 是"必上"，L2 是"选上"，配色不同）
    const levels = screen.getAllByTestId("event-feed-level").map((n) => n.textContent);
    expect(levels).toEqual(["L1", "L2"]);
    expect(screen.getByText("国家政策")).toBeTruthy();
    expect(screen.getByText("市场热点")).toBeTruthy();
  });

  it("【口径隔离】资讯条目不进未读：徽标与红点仍只由个股机会驱动", async () => {
    payload = makePayload([item({ id: "a", title: "唯一的机会提醒" })]);
    renderBell();
    // 徽标 = 1（来自个股机会）
    await waitFor(() => expect(screen.getByTestId("notification-badge")?.textContent).toBe("1"));
    await openDrawer();
    await switchToEvents();

    // 资讯 tab 下渲染了两条资讯，但**没有任何未读红点**，徽标也不因资讯而增长
    expect(await screen.findByText("政策级事件")).toBeTruthy();
    expect(screen.queryAllByTestId("notification-unread-dot").length).toBe(0);
    expect(screen.getByTestId("notification-badge")?.textContent).toBe("1");
    // 推送面才有的操作在浏览面不出现（避免"点了没反馈"）
    expect(screen.queryByTestId("notification-mark-all-read")).toBeNull();
  });

  it("「只看 L1」筛选生效（且不是把 L2 隐藏后又全量显示）", async () => {
    renderBell();
    await openDrawer();
    await switchToEvents();

    expect(await screen.findByText("题材级事件")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("只看 L1"));
    await waitFor(() => expect(screen.queryByText("题材级事件")).toBeNull());
    expect(screen.getByText("政策级事件")).toBeTruthy();
  });

  it("无原文链接的事件 ⇒ 点击走通用详情弹窗，带 eventId（不静默失败）", async () => {
    renderBell();
    await openDrawer();
    await switchToEvents();

    fireEvent.click(await screen.findByText("政策级事件"));

    expect(spies.detailOpen).toHaveBeenCalledTimes(1);
    const arg = spies.detailOpen.mock.calls[0][0] as { kind: string; eventId: number | null };
    expect(arg.kind).toBe("event");
    // 只给标题会退化成"点开什么也没有"——必须能把事件 id 传下去取方向行/判定状态
    expect(arg.eventId).toBe(1);
  });
});
