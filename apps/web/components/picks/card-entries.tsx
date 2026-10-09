"use client";

/**
 * 猎场个股卡片入口行（2026-09-09 需求 3，17:15 按用户反馈收敛）
 *
 * 入口收敛为三个，且**无数据不出现**：
 * - **消息**（事件 + 资讯合并为一个）→ 弹通用详情弹窗（feed 列表），**不跳转**；
 *   该股既无关联事件也无资讯时整个按钮不渲染（避免"点了只有一句暂无"的噪音）。
 * - **资金** → 弹窗内展示资金图（`kind: "capital"`，复用工作台组件），**不跳转**；
 *   **梯队** → 原地题材旁览，保留当前候选。
 *   两者都阻止冒泡（卡片整块可点会带走路由）。
 *
 * 冒泡纪律：CardShell 整卡 onClick=stockNav，任何子元素按钮一律 stopAnd()。
 */
import { useResource } from "@/hooks/use-resource";
import { getEventsForSymbol } from "@/lib/api";
import { useDetailModal } from "@/components/detail/detail-modal";
import { useInspection } from "@/components/inspection/inspection-context";
import { motionOrigin } from "@/lib/surface-motion";

const BTN =
  "card-entry-action";

/** Bounded, expiring presence cache. A negative result is never permanent. */
const hasFeedCache = new Map<string, {value: boolean; expiresAt: number}>();
const inFlight = new Map<string, Promise<boolean>>();
async function feedPresence(symbol: string): Promise<boolean> {
  const cached = hasFeedCache.get(symbol);
  if (cached && cached.expiresAt > Date.now()) return cached.value;
  const pending = inFlight.get(symbol);
  if (pending) return pending;
  const request = getEventsForSymbol(symbol).then(events => {
    const value = (events.items?.length ?? 0) > 0;
    hasFeedCache.delete(symbol);
    hasFeedCache.set(symbol, {value, expiresAt: Date.now() + 30_000});
    if (hasFeedCache.size > 128) hasFeedCache.delete(hasFeedCache.keys().next().value!);
    return value;
  }).finally(() => inFlight.delete(symbol));
  inFlight.set(symbol, request);
  return request;
}

export function CardEntryRow({
  symbol,
  name,
  theme,
}: {
  symbol: string;
  name?: string | null;
  theme?: string | null;
}) {
  const { open } = useDetailModal();
  const { open: inspect } = useInspection();
  const feed = useResource(() => feedPresence(symbol), {key: symbol, intervalMs: 30_000});
  const hasFeed = feed.data;

  // ⚠️ 卡片整块可点（CardShell onClick=stockNav 跳个股页），入口按钮必须阻止冒泡，
  // 否则点击会顺带触发整卡跳转——弹窗刚开就被路由带走（2026-09-09 实测）。
  function stopAnd(fn: () => void) {
    return (e: React.MouseEvent) => {
      e.stopPropagation();
      e.preventDefault();
      fn();
    };
  }

  return (
    <div className="card-entry-dock" aria-label="关联研究入口">
      {hasFeed && (
        <button
          type="button"
          className={BTN}
          onClick={stopAnd(() =>
            open({ kind: "feed", title: `${name ?? symbol} · 消息`, symbol, theme: theme ?? null }),
          )}
          title="关联事件 + 资讯公告（合并视图）"
        >
          <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M5 5h14v14H5zM8 9h8M8 13h6" /></svg>消息
        </button>
      )}
      <button
        type="button"
        className={BTN}
        onClick={stopAnd(() =>
          open({ kind: "capital", title: `${name ?? symbol} · 资金流`, symbol }),
        )}
        title="资金图：近 30 日主力净额（复用工作台组件，弹窗内展示）"
      >
        <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 19h16M7 15V9m5 6V5m5 10v-7" /></svg>资金
      </button>
      {theme && (
        <button
          type="button"
          className={BTN}
          onClick={event => { event.stopPropagation(); event.preventDefault(); inspect({kind: "themes", focus: theme, motionOrigin: motionOrigin(event)}); }}
          title={`在当前页查看：${theme} 梯队`}
        >
          <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 19h5v-5H4zM9 14h5V9H9zM14 9h5V4h-5z" /></svg>梯队
        </button>
      )}
    </div>
  );
}
