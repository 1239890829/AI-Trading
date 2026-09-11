import { describe, expect, it } from "vitest";
import { createEntityMatcher, type EntityDict } from "@/lib/entity-links";

const dict: EntityDict = {
  stocks: [
    { name: "贵州茅台", code: "600519" },
    { name: "平安银行", code: "000001" },
    { name: "AI眼镜", code: "301598" },
  ],
  themes: ["存储芯片", "芯片设备", "AI", "机器人"],
};

describe("createEntityMatcher", () => {
  it("空词典返回空匹配器", () => {
    expect(createEntityMatcher(null)("贵州茅台涨停")).toEqual([]);
    expect(createEntityMatcher({ stocks: [], themes: [] })("贵州茅台涨停")).toEqual([]);
  });

  it("识别股票名称", () => {
    const m = createEntityMatcher(dict);
    expect(m("贵州茅台今天表现如何")).toEqual([
      { type: "stock", text: "贵州茅台", code: "600519", name: "贵州茅台" },
    ]);
  });

  it("识别字典内代码，字典外数字不误判", () => {
    const m = createEntityMatcher(dict);
    expect(m("600519 收盘价 123456 元")).toEqual([
      { type: "stock", text: "600519", code: "600519", name: "贵州茅台" },
    ]);
  });

  it("识别题材名，长名优先于短名", () => {
    const m = createEntityMatcher(dict);
    const hits = m("芯片设备和存储芯片都在涨");
    expect(hits.map((h) => h.text)).toEqual(["芯片设备", "存储芯片"]);
    expect(hits.every((h) => h.type === "theme")).toBe(true);
  });

  it("不命中字母数字单词内部（OpenAI 不出 AI）", () => {
    const m = createEntityMatcher(dict);
    expect(m("OpenAI 是一家公司")).toEqual([]);
  });

  it("日期数字里不出代码（20260904 连号 8 位）", () => {
    const m = createEntityMatcher(dict);
    expect(m("20260904 的行情")).toEqual([]);
  });

  it("连续混合文本按序识别", () => {
    const m = createEntityMatcher(dict);
    const hits = m("机器人板块里的平安银行与AI眼镜");
    expect(hits.map((h) => [h.type, h.text])).toEqual([
      ["theme", "机器人"],
      ["stock", "平安银行"],
      ["stock", "AI眼镜"],
    ]);
  });

  it("空文本安全", () => {
    expect(createEntityMatcher(dict)("")).toEqual([]);
  });

  // ---- 功能入口别名（2026-09-06 扩面：回答里的功能名可一键跳转） ----

  it("识别功能入口别名并带上站内 URL", () => {
    const m = createEntityMatcher(null);
    const hits = m("今日涨停池有 48 家，龙虎榜净买入集中在存储芯片");
    const navs = hits.filter((h) => h.type === "nav");
    expect(navs.map((h) => h.text)).toEqual(["涨停池", "龙虎榜"]);
    expect(navs[0].url).toBe("/tape?tab=limitup");
    expect(navs[1].url).toBe("/tape?tab=longhu");
    expect(navs.every((h) => h.url!.startsWith("/"))).toBe(true);
  });

  it("个股/题材与功能别名可共存且各自带正确类型", () => {
    const m = createEntityMatcher(dict);
    const hits = m("贵州茅台的复盘报告见研究页，存储芯片仍在涨停池");
    expect(hits.map((h) => [h.type, h.text])).toEqual([
      ["stock", "贵州茅台"],
      ["nav", "复盘报告"],
      ["theme", "存储芯片"],
      ["nav", "涨停池"],
    ]);
  });

  it("空词典也识别功能别名（词典没加载不阻塞跳转）", () => {
    expect(createEntityMatcher(undefined)("看一下盘前简报").map((h) => h.type)).toEqual(["nav"]);
  });

  // ---- 「个股 + 页签」组合（2026-09-11，P2-28②） ----
  //
  // 背景：页签构造器（stock_minute 等）早已就绪，但识别侧只产裸 stock，
  // 于是助手说"到分时页签看"时只能落到工作台首页——说白了就是点不到位。
  // 下面钉住三件事：**命中组合要直达**、**不命中要老实落首页**、**页签词不能
  // 被二次识别成独立入口**（"资金流向"单独出现是市场资金面，跟在个股后是该股资金图）。

  it("个股紧跟页签词 → 产出直达该页签的深链", () => {
    const m = createEntityMatcher(dict);
    expect(m("可以到贵州茅台的分时图看")).toEqual([
      {
        type: "stock",
        text: "贵州茅台",
        code: "600519",
        name: "贵州茅台",
        key: "stock_minute",
        url: "/workbench?symbol=600519&ct=minute",
      },
    ]);
  });

  it("间隔允许空白与一个「的」，两种写法等价", () => {
    const m = createEntityMatcher(dict);
    const url = (t: string) => m(t)[0]?.url;
    expect(url("600519 K线")).toBe("/workbench?symbol=600519&ct=kline");
    expect(url("600519的K线")).toBe("/workbench?symbol=600519&ct=kline");
    expect(url("贵州茅台的资金流向图")).toBe("/workbench?symbol=600519&ct=flow");
    expect(url("平安银行的逐笔成交")).toBe("/workbench?symbol=000001&rt=trades");
  });

  it("组合命中后页签词不再单独成链（同一词在两个上下文里是两个落点）", () => {
    const m = createEntityMatcher(dict);
    const hits = m("贵州茅台的资金流向偏弱");
    expect(hits).toHaveLength(1);
    expect(hits[0].key).toBe("stock_flow");
    // 单独出现时仍是市场资金面（既有 NAV_ALIASES 行为不变）
    expect(m("今天资金流向偏弱")[0]).toEqual({
      type: "nav",
      text: "资金流向",
      name: "资金流向",
      key: "market_fund",
      url: "/market?tab=fund",
    });
  });

  it("无页签词 / 间隔过宽 → 仍落工作台首页（不硬连）", () => {
    const m = createEntityMatcher(dict);
    for (const t of ["贵州茅台今天表现如何", "贵州茅台今日分时走弱", "贵州茅台和平安银行的分时"]) {
      const stock = m(t).find((h) => h.name === "贵州茅台");
      expect(stock, `「${t}」未识别出贵州茅台`).toBeDefined();
      expect(stock!.key, `「${t}」不该被组合命中`).toBeUndefined();
      expect(stock!.url, `「${t}」不该带深链`).toBeUndefined();
    }
  });

  it("页签词单独出现不误跳（页签词不在主扫描候选里）", () => {
    const m = createEntityMatcher(dict);
    expect(m("今天分时怎么样")).toEqual([]);
    expect(m("盘口没什么挂单")).toEqual([]);
  });

  it("题材+页签词不组合（题材只有一个落点）", () => {
    const m = createEntityMatcher(dict);
    expect(m("存储芯片的分时")).toEqual([{ type: "theme", text: "存储芯片", name: "存储芯片" }]);
  });

  it("字典外数字 + 页签词不产跳转（防金额/手数被当个股）", () => {
    const m = createEntityMatcher(dict);
    expect(m("123456 分时")).toEqual([]);
  });

  it("组合产出的 URL 一律是站内 workbench 深链（过白名单守卫）", () => {
    const m = createEntityMatcher(dict);
    const hits = m("贵州茅台分时、平安银行盘口、600519 资讯");
    expect(hits.map((h) => h.key)).toEqual(["stock_minute", "stock_book", "stock_info"]);
    for (const h of hits) {
      expect(h.url!.startsWith("/workbench?symbol=")).toBe(true);
      expect(h.type).toBe("stock");
    }
  });
});
