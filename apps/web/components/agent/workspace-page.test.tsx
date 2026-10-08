import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import AgentPage from "@/app/agent/page";

const nav = vi.hoisted(() => ({search: "", push: vi.fn(), replace: vi.fn()}));
vi.mock("next/navigation", () => ({useRouter: () => nav, useSearchParams: () => new URLSearchParams(nav.search)}));
vi.mock("@/components/agent/evolution-tab", () => ({EvolutionTab: () => <div>迭代内容</div>}));
vi.mock("@/components/agent/kb-browser-tab", () => ({KbBrowserTab: () => <div>知识内容</div>}));
vi.mock("@/components/agent/repo-tracker-tab", () => ({RepoTrackerTab: () => <div>追踪内容</div>}));
vi.mock("@/components/agent/params-tab", () => ({ParamsTab: () => <div>配置内容</div>}));
vi.mock("@/components/agent/strategy-health-tab", () => ({StrategyHealthTab: () => <div>核验内容</div>}));
vi.mock("@/components/agent/task-center", () => ({TaskCenter: () => <div>任务内容</div>}));
vi.mock("@/components/research/alerts-tab", () => ({AlertsTab: () => <div>规则内容</div>}));
vi.mock("@/components/agent/production-operations", () => ({ProductionOperations: () => <div>运行结果</div>}));
vi.mock("@/components/hunting/leader-research-panel", () => ({LeaderResearchPanel: () => <div>样本内容</div>}));
vi.mock("@/components/research/review-tab", () => ({ReviewTab: ({allowDispose}: {allowDispose: boolean}) => <div>{allowDispose ? "受控处置内容" : "只读复盘内容"}</div>}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("折叠工具与原深链", () => {
  it("does not mount maintenance tools in daily research and preserves context on opening", () => {
    nav.search = "area=research&tab=review&date=2026-09-30&symbol=600127";
    render(<AgentPage />);
    expect(screen.getByText("只读复盘内容")).toBeTruthy();
    expect(screen.queryByText("配置内容")).toBeNull();
    expect(screen.queryByText("核验内容")).toBeNull();
    fireEvent.click(screen.getByText("样本与知识"));
    fireEvent.click(screen.getByRole("button", {name: /龙头样本/}));
    const url = new URL(nav.push.mock.calls[0][0], "https://example.invalid");
    expect(url.searchParams.get("date")).toBe("2026-09-30");
    expect(url.searchParams.get("symbol")).toBe("600127");
    expect(url.searchParams.get("tab")).toBe("leaders");
  });
  it("opens an old configuration link in maintenance without granting a research action", () => {
    nav.search = "tab=params&date=2026-09-30";
    render(<AgentPage />);
    expect(screen.getByRole("dialog", {name: "参数配置"})).toBeTruthy();
    expect(screen.getByText("配置内容")).toBeTruthy();
    expect(screen.queryByText("只读复盘内容")).toBeNull();
    expect(screen.queryByText("任务内容")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "关闭"}));
    expect(nav.replace).toHaveBeenCalledWith("/agent?tab=operations&date=2026-09-30&area=maintenance", {scroll: false});
  });
  it("allows method evidence as read-only research without enabling disposal", () => {
    nav.search = "area=research&tab=strategies";
    render(<AgentPage />);
    expect(screen.getByRole("dialog", {name: "方法验证"})).toBeTruthy();
    expect(screen.getByText("核验内容")).toBeTruthy();
    expect(screen.queryByText("受控处置内容")).toBeNull();
  });
});
