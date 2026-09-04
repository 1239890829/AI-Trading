"use client";

import { useEffect, useState } from "react";
import { getEventsForSymbol, type EventSummary } from "@/lib/api";
import { NewsModal, type NewsModalItem } from "@/components/news-modal";

const DIRECTION_LABEL: Record<number, { text: string; cls: string }> = {
  1: { text: "利好", cls: "text-up" },
  [-1]: { text: "利空", cls: "text-down" },
  0: { text: "待判", cls: "text-zinc-400" },
};

const CATEGORY_LABEL: Record<string, string> = {
  policy: "政策",
  statement: "发言",
  data: "数据",
  rumor: "传闻",
  corporate: "公司",
  other: "其他",
};

/**
 * 详情页「相关事件」行（E2/L9）：与该股相关的活跃事件（方向题材命中归属 or 事件源自该股）。
 * 无命中时零占用；加载失败静默——事件是增强信息，不拖垮详情页。
 */
export function StockEventsRow({ symbol }: { symbol: string }) {
  const [events, setEvents] = useState<EventSummary[] | null>(null);
  const [modalItem, setModalItem] = useState<NewsModalItem | null>(null);
  // 切股先清空（渲染期 adjust-state：symbol 变化即重置，防上一只事件残留一帧）
  const [prevSymbol, setPrevSymbol] = useState<string | null>(null);
  if (symbol !== prevSymbol) {
    setPrevSymbol(symbol);
    setEvents(null);
  }

  useEffect(() => {
    let alive = true;
    getEventsForSymbol(symbol)
      .then((r) => alive && setEvents(r.items))
      .catch(() => alive && setEvents([]));
    return () => {
      alive = false;
    };
  }, [symbol]);

  if (events === null || events.length === 0) return null;

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs">
      <span className="shrink-0 text-zinc-400">相关事件</span>
      {events.slice(0, 3).map((e) => {
        const d = e.directions[0];
        const dir = d ? DIRECTION_LABEL[d.direction] : null;
        const title = (
          <>
            <span className="text-zinc-700 dark:text-zinc-200">{e.title.slice(0, 30)}</span>
            {dir && <span className={`ml-1 font-medium ${dir.cls}`}>{dir.text}</span>}
          </>
        );
        const tip = [
          e.directions[0]?.chain,
          e.directions[0]?.basis,
          `来源 ${e.source_tier}/5 · ${e.certainty}`,
        ]
          .filter(Boolean)
          .join(" ｜ ");
        return e.url ? (
          <button
            key={e.id}
            onClick={() =>
              setModalItem({ title: e.title, url: e.url!, date: e.published_at ?? null, source: e.source ?? null, kindLabel: "事件" })
            }
            title={tip}
            className="rounded border border-zinc-200 px-1.5 py-0.5 text-left hover:border-zinc-400 dark:border-zinc-700"
          >
            {title}
          </button>
        ) : (
          <span
            key={e.id}
            title={tip}
            className="rounded border border-zinc-200 px-1.5 py-0.5 dark:border-zinc-700"
          >
            {title}
          </span>
        );
      })}
      <NewsModal item={modalItem} onClose={() => setModalItem(null)} />
    </div>
  );
}
