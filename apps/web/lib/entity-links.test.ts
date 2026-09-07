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
});
