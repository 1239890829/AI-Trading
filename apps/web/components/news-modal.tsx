"use client";

/** 资讯弹窗：全站资讯类内容的统一展示形态（新闻/快讯/公告三源共用）。

数据通道：后端 /api/news/content 抓取原文正文重新排版（域名白名单），
失败时降级为「摘要 + 原文链接」——摘要由调用方通过 digest 传入，
绝不显示"内容为空"的假成功。原文链接始终保留在弹窗底部（版权边界：
标注来源、保留跳转、不篡改正文）。 */
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { getNewsContent, type ArticleContent } from "@/lib/api";

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

export function NewsModal({ item, onClose }: { item: NewsModalItem | null; onClose: () => void }) {
  const [content, setContent] = useState<ArticleContent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // 渲染期 adjust-state（React 官方模式）：url 变了立即重置为加载态，防上一条内容残留
  const [fetchedUrl, setFetchedUrl] = useState<string | null>(null);
  if (item && item.url !== fetchedUrl) {
    setFetchedUrl(item.url);
    setContent(null);
    setError(null);
    setLoading(true);
  }

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
  const shownTime = content?.published ?? item.date ?? null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      onMouseDown={handleBackdrop}
      role="dialog"
      aria-modal="true"
      aria-label={shownTitle}
      data-testid="news-modal"
    >
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-900">
        {/* 头部：标题 + 元信息 + 关闭 */}
        <div className="border-b border-zinc-100 px-5 py-3.5 dark:border-zinc-800/80">
          <div className="flex items-start justify-between gap-3">
            <h2 className="text-[15px] font-semibold leading-snug text-zinc-900 dark:text-zinc-100">{shownTitle}</h2>
            <button
              onClick={onClose}
              className="shrink-0 rounded p-1 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
              aria-label="关闭"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-zinc-500">
            {item.kindLabel && <span className="rounded bg-zinc-100 px-1 py-px dark:bg-zinc-800">{item.kindLabel}</span>}
            {shownSource && <span>{shownSource}</span>}
            {shownTime && <span>{shownTime}</span>}
            {content?.cached && <span className="text-zinc-400">缓存</span>}
            {content?.truncated && <span className="text-amber-500">长文已截断，完整内容见原文</span>}
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
            <div className="space-y-3">
              {content.paragraphs.map((p, i) => (
                // 正文为纯文本段落（后端已去标签），index 作 key 足够
                <p key={i} className="text-[13px] leading-relaxed text-zinc-700 dark:text-zinc-300">
                  {p}
                </p>
              ))}
            </div>
          )}

          {!loading && !content && (
            <div data-testid="news-modal-degraded">
              <p className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
                正文获取失败{error ? `（${error}）` : ""}，以下为摘要，可在原文页查看完整内容。{" "}
                {/* 渲染守卫会跳过同 URL 重取，瞬态失败必须显式重试入口自愈 */}
                <button
                  onClick={() => setFetchedUrl(null)}
                  className="font-medium underline underline-offset-2 hover:opacity-80"
                  data-testid="news-modal-retry"
                >
                  重试
                </button>
              </p>
              {item.digest ? (
                <p className="mt-3 text-[13px] leading-relaxed text-zinc-700 dark:text-zinc-300">{item.digest}</p>
              ) : (
                <p className="mt-3 text-[13px] text-zinc-500">暂无摘要。</p>
              )}
            </div>
          )}
        </div>

        {/* 底部：原文链接（版权边界：始终保留跳转） */}
        <div className="flex items-center justify-between border-t border-zinc-100 px-5 py-2.5 text-[11px] dark:border-zinc-800/80">
          <span className="text-zinc-400">内容归原作者/来源媒体所有，本站仅作研究参考</span>
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            className="font-medium text-blue-600 transition-colors hover:text-blue-500 dark:text-blue-400"
          >
            查看原文 ↗
          </a>
        </div>
      </div>
    </div>,
    document.body,
  );
}
