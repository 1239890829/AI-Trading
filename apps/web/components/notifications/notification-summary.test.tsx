import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import type {NotificationItem, NotificationsPayload} from "@/lib/api";
import {NotificationBell} from "./notification-drawer";
import {SymbolDetailCtx} from "@/components/detail/symbol-detail-context";
import {__resetPrefsCache} from "@/lib/notification-read";

let payload: NotificationsPayload;
const openSymbol = vi.fn();
vi.mock("@/lib/api", async () => ({
  ...await vi.importActual<typeof import("@/lib/api")>("@/lib/api"),
  getNotifications: vi.fn(async () => payload),
  getNotificationReadState: vi.fn(async () => {throw new Error("isolated browser");}),
  saveNotificationReadState: vi.fn(async () => {throw new Error("isolated browser");}),
}));
vi.mock("next/link", () => ({default: ({href, children, ...props}: {href: string; children: React.ReactNode}) => <a href={href} {...props}>{children}</a>}));

function notice(over: Partial<NotificationItem> = {}): NotificationItem {
  return {id:"alert-17", category:"opportunity", label:"入选观察", session:"intraday", ts:"2026-10-13T01:32:00Z", title:"【入选观察】600127 核对对象", body:"原消息需核对。".repeat(60) + "最终限制：不代表当前可以参与。", symbol:"600127", url:null, score:null, source:"盘中选股原结果", validity:"仅原时点成立，消息撤回或报价缺失时失效。", source_as_of:"2026-10-13T01:31:59Z", recorded_at:"2026-10-13T01:32:03Z", channels:[], references:[{event_id:17, kind:"selection", selection_source:"daily", trade_date:"2026-10-13", source_id:"daily:A", source_version:"version-A", source_as_of:"2026-10-13T01:31:59Z", recorded_at:"2026-10-13T01:32:03Z", evidence:{pick_basis:"供应链入选；但尚未确认公司直接受益，不能据此认定买点。", invalidations:["消息撤回", "报价缺失"], quote_audit:{source:"tencent", data_timestamp:"2026-10-13T01:31:58Z", quality:"medium"}}}], ...over};
}
async function open(over: Partial<NotificationItem> = {}) {
  const item = notice(over);
  payload = {items:[item], count:1, generated_at:"2026-10-13T01:33:00Z", news_min_score:60, policy:"stock_opportunities_only", errors:null};
  render(<SymbolDetailCtx.Provider value={{open:openSymbol, close:vi.fn()}}><NotificationBell /></SymbolDetailCtx.Provider>);
  fireEvent.click(screen.getByRole("button", {name:/打开通知中心/}));
  await waitFor(() => expect(screen.getByTestId("notification-row")).toBeTruthy());
  return item;
}
beforeEach(() => {
  localStorage.clear(); __resetPrefsCache();
  vi.stubGlobal("fetch", vi.fn(async () => {throw new Error("No network allowed");}));
  vi.stubGlobal("matchMedia", () => ({matches:true, addEventListener:vi.fn(), removeEventListener:vi.fn()}));
});
afterEach(() => {cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals();});

it("visibly labels the summary on touch screens and preserves a qualification beyond the excerpt in one-click detail",async()=>{
  const condition="但尚未完成审计，不保证交易成功。";
  const basis="拟收购事项及背景".repeat(25)+condition;
  await open({references:[{event_id:17,kind:"selection",trade_date:null,source_as_of:null,recorded_at:null,source_id:"daily:A",source_version:"version-A",evidence:{pick_basis:basis}}]});
  const row=screen.getByTestId("notification-row");
  expect(within(row).getByTestId("notification-summary").textContent).toContain("依据节选：");
  expect(within(row).getByText("600127 · 条件见详情")).toBeTruthy();
  expect(within(row).getByTestId("notification-summary").textContent).not.toContain(condition);
  fireEvent.click(within(row).getByTestId("notification-judgment"));
  await waitFor(()=>expect(screen.getByTestId("notification-detail")).toBeTruthy());
  fireEvent.click(screen.getByRole("button",{name:"完整依据与研究记录"}));
  expect(within(screen.getByTestId("notification-detail")).getByText(basis)).toBeTruthy();
});

it("shows the object, original reason including its limitation and key state without exposing a full dossier", async () => {
  await open();
  const row = screen.getByTestId("notification-row");
  expect(row.textContent).toContain("600127 核对对象");
  expect(row.textContent).toContain("供应链入选；但尚未确认公司直接受益，不能据此认定买点。");
  expect(row.textContent).toContain("当前条件需核对");
  expect(row.textContent).not.toContain("最终限制");
  expect(row.textContent).not.toContain("version-A");
  expect(row.textContent).not.toContain("原观察：");
  expect(within(row).queryByRole("tab")).toBeNull();
  expect(row.textContent).toContain("2026-10-13 09:32");
  expect(row.textContent).not.toContain("+08:00");
  expect(fetch).not.toHaveBeenCalled();
});

it("one click opens every original condition and both clocks; it does not open stock quotes", async () => {
  const item = await open();
  fireEvent.click(screen.getByText(item.title));
  const detail = await screen.findByRole("dialog", {name:item.title});
  fireEvent.click(within(detail).getByRole("button",{name:"完整依据与研究记录"}));
  expect(detail.textContent).toContain("最终限制：不代表当前可以参与。");
  expect(detail.textContent).toContain("供应链入选；但尚未确认公司直接受益");
  expect(detail.textContent).toContain("报价缺失");
  expect(detail.textContent).toContain("version-A");
  expect(detail.textContent).toContain("2026-10-13 09:31:59");
  expect(detail.textContent).toContain("2026-10-13 09:32:03");
  expect(detail.textContent).not.toContain("T01:");
  expect(within(detail).getByRole("link", {name:"查看此版本复盘"}).getAttribute("href")).toBe("/hunting?view=review&date=2026-10-13&version=version-A");
  expect(openSymbol).not.toHaveBeenCalled();
  expect(screen.queryByTestId("notification-unread-dot")).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it("the quote action owns its click and does not open notification detail or bubble to its row", async () => {
  const item = await open();
  fireEvent.click(within(screen.getByTestId("notification-row")).getByRole("link", {name:/行情/}));
  expect(openSymbol).toHaveBeenCalledTimes(1);
  expect(openSymbol).toHaveBeenCalledWith({symbol:"600127", preferModal:true});
  expect(screen.queryByRole("dialog", {name:item.title})).toBeNull();
  expect(screen.getByTestId("notification-unread-dot")).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
});

it("unknown channel results remain visible in a compact risk row and the full reason remains in detail", async () => {
  await open({category:"risk", label:"持仓风险", title:"成本信息待核", symbol:null, body:"成本缺失，不能完成本轮风险核对。", references:[], channels:[{channel:"feishu", state:"unknown", reason:"acceptance_unconfirmed", created_at_ms:1, expires_at_ms:2, accepted_at_ms:null}]});
  expect(screen.getByTestId("notification-row").textContent).toContain("外部结果待核");
  expect(within(screen.getByTestId("notification-row")).queryByRole("link", {name:/行情/})).toBeNull();
  fireEvent.click(screen.getByText("成本信息待核"));
  const detail = await screen.findByRole("dialog", {name:"成本信息待核"});
  fireEvent.click(within(detail).getByRole("button",{name:"完整依据与研究记录"}));
  expect(detail.textContent).toContain("平台受理回执未确认");
  expect(detail.textContent).toContain("成本缺失，不能完成本轮风险核对。");
  expect(fetch).not.toHaveBeenCalled();
});
