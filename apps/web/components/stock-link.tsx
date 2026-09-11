"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback } from "react";
import { workbenchUrlWithBack } from "@/lib/routing";

/**
 * 行级整行跳转（2026-09-08 用户反馈：列表里应该整个行都能点，而不是只能点名字）。
 * 用法：`<tr onClick={stockNav(r.symbol)} className="cursor-pointer ...">`。
 * 行内名字的 StockLink 保留（同 URL，重复导航无害）；行内其他可点击元素
 * （详情按钮等）自行 stopPropagation。
 */
export function useStockRowNav() {
  const router = useRouter();
  return useCallback(
    (symbol: string) => () => {
      void router.push(workbenchUrlWithBack(symbol));
    },
    [router],
  );
}

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
