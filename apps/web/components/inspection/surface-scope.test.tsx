import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { InspectionScope, useSurfaceScope } from "./surface-scope";

const route = vi.hoisted(() => ({search: "date=20261009&mode=watch&account=manual"}));
vi.mock("next/navigation", () => ({useSearchParams: () => new URLSearchParams(route.search)}));

function ScopeReader() {
  const {searchParams, replaceSearch} = useSurfaceScope();
  return <><output aria-label="当前范围">{searchParams.toString()}</output><button onClick={() => {const next = new URLSearchParams(searchParams); next.set("date", "2026-10-02"); replaceSearch(next);}}>切换日期</button></>;
}
afterEach(() => {cleanup(); vi.restoreAllMocks();});

describe("inspection filter ownership", () => {
  it("changes the embedded reader scope while the originating workspace and URL stay intact", () => {
    const replace = vi.spyOn(window.history, "replaceState");
    render(<InspectionScope initialSearch="date=20261001&theme=粮食"><ScopeReader /></InspectionScope>);
    expect(screen.getByLabelText("当前范围").textContent).toBe("date=20261001&theme=%E7%B2%AE%E9%A3%9F");
    fireEvent.click(screen.getByRole("button", {name: "切换日期"}));
    expect(screen.getByLabelText("当前范围").textContent).toBe("date=2026-10-02&theme=%E7%B2%AE%E9%A3%9F");
    expect(replace).not.toHaveBeenCalled();
    expect(route.search).toBe("date=20261009&mode=watch&account=manual");
  });

  it("retains URL ownership for the standalone page reader", () => {
    const replace = vi.spyOn(window.history, "replaceState");
    render(<ScopeReader />);
    fireEvent.click(screen.getByRole("button", {name: "切换日期"}));
    expect(replace).toHaveBeenCalledWith({}, "", "?date=2026-10-02&mode=watch&account=manual");
  });
});
