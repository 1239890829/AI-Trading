import { describe, expect, it } from "vitest";
import { activeTask, agentArea, patchWorkspaceUrl, TASK_LINKS } from "./task-navigation";

describe("任务导航与对象身份", () => {
  it("keeps every existing route reachable with explicit task and account distinctions", () => {
    for (const task of TASK_LINKS) {
      const url = new URL(task.href, "https://example.invalid");
      expect(activeTask(url.pathname, url.searchParams)).toBe(task.id);
    }
    expect(activeTask("/tape", new URLSearchParams("date=2026-09-29"))).toBe("market");
    expect(agentArea(null, "params")).toBe("maintenance");
    expect(agentArea(null, "review")).toBe("research");
    expect(agentArea("research", "tasks")).toBe("maintenance");
  });
  it("encodes Chinese once and preserves scope, date, version and return anchor when changing symbol", () => {
    const old = new URLSearchParams({mode: "positions", account: "paper", date: "2026-09-29", version: "v2", from: "/tape?focus=农业&date=2026-09-29"});
    const url = new URL(patchWorkspaceUrl("/workbench", old.toString(), {symbol: "600127", rt: "trade"}), "https://example.invalid");
    expect(url.searchParams.get("from")).toBe("/tape?focus=农业&date=2026-09-29");
    expect(url.searchParams.get("account")).toBe("paper");
    expect(url.searchParams.get("date")).toBe("2026-09-29");
    expect(url.searchParams.get("version")).toBe("v2");
    const theme = new URL(patchWorkspaceUrl("/hunting", "", {theme: "农业"}), "https://example.invalid");
    expect(theme.searchParams.get("theme")).toBe("农业");
    expect(theme.search).not.toContain("%25");
  });
});
