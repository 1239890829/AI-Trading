import {expect, it} from "vitest";
import {limitReasonView, matchSourceReason} from "./limit-reason-view";
const item = (title: string, summary: string | null = null) => ({source_context: {title, summary, url: null, observation_id: 1, change_kind: "initial"}});

it("long source reasons keep a visible exact core and all complete conditional sentences", () => {
  const reason = `覆铜板涨价，${"供应链说明".repeat(50)}。拟收购欧康诺，交易尚需审批且未完成审计。该业务收入不足总收入1%，预计暂不贡献利润。`;
  const value = limitReasonView(reason);
  expect(value.core).toBe("覆铜板涨价");
  expect(value.excerpted).toBe(true);
  expect(value.conditions).toContain("拟收购欧康诺，交易尚需审批且未完成审计");
  expect(value.conditions).toContain("该业务收入不足总收入1%，预计暂不贡献利润");
});
it("precise topic or acquisition cross-evidence may be shown but generic/company-only news is excluded", () => {
  expect(matchSourceReason(item("厄尔尼诺导致粮食减产预期"), "农业+厄尔尼诺")).toContain("厄尔尼诺");
  expect(matchSourceReason(item("拟收购界面财联社"), "拟收购财联社+重大资产重组")).toBeTruthy();
  expect(matchSourceReason(item("控股股东拟减持公司股票"), "拟收购财联社+重大资产重组")).toBeNull();
  expect(matchSourceReason(item("拟收购另一家芯片公司"), "拟收购财联社+重大资产重组")).toBeNull();
  expect(matchSourceReason(item("公司发布投资公告"), "公告+投资+农业")).toBeNull();
  expect(matchSourceReason(item("公司新闻"), null)).toBeNull();
});
