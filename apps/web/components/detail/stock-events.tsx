"use client";

import { getEventsForSymbol } from "@/lib/api";
import { useResource } from "@/hooks/use-resource";
import { useDetailModal } from "@/components/detail/detail-modal";
import { eventDetailPayload, eventDirectionForSymbol, eventDirectionView, eventJudgeView } from "@/lib/event-view";
import { ReadingButton } from "@/components/ui/reading-row";

/**
 * 详情页「相关事件」行（E2/L9）：与该股相关的活跃事件（方向题材命中归属 or 事件源自该股）。
 * 无命中、读取失败和保留旧结果分开；可见详情每分钟校准一次。
 *
 * `isIndex`（2026-09-11）：指数不适用本行。后端 `/api/events/symbol/{code}` 只接受
 * 6 位个股代码，指数段（sh000001/sz399001…）返回 400「非法代码」，此前指数详情
 * 常驻一条「相关事件 · 加载失败 [重试]」。这里**不发请求也不渲染**（而非发了再吞错），
 * 既避免噪声，也省掉一次必然失败的往返。
 */
export function StockEventsRow({ symbol, isIndex = false, active = true }: { symbol: string; isIndex?: boolean; active?: boolean }) {
  const resource = useResource(async () => {
    const result = await getEventsForSymbol(symbol);
    if (result.symbol !== symbol || !Array.isArray(result.items)) throw new Error("事件响应标的不匹配");
    return result;
  }, {key: symbol, intervalMs: 60_000, enabled: active && !isIndex, marketHours: false});
  const events = resource.data?.items;
  const failed = !!resource.error;
  // 2026-09-09：改用全站通用详情弹窗（Provider），无 url 也能点开看判定结果
  const { open } = useDetailModal();
  if (isIndex || !active) return null;

  // 2026-09-09 修复：加载失败/空数据此前一律 return null（用户看不到任何东西，
  // 也不知道是没数据还是挂了）→ 改为显式三态：loading / error（可重试）/ empty。
  if (events === undefined && !failed) {
    return (
      <div className="flex shrink-0 items-center gap-1 text-xs text-zinc-600 dark:text-zinc-400">
        <span>相关事件</span>
        <span className="animate-pulse">加载中…</span>
      </div>
    );
  }
  const failure = failed && (
      <div className="flex shrink-0 items-center gap-1 text-xs">
        <span className="text-zinc-600 dark:text-zinc-400">相关事件</span>
        <span role="alert" className="text-amber-800 dark:text-amber-400">加载失败{events ? " · 保留上次结果，本次状态未确认" : " · 尚不能确认是否有相关事件"}</span>
        <button data-action="secondary"
          type="button"
          disabled={resource.pending}
          onClick={resource.refresh}
        >
          重试
        </button>
      </div>
    );
  if (!events) return failure;
  if (events.length === 0) {
    return (
      <div className="flex shrink-0 items-center gap-1 text-xs text-zinc-600 dark:text-zinc-400">
        <span>相关事件</span>
        {failure || <span>— 本次暂无与该股题材匹配的活跃事件</span>}
      </div>
    );
  }

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs">
      <span className="shrink-0 text-zinc-600 dark:text-zinc-400">相关事件</span>
      {failure}
      {events.slice(0, 3).map((e) => {
        const d = eventDirectionForSymbol(e, symbol);
        const dir = d ? eventDirectionView(d, e, symbol) : null;
        const judgement = eventJudgeView(e);
        const title = (
          <>
            <span className="line-clamp-2 text-zinc-700 dark:text-zinc-200">{e.title}</span>
            {dir && <span className={`ml-1 font-medium ${dir.cls}`}>{dir.text}</span>}
            <span title={judgement.title} className={`ml-1 whitespace-nowrap ${judgement.cls}`}>{judgement.text}</span>
          </>
        );
        const tip = [
          dir?.title,
          judgement.title,
          `来源 ${e.source_tier}/5 · ${e.certainty}`,
        ]
          .filter(Boolean)
          .join(" ｜ ");
        // 2026-09-09：无 url 也能点（此前渲染成 span → 点击无响应）；
        // 弹窗内由 DetailModal 统一处理「原文链接缺失」兜底。
        return (
          <ReadingButton
            key={e.id}
            aria-label={`查看事件：${e.title}${dir ? `，${dir.text}` : ""}，${judgement.text}`}
            onClick={() =>
              open(eventDetailPayload(e, symbol))
            }
            title={tip}
            className="min-w-0 max-w-full rounded border border-zinc-200 px-1.5 py-0.5 text-left [overflow-wrap:anywhere] hover:border-zinc-400 dark:border-zinc-700"
          >
            {title}
          </ReadingButton>
        );
      })}
    </div>
  );
}
