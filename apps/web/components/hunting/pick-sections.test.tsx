import {afterEach, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import {DailyReviews, HistoryList, RolePerformanceTable} from "./pick-sections";

vi.mock("next/navigation", () => ({useRouter: () => ({push: vi.fn()})}));
afterEach(cleanup);

it("retains the whole review note and its original version and observation boundary", () => {
  const note = "该观察仅用于核对原始条件，不证明已经成交。".repeat(25) + "最后失效条件：题材退潮后重新核验。";
  render(<DailyReviews reviews={[{date: "2026-10-09", symbol: "600127", name: "金健米业", verdict: "bad", reason_category: "board_receding", excess_pct: null, note, statistics_eligible: false, review_context: {selection_version: "original-v1", window_start: "2026-10-09T09:35:00+08:00", window_end: null}}]}/>);
  fireEvent.click(screen.getByRole("button", {name: "逐股复盘完整说明"}));
  expect(screen.getByText(note).textContent).toContain(note);
  expect(screen.getByText("收益观察未知")).toBeTruthy();
  expect(screen.getByTitle("original-v1").textContent).toContain("未进入可信统计");
  expect(screen.getByTitle("original-v1").textContent).toContain("终点未知");
});

it("keeps role observation values attached to their field labels when the table is rearranged", () => {
  render(<RolePerformanceTable rows={[{role: "中军", count: 6, win_rate: 70, avg_excess: 1.5}]}/>);
  const table = screen.getByRole("table", {name: "梯队角色价格观察"});
  const cells = within(within(table).getAllByRole("row")[1]).getAllByRole("cell");
  expect(cells.map(cell => [cell.getAttribute("data-label"), cell.textContent])).toEqual([
    ["角色", "中军"], ["样本", "6"], ["走好观察占比", "70%"], ["平均超额", "+1.50%"],
  ]);
  expect(screen.getByText(/同版窗口；不是成交胜率/)).toBeTruthy();
});

it("retains the complete original symbol list, date and average in a long historical group", () => {
  const symbols = ["000333", "601229", "001318", "001317", "600127", "002264", "002315", "002261"];
  render(<HistoryList history={[{date: "2026-10-28", score_avg: 67.25, symbols}]}/>);
  expect(screen.getByText("2026-10-28")).toBeTruthy();
  expect(screen.getByText("均分 67.25")).toBeTruthy();
  expect(screen.getByText(symbols.join(" · ")).textContent).toBe(symbols.join(" · "));
});
