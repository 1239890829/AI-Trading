/** Read-only excerpts. These functions do not grade, summarize or regenerate research. */
export function researchPlainText(raw: string): string {
  return raw.replace(/<PEER_REFS>[\s\S]*?(?:<\/PEER_REFS>|$)/g, "")
    .replace(/\[([^\]]+)\]\(https?:\/\/[^)]+\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "").replace(/\*\*/g, "").replace(/\r/g, "").trim();
}

export function researchField(raw: string, field: string): string | null {
  const label = field.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`(?:^|\\n)\\s*(?:\\*\\*)?${label}(?:\\*\\*)?\\s*[:：]\\s*([\\s\\S]*?)(?=\\n\\s*(?:\\*\\*[^\\n]+\\*\\*\\s*[:：]|#{1,6}\\s)|$)`, "i").exec(raw);
  return match ? researchPlainText(match[1]) : null;
}

/** A complete first sentence, never a generated paraphrase or a clipped qualifier. */
export function researchExcerpt(raw: string, limit = 170): string {
  const clean = researchPlainText(raw).replace(/^\s*(?:[-*]|\d+[.)、])\s+/, "");
  const paragraph = clean.split(/\n\s*\n/).find(value => value.trim())?.trim() ?? "";
  const sentence = paragraph.match(/^[\s\S]*?[。！？](?:[”’」』])?/)?.[0]
    ?? paragraph.match(/^[\s\S]*?[.!?](?:\s|$)/)?.[0]?.trim() ?? paragraph;
  if (!sentence) return "原报告没有可摘录正文。";
  return sentence.length <= limit ? sentence : "这段原答较长，请在原文中核对完整表述。";
}

export function originalRating(raw: string): {label: string; original: string} | null {
  const field = researchField(raw, "Rating");
  if (!field) return null;
  const value = field.split(/\n/)[0].trim();
  const labels: Record<string, string> = {underweight: "谨慎研究", overweight: "偏积极研究", neutral: "中性研究", hold: "维持观察", buy: "偏积极研究", sell: "谨慎研究"};
  return value.length <= 40 ? {label: labels[value.toLowerCase()] ?? "查看原评级", original: value} : null;
}
