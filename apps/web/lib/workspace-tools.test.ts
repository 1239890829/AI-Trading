import { describe, expect, it } from "vitest";
import { MARKET_LENSES, marketLensUrl, RESEARCH_TOOLS, MAINTENANCE_TOOLS } from "./workspace-tools";
import { activeTask, agentArea } from "./task-navigation";

describe("工作区身份与能力边界", () => {
  it("carries the object, date and return anchor across every market lens", () => {
    const input = new URLSearchParams({tab: "fund", date: "2026-09-30", symbol: "600127", focus: "农业", from: "/hunting?theme=农业"});
    for (const lens of MARKET_LENSES) {
      const url = new URL(marketLensUrl(lens.href, input.toString()), "https://example.invalid");
      expect(url.searchParams.get("symbol")).toBe("600127");
      expect(url.searchParams.get("date")).toBe("2026-09-30");
      expect(url.searchParams.get("from")).toBe("/hunting?theme=农业");
      expect(url.searchParams.get("focus")).toBe("农业");
      expect(url.search).not.toContain("%25");
      expect(url.searchParams.get("tab")).toBe(lens.key === "overview" ? null : lens.key);
    }
  });
  it("has one workspace entry while retaining each account identity", () => {
    for (const account of ["manual", "paper", "daily", "hunting"]) {
      const params = new URLSearchParams({mode: "positions", account});
      expect(activeTask("/workbench", params)).toBe("workspace");
      expect(params.get("account")).toBe(account);
    }
  });
  it("keeps configuration and controlled execution outside the research tools", () => {
    expect(RESEARCH_TOOLS.map(tool => tool.key)).not.toContain("params");
    expect(RESEARCH_TOOLS.map(tool => tool.key)).not.toContain("tasks");
    for (const tool of MAINTENANCE_TOOLS.filter(tool => !["review", "strategies"].includes(tool.key))) {
      expect(agentArea("research", tool.key)).toBe("maintenance");
    }
  });
});
