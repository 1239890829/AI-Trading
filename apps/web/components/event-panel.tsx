"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { NewsModal, type NewsModalItem } from "@/components/news-modal";
import { Panel } from "@/components/panel";
import {
  getEventStocks,
  getImpactEvents,
  type EventDirectionRow,
  type EventStockPool,
  type ImpactEvent,
} from "@/lib/api";
import { workbenchUrlWithBack } from "@/lib/routing";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { Skeleton } from "@/components/ui/loading";

/**
 * 事件驱动面板（总览底部，2026-09-04 任务④重设计）。
 *
 * 决策：**用「盘面相关性 Top4 摘要」替代原时间序列表**——
 * - 原形态：12 条时间序个股新闻塞进 ~148px 高的面板，每次只露 2 条且与盘面
 *   无关（50 条活跃事件 90% 是 other 类个股流水账）= 用户诊断的「毫无价值」；
 * - 扩数量不可行：总览页一屏纪律（严禁页面级滚动）锁死面板高度；
 * - 消费 /api/events/impact 的相关性排序（rank_score/理由/相位加权），
 *   总览给「什么事件在解释今天的盘面」，完整明细在事件 Tab（链接入口）。
 */

const FOUR_STYLE: Record<string, string> = {
  international: "bg-sky-500/15 text-sky-700 border-sky-500/40 dark:text-sky-300",
  policy: "bg-amber-500/15 text-amber-700 border-amber-500/40 dark:text-amber-300",
  hot: "bg-zinc-500/15 text-zinc-700 border-zinc-500/40 dark:text-zinc-300",
  material: "bg-teal-500/15 text-teal-700 border-teal-500/40 dark:text-teal-300",
};

const TOP_N = 4;

function directionLabel(d: number): { text: string; cls: string } {
  if (d > 0) return { text: "利好", cls: "text-up" };
  if (d < 0) return { text: "利空", cls: "text-down" };
  return { text: "待判", cls: "text-zinc-400" };
}

export { directionLabel };

/** 单个事件的标的池展开（E2/L9：事件 → 标的 → 详情）。market 事件 Tab 复用。 */
export function StockPools({ eventId }: { eventId: number }) {
  const [pools, setPools] = useState<EventStockPool[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getEventStocks(eventId)
      .then((p) => alive && setPools(p))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [eventId]);

  if (error) return <p className="text-[11px] text-amber-600 dark:text-amber-300">标的池加载失败：{error}</p>;
  if (pools === null) return <p className="text-[11px] text-zinc-400">标的池加载中…</p>;

  return (
    <div className="space-y-1.5">
      {pools.map((p) => (
        <div key={p.target} className="text-[11px]">
          <div className="flex items-baseline gap-1.5">
            <span className={`font-medium ${directionLabel(p.direction ?? 0).cls}`}>
              {p.target} {directionLabel(p.direction ?? 0).text}
            </span>
            {p.chain && <span className="text-zinc-400">{p.chain}</span>}
            <span className="ml-auto text-zinc-400">{p.stocks.length} 只</span>
          </div>
          {p.note ? (
            <p className="text-zinc-400">{p.note}</p>
          ) : (
            <div className="mt-0.5 flex flex-wrap gap-1">
              {p.stocks.slice(0, 12).map((s) => (
                <Link
                  key={s.symbol}
                  href={workbenchUrlWithBack(s.symbol)}
                  className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
                  title={`${s.symbol} ${s.name} · 查看详情`}
                >
                  {s.symbol} {s.name}
                </Link>
              ))}
              {p.stocks.length > 12 && <span className="self-center text-zinc-400">+{p.stocks.length - 12}</span>}
            </div>
          )}
        </div>
      ))}
      <p className="text-[10px] text-zinc-400">标的池仅为事件关联的官方成分，不构成买卖建议。</p>
    </div>
  );
}

