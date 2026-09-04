"use client";

import { useCallback, useState } from "react";
import { Panel } from "@/components/panel";
import { StockPools, directionLabel } from "@/components/event-panel";
import { getImpactEvents, type ImpactEvent } from "@/lib/api";
import { usePollingFetch } from "@/hooks/use-polling-fetch";

/**
 * 事件 Tab（§六.4 用户拍板 2026-09-04）：云图之后独立视图。
 * 四级分类（国际时事/国家政策/市场热点/原材料涨价）+ 三级影响力（L1 必上/L2 选上/L3 不上）。
 * 复用既有 EventCard 抽取管道，本组件只是影响力视图的消费端。
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

const FOUR_FILTERS = [
  { key: "all", label: "全部" },
  { key: "material", label: "原材料涨价" },
  { key: "policy", label: "国家政策" },
  { key: "international", label: "国际时事" },
  { key: "hot", label: "市场热点" },
] as const;

export function EventsTab() {
  const [items, setItems] = useState<ImpactEvent[] | null>(null);
  const [countsAll, setCountsAll] = useState<Record<string, number> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [four, setFour] = useState<string>("all");
  const [l1Only, setL1Only] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(async () => {
    const r = await getImpactEvents(false, 100);
    setItems(r.items);
    setCountsAll(r.countsAll);
    setError(null);
  }, []);

  usePollingFetch(async () => {
    try {
      await load();
    } catch {
      setError("事件影响力数据加载失败（后端不可达或数据源异常）。");
    }
  }, 60_000);

  const shown = (items ?? []).filter(
    (e) => (four === "all" || e.four_category === four) && (!l1Only || e.impact_level === "L1"),
  );

  return (
    <Panel
      title="事件影响力"
      extra={
        <span className="text-[11px] text-zinc-400">
          {countsAll ? `L1 ${countsAll.L1 ?? 0} · L2 ${countsAll.L2 ?? 0} · L3 已滤 ${countsAll.L3 ?? 0}` : ""}
        </span>
      }
      className="min-h-0 overflow-hidden"
    >
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-zinc-100 px-3 py-2 dark:border-zinc-800/60">
          {FOUR_FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFour(f.key)}
              className={`rounded-full border px-2.5 py-0.5 text-xs ${
                four === f.key
                  ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                  : "border-zinc-200 text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100"
              }`}
            >
              {f.label}
            </button>
          ))}
          <label className="ml-auto flex cursor-pointer items-center gap-1 text-xs text-zinc-500">
            <input type="checkbox" checked={l1Only} onChange={(e) => setL1Only(e.target.checked)} />
            只看 L1
          </label>
        </div>

        {error && (
          <p className="shrink-0 px-4 py-2 text-xs text-amber-600 dark:text-amber-300">{error}</p>
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
                  <a href={e.url} target="_blank" rel="noreferrer" className="text-sm text-zinc-800 hover:underline dark:text-zinc-100">
                    {e.title}
                  </a>
                ) : (
                  <span className="text-sm text-zinc-800 dark:text-zinc-100">{e.title}</span>
                )}
                <span className="ml-auto shrink-0 text-[11px] text-zinc-400">
                  {e.published_at?.slice(5, 16) ?? ""} · {e.source}
                </span>
              </div>
              {e.directions.length > 0 && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {e.directions.map((d) => {
                    const { text, cls } = directionLabel(d.direction);
                    const isStock = d.target_type === "symbol";
                    const tip = [d.chain, d.basis].filter(Boolean).join(" ｜ ");
                    return (
                      <a
                        key={`${d.target_type}-${d.target}`}
                        href={isStock ? `/workbench?symbol=${encodeURIComponent(d.target)}` : `/tape?tab=themes&focus=${encodeURIComponent(d.target)}`}
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
    </Panel>
  );
}
