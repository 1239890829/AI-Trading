/** 盘口 / 逐笔 共用视图（右列 book 与 trades 页签的内容区）。纯展示。
 *  三态纪律（2026-09-08 审查 F1）：undefined=尚未拉到 → Skeleton 占位；
 *  null/[] =拉过且确认无 → 空态文案。此前切股重挂载窗口期把"加载中"渲染成
 *  "盘口数据不可用（免费源仅盘中提供）"——把"还没拉到"说成"不可用"是误导。 */
import type { OrderBook, Trade } from "@/types/market";
import { fmt, fmtVolume, timeText } from "@/lib/format";
import { Skeleton } from "@/components/ui/loading";

function BookSkeleton() {
  return (
    <div className="space-y-1 px-3 py-2" aria-hidden>
      {Array.from({ length: 10 }, (_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-3 w-8" />
          <Skeleton className="h-3 flex-1" />
          <Skeleton className="h-3 w-14" />
        </div>
      ))}
    </div>
  );
}

function TradesSkeleton() {
  return (
    <div className="space-y-1.5 px-3 py-2" aria-hidden>
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-3 w-14" />
          <Skeleton className="h-3 flex-1" />
          <Skeleton className="h-3 w-12" />
        </div>
      ))}
    </div>
  );
}

export function BookTradesView({ book, trades, showBook }: { book: OrderBook | null | undefined; trades: Trade[] | undefined; showBook: boolean }) {
  if (showBook) {
    if (book === undefined) return <BookSkeleton />;
    return book ? (
      <table className="w-full text-sm">
        <tbody>
          {[...book.asks].reverse().map((lv, i) => (
            <tr key={`a${i}`} className="border-b border-zinc-100 dark:border-zinc-800/60">
              <td className="px-3 py-1.5 text-xs text-zinc-600 dark:text-zinc-400">卖{book.asks.length - i}</td>
              <td className="px-2 py-1.5 text-right font-mono tabular-nums text-down-ink dark:text-down">{fmt(lv.price)}</td>
              <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-zinc-600 dark:text-zinc-400">{fmt(lv.volume, 0)}</td>
            </tr>
          ))}
          {[...book.bids].map((lv, i) => (
            <tr key={`b${i}`}>
              <td className="px-3 py-1.5 text-xs text-zinc-600 dark:text-zinc-400">买{i + 1}</td>
              <td className="px-2 py-1.5 text-right font-mono tabular-nums text-up-ink dark:text-up">{fmt(lv.price)}</td>
              <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-zinc-600 dark:text-zinc-400">{fmt(lv.volume, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    ) : (
      <p className="px-3 py-10 text-center text-xs text-zinc-600 dark:text-zinc-400">盘口数据不可用（免费源仅盘中提供）</p>
    );
  }
  if (trades === undefined) return <TradesSkeleton />;
  return trades.length > 0 ? (
    <table className="w-full text-sm">
      <tbody>
        {trades.map((t, i) => (
          <tr key={i} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
            <td className="px-2 py-1 font-mono text-[11px] tabular-nums text-zinc-600 dark:text-zinc-400">{timeText(t.ts)}</td>
            <td className={`px-1 py-1 text-right font-mono tabular-nums ${t.side === "buy" ? "text-up-ink dark:text-up" : t.side === "sell" ? "text-down-ink dark:text-down" : "text-zinc-600 dark:text-zinc-300"}`}>{fmt(t.price)}</td>
            <td className="px-2 py-1 text-right font-mono text-[11px] tabular-nums text-zinc-600 dark:text-zinc-400">{fmtVolume(t.volume)}</td>
            <td className="pr-2 text-right text-[11px] text-zinc-600 dark:text-zinc-400">{t.side === "buy" ? "B" : t.side === "sell" ? "S" : "·"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  ) : (
    <p className="px-3 py-8 text-center text-xs text-zinc-600 dark:text-zinc-400">暂无逐笔（盘中看分时）</p>
  );
}
