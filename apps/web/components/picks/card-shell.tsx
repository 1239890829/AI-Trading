"use client";

import type { ReactNode } from "react";

import { StockLink } from "@/components/stock-link";

/**
 * 选股类卡片的共享布局壳（2026-09-08 猎场批次①，docs/system-audit-20260908.md §3.3）。
 *
 * PickCard（每日精选）与 TopDetailCard（盘中跟踪详情）此前各自手写同构的
 * 「圆角外框 + 名称代码头部」，结构漂移只能靠肉眼对齐。抽出两个最小单元：
 * - CardShell：圆角外框；`flow` 打开时附带瀑布流 margin（CSS columns 单元）。
 * - CardHead：头部行——左侧名称+代码（可选 StockLink 跳工作台），右侧内容槽
 *   （精选放价格/涨跌幅，跟踪放 T 档徽标/涨跌幅，各卡自定）。
 *
 * 只抽「壳」，不抽「内容」：两卡的分节（评分条/出场纪律 vs 判定徽标/入选理由）
 * 语义差异大，强行合并会把条件渲染做成 5 处分支，可读性反而崩（审查 §3.3 结论）。
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
      <span className="ml-1.5 font-mono text-[10px] text-zinc-400">{symbol}</span>
    </>
  );
  return (
    <div className="flex items-baseline justify-between gap-2">
      <div>{link ? <StockLink symbol={symbol}>{label}</StockLink> : label}</div>
      <div className="text-right">{right}</div>
    </div>
  );
}
