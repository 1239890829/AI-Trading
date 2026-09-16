/** 盘口 / 逐笔 共用视图（右列 book 与 trades 页签的内容区）。纯展示。
 *  三态纪律（2026-09-08 审查 F1）：undefined=尚未拉到 → Skeleton 占位；
 *  null/[] =拉过且确认无 → 空态文案。此前切股重挂载窗口期把"加载中"渲染成
 *  "盘口数据不可用（免费源仅盘中提供）"——把"还没拉到"说成"不可用"是误导。 */
import type { OrderBook, Trade } from "@/types/market";
import { fmt, sourceLabel, timeText } from "@/lib/format";
import { Skeleton } from "@/components/ui/loading";

/** 逐笔**口径**表（2026-09-16 `IMP-038`）。
 *
 *  为什么必须有：两个源给的东西**不是一回事**——TDX（主源）按 **3 秒快照聚合**
 *  （实测 600519 全日 3867 行、相邻时间差众数 = 3s、秒位只落在 3 的倍数上），
 *  东财（备源）是逐笔明细。文案由 `Trade.source` 推导而**不写死**：
 *  写死"逐笔"在备源接管时就是错的（红线 3 的同族问题——口径不许想当然）。 */
const TRADES_CALIBER: Record<string, string> = {
  tdx: "3 秒快照聚合",
  eastmoney: "逐笔明细",
};

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
  if (trades.length === 0) {
    return (
      <p className="px-3 py-8 text-center text-xs text-zinc-600 dark:text-zinc-400">暂无逐笔（盘中看分时）</p>
    );
  }
  const src = trades[0]?.source;
  return (
    <>
      <p className="border-b border-zinc-100 px-3 py-1.5 text-[10px] text-zinc-500 dark:border-zinc-800/60 dark:text-zinc-500">
        口径：{sourceLabel(src)} {TRADES_CALIBER[src] ?? "明细"} · 共 {trades.length} 笔（时间升序，量：手）
      </p>
      <table className="w-full text-sm">
        <tbody>
          {trades.map((t, i) => (
            <tr key={i} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
              <td className="px-2 py-1 font-mono text-[11px] tabular-nums text-zinc-600 dark:text-zinc-400">{timeText(t.ts)}</td>
              <td className={`px-1 py-1 text-right font-mono tabular-nums ${t.side === "buy" ? "text-up-ink dark:text-up" : t.side === "sell" ? "text-down-ink dark:text-down" : "text-zinc-600 dark:text-zinc-300"}`}>{fmt(t.price)}</td>
              {/* ⚠️ 此处**不能**用 `fmtVolume`：它按"后端统一为股"除以 100 转手，
                  而逐笔的 `Trade.volume` 本身就是**手**（东财 details 第 3 列 = 手；
                  TDX `vol` 实测同为手：600519 全日 26,243 手 ↔ fuyao 日线 26,235.24 手）。
                  用错格式化器不会报错，只会把每行都渲染成 **0**（2026-09-16 实测踩到，
                  此前不可见只因逐笔端点一直是 502）。量纲守卫见本文件用例。 */}
              <td className="px-2 py-1 text-right font-mono text-[11px] tabular-nums text-zinc-600 dark:text-zinc-400">{fmt(t.volume, 0)}</td>
              <td className="pr-2 text-right text-[11px] text-zinc-600 dark:text-zinc-400">{t.side === "buy" ? "B" : t.side === "sell" ? "S" : "·"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
