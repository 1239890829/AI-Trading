import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { AccountScopePanel } from "./account-scope-panel";
vi.mock("@/lib/api", () => ({getPaperAccount: vi.fn(async () => ({total: 100})), getPaperOrders: vi.fn(async () => [])}));
vi.mock("@/lib/api/internal", () => ({getJson: vi.fn(async () => ({data: {enabled:false, note:"未启用精选影子"}}))}));
const api = await import("@/lib/api"); const internal = await import("@/lib/api/internal");
const meta = {provider:"fixture",is_realtime:false,is_stale:false,last_success_refresh:null,generated_at:"2026-10-09T10:00:00+08:00"};
const activation = {configured:true,runner_loaded:true,timezone:"Asia/Shanghai",start_time:"09:30",end_time:"09:40",mode:"daily_morning",quote_recheck_seconds:null,poll_seconds:60};
afterEach(() => {cleanup();vi.clearAllMocks();});
describe("account identity", () => {
  it("uses a read-only main snapshot in inspection and does not invent initial capital", async () => {
    vi.mocked(api.getPaperAccount).mockResolvedValueOnce({account_created: false, total: null} as never);
    render(<AccountScopePanel account="paper" readOnly />);
    expect(await screen.findByText(/main 账户尚未建立/)).toBeTruthy();
    expect(api.getPaperAccount).toHaveBeenCalledWith(true);
    expect(screen.queryByText(/账户总额/)).toBeNull();
  });
  it("manual records never fetch or fabricate account capital", () => {
    render(<AccountScopePanel account="manual" />);
    expect(screen.getByText(/非券商验证/)).toBeTruthy();
    expect(api.getPaperAccount).not.toHaveBeenCalled();expect(internal.getJson).not.toHaveBeenCalled();
  });
  it("changing from main to daily cannot retain main capital or orders", async () => {
    const {rerender} = render(<AccountScopePanel account="paper" />);
    expect(await screen.findByText(/账户总额 100/)).toBeTruthy();
    rerender(<AccountScopePanel account="daily" />);
    expect(await screen.findByText("未启用精选影子")).toBeTruthy();
    expect(screen.queryByText(/账户总额/)).toBeNull();
    expect(internal.getJson).toHaveBeenCalledWith("/api/picks/shadow");
  });
  it("hunting reads only its scope and preserves historical evidence while disabled", async () => {
    vi.mocked(internal.getJson).mockResolvedValueOnce({meta:{provider:"fixture",is_realtime:false,is_stale:false,last_success_refresh:null,generated_at:"2026-10-01T10:00:00+08:00"}, data: {enabled:false, note:"猎场影子未启用", status:"incomplete", opportunities:1, independent_decisions:1, closed_fills:0, counts:{filled:1}, net_return_pct:null, net_median_pct:null, win_rate:null, issues:[], runtime:{state:"not_loaded", as_of:null}, records:[{id:"a", symbol:"600127", state:"filled", reason:"accepted", filled_price:10, net_return_pct:null, decision_version:"v1", entry_order_id:1}]}});
    render(<AccountScopePanel account="hunting" />);
    expect(await screen.findByText("猎场影子未启用")).toBeTruthy();
    expect(screen.getByText(/结果未成熟/)).toBeTruthy();
    expect(screen.getByText(/已成交，待退出/)).toBeTruthy();
    expect(api.getPaperAccount).not.toHaveBeenCalled();
    expect(internal.getJson).toHaveBeenCalledWith("/api/paper/hunting-shadow");
    expect(screen.queryByText(/账户总额/)).toBeNull();
  });

  it("shows the configured morning window rather than a hardcoded default and does not expose an activation command", async () => {
    vi.mocked(internal.getJson).mockResolvedValueOnce({meta, data:{enabled:true,activation,runtime:{state:"unknown",as_of:null},scope:"shadow",cash:1000,executed_today:false,positions:[],recent_orders:[]}});
    render(<AccountScopePanel account="daily" />);
    expect(await screen.findByText(/09:30–09:40/)).toBeTruthy();
    expect(screen.getByText("后台回执：运行状态未知")).toBeTruthy();
    expect(screen.getByText(/今日尚无买入委托/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", {name:"执行时机"}));
    expect(screen.getByText(/后台检查间隔 60 秒/)).toBeTruthy();
    expect(screen.getByText(/错过晨窗不追价/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", {name:"资格条件"}));
    expect(screen.getByText(/T\+1、涨跌停、整手、费用与停牌限制/)).toBeTruthy();
    expect(screen.queryByText(/09:26/)).toBeNull();
    expect(screen.queryByRole("button", {name:/启动|启用/})).toBeNull();
    expect(api.getPaperAccount).not.toHaveBeenCalled();
  });

  it("does not equate configured daily activation with a loaded runner or a filled order", async () => {
    vi.mocked(internal.getJson).mockResolvedValueOnce({meta,data:{enabled:true,activation:{...activation,runner_loaded:false},runtime:{state:"not_loaded",as_of:null},executed_today:true,recent_orders:[{id:2,symbol:"600127",side:"buy",quantity:100,status:"rejected",reason:"limit_up"}]}});
    render(<AccountScopePanel account="daily" />);
    expect(await screen.findByText(/已配置，但运行器未加载/)).toBeTruthy();
    expect(screen.getByText(/今日已有买入委托，成交情况请核下方回执/)).toBeTruthy();
    expect(screen.getByText(/已拒绝 · limit_up/)).toBeTruthy();
    expect(screen.queryByText(/已成交/)).toBeNull();
  });

  it("leaves missing window and runtime unknown instead of inferring readiness from enabled", async () => {
    vi.mocked(internal.getJson).mockResolvedValueOnce({meta,data:{enabled:true}});
    render(<AccountScopePanel account="daily" />);
    const missing = await screen.findByRole("region", {name:"仍需核对"});
    expect(within(missing).getByText("当前晨窗配置尚未返回")).toBeTruthy();
    expect(screen.getByText("后台回执：运行状态尚未返回")).toBeTruthy();
    expect(screen.queryByText(/最近一轮已完成/)).toBeNull();
  });

  it("explains the opportunity shadow's qualified trigger and distinguishes paused runtime from completed evidence", async () => {
    vi.mocked(internal.getJson).mockResolvedValueOnce({meta,data:{enabled:true,activation:{...activation,mode:"qualified_buy_point",start_time:null,end_time:null,quote_recheck_seconds:60,poll_seconds:30},note:"只消费现有获准买点规则",trade_date:"2026-10-09",status:"incomplete",opportunities:0,independent_decisions:0,closed_fills:0,counts:{},net_return_pct:null,net_median_pct:null,win_rate:null,issues:[],runtime:{state:"paused",as_of:null},records:[]}});
    render(<AccountScopePanel account="hunting" />);
    await screen.findByRole("tab", {name:"执行时机"});
    expect(screen.getByText("后台回执：调度已暂停")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", {name:"执行时机"}));
    expect(screen.getByText(/决定须在 60 秒内有效/)).toBeTruthy();
    expect(screen.getByText(/后台检查间隔 30 秒/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", {name:"资格条件"}));
    expect(screen.getByText(/仅入选或出现在题材参考区不会自动触发/)).toBeTruthy();
    expect(screen.queryByText(/09:30–09:40/)).toBeNull();
  });

  it("reading the four stages preserves raw receipt time and reason without fetching again or activating a runner", async () => {
    vi.mocked(internal.getJson).mockResolvedValueOnce({meta,data:{enabled:true,activation,runtime:{state:"degraded",as_of:"2026-10-09T09:34:12+08:00",reason:"quote_age_exceeded；限价未成交"},positions:[],recent_orders:[]}});
    render(<AccountScopePanel account="daily" />);
    expect(await screen.findByText("后台回执：运行失败，需核回执")).toBeTruthy();
    expect(screen.getByRole("tabpanel").textContent).toContain("用途：验证每日精选通过执行闸门后的模拟表现。");
    fireEvent.click(screen.getByRole("tab", {name:"后台回执"}));
    expect(screen.getByText("2026-10-09T09:34:12+08:00")).toBeTruthy();
    expect(screen.getByText("quote_age_exceeded；限价未成交")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", {name:"用途"}));
    expect(screen.queryByText("quote_age_exceeded；限价未成交")).toBeNull();
    expect(internal.getJson).toHaveBeenCalledTimes(1);
    expect(api.getPaperAccount).not.toHaveBeenCalled();
  });

});
