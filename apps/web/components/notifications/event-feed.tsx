"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";

import { getImpactEvents, type EventSort, type ImpactEvent } from "@/lib/api";
import {
  FOUR_STYLE,
  LEVEL_STYLE,
  TAG_STYLE,
  eventDetailPayload,
  eventNewsItem,
} from "@/lib/event-view";
import { NewsModal, type NewsModalItem } from "@/components/news-modal";
import { useDetailModal } from "@/components/detail/detail-modal";
import { symbolDetailClick, useSymbolDetail } from "@/components/detail/symbol-detail-context";
import { IncrementalSentinel } from "@/components/ui/incremental-sentinel";
import { useIncremental } from "@/hooks/use-incremental";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { themesUrl, workbenchUrl } from "@/lib/routing";

/**
 * 通知中心「资讯 / 事件」tab（2026-09-16 用户需求①）。
 *
 * **为什么需要它**：`IMP-028` 把通知中心收口成 `stock_opportunities_only`
 * （`routes/notifications.py:69` 的 `_NOTIF_RULE_NAMES` 只认 `__picks_buy_point__`），
 * 板块异动 / 题材方向 / 新闻**全部移出推送** —— 收口本身是对的（可研究的信息不该
 * 打断用户），但代价是**资讯与事件在通知中心彻底不可见**（实测生产库
 * `event_card` 当日 798 条，通知中心一条都看不到）。
 *
 * 本 tab 用**浏览面**补回可见性，**不撤销收口**：
 *  - 数据源直接复用 `GET /api/events/impact`（盘面页「事件影响力」同源），
 *    **不新增采集链路、不新增后端端点**；
 *  - 铃铛徽标与未读红点**仍只由个股机会驱动**（`NotificationBell` 的 `unread`
 *    派生自 `/api/notifications` 的 items）—— 本 tab 的条目**不**计入未读，
 *    否则「值得打断用户的」与「可研究的」又被混为一谈，正是收口要消除的东西。
 *
 * 版式：抽屉是窄栏（`max-w-sm` = 384px），故为**紧凑版**；配色与点击落点
 * 一律取自 `lib/event-view.ts`（与盘面页同一份，避免两处漂移）。
 *
 * ⚠️ 懒加载：`active` 为假时不发任何请求（`usePollingFetch` 的 `enabled`）——
 * 抽屉默认停在「个股机会」，不该为没打开的 tab 每 60s 白拉一次 100 条事件。
 */
const SORTS: { key: EventSort; label: string; title: string }[] = [
  { key: "relevance", label: "最相关", title: "与当日大盘/情绪强关联的事件排前（题材共振+相位加权+影响力+时效）" },
  { key: "time", label: "最新", title: "按发布时间倒序" },
];

const CHIP = "rounded-full border px-2 py-0.5 text-[11px] transition-colors";
const CHIP_ON =
  "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900";
const CHIP_OFF =
  "border-zinc-200 text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100";

