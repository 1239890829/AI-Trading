"use client";

/**
 * 瀑布流容器（2026-09-09 用户反馈：CSS columns 有空位/留白空洞）。
 *
 * CSS columns 的机理是**垂直切分**内容到各列：卡片高度不均时最后列尾部出现
 * 整块空洞，break-inside-avoid 大卡被推下时上方也留白。本组件改为
 * round-robin 均衡分配——每张卡片进一个独立 flex 列（i % 列数），
 * 每列是完整文档流：不再有被推下的大空洞。
 *
 * 列数响应式：<768 → 1；<1280 → 2；否则 3（与原 md:/xl: 断点一致）。
 * 限制：round-robin 不做高度测量（零依赖零抖动），极端高度差下列尾仍可能
 * 有小空隙——消的是「整块空洞」，不是像素级 masonry。
 */
import { ReactNode, useEffect, useState } from "react";

function columnCount(w: number): number {
  if (w < 768) return 1;
  if (w < 1280) return 2;
  return 3;
}

export function MasonryColumns({
  children,
  gap = "gap-3",
}: {
  children: ReactNode;
  gap?: string;
}) {
  const [cols, setCols] = useState<number | null>(null);

  useEffect(() => {
    const update = () => setCols(columnCount(window.innerWidth));
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  // SSR/首帧前不渲染（与原 columns 布局一致的渐进呈现）
  const n = cols ?? 1;
  const buckets: ReactNode[][] = Array.from({ length: n }, () => []);
  let i = 0;
  for (const child of Array.isArray(children) ? children : [children]) {
    buckets[i % n].push(child);
    i += 1;
  }

  return (
    <div className={`flex items-start ${gap}`}>
      {buckets.map((bucket, idx) => (
        <div key={idx} className="min-w-0 flex-1 space-y-3">
          {bucket}
        </div>
      ))}
    </div>
  );
}
