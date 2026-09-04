"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { NewsModal, type NewsModalItem } from "@/components/news-modal";
import { Panel } from "@/components/panel";
import { StockPools, directionLabel } from "@/components/event-panel";
import { getImpactEvents, type EventSort, type ImpactEvent } from "@/lib/api";
import { themesUrl, workbenchUrl } from "@/lib/routing";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { Skeleton } from "@/components/ui/loading";

/**
 * 事件 Tab（§六.4 用户拍板 2026-09-04）：云图之后独立视图。
 * 四级分类（国际时事/国家政策/市场热点/原材料涨价）+ 三级影响力（L1/L2/L3）。
 * 2026-09-04 任务②③增强：
 * - 排序 tag 切换：最相关（默认，与盘面/情绪强关联，后端 rank_score 打分）/ 最新 / 影响力；
 *   每条展示排序分与理由（可解释、可追溯，title 悬浮看全部理由）。
 * - 事件标签：业绩/公告/异动/资金/行业（规则派生，对照同花顺常用分类补覆盖），
 *   支持按标签筛选，条目内联标签徽标。
 */

const FOUR_STYLE: Record<string, string> = {
  international: "bg-sky-500/15 text-sky-700 border-sky-500/40 dark:text-sky-300",
  policy: "bg-amber-500/15 text-amber-700 border-amber-500/40 dark:text-amber-300",
  hot: "bg-zinc-500/15 text-zinc-700 border-zinc-500/40 dark:text-zinc-300",
  material: "bg-teal-500/15 text-teal-700 border-teal-500/40 dark:text-teal-300",
};

const LEVEL_STYLE: Record<string, string> = {
  L1: "bg-up/20 text-up border-up/50",
  L2: "bg-zinc-500/15 text-zinc-600 border-zinc-500/40 dark:text-zinc-300",
};

const TAG_STYLE = "bg-indigo-500/10 text-indigo-600 border-indigo-500/30 dark:text-indigo-300";

const FOUR_FILTERS = [
  { key: "all", label: "全部" },
  { key: "material", label: "原材料涨价" },
  { key: "policy", label: "国家政策" },
  { key: "international", label: "国际时事" },
  { key: "hot", label: "市场热点" },
] as const;

const SORTS: { key: EventSort; label: string; title: string }[] = [
  { key: "relevance", label: "最相关", title: "与当日大盘/情绪强关联的事件排前（题材共振+相位加权+影响力+时效）" },
  { key: "time", label: "最新", title: "按发布时间倒序" },
  { key: "impact", label: "影响力", title: "L1 → L2 → L3，同级别按来源分级与时间" },
];

const TAGS = ["业绩", "公告", "异动", "资金", "行业"] as const;

const CHIP =
  "rounded-full border px-2.5 py-0.5 text-xs transition-colors";
const CHIP_ON =
  "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900";
const CHIP_OFF =
  "border-zinc-200 text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100";

