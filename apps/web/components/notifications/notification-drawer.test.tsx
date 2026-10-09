import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { getNotificationDiagnostics, getNotifications, type NotificationItem, type NotificationsPayload } from "@/lib/api";

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
    getNotificationDiagnostics: vi.fn(async () => payload.diagnostics ?? ranRejectedDiag),
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
import { DetailModalProvider } from "@/components/detail/detail-modal";
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
  it.each([
    ["临板 7.2%（距封板 +2.50pct）", "临板 7.2%（旧口径：距封板判定线 +2.50 个百分点）"],
    ["距实际涨停价还需上涨 2.33%", "距实际涨停价还需上涨 2.33%"],
  ])("列表与判读全文保持距离口径一致：%s", async (body, displayed) => {
    payload = makePayload([item({ title: "距离通知", body, symbol: "600519" })]);
    render(<DetailModalProvider><NotificationBell /></DetailModalProvider>);
    await openDrawer();
    expect(screen.getByTestId("notification-list").textContent).toContain(displayed);

    fireEvent.click(screen.getByTestId("notification-judgment"));
    expect((await screen.findByRole("dialog", { name: "距离通知" })).textContent).toContain(displayed);
    expect(payload.items[0].body).toBe(body);
  });

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

/**
 * 空态诊断（`BUG-016` 子项③，2026-09-16）。
 *
 * 用户现场反馈「为什么消息通知一个也没有呢」——旧界面只回一句
 * 「盘中暂无通过多维筛选的个股机会」，把三种处境（全被否 / 链路没跑 / 盘前没选出）
 * **说成同一件事**。这组用例守两件事：
 *  ① 后端给了诊断 ⇒ **真的渲染出来**（含逐股原因，不是只改个标题）；
 *  ② 后端没给 / 不该给 ⇒ **不臆造**，退回原有一句话。
 */
function diagPayload(
  diag: NotificationsPayload["diagnostics"],
  items: NotificationItem[] = [],
): NotificationsPayload {
  return { ...makePayload(items), diagnostics: diag };
}

const ranRejectedDiag: NonNullable<NotificationsPayload["diagnostics"]> = {
  state: "ran_rejected",
  trade_date: "2026-09-16",
  as_of: "2026-09-16 13:16:00",
  pick_set: {
    present: true,
    count: 1,
    tier_counts: { observe: 1 },
    score_range: [50.6, 50.6],
    observation_only: 1,
    no_buy_range: 1,
    gate: { stand_aside: true, level: "strong", phase: "退潮", reasons: ["大盘退潮"] },
  },
  decisions: {
    present: true,
    polls: 16,
    by_decision: { rejected: 1 },
    symbols: ["603162"],
    tier_counts: { observe: 1 },
    top_tier: "observe",
    unknown_tiers: [],
    reasons: [{ reason: "置信档 observe 不足（需 executable/strong）", count: 1, symbols: ["603162"] }],
    last_as_of: "2026-09-16 13:15:00",
  },
  // 与后端真实 note 同构：**强调** + `行内代码` 两种标记都有（否则剥离断言会平凡通过）
  note: "判定链**已跑**（16 拍 / 1 只），全部被否决；门顺序短路：`快照无现价` → `置信档`。",
};

