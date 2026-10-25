import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { HuntingCandidateSwitch, HuntingTaskRail } from "./hunting-task-rail";

afterEach(cleanup);
const query = "symbol=600127&date=2026-09-30&theme=%E5%86%9C%E4%B8%9A&ct=kline&from=%2Fmarket%3Ftab%3Dthemes&panel=tracking";
const params = (link: HTMLElement) => new URL(link.getAttribute("href")!, "http://localhost").searchParams;

describe("选股任务入口保真", () => {
  it("uses one local pair without repeating primary links in the discovery task menu", () => {
    render(<><HuntingTaskRail view="discover" section="daily" search={query} localCandidates/><HuntingCandidateSwitch group="daily" search={query} counts={{candidates:0,daily:3}}/></>);
    expect(screen.getAllByRole("link",{name:"每日精选"})).toHaveLength(1);
    expect(screen.getAllByRole("link",{name:"盘中候选"})).toHaveLength(1);
    const local=screen.getByRole("navigation",{name:"候选名单切换"});
    expect(local.contains(screen.getByRole("link",{name:"每日精选"}))).toBe(true);
    const daily=screen.getByRole("link",{name:"每日精选"});
    expect(daily.getAttribute("aria-current")).toBe("page");
    expect(daily.textContent).toContain("3只");
    const intraday=screen.getByRole("link",{name:"盘中候选"});
    expect(intraday.textContent).toContain("0只");
    expect(params(intraday).get("date")).toBe("2026-09-30");
    expect(params(intraday).get("symbol")).toBe("600127");
    expect(params(intraday).has("panel")).toBe(false);
    intraday.focus();fireEvent.keyDown(intraday,{key:"ArrowRight"});
    expect(document.activeElement).toBe(daily);
    fireEvent.keyDown(daily,{key:"Home",isComposing:true,keyCode:229});
    expect(document.activeElement).toBe(daily);
  });
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
    expect(screen.getAllByRole("link").map(link => link.textContent)).toEqual(["盘中候选", "每日精选", "题材参与", "机会依据", "选股方法"]);
    fireEvent.click(screen.getByRole("button", { name: "持续观察" }));
    expect(screen.getAllByRole("link").map(link => link.textContent)).toEqual(["跟踪记录", "接力与潜伏", "盘中节拍"]);
    fireEvent.click(screen.getByRole("button", { name: "验证复盘" }));
    expect(screen.getAllByRole("link").map(link => link.textContent)).toEqual(["验证统计", "当日对照", "龙头研究", "跨日复盘"]);
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

  it("keeps known counts distinct from unread counts and preserves link names and scope", () => {
    const page = render(<HuntingTaskRail view="discover" section="daily" search={query} counts={{candidates: 0, daily: 3}} />);
    const daily = screen.getByRole("link", {name: "每日精选"});
    expect(daily.textContent).toBe("每日精选3只");
    expect(document.getElementById(daily.getAttribute("aria-describedby")!)?.textContent).toBe("3只");
    expect(screen.getByRole("link", {name: "盘中候选"}).textContent).toBe("盘中候选0只");
    expect(params(daily).get("date")).toBe("2026-09-30");
    page.rerender(<HuntingTaskRail view="discover" section="daily" search={query} counts={{candidates: Number.NaN}} />);
    expect(screen.getByRole("link", {name: "盘中候选"}).textContent).toBe("盘中候选");
    expect(screen.getByRole("link", {name: "每日精选"}).hasAttribute("aria-describedby")).toBe(false);
  });

  it("supports explicit keyboard group and destination inspection without navigating on focus", () => {
    render(<HuntingTaskRail view="discover" section="daily" search={query} />);
    const discover = screen.getByRole("button", {name: "发现候选"});
    discover.focus(); fireEvent.keyDown(discover, {key: "ArrowRight"});
    const tracking = screen.getByRole("button", {name: "持续观察"});
    expect(document.activeElement).toBe(tracking);
    expect(tracking.getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(tracking, {key: "Home"});
    const first = screen.getByRole("link", {name: "盘中候选"});
    first.focus(); fireEvent.keyDown(first, {key: "ArrowRight"});
    const daily = screen.getByRole("link", {name: "每日精选"});
    expect(document.activeElement).toBe(daily);
    expect(daily.getAttribute("aria-current")).toBe("page");
    fireEvent.keyDown(daily, {key: "End"});
    expect(document.activeElement).toBe(screen.getByRole("link", {name: "选股方法"}));
    fireEvent.keyDown(document.activeElement!, {key: "ArrowLeft", isComposing: true, keyCode: 229});
    expect(document.activeElement).toBe(screen.getByRole("link", {name: "选股方法"}));
    fireEvent.keyDown(document.activeElement!, {key: "Home", ctrlKey: true});
    expect(document.activeElement).toBe(screen.getByRole("link", {name: "选股方法"}));
  });
});
