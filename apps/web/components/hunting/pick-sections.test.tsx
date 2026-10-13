import {afterEach, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {DailyReviews} from "./pick-sections";

vi.mock("next/navigation", () => ({useRouter: () => ({push: vi.fn()})}));
afterEach(cleanup);

it("retains the whole review note and its original version and observation boundary", () => {
  const note = "该观察仅用于核对原始条件，不证明已经成交。".repeat(25) + "最后失效条件：题材退潮后重新核验。";
  render(<DailyReviews reviews={[{date: "2026-10-09", symbol: "600127", name: "金健米业", verdict: "bad", reason_category: "board_receding", excess_pct: null, note, statistics_eligible: false, review_context: {selection_version: "original-v1", window_start: "2026-10-09T09:35:00+08:00", window_end: null}}]}/>);
  fireEvent.click(screen.getByRole("button", {name: "完整原文"}));
  expect(screen.getByLabelText("逐股复盘原说明完整原文").textContent).toContain(note);
  expect(screen.getByText("收益观察未知")).toBeTruthy();
  expect(screen.getByTitle("original-v1").textContent).toContain("未进入可信统计");
  expect(screen.getByTitle("original-v1").textContent).toContain("终点未知");
});
