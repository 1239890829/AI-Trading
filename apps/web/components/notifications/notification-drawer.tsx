"use client";

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

import {
  getNotifications,
  type NotificationItem,
  type NotificationsPayload,
} from "@/lib/api";
import {
  countUnread,
  getPrefsSnapshot,
  getServerPrefsSnapshot,
  isCleared,
  isUnread,
  setPrefs,
  subscribePrefs,
  withAllRead,
  withRead,
} from "@/lib/notification-read";
import { StockLink } from "@/components/stock-link";
import { IncrementalSentinel } from "@/components/ui/incremental-sentinel";
import { useIncremental } from "@/hooks/use-incremental";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { useDetailModal } from "@/components/detail/detail-modal";
import { NewsModal, type NewsModalItem } from "@/components/news-modal";

/**
 * 站内通知中心（2026-09-07 用户需求③）：导航栏铃铛 → 右侧抽屉。
 *
 * 内容只保留经过置信档、多维评分、红线、闸门、买入区间、涨停区与实时行情
 * 共同门控后的个股机会。板块异动、题材方向、每日精选与新闻留在各自分析页面，
 * 不再作为会打断用户的通知。
 * 抽屉内一层 tab 按 盘前/盘中/盘后 分类（后端判定：交易日历优先，非交易日归盘前）。
 * 新闻不逐条推送：score ≥ 阈值才出现（默认 60，后端 settings 配置）。
 *
 * 已读规则（2026-09-11 重做，见 lib/notification-read.ts 的根因说明）：
 *  - 未读 = **条目级**：`ts` 晚于已读水位、且未被单独点开；未读条目左侧带红点；
 *  - 点开某条 → 该条已读、红点消失；「全部已读」→ 水位推进到当下，红点全消；
 *  - 铃铛徽标 = 未读条数（**派生自本次 payload**，不再用会失真的字符串比较）；
 *  - 打开抽屉**不再**自动全部已读（否则红点会一闪即逝，看不到自己没读什么）。
 *
 * 持久化（2026-09-12 缺陷修复）：已读状态**双写** —— localStorage 作首帧缓存 +
 * 服务端权威副本（`/api/notifications/read-state`）。此前只存 localStorage，
 * 而它按 origin 命名空间，换源/换 profile/清站点数据就让已读整体归零
 * （实测 localhost↔127.0.0.1 徽标回到 65）。同步逻辑全在 `lib/notification-read.ts`，
 * 本组件只消费外部存储快照，因此无需改动。
 *
 * ⚠️ 历史缺陷（勿回退）：旧实现把「已读水位」写成 `toISOString()`（UTC 带 T），
 * 却与后端 `ts`（北京 naive 带空格）做**字面比较** —— 当天条目恒被判为已读，
 * 跨日又整天一起计入未读，表现为「一键已读后计数没了，来了新的却在旧累积上累加」。
 * 一律先 `parseTs` 转 epoch 再比大小。
 */

const SESSION_TABS: { key: NotificationItem["session"]; label: string }[] = [
  { key: "pre_open", label: "盘前" },
  { key: "intraday", label: "盘中" },
  { key: "after_close", label: "盘后" },
];


