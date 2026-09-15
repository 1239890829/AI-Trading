import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { NotificationItem, NotificationsPayload } from "@/lib/api";

/**
 * 通知中心未读口径与外显（用户 2026-09-11 反馈）：
 *  ①「一键已读后计数消失，但新来的一条是在之前的累积上累加」→ 徽标必须等于**未读条数**；
 *  ②「未读消息加红点，读了就消失」→ 未读条目左侧红点，点开即消失。
 * 这里从**渲染结果**断言（徽标数字 / 红点数量），不读内部状态。
 */

let payload: NotificationsPayload;

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getNotifications: vi.fn(async () => payload),
    // 已读状态的服务端同步（2026-09-12）：组件挂载即触发 hydration。
    // 本文件只验「渲染结果」，把服务端桩成不可达 ⇒ 快照退回纯 localStorage 语义，
    // 与这些用例的断言口径一致（跨源持久化另有 notification-read.test.ts 覆盖）。
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

function item(over: Partial<NotificationItem> = {}): NotificationItem {
  return {
    id: "alert-1",
    category: "opportunity",
    label: "确认",
    session: "intraday",
    ts: "2026-09-11 12:35:00",
    title: "示例通知",
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
    generated_at: "2026-09-11 12:40:00",
    news_min_score: 60,
    policy: "stock_opportunities_only",
    errors: null,
  };
}

const badge = () => screen.queryByTestId("notification-badge");
const dots = () => screen.queryAllByTestId("notification-unread-dot");

/**
 * 生成「相对现在」的北京 naive 时间戳（后端 ts 的实际格式）。
 *
 * 为什么不用写死的时间：`全部已读` 的水位取的是**真实 Date.now()**，
 * 写死 ts 的用例会随运行时刻漂移（上午跑通过、下午跑就变成"新条目早于水位"）。
 * 用偏移量表达"更早 / 随后到来"，用例才与运行时间无关。
 */
function bjNow(offsetMs = 0): string {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(Date.now() + offsetMs));
}

async function openDrawer() {
  fireEvent.click(screen.getByRole("button", { name: /打开通知中心/ }));
  await waitFor(() => expect(screen.getByTestId("notification-list")).toBeTruthy());
}

beforeEach(() => {
  localStorage.clear();
  __resetPrefsCache();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("通知中心：未读计数与红点", () => {
  it("徽标 = 未读条数；打开抽屉后每条未读都有红点", async () => {
    payload = makePayload([
      item({ id: "a", ts: "2026-09-11 09:40:00", title: "A" }),
      item({ id: "b", ts: "2026-09-11 11:20:00", title: "B" }),
      item({ id: "c", ts: "2026-09-11 12:35:00", title: "C" }),
    ]);
    render(<NotificationBell />);

    await waitFor(() => expect(badge()?.textContent).toBe("3"));
    await openDrawer();
    expect(dots().length).toBe(3);
    expect(screen.getByTestId("notification-unread-summary").textContent).toContain("未读 3");
    // 每个时段 tab 上的未读红点
    expect(screen.getByTestId("notification-tab-dot-intraday")).toBeTruthy();
  });

  it("一键已读：红点与徽标同时清零", async () => {
    payload = makePayload([
      item({ id: "a", ts: "2026-09-11 09:40:00" }),
      item({ id: "b", ts: "2026-09-11 12:35:00" }),
    ]);
    render(<NotificationBell />);
    await waitFor(() => expect(badge()?.textContent).toBe("2"));
    await openDrawer();

    fireEvent.click(screen.getByTestId("notification-mark-all-read"));
    await waitFor(() => expect(dots().length).toBe(0));
    expect(badge()).toBeNull(); // 归零即不渲染徽标
    expect(screen.queryByTestId("notification-unread-summary")).toBeNull();
  });

  it("【回归】已读后来一条新通知：只算这一条，不是 0，也不是旧累积", async () => {
    // 先有三条当天通知（都早于"现在"），全部已读
    const older = (min: number) => bjNow(-min * 60_000);
    payload = makePayload([
      item({ id: "a", ts: older(180) }),
      item({ id: "b", ts: older(120) }),
      item({ id: "c", ts: older(60) }),
    ]);
    render(<NotificationBell />);
    await waitFor(() => expect(badge()?.textContent).toBe("3"));
    await openDrawer();
    fireEvent.click(screen.getByTestId("notification-mark-all-read"));
    await waitFor(() => expect(badge()).toBeNull());

    // 随后到来一条新的（同一天、晚于已读时刻）——旧实现因格式错配恒判已读 → 徽标永远 0
    payload = makePayload([
      item({ id: "a", ts: older(180) }),
      item({ id: "b", ts: older(120) }),
      item({ id: "c", ts: older(60) }),
      item({ id: "new", ts: bjNow(60_000), title: "新来的" }),
    ]);
    fireEvent.click(screen.getByLabelText("刷新通知"));

    await waitFor(() => expect(badge()?.textContent).toBe("1"));
    await waitFor(() => expect(dots().length).toBe(1));
    expect(screen.getByText("新来的")).toBeTruthy();
  });

  it("点开某条 → 该条红点消失、徽标减一，其余未读不受影响", async () => {
    payload = makePayload([
      item({ id: "a", ts: "2026-09-11 09:40:00", title: "A" }),
      item({ id: "b", ts: "2026-09-11 12:35:00", title: "B" }),
    ]);
    render(<NotificationBell />);
    await waitFor(() => expect(badge()?.textContent).toBe("2"));
    await openDrawer();

    fireEvent.click(screen.getByText("B"));
    await waitFor(() => expect(dots().length).toBe(1));
    expect(badge()?.textContent).toBe("1");
  });

  it("一键清除：清掉的条目既不出现在列表，也不计入未读", async () => {
    payload = makePayload([
      item({ id: "a", ts: "2026-09-11 09:40:00", title: "旧条目" }),
      item({ id: "b", ts: "2026-09-11 12:35:00", title: "新条目" }),
    ]);
    render(<NotificationBell />);
    await waitFor(() => expect(badge()?.textContent).toBe("2"));
    await openDrawer();

    fireEvent.click(screen.getByText("一键清除"));
    await waitFor(() => expect(badge()).toBeNull());
    // 清除是按时间水位：两条都在水位之前 → 列表清空
    await waitFor(() => expect(screen.queryByText("新条目")).toBeNull());
  });
});
