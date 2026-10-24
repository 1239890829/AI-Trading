import {describe, expect, it} from "vitest";
import {originalRating, researchExcerpt, researchField, researchPlainText} from "./research-presentation";

describe("saved research excerpts", () => {
  it("extracts declared fields and removes peer metadata from excerpts without changing the source", () => {
    const raw = '**Rating**: Underweight\n\n**Executive Summary**: 收入有增长，但盈利尚未确认。下一步核回款。\n\n**Investment Thesis**: 另一个原字段。<PEER_REFS>{"citations":[]}</PEER_REFS>';
    expect(originalRating(raw)).toEqual({label: "谨慎研究", original: "Underweight"});
    expect(researchField(raw, "Executive Summary")).toBe("收入有增长，但盈利尚未确认。下一步核回款。");
    expect(researchExcerpt(researchField(raw, "Executive Summary")!)).toBe("收入有增长，但盈利尚未确认。");
    expect(researchPlainText(raw)).not.toMatch(/PEER_REFS|citations|\*\*/);
    expect(raw).toContain('<PEER_REFS>{"citations":[]}</PEER_REFS>');
  });
  it("does not clip a long qualification or manufacture a new summary", () => {
    const raw = "项目有线索".repeat(40) + "，但未形成订单。";
    expect(researchExcerpt(raw)).toBe("这段原答较长，请在原文中核对完整表述。");
    expect(originalRating("这里没有声明研究评级。")).toBeNull();
    expect(researchField("普通旧报告", "Executive Summary")).toBeNull();
    expect(researchExcerpt("冻结比例基期未经审计，当前现金流未知")).toBe("冻结比例基期未经审计，当前现金流未知");
  });
  it("keeps source labels without executing links or treating an incomplete peer block as visible prose", () => {
    expect(researchPlainText("[半年报](https://example.com/report)原句。<PEER_REFS>{")).toBe("半年报原句。");
    expect(researchExcerpt("\n\n# 标题\n原句。" )).toBe("标题\n原句。");
  });
  it("reads the complete source sentence after the saved fundamentals title without changing the original", () => {
    const title = "000538（云南白药集团股份有限公司）基本面研究摘要";
    const sentence = "截至北京时间2026年10月24日00:29:52.143433，冻结材料支持的主要判断是：000538具有明确的品牌中药、日化核心品类及医药商业业务基础，公司披露部分数字化营销建设已经落地；但财务摘要显示，2026年上半年收入和利润增长放缓，毛利率同比下降。";
    const raw = `${title}\n\n${sentence}现有证据尚不能证明后续业绩恢复。`;
    const original = raw;
    expect(researchExcerpt(raw, 210)).toBe(`${title}\n\n${sentence}`);
    expect(raw).toBe(original);
  });
  it.each([
    ["基本面研究摘要\n\n现金流尚未确认。", "基本面研究摘要\n\n现金流尚未确认。"],
    ["## 基本面研究摘要\n\n现金流尚未确认。", "基本面研究摘要\n\n现金流尚未确认。"],
    ["# Report\n## Executive Summary\nRevenue grew, but cash flow remains unverified.", "Report\nExecutive Summary\nRevenue grew, but cash flow remains unverified."],
    ["# Report\n\n## Executive Summary\n\nRevenue grew, but cash flow remains unverified. Later text.", "Report\nExecutive Summary\n\nRevenue grew, but cash flow remains unverified."],
    ["基本面研究摘要\n---\n\n现金流尚未确认。", "基本面研究摘要\n---\n\n现金流尚未确认。"],
    ["**000538（云南白药集团股份有限公司）基本面研究摘要**\n\n现金流尚未确认。", "000538（云南白药集团股份有限公司）基本面研究摘要\n\n现金流尚未确认。"],
    ["尚待复核\n\n仅依据冻结材料\n\n收入增长，但利润未经核实。后一句。", "尚待复核\n\n仅依据冻结材料\n\n收入增长，但利润未经核实。"],
  ])("preserves leading short paragraphs in order through the first complete source sentence: %s", (raw, expected) => {
    expect(researchExcerpt(raw, 210)).toBe(expected);
  });
  it.each(["000538（云南白药集团股份有限公司）基本面研究摘要", "# Executive Summary", "研究摘要\n==="])("keeps the original short text when no body follows: %s", raw => {
    expect(researchExcerpt(raw, 210)).toBe(researchPlainText(raw));
  });
  it.each(["", " \n\t", "<PEER_REFS>{}"])("does not invent a body for empty source text: %s", raw => {
    expect(researchExcerpt(raw, 210)).toBe("原报告没有可摘录正文。");
  });
  it.each(["Research summary remains unverified.", "Short research report", "基本面研究摘要尚未确认。", "现金流未知", "收入增长，但利润并未恢复。"])("preserves genuine short source text: %s", raw => {
    expect(researchExcerpt(raw, 210)).toBe(raw);
  });
  it.each([
    ["## 尚未核实利润改善，不能作为买入依据\n\n收入增长。", "尚未核实利润改善，不能作为买入依据\n\n收入增长。"],
    ["利润改善尚未核实\n---\n\n收入增长。", "利润改善尚未核实\n---\n\n收入增长。"],
    ["## 盈利改善\n\n收入增长，但现金流仍未经核实。后一句。", "盈利改善\n\n收入增长，但现金流仍未经核实。"],
    ["## Revenue increased\n\nCash flow remains unverified.", "Revenue increased\n\nCash flow remains unverified."],
    ["000538（尚未核实利润改善，不能作为买入依据）基本面研究摘要\n\n收入增长。", "000538（尚未核实利润改善，不能作为买入依据）基本面研究摘要\n\n收入增长。"],
  ])("retains an unknown or judgment-bearing heading together with its body: %s", (raw, expected) => {
    expect(researchExcerpt(raw, 210)).toBe(expected);
  });
  it("falls back instead of dropping a judgment-bearing heading to fit a later short sentence", () => {
    expect(researchExcerpt(`## ${"原限制".repeat(75)}\n\n收入增长。`, 210)).toBe("这段原答较长，请在原文中核对完整表述。");
    expect(researchExcerpt("# 尚未核实，不能作为买入依据。\n\n收入增长。", 210)).toBe("尚未核实，不能作为买入依据。");
  });
  it("keeps the role excerpt limit without clipping its qualifying sentence or choosing a later one", () => {
    const prefix = "基本面研究摘要\n\n";
    const qualification = "，但现金流仍未经核实。";
    const withinLimit = prefix + "原".repeat(210 - prefix.length - qualification.length) + qualification;
    expect(withinLimit.length).toBe(210);
    expect(researchExcerpt("## " + withinLimit, 210)).toBe(withinLimit);
    expect(researchExcerpt("## " + withinLimit.replace(prefix, prefix + "原") + "另一个短句。", 210)).toBe("这段原答较长，请在原文中核对完整表述。");
  });
});
