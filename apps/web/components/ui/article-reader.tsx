"use client";

import {useState, type ReactNode} from "react";
import type {ArticleBlock} from "@/lib/api";
import {ExplanationMap} from "./explanation-map";

/** Source blocks stay in their original order. Navigation is a reading aid, not an AI summary. */
export function ArticleReader(props: {blocks: ArticleBlock[]; identity: string; renderBlock: (block: ArticleBlock, index: number) => ReactNode}) {
  return <Reader key={props.identity} {...props}/>;
}

function Reader({blocks, identity, renderBlock}: {blocks: ArticleBlock[]; identity: string; renderBlock: (block: ArticleBlock, index: number) => ReactNode}) {
  const [full, setFull] = useState(false);
  const render = (start: number, end: number) => <div className="space-y-3">{blocks.slice(start, end).map((block, offset) => <div key={start + offset}>{renderBlock(block, start + offset)}</div>)}</div>;
  if (blocks.length < 4) return render(0, blocks.length);
  const size = Math.ceil(blocks.length / Math.min(6, Math.ceil(blocks.length / 3)));
  const groups = Array.from({length: Math.ceil(blocks.length / size)}, (_, index) => ({start: index * size, end: Math.min((index + 1) * size, blocks.length)}));
  return <div className="min-w-0 space-y-3" onClick={event => event.stopPropagation()}>
    <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-zinc-600 dark:text-zinc-400"><span>原文 {blocks.filter(block => block.type === "p").length} 段 · 配图 {blocks.filter(block => block.type === "img").length} 张 · 表格 {blocks.filter(block => block.type === "table").length} 份</span><button type="button" data-action="quiet" aria-pressed={full} onClick={() => setFull(value => !value)}>{full ? "分段阅读" : "完整原文"}</button></div>
    {full ? <section aria-label="完整原文">{render(0, blocks.length)}</section> : <ExplanationMap label="原文阅读路线" identity={identity} steps={groups.map(({start, end}, index) => {
      const group = blocks.slice(start, end);
      const firstText = group.find(block => block.type === "p");
      return {id: String(start), label: `第 ${index + 1} 部分`, icon: "source", summary: firstText?.type === "p" ? firstText.text.slice(0, 40) + (firstText.text.length > 40 ? "…" : "") : "原文配图与表格", content: render(start, end)};
    })}/>}
  </div>;
}
