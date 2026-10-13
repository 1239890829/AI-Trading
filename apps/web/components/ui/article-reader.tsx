"use client";

import {useState, type ReactNode} from "react";
import type {ArticleBlock} from "@/lib/api";
import {readingClues} from "@/lib/reading-clues";
import {useExitPresence} from "@/hooks/use-exit-presence";
import {SourceClueCards, SourceExcerpt, SourceOutline, useExplanationDetail} from "./explanation-map";
import styles from "./explanation-map.module.css";

type ArticleReaderProps = {blocks: ArticleBlock[]; identity: string; title?: string; alreadyShownExcerpt?: string; renderBlock: (block: ArticleBlock, index: number) => ReactNode};

/** Preserve the incumbent classification, including the strict >50% rule over all body rows. */
export function isSourceNumericColumn(block: Extract<ArticleBlock, {type: "table"}>, column: number) {
  const body = block.header ? block.rows.slice(1) : block.rows;
  const cells = body.map(row => row[column]).filter(Boolean);
  if (!cells.length) return false;
  return cells.filter(cell => /^[-+]?[\d,.%]+$/.test(cell)).length / cells.length > 0.5;
}

function withoutProvidedTitle(text: string, title?: string) {
  const value = text.trim();
  const actualTitle = title?.trim();
  if (!actualTitle) return value;
  const bracketTitle = `【${actualTitle}】`;
  if (value.startsWith(bracketTitle)) return value.slice(bracketTitle.length).trim();
  if (value.startsWith(actualTitle) && (value.length === actualTitle.length || /^[\s:：]/.test(value.slice(actualTitle.length)))) return value.slice(actualTitle.length).replace(/^\s*[:：]?\s*/, "");
  return value;
}

/** Source prose, conditions and actual media are readable immediately, without a graphic menu. */
export function ArticleReader(props: ArticleReaderProps) {
  const detail = useExplanationDetail();
  if (detail) return <div className="min-w-0 space-y-3">{props.blocks.map((block, index) => <div key={index}>{props.renderBlock(block, index)}</div>)}</div>;
  return <Reader key={props.identity} {...props}/>;
}

function InlineSourceTable({block, ordinal, original}: {block: Extract<ArticleBlock, {type: "table"}>; ordinal: number; original: ReactNode}) {
  const [open, setOpen] = useState(false);
  const presence = useExitPresence(open ? true : null);
  const header = block.header ? block.rows[0] : null;
  const rows = block.header ? block.rows.slice(1) : block.rows;
  const columns = Math.max(0, ...block.rows.map(row => row.length));
  if (!block.rows.length) return null;
  const preview = <div className={styles.tableScroll}><table className={styles.sourceTable} style={{minWidth: columns > 3 ? columns * 64 : undefined}}>
    {header && <thead><tr>{Array.from({length:columns},(_,column) => <th key={column} className={isSourceNumericColumn(block,column) ? "whitespace-nowrap text-right" : "whitespace-normal text-left [overflow-wrap:anywhere]"}>{header[column] ?? ""}</th>)}</tr></thead>}
    <tbody>{rows.slice(0, 2).map((row, index) => <tr key={index}>{Array.from({length:columns},(_,column) => <td key={column} className={isSourceNumericColumn(block,column) ? "whitespace-nowrap text-right font-mono tabular-nums" : "whitespace-normal text-left [overflow-wrap:anywhere]"}>{row[column] ?? ""}</td>)}</tr>)}</tbody>
  </table></div>;
  return <section className={styles.inlineTable} aria-label={`原文表格 ${ordinal}`}>
    <div className={styles.heading}><h4>原文表格 {ordinal}</h4>{rows.length > 2 && <button type="button" className={styles.originalToggle} data-action="quiet" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? "收起" : "展开"}表格 {ordinal}</button>}</div>
    {block.truncated_rows && <p className={styles.orderNote}>表格过长已截断，完整内容见原文</p>}
    <div className={styles.tableSurface}>{!open && preview}{presence.value && <div className={styles.fullTable} data-exiting={!presence.active || undefined} aria-hidden={!presence.active || undefined} inert={!presence.active}>{original}</div>}</div>
    {!open && rows.length > 2 && <p className={styles.orderNote}>当前列出前 2 行，另有 {rows.length - 2} 行已获取数据。</p>}
  </section>;
}

function Reader({blocks, identity, title, alreadyShownExcerpt, renderBlock}: ArticleReaderProps) {
  const [full, setFull] = useState(false);
  const render = (indices: number[]) => <div className="space-y-3">{indices.map(index => <div key={index}>{renderBlock(blocks[index], index)}</div>)}</div>;
  const paragraphs = blocks.flatMap(block => block.type === "p" && block.text.trim() ? [block.text] : []);
  // Only an explicit exact title match is treated as the supplied headline.
  const bodyParagraphs = title && paragraphs[0]?.trim() === title.trim() ? paragraphs.slice(1) : paragraphs;
  const text = bodyParagraphs.join("\n\n");
  const alreadyShown = alreadyShownExcerpt?.trim();
  const suppressExcerpt = Boolean(alreadyShown && alreadyShown.length <= 140 && withoutProvidedTitle(alreadyShown, title) === withoutProvidedTitle(bodyParagraphs[0] ?? "", title));
  const clues = readingClues(text);
  const hasClues = clues.some(clue => clue.kind !== "date");
  const media = blocks.flatMap((block, index) => block.type === "p" ? [] : [{block, index}]);
  if (!blocks.length) return <p className={styles.empty}>未提供正文材料，可到原文核对。</p>;
  let tableOrdinal = 0;
  return <div className={styles.article} onClick={event => event.stopPropagation()}>
    <div className={styles.articleToolbar}><span>按原文内容整理，未自动摘要、归因或核实。</span><button type="button" data-action="quiet" aria-pressed={full} onClick={() => setFull(value => !value)}>{full ? "返回要点阅读" : "完整原文"}</button></div>
    {full ? <section className={styles.fullText} aria-label="完整原文">{render(blocks.map((_, index) => index))}</section> : <>
      {text && !suppressExcerpt && <SourceExcerpt text={text}/>}
      {hasClues ? <SourceClueCards items={clues} identity={identity}/> : <SourceOutline paragraphs={bodyParagraphs}/>}
      {media.length > 0 && <div className={styles.articleMaterials}>{media.map(({block, index}) => block.type === "img" ? <section key={index} className={styles.inlineImage} aria-label="原文配图"><h4>原文配图</h4>{renderBlock(block, index)}</section> : <InlineSourceTable key={index} block={block} ordinal={++tableOrdinal} original={renderBlock(block, index)}/>)}</div>}
    </>}
  </div>;
}
