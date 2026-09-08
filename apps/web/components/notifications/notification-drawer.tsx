"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import {
  getNotifications,
  type NotificationItem,
  type NotificationsPayload,
} from "@/lib/api";
import { StockLink } from "@/components/stock-link";
import { NewsModal, type NewsModalItem } from "@/components/news-modal";

/**
 * 站内通知中心（2026-09-07 用户需求③）：导航栏铃铛 → 右侧抽屉。
 *
 * 内容：个股机会（watcher 确认/证伪）+ 每日精选 + 评分过滤后的消息面/新闻/政策
 * （后端 /api/notifications 三源合并，评分与时事新闻板块 events ranking 同源）。
 * 抽屉内一层 tab 按 盘前/盘中/盘后 分类（北京时间墙钟，后端判定同口径）。
 * 新闻不逐条推送：score ≥ 阈值才出现（默认 60，后端 settings 配置）。
 *
 * 已读规则（轻量）：localStorage 记最后已见条目 id 集合最新时间戳，铃铛圆点 =
 * 存在比其更新的条目；打开抽屉即刷新该时间戳（不做逐条已读，通知是快照流）。
 */

const SESSION_TABS: { key: NotificationItem["session"]; label: string }[] = [
  { key: "pre_open", label: "盘前" },
  { key: "intraday", label: "盘中" },
  { key: "after_close", label: "盘后" },
];

const LAST_SEEN_KEY = "ashare.notifications.lastSeenTs";

const CATEGORY_TONE: Record<NotificationItem["category"], string> = {
  opportunity: "bg-up/10 text-up",
  daily_picks: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  news: "bg-sky-500/10 text-sky-600 dark:text-sky-300",
  risk: "bg-red-500/10 text-red-600 dark:text-red-300",
};

const CATEGORY_LABEL: Record<NotificationItem["category"], string> = {
  opportunity: "个股机会",
  daily_picks: "每日精选",
  news: "消息面",
  risk: "策略风险",
};

function timeText(ts: string | null): string {
  if (!ts) return "--";
  // 后端两种 ts 语义都截 HH:MM 展示（日期在标题里）；跨天场景标题带日期足够
  return ts.length >= 16 ? ts.slice(11, 16) : ts;
}

