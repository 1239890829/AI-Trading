"use client";

import Link from "next/link";
import { inspectionClick, useInspection } from "@/components/inspection/inspection-context";
import { useMemo, useState } from "react";
import { useExitPresence } from "@/hooks/use-exit-presence";
import type { StockThemes } from "@/lib/api";
import { pctColor, pctText } from "@/lib/format";
import { themesUrl } from "@/lib/routing";

/** 来源徽标语义（architecture-design §1）：官方成分=结构性归属，涨停归因=行为性归属，人工=override。 */
const SOURCE_LABEL: Record<string, string> = {
  ths_official: "官方成分",
  manual: "人工",
};

/** 官方成分默认展示条数（2026-09-01 用户反馈：全量展示会把分时/K线挤下去）。 */
const OFFICIAL_TOP_N = 6;

/** One link shape for source-labelled membership and existing company concepts. */
export function ThemeLinkChip({name, title, attributed = false, children}: {name: string; title: string; attributed?: boolean; children?: React.ReactNode}) {
  const {open: inspect} = useInspection();
  return <Link href={themesUrl(name)} onClick={inspectionClick(inspect, {kind: "themes", focus: name})} title={title} aria-label={name}
    className={`inline-flex max-w-full items-center rounded border px-1.5 py-0.5 text-zinc-700 dark:text-zinc-200 [overflow-wrap:anywhere] ${attributed ? "border-amber-500/30 bg-amber-500/5 hover:border-amber-500/60 dark:border-amber-400/30 dark:bg-amber-400/5" : "border-sky-500/30 bg-sky-500/5 hover:border-sky-500/60 dark:border-sky-400/30 dark:bg-sky-400/5"}`}>
    <span className="min-w-0">{name}</span>{children}
  </Link>;
}

export function CompanyConceptChips({names, heading = true}: {names: string[]; heading?: boolean}) {
  if (!names.length) return null;
  return <div className="space-y-1.5 text-xs">{heading && <h3 className="text-zinc-600 dark:text-zinc-400">公司资料概念</h3>}<div className="flex flex-wrap gap-1">{names.map(name => <ThemeLinkChip key={name} name={name} title="公司资料中的概念标签；不证明今日涨停或业务受益，查看现有题材看板" />)}</div></div>;
}

// 本地 pctText 已删（2026-09-11，P2-20 / 冗余清理）：改用 lib/format.ts 的权威实现。
// 原实现在 `pct == null` 时返回**空串**、而权威实现返回 `--`——违反项目三态纪律
// （缺失必须显式标注，不用空白冒充）。之所以是**零可见变更**：唯一调用点外面有
// `t.theme_chg_1d != null &&` 守卫，null 分支根本到不了；且 |Δ|<1000 时
// toFixed(2) 与 fmt(v,2)（zh-CN 千分位）输出逐字节相同。

/**
 * 题材归属行（L4 联动）：个股 → 题材看板。
 *
 * 2026-09-01 二次改版（用户反馈 #2）：排序依据从「板块涨跌幅大小」改为
 * **与今日整体涨跌行情的联动度**（theme_align_1d = 方向一致家数占比，只看
 * 方向不看数值幅度）——「今天行情最相关的题材排前列」；联动度不可得的
 * 退回按板块涨跌幅降序。tag 内涨跌幅徽标保留（红涨绿跌）仅作展示。
 * - 默认只展示前 6 个，其余折叠——避免全量铺开把分时/K线挤出可视区；
 * - 涨停归因（行为性归属）排官方成分之后，同样参与折叠。
 * 双源并列展示、互不覆盖的原则不变；无归属时返回 null（零占用）。
 */
export function ThemeChipsRow({themes, companyNames = []}: {themes: StockThemes | null; companyNames?: string[]}) {
  const [expanded, setExpanded] = useState(false);
  const presence = useExitPresence(expanded ? true : null);
  const entries = useMemo(() => {
    const result = new Map<string, {name: string; sources: string[]; change: number | null; attributed: boolean}>();
    const add = (name: string, source: string, change: number | null = null, attributed = false) => {
      const key = name.trim(); if (!key) return;
      const item = result.get(key) || {name: key, sources: [], change: null, attributed: false};
      if (!item.sources.includes(source)) item.sources.push(source);
      if (item.change === null && typeof change === "number" && Number.isFinite(change)) item.change = change;
      item.attributed ||= attributed; result.set(key, item);
    };
    const official = [...(themes?.official || [])].sort((a, b) => (b.theme_align_1d ?? -1) - (a.theme_align_1d ?? -1) || (b.theme_chg_1d ?? -Infinity) - (a.theme_chg_1d ?? -Infinity));
    for (const item of official) add(item.theme_name, SOURCE_LABEL[item.source] || item.source, item.theme_chg_1d ?? null);
    for (const item of themes?.attribution || []) add(item.theme_name, `${item.date} 来源归因`, null, true);
    for (const name of companyNames) add(name, "公司资料");
    return [...result.values()];
  }, [themes, companyNames]);
  if (!entries.length) return null;
  const visible = entries.slice(0, OFFICIAL_TOP_N), hidden = entries.slice(OFFICIAL_TOP_N);
  const render = (item: typeof entries[number]) => <ThemeLinkChip key={item.name} name={item.name} attributed={item.attributed}
    title={`${item.sources.join(" · ")}；概念归属不证明当日催化或个股受益`}>
    {item.change !== null && <span className={`ml-1 font-mono tabular-nums ${pctColor(item.change)}`}>{pctText(item.change)}</span>}
    <span className="ml-1 text-[10px] text-zinc-600 dark:text-zinc-400">{item.sources.map(value => value.endsWith("来源归因") ? "归因" : value === "官方成分" ? "官方" : value === "公司资料" ? "资料" : value).join(" / ")}</span>
  </ThemeLinkChip>;
  return <div className="space-y-1.5 text-xs" aria-label="概念题材">
    <div className="flex items-center justify-between gap-2"><h3 className="text-zinc-600 dark:text-zinc-400">概念题材</h3>{hidden.length > 0 && <button type="button" data-action="quiet" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? "收起" : `＋${hidden.length} 个`}</button>}</div>
    <div className="flex flex-wrap gap-1.5">{visible.map(render)}</div>
    {presence.value && <div className="theme-chips-extras motion-disclosure" data-open={expanded} aria-hidden={!expanded || undefined} inert={!expanded}><div className="motion-disclosure-body"><div className="flex flex-wrap gap-1.5 pt-1.5">{hidden.map(render)}</div></div></div>}
  </div>;
}