export function EventPanel() {
  const [items, setItems] = useState<ImpactEvent[] | null>(null);
  const [phase, setPhase] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modalItem, setModalItem] = useState<NewsModalItem | null>(null);

  const load = useCallback(async () => {
    const r = await getImpactEvents(false, 100, "relevance");
    setItems(r.items.slice(0, TOP_N));
    // 相位从首条的理由里取（后端 rank_factors.phase），缺省 null 不臆造
    setPhase(r.items.find((e) => e.rank_factors?.phase)?.rank_factors?.phase ?? null);
    setError(null);
  }, []);

  usePollingFetch(async () => {
    try {
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "事件数据加载失败。");
    }
  }, 60_000);

  return (
    <Panel
      title="事件驱动"
      extra={
        <span className="flex items-center gap-2 text-[11px] text-zinc-400">
          {phase && <span title="当前市场情绪相位（影响事件排序权重）">相位 {phase}</span>}
          <span className="hidden xl:inline">按盘面相关性排序（仅关联，不构成建议）</span>
          <Link href="/market?tab=events" className="text-zinc-400 transition-colors hover:text-zinc-900 dark:hover:text-zinc-100">
            完整列表 ↗
          </Link>
        </span>
      }
    >
      {error && <p className="px-4 py-3 text-xs text-amber-600 dark:text-amber-300">{error}</p>}
      {items === null && !error && (
        <ul className="space-y-2.5 px-4 py-3" aria-hidden>
          {Array.from({ length: 3 }, (_, i) => (
            <li key={i} className="space-y-1.5">
              <div className="flex items-center gap-2">
                <Skeleton className="h-3.5 w-9" />
                <Skeleton className="h-3.5 w-12" />
                <Skeleton className="h-3.5 flex-1" />
              </div>
              <Skeleton className="h-3 w-2/3" />
            </li>
          ))}
        </ul>
      )}
      {items !== null && items.length === 0 && !error && (
        <p className="px-4 py-6 text-center text-xs text-zinc-400">
          暂无活跃事件（时效 = 半衰期 × 2，过期自动隐去）
        </p>
      )}
      {items !== null && items.length > 0 && (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {items.map((e) => (
            <li key={e.id} className="px-4 py-2">
              <div className="flex items-baseline gap-2">
                {e.rank_score != null && (
                  <span
                    className="shrink-0 rounded bg-zinc-900 px-1 py-0.5 font-mono text-[10px] font-semibold text-white dark:bg-zinc-100 dark:text-zinc-900"
                    title={(e.rank_reasons ?? []).join("\n")}
                  >
                    {Math.round(e.rank_score)}分
                  </span>
                )}
                <span className={`shrink-0 rounded border px-1 py-0.5 text-[10px] ${FOUR_STYLE[e.four_category] ?? ""}`}>
                  {e.four_label}
                </span>
                {e.url ? (
                  <button
                    onClick={() =>
                      setModalItem({ title: e.title, url: e.url!, date: e.published_at ?? null, source: e.source ?? null, kindLabel: "快讯" })
                    }
                    className="truncate text-left text-sm text-zinc-800 hover:underline dark:text-zinc-100"
                    title={e.title}
                  >
                    {e.title}
                  </button>
                ) : (
                  <span className="truncate text-sm text-zinc-800 dark:text-zinc-100" title={e.title}>{e.title}</span>
                )}
                <span className="ml-auto shrink-0 text-[11px] text-zinc-400">{e.published_at?.slice(5, 11) ?? ""}</span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                {e.rank_reasons?.[0] && (
                  <span className="truncate text-[11px] text-zinc-400" title={e.rank_reasons.join("；")}>
                    {e.rank_reasons[0]}
                  </span>
                )}
                {e.directions.slice(0, 3).map((d) => {
                  const { text, cls } = directionLabel(d.direction);
                  return (
                    <Link
                      key={`${d.target_type}-${d.target}`}
                      href={d.target_type === "symbol" ? workbenchUrlWithBack(d.target) : `/tape?tab=themes&focus=${encodeURIComponent(d.target)}`}
                      title={[d.chain, d.basis].filter(Boolean).join(" ｜ ") || `关联${d.target_type === "symbol" ? "个股" : "题材"} ${d.target}`}
                      className="inline-flex items-center gap-1 rounded border border-zinc-200 px-1.5 py-0.5 text-[11px] text-zinc-700 hover:border-zinc-400 dark:border-zinc-700 dark:text-zinc-200"
                    >
                      <span className="text-zinc-400">{d.target_type === "symbol" ? "个股" : "题材"}</span>
                      <span>{d.target}</span>
                      <span className={`font-medium ${cls}`}>{text}</span>
                    </Link>
                  );
                })}
              </div>
            </li>
          ))}
        </ul>
      )}
      <NewsModal item={modalItem} onClose={() => setModalItem(null)} />
    </Panel>
  );
}
