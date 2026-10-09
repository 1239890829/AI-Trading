import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
    expect(screen.getByText(/运行状态未知/)).toBeTruthy();
    expect(screen.getByText(/今日尚无买入委托/)).toBeTruthy();
    fireEvent.click(screen.getByText("什么时候需要启用"));
    expect(screen.getByText(/后台检查间隔 60 秒/)).toBeTruthy();
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
    expect(await screen.findByText(/当前晨窗配置尚未返回/)).toBeTruthy();
    expect(screen.getByText(/运行状态尚未返回/)).toBeTruthy();
    expect(screen.queryByText(/最近一轮已完成/)).toBeNull();
  });

  it("explains the opportunity shadow's qualified trigger and distinguishes paused runtime from completed evidence", async () => {
    vi.mocked(internal.getJson).mockResolvedValueOnce({meta,data:{enabled:true,activation:{...activation,mode:"qualified_buy_point",start_time:null,end_time:null,quote_recheck_seconds:60,poll_seconds:30},note:"只消费现有获准买点规则",trade_date:"2026-10-09",status:"incomplete",opportunities:0,independent_decisions:0,closed_fills:0,counts:{},net_return_pct:null,net_median_pct:null,win_rate:null,issues:[],runtime:{state:"paused",as_of:null},records:[]}});
    render(<AccountScopePanel account="hunting" />);
    expect(await screen.findByText(/决定须在 60 秒内有效/)).toBeTruthy();
    expect(screen.getByText(/调度已暂停/)).toBeTruthy();
    fireEvent.click(screen.getByText("什么时候需要启用"));
    expect(screen.getByText(/仅入选或出现在题材参考区不会自动触发/)).toBeTruthy();
    expect(screen.getByText(/后台检查间隔 30 秒/)).toBeTruthy();
    expect(screen.queryByText(/09:30–09:40/)).toBeNull();
  });

});
