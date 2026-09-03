"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Panel } from "@/components/panel";
import {
  getEvents,
  getEventStocks,
  type EventDirectionRow,
  type EventStockPool,
  type EventSummary,
} from "@/lib/api";
import { workbenchUrlWithBack } from "@/lib/routing";

const CATEGORY_LABEL: Record<string, string> = {
  policy: "政策",
  statement: "发言",
  data: "数据",
  rumor: "传闻",
  corporate: "公司",
  other: "其他",
};

const CERTAINTY_LABEL: Record<string, string> = {
  done: "已落地",
  proposed: "拟议",
  rumor: "传闻",
};

const FACT_LABEL: Record<string, string> = {
  fact: "事实",
  opinion: "解读",
  rumor: "传闻",
};

function directionLabel(d: number): { text: string; cls: string } {
  if (d > 0) return { text: "利好", cls: "text-up" };
  if (d < 0) return { text: "利空", cls: "text-down" };
  return { text: "待判", cls: "text-zinc-400" };
}

function DirectionChip({ d }: { d: EventDirectionRow }) {
  const { text, cls } = directionLabel(d.direction);
  const tip = [d.chain, d.basis].filter(Boolean).join(" ｜ ");
  return (
    <Link
      href={`/tape?tab=themes&focus=${encodeURIComponent(d.target)}`}
      title={tip || `关联题材 ${d.target}`}
      className="inline-flex items-center gap-1 rounded border border-zinc-200 px-1.5 py-0.5 text-[11px] text-zinc-700 hover:border-zinc-400 dark:border-zinc-700 dark:text-zinc-200"
    >
      <span>{d.target}</span>
      <span className={`font-medium ${cls}`}>{text}</span>
    </Link>
  );
}

/** 单个事件的标的池展开（E2/L9：事件 → 标的 → 详情）。 */
function StockPools({ eventId }: { eventId: number }) {
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

/**
 * 事件驱动面板（E1⑥/E2）：活跃事件 → 方向 → 标的池 → 详情/题材联动。
 * 自取数：失败静默为空态，不拖垮市场页其余部分。
 */
export function EventPanel() {
  const [events, setEvents] = useState<EventSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    getEvents(true, 12)
      .then((list) => alive && setEvents(list))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <Panel
      title="事件驱动"
      extra={<span className="text-[11px] text-zinc-400">事件 → 题材 → 标的池（仅关联，不构成建议）</span>}
    >
      {error && <p className="px-4 py-3 text-xs text-amber-600 dark:text-amber-300">{error}</p>}
      {events !== null && events.length === 0 && (
        <p className="px-4 py-6 text-center text-xs text-zinc-400">暂无活跃事件（时效 = 半衰期 × 2，过期自动隐去）</p>
      )}
      {events !== null && events.length > 0 && (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {events.map((e) => (
            <li key={e.id} className="px-4 py-2.5">
              <div className="flex items-baseline gap-2">
                {e.url ? (
                  <a href={e.url} target="_blank" rel="noreferrer" className="text-sm text-zinc-800 hover:underline dark:text-zinc-100">
                    {e.title}
                  </a>
                ) : (
                  <span className="text-sm text-zinc-800 dark:text-zinc-100">{e.title}</span>
                )}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-zinc-400">
                <span className="rounded bg-zinc-100 px-1 dark:bg-zinc-800">{CATEGORY_LABEL[e.category] ?? e.category}</span>
                <span className="rounded bg-zinc-100 px-1 dark:bg-zinc-800">
                  {CERTAINTY_LABEL[e.certainty] ?? e.certainty} · {FACT_LABEL[e.fact_kind] ?? e.fact_kind}
                </span>
                <span title={`来源分级 ${e.source_tier}/5（5=官方公告 4=一线权威 3=主流财经 2=聚合转载 1=自媒体）`}>
                  来源 {e.source_tier}/5
                </span>
                <span title={`半衰期 ${e.half_life_hours} 小时，过期后自动隐去`}>半衰期 {e.half_life_hours}h</span>
                {e.source_symbol && <span className="font-mono">via {e.source_symbol}</span>}
              </div>
              {e.directions.length > 0 && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {e.directions.map((d) => (
                    <DirectionChip key={`${d.target_type}-${d.target}`} d={d} />
                  ))}
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
      )}
    </Panel>
  );
}