describe("通知中心：空态诊断（BUG-016 子项③）", () => {
  it("空态且有诊断 → 渲染状态、原因与逐股明细", async () => {
    payload = diagPayload(ranRejectedDiag);
    render(<NotificationBell />);
    await openDrawer();

    const box = await screen.findByTestId("notification-empty-diagnosis");
    expect(box.dataset.state).toBe("ran_rejected");
    expect(box.textContent).toContain("已归档候选均被否决");
    expect(box.textContent).toContain("置信档 observe 不足（需 executable/strong）");
    expect(box.textContent).toContain("603162");
    // 计数口径：按 symbol 去重 ⇒ 16 拍不显示成 16 只
    expect(box.textContent).toContain("1 只");
    expect(box.textContent).toContain("判定 16 拍");
    expect(box.textContent).toContain("最高档 observe");
    expect(box.textContent).toContain("仅汇总今日已归档买点判定");
    expect(box.textContent).toContain("不代表全部每日或盘中入选结果，也不代表全部通知链");
    // 后端 note 按 Markdown 写，抽屉是纯文本 ⇒ 星号与反引号都不得出现在界面上
    // （反引号这条是**渲染实测**发现的：只剥 `**` 时界面会原样显示 `` `快照无现价` ``）
    expect(box.textContent).not.toContain("**");
    expect(box.textContent).not.toContain("`");
  });

  it("空态但后端没给诊断（旧后端/字段缺失）→ 退回原有一句话，不臆造", async () => {
    payload = makePayload([]); // 无 diagnostics 键
    render(<NotificationBell />);
    await openDrawer();

    expect(screen.queryByTestId("notification-empty-diagnosis")).toBeNull();
    expect(screen.getByText("盘中通知状态未知，请查看未提醒原因")).toBeTruthy();
  });

  it("非空态 → 不展示诊断块（即使该字段被误带上）", async () => {
    // 反向断言：诊断的语义是"整份 payload 为空"，不是"某 tab 为空"。
    // 若前端改成像后端一样无条件渲染，本条立刻红。
    payload = diagPayload(ranRejectedDiag, [
      item({ id: "a", ts: "2026-09-16 10:00:00", title: "有通知" }),
    ]);
    render(<NotificationBell />);
    await openDrawer();

    expect(await screen.findByText("有通知")).toBeTruthy();
    expect(screen.queryByTestId("notification-empty-diagnosis")).toBeNull();
  });

  it("本时段空但别的时段有 → 不展示诊断（只是切到了没内容的 tab）", async () => {
    payload = diagPayload(ranRejectedDiag, [
      item({ id: "a", session: "pre_open", ts: "2026-09-16 08:30:00", title: "盘前那条" }),
    ]);
    render(<NotificationBell />);
    await openDrawer(); // 默认 tab = 盘中

    expect(screen.queryByTestId("notification-empty-diagnosis")).toBeNull();
    expect(screen.getByText("该时段暂无可见通知，请查看其他时段。")).toBeTruthy();

    fireEvent.click(screen.getByText("盘前"));
    expect(await screen.findByText("盘前那条")).toBeTruthy();
  });

  it("诊断不可用（unavailable）→ 明说不可用，不得显示成「没有机会」", async () => {
    payload = diagPayload({
      ...ranRejectedDiag,
      state: "unavailable",
      note: "诊断数据读取失败（OperationalError: db down）⇒ **本字段为空不等于没有机会**。",
      decisions: { present: false, polls: 0 },
      pick_set: { present: false, count: 0 },
    });
    render(<NotificationBell />);
    await openDrawer();

    const box = await screen.findByTestId("notification-empty-diagnosis");
    expect(box.dataset.state).toBe("unavailable");
    expect(box.textContent).toContain("诊断不可用");
    expect(box.textContent).toContain("不等于没有机会");
  });
});

/**
 * 原事件计数不能证明消息资格、去重后的消息条数或全天运行状态。
 * 原入选/买点是机会来源；临板等观察记录不取得机会消息资格，风险/自设独立。
 *
 * ⚠️ 这组用例的**判据边界**：只验「渲染出来的形状计数是否讲对了话」，
 * 不验后端是否算对（那在 `backend/tests/test_notifications.py`）。
 */
