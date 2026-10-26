import {cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import {afterEach, expect, it} from "vitest";
import type {TradingAgentsResearchReport} from "@/lib/api/agent";
import {TradingAgentsReportExplorer} from "./tradingagents-report-explorer";

afterEach(cleanup);
const report: TradingAgentsResearchReport = {symbol: "600410", version: 1, state: "completed", run_id: "saved-run", generated_at: "2026-10-23T13:43:00+08:00", as_of: "2026-10-23T08:55:00+08:00", generated_today: true,
  upstream_sha: "upstream", input_sha256: "input", method_sha256: "method", verification: "unverified_full_graph_research", production_eligible: false,
  review_state: "finite_failed_requires_review", review_summary: {finite_failures: 2, peer_review_required: 7, source_verified: false, independent_human_gold: false},
  reports: {market_report: "价格偏弱，但不证明继续下跌。未重取数据。", sentiment_report: "缺少有效情绪来源，保持未知。", news_report: "没有本周新增消息证据。", fundamentals_report: "收入增长，但回款尚未核实。不能确认盈利修复。", investment_plan: "有产品线索，但订单归属待核。", trader_investment_plan: "缺少账户与实时价格，不提供仓位。", final_trade_decision: '**Rating**: Underweight\n\n**Executive Summary**: 盈利兑现反证更强，维持谨慎研究。不得形成确定卖出指令。<PEER_REFS>{"citations":[]}</PEER_REFS>'}};

it("shows a short saved excerpt and truthful report coverage, not full raw prose or a synthetic score", () => {
  render(<TradingAgentsReportExplorer report={report}/>);
  expect(screen.getByText("谨慎研究")).toBeTruthy();
  expect(screen.getByText("7 / 7 份已保存")).toBeTruthy();
  expect(screen.getByRole("tabpanel").textContent).toContain("盈利兑现反证更强，维持谨慎研究。");
  expect(screen.queryByText(/PEER_REFS/)).toBeNull();
  expect(screen.queryByRole("meter")).toBeNull();
  expect(screen.queryByLabelText("最终判断完整原文")).toBeNull();
});

it("compares two named originals and switches a source without producing another report", () => {
  render(<TradingAgentsReportExplorer report={report}/>);
  fireEvent.click(screen.getByRole("button", {name: "并排对照"}));
  const comparison = screen.getByLabelText("对照报告摘录");
  expect(comparison.textContent).toContain("收入增长，但回款尚未核实。");
  fireEvent.change(screen.getByLabelText("对照哪份原答"), {target: {value: "news_report"}});
  expect(comparison.textContent).toContain("没有本周新增消息证据。");
  fireEvent.click(screen.getByRole("tab", {name: /公司/}));
  expect(screen.getByRole("tabpanel").textContent).toContain("收入增长，但回款尚未核实。");
  expect((within(comparison).getByRole("combobox") as HTMLSelectElement).value).toBe("news_report");
});

it("keeps comparison sources distinct when the left side becomes the previous comparison source", () => {
  render(<TradingAgentsReportExplorer report={report}/>);
  fireEvent.click(screen.getByRole("button", {name: "并排对照"}));
  fireEvent.click(screen.getByRole("tab", {name: /公司/}));
  expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("final_trade_decision");
  expect(screen.getByLabelText("对照报告摘录").textContent).toContain("盈利兑现反证更强");
});

it("supports keyboard report selection, ignores IME navigation, and preserves every complete original on demand", () => {
  render(<TradingAgentsReportExplorer report={report}/>);
  const final = screen.getByRole("tab", {name: /最终判断/});
  fireEvent.keyDown(final, {key: "Home", isComposing: true});
  expect(final.getAttribute("aria-selected")).toBe("true");
  fireEvent.keyDown(final, {key: "Home"});
  expect(document.activeElement).toBe(screen.getByRole("tab", {name: /行情/}));
  for (const [key, label] of [["market_report", "行情"], ["sentiment_report", "情绪"], ["news_report", "新闻"], ["fundamentals_report", "公司"], ["investment_plan", "多空研究"], ["trader_investment_plan", "交易研究"], ["final_trade_decision", "最终判断"]] as const) {
    fireEvent.click(screen.getByRole("tab", {name: new RegExp(label)}));
    fireEvent.click(screen.getByRole("button", {name: `核对${label}完整原文`}));
    expect(screen.getByLabelText(`${label}完整原文`).querySelector("pre")?.textContent).toBe(report.reports[key]);
  }
});

it("gives older seven-report results a whole-passage navigator while retaining the full source",()=>{
  const raw="收入增长。回款未核实，不能确认盈利修复。\n\n第二段给出产品业务范围，尚无订单证据。";
  render(<TradingAgentsReportExplorer report={{...report,reports:{...report.reports,fundamentals_report:raw}}}/>);
  fireEvent.click(screen.getByRole("tab",{name:/公司/}));
  expect(screen.getByRole("tabpanel").textContent).toContain("收入增长。回款未核实，不能确认盈利修复。");
  expect(screen.queryByText("第二段给出产品业务范围，尚无订单证据。")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"原文论据"}));
  fireEvent.click(screen.getByRole("button",{name:/原文第2段/}));
  expect(screen.getByText("第二段给出产品业务范围，尚无订单证据。")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"上一段"}));
  expect(screen.getByRole("button",{name:"上一段"}).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button",{name:"核对公司完整原文"}));
  expect(screen.getByLabelText("公司完整原文").querySelector("pre")?.textContent).toBe(raw);
});

it("opens the chosen comparison source directly in its own original-evidence view",()=>{
  render(<TradingAgentsReportExplorer report={report}/>);
  fireEvent.click(screen.getByRole("button",{name:"并排对照"}));
  fireEvent.click(screen.getByRole("button",{name:"核对公司论据"}));
  expect(screen.getByRole("tab",{name:/公司/}).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByLabelText("公司原文索引")).toBeTruthy();
  expect(screen.queryByLabelText("对照报告摘录")).toBeNull();
});
