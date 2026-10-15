"use client";

/**
 * 猎场个股卡片入口行（2026-09-09 需求 3，17:15 按用户反馈收敛）
 *
 * 入口收敛为依据、资金、梯队，依据按需读取，缺失和失败分别说明：
 * - **依据** → 当前页按需读取事件解释与原入选引用；候选卡片不再逐股轮询。
 * - **资金** → 弹窗内展示资金图（`kind: "capital"`，复用工作台组件），**不跳转**；
 *   **梯队** → 原地题材旁览，保留当前候选。
 *   两者都阻止冒泡（卡片整块可点会带走路由）。
 *
 * 冒泡纪律：CardShell 整卡 onClick=stockNav，任何子元素按钮一律 stopAnd()。
 */
import type {EventInterpretationRef, SelectionEntry} from "@/lib/api";
import { useDetailModal } from "@/components/detail/detail-modal";
import { useInspection } from "@/components/inspection/inspection-context";
import { motionOrigin } from "@/lib/surface-motion";

const BTN =
  "card-entry-action";

export function CardEntryRow({
  symbol,
  name,
  theme,
  eventRefs,
  selectionEntry,
}: {
  symbol: string;
  name?: string | null;
  theme?: string | null;
  eventRefs?: EventInterpretationRef[];
  selectionEntry?: SelectionEntry | null;
}) {
  const { open } = useDetailModal();
  const { open: inspect } = useInspection();

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
        <button
          type="button"
          className={BTN}
          onClick={stopAnd(() =>
            open({ kind: "attribution", title: `${name ?? symbol} · 驱动依据`, symbol, theme: theme ?? null, eventRefs, selectionEntry }),
          )}
          title="在当前页核对最新事件及原入选引用，不改写首次加入依据"
        >
          <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M5 5h14v14H5zM8 9h8M8 13h6" /></svg>关联消息
        </button>
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
