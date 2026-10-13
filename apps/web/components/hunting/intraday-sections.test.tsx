import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { IntradayThemeRow, OpportunitySection } from "./intraday-sections";
import type { IntradayOpportunities, OpportunityTheme } from "@/lib/api";

vi.mock("@/components/picks/pick-card", () => ({
  fromIntradayStock: (item: unknown) => item,
  PickCard: ({ item, readError }: { item: { symbol: string }; readError?: boolean }) =>
    <article data-testid={item.symbol} data-read-error={String(!!readError)}>{readError ? "参与条件待核" : "当前可参与"}</article>,
}));
afterEach(() => {cleanup(); vi.useRealTimers();});
const theme: OpportunityTheme = {
  theme: "固定题材", stage: "主升", stage_basis: [], strength_score: 70, strength_tier: "A",
  tier_basis: null, formation: null, health_note: null, risks: [], max_boards: 2,
  limit_up_count: 1, has_succession: true,
  participants: [{ symbol: "600001" }] as OpportunityTheme["participants"],
  stocks: [{ symbol: "600002" }] as OpportunityTheme["stocks"],
};
const opps: IntradayOpportunities = { trade_date: "2026-10-09", themes: [theme], caveats: [], hot_available: false,
  summary: { limit_up_total: 1, market_max_boards: 2, top_theme: "固定题材" } };

it("keeps all collapsed theme risks visible and internal inspection does not toggle the row", () => {
  const toggle = vi.fn(), ancestor = vi.fn();
  render(<div onClick={ancestor}><IntradayThemeRow t={{...theme, risks: ["第一风险", "第二风险", "第三风险", "第四风险不能省略"]}} expanded={false} onToggle={toggle} /></div>);
  expect(screen.getByText("第四风险不能省略")).toBeTruthy();
  fireEvent.click(screen.getByText("市场梯队"));
  expect(toggle).not.toHaveBeenCalled();
  expect(ancestor).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("第一风险"));
  expect(toggle).toHaveBeenCalledTimes(1);
  expect(ancestor).not.toHaveBeenCalled();
});

it("题材来源失效同时降级参与组与梯队组，恢复后再显示当前资格", () => {
  const view = render(<OpportunitySection opps={opps} expanded="固定题材" onToggle={() => {}} showLedger={false} readError />);
  expect(screen.getByTestId("600001").dataset.readError).toBe("true");
  expect(screen.getByTestId("600002").dataset.readError).toBe("true");
  expect(screen.queryByText(/只 · 当前未封板、可进入参与评估/)).toBeNull();
  expect(screen.getByRole("heading", { name: /上次题材机会/ })).toBeTruthy();
  view.rerender(<OpportunitySection opps={opps} expanded="固定题材" onToggle={() => {}} showLedger={false} />);
  expect(screen.getByTestId("600001").dataset.readError).toBe("false");
  expect(screen.getByTestId("600002").dataset.readError).toBe("false");
});

it("theme collapse removes candidates from the accessible scope immediately and rapid reversal preserves the reopened cards", () => {
  vi.useFakeTimers();
  const view = render(<OpportunitySection opps={opps} expanded="固定题材" onToggle={() => {}} showLedger={false} />);
  view.rerender(<OpportunitySection opps={opps} expanded={null} onToggle={() => {}} showLedger={false} />);
  expect(screen.queryAllByRole("article")).toHaveLength(0);
  expect(screen.getByTestId("600001").closest("[inert]")).toBeTruthy();
  view.rerender(<OpportunitySection opps={opps} expanded="固定题材" onToggle={() => {}} showLedger={false} />);
  act(() => vi.advanceTimersByTime(200));
  expect(screen.getAllByRole("article")).toHaveLength(2);
});