function NotificationRow({ item, onOpenNews }: { item: NotificationItem; onOpenNews: (n: NewsModalItem) => void }) {
  const clickable = item.url?.startsWith("http") ?? false;
  const inner = (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${CATEGORY_TONE[item.category]}`}>
          {item.label}
        </span>
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-zinc-900 dark:text-zinc-50" title={item.title}>
          {item.title}
        </span>
        <span className="shrink-0 font-mono text-[10px] text-zinc-400">{timeText(item.ts)}</span>
      </div>
      <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">{item.body}</p>
      <div className="mt-1 flex items-center gap-2 text-[10px] text-zinc-400">
        <span className="rounded bg-zinc-100 px-1 py-px dark:bg-zinc-800">{CATEGORY_LABEL[item.category]}</span>
        {item.score != null && (
          <span className="font-mono" title="事件评分（与时事新闻板块同源）：影响力/题材共振/新鲜度/来源综合">
            评分 {item.score}
          </span>
        )}
        {clickable && <span className="text-sky-500">查看全文 ↗</span>}
      </div>
    </>
  );
  const shell = `block w-full rounded-lg border border-zinc-100 p-2.5 text-left transition-colors hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-800/40 ${
    clickable ? "cursor-pointer" : ""
  }`;
  if (clickable) {
    return (
      <button
        type="button"
        className={shell}
        onClick={() =>
          onOpenNews({ title: item.title, url: item.url!, date: item.ts, source: null, kindLabel: item.label })
        }
      >
        {inner}
      </button>
    );
  }
  return <div className={shell}>{inner}</div>;
}

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [payload, setPayload] = useState<NotificationsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<NotificationItem["session"]>("intraday");
  const [newsItem, setNewsItem] = useState<NewsModalItem | null>(null);
  const [unread, setUnread] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const p = await getNotifications();
      setPayload(p);
    } catch (e) {
      setError((e as Error).message || "通知加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  // 打开抽屉即拉取 + 刷新已读时间戳；关闭后 60s 轮询维持未读点新鲜（轻量：只在打开过一次后启用）
  useEffect(() => {
    if (!open) return;
    void load();
  }, [open, load]);

  useEffect(() => {
    if (!open || !payload) return;
    try {
      localStorage.setItem(LAST_SEEN_KEY, payload.generated_at);
      setUnread(false);
    } catch {}
  }, [open, payload]);

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const p = await getNotifications();
        if (!alive) return;
        const last = localStorage.getItem(LAST_SEEN_KEY);
        setUnread(!!last && p.items.some((i) => (i.ts ?? "") > last));
      } catch {}
    };
    void check();
    const t = setInterval(check, 60_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const bySession = useMemo(() => {
    const m: Record<NotificationItem["session"], NotificationItem[]> = { pre_open: [], intraday: [], after_close: [] };
    for (const i of payload?.items ?? []) m[i.session].push(i);
    return m;
  }, [payload]);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        aria-label="打开通知中心"
        title="通知中心：个股机会 / 每日精选 / 评分过滤后的消息面（盘前·盘中·盘后）"
        className="relative rounded-md border border-zinc-200 p-2 text-sm text-zinc-500 transition-colors hover:text-zinc-900 dark:border-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {unread && <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-up" aria-hidden />}
      </button>

      {open &&
        createPortal(
          <div className="fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-sm" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }} role="dialog" aria-modal="true" aria-label="通知中心" data-testid="notification-drawer">
            <div className="flex h-full w-full max-w-sm flex-col border-l border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-950">
              {/* 头：标题 + 关闭 */}
              <div className="flex items-center justify-between border-b border-zinc-100 px-4 py-3 dark:border-zinc-800/80">
                <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">通知中心</h2>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => void load()}
                    className="rounded p-1 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                    aria-label="刷新通知"
                    title="重新拉取（页面打开期间每 60s 自动检查新通知）"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" />
                    </svg>
                  </button>
                  <button
                    onClick={() => setOpen(false)}
                    className="rounded p-1 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                    aria-label="关闭"
                  >
                    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                      <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                    </svg>
                  </button>
                </div>
              </div>

              {/* tab：盘前 / 盘中 / 盘后 */}
              <div className="flex gap-1 border-b border-zinc-100 px-4 py-2 dark:border-zinc-800/80">
                {SESSION_TABS.map((t) => (
                  <button
                    key={t.key}
                    onClick={() => setTab(t.key)}
                    className={`rounded-full px-3 py-1 text-xs transition-colors ${
                      tab === t.key
                        ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                        : "text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                    }`}
                  >
                    {t.label}
                    <span className="ml-1 text-[10px] opacity-70">{bySession[t.key].length}</span>
                  </button>
                ))}
              </div>

              {/* 列表 */}
              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3" data-testid="notification-list">
                {loading && !payload && (
                  <div className="space-y-2">
                    {[0, 1, 2].map((i) => (
                      <div key={i} className="h-16 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-800/60" />
                    ))}
                  </div>
                )}
                {error && (
                  <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-300">
                    {error}（可点右上刷新重试）
                  </p>
                )}
                {payload && bySession[tab].length === 0 && (
                  <p className="px-2 py-8 text-center text-xs text-zinc-400">
                    {tab === "intraday" ? "盘中暂无通知（watcher 确认/证伪提醒与评分达标新闻会出现在这里）" : "该时段暂无通知"}
                  </p>
                )}
                {payload?.items.map((i) =>
                  i.session === tab ? (
                    i.symbol ? (
                      <div key={i.id} className="flex items-start gap-2">
                        <NotificationRow item={i} onOpenNews={setNewsItem} />
                        <StockLink
                          symbol={i.symbol}
                          className="mt-2.5 shrink-0 rounded border border-zinc-200 px-1.5 py-0.5 text-[10px] text-zinc-400 dark:border-zinc-700"
                          title={`查看 ${i.symbol} 行情详情`}
                        >
                          行情 ↗
                        </StockLink>
                      </div>
                    ) : (
                      <NotificationRow key={i.id} item={i} onOpenNews={setNewsItem} />
                    )
                  ) : null,
                )}
                {payload?.errors && (
                  <p className="pt-1 text-[10px] text-amber-500/90" title={Object.entries(payload.errors).map(([k, v]) => `${k}: ${v}`).join("；")}>
                    ⚠ 部分来源降级：{Object.keys(payload.errors).join("、")}
                  </p>
                )}
              </div>

              <div className="border-t border-zinc-100 px-4 py-2 text-[10px] leading-relaxed text-zinc-400 dark:border-zinc-800/80">
                新闻仅推送评分 ≥ {payload?.news_min_score ?? 60} 的条目（与时事新闻板块同一评分机制）；
                名单类通知均为可解释依据，不构成买卖建议。
              </div>
            </div>
          </div>,
          document.body,
        )}

      {/* 新闻全文复用全站资讯弹窗（评分达标的新闻点击看正文） */}
      <NewsModal item={newsItem} onClose={() => setNewsItem(null)} />
    </>
  );
}
