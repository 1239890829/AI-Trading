import type {AttributionEvidenceItem} from "@/lib/api";

/** Exact source excerpts, not a generated cause or a lossless replacement for the original. */
export function limitReasonView(reason: string) {
  const text = reason.trim();
  const clauses = text.split(/[，,。；;！？!?\r\n]+/).map(value => value.trim()).filter(Boolean);
  const first = clauses[0] || text;
  const core = first.length > 160 ? `${first.slice(0, 160)}…` : first;
  // Keep the full sentence, including the subject before a comma. A bare
  // "尚需审批" label would lose what exactly remains conditional.
  const sentences = text.split(/[。；;！？!?\r\n]+/).map(value => value.trim()).filter(Boolean);
  const conditions = [...new Set(sentences.filter(value => /不|未|无|尚|否认|澄清|拟|计划|预案|意向|预计|可能|需|须|待|审批|批准|审议|审计|完成|终止|撤销|取消|风险|收入|营收|利润|净利|毛利|占比|贡献|金额|规模|期限|有效期|截至|仅|暂/.test(value)))];
  return {core, conditions, excerpted: core !== text};
}

const normalize = (text: string) => text.toLowerCase().replace(/\s+/g, "");
const generic = new Set(["公告", "投资", "利好", "概念", "题材", "异动", "涨停", "重大资产重组", "国企改革", "农业", "传媒", "医药", "消费", "人工智能", "算力"]);
/** Conservative display cross-check only. It does not establish semantic accuracy or causality. */
export function matchSourceReason(item: Pick<AttributionEvidenceItem, "source_context">, reason: string | null | undefined): string | null {
  if (!reason?.trim()) return null;
  const source = normalize(reason);
  const evidence = normalize([item.source_context?.title, item.source_context?.summary].filter(Boolean).join("\n"));
  const labels = reason.split(/[+＋、；;\r\n]/).map(value => value.trim()).filter(value => value.length >= 2 && value.length <= 32 && !generic.has(value));
  for (const label of labels) if (evidence.includes(normalize(label))) return `来源原词“${label}”`;
  const acquisition = /(?:拟|计划)?(?:收购|并购)([\u4e00-\u9fffA-Za-z0-9]{2,16})/.exec(reason);
  if (acquisition && !evidence.includes(normalize(acquisition[1]))) return null;
  const actions: [RegExp, RegExp, string][] = [
    [/收购|并购/, /收购|并购/, "收购动作"],
    [/控制权|实控人变更|股权转让/, /控制权|实控人变更|股权转让|拟入主/, "控制权变更"],
    [/业绩预增|业绩增长|业绩大增|扭亏/, /业绩预增|净利.{0,20}增长|扭亏|预计.{0,12}盈利/, "业绩变化"],
    [/涨价|提价|价格上涨/, /涨价|提价|价格上涨/, "价格上涨线索"],
  ];
  for (const [sourceAction, eventAction, label] of actions) if (sourceAction.test(source) && eventAction.test(evidence)) return label;
  return null;
}
