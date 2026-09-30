import {afterEach, describe, expect, it, vi} from "vitest";
import {cleanup, render, screen} from "@testing-library/react";
import {LimitUpTab} from "./limit-up-tab";
const state = vi.hoisted(() => ({params:new URLSearchParams("date=20260929")}));
vi.mock("next/navigation", () => ({useSearchParams:()=>state.params}));
vi.mock("@/lib/api", () => ({getLimitUpPool:vi.fn(async () => [])}));
vi.mock("@/components/stock-link", () => ({StockLink:({children}:{children:React.ReactNode})=>children,useStockRowNav:()=>()=>()=>{}}));
const api=await import("@/lib/api");
afterEach(()=>{cleanup();vi.clearAllMocks();});
describe("empty limit pool date", () => {
 it("retains the requested date in the title and the date input for a legal empty set", async()=>{
  render(<LimitUpTab/>);
  await screen.findByText(/暂无涨停/);
  expect((screen.getByLabelText("按日期查询：") as HTMLInputElement).value).toBe("2026-09-29");
  expect(screen.getByRole("heading",{name:"涨停池 · 20260929"})).toBeTruthy();
  expect(api.getLimitUpPool).toHaveBeenCalledWith("20260929");
 });
});
