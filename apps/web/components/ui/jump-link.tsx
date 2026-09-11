"use client";

import Link from "next/link";
import type { ReactNode } from "react";

/**
 * 跳转入口 pill（P1-18，2026-09-10）。
 *
 * 起因（2026-09-09 系统审查）：「题材页↗」「成分↗」原样式为
 * `text-[10px] text-zinc-600 dark:text-zinc-400` + 无底色 —— 视觉上与灰色注释文字无异，
 * 用户扫过去认不出是可点的入口（审查原话：跳转按钮可发现性偏弱）。
 *
 * 「看起来可点击」需要三个信号同时成立，缺一个就退化成注释：
 *   ① 底色/边框与周围文本形成块状边界（0.5px 灰字永远读成说明）；
 *   ② 字号 ≥11px（10px 在「中文 + 符号」混排下已接近注释尺寸）；
 *   ③ hover 有明确颜色反馈（否则划过没有「我点得到」的确认）。
 *
 * **适用范围（勿无差别套用）**：只用于**内容行内**的跳转入口 ——
 * 它们与一堆 chips 挤在同一行，弱小就会被读成注释。
 * `Panel` 标题右侧的 `extra` 位跳转（如市场页「资金详情↗ / 全部 ↗」）
 * **刻意保持低调文字链接**：那是面板的附属出口，强 pill 化会与标题抢注意力。
 *
 * 用法：跨页跳转用 `<JumpLink>`；同页开弹窗等非 `<a>` 场景把
 * `JUMP_PILL_CLASS` 套在 `<button>` 上（同一常量，避免样式各写各的漂移）。
 */
export const JUMP_PILL_CLASS =
  "inline-flex shrink-0 items-center gap-0.5 rounded-md border border-zinc-300 " +
  "bg-zinc-50 px-1.5 py-0.5 text-[11px] font-medium text-zinc-600 dark:text-zinc-400 transition-colors " +
  "hover:border-sky-400 hover:bg-sky-50 hover:text-sky-700 " +
  "dark:border-zinc-600 dark:bg-zinc-800/60 dark:text-zinc-300 " +
  "dark:hover:border-sky-500 dark:hover:bg-sky-500/10 dark:hover:text-sky-300";

export function JumpLink({
  href,
  title,
  children,
  onClick,
}: {
  href: string;
  title?: string;
  children: ReactNode;
  onClick?: () => void;
}) {
  return (
    <Link href={href} title={title} onClick={onClick} className={JUMP_PILL_CLASS}>
      {children}
    </Link>
  );
}