function EventRow({
  e,
  showRank,
  onOpenNews,
}: {
  e: ImpactEvent;
  showRank: boolean;
  onOpenNews: (n: NewsModalItem) => void;
}) {
  const { open: openDetail } = useDetailModal();
  const { open: openSymbolDetail } = useSymbolDetail();
  const router = useRouter();
  const clickable = Boolean(e.url);

  return (
    <div
      data-testid="event-feed-row"
      className="rounded-lg border border-zinc-100 p-2.5 text-left transition-colors hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-800/40"
    >
      <div className="flex flex-wrap items-center gap-1">
        {showRank && e.rank_score != null && (
          <span
            className="shrink-0 rounded bg-zinc-900 px-1 py-px font-mono text-[10px] font-semibold text-white dark:bg-zinc-100 dark:text-zinc-900"
            title={(e.rank_reasons ?? []).join("\n") || "相关性评分"}
          >
            {Math.round(e.rank_score)}分
          </span>
        )}
        <span
          data-testid="event-feed-level"
          className={`shrink-0 rounded border px-1 py-px text-[10px] font-semibold ${LEVEL_STYLE[e.impact_level] ?? ""}`}
          title={e.impact_level === "L1" ? "L1 必上：政策/行业级/国际重大" : "L2 选上：事实类+有标的链"}
        >
          {e.impact_level}
        </span>
        <span
          className={`shrink-0 rounded border px-1 py-px text-[10px] ${FOUR_STYLE[e.four_category] ?? ""}`}
        >
          {e.four_label}
        </span>
        <span className="ml-auto shrink-0 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">
          {e.published_at?.slice(5, 16) ?? ""}
        </span>
      </div>

      {/* 标题即落点：有原文 ⇒ 全文弹窗；无原文 ⇒ 通用详情弹窗（带 eventId 取方向行）。
          与盘面页事件标签**同一口径**（`lib/event-view.ts`），不静默失败。 */}
      <button
        type="button"
        onClick={() =>
          clickable ? onOpenNews(eventNewsItem(e)) : openDetail(eventDetailPayload(e))
        }
        className="mt-1 block w-full text-left text-xs font-medium leading-relaxed text-zinc-900 transition-colors hover:text-sky-700 dark:text-zinc-50 dark:hover:text-sky-400"
      >
        {e.title}
      </button>

      {/* 排序依据（最相关模式）：首条内联，全部悬浮可追溯 */}
      {showRank && e.rank_reasons && e.rank_reasons.length > 0 && (
        <p
          className="mt-0.5 truncate text-[10px] text-zinc-500 dark:text-zinc-500"
          title={e.rank_reasons.join("；")}
        >
          排序依据：{e.rank_reasons[0]}
          {e.rank_reasons.length > 1 && ` 等 ${e.rank_reasons.length} 项`}
        </p>
      )}

      {/* 方向落点：个股 → 就地弹窗（与全站详情弹窗化一致）；题材 → 跳题材页 */}
      {e.directions.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          {e.directions.slice(0, 3).map((d) => {
            const isStock = d.target_type === "symbol";
            const label = d.direction > 0 ? "利好" : d.direction < 0 ? "利空" : "待判";
            const cls =
              d.direction > 0 ? "text-up-ink dark:text-up" : d.direction < 0 ? "text-down-ink dark:text-down" : "text-zinc-500";
            return (
              <a
                key={`${d.target_type}-${d.target}`}
                href={isStock ? workbenchUrl(d.target) : themesUrl(d.target)}
                onClick={
                  isStock
                    ? symbolDetailClick(openSymbolDetail, { symbol: d.target })
                    : (ev) => {
                        // 题材页是整页切换：抽屉随导航关闭（不 preventDefault，走正常路由）
                        ev.currentTarget.blur();
                        router.push(themesUrl(d.target));
                      }
                }
                title={[d.chain, d.basis].filter(Boolean).join(" ｜ ") || `关联${isStock ? "个股" : "题材"} ${d.target}`}
                className="inline-flex items-center gap-1 rounded border border-zinc-200 px-1 py-px text-[10px] text-zinc-700 hover:border-zinc-400 dark:border-zinc-700 dark:text-zinc-200"
              >
                <span className="text-zinc-500 dark:text-zinc-400">{isStock ? "个股" : "题材"}</span>
                <span>{d.target}</span>
                <span className={`font-medium ${cls}`}>{label}</span>
              </a>
            );
          })}
          {e.tags.slice(0, 2).map((t) => (
            <span key={t} className={`rounded border px-1 py-px text-[10px] ${TAG_STYLE}`}>
              {t}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function EventFeed({ active, refreshToken = 0 }: { active: boolean; refreshToken?: number }) {
  const [items, setItems] = useState<ImpactEvent[] | null>(null);
  const [countsAll, setCountsAll] = useState<Record<string, number> | null>(null);
  const [sort, setSort] = useState<EventSort>("relevance");
  const [l1Only, setL1Only] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newsItem, setNewsItem] = useState<NewsModalItem | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await getImpactEvents(false, 100, sort);
      setItems(r.items);
      setCountsAll(r.countsAll);
      setError(null);
    } catch (e) {
      setError((e as Error).message || "资讯加载失败");
    }
  }, [sort]);

  // `key`：排序切换或抽屉顶部「刷新」被按下时立即重拉（否则要等下一次 60s 轮询）；
  // `enabled = active`：抽屉没切到本 tab 时不发请求。
  usePollingFetch(load, 60_000, `${sort}#${refreshToken}`, { marketHours: false, enabled: active });

  const shown = (items ?? []).filter((e) => !l1Only || e.impact_level === "L1");
  const { shown: shownPage, visible, sentinelRef } = useIncremental(shown, {
    resetKey: `${sort}/${l1Only}`,
  });

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="event-feed">
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-zinc-100 px-4 py-2 dark:border-zinc-800/80">
        {SORTS.map((s) => (
          <button
            key={s.key}
            type="button"
            onClick={() => setSort(s.key)}
            title={s.title}
            className={CHIP + " " + (sort === s.key ? CHIP_ON : CHIP_OFF)}
          >
            {s.label}
          </button>
        ))}
        <label className="ml-auto flex cursor-pointer items-center gap-1 text-[11px] text-zinc-600 dark:text-zinc-400">
          <input type="checkbox" checked={l1Only} onChange={(e) => setL1Only(e.target.checked)} />
          只看 L1
        </label>
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3" data-testid="event-feed-list">
        {error && (
          <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
            {error}（切走再切回可重试）
          </p>
        )}
        {items === null && !error && (
          <div className="space-y-2" aria-hidden>
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-16 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-800/60" />
            ))}
          </div>
        )}
        {items !== null && shown.length === 0 && !error && (
          <p className="py-8 text-center text-xs text-zinc-600 dark:text-zinc-400">
            当前筛选下暂无资讯事件（L3 日常经营/人事变动默认不上榜）
          </p>
        )}
        {shownPage.map((e) => (
          <EventRow key={e.id} e={e} showRank={sort === "relevance"} onOpenNews={setNewsItem} />
        ))}
        <IncrementalSentinel
          sentinelRef={sentinelRef}
          visible={visible}
          total={shown.length}
          unit="条资讯"
          testId="event-feed-sentinel"
        />
      </div>

      <NewsModal item={newsItem} onClose={() => setNewsItem(null)} />
      {/* 数据面自陈：条数口径来自后端 counts_all，避免"看起来只有这么几条" */}
      {countsAll && (
        <p className="shrink-0 border-t border-zinc-100 px-4 py-1.5 text-[10px] text-zinc-600 dark:text-zinc-400 dark:border-zinc-800/80">
          事件影响力：L1 {countsAll.L1 ?? 0} · L2 {countsAll.L2 ?? 0} · L3 已滤 {countsAll.L3 ?? 0}
          （源自东财快讯等，L3 日常经营/人事变动默认不上榜）
        </p>
      )}
    </div>
  );
}