describe("通知中心：空态形状计数（2026-09-16）", () => {
  const shapeDiag: NonNullable<NotificationsPayload["diagnostics"]> = {
    ...ranRejectedDiag,
    shapes: { selection: 0, buy_point: 0, pre_limit: 0, board_low_absorb: 293, board_flow_surge: 242 },
  };

  it("原入选/买点为 0 → 限定读取窗口，观察计数不冒充机会消息", async () => {
    payload = diagPayload(shapeDiag);
    render(<NotificationBell />);
    await openDrawer();

    const shapes = await screen.findByTestId("notification-empty-shapes");
    // 机器可读的口径，供 e2e/其它断言复用（别只靠文案匹配）
    expect(shapes.dataset.stockLevel).toBe("0");
    expect(shapes.textContent).toContain("买点 0");
    expect(shapes.textContent).toContain("入选 0");
    expect(shapes.textContent).toContain("读取窗口内未见原入选/买点记录");
    expect(shapes.textContent).toContain("不代表全天未运行");
    expect(shapes.textContent).toContain("板块低吸 293");
    // 同样是纯文本渲染面 ⇒ 标记不得漏到界面上（同 plainNote 的教训）
    expect(shapes.textContent).not.toContain("**");
    expect(shapes.textContent).not.toContain("`");
  });

  it("121 条临板记录仅供观察，不获得消息资格，也不能归因为字段校验失败", async () => {
    payload = diagPayload({
      ...ranRejectedDiag,
      shapes: { selection: 0, buy_point: 0, pre_limit: 121 },
    });
    render(<NotificationBell />);
    await openDrawer();

    const shapes = await screen.findByTestId("notification-empty-shapes");
    expect(shapes.dataset.stockLevel).toBe("0");
    expect(shapes.textContent).toContain("临板预警 121");
    expect(shapes.textContent).toContain("仅供观察");
    expect(shapes.textContent).toContain("不因此取得机会消息资格");
    expect(shapes.textContent).not.toContain("缺股票名称");
    expect(shapes.textContent).not.toContain("字段校验未通过");
  });

  it("原入选和买点计数是原记录合计，不推断未进入列表的原因；风险/自设独立", async () => {
    payload = diagPayload({ ...ranRejectedDiag, shapes: { selection: 2, buy_point: 3, pre_limit: 121, real_exit_alert: 1 } });
    render(<NotificationBell />);
    await openDrawer();
    const shapes = await screen.findByTestId("notification-empty-shapes");
    expect(shapes.dataset.stockLevel).toBe("5");
    expect(shapes.textContent).toContain("入选 2");
    expect(shapes.textContent).toContain("买点 3");
    expect(shapes.textContent).toContain("同股同日合并");
    expect(shapes.textContent).toContain("不能据此判断消息资格或未进入列表的原因");
    expect(shapes.textContent).toContain("风险和自设提醒独立");
    expect(shapes.textContent).not.toContain("缺股票名称");
  });

  it("shapes 为空对象 → 计数未知，不推断规则从未触发或调度未运行", async () => {
    payload = diagPayload({ ...ranRejectedDiag, shapes: {} });
    render(<NotificationBell />);
    await openDrawer();

    const shapes = await screen.findByTestId("notification-empty-shapes");
    expect(shapes.dataset.stockLevel).toBe("unknown");
    expect(shapes.textContent).toContain("来源形状计数未提供");
    expect(shapes.textContent).toContain("无法判断规则是否运行");
    expect(shapes.textContent).not.toContain("从未触发");
  });

  it("旧后端缺少入选计数 → 显示未知，不能当作入选 0", async () => {
    payload = diagPayload({ ...ranRejectedDiag, shapes: { buy_point: 0 } });
    render(<NotificationBell />);
    await openDrawer();
    const shapes = await screen.findByTestId("notification-empty-shapes");
    expect(shapes.dataset.stockLevel).toBe("unknown");
    expect(shapes.textContent).toContain("入选 未提供");
    expect(shapes.textContent).toContain("原入选/买点计数未完整提供");
    expect(shapes.textContent).not.toContain("读取窗口内未见原入选/买点记录");
  });

  it("旧后端不返回 shapes → 不渲染该块（不得臆造形状计数）", async () => {
    payload = diagPayload(ranRejectedDiag); // 无 shapes 键
    render(<NotificationBell />);
    await openDrawer();

    await screen.findByTestId("notification-empty-diagnosis");
    expect(screen.queryByTestId("notification-empty-shapes")).toBeNull();
  });
});

