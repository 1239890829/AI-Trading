"use client";

import { inspectionRequestForHref, useInspection } from "@/components/inspection/inspection-context";

/** 资讯弹窗：全站资讯类内容的统一展示形态（新闻/快讯/公告三源共用）。

数据通道：后端 /api/news/content 抓取原文正文重新排版（域名白名单），
失败时降级为「摘要 + 原文链接」——摘要由调用方通过 digest 传入，
绝不显示"内容为空"的假成功。原文链接始终保留在弹窗底部（版权边界：
标注来源、保留跳转、不篡改正文）。 */
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { HugeiconsIcon } from "@hugeicons/react";
import ArrowUpRight01Icon from "@hugeicons/core-free-icons/ArrowUpRight01Icon";

import { API_BASE, getNewsContent, type ArticleContent, type EventInterpretationRef } from "@/lib/api";
import { StockAttributionPanel } from "@/components/detail/attribution-evidence";
import { withFrom } from "@/lib/routing";
import { parseWorkbenchDetailUrl } from "@/lib/detail-tabs";
import { useSymbolDetail } from "@/components/detail/symbol-detail-context";
import { createEntityMatcher, type EntityDict, type EntityMatch, type EntityMatcher } from "@/lib/entity-links";
import { isAllowedNav } from "@/lib/nav-targets";
import {ArticleBlocks} from "@/components/ui/article-blocks";
import {NarrativeText} from "@/components/ui/explanation-map";
import { useExitPresence } from "@/hooks/use-exit-presence";
import { ModalShell } from "@/components/ui/modal-shell";
import { eventTimeText } from "@/lib/format";

