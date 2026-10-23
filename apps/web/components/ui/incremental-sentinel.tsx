"use client";

import type React from "react";

/**
 * 增量列表的状态行（哨兵 + "已加载 X / Y"）。
 *
 * 与 `hooks/use-incremental` 配对：哨兵是 0 高度 div，滚动进视口底部即触发追加载。
 * 把文案收口在这里，避免每个列表各写一套（此前 board-flow 独有，其余页面无提示，
 * 用户看到"列表变短了"却不知道可以继续滚）。
 */
export function IncrementalSentinel({
  sentinelRef,
  visible,
  total,
  unit = "条",
  testId,
  onLoadMore,
}: {
  sentinelRef: React.RefObject<HTMLDivElement | null>;
  visible: number;
  total: number;
  unit?: string;
  testId?: string;
  onLoadMore?: () => void;
}) {
  return (
    <>
      <div ref={sentinelRef} className="h-px" data-testid={testId} />
      {visible < total ? (
        <p className="py-2 text-center text-[10px] text-zinc-600 dark:text-zinc-400">
          已加载 {visible} / {total} {unit}，滚动加载更多
          {onLoadMore && <button type="button" onClick={onLoadMore} title="显示已读取列表的下一部分，不重新请求数据"
            className="ml-2 inline-flex min-h-8 items-center rounded border border-zinc-300 px-2 text-xs text-zinc-700 focus-visible:outline-2 focus-visible:outline-offset-2 dark:border-zinc-600 dark:text-zinc-200">继续加载</button>}
        </p>
      ) : (
        total > 0 && (
          <p className="py-2 text-center text-[10px] text-zinc-600 dark:text-zinc-400">
            已全部加载（{total} {unit}）
          </p>
        )
      )}
    </>
  );
}
