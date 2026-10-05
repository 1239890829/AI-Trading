"use client";

/**
 * 猎场个股卡片入口行（2026-09-09 需求 3，17:15 按用户反馈收敛）
 *
 * 入口收敛为三个，且**无数据不出现**：
 * - **消息**（事件 + 资讯合并为一个）→ 弹通用详情弹窗（feed 列表），**不跳转**；
 *   该股既无关联事件也无资讯时整个按钮不渲染（避免"点了只有一句暂无"的噪音）。
 * - **资金** → 弹窗内展示资金图（`kind: "capital"`，复用工作台组件），**不跳转**；
 *   **梯队** → 跳题材页（题材页仍是独立功能面，这一条保留跳转）。
 *   两者都阻止冒泡（卡片整块可点会带走路由）。
 *
 * 冒泡纪律：CardShell 整卡 onClick=stockNav，任何子元素按钮一律 stopAnd()。
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { getEventsForSymbol } from "@/lib/api";
import { useDetailModal } from "@/components/detail/detail-modal";
import { themesUrl } from "@/lib/routing";

const BTN =
  "card-entry-action";

/** 预检缓存：同一 symbol 页面内只探一次（卡片多，避免请求放大；刷新即失效）。 */
const hasFeedCache = new Map<string, boolean>();

export function CardEntryRow({
  symbol,
  name,
  theme,
}: {
  symbol: string;
  name?: string | null;
  theme?: string | null;
}) {
  const router = useRouter();
  const { open } = useDetailModal();
  const [hasFeed, setHasFeed] = useState<boolean | null>(null);

  // 切股 → 当帧用缓存值或「待定」（渲染期 adjust-state；原为 effect 内同步 setState，
  // 会残留上一只的入口一帧，且触发 react-hooks/set-state-in-effect，P1-27）
  const [prevSymbol, setPrevSymbol] = useState<string | null>(null);
  if (symbol !== prevSymbol) {
    setPrevSymbol(symbol);
    const hit = hasFeedCache.get(symbol);
    setHasFeed(hit === undefined ? null : hit);
  }

  // 预检：有事件或资讯才显示「消息」入口（用户要求：没有就不出现）
  useEffect(() => {
    let alive = true;
    if (hasFeedCache.get(symbol) !== undefined) return; // 缓存命中已在渲染期填好
    (async () => {
      try {
        // ⚠️ 只用快接口预检（events 0.02s）；news/digest 实测 ~32s，
        // 拿它做预检会让按钮几十秒后才出现（2026-09-09 实测）。
        const ev = await getEventsForSymbol(symbol);
        const has = (ev.items?.length ?? 0) > 0;
        hasFeedCache.set(symbol, has);
        if (alive) setHasFeed(has);
      } catch {
        if (alive) setHasFeed(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [symbol]);

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
          onClick={stopAnd(() => router.push(themesUrl(theme)))}
          title={`打开题材页：${theme} 梯队`}
        >
          <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 19h5v-5H4zM9 14h5V9H9zM14 9h5V4h-5z" /></svg>梯队
        </button>
      )}
    </div>
  );
}
