import { describe, expect, it } from "vitest";
import {
  NAV_ALIASES,
  NAV_ALLOWED_PATHS,
  buildNav,
  isAllowedNav,
  navAliasWords,
  navUrlForAlias,
} from "@/lib/nav-targets";

describe("isAllowedNav（站内白名单守卫）", () => {
  it("放行白名单内的站内路径（含 query）", () => {
    expect(isAllowedNav("/workbench?symbol=600519")).toBe(true);
    expect(isAllowedNav("/tape?tab=limitup&date=2026-09-04")).toBe(true);
    expect(isAllowedNav("/market?tab=heatmap")).toBe(true);
  });

  it("拦截站外与协议相对链接（注入防线）", () => {
    expect(isAllowedNav("https://evil.com")).toBe(false);
    expect(isAllowedNav("//evil.com")).toBe(false);
    expect(isAllowedNav("javascript:alert(1)")).toBe(false);
    expect(isAllowedNav("http:/evil.com")).toBe(false);
  });

  it("拦截白名单外的站内路径（未接入的落点不允许跳）", () => {
    expect(isAllowedNav("/settings")).toBe(false);
    expect(isAllowedNav("/")).toBe(false);
    expect(isAllowedNav("")).toBe(false);
  });

  it("白名单与别名表覆盖的落点一致（新增别名必须落在已接入页）", () => {
    for (const [alias, key] of Object.entries(NAV_ALIASES)) {
      const url = buildNav(key);
      expect(url, `别名「${alias}」对应的 ${key} 产出非法 URL`).not.toBeNull();
      expect(isAllowedNav(url!)).toBe(true);
    }
  });
});

describe("buildNav", () => {
  it("无参入口产出稳定 URL", () => {
    expect(buildNav("limitup")).toBe("/tape?tab=limitup");
    expect(buildNav("longhu")).toBe("/tape?tab=longhu");
    expect(buildNav("market_fund")).toBe("/market?tab=fund");
    expect(buildNav("research_review")).toBe("/research?tab=review");
    expect(buildNav("intraday_brief")).toBe("/intraday?sec=brief");
  });

  it("带日期/题材参数时正确编码", () => {
    expect(buildNav("limitup", "2026-09-04")).toBe("/tape?tab=limitup&date=2026-09-04");
    expect(buildNav("research_review", "2026-09-04")).toBe("/research?tab=review&date=2026-09-04");
    expect(buildNav("intraday_theme", "存储芯片")).toBe(
      `/intraday?theme=${encodeURIComponent("存储芯片")}`,
    );
  });

  it("个股类入口带 ct/rt 深链参数", () => {
    expect(buildNav("stock", "600519")).toBe("/workbench?symbol=600519");
    expect(buildNav("stock_flow", "600519")).toBe("/workbench?symbol=600519&ct=flow");
    expect(buildNav("stock_profile", "600519")).toBe("/workbench?symbol=600519&rt=profile");
  });

  it("未知 key 返回 null（调用方降级为纯文本，不渲染成链接）", () => {
    // @ts-expect-error 故意传非法 key 验证运行时兜底
    expect(buildNav("not_a_key")).toBeNull();
  });
});

describe("navUrlForAlias", () => {
  it("命中无参别名返回 URL", () => {
    expect(navUrlForAlias("涨停池")).toBe("/tape?tab=limitup");
    expect(navUrlForAlias("龙虎榜")).toBe("/tape?tab=longhu");
    expect(navUrlForAlias("复盘报告")).toBe("/research?tab=review");
  });

  it("需要参数的别名不参与无参匹配（避免跳到没有标的的空页）", () => {
    expect(navUrlForAlias("个股")).toBeNull();
  });

  it("未知词返回 null", () => {
    expect(navUrlForAlias("随便一个词")).toBeNull();
  });

  it("别名按长度降序，保证最长匹配优先", () => {
    const words = navAliasWords();
    for (let i = 1; i < words.length; i += 1) {
      expect(words[i - 1].length).toBeGreaterThanOrEqual(words[i].length);
    }
  });
});

describe("NAV_ALLOWED_PATHS", () => {
  it("不含 /stock 中转页（它只是重定向，不是落点）", () => {
    expect(NAV_ALLOWED_PATHS).not.toContain("/stock");
  });
});