const CATEGORY_TONE: Record<NotificationItem["category"], string> = {
  opportunity: "bg-up/10 text-up-ink dark:text-up",
  daily_picks: "bg-amber-500/10 text-amber-800 dark:text-amber-400",
  news: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  risk: "bg-red-500/10 text-red-700 dark:text-red-300",
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

function NotificationRow({
  item,
  unread,
  onRead,
  onOpenNews,
}: {
  item: NotificationItem;
  /** 未读 → 左侧红点；点开即消失（用户 2026-09-11 需求） */
  unread: boolean;
  onRead: () => void;
  onOpenNews: (n: NewsModalItem) => void;
}) {
  const { open: openDetail } = useDetailModal();
  // 2026-09-09：无 url 的通知（快讯类大多无原文链接）此前渲染成死 div 点不动。
  // 现统一可点：有 url 走 NewsModal 看原文；无 url 走通用详情弹窗看 body+评分。
  const clickable = item.url?.startsWith("http") ?? false;
  const inner = (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        {unread && (
          <span
            data-testid="notification-unread-dot"
            aria-label="未读"
            title="未读"
            className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500"
          />
        )}
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${CATEGORY_TONE[item.category]}`}>
          {item.label}
        </span>
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-zinc-900 dark:text-zinc-50" title={item.title}>
          {item.title}
        </span>
        <span className="shrink-0 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{timeText(item.ts)}</span>
      </div>
      <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">{item.body}</p>
      <div className="mt-1 flex items-center gap-2 text-[10px] text-zinc-600 dark:text-zinc-400">
        <span className="rounded bg-zinc-100 px-1 py-px dark:bg-zinc-800">{CATEGORY_LABEL[item.category]}</span>
        {item.score != null && (
          <span className="font-mono" title="事件评分（与时事新闻板块同源）：影响力/题材共振/新鲜度/来源综合">
            评分 {item.score}
          </span>
        )}
        {clickable && <span className="text-sky-700 dark:text-sky-500">查看全文 ↗</span>}
      </div>
    </>
  );
  const shell = `block w-full rounded-lg border p-2.5 text-left transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/40 ${
    unread
      ? "border-rose-200 bg-rose-50/40 dark:border-rose-500/25 dark:bg-rose-500/5"
      : "border-zinc-100 dark:border-zinc-800/60"
  } ${clickable ? "cursor-pointer" : ""}`;
  if (clickable) {
    return (
      <button
        type="button"
        className={shell}
        onClick={() => {
          onRead();
          onOpenNews({ title: item.title, url: item.url!, date: item.ts, source: null, kindLabel: item.label });
        }}
      >
        {inner}
      </button>
    );
  }
  return (
    <button
      type="button"
      className={shell}
      onClick={() => {
        onRead();
        openDetail({
          kind: item.category === "news" ? "event" : "generic",
          title: item.title,
          body: item.body,
          symbol: item.symbol,
          source: null,
          date: item.ts,
          meta: [
            { label: "分类", value: item.label },
            ...(item.score != null ? [{ label: "评分", value: String(item.score) }] : []),
          ],
        });
      }}
    >
      {inner}
    </button>
  );
}

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [payload, setPayload] = useState<NotificationsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<NotificationItem["session"]>("intraday");
  const [newsItem, setNewsItem] = useState<NewsModalItem | null>(null);
  // 已读偏好（水位 + 逐条 id + 清除水位）：**外部存储订阅**，见 lib/notification-read.ts。
  // 首帧（含 hydration）给服务端空快照，hydration 后自动切到 localStorage 真实值。
  const prefs = useSyncExternalStore(subscribePrefs, getPrefsSnapshot, getServerPrefsSnapshot);
  const { read: readState, clearBefore } = prefs;

  /** 一键已读：水位推进到「现在」——覆盖所有更早条目（含拉取窗口外的历史）。 */
  const markAllRead = useCallback(() => {
    setPrefs({ read: withAllRead(readState), clearBefore });
  }, [readState, clearBefore]);

  /** 一键清除：记录清除时刻——更早条目整体隐藏；顺带把已读水位一并推到该时刻。 */
  const clearAll = useCallback(() => {
    const ts = Date.now();
    setPrefs({ read: withAllRead(readState, ts), clearBefore: ts });
  }, [readState]);

  const markOneRead = useCallback(
    (item: NotificationItem) => {
      const nextRead = withRead(readState, item);
      if (nextRead !== readState) setPrefs({ read: nextRead, clearBefore });
    },
    [readState, clearBefore],
  );

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

  // 打开抽屉即拉取（关闭后由下方 60s 轮询维持未读点新鲜）
  //
  // 下方 void load() 为 **C 类显式豁免**（P1-27）：load 首行是
  // `setLoading(true)/setError(null)` 的**同步** loading 标志——这是"点击即骨架"
  // 的既定契约，规则想防的级联渲染在这里不成立；改成微任务延后会引入一帧
  // 无骨架的闪空，反而更差。
  useEffect(() => {
    if (!open) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [open, load]);

  // 60s 轮询：只刷新 payload，**不再在 effect 里改已读状态**
  // （旧实现打开抽屉就写水位，导致"红点一闪即逝"且水位格式两套，见文件头说明）。
  // 2026-09-11（S2-5）：裸 setInterval → 统一入口（获得可见性暂停）。
  // `marketHours: false` —— 通知含「盘后」时段条目，盘外正是需要及时看到的时候，
  // 不能套用行情类的盘外 ×5 降频（60s 会变 120s）。
  usePollingFetch(
    async () => {
      try {
        const p = await getNotifications();
        setPayload(p);
      } catch {
        /* 轮询失败静默：下次再试，不清空已有内容 */
      }
    },
    60_000,
    undefined,
    { marketHours: false }
  );

  // 未读数：**派生自当前 payload**（单一真相源是 payload.items + readState），
  // 不再用「上一次轮询算出的数字」——那正是"计数在旧累积上叠加"的来源。
  const unread = useMemo(
    () => countUnread(payload?.items ?? [], readState, clearBefore),
    [payload, readState, clearBefore],
  );

  const bySession = useMemo(() => {
    const m: Record<NotificationItem["session"], NotificationItem[]> = { pre_open: [], intraday: [], after_close: [] };
    for (const i of payload?.items ?? []) {
      if (isCleared(i, clearBefore)) continue;
      m[i.session].push(i);
    }
    return m;
  }, [payload, clearBefore]);

  const unreadBySession = useMemo(() => {
    const m: Record<NotificationItem["session"], number> = { pre_open: 0, intraday: 0, after_close: 0 };
    for (const i of payload?.items ?? []) {
      if (isCleared(i, clearBefore)) continue;
      if (isUnread(i, readState)) m[i.session] += 1;
    }
    return m;
  }, [payload, readState, clearBefore]);

  const isItemUnread = useCallback(
    (item: NotificationItem) => !isCleared(item, clearBefore) && isUnread(item, readState),
    [clearBefore, readState],
  );

  // 增量渲染（#55）：/api/notifications 默认 60 条（实测 16KB），每条是富文本卡片，
  // 一次性全铺会拖慢抽屉打开的首帧。切换时段 tab / 清空历史时重置到首页。
  const activeList = bySession[tab];
  const { shown: shownNotices, visible, sentinelRef } = useIncremental(activeList, {
    resetKey: `${tab}/${clearBefore}`,
  });

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        aria-label={unread > 0 ? `打开通知中心（${unread} 条未读）` : "打开通知中心"}
        title="通知中心：仅多维筛选后的个股机会（盘前·盘中·盘后）"
        className="relative rounded-md border border-zinc-200 p-2 text-sm text-zinc-600 transition-colors hover:text-zinc-900 dark:border-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {unread > 0 && (
          <span
            data-testid="notification-badge"
            className="absolute -right-1.5 -top-1.5 min-w-[16px] rounded-full bg-up-deep px-1 text-center text-[10px] font-semibold leading-4 text-white"
            aria-label={`${unread} 条未读通知`}
          >
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open &&
        createPortal(
          <div className="anim-backdrop-in fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-sm" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }} role="dialog" aria-modal="true" aria-label="通知中心" data-testid="notification-drawer">
            <div className="anim-slide-in-right flex h-full w-full max-w-sm flex-col border-l border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-950">
              {/* 头：标题 + 未读数 + 操作 */}
              <div className="flex items-center justify-between border-b border-zinc-100 px-4 py-3 dark:border-zinc-800/80">
                <h2 className="flex items-center gap-1.5 text-sm font-semibold text-zinc-900 dark:text-zinc-50">
                  通知中心
                  {unread > 0 && (
                    <span
                      data-testid="notification-unread-summary"
                      className="rounded-full bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-medium text-rose-700 dark:text-rose-300"
                    >
                      未读 {unread}
                    </span>
                  )}
                </h2>
                <div className="flex items-center gap-2">
                  <button
                    onClick={markAllRead}
                    disabled={unread === 0}
                    data-testid="notification-mark-all-read"
                    className="rounded px-1.5 py-0.5 text-[11px] text-zinc-600 transition-colors hover:bg-zinc-100 hover:text-zinc-700 disabled:opacity-40 disabled:hover:bg-transparent dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                    title="全部标记为已读（未读红点与徽标清零）"
                  >
                    全部已读
                  </button>
                  <button
                    onClick={clearAll}
                    className="rounded px-1.5 py-0.5 text-[11px] text-zinc-600 dark:text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                    title="隐藏当前全部条目（本地清除，随时可清 storage 恢复）"
                  >
                    一键清除
                  </button>
                  <button
                    onClick={() => void load()}
                    className="rounded p-1 text-zinc-600 dark:text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                    aria-label="刷新通知"
                    title="重新拉取（页面打开期间每 60s 自动检查新通知）"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" />
                    </svg>
                  </button>
                  <button
                    onClick={() => setOpen(false)}
                    className="rounded p-1 text-zinc-600 dark:text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                    aria-label="关闭"
                  >
                    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                      <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                    </svg>
                  </button>
                </div>
              </div>

              {/* tab：盘前 / 盘中 / 盘后（红点 = 该时段有未读） */}
              <div className="flex gap-1 border-b border-zinc-100 px-4 py-2 dark:border-zinc-800/80">
                {SESSION_TABS.map((t) => (
                  <button
                    key={t.key}
                    onClick={() => setTab(t.key)}
                    className={`flex items-center gap-1 rounded-full px-3 py-1 text-xs transition-colors ${
                      tab === t.key
                        ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                        : "text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                    }`}
                  >
                    {t.label}
                    {unreadBySession[t.key] > 0 && (
                      <span
                        data-testid={`notification-tab-dot-${t.key}`}
                        aria-label={`${unreadBySession[t.key]} 条未读`}
                        className="h-1.5 w-1.5 rounded-full bg-rose-500"
                      />
                    )}
                    <span className="text-[10px] opacity-70">{bySession[t.key].length}</span>
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
                  <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
                    {error}（可点右上刷新重试）
                  </p>
                )}
                {payload && bySession[tab].length === 0 && (
                  <p className="px-2 py-8 text-center text-xs text-zinc-600 dark:text-zinc-400">
                    {tab === "intraday" ? "盘中暂无通过多维筛选的个股机会" : "该时段暂无个股机会"}
                  </p>
                )}
                {shownNotices.map((i) =>
                  i.symbol ? (
                    <div key={i.id} className="flex items-start gap-2">
                      <NotificationRow
                        item={i}
                        unread={isItemUnread(i)}
                        onRead={() => markOneRead(i)}
                        onOpenNews={setNewsItem}
                      />
                      <StockLink
                        symbol={i.symbol}
                        className="mt-2.5 shrink-0 rounded border border-zinc-200 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:text-zinc-400 dark:border-zinc-700"
                        title={`查看 ${i.symbol} 行情详情`}
                      >
                        行情 ↗
                      </StockLink>
                    </div>
                  ) : (
                    <NotificationRow
                      key={i.id}
                      item={i}
                      unread={isItemUnread(i)}
                      onRead={() => markOneRead(i)}
                      onOpenNews={setNewsItem}
                    />
                  ),
                )}
                {/* 哨兵须在滚动容器内部（本 div overflow-y-auto），否则不随滚动移动、只触发一次 */}
                <IncrementalSentinel
                  sentinelRef={sentinelRef}
                  visible={visible}
                  total={activeList.length}
                  unit="条通知"
                  testId="notification-sentinel"
                />
                {payload?.errors && (
                  <p className="pt-1 text-[10px] text-amber-500/90" title={Object.entries(payload.errors).map(([k, v]) => `${k}: ${v}`).join("；")}>
                    ⚠ 部分来源降级：{Object.keys(payload.errors).join("、")}
                  </p>
                )}
              </div>

              <div className="border-t border-zinc-100 px-4 py-2 text-[10px] leading-relaxed text-zinc-600 dark:text-zinc-400 dark:border-zinc-800/80">
                仅推送通过有效筛选、逻辑校验与多维评估的个股机会；板块机会不通知。
                所有提醒均附可解释依据与失效条件，不构成买卖建议。
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
