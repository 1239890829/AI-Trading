"use client";

import Link from "next/link";
import type { StockThemes } from "@/lib/api";

/** 来源徽标语义（linkage-design §3.2）：官方成分=结构性归属，涨停归因=行为性归属，人工=override。 */
const SOURCE_LABEL: Record<string, string> = {
  ths_official: "官方成分",
  manual: "人工",
};

/**
 * 题材归属行（L4 联动）：个股 → 题材看板。
 *
 * 双源并列展示、互不覆盖——一只票既可能常年属于某官方概念，
 * 又可能因当日涨停被归因进另一题材，两者都值得点进看板核对。
 * 无归属时返回 null（零占用）；归属为空 ≠ 数据缺失提示，不渲染占位。
 */
export function ThemeChipsRow({ themes }: { themes: StockThemes | null }) {
  if (!themes) return null;
  const official = themes.official ?? [];
  const attribution = themes.attribution ?? [];
  if (official.length === 0 && attribution.length === 0) return null;

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs">
      <span className="shrink-0 text-zinc-400">题材归属</span>
      {official.map((t) => (
        <Link
          key={`o-${t.theme_code}`}
          href={`/tape?tab=themes&focus=${encodeURIComponent(t.theme_name)}`}
          title={`THS 官方概念成分（${t.theme_code}）· 点击查看该题材当下梯队`}
          className="inline-flex items-center rounded border border-sky-500/30 bg-sky-500/5 px-1.5 py-0.5 text-zinc-700 hover:border-sky-500/60 dark:border-sky-400/30 dark:bg-sky-400/5 dark:text-zinc-200"
        >
          {t.theme_name}
          <span className="ml-1 rounded bg-sky-500/10 px-1 text-[10px] text-sky-600 dark:text-sky-300">
            {SOURCE_LABEL[t.source] ?? "官方成分"}
          </span>
        </Link>
      ))}
      {attribution.map((a) => (
        <Link
          key={`a-${a.theme_name}`}
          href={`/tape?tab=themes&focus=${encodeURIComponent(a.theme_name)}`}
          title={`${a.date} 涨停归因（来自涨停原因原文）· 点击查看该题材当下梯队`}
          className="inline-flex items-center rounded border border-amber-500/30 bg-amber-500/5 px-1.5 py-0.5 text-zinc-700 hover:border-amber-500/60 dark:border-amber-400/30 dark:bg-amber-400/5 dark:text-zinc-200"
        >
          {a.theme_name}
          <span className="ml-1 rounded bg-amber-500/10 px-1 text-[10px] text-amber-700 dark:text-amber-300">
            涨停归因
          </span>
        </Link>
      ))}
    </div>
  );
}
