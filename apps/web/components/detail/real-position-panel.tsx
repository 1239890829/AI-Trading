"use client";

import { useEffect, useState } from "react";
import {
  createRealTrade,
  deleteRealPosition,
  deleteRealTrade,
  overrideRealPosition,
  type RealPositionRow,
} from "@/lib/api";
import { fmt, pctColor, pctText } from "@/lib/format";
import { APP_EVENTS, emitAppEvent } from "@/lib/events";
import { useRealPositions } from "@/hooks/use-real-positions";

/**
 * 真实持仓面板（CONTEXT.md: Real Position 域；与模拟交易 tab 完全独立）。
 *
 * 上半：「记一笔」表单——成交价默认当前现价、**可改**（实际成交价语义：
 * 你在同花顺以 18.479 成交就记 18.479，不是现价）；买/卖切换；费用可选。
 * 下半：持仓视图（流水聚合 + 手动覆盖）+ 已清仓标的的已实现盈亏。
 *
 * 展示字段（需求 3 设计）：标的 | 数量(股) | 摊薄成本 | 总成本 | 现价 |
 * 市值 | 浮动盈亏(+%) | 已实现盈亏 | 状态（已手动修正/正常）。
 * 汇总行：总市值 / 总成本 / 总浮动盈亏 / 累计已实现盈亏。
 */
