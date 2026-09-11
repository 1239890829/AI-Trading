import { describe, expect, it } from "vitest";
import {
  NAV_ALIASES,
  NAV_ALLOWED_PATHS,
  NAV_LABELS,
  NAV_TARGETS,
  STOCK_TAB_ALIASES,
  buildNav,
  isAllowedNav,
  navAliasWords,
  navUrlForAlias,
  stockTabWords,
} from "@/lib/nav-targets";
import { parseChartTab, parseRightTab } from "@/lib/detail-tabs";

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
    // 2026-09-08 研究页下线：复盘落点改指 AI 控制台
    expect(buildNav("research_review")).toBe("/agent?tab=review");
    expect(buildNav("intraday_brief")).toBe("/hunting?sec=brief");
    expect(buildNav("picks")).toBe("/hunting?tag=pick");
    expect(buildNav("intraday")).toBe("/hunting?tag=watch");
    expect(buildNav("hunting")).toBe("/hunting");
  });

  it("带日期/题材参数时正确编码", () => {
    expect(buildNav("limitup", "2026-09-04")).toBe("/tape?tab=limitup&date=2026-09-04");
    expect(buildNav("research_review", "2026-09-04")).toBe("/agent?tab=review&date=2026-09-04");
    expect(buildNav("intraday_theme", "存储芯片")).toBe(
      `/hunting?theme=${encodeURIComponent("存储芯片")}`,
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
    expect(navUrlForAlias("复盘报告")).toBe("/agent?tab=review");
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

describe("STOCK_TAB_ALIASES（「个股 + 页签」组合识别用，P2-28②）", () => {
  it("每条都指向带 symbol 参数的个股入口，且 URL 过白名单守卫", () => {
    for (const [word, key] of Object.entries(STOCK_TAB_ALIASES)) {
      expect(key.startsWith("stock_"), `「${word}」→ ${key} 不是个股页签入口`).toBe(true);
      const url = buildNav(key, "600519");
      expect(url, `「${word}」产出 null`).not.toBeNull();
      expect(url!.startsWith("/workbench?symbol=600519"), `「${word}」落点不是工作台`).toBe(true);
      expect(isAllowedNav(url!)).toBe(true);
    }
  });

  it("同词可在两张表里指向不同落点（组合上下文优先，识别侧负责分流）", () => {
    // "资金流向" 单独出现 = 市场资金面；跟在个股后 = 该股资金图
    expect(NAV_ALIASES["资金流向"]).toBe("market_fund");
    expect(STOCK_TAB_ALIASES["资金流向"]).toBe("stock_flow");
  });

  it("页签词不靠无参路径进个股页签（带参入口必须由组合提供参数）", () => {
    for (const [word, key] of Object.entries(STOCK_TAB_ALIASES)) {
      const viaAlias = navUrlForAlias(word);
      if (viaAlias === null) continue; // 不在无参表里：正常
      // 同名词若在无参表里，必须是**另一个**落点（市场/盘面级），
      // 且绝不能落到 /workbench?symbol= 这种缺少标的的空页
      expect(
        viaAlias.startsWith("/workbench?symbol="),
        `「${word}」经无参路径跳到了个股页签`,
      ).toBe(false);
      expect(NAV_ALIASES[word], `「${word}」与页签词同 key`).not.toBe(key);
    }
    // 已知同名词举例（防回归：有人图省事把两张表合并）
    expect(navUrlForAlias("资金流向")).toBe("/market?tab=fund");
  });

  it("按长度降序，保证最长匹配优先（「资金流向图」先于「资金流向」）", () => {
    const words = stockTabWords();
    for (let i = 1; i < words.length; i += 1) {
      expect(words[i - 1].length).toBeGreaterThanOrEqual(words[i].length);
    }
  });

  it("不收单字词（会与正文抢匹配）", () => {
    for (const word of Object.keys(STOCK_TAB_ALIASES)) {
      expect(word.length, `页签词「${word}」过短`).toBeGreaterThanOrEqual(2);
    }
  });

  it("每个入口都有中文标签（助手按钮文案用）", () => {
    for (const key of Object.keys(NAV_TARGETS)) {
      expect(NAV_LABELS[key as keyof typeof NAV_LABELS], `${key} 缺 NAV_LABELS`).toBeTruthy();
    }
  });
});

/**
 * 构造器 ↔ 解析器的跨模块契约（2026-09-11）。
 *
 * 这是**没人验证过的一条接缝**：`nav-targets.ts` 产出 `?ct=` / `?rt=`，
 * `app/workbench/page.tsx` 解析它们。两边漂移时链接既不报错也不 404——
 * 只是**静默回落默认 tab**，正是 P2-28② 要修的那类"点不到位"。
 * 所以这里直接拿构造器产出的 URL 喂给解析器，断言能原样读出来。
 */
describe("深链参数与 workbench 解析器对得上（防静默回落）", () => {
  const qs = (url: string, k: string) => new URL(url, "http://x").searchParams.get(k);

  it("每个个股图表入口的 ct 都被 parseChartTab 认下", () => {
    for (const key of ["stock_kline", "stock_minute", "stock_flow"] as const) {
      const url = buildNav(key, "600519")!;
      const ct = qs(url, "ct")!;
      expect(parseChartTab(ct), `${key} 产出的 ct=${ct} 解析不了`).toBe(ct);
    }
  });

  it("每个个股右栏入口的 rt 都被 parseRightTab 认下", () => {
    for (const key of ["stock_book", "stock_trades", "stock_dt", "stock_profile", "stock_info"] as const) {
      const url = buildNav(key, "600519")!;
      const rt = qs(url, "rt")!;
      expect(parseRightTab(rt), `${key} 产出的 rt=${rt} 解析不了`).toBe(rt);
    }
  });

  it("符号与参数同时到得了（助手深链的完整形态）", () => {
    const url = buildNav("stock_minute", "600519")!;
    expect(qs(url, "symbol")).toBe("600519");
    expect(parseChartTab(qs(url, "ct"))).toBe("minute");
  });

  it("非法/缺失值一律 undefined（URL 是外部输入，回落默认不抛错）", () => {
    expect(parseChartTab(null)).toBeUndefined();
    expect(parseChartTab("bogus")).toBeUndefined();
    expect(parseRightTab(null)).toBeUndefined();
    expect(parseRightTab("bogus")).toBeUndefined();
  });

  it("纯个股入口不带 ct/rt（保持落首页的语义）", () => {
    const url = buildNav("stock", "600519")!;
    expect(qs(url, "ct")).toBeNull();
    expect(qs(url, "rt")).toBeNull();
  });
});