describe("通知事实与浏览状态分离（BUG-016）", () => {
  it("手动刷新失败保留上次内容，并标明徽标不是当前事实", async () => {
    payload = makePayload([item({ title: "上次提醒" })]);
    render(<NotificationBell />);
    await openDrawer();
    vi.mocked(getNotifications).mockRejectedValueOnce(new Error("source offline"));
    fireEvent.click(screen.getByLabelText("刷新通知"));
    await waitFor(() => expect(screen.getByText(/下方为上次读取结果/)).toBeTruthy());
    expect(screen.getByText("上次提醒")).toBeTruthy();
    expect(screen.getByRole("button", { name: /打开通知中心/ }).getAttribute("title")).toContain("上次读取结果");
    expect(screen.getByRole("button", { name: /打开通知中心/ }).getAttribute("aria-label")).toContain("上次读取的未读数");
  });

  it("来源失败时不把空列表解释成没有机会", async () => {
    payload = { ...diagPayload(ranRejectedDiag), errors: { alerts: "read failed" } };
    render(<NotificationBell />);
    await openDrawer();
    expect(screen.getByText(/通知来源读取失败，当前条目不可确认/)).toBeTruthy();
    expect(screen.queryByTestId("notification-empty-diagnosis")).toBeNull();
  });

  it("清除后解释为隐藏，且说明服务端未确认时仅本机有效", async () => {
    payload = makePayload([item({ ts: bjNow(-60_000), title: "已存在的提醒" })]);
    render(<NotificationBell />);
    await openDrawer();
    fireEvent.click(screen.getByText("一键清除"));
    await waitFor(() => expect(screen.getByText(/当前读取范围的通知已被一键清除隐藏/)).toBeTruthy());
    expect(screen.getByTestId("notification-sync-status").textContent).toContain("服务端未确认；当前页暂存");
    expect(screen.getByText("一键清除").getAttribute("title")).toContain("服务端确认后");
  });

  it("站内已读和渠道受理分别呈现，非空列表也能查看其他候选的未提醒原因", async () => {
    payload = makePayload([item({
      source: "盘中买点判定", validity: "条件变化后须重新核验",
      channels: [{ channel: "feishu", state: "accepted", reason: "ok",
        created_at_ms: 1, expires_at_ms: 2, accepted_at_ms: 1 }],
    })]);
    render(<NotificationBell />);
    await openDrawer();
    const facts = screen.getByTestId("notification-facts");
    expect(facts.textContent).toContain("盘中买点判定");
    expect(facts.textContent).toContain("渠道已受理（未确认送达）");
    expect(facts.textContent).toContain("未读仅表示站内尚未点开");
    payload.diagnostics = {
      ...ranRejectedDiag,
      decisions: { ...ranRejectedDiag.decisions, latest: [{
        symbol: "603162", name: "测试股", decision: "rejected", as_of: "2026-09-16 13:15:00",
        snapshot_id: "snap-1", decision_id: null, decision_version: null,
        reason: "置信档不足", data_state: "ready",
      }] },
    };
    fireEvent.click(screen.getByTestId("notification-explain"));
    await waitFor(() => expect(vi.mocked(getNotificationDiagnostics)).toHaveBeenCalled());
    const diagnosis = await screen.findByTestId("notification-empty-diagnosis");
    expect(diagnosis.textContent).toContain("置信档不足");
    expect(diagnosis.textContent).toContain("记录 snap-1");
    expect(diagnosis.textContent).toContain("版本 未记录");
  });
});

it("switches notice types with arrow/home/end keys and leaves composition untouched", async () => {
 payload=makePayload([]);render(<NotificationBell/>);await openDrawer();
 const choices=screen.getAllByRole("tab");
 expect(choices).toHaveLength(2);
 choices[0].focus();
 fireEvent.keyDown(choices[0],{key:"ArrowRight",isComposing:true});
 expect(choices[0].getAttribute("aria-selected")).toBe("true");
 fireEvent.keyDown(choices[0],{key:"ArrowRight"});
 expect(choices[1].getAttribute("aria-selected")).toBe("true");
 expect(document.activeElement).toBe(choices[1]);
 expect(choices[1].tabIndex).toBe(0);expect(choices[0].tabIndex).toBe(-1);
 fireEvent.keyDown(choices[1],{key:"Home"});
 expect(choices[0].getAttribute("aria-selected")).toBe("true");
 expect(document.activeElement).toBe(choices[0]);
 fireEvent.keyDown(choices[0],{key:"End"});
 expect(choices[1].getAttribute("aria-selected")).toBe("true");
});

it("user-defined reminders have their own category while stock opportunities, risks, timestamps and detail stay readable", async () => {
  payload = makePayload([
    item({id: "custom", category: "reminder", label: "自设条件提醒", title: "显式个股阈值", body: "研究观察，不构成买卖建议", symbol: "600519", source: "用户条件规则", validity: "原触发条件需重验"}),
    item({id: "stock", title: "原个股消息"}),
    item({id: "risk", category: "risk", label: "持仓风险", title: "原风险消息"}),
  ]);
  render(<DetailModalProvider><NotificationBell /></DetailModalProvider>);
  await openDrawer();
  expect(screen.getByRole("tab", {name: "个股提醒"}).getAttribute("title")).toContain("原入选/买点消息、真实持仓风险与自设条件提醒");
  const list = screen.getByTestId("notification-list");
  for (const text of ["自设条件提醒", "自设提醒", "原个股消息", "个股机会", "原风险消息", "策略风险", "12:35", "用户条件规则", "原触发条件需重验"])
    expect(list.textContent).toContain(text);
  fireEvent.click(screen.getByTestId("notification-judgment"));
  expect((await screen.findByRole("dialog", {name: "显式个股阈值"})).textContent).toContain("研究观察，不构成买卖建议");
});
