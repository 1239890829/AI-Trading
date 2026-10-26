import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, expect, it} from "vitest";
import type {SavedStrategyRun} from "@/lib/api/picks";
import {ReferenceOutcomeSummary} from "./method-run-reader";

afterEach(cleanup);
function outcomes(): NonNullable<SavedStrategyRun["outcomes"]> {
  return {state:"descriptive_reference",window_start:"2026-07-01",signal_through:"2026-10-23",outcome_through:"2026-10-26",
    cost_bps:35,return_identity:"adjusted_close_to_close_proxy",semantics:"研究参考",production_promotion_eligible:false,
    horizons:{"1":{signals:10,matured:6,immature:1,missing:3,wins:3,positive_rate:.5,mean_return_pct:1.2,stress_mean_return_pct:.85},
      "3":{signals:10,matured:4,immature:2,missing:4,wins:3,positive_rate:.75,mean_return_pct:2.1,stress_mean_return_pct:1.75},
      "5":{signals:10,matured:0,immature:8,missing:2,wins:0,positive_rate:null,mean_return_pct:null,stress_mean_return_pct:null}}};
}

it("switches real horizons and preserves mature missing versus immature denominators",()=>{
  render(<ReferenceOutcomeSummary outcomes={outcomes()}/>);
  expect(screen.getByText("上涨 3 / 可测 6")).toBeTruthy();
  expect(screen.getByText("50.0%")).toBeTruthy();
  expect(screen.getByRole("meter")).toHaveProperty("value",.5);
  fireEvent.click(screen.getByRole("button",{name:"D3"}));
  expect(screen.getByText("上涨 3 / 可测 4")).toBeTruthy();
  expect(screen.getByText("75.0%")).toBeTruthy();
  expect(screen.getByText("已成熟缺价").nextElementSibling?.textContent).toBe("4");
  expect(screen.getByText("未成熟").nextElementSibling?.textContent).toBe("2");
  fireEvent.click(screen.getByRole("button",{name:"D5"}));
  expect(screen.getByText("上涨 0 / 可测 0")).toBeTruthy();
  expect(screen.queryByRole("meter")).toBeNull();
  expect(screen.getAllByText("未测得")).toHaveLength(3);
});

it("does not turn an older saved batch into zero performance",()=>{
  render(<ReferenceOutcomeSummary outcomes={null}/>);
  expect(screen.getByText(/旧批未保存参考价表现/)).toBeTruthy();
  expect(screen.queryByRole("meter")).toBeNull();
});

it("rejects contradictory saved denominators",()=>{
  const value=outcomes();value.horizons["1"].missing=100;
  render(<ReferenceOutcomeSummary outcomes={value}/>);
  expect(screen.getByRole("alert").textContent).toContain("分母未能核对");
  expect(screen.queryByRole("meter")).toBeNull();
  expect(screen.queryByText("50.0%")).toBeNull();
});