export interface NewsModalItem {
  title: string;
  url: string;
  date?: string | null;
  source?: string | null;
  kindLabel?: string;
  digest?: string | null;
  evidence?: string;
  /** 事件解释状态与证据边界，原文正文不改写。 */
  judgement?: string;
  eventId?: number;
  eventRef?: EventInterpretationRef | null;
  symbol?: string | null;
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
/** 失败后的重试闸门（0 = 立即可试）。
 *
 * **不得用"失败即写 null"来充当"已判定"**：判定条件是
 * `entityDictCache !== undefined`，而 `null !== undefined` 成立 ⇒ 一次瞬时失败
 * 会让词典在**本次会话内永不重试**（与后端 heatmap 行业映射的
 * "一次失败=永久失败"是同一类缺陷，见 B4-A / test_degradation_contracts.py）。
 * 现在失败只开冷却窗，过期后自动重试。 */
let entityDictRetryAfter = 0;
const ENTITY_DICT_RETRY_MS = 60_000;

/** 导出仅供守卫测试（`news-modal.test.tsx`）——生产调用点在本模块内。 */
export async function loadEntityDict(): Promise<EntityDict | null> {
  if (entityDictCache !== undefined) return entityDictCache;
  if (Date.now() < entityDictRetryAfter) return null; // 冷却期内不重复打网络
  try {
    const r = await fetch(`${API_BASE}/api/assistant/entity-dict`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    entityDictCache = (j?.data as EntityDict) ?? null;
  } catch {
    entityDictRetryAfter = Date.now() + ENTITY_DICT_RETRY_MS;
    // 保持 undefined ⇒ 冷却到期后自动重试（后端返回的空词典仍按 null 正常缓存）
  }
  return entityDictCache ?? null;
}

export function NewsModal({ item, onClose }: { item: NewsModalItem | null; onClose: () => void }) {
  const presence = useExitPresence(item);
  const router = useRouter();
  // 正文里的个股实体 → **就地弹窗**看详情（2026-09-15 详情弹窗化）
  const { open: openSymbolDetail } = useSymbolDetail();
  const { open: inspect } = useInspection();
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

  // 实体点击导航：深链优先（功能入口 / 个股+页签）；个股 → **就地弹窗**；
  // 题材 → 题材梯队。来源参数走 withFrom，深链与首页两种形态只此一种拼法。
  //
  // 2026-09-15 详情弹窗化：个股相关落点不再跳工作台——
  // - 注册表产出的个股深链（`/workbench?symbol=…&ct=…&rt=…`）经
  //   `parseWorkbenchDetailUrl` 还原成弹窗入参，**页签意图（K线/资金/逐笔…）一并带走**；
  // - 无法识别为标的详情的站内深链（"涨停池""题材梯队"等功能入口）照常跳转。
  const onNavigate = useCallback(
    (m: EntityMatch) => {
      onClose();
      if (m.url && isAllowedNav(m.url)) {
        const target = parseWorkbenchDetailUrl(m.url);
        if (target) {
          openSymbolDetail({...target, preferModal: true});
          return;
        }
        const inspection = inspectionRequestForHref(m.url);
        if (inspection) inspect(inspection);
        else router.push(withFrom(m.url));
      } else if (m.type === "stock" && m.code) {
        openSymbolDetail({ symbol: m.code, preferModal: true });
      } else if (m.type === "theme") {
        inspect({kind: "themes", focus: m.name});
      } else if (m.type === "nav" && m.url) {
        router.push(m.url);
      }
    },
    [router, openSymbolDetail, inspect, onClose],
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

  const shownItem = presence.value;
  if (!shownItem) return null;

  const shownTitle = content?.title ?? shownItem.title;
  const shownSource = content?.source_label ?? sourceText(shownItem.source);
  // 2026-09-09：此前优先取 content.published（抓正文接口的源站时间，与列表的
  // EventStore.published_at 不同源 → 时间对不上）。改为**优先列表同字段**
  // shownItem.date，两者都经 eventTimeText 统一格式。
  const shownTime = eventTimeText(shownItem.date ?? content?.published ?? null);

  return (
    <ModalShell open={presence.active} presentation="drawer"
      onClose={onClose}
      label={shownTitle}
      testid="news-modal"
      size="md"
      header={
        <>
          <h2 className="text-[15px] font-semibold leading-snug text-zinc-900 dark:text-zinc-100">{shownTitle}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-zinc-600 dark:text-zinc-400">
            {shownItem.kindLabel && <span className="rounded bg-zinc-100 px-1 py-px dark:bg-zinc-800">{shownItem.kindLabel}</span>}
            {shownSource && <span>{shownSource}</span>}
            {shownTime && <span>{shownTime}</span>}
            {shownItem.evidence && <span>{shownItem.evidence}</span>}
            {content?.cached && <span className="text-zinc-600 dark:text-zinc-400">缓存</span>}
            {content?.truncated && <span className="text-amber-800 dark:text-amber-500">长文已截断，完整内容见原文</span>}
          </div>
        </>
      }
      footer={
        <>
          <span className="min-w-0 flex-1 text-zinc-600 dark:text-zinc-400">内容归原作者/来源媒体所有，本站仅作研究参考</span>
          {shownItem.url.trim() ? <a
            data-action="secondary"
            href={shownItem.url}
            target="_blank"
            rel="noopener noreferrer"
          >
            <span>查看原文</span>
            <HugeiconsIcon icon={ArrowUpRight01Icon} aria-hidden="true" size={14} strokeWidth={1.6} className="shrink-0" />
          </a> : <span>原文链接缺失</span>}
        </>
      }
      bodyClassName="overflow-y-auto px-5 py-4"
      bodyTestId="news-modal-body"
    >
          {shownItem.judgement && (
            <div role="note" className="mb-4 border-b border-zinc-200 pb-3 text-xs leading-relaxed text-zinc-600 dark:border-zinc-700 dark:text-zinc-300">
              <NarrativeText text={shownItem.judgement} label="方向说明" identity={shownItem.url}/>
            </div>
          )}
          {!!shownItem.eventId && presence.active && <StockAttributionPanel key={shownItem.eventId} eventId={shownItem.eventId} symbol={shownItem.symbol} title="事件依据核对" bounded={false} recordedRefs={shownItem.eventRef ? [shownItem.eventRef] : undefined} />}
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
              <ArticleBlocks identity={shownItem.url} blocks={content.blocks} matcher={matcher} onNavigate={onNavigate} />
            ) : (
              <ArticleBlocks identity={shownItem.url} blocks={content.paragraphs.map(text => ({type: "p", text}))} matcher={matcher} onNavigate={onNavigate} />
            )
          )}

          {!loading && !content && (
            <div data-testid="news-modal-degraded">
              <p className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-400">
                正文获取失败{error ? `（${error}）` : ""}，以下为摘要。{shownItem.url.trim() ? "可在原文页查看完整内容。" : "原文链接缺失，无法跳转核对。"}{" "}
                {/* 渲染守卫会跳过同 URL 重取，瞬态失败必须显式重试入口自愈 */}
                <button
                  onClick={() => setFetchedUrl(null)}
                  className="font-medium text-sky-700 transition-colors hover:text-sky-700 dark:text-sky-400 dark:hover:text-sky-300"
                  data-testid="news-modal-retry"
                >
                  重试
                </button>
              </p>
              {shownItem.digest ? (
                <div className="mt-3"><NarrativeText text={shownItem.digest} label="来源摘要" identity={shownItem.url}/></div>
              ) : (
                <p className="mt-3 text-[13px] text-zinc-600 dark:text-zinc-400">暂无摘要。</p>
              )}
            </div>
          )}
    </ModalShell>
  );
}
