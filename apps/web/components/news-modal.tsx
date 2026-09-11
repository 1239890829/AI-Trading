"use client";

/** 资讯弹窗：全站资讯类内容的统一展示形态（新闻/快讯/公告三源共用）。

数据通道：后端 /api/news/content 抓取原文正文重新排版（域名白名单），
失败时降级为「摘要 + 原文链接」——摘要由调用方通过 digest 传入，
绝不显示"内容为空"的假成功。原文链接始终保留在弹窗底部（版权边界：
标注来源、保留跳转、不篡改正文）。 */
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";

import { API_BASE, getNewsContent, type ArticleBlock, type ArticleContent } from "@/lib/api";
import { withFrom, workbenchUrlWithBack, themesUrl } from "@/lib/routing";
import { createEntityMatcher, type EntityDict, type EntityMatch, type EntityMatcher } from "@/lib/entity-links";
import { isAllowedNav } from "@/lib/nav-targets";
import { RichText } from "@/components/assistant/rich-text";
import { eventTimeText } from "@/lib/format";

export interface NewsModalItem {
  title: string;
  url: string;
  date?: string | null;
  source?: string | null;
  kindLabel?: string;
  digest?: string | null;
}

const SOURCE_LABEL: Record<string, string> = {
  eastmoney: "东方财富",
  ths: "同花顺",
  sina: "新浪财经",
  tencent: "腾讯财经",
};

function sourceText(source?: string | null): string | null {
  if (!source) return null;
  return SOURCE_LABEL[source] ?? source;
}

// ---- 实体词典（新闻正文个股/题材链接化，2026-09-07 用户需求）----
// 模块级缓存：词典与文章无关，全站共用一份；失败缓存 null（识别是增强层，
// 失败降级为纯文本，绝不阻塞正文渲染）。
let entityDictCache: EntityDict | null | undefined;

