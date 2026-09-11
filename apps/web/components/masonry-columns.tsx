"use client";

/**
 * 卡片墙容器（2026-09-10 重写为**原生 CSS 多列**，v6）。
 *
 * 为什么放弃 JS 瀑布流：连续四版都栽在同一个结构矛盾上——
 * 「重排会改变测量，测量又决定重排」。只要布局由 JS 计算并写回，就必然存在
 * 「测量 → 决策 → DOM 变化 → 再测量」的环，只能靠各种守卫去压制，结果是
 * 要么死循环、要么把错误布局冻结、要么反复换列导致**视觉闪烁**：
 *
 * - v1 `current` 进 effect 依赖 → 测量反馈环 → `/hunting` 被错误边界接管
 *   （Maximum update depth exceeded）。
 * - v2 「每代只决策一次」压制循环 → 循环停了，但把内容未稳定时的失真高度
 *   算出的错误布局**永久冻结**：实测 8 卡分成 4/2/2，首列 1937px、另两列 863px，
 *   第 4 张排在 y=1858 而上方留出 538px 空洞（用户反馈「明明有位置却在下方」）。
 * - v3 补观察器 + 幂等 bail out，但观察的是卡片节点：卡片换列 = 跨父节点移动，
 *   React 会卸载重建节点，观察器随即失明，且缓存引用读到的是脱离文档的节点
 *   （`getBoundingClientRect()` 恒为 0）→ 系统性错排。
 * - v4 为补观察盲区把 `layout` 加进依赖 → 循环原样回归。
 * - v5 改为观察列容器 + 不可信读数不决策 → 不炸了，但换列触发的列高变化会再次
 *   触发重排，卡片不断重建 ⇒ **一直闪**（用户反馈「一闪一闪的」）。
 *
 * 结构性解法：把布局交给浏览器。CSS 多列（`column-count` + `break-inside: avoid`）
 * 由排版引擎按高度均衡分列，**没有 JS 测量、没有 state、没有卡片搬家**，因此
 * 不存在反馈环，也不可能闪烁；内容异步变高时浏览器自动重新均衡，无需任何补偿逻辑。
 * 净减少约 100 行易错代码。
 *
 * 取舍（已知且有意为之）：
 * - 阅读顺序为**列优先**（先填满第一列再填第二列），与「行优先」的 JS 版本不同；
 *   卡片墙（Pinterest / 小红书式）惯例即如此。
 * - 分列由浏览器均衡算法决定，不保证「最高列最矮」的数学最优，但无空洞且稳定。
 *
 * 断点与历史行为保持一致：<768 单列、<1280 双列、≥1280 三列。
 */
import { ReactNode } from "react";

export function MasonryColumns({
  children,
  gap = "gap-3",
}: {
  children: ReactNode;
  gap?: string;
}) {
  const kids = Array.isArray(children) ? children : [children];
  // 间距：与旧 API 保持兼容（调用方传的是 Tailwind 类名，这里只取间距像素值）
  const gapPx = gap === "gap-2" ? 8 : 12;

  return (
    <div
      className="columns-1 md:columns-2 xl:columns-3"
      style={{ columnGap: gapPx }}
      data-testid="masonry"
    >
      {kids.map((kid, i) => (
        // breakInside: avoid —— 卡片不允许被列边界切断
        // marginBottom 即行间距（多列布局里用间距类名不生效）
        <div
          key={i}
          data-idx={i}
          style={{ breakInside: "avoid", marginBottom: gapPx }}
        >
          {kid}
        </div>
      ))}
    </div>
  );
}
