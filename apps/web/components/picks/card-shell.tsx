"use client";

import type { ReactNode } from "react";

import { StockLink } from "@/components/stock-link";

/**
 * 选股类卡片的共享布局壳（2026-09-08 猎场批次①，docs/system-audit-20260908.md §3.3）。
 *
 * - CardShell：圆角外框；`flow` 打开时附带瀑布流 margin（CSS columns 单元）。
 * - CardHead：头部行——左侧名称+代码（可选 StockLink 跳工作台），右侧内容槽
 *   （来源/持仓徽标 + 现价 + 涨跌幅）。
 *
 * 2026-09-10 复核更正：本文件原注释断言「只抽壳不抽内容——两卡分节语义差异大，
 * 强行合并会把条件渲染做成 5 处分支」。该结论**已被推翻**：两卡实为**互补**关系
 * （盘前有评分/估值/买入区间，盘中有辨识度·确定性判定/涨停原因/T 档），
 * 合并通路不是「渲染层堆分支」，而是**在适配器里归一输入**（`pick-card.tsx`
 * 的 `fromDailyPick` / `fromIntradayStock` → `TradingCard`），渲染层只认一种形状。
 * 教训：判断「两个组件该不该合并」时，先看**输入契约**能否归一，再看渲染分支数。
 */

export function CardShell({
  flow = false,
  className = "",
  onClick,
  children,
}: {
  /** 瀑布流单元模式：附加 break-inside-avoid + 底部间距 */
  flow?: boolean;
  className?: string;
  /** 整卡点击（2026-09-09 用户反馈：盘中跟踪卡点击跳工作台）——传入后整卡 cursor-pointer */
  onClick?: () => void;
  children: ReactNode;
}) {
  return (
    <div
      onClick={onClick}
      className={`rounded-xl border border-zinc-200 p-3 dark:border-zinc-800 ${flow ? "mb-3 break-inside-avoid" : ""} ${onClick ? "cursor-pointer transition-colors hover:border-zinc-300 dark:hover:border-zinc-700" : ""} ${className}`}
    >
      {children}
    </div>
  );
}

export function CardHead({
  name,
  symbol,
  link = false,
  right,
}: {
  name: string | null;
  symbol: string;
  /** true 时名称/代码可点 → 工作台详情（瀑布流场景）；弹窗内不需要 */
  link?: boolean;
  right: ReactNode;
}) {
  const label = (
    <>
      <span className="text-sm font-semibold">{name ?? "--"}</span>
      <span className="ml-1.5 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{symbol}</span>
    </>
  );
  return (
    <div className="flex items-baseline justify-between gap-2">
      <div>{link ? <StockLink symbol={symbol}>{label}</StockLink> : label}</div>
      <div className="text-right">{right}</div>
    </div>
  );
}
