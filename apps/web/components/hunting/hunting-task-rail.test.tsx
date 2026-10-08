import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { HuntingTaskRail } from "./hunting-task-rail";

afterEach(cleanup);
const query = "symbol=600127&date=2026-09-30&theme=%E5%86%9C%E4%B8%9A&ct=kline&from=%2Fmarket%3Ftab%3Dthemes&panel=tracking";
const params = (link: HTMLElement) => new URL(link.getAttribute("href")!, "http://localhost").searchParams;

describe("选股任务入口保真", () => {
  it("keeps object, date, source and chart context when switching consumer", () => {
    render(<HuntingTaskRail view="discover" section={null} search={query} />);
    const evidence = params(screen.getByRole("link", { name: "机会依据" }));
    expect(evidence.get("view")).toBe("evidence");
    expect(evidence.get("symbol")).toBe("600127");
    expect(evidence.get("date")).toBe("2026-09-30");
    expect(evidence.get("theme")).toBe("农业");
    expect(evidence.get("ct")).toBe("kline");
    expect(evidence.get("from")).toBe("/market?tab=themes");
    expect(evidence.has("panel")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "持续观察" }));
    expect(params(screen.getByRole("link", { name: "跟踪记录" })).get("view")).toBe("tracking");
    expect(params(screen.getByRole("link", { name: "接力与潜伏" })).get("sec")).toBe("postmarket");
  });

  it("keeps every original capability reachable without showing all tools at once", () => {
    render(<HuntingTaskRail view="discover" section={null} search="" />);
    expect(screen.getAllByRole("link").map(link => link.textContent)).toEqual(["盘中候选", "每日精选", "题材参与", "机会依据"]);
    fireEvent.click(screen.getByRole("button", { name: "持续观察" }));
    expect(screen.getAllByRole("link").map(link => link.textContent)).toEqual(["跟踪记录", "接力与潜伏", "盘中节拍"]);
    fireEvent.click(screen.getByRole("button", { name: "验证复盘" }));
    expect(screen.getAllByRole("link").map(link => link.textContent)).toEqual(["当日对照", "龙头研究", "跨日复盘"]);
    const crossday = params(screen.getByRole("link", { name: "跨日复盘" }));
    expect(crossday.get("area")).toBe("research");
    expect(crossday.get("tab")).toBe("review");
    expect(crossday.get("from")).toBe("/hunting");
  });

  it("follows incoming deep links and only marks one current destination", () => {
    const { rerender } = render(<HuntingTaskRail view="tracking" section={null} search={query} />);
    expect(screen.getByRole("link", { name: "跟踪记录" }).getAttribute("aria-current")).toBe("page");
    fireEvent.click(screen.getByRole("button", { name: "发现候选" }));
    rerender(<HuntingTaskRail view="research" section={null} search={query} />);
    expect(screen.getByRole("button", { name: "验证复盘" }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("link", { name: "龙头研究" }).getAttribute("aria-current")).toBe("page");
    expect(screen.queryByRole("link", { name: "跟踪记录" })).toBeNull();
  });
});