export function RealPositionPanel({ symbol, currentPrice, currentName, className }: { symbol?: string; currentPrice?: number | null; currentName?: string | null; className?: string }) {
  // 聚合数据：共用 hook 一份轮询（评审 M3），reload 供表单提交后即时刷新
  const { data, error: loadError, reload } = useRealPositions();
  // 表单错误与数据加载错误分开展示（原实现共用一个 state，评审 M3 抽 hook 时拆开）
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 表单态
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [formSymbol, setFormSymbol] = useState(symbol ?? "");
  const [price, setPrice] = useState("");
  const [qty, setQty] = useState("");
  const [fee, setFee] = useState("");
  const [tradedAt, setTradedAt] = useState(() => new Date().toISOString().slice(0, 10));
  // 手动修正态（对哪只持仓改数量/总成本）
  const [editSymbol, setEditSymbol] = useState<string | null>(null);
  const [editQty, setEditQty] = useState("");
  const [editCost, setEditCost] = useState("");

  useEffect(() => {
    if (symbol) {
      setFormSymbol(symbol);
      setSide("buy");
    }
  }, [symbol]);
  // 成交价默认现价（录入默认值，可改——改过的才是实际成交价）
  useEffect(() => {
    if (currentPrice != null && currentPrice > 0) setPrice((p) => p || String(+currentPrice.toFixed(3)));
  }, [currentPrice]);

  const notify = () => emitAppEvent(APP_EVENTS.realChanged);

  async function submitTrade() {
    const pv = Number(price.replace(/,/g, ""));
    const qv = Number(qty);
    const fv = fee ? Number(fee) : 0;
    if (!formSymbol.trim()) return setError("请填代码");
    if (!(pv > 0)) return setError("成交价必须大于 0");
    if (!(qv > 0) || !Number.isInteger(qv)) return setError("数量必须为正整数（股）");
    setBusy(true);
    setError(null);
    try {
      await createRealTrade({ symbol: formSymbol.trim(), name: formSymbol === symbol ? currentName ?? null : null, side, fill_price: pv, quantity: qv, fee: fv, traded_at: tradedAt });
      setQty("");
      setFee("");
      notify();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function submitOverride(row: RealPositionRow) {
    const qv = Number(editQty);
    const cv = Number(editCost.replace(/,/g, ""));
    if (!(qv > 0) || !Number.isInteger(qv)) return setError("修正数量必须为正整数");
    if (!(cv > 0)) return setError("修正总成本必须大于 0");
    setBusy(true);
    try {
      await overrideRealPosition(row.symbol, qv, cv);
      setEditSymbol(null);
      notify();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function removeRow(row: RealPositionRow) {
    if (!window.confirm(`删除 ${row.name ?? row.symbol} 的全部真实持仓流水？此操作不可恢复。`)) return;
    setBusy(true);
    try {
      await deleteRealPosition(row.symbol);
      notify();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`flex min-h-0 flex-col ${className ?? ""}`}>
      {/* 记一笔 */}
      <div className="shrink-0 border-b border-zinc-100 px-2 py-2 dark:border-zinc-800/60">
        <div className="flex items-center gap-1.5">
          {(
            [
              ["buy", "记买入"],
              ["sell", "记卖出"],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setSide(k)}
              className={`rounded px-2 py-0.5 text-xs ${side === k ? (k === "buy" ? "bg-up/15 font-medium text-up" : "bg-down/15 font-medium text-down") : "text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"}`}
            >
              {label}
            </button>
          ))}
          <span className="ml-auto text-[10px] text-zinc-500">记账按你在券商的实际成交价，与模拟账户无关</span>
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
          <input
            value={formSymbol}
            onChange={(e) => setFormSymbol(e.target.value)}
            placeholder="代码"
            className="w-20 rounded border border-zinc-200 bg-transparent px-1.5 py-0.5 font-mono dark:border-zinc-700"
            aria-label="代码"
          />
          <input
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            placeholder="成交价"
            inputMode="decimal"
            className="w-24 rounded border border-zinc-200 bg-transparent px-1.5 py-0.5 font-mono tabular-nums dark:border-zinc-700"
            aria-label="实际成交价"
            title="你在券商的真实成交价（默认带当前现价，请改成实际值）"
          />
          <input
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            placeholder="数量(股)"
            inputMode="numeric"
            className="w-20 rounded border border-zinc-200 bg-transparent px-1.5 py-0.5 font-mono tabular-nums dark:border-zinc-700"
            aria-label="数量"
          />
          <input
            value={fee}
            onChange={(e) => setFee(e.target.value)}
            placeholder="费用(可选)"
            inputMode="decimal"
            className="w-20 rounded border border-zinc-200 bg-transparent px-1.5 py-0.5 font-mono tabular-nums dark:border-zinc-700"
            aria-label="费用"
          />
          <input
            type="date"
            value={tradedAt}
            onChange={(e) => setTradedAt(e.target.value)}
            className="rounded border border-zinc-200 bg-transparent px-1.5 py-0.5 dark:border-zinc-700"
            aria-label="成交日期"
          />
          <button
            onClick={() => void submitTrade()}
            disabled={busy}
            className="rounded bg-sky-500/90 px-2.5 py-0.5 text-xs font-medium text-white hover:bg-sky-500 disabled:opacity-50"
          >
            记账
          </button>
        </div>
      </div>

      {(error || loadError) && (
        <div className="shrink-0 px-2 py-1.5 text-xs text-amber-600 dark:text-amber-300">{error ?? loadError}</div>
      )}

      {/* 持仓视图 */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-zinc-50 text-zinc-400 dark:bg-zinc-900">
            <tr className="text-left">
              <th className="px-2 py-1.5 font-normal">标的</th>
              <th className="px-2 py-1.5 text-right font-normal">数量</th>
              <th className="px-2 py-1.5 text-right font-normal">摊薄成本</th>
              <th className="px-2 py-1.5 text-right font-normal">现价</th>
              <th className="px-2 py-1.5 text-right font-normal">市值</th>
              <th className="px-2 py-1.5 text-right font-normal">浮动盈亏</th>
              <th className="px-2 py-1.5 text-right font-normal">已实现</th>
              <th className="px-2 py-1.5 text-right font-normal">操作</th>
            </tr>
          </thead>
          <tbody>
            {(data?.items ?? []).map((r) => (
              <tr key={r.symbol} className={`border-b border-zinc-100 last:border-0 dark:border-zinc-800/60 ${r.symbol === symbol ? "bg-sky-500/5" : ""}`}>
                <td className="px-2 py-1.5">
                  <div className="font-mono text-[10px] text-zinc-400">
                    {r.symbol}
                    {r.overridden && <span className="ml-1 rounded bg-amber-500/15 px-1 text-amber-600 dark:text-amber-300">已手动修正</span>}
                  </div>
                  <div>{r.name ?? "--"}</div>
                </td>
                <td className="px-2 py-1.5 text-right font-mono tabular-nums">{r.quantity}</td>
                <td className="px-2 py-1.5 text-right font-mono tabular-nums">{fmt(r.avg_cost)}</td>
                <td className="px-2 py-1.5 text-right font-mono tabular-nums">
                  {fmt(r.last_price)}
                  {r.day_change_pct != null && <span className={`ml-1 text-[10px] ${pctColor(r.day_change_pct)}`}>{pctText(r.day_change_pct)}</span>}
                </td>
                <td className="px-2 py-1.5 text-right font-mono tabular-nums">{fmt(r.market_value)}</td>
                <td className={`px-2 py-1.5 text-right font-mono font-medium tabular-nums ${pctColor(r.unrealized_pnl)}`}>
                  {r.unrealized_pnl != null ? `${r.unrealized_pnl > 0 ? "+" : ""}${fmt(r.unrealized_pnl)}` : "--"}
                  <span className="ml-1 text-[10px]">{pctText(r.unrealized_pct)}</span>
                </td>
                <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${r.realized_pnl > 0 ? "text-up" : r.realized_pnl < 0 ? "text-down" : "text-zinc-400"}`}>
                  {r.realized_pnl !== 0 ? `${r.realized_pnl > 0 ? "+" : ""}${fmt(r.realized_pnl)}` : "--"}
                </td>
                <td className="px-2 py-1.5 text-right">
                  <button
                    onClick={() => {
                      setEditSymbol(editSymbol === r.symbol ? null : r.symbol);
                      setEditQty(String(r.quantity));
                      setEditCost(String(r.cost_total));
                    }}
                    className="text-zinc-400 hover:text-sky-400"
                    title="手动修正数量/总成本"
                  >
                    改
                  </button>
                  <button onClick={() => void removeRow(r)} className="ml-1.5 text-zinc-400 hover:text-red-400" title="删除全部流水">
                    删
                  </button>
                  {editSymbol === r.symbol && (
                    <div className="mt-1 flex justify-end gap-1">
                      <input value={editQty} onChange={(e) => setEditQty(e.target.value)} className="w-14 rounded border border-zinc-200 bg-transparent px-1 py-0.5 font-mono dark:border-zinc-700" aria-label="修正数量" />
                      <input value={editCost} onChange={(e) => setEditCost(e.target.value)} className="w-20 rounded border border-zinc-200 bg-transparent px-1 py-0.5 font-mono dark:border-zinc-700" aria-label="修正总成本" />
                      <button onClick={() => void submitOverride(r)} disabled={busy} className="rounded bg-sky-500/90 px-1.5 text-white disabled:opacity-50">
                        保存
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
            {(data?.items.length ?? 0) === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-zinc-400">
                  {data ? "暂无真实持仓。在上方记一笔买入（按你在券商的实际成交价）。" : "加载中…"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {(data?.cleared.length ?? 0) > 0 && (
          <div className="border-t border-zinc-100 px-2 py-1.5 text-[11px] text-zinc-400 dark:border-zinc-800/60">
            已清仓：
            {(data?.cleared ?? []).map((c) => (
              <span key={c.symbol} className="mr-2">
                {c.name ?? c.symbol}
                <span className={`ml-0.5 font-mono ${c.realized_pnl > 0 ? "text-up" : c.realized_pnl < 0 ? "text-down" : ""}`}>
                  {c.realized_pnl > 0 ? "+" : ""}
                  {fmt(c.realized_pnl)}
                </span>
              </span>
            ))}
          </div>
        )}
      </div>

      {data && (
        <div className="shrink-0 border-t border-zinc-100 px-2 py-1.5 text-[11px] tabular-nums dark:border-zinc-800/60">
          总市值 <span className="font-mono">{fmt(data.total.market_value)}</span> · 总成本{" "}
          <span className="font-mono">{fmt(data.total.cost_total)}</span> · 浮动盈亏{" "}
          <span className={`font-mono ${pctColor(data.total.unrealized_pnl)}`}>
            {data.total.unrealized_pnl > 0 ? "+" : ""}
            {fmt(data.total.unrealized_pnl)}
          </span>{" "}
          · 累计已实现 <span className={`font-mono ${pctColor(data.total.realized_pnl)}`}>{data.total.realized_pnl > 0 ? "+" : ""}{fmt(data.total.realized_pnl)}</span>
          <span className="ml-2 text-zinc-500">不构成买卖建议</span>
        </div>
      )}
    </div>
  );
}
