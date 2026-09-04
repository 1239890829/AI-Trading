/**
 * 交易页签：账户摘要 + 下单表单 + 持仓 + 挂单撤单 + 成交记录 + 重置账户。
 * 数据加载（paper 轮询）在壳内；本组件只做展示与直接交互（撤单/重置）。
 */
import { cancelPaperOrder } from "@/lib/api";
import type { PaperFill, PaperOrderInfo, PaperPositionInfo, PaperAccountInfo } from "@/lib/api";
import type { Quote } from "@/types/market";
import { TradeForm } from "@/components/trade-form";
import { fmt } from "@/lib/format";

export interface PaperBundle {
  acc: PaperAccountInfo;
  positions: PaperPositionInfo[];
  orders: PaperOrderInfo[];
}

export function TradePanel({
  symbol,
  paper,
  fills,
  quote,
  resetBusy,
  onResetAccount,
  onPaperChanged,
}: {
  symbol: string;
  paper: PaperBundle;
  fills: PaperFill[];
  quote: Quote | null;
  resetBusy: boolean;
  onResetAccount: () => void;
  /** 下单/撤单/重置后刷新模拟账户数据（壳内 loadPaper，2026-09-01 替代全局事件）。 */
  onPaperChanged: () => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="grid shrink-0 grid-cols-2 gap-1 border-b border-zinc-100 px-3 py-2 text-xs dark:border-zinc-800/60">
        <span className="text-zinc-400">
          总资产 <span className="font-mono text-sm text-zinc-700 dark:text-zinc-100">{fmt(paper.acc.total)}</span>
        </span>
        <span className="text-zinc-400">
          现金 <span className="font-mono text-sm text-zinc-700 dark:text-zinc-100">{fmt(paper.acc.cash)}</span>
        </span>
        <span className="text-zinc-400">
          持仓市值 <span className="font-mono text-sm text-zinc-700 dark:text-zinc-100">{fmt(paper.acc.market_value)}</span>
        </span>
        <span className={paper.acc.total_pnl >= 0 ? "text-up" : "text-down"}>
          总盈亏 <span className="font-mono text-sm">{fmt(paper.acc.total_pnl)}（{fmt(paper.acc.total_pnl_pct)}%）</span>
        </span>
      </div>
      <TradeForm symbol={symbol} price={quote?.price ?? null} limitUp={quote?.limit_up_price ?? null} limitDown={quote?.limit_down_price ?? null} onTraded={onPaperChanged} />
      <h3 className="shrink-0 px-3 pb-1 pt-2 text-xs font-medium text-zinc-500 dark:text-zinc-300">持仓</h3>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <table className="w-full text-sm">
          <tbody>
            {paper.positions.map((pos) => (
              <tr key={pos.symbol} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                <td className="px-3 py-1.5">
                  <div className="font-mono text-xs">{pos.symbol}</div>
                  <div className="text-[11px] text-zinc-400">{pos.quantity}股 · 可卖{pos.available}</div>
                </td>
                <td className="px-2 py-1.5 text-right font-mono text-xs">{fmt(pos.cost_price)}</td>
                <td className={`px-3 py-1.5 text-right font-mono text-xs ${(pos.pnl ?? 0) > 0 ? "text-up" : (pos.pnl ?? 0) < 0 ? "text-down" : "text-zinc-400"}`}>
                  {pos.pnl != null ? fmt(pos.pnl) : "--"}
                </td>
              </tr>
            ))}
            {paper.positions.length === 0 && (
              <tr><td colSpan={3} className="px-3 py-4 text-center text-xs text-zinc-500">空仓</td></tr>
            )}
          </tbody>
        </table>
        {paper.orders.filter((o) => o.status === "pending").length > 0 && (
          <>
            <h3 className="px-3 pb-1 pt-2 text-xs font-medium text-zinc-500 dark:text-zinc-300">挂单</h3>
            {paper.orders.filter((o) => o.status === "pending").map((o) => (
              <div key={o.id} className="flex items-center justify-between border-b border-zinc-100 px-3 py-1 text-xs dark:border-zinc-800/60">
                <span className={o.side === "buy" ? "text-up" : "text-down"}>{o.side === "buy" ? "买" : "卖"} {o.symbol}</span>
                <span className="font-mono text-zinc-400">{fmt(o.price)} × {o.quantity}</span>
                <button
                  onClick={async () => { await cancelPaperOrder(o.id).catch(() => {}); onPaperChanged(); }}
                  className="rounded border border-zinc-300 px-1.5 text-zinc-400 hover:text-red-400 dark:border-zinc-600"
                >
                  撤
                </button>
              </div>
            ))}
          </>
        )}

        <h3 className="shrink-0 px-3 pb-1 pt-2 text-xs font-medium text-zinc-500 dark:text-zinc-300">成交记录</h3>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-[10px] text-zinc-500">
              <th className="px-3 pb-1 text-left font-normal">日期</th>
              <th className="px-2 pb-1 text-left font-normal">方向</th>
              <th className="px-2 pb-1 text-right font-normal">价格</th>
              <th className="px-2 pb-1 text-right font-normal">数量</th>
              <th className="px-3 pb-1 text-right font-normal">费用</th>
            </tr>
          </thead>
          <tbody>
            {fills.map((f, i) => (
              <tr key={`${f.date}-${f.side}-${f.price}-${i}`} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                <td className="px-3 py-1 font-mono text-zinc-400">{f.date || "--"}</td>
                <td className={`px-2 py-1 ${f.side === "buy" ? "text-up" : "text-down"}`}>{f.side === "buy" ? "买入" : "卖出"}</td>
                <td className="px-2 py-1 text-right font-mono">{fmt(f.price)}</td>
                <td className="px-2 py-1 text-right font-mono">{f.quantity}</td>
                <td className="px-3 py-1 text-right font-mono text-zinc-500">{fmt(f.fee ?? 0)}</td>
              </tr>
            ))}
            {fills.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-3 text-center text-zinc-500">本股暂无成交</td></tr>
            )}
          </tbody>
        </table>

        <div className="shrink-0 px-3 py-2">
          <button
            onClick={onResetAccount}
            disabled={resetBusy}
            className="w-full rounded border border-zinc-300 py-1 text-xs text-zinc-400 hover:border-red-400 hover:text-red-400 disabled:opacity-40 dark:border-zinc-600"
          >
            {resetBusy ? "重置中…" : "重置模拟账户"}
          </button>
          <p className="mt-1 text-[10px] leading-relaxed text-zinc-500">清空全部持仓、挂单与成交记录，资金回到初始额度</p>
        </div>
      </div>
    </div>
  );
}