export function EventsTab() {
  const [items, setItems] = useState<ImpactEvent[] | null>(null);
  const [countsAll, setCountsAll] = useState<Record<string, number> | null>(null);
  const [tagCounts, setTagCounts] = useState<Record<string, number> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [four, setFour] = useState<string>("all");
  const [tag, setTag] = useState<string>("all");
  const [sort, setSort] = useState<EventSort>("relevance");
  const [modalItem, setModalItem] = useState<NewsModalItem | null>(null);
  const [l1Only, setL1Only] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(async () => {
    const r = await getImpactEvents(false, 100, sort);
    setItems(r.items);
    setCountsAll(r.countsAll);
    setTagCounts(r.tagCounts);
    setError(null);
  }, [sort]);

  usePollingFetch(async () => {
    try {
      await load();
    } catch {
      setError("事件影响力数据加载失败（后端不可达或数据源异常）。");
    }
  }, 60_000);

  // 排序切换立即拉取（usePollingFetch 只在挂载与周期触发，不等 60s 轮询）；
  // ref 守卫跳过挂载首拍（轮询已拉过），只对 sort 变化补拉。
  const prevSort = useRef(sort);
  useEffect(() => {
    if (prevSort.current === sort) return;
    prevSort.current = sort;
    void load();
  }, [sort, load]);

  const shown = (items ?? []).filter(
    (e) =>
      (four === "all" || e.four_category === four) &&
      (tag === "all" || e.tags.includes(tag)) &&
      (!l1Only || e.impact_level === "L1"),
  );

  return (
    <Panel
      title="事件影响力"
      extra={
        <span className="text-[11px] text-zinc-400">
          {countsAll ? `L1 ${countsAll.L1 ?? 0} · L2 ${countsAll.L2 ?? 0} · L3 已滤 ${countsAll.L3 ?? 0}` : ""}
        </span>
      }
      className="h-full min-h-0 overflow-hidden"
    >
      <div className="flex h-full min-h-0 flex-col">
        {/* 第一行：排序 tag 切换 + 只看 L1 */}
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-zinc-100 px-3 py-2 dark:border-zinc-800/60">
          {SORTS.map((s) => (
            <button
              key={s.key}
              onClick={() => setSort(s.key)}
              title={s.title}
              className={CHIP + " " + (sort === s.key ? CHIP_ON : CHIP_OFF)}
            >
              {s.label}
            </button>
          ))}
          <label className="ml-auto flex cursor-pointer items-center gap-1 text-xs text-zinc-500">
            <input type="checkbox" checked={l1Only} onChange={(e) => setL1Only(e.target.checked)} />
            只看 L1
          </label>
        </div>

        {/* 第二行：四级分类 + 事件标签筛选 */}
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-zinc-100 px-3 py-2 dark:border-zinc-800/60">
          {FOUR_FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFour(f.key)}
              className={CHIP + " " + (four === f.key ? CHIP_ON : CHIP_OFF)}
            >
              {f.label}
            </button>
          ))}
          <span className="mx-1 h-4 w-px bg-zinc-200 dark:bg-zinc-700" aria-hidden />
          <button
            onClick={() => setTag("all")}
            className={CHIP + " " + (tag === "all" ? CHIP_ON : CHIP_OFF)}
          >
            标签
          </button>
          {TAGS.map((t) => (
            <button
              key={t}
              onClick={() => setTag(tag === t ? "all" : t)}
              title={`${t}类事件（规则派生）${tagCounts?.[t] != null ? ` · 当前 ${tagCounts[t]} 条` : ""}`}
              className={CHIP + " " + (tag === t ? CHIP_ON : CHIP_OFF)}
            >
              {t}
              {tagCounts?.[t] ? <span className="ml-1 opacity-60">{tagCounts[t]}</span> : null}
            </button>
          ))}
        </div>

        {error && (
          <p className="shrink-0 px-4 py-2 text-xs text-amber-600 dark:text-amber-300">{error}</p>
        )}
        {items === null && !error && (
          <ul className="min-h-0 flex-1 space-y-3 overflow-hidden px-4 py-3" aria-hidden>
            {Array.from({ length: 8 }, (_, i) => (
              <li key={i} className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <Skeleton className="h-3.5 w-8" />
                  <Skeleton className="h-3.5 w-12" />
                  <Skeleton className="h-3.5 flex-1" />
                </div>
                <Skeleton className="h-3 w-2/3" />
              </li>
            ))}
          </ul>
        )}
        {items !== null && shown.length === 0 && !error && (
          <p className="flex-1 px-4 py-6 text-center text-sm text-zinc-400">
            当前筛选下暂无事件（L3 日常经营/人事变动默认不上榜；时效 = 半衰期 × 2）
          </p>
        )}

        <ul className="min-h-0 flex-1 divide-y divide-zinc-100 overflow-y-auto dark:divide-zinc-800">
          {shown.map((e) => (
            <li key={e.id} className="px-4 py-2.5">
              <div className="flex items-baseline gap-2">
                {sort === "relevance" && e.rank_score != null && (
                  <span
                    className="shrink-0 rounded bg-zinc-900 px-1 py-0.5 font-mono text-[10px] font-semibold text-white dark:bg-zinc-100 dark:text-zinc-900"
                    title={(e.rank_reasons ?? []).join("\n")}
                  >
                    {Math.round(e.rank_score)}分
                  </span>
                )}
                <span
                  className={`shrink-0 rounded border px-1 py-0.5 text-[10px] font-semibold ${LEVEL_STYLE[e.impact_level] ?? ""}`}
                  title={e.impact_level === "L1" ? "L1 必上：政策/行业级/国际重大" : "L2 选上：事实类+有标的链"}
                >
                  {e.impact_level}
                </span>
                <span
                  className={`shrink-0 rounded border px-1 py-0.5 text-[10px] ${FOUR_STYLE[e.four_category] ?? ""}`}
                >
                  {e.four_label}
                </span>
                {e.url ? (
                  <button
                    onClick={() =>
                      setModalItem({ title: e.title, url: e.url!, date: e.published_at ?? null, source: e.source ?? null, kindLabel: "快讯" })
                    }
                    className="text-left text-sm text-zinc-800 hover:underline dark:text-zinc-100"
                  >
                    {e.title}
                  </button>
                ) : (
                  <span className="text-sm text-zinc-800 dark:text-zinc-100">{e.title}</span>
                )}
                <span className="ml-auto shrink-0 text-[11px] text-zinc-400">
                  {e.published_at?.slice(5, 16) ?? ""} · {e.source}
                </span>
              </div>
              {/* 排序依据（最相关模式）：首条理由内联，全部理由悬浮 title 可追溯 */}
              {sort === "relevance" && e.rank_reasons && e.rank_reasons.length > 0 && (
                <p
                  className="mt-1 truncate text-[11px] text-zinc-400"
                  title={e.rank_reasons.join("；")}
                >
                  排序依据：{e.rank_reasons[0]}
                  {e.rank_reasons.length > 1 && ` 等 ${e.rank_reasons.length} 项`}
                </p>
              )}
              {/* 事件标签（内联徽标） */}
              {e.tags.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {e.tags.map((t) => (
                    <span key={t} className={`rounded border px-1 py-0.5 text-[10px] ${TAG_STYLE}`}>
                      {t}
                    </span>
                  ))}
                </div>
              )}
              {e.directions.length > 0 && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {e.directions.map((d) => {
                    const { text, cls } = directionLabel(d.direction);
                    const isStock = d.target_type === "symbol";
                    const tip = [d.chain, d.basis].filter(Boolean).join(" ｜ ");
                    return (
                      <a
                        key={`${d.target_type}-${d.target}`}
                        href={isStock ? workbenchUrl(d.target) : themesUrl(d.target)}
                        title={tip || `关联${isStock ? "个股" : "题材"} ${d.target}（${d.basis || "入选理由见标的池"}）`}
                        className="inline-flex items-center gap-1 rounded border border-zinc-200 px-1.5 py-0.5 text-[11px] text-zinc-700 hover:border-zinc-400 dark:border-zinc-700 dark:text-zinc-200"
                      >
                        <span className="text-zinc-400">{isStock ? "个股" : "题材"}</span>
                        <span>{d.target}</span>
                        <span className={`font-medium ${cls}`}>{text}</span>
                      </a>
                    );
                  })}
                  <button
                    onClick={() => setExpanded(expanded === e.id ? null : e.id)}
                    className="rounded border border-zinc-200 px-1.5 py-0.5 text-[11px] text-zinc-500 hover:text-zinc-800 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-100"
                  >
                    {expanded === e.id ? "收起标的池" : "标的池 ↗"}
                  </button>
                </div>
              )}
              {expanded === e.id && (
                <div className="mt-2">
                  <StockPools eventId={e.id} />
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
      <NewsModal item={modalItem} onClose={() => setModalItem(null)} />
    </Panel>
  );
}
