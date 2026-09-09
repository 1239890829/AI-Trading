"use client";

/**
 * 瀑布流容器 v2（2026-09-09 用户二次反馈：round-robin 仍不齐）。
 *
 * 两阶段布局：
 * 1. 首帧 round-robin 分列（保证每卡都有归宿）；
 * 2. useLayoutEffect 测量每张卡实际高度（data-idx 标记原始索引）→
 *    **贪心重排**：按原始顺序依次放入当前最矮列 → 与当前布局不同才 setState。
 *
 * 收敛性：卡片宽度相同（flex-1 列等宽）→ 高度与所在列无关 → 第二次测量
 * 得到同样的贪心解 → 不再 setState，稳定收敛。
 *
 * 已知取舍：首帧到测量完成之间有一次布局跳变（同帧内完成，用户不可见）；
 * 卡片高度随内容异步变化（图片加载）不会自动重排——本页卡片无大图，可接受。
 */
import { ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

function columnCount(w: number): number {
  if (w < 768) return 1;
  if (w < 1280) return 2;
  return 3;
}

function roundRobin(count: number, cols: number): number[][] {
  const buckets: number[][] = Array.from({ length: cols }, () => []);
  for (let i = 0; i < count; i += 1) buckets[i % cols].push(i);
  return buckets;
}

/** 贪心：原始顺序依次放入当前最矮列（高度来自实测）。 */
function greedyAssign(heights: number[], cols: number, gapPx: number): number[][] {
  const colH = new Array(cols).fill(0);
  const assign: number[][] = Array.from({ length: cols }, () => []);
  heights.forEach((h, idx) => {
    let target = 0;
    for (let c = 1; c < cols; c += 1) if (colH[c] < colH[target]) target = c;
    assign[target].push(idx);
    colH[target] += h + gapPx;
  });
  return assign;
}

function flatten(l: number[][]): number[] {
  return l.flat();
}

export function MasonryColumns({
  children,
  gap = "gap-3",
}: {
  children: ReactNode;
  gap?: string;
}) {
  const kids = Array.isArray(children) ? children : [children];
  const count = kids.length;
  const [cols, setCols] = useState<number | null>(null);
  const [layout, setLayout] = useState<number[][] | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const gapPx = gap === "gap-2" ? 8 : 12;

  useEffect(() => {
    const update = () => setCols(columnCount(window.innerWidth));
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  // 列数/卡片数变化 → 重置为 round-robin，重新测量
  useEffect(() => {
    setLayout(null);
  }, [cols, count]);

  // 2026-09-09 用户反馈：移除「列数 ≤ ⌈卡数/2⌉」上限——该上限让少卡时退化为
  // 单列独占整行（2 卡=1 列），与「铺满多列」预期相反。实际列数由下方
  // 「最高列最矮」自动选择决定：列数只有在真正降低总高时才会增加。
  const maxCols = cols;
  const current = layout ?? roundRobin(count, maxCols ?? 1);

  useLayoutEffect(() => {
    if (maxCols == null || count === 0) return;
    const root = containerRef.current;
    if (!root) return;
    const els = root.querySelectorAll<HTMLElement>("[data-idx]");
    if (els.length !== count) return; // 未渲染全，下一轮再测
    const heights = Array.from(els).map((el) => el.getBoundingClientRect().height);
    // 自动选列数：对 1..maxCols 各跑贪心，取「最高列最矮」的方案
    // （瀑布流目标=整体最紧凑；注意不能用「列高差」——单列差恒为 0 必胜但总高最大。
    //   高度按当前列宽实测，列数不同宽度略异——贪心对近似高度鲁棒）
    let best: number[][] | null = null;
    let bestMax = Number.POSITIVE_INFINITY;
    for (let c = 1; c <= maxCols; c += 1) {
      const assign = greedyAssign(heights, c, gapPx);
      const colHeights = assign.map((col) => col.reduce((sum, idx) => sum + heights[idx] + gapPx, 0));
      const maxH = Math.max(...colHeights);
      if (maxH < bestMax) {
        bestMax = maxH;
        best = assign;
      }
    }
    if (best && JSON.stringify(best) !== JSON.stringify(current)) setLayout(best);
  }, [cols, count, current, gapPx, maxCols]);

  return (
    <div ref={containerRef} className={`flex items-start ${gap}`}>
      {current.map((colIdxs, ci) => (
        <div key={ci} className="min-w-0 flex-1 space-y-3">
          {colIdxs.map((idx) => (
            <div key={idx} data-idx={idx}>
              {kids[idx]}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
