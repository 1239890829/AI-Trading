"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { StockThemeLink, StockThemes } from "@/lib/api";
import { pctColor } from "@/lib/format";

/** 来源徽标语义（linkage-design §3.2）：官方成分=结构性归属，涨停归因=行为性归属，人工=override。 */
const SOURCE_LABEL: Record<string, string> = {
  ths_official: "官方成分",
  manual: "人工",
};

/** 官方成分默认展示条数（2026-09-01 用户反馈：全量展示会把分时/K线挤下去）。 */
const OFFICIAL_TOP_N = 6;

function pctText(pct: number | null | undefined): string {
  if (pct == null) return "";
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

/**
 * 题材归属行（L4 联动）：个股 → 题材看板。
 *
 * 2026-09-01 改版（用户反馈）：
 * - 官方成分按**官方板块指数当日涨跌幅降序**排前（当天炒什么一目了然），
 *   tag 内直接带涨跌幅（红涨绿跌）——口径与概念目录同源（THS 板块指数），杜绝跨源名称匹配；
 * - 默认只展示涨幅最相关的前 6 个，其余折叠进「展开全部」——避免 29 个概念
 *   全量铺开把分时/K线挤出可视区；涨跌幅拉取失败的排在有值的后面；
 * - 涨停归因（行为性归属）排官方成分之后，同样参与折叠。
 * 双源并列展示、互不覆盖的原则不变；无归属时返回 null（零占用）。
 */
export function ThemeChipsRow({ themes }: { themes: StockThemes | null }) {
  const [expanded, setExpanded] = useState(false);
  const official: StockThemeLink[] = useMemo(() => {
    const list = [...(themes?.official ?? [])];
    // 有涨跌幅的按降序在前；无值（拉取失败/新题材）排在有值之后保持原序
    const withPct = list
      .filter((t) => t.theme_chg_1d != null)
      .sort((a, b) => (b.theme_chg_1d ?? 0) - (a.theme_chg_1d ?? 0));
    const without = list.filter((t) => t.theme_chg_1d == null);
    return [...withPct, ...without];
  }, [themes]);
  const attribution = themes?.attribution ?? [];

  if (!themes || (official.length === 0 && attribution.length === 0)) return null;

  const collapsedCount = official.length + attribution.length;
  const showAll = expanded || collapsedCount <= OFFICIAL_TOP_N;
  const visibleOfficial = showAll ? official : official.slice(0, OFFICIAL_TOP_N);
  const visibleAttribution = showAll ? attribution : [];
  const hidden = collapsedCount - visibleOfficial.length - visibleAttribution.length;

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs">
      <span className="shrink-0 text-zinc-400">题材归属</span>
      {visibleOfficial.map((t) => (
        <Link
          key={`o-${t.theme_code}`}
          href={`/tape?tab=themes&focus=${encodeURIComponent(t.theme_name)}`}
          title={`THS 官方概念成分（${t.theme_code}）· 板块当日涨跌幅 · 点击查看该题材当下梯队`}
          className="inline-flex items-center rounded border border-sky-500/30 bg-sky-500/5 px-1.5 py-0.5 text-zinc-700 hover:border-sky-500/60 dark:border-sky-400/30 dark:bg-sky-400/5 dark:text-zinc-200"
        >
          {t.theme_name}
          {t.theme_chg_1d != null && (
            <span className={`ml-1 font-mono tabular-nums ${pctColor(t.theme_chg_1d)}`}>
              {pctText(t.theme_chg_1d)}
            </span>
          )}
        </Link>
      ))}
      {visibleAttribution.map((a) => (
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
      {hidden > 0 && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="rounded border border-zinc-200 px-1.5 py-0.5 text-[11px] text-zinc-400 hover:text-zinc-700 dark:border-zinc-700 dark:hover:text-zinc-200"
          title={expanded ? "收起，只显示涨跌幅最相关的题材" : "展开全部归属题材"}
        >
          {expanded ? "收起" : `＋${hidden} 个`}
        </button>
      )}
    </div>
  );
}
