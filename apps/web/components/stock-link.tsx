"use client";

import Link from "next/link";
import { workbenchUrlWithBack } from "@/lib/routing";

/**
 * 全站统一「个股跳转链接」（2026-09-07 用户需求：各板块出现的每只个股均可点击
 * 跳转工作台并显示该股详情；题材卡/涨停池/龙虎榜/板块成员/新闻正文等共用）。
 *
 * 交互规范（用户 2026-09-07 定稿）：
 * - 一律不用下划线；可点击性用 hover 轻量效果提示——底色洗染 + 文字提亮；
 * - hover 双主题适配：light = text-sky-700 + bg-sky-500/10；dark = text-sky-300 +
 *   同底色。对比度适中、不过分强烈，明确告知「已悬停」；
 * - 跨页跳转一律走 workbenchUrlWithBack（带 from），工作台可一键返回来源页；
 * - 路由规范见 lib/routing.ts：不手拼字符串（2026-08-31 跨页联动 bug 教训）。
 */
export function StockLink({
  symbol,
  className = "",
  title = "查看个股详情（工作台）",
  children,
}: {
  symbol: string;
  className?: string;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={workbenchUrlWithBack(symbol)}
      title={title}
      className={`cursor-pointer rounded transition-colors hover:bg-sky-500/10 hover:text-sky-700 dark:hover:text-sky-300 ${className}`}
    >
      {children}
    </Link>
  );
}

/**
 * 通用可点击元素 hover（非个股：新闻标题/事件标题/操作按钮等）：
 * 中性底色洗染 + 文字加深/提亮，light/dark 各自适配，同样不用下划线。
 */
export const HOVER_SOFT =
  "cursor-pointer rounded transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100";
