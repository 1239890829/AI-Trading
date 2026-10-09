import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MarketOverviewStrip } from "./intraday-sections";
import type { IntradayOpportunities, OpportunityTheme } from "@/lib/api";

const spies = vi.hoisted(() => ({inspection: vi.fn(), symbol: vi.fn()}));
vi.mock("@/components/inspection/inspection-context", () => ({useInspection: () => ({open: spies.inspection}), inspectionClick: () => () => {}}));
vi.mock("@/components/detail/symbol-detail-context", () => ({useSymbolDetail: () => ({open: spies.symbol})}));
vi.mock("@/components/stock-link", () => ({StockLink: ({symbol, children}: {symbol: string; children: ReactNode}) => <button onClick={() => spies.symbol({symbol})}>{children}</button>}));
vi.mock("@/components/ui/modal-shell", () => ({ModalShell: ({label, children, header, onClose, open}: {label: string; children: ReactNode; header: ReactNode; onClose: () => void; open: boolean}) => open ? <div role="dialog" aria-label={label}>{header}<button onClick={onClose}>关闭</button>{children}</div> : null}));
vi.mock("@/hooks/use-exit-presence", () => ({useExitPresence: (value: unknown) => ({value, active: value !== null})}));

function theme(name: string, succession: boolean | null): OpportunityTheme {
  return {theme:name,stage:"启动",stage_basis:["当日事件形成"],strength_score:60,strength_tier:"活跃",tier_basis:null,formation:null,health_note:null,risks:[],max_boards:2,limit_up_count:3,has_succession:succession,stocks:[],participants:[],participants_note:"本轮尚无合格参与候选"};
}
function payload(overrides: Partial<IntradayOpportunities> = {}): IntradayOpportunities {
  return {trade_date:"2026-10-09",themes:[theme("农业",false),theme("算力",null),theme("传媒",true)],summary:{limit_up_total:17,market_max_boards:4,top_theme:"农业",market_max_board_stocks:[{symbol:"300001",name:"完整池最高股",boards:4}]},hot_available:true,caveats:[],...overrides};
}
afterEach(() => {cleanup();vi.clearAllMocks();});

describe("今日盘面卡片", () => {
  it("opens the existing limit-up inspection with the batch trade day rather than navigating", () => {
    render(<MarketOverviewStrip opps={payload()} />);
    fireEvent.click(screen.getByRole("button", {name:/涨停家数/}));
    expect(spies.inspection).toHaveBeenCalledWith({kind:"limit-up",date:"2026-10-09",motionOrigin:undefined});
    expect(spies.symbol).not.toHaveBeenCalled();
  });

  it("opens the complete-pool highest identity even when it is outside the displayed main-board candidates", () => {
    render(<MarketOverviewStrip opps={payload()} />);
    fireEvent.click(screen.getByRole("button", {name:/最高连板/}));
    expect(spies.symbol).toHaveBeenCalledWith({symbol:"300001",motionOrigin:undefined});
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("requires a choice when multiple stocks share the highest board count", () => {
    const source = payload();
    source.summary.market_max_board_stocks!.push({symbol:"600127",name:"另一最高股",boards:4});
    render(<MarketOverviewStrip opps={source} />);
    fireEvent.click(screen.getByRole("button", {name:/最高连板/}));
    expect(spies.symbol).not.toHaveBeenCalled();
    const modal = screen.getByRole("dialog", {name:"最高连板个股"});
    fireEvent.click(within(modal).getByRole("button", {name:/另一最高股/}));
    expect(spies.symbol).toHaveBeenCalledWith({symbol:"600127"});
  });

  it("keeps the clicked theme batch stable across a new poll and includes themes with no participant", () => {
    const view = render(<MarketOverviewStrip opps={payload()} />);
    fireEvent.click(screen.getByRole("button", {name:/题材机会/}));
    const modal = screen.getByRole("dialog", {name:"本轮题材机会"});
    expect(within(modal).getByText(/按强度展示 3 个题材/)).toBeTruthy();
    expect(within(modal).getByText("农业")).toBeTruthy();
    expect(within(modal).getByText(/候选为空的题材也保留/)).toBeTruthy();
    const agriculture = within(modal).getByRole("button", {name:/农业/});
    expect(agriculture.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(agriculture);
    expect(agriculture.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById(agriculture.getAttribute("aria-controls")!)).not.toBeNull();
    view.rerender(<MarketOverviewStrip opps={payload({trade_date:"2026-10-10",themes:[theme("新题材",true)]})} />);
    expect(within(modal).getByText(/2026-10-09 · 保留点击时/)).toBeTruthy();
    expect(within(modal).queryByText("新题材")).toBeNull();
    fireEvent.click(within(modal).getByRole("button", {name:"关闭"}));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows only confirmed broken ladders and never treats unknown succession as broken", () => {
    render(<MarketOverviewStrip opps={payload()} />);
    fireEvent.click(screen.getByRole("button", {name:/梯队断层 1 个/}));
    const modal = screen.getByRole("dialog", {name:"梯队断层"});
    expect(within(modal).getByText("农业")).toBeTruthy();
    expect(within(modal).queryByText("算力")).toBeNull();
    expect(within(modal).queryByText("传媒")).toBeNull();
  });

  it("does not infer a missing highest stock identity from an available theme", () => {
    const source = payload();
    delete source.summary.market_max_board_stocks;
    render(<MarketOverviewStrip opps={source} />);
    const button = screen.getByRole("button", {name:/最高连板/}) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(spies.symbol).not.toHaveBeenCalled();
    expect(screen.getByText(/最高连板个股身份未返回/)).toBeTruthy();
  });

  it("valid zero counts open an honest empty detail while a missing payload stays unavailable", () => {
    const source = payload({themes:[],summary:{limit_up_total:0,market_max_boards:0,top_theme:null,market_max_board_stocks:[]}});
    const view = render(<MarketOverviewStrip opps={source} />);
    fireEvent.click(screen.getByRole("button", {name:/梯队断层 0 个/}));
    expect(screen.getByText(/暂无已确认断层/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name:"关闭"}));
    fireEvent.click(screen.getByRole("button", {name:/最高连板 0 板/}));
    expect(screen.getByText(/本批次暂无最高连板个股/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name:"关闭"}));
    view.rerender(<MarketOverviewStrip opps={null} />);
    expect(screen.getAllByRole("button").every(button => (button as HTMLButtonElement).disabled)).toBe(true);
  });
});