async function loadEntityDict(): Promise<EntityDict | null> {
  if (entityDictCache !== undefined) return entityDictCache;
  try {
    const r = await fetch(`${API_BASE}/api/assistant/entity-dict`);
    const j = await r.json();
    entityDictCache = (j?.data as EntityDict) ?? null;
  } catch {
    entityDictCache = null;
  }
  return entityDictCache;
}

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
    <div className="-mx-1 overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
      <table className="w-full min-w-[420px] border-collapse text-[11px] leading-5">
        {head && (
          <thead>
            <tr className="bg-zinc-50 dark:bg-zinc-800/60">
              {Array.from({ length: colCount }, (_, c) => (
                <th
                  key={c}
                  className={`whitespace-nowrap border-b border-zinc-200 px-2 py-1.5 font-medium text-zinc-600 dark:border-zinc-700 dark:text-zinc-300 ${
                    isNumericCol(c) ? "text-right" : "text-left"
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
                  className={`whitespace-nowrap border-b border-zinc-100 px-2 py-1 text-zinc-700 last:border-0 dark:border-zinc-800/60 dark:text-zinc-300 ${
                    isNumericCol(c) ? "text-right font-mono tabular-nums" : "text-left"
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

function ArticleBlocks({
  blocks,
  matcher,
  onNavigate,
}: {
  blocks: ArticleBlock[];
  matcher: EntityMatcher | null;
  onNavigate: (m: EntityMatch) => void;
}) {
  return (
    <div className="space-y-3">
      {blocks.map((b, i) => {
        if (b.type === "p") {
          // 正文段落经实体匹配渲染：个股/题材命中 → 可点击跳转（识别失败降级纯文本）
          if (matcher) {
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
      })}
    </div>
  );
}

export function NewsModal({ item, onClose }: { item: NewsModalItem | null; onClose: () => void }) {
  const router = useRouter();
  const [content, setContent] = useState<ArticleContent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // 实体识别器（新闻正文个股链接化）：词典懒加载一次，失败降级为 null → 纯文本
  const [matcher, setMatcher] = useState<EntityMatcher | null>(null);
  // 渲染期 adjust-state（React 官方模式）：url 变了立即重置为加载态，防上一条内容残留
  const [fetchedUrl, setFetchedUrl] = useState<string | null>(null);
  if (item && item.url !== fetchedUrl) {
    setFetchedUrl(item.url);
    setContent(null);
    setError(null);
    setLoading(true);
  }

  // 词典懒加载（模块级缓存后仅首篇触发网络请求）
  useEffect(() => {
    if (!item || matcher) return;
    let alive = true;
    loadEntityDict().then((d) => {
      // 必须包一层：createEntityMatcher 返回函数，直接传会被 React 当 updater 调用
      // （fn(prev) → IDLE 返回 []），matcher state 变成数组导致弹窗崩溃（P0）。
      if (alive) setMatcher(() => createEntityMatcher(d));
    });
    return () => {
      alive = false;
    };
  }, [item, matcher]);

  // 实体点击导航：深链优先（功能入口 / 个股+页签）；个股 → 工作台详情（带 from）；
  // 题材 → 题材梯队。来源参数走 withFrom，深链与首页两种形态只此一种拼法。
  const onNavigate = useCallback(
    (m: EntityMatch) => {
      if (m.url && isAllowedNav(m.url)) {
        router.push(withFrom(m.url));
      } else if (m.type === "stock" && m.code) {
        router.push(workbenchUrlWithBack(m.code));
      } else if (m.type === "theme") {
        router.push(themesUrl(m.name));
      } else if (m.type === "nav" && m.url) {
        router.push(m.url);
      }
    },
    [router],
  );

  // 拉取正文；失败记 error 走降级（setState 仅出现在异步回调，不经 effect 同步触发）
  useEffect(() => {
    if (!item || !loading) return;
    let alive = true;
    getNewsContent(item.url)
      .then((c) => {
        if (alive) setContent(c);
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : "正文抓取失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- loading 作触发闸门，item.url 由渲染期守卫保证一致
  }, [item?.url, loading]);

  // Esc 关闭
  useEffect(() => {
    if (!item) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [item, onClose]);

  const handleBackdrop = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  if (!item) return null;

  const shownTitle = content?.title ?? item.title;
  const shownSource = content?.source_label ?? sourceText(item.source);
  // 2026-09-09：此前优先取 content.published（抓正文接口的源站时间，与列表的
  // EventStore.published_at 不同源 → 时间对不上）。改为**优先列表同字段**
  // item.date，两者都经 eventTimeText 统一格式。
  const shownTime = eventTimeText(item.date ?? content?.published ?? null);

  return createPortal(
    <div
      className="anim-backdrop-in fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      onMouseDown={handleBackdrop}
      role="dialog"
      aria-modal="true"
      aria-label={shownTitle}
      data-testid="news-modal"
    >
      <div className="anim-scale-in flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-900">
        {/* 头部：标题 + 元信息 + 关闭 */}
        <div className="border-b border-zinc-100 px-5 py-3.5 dark:border-zinc-800/80">
          <div className="flex items-start justify-between gap-3">
            <h2 className="text-[15px] font-semibold leading-snug text-zinc-900 dark:text-zinc-100">{shownTitle}</h2>
            <button
              onClick={onClose}
              className="shrink-0 rounded p-1 text-zinc-600 dark:text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
              aria-label="关闭"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-zinc-600 dark:text-zinc-400">
            {item.kindLabel && <span className="rounded bg-zinc-100 px-1 py-px dark:bg-zinc-800">{item.kindLabel}</span>}
            {shownSource && <span>{shownSource}</span>}
            {shownTime && <span>{shownTime}</span>}
            {content?.cached && <span className="text-zinc-600 dark:text-zinc-400">缓存</span>}
            {content?.truncated && <span className="text-amber-800 dark:text-amber-500">长文已截断，完整内容见原文</span>}
          </div>
        </div>

        {/* 正文 / 降级 / 加载 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4" data-testid="news-modal-body">
          {loading && (
            <div className="space-y-2.5" data-testid="news-modal-loading">
              {[92, 100, 96, 88, 60].map((w, i) => (
                <div key={i} className="h-3.5 animate-pulse rounded bg-zinc-100 dark:bg-zinc-800" style={{ width: `${w}%` }} />
              ))}
            </div>
          )}

          {!loading && content && (
            // blocks 为空（旧缓存/公告旧响应）时回退 paragraphs，绝不满屏空白
            content.blocks?.length > 0 ? (
              <ArticleBlocks blocks={content.blocks} matcher={matcher} onNavigate={onNavigate} />
            ) : (
              <div className="space-y-3">
                {content.paragraphs.map((p, i) =>
                  matcher ? (
                    <RichText
                      key={i}
                      text={p}
                      matcher={matcher}
                      onNavigate={onNavigate}
                      className="text-[13px] leading-relaxed text-zinc-700 dark:text-zinc-300"
                    />
                  ) : (
                    <p key={i} className="text-[13px] leading-relaxed text-zinc-700 dark:text-zinc-300">
                      {p}
                    </p>
                  ),
                )}
              </div>
            )
          )}

          {!loading && !content && (
            <div data-testid="news-modal-degraded">
              <p className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-400">
                正文获取失败{error ? `（${error}）` : ""}，以下为摘要，可在原文页查看完整内容。{" "}
                {/* 渲染守卫会跳过同 URL 重取，瞬态失败必须显式重试入口自愈 */}
                <button
                  onClick={() => setFetchedUrl(null)}
                  className="font-medium text-sky-700 transition-colors hover:text-sky-700 dark:text-sky-400 dark:hover:text-sky-300"
                  data-testid="news-modal-retry"
                >
                  重试
                </button>
              </p>
              {item.digest ? (
                <p className="mt-3 text-[13px] leading-relaxed text-zinc-700 dark:text-zinc-300">{item.digest}</p>
              ) : (
                <p className="mt-3 text-[13px] text-zinc-600 dark:text-zinc-400">暂无摘要。</p>
              )}
            </div>
          )}
        </div>

        {/* 底部：原文链接（版权边界：始终保留跳转） */}
        <div className="flex items-center justify-between border-t border-zinc-100 px-5 py-2.5 text-[11px] dark:border-zinc-800/80">
          <span className="text-zinc-600 dark:text-zinc-400">内容归原作者/来源媒体所有，本站仅作研究参考</span>
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            className="font-medium text-blue-700 transition-colors hover:text-blue-500 dark:text-blue-400"
          >
            查看原文 ↗
          </a>
        </div>
      </div>
    </div>,
    document.body,
  );
}
