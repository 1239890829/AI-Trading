import {expect,it} from "vitest";
import {readingClues,readingExcerpt} from "./reading-clues";

it("extracts exact dates and decimal amounts without counting date fragments as amounts",()=>{
  const text="公司于2026-10-13 09:21:03发布公告。金额不超过 12.36 亿元，涨幅为+2.80%。";
  const clues=readingClues(text);
  expect(clues.filter(item=>item.kind==="date").map(item=>item.value)).toEqual(["2026-10-13 09:21:03"]);
  expect(clues.filter(item=>item.kind==="number").map(item=>item.value)).toEqual(["12.36 亿元","+2.80%"]);
  clues.forEach(item=>expect(text.slice(item.offset,item.offset+item.value.length)).toBe(item.value));
});
it("preserves original negated and conditional sentences, not a generated positive interpretation",()=>{
  const text="公司未生产粮食。\r\n原材料涨价可能挤压利润，并不等于公司受益。尚需股东大会批准。";
  const qualification=readingClues(text).filter(item=>item.kind==="qualification");
  expect([...new Set(qualification.map(item=>item.sourceText))]).toEqual(["公司未生产粮食。","原材料涨价可能挤压利润，并不等于公司受益。","尚需股东大会批准。"]);
  expect(qualification.map(item=>item.value)).toEqual(["未","可能","并不","尚需"]);
});
it("keeps an unpunctuated sentence whole, retains repeated source conditions and never guesses absent clues",()=>{
  const text="仅为意向金额不超过12.36亿元".repeat(30);
  expect(readingClues(text).filter(item=>item.kind==="qualification")[0].sourceText).toBe(text);
  expect(readingClues("题材关联来源未知。").filter(item=>item.kind==="date")).toEqual([]);
  expect(readingExcerpt("  原文中没有核实结论。  ",8)).toBe("原文中没有核实结…");
});
