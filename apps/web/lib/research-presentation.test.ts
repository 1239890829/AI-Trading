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
});
