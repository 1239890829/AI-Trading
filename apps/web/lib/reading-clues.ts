export type ReadingClue = {
  kind: "date" | "number" | "qualification";
  value: string;
  sourceText: string;
  sourceOffset: number;
  offset: number;
};

/** Lexical pointers into supplied text. These are neither facts nor a semantic summary. */
export function readingClues(text: string): ReadingClue[] {
  const sentences: {text: string; offset: number}[] = [];
  let start = 0;
  const ends = /[。！？!?]+[”’」』）)\]"]*|\r?\n/g;
  for (const match of text.matchAll(ends)) {
    const end = match.index! + match[0].length;
    if (text.slice(start, end).trim()) sentences.push({text: text.slice(start, end), offset: start});
    start = end;
  }
  if (start < text.length && text.slice(start).trim()) sentences.push({text: text.slice(start), offset: start});

  const clues: ReadingClue[] = [];
  const dates = /(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2})?)?|(?:\d{4}年)?\d{1,2}月\d{1,2}日|\d{1,2}:\d{2}(?::\d{2})?)/g;
  // Keep decimals and units exactly as written. Date substrings are not counted twice.
  const numbers = /[-+]?\d+(?:[,.]\d+)*(?:\s*(?:个百分点|亿元|万元|亿股|万股|万片|万台|万吨|万亿|亿|万|元|股|吨|片|台|%|％))?/g;
  const qualifications = /尚未|尚需|未完成|未经|未获|未能|未提供|不超过|不低于|不突破|不得|不代表|不构成|不等于|不能|没有|并不|仅为|仅签|仅提出|仅限|可能|预计|最高|上限|及以上|按规定|并完成|需达到|必须|如果|若(?:非|未|需|要)|待审|需(?:经|要|审|通过)|审批|未|不/g;

  for (const sentence of sentences) {
    const dateRanges: {start: number; end: number}[] = [];
    for (const match of sentence.text.matchAll(dates)) {
      dateRanges.push({start: match.index!, end: match.index! + match[0].length});
      clues.push({kind: "date", value: match[0], sourceText: sentence.text, sourceOffset: sentence.offset, offset: sentence.offset + match.index!});
    }
    for (const match of sentence.text.matchAll(numbers)) {
      if (dateRanges.some(range => match.index! >= range.start && match.index! < range.end)) continue;
      clues.push({kind: "number", value: match[0], sourceText: sentence.text, sourceOffset: sentence.offset, offset: sentence.offset + match.index!});
    }
    for (const qualifier of sentence.text.matchAll(qualifications)) clues.push({kind: "qualification", value: qualifier[0], sourceText: sentence.text, sourceOffset: sentence.offset, offset: sentence.offset + qualifier.index!});
  }
  return clues.sort((a, b) => a.offset - b.offset || a.kind.localeCompare(b.kind));
}

/** Select the untouched original clause containing the cue, keeping its limits and units. */
export function readingClueContext(clue: ReadingClue): string {
  const position = clue.offset - clue.sourceOffset;
  let start = 0;
  let end = clue.sourceText.length;
  for (const separator of clue.sourceText.matchAll(/[,，;；。!?！？\r\n]/g)) {
    if (separator.index! < position) start = separator.index! + separator[0].length;
    else if (separator.index! >= position + clue.value.length) {end = separator.index! + separator[0].length; break;}
  }
  return clue.sourceText.slice(start, end).trim();
}

/** A visible excerpt is not a summary. The exact complete sentence remains at its node. */
export function readingExcerpt(text: string, limit = 54): string {
  const value = text.trim();
  return value.slice(0, limit) + (value.length > limit ? "…" : "");
}

/** Pick an untouched first source sentence. Explicit Markdown headings are not body prose. */
export function firstSourceSentence(text: string): string {
  const first = [...text.matchAll(/[^\r\n]+/g)].find(match => match[0].trim() && !/^\s*#{1,6}\s+/.test(match[0]));
  if (!first) return "";
  const body = text.slice(first.index).trimStart();
  const end = body.match(/[。！？!?]+[”’」』）)\]"]*/);
  return (end ? body.slice(0, end.index! + end[0].length) : body).trim();
}
