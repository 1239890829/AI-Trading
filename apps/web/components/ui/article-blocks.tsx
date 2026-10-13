"use client";

import {useState} from "react";
import type {ArticleBlock} from "@/lib/api";
import type {EntityMatcher, EntityMatch} from "@/lib/entity-links";
import {RichText} from "@/components/assistant/rich-text";
import {ArticleReader} from "./article-reader";

/** 表格块：数据类文章的排行榜/涨跌榜按真表格渲染（横向可滚，斑马纹，小字号）。 */
function TableBlock({ block }: { block: Extract<ArticleBlock, { type: "table" }> }) {
  if (block.rows.length === 0) return null;
  const [head, ...body] = block.header ? [block.rows[0], ...block.rows.slice(1)] : [null, ...block.rows];
  // 数字列右对齐：表头或数据列里数字占比过半则判定
  const isNumericCol = (col: number) => {
    const cells = body.map((r) => r[col]).filter(Boolean);
    if (cells.length === 0) return false;
    const numCount = cells.filter((c) => /^[-+]?[\d,.%]+$/.test(c)).length;
    return numCount / cells.length > 0.5;
  };
  const colCount = Math.max(...block.rows.map((r) => r.length));
  return (
    <div className="max-w-full overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
      <table className="w-full border-collapse text-[11px] leading-5" style={{minWidth: colCount >= 4 ? colCount * 96 : undefined}}>
        {head && (
          <thead>
            <tr className="bg-zinc-50 dark:bg-zinc-800/60">
              {Array.from({ length: colCount }, (_, c) => (
                <th
                  key={c}
                  className={`border-b border-zinc-200 px-2 py-1.5 font-medium text-zinc-600 dark:border-zinc-700 dark:text-zinc-300 ${
                    isNumericCol(c) ? "whitespace-nowrap text-right" : "whitespace-normal text-left [overflow-wrap:anywhere]"
                  }`}
                >
                  {head[c] ?? ""}
                </th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {body.map((row, r) => (
            <tr key={r} className="odd:bg-zinc-50/50 dark:odd:bg-zinc-800/30">
              {Array.from({ length: colCount }, (_, c) => (
                <td
                  key={c}
                  className={`border-b border-zinc-100 px-2 py-1 text-zinc-700 last:border-0 dark:border-zinc-800/60 dark:text-zinc-300 ${
                    isNumericCol(c) ? "whitespace-nowrap text-right font-mono tabular-nums" : "whitespace-normal text-left [overflow-wrap:anywhere]"
                  }`}
                >
                  {row[c] ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {block.truncated_rows && <p className="px-2 py-1 text-[10px] text-amber-800 dark:text-amber-500">表格过长已截断，完整内容见原文</p>}
    </div>
  );
}

/** 图片块：内嵌配图；反盗链/加载失败时优雅降级为占位说明（不破版面）。 */
function ImageBlock({ src }: { src: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <p className="rounded-md bg-zinc-50 px-3 py-2 text-[11px] text-zinc-600 dark:text-zinc-400 dark:bg-zinc-800/50">
        [配图未能加载，可到原文查看]
      </p>
    );
  }
  return (
    <figure className="m-0">
      {/* referrerPolicy：东财 CDN 部分图片校验 Referer，no-referrer 提高加载成功率 */}
      {/* eslint-disable-next-line @next/next/no-img-element -- C 类显式豁免（P1-27）：
          next/image 走自家优化端点取图会丢 Referer；新闻配图来自任意第三方域名，
          逐个维护 remotePatterns 不现实。保留原生 <img> + referrerPolicy + onError 降级。 */}
      <img
        src={src}
        alt=""
        referrerPolicy="no-referrer"
        loading="lazy"
        onError={() => setFailed(true)}
        className="mx-auto max-h-96 w-auto max-w-full rounded-lg border border-zinc-200 dark:border-zinc-800"
      />
    </figure>
  );
}

export function ArticleBlocks({
  blocks,
  identity,
  matcher,
  onNavigate,
}: {
  blocks: ArticleBlock[];
  identity: string;
  matcher?: EntityMatcher | null;
  onNavigate?: (m: EntityMatch) => void;
}) {
  return (
    <ArticleReader blocks={blocks} identity={identity} renderBlock={(b, i) => {
        if (b.type === "p") {
          // 正文段落经实体匹配渲染：个股/题材命中 → 可点击跳转（识别失败降级纯文本）
          if (matcher && onNavigate) {
            return (
              <RichText
                key={i}
                text={b.text}
                matcher={matcher}
                onNavigate={onNavigate}
                className="space-y-3 text-[13px] leading-relaxed text-zinc-700 dark:text-zinc-300"
              />
            );
          }
          return (
            <p key={i} className="text-[13px] leading-relaxed text-zinc-700 dark:text-zinc-300">
              {b.text}
            </p>
          );
        }
        if (b.type === "table") return <TableBlock key={i} block={b} />;
        return <ImageBlock key={i} src={b.src} />;
      }}/>
  );
}
