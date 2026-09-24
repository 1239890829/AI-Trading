"use client";

import { useEffect, useState } from "react";
import { getEventsForSymbol, type EventSummary } from "@/lib/api";
import { useDetailModal } from "@/components/detail/detail-modal";
import { eventInterpretationText } from "@/lib/event-view";

const DIRECTION_LABEL: Record<number, { text: string; cls: string }> = {
  1: { text: "利好", cls: "text-up-ink dark:text-up" },
  [-1]: { text: "利空", cls: "text-down-ink dark:text-down" },
  0: { text: "待判", cls: "text-zinc-600 dark:text-zinc-400" },
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
 *
 * `isIndex`（2026-09-11）：指数不适用本行。后端 `/api/events/symbol/{code}` 只接受
 * 6 位个股代码，指数段（sh000001/sz399001…）返回 400「非法代码」，此前指数详情
 * 常驻一条「相关事件 · 加载失败 [重试]」。这里**不发请求也不渲染**（而非发了再吞错），
 * 既避免噪声，也省掉一次必然失败的往返。
 */
export function StockEventsRow({ symbol, isIndex = false }: { symbol: string; isIndex?: boolean }) {
  const [events, setEvents] = useState<EventSummary[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  // 2026-09-09：改用全站通用详情弹窗（Provider），无 url 也能点开看判定结果
  const { open } = useDetailModal();
  // 切股先清空（渲染期 adjust-state：symbol 变化即重置，防上一只事件残留一帧）
  const [prevSymbol, setPrevSymbol] = useState<string | null>(null);
  if (symbol !== prevSymbol) {
    setPrevSymbol(symbol);
    setEvents(null);
  }
  // 失败态同样改渲染期重置（键含 attempt，与原 effect 内 `setFailed(false)` 的
  // 触发条件严格一致；顺带消掉 react-hooks/set-state-in-effect，P1-27）
  const fetchKey = `${symbol}:${attempt}`;
  const [prevFetchKey, setPrevFetchKey] = useState(fetchKey);
  if (fetchKey !== prevFetchKey) {
    setPrevFetchKey(fetchKey);
    setFailed(false);
  }

  useEffect(() => {
    if (isIndex) return;   // 指数：不发请求（后端必拒 400）
    let alive = true;
    getEventsForSymbol(symbol)
      .then((r) => alive && setEvents(r.items))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [symbol, attempt, isIndex]);

  if (isIndex) return null;   // 指数：整行不渲染（hook 已全部调用，无顺序风险）

  // 2026-09-09 修复：加载失败/空数据此前一律 return null（用户看不到任何东西，
  // 也不知道是没数据还是挂了）→ 改为显式三态：loading / error（可重试）/ empty。
  if (events === null && !failed) {
    return (
      <div className="flex shrink-0 items-center gap-1 text-xs text-zinc-600 dark:text-zinc-400">
        <span>相关事件</span>
        <span className="animate-pulse">加载中…</span>
      </div>
    );
  }
  if (failed) {
    return (
      <div className="flex shrink-0 items-center gap-1 text-xs">
        <span className="text-zinc-600 dark:text-zinc-400">相关事件</span>
        <span className="text-amber-800 dark:text-amber-400">加载失败</span>
        <button
          type="button"
          onClick={() => setAttempt((n) => n + 1)}
          className="rounded border border-zinc-200 px-1 text-[11px] text-zinc-600 dark:text-zinc-400 hover:border-zinc-400 dark:border-zinc-700"
        >
          重试
        </button>
      </div>
    );
  }
  // 三态已在上文分流（loading/failed），此处 TS 收窄需要显式判空
  if (!events) {
    return (
      <div className="flex shrink-0 items-center gap-1 text-xs text-zinc-600 dark:text-zinc-400">
        <span>相关事件</span>
        <span>— 今日暂无与该股题材匹配的活跃事件</span>
      </div>
    );
  }
  if (events.length === 0) {
    return (
      <div className="flex shrink-0 items-center gap-1 text-xs text-zinc-600 dark:text-zinc-400">
        <span>相关事件</span>
        <span>— 今日暂无与该股题材匹配的活跃事件</span>
      </div>
    );
  }

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs">
      <span className="shrink-0 text-zinc-600 dark:text-zinc-400">相关事件</span>
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
        // 2026-09-09：无 url 也能点（此前渲染成 span → 点击无响应）；
        // 弹窗内由 DetailModal 统一处理「原文链接缺失」兜底。
        return (
          <button
            key={e.id}
            onClick={() =>
              open({
                kind: "event",
                title: e.title,
                url: e.url ?? null,
                symbol,
                theme: e.directions[0]?.target ?? null,
                source: e.source ?? null,
                date: e.published_at ?? null,
                body: e.summary ?? null,
                meta: [
                  { label: "列表解释版本", value: eventInterpretationText(e) },
                  ...(dir ? [{ label: "判定", value: `${dir.text}${e.judge_status_label ? `（${e.judge_status_label}）` : ""}` }] : []),
                  ...(e.directions[0]?.target ? [{ label: "关联板块", value: e.directions[0].target }] : []),
                  ...(e.directions[0]?.basis ? [{ label: "依据", value: e.directions[0].basis }] : []),
                  { label: "来源", value: `${e.source_tier}/5 · ${e.certainty}` },
                ],
              })
            }
            title={tip}
            className="rounded border border-zinc-200 px-1.5 py-0.5 text-left hover:border-zinc-400 dark:border-zinc-700"
          >
            {title}
          </button>
        );
      })}
    </div>
  );
}
