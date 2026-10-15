/** Presentation only. Never used for ranking, direction, eligibility or stored evidence. */
export function readerText(value: string): string {
  return value.replace(/空仓闸门/g, "暂停新增买入条件")
    .replace(/一票否决/g, "不满足入选条件")
    .replace(/联动确定性/g, "题材关联强弱")
    .replace(/可参与性/g, "参与条件")
    .replace(/辨识度/g, "市场关注程度")
    .replace(/反证/g, "不支持这一判断的记录")
    .replace(/源钟/g, "数据来源时间")
    .replace(/降级/g, "资料不完整");
}

/** Explicitly an excerpt, not a generated explanation. The original remains in evidence. */
export function readerExcerpt(value: string | null | undefined, limit = 64): string {
  const text = readerText(value?.trim().replace(/\s+/g, " ") || "");
  return text.length <= limit ? text : `${text.slice(0, limit)}…`;
}

/** Short caution prompts, not claims that a transaction has or has not completed. */
export function readerCautions(values: readonly string[]): string[] {
  const text = values.join("\n");
  return [
    /否认|澄清|终止|撤销|撤回|取消/.test(text) ? "留意澄清或撤回说明" : "",
    /尚未|尚需|仍需|待审|待批|拟|预案|意向|传闻|不确定|预计|可能|审批|审议|需经|如果/.test(text) ? "仍有待确认事项" : "",
    /不代表|不构成|不能|不保证|未确认|未证实|没有|不改|无直接|仅供|仅为|未贡献|占比|利润|营收/.test(text) ? "注意适用范围与限制" : "",
  ].filter(Boolean);
}
