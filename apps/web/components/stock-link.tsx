"use client";

import Link from "next/link";
import { useCallback } from "react";
import { workbenchUrlWithBack } from "@/lib/routing";
import { symbolDetailClick, useSymbolDetail } from "@/components/detail/symbol-detail-context";

/**
 * 行级整行打开详情。
 * 用法：`<tr onClick={stockNav(r.symbol)} className="cursor-pointer ...">`。
 * 行内名字的 StockLink 保留（同一个弹窗，重复触发无害——payload 相同则
 * 只是再次 setState；行内其他可点击元素自行 stopPropagation）。
 *
 * 2026-09-15 详情弹窗化：此前是 `router.push(workbenchUrlWithBack(symbol))`，
 * 整行点击会**离开当前页**跳到工作台；现改为就地弹窗。
 */
export function useStockRowNav() {
  const { open } = useSymbolDetail();
  return useCallback((symbol: string) => () => open({ symbol }), [open]);
}

/**
 * 全站统一「个股跳转链接」（2026-09-07 用户需求：各板块出现的每只个股均可点击
 * 查看详情；题材卡/涨停池/龙虎榜/板块成员/新闻正文等共用）。
 *
 * 交互规范（用户 2026-09-07 定稿 + 2026-09-15 弹窗化）：
 * - 一律不用下划线；可点击性用 hover 轻量效果提示——底色洗染 + 文字提亮；
 * - hover 双主题适配：light = text-sky-700 + bg-sky-500/10；dark = text-sky-300 +
 *   同底色。对比度适中、不过分强烈，明确告知「已悬停」；
 * - **点击 = 就地弹窗**（`symbol-detail-modal`），不再跳转工作台——用户不丢当前
 *   页面的列表/tab 上下文，看完关掉即可；
 * - **href 保留** `workbenchUrlWithBack(symbol)`：右键「在新标签打开」、
 *   中键、复制链接、以及 `⌘/Ctrl/Shift + 点击` 仍走真实 URL（工作台深链）。
 *   这是"改点击行为、不废链接"——既有分享链接与 /stock 中转页继续有效，
 *   且 `a[href^="/workbench?symbol="]` 这类既有断言不受影响；
 * - 路由规范见 lib/routing.ts：不手拼字符串（2026-08-31 跨页联动 bug 教训）。
 */
export function StockLink({
  symbol,
  className = "",
  title = "查看个股详情",
  children,
}: {
  symbol: string;
  className?: string;
  title?: string;
  children: React.ReactNode;
}) {
  const { open } = useSymbolDetail();
  return (
    <Link
      href={workbenchUrlWithBack(symbol)}
      title={title}
      onClick={symbolDetailClick(open, { symbol })}
      className={`cursor-pointer rounded transition-colors hover:bg-sky-500/10 hover:text-sky-700 dark:hover:text-sky-300 ${className}`}
    >
      {children}
    </Link>
  );
}
