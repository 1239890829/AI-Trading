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
  expect(screen.getByRole("heading",{name:/共 0 只/})).toBeTruthy();
 });
});

it("keeps unavailable counts unknown and does not invent an empty theme result", async()=>{
 vi.mocked(api.getLimitUpPool).mockRejectedValueOnce(new Error("source unavailable"));
 render(<LimitUpTab/>);
 await screen.findByText(/涨停池加载失败/);
 expect(screen.getByRole("heading",{name:"数量待核对"})).toBeTruthy();
 expect(screen.queryByRole("heading",{name:/共 0 只/})).toBeNull();
 expect(screen.queryByText(/的梯队成员均不在/)).toBeNull();
 expect(screen.queryByText(/今日暂无涨停/)).toBeNull();
});
