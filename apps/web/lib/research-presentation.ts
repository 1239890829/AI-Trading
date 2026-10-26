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

/** Leading source text through a complete sentence; no title inference or clipped qualifier. */
export function researchExcerpt(raw: string, limit = 170): string {
  const clean = researchPlainText(raw).replace(/^\s*(?:[-*]|\d+[.)、])\s+/, "");
  const sentence = clean.match(/^[\s\S]*?[。！？](?:[”’」』])?/)?.[0]
    ?? clean.match(/^[\s\S]*?[.!?](?:\s|$)/)?.[0]?.trim() ?? clean;
  if (!sentence) return "原报告没有可摘录正文。";
  return sentence.length <= limit ? sentence : "这段原答较长，请在原文中核对完整表述。";
}

/** Display-only source paragraphs. Each item keeps its complete local qualifications. */
export function researchPassages(raw: string): {title: string; text: string; index: number}[] {
  const blocks = researchPlainText(raw).split(/\n\s*\n/).map(text => text.trim()).filter(Boolean);
  return blocks.map((text, index) => {
    const firstLine = text.split("\n")[0];
    // The title is an index label, never a replacement for the complete passage.
    const title = firstLine.length <= 26 ? firstLine : `${firstLine.slice(0, 26)}…`;
    return {title, text, index};
  });
}

/** A complete opening paragraph, not a synthesized verdict or a clipped first sentence. */
export function researchLead(raw: string, limit = 220): string {
  const passages = researchPassages(raw);
  if (!passages.length) return "原报告没有可摘录正文。";
  const first = passages[0].text;
  // A short heading and its following paragraph retain their original relationship.
  const lead = !/[。！？.!?]/.test(first) && passages[1] ? `${first}\n\n${passages[1].text}` : first;
  return lead.length <= limit ? lead : "本段条件较多，请点“原文论据”逐条核对。";
}

/** Conservative source locations. Uncertain punctuation stays in its original sentence. */
export function researchSentences(raw: string): string[] {
  // Parser tokens, not display labels or a backend enum. Unknown characters
  // have no closing token; Map.get preserves that fact in the type contract.
  const closers = new Map<string,string>([["[","]"],["(",")"],["（","）"],["【","】"],["「","」"],["『","』"],["“","”"],["‘","’"]]);
  const stack: string[]=[];
  const sentences: string[]=[];
  let start=0;
  for(let index=0;index<raw.length;index++) {
    const char=raw[index];
    if(char==='"') {if(stack.at(-1)==='"')stack.pop();else stack.push('"');continue;}
    const closer=closers.get(char);
    if(closer!==undefined) {stack.push(closer);continue;}
    if(stack.at(-1)===char) {stack.pop();continue;}
    if(stack.length)continue;
    let boundary=/[。！？]/.test(char);
    if(/[.!?]/.test(char)) {
      const prefix=raw.slice(0,index+1), suffix=raw.slice(index+1);
      const uncertainDot=char==='.' && (/[\d.]/.test(raw[index-1]??'') && /[\d.]/.test(raw[index+1]??'')
        || raw[index-1]==='.' || /\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|Inc|Ltd|No|Fig|pp|vol)\.$/i.test(prefix)
        || /(?:\b[A-Za-z]\.){1,}$/.test(prefix));
      boundary=!uncertainDot && (suffix.trim()==='' || /^\s+[A-Z]/.test(suffix));
    }
    if(boundary) {sentences.push(raw.slice(start,index+1));start=index+1;}
  }
  if(start<raw.length) {
    const tail=raw.slice(start);
    if(tail.trim())sentences.push(tail);
    else if(sentences.length)sentences[sentences.length-1]+=tail;
  }
  // A malformed group can hide a later qualifier. Fall back to the whole passage.
  return stack.length ? [raw] : sentences.filter(sentence=>sentence.trim());
}

/** Only a complete, rectangular Markdown source table can become a row reader. */
export function researchTable(raw: string): {headers: string[]; rows: string[][]} | null {
  const lines=raw.trim().split("\n").map(line=>line.trim());
  if(lines.length<3 || lines.some(line=>!line.startsWith("|") || !line.endsWith("|") || line.includes("\\|")))return null;
  const cells=lines.map(line=>line.slice(1,-1).split("|").map(cell=>cell.trim()));
  const headers=cells[0];
  if(headers.length<2 || headers.some(header=>!header) || cells.some(row=>row.length!==headers.length)
    || !cells[1].every(cell=>/^:?-{3,}:?$/.test(cell)))return null;
  return {headers,rows:cells.slice(2)};
}

export function originalRating(raw: string): {label: string; original: string} | null {
  const field = researchField(raw, "Rating");
  if (!field) return null;
  const value = field.split(/\n/)[0].trim();
  const labels: Record<string, string> = {underweight: "谨慎研究", overweight: "偏积极研究", neutral: "中性研究", hold: "维持观察", buy: "偏积极研究", sell: "谨慎研究"};
  return value.length <= 40 ? {label: labels[value.toLowerCase()] ?? "查看原评级", original: value} : null;
}
