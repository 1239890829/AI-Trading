"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { KlineChart } from "@/components/kline-chart";
import { Panel } from "@/components/panel";
import { QualityBadge } from "@/components/quality-badge";
import { useQuoteStream, StreamStatus } from "@/hooks/use-quote-stream";
import {
  getKline,
  getMarketOverview,
  getOrderBook,
  getQuotes,
  getTrades,
  getWatchlist,
  removeFromWatchlist,
} from "@/lib/api";
import { fmt, fmtAmount, fmtVolume, pctColor, pctText, timeText } from "@/lib/format";
import type { Kline, OrderBook, Quote, Trade } from "@/types/market";

const STATUS_LABEL: Record<StreamStatus, { text: string; cls: string }> = {
  connecting: { text: "连接中", cls: "text-zinc-400" },
  live: { text: "WS 实时推送", cls: "text-sky-400" },
  polling: { text: "WS 断线 · REST 轮询", cls: "text-amber-400" },
  error: { text: "连接失败", cls: "text-red-400" },
};

export default function WorkbenchPage() {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [indices, setIndices] = useState<Quote[]>([]);
  const [totalAmount, setTotalAmount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string>("");
  const [selected, setSelected] = useState<string>("600519");

  const [bars, setBars] = useState<Kline[]>([]);
  const [book, setBook] = useState<OrderBook | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [detailError, setDetailError] = useState<string | null>(null);

  const { quotes, status } = useQuoteStream(symbols);

  // WS 尚未覆盖的 symbol（如刚加自选）用 REST 兜底
  const [extra, setExtra] = useState<Record<string, Quote>>({});
  const merged: Record<string, Quote> = { ...extra, ...quotes };

  const loadBase = useCallback(async () => {
    try {
      const [wl, overview] = await Promise.all([getWatchlist(), getMarketOverview()]);
      setSymbols(wl.map((i) => i.symbol));
      setIndices(overview.indices);
      setTotalAmount(overview.total_amount);
      setError(null);
      setUpdatedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    } catch {
      setError("无法连接后端行情服务。请先启动：cd backend && uvicorn app.main:app --reload --port 8000");
    }
  }, []);

  useEffect(() => {
    void loadBase();
    const t = setInterval(loadBase, 10000);
    return () => clearInterval(t);
  }, [loadBase]);

  // 若 WS 尚未覆盖到的 symbol（如刚加自选），用 REST 兜底拉一次
  useEffect(() => {
    if (symbols.length === 0) return;
    const missing = symbols.filter((s) => !(s in merged));
    if (missing.length > 0) {
      getQuotes(missing)
        .then((qs) =>
          setExtra((prev) => Object.fromEntries([...Object.entries(prev), ...qs.map((q) => [q.symbol, q])]))
        )
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbols]);

  useEffect(() => {
    if (!selected) return;
    let alive = true;
    setDetailError(null);
    Promise.all([getKline(selected, "1d", 120), getOrderBook(selected).catch(() => null), getTrades(selected, 30).catch(() => [])])
      .then(([b, ob, tr]) => {
        if (!alive) return;
        setBars(b);
        setBook(ob);
        setTrades(tr);
      })
      .catch((e: Error) => alive && setDetailError(e.message));
    return () => {
      alive = false;
    };
  }, [selected]);

  const watchQuotes: Quote[] = symbols.map((s) => merged[s]).filter(Boolean);
  const d = merged[selected];

  async function remove(symbol: string) {
    try {
      await removeFromWatchlist(symbol);
      setSymbols((prev) => prev.filter((s) => s !== symbol));
    } catch {}
  }

  return (
    <main className="py-6">
      {error && (
        <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-600 dark:text-amber-300">
          {error}
        </div>
      )}

      {/* 指数行情 */}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        {indices.map((q) => (
          <div key={q.symbol} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
            <div className="flex items-baseline justify-between">
              <span className="text-xs text-zinc-400">{q.name ?? q.symbol}</span>
              <QualityBadge quality={q.quality} reasons={q.quality_reasons} />
            </div>
            <div className="mt-1 font-mono text-lg font-semibold">{fmt(q.price)}</div>
            <div className={`font-mono text-xs ${pctColor(q.change_pct)}`}>
              {pctText(q.change_pct)} · {q.source}
            </div>
          </div>
        ))}
        {indices.length === 0 && !error && <div className="col-span-6 text-sm text-zinc-400">加载中…</div>}
      </div>

      <div className="mb-4 flex flex-wrap items-center justify-between gap-2 text-xs text-zinc-400">
        <span>
          两市成交额合计：<span className="font-mono text-zinc-200">{fmtAmount(totalAmount)}</span>
        </span>
        <span>
          行情状态：<span className={STATUS_LABEL[status].cls}>{STATUS_LABEL[status].text}</span> · 指数刷新{" "}
          {updatedAt || "--"} · 数据来源 {indices[0]?.source ?? "--"}
        </span>
      </div>

      <div className="grid gap-4 lg:grid-cols-[360px,1fr]">
        {/* 自选股 */}
        <Panel
          title="自选股"
          extra={
            <Link href="/watchlist" className="text-sky-400 hover:underline">
              管理
            </Link>
          }
          className="max-h-[640px]"
        >
          {watchQuotes.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-zinc-400">
              自选为空或行情未就绪。
              <br />
              可在顶部搜索框添加股票。
            </p>
          ) : (
            <table className="w-full text-sm">
              <tbody>
                {watchQuotes.map((q) => (
                  <tr
                    key={q.symbol}
                    onClick={() => setSelected(q.symbol)}
                    className={`cursor-pointer border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900 ${
                      selected === q.symbol ? "bg-zinc-50 dark:bg-zinc-900" : ""
                    }`}
                  >
                    <td className="px-3 py-2">
                      <div className="font-mono text-xs text-zinc-400">{q.symbol}</div>
                      <div className="text-sm">{q.name ?? "--"}</div>
                    </td>
                    <td className="px-2 py-2 text-right font-mono">{fmt(q.price)}</td>
                    <td className={`px-2 py-2 text-right font-mono ${pctColor(q.change_pct)}`}>{pctText(q.change_pct)}</td>
                    <td className="px-2 py-2 text-right font-mono text-xs text-zinc-400">{fmtAmount(q.amount)}</td>
                    <td className="px-2 py-2 text-right">
                      {q.quality !== "high" && <QualityBadge quality={q.quality} reasons={q.quality_reasons} />}
                    </td>
                    <td className="pr-2 text-right">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          void remove(q.symbol);
                        }}
                        className="text-xs text-zinc-400 hover:text-red-400"
                        title="移出自选"
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        {/* 个股详情 */}
        <div className="flex flex-col gap-4">
          {d && (
            <div className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <h1 className="text-lg font-semibold">
                  {d.name ?? "--"} <span className="ml-1 font-mono text-sm text-zinc-400">{d.market}.{d.symbol}</span>
                </h1>
                <span className={`font-mono text-2xl font-semibold ${pctColor(d.change_pct)}`}>{fmt(d.price)}</span>
                <span className={`font-mono ${pctColor(d.change)}`}>
                  {d.change != null ? `${d.change > 0 ? "+" : ""}${fmt(d.change)}` : "--"}（{pctText(d.change_pct)}）
                </span>
                <QualityBadge quality={d.quality} reasons={d.quality_reasons} />
              </div>
              <dl className="mt-3 grid grid-cols-3 gap-x-6 gap-y-1 text-sm md:grid-cols-6">
                {(
                  [
                    ["今开", fmt(d.open)],
                    ["最高", fmt(d.high)],
                    ["最低", fmt(d.low)],
                    ["昨收", fmt(d.prev_close)],
                    ["成交量(手)", fmtVolume(d.volume)],
                    ["成交额", fmtAmount(d.amount)],
                    ["换手率", d.turnover_rate != null ? `${fmt(d.turnover_rate)}%` : "--"],
                  ] as const
                ).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-2">
                    <dt className="text-zinc-400">{k}</dt>
                    <dd className="font-mono">{v}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-2 text-xs text-zinc-400">
                数据时间 {timeText(d.data_timestamp)} · 来源 {d.source} · 接收 {timeText(d.received_at)}
              </p>
            </div>
          )}

          {detailError && (
            <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-500">
              K线加载失败：{detailError}
            </div>
          )}

          <Panel title={`日 K 线（近 120 日 · 前复权）`} className="min-h-[400px]">
            {bars.length > 0 ? (
              <KlineChart bars={bars} />
            ) : (
              <p className="px-4 py-10 text-center text-sm text-zinc-400">等待 K 线数据…</p>
            )}
          </Panel>

          <div className="grid gap-4 md:grid-cols-2">
            <Panel
              title="五档盘口"
              source={book?.source}
              dataTimestamp={book?.data_timestamp}
              quality={book?.quality}
              qualityReasons={book?.quality_reasons}
            >
              {book ? (
                <table className="w-full text-sm">
                  <tbody>
                    {[...book.asks].reverse().map((lv, i) => (
                      <tr key={`a${i}`} className="border-b border-zinc-100 dark:border-zinc-800/60">
                        <td className="px-3 py-1 text-xs text-zinc-400">卖{book.asks.length - i}</td>
                        <td className="px-2 py-1 text-right font-mono text-down">{fmt(lv.price)}</td>
                        <td className="px-3 py-1 text-right font-mono text-zinc-400">{fmt(lv.volume, 0)}</td>
                      </tr>
                    ))}
                    {[...book.bids].map((lv, i) => (
                      <tr key={`b${i}`}>
                        <td className="px-3 py-1 text-xs text-zinc-400">买{i + 1}</td>
                        <td className="px-2 py-1 text-right font-mono text-up">{fmt(lv.price)}</td>
                        <td className="px-3 py-1 text-right font-mono text-zinc-400">{fmt(lv.volume, 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="px-4 py-8 text-center text-sm text-zinc-400">盘口数据不可用（免费源仅盘中提供）</p>
              )}
            </Panel>

            <Panel title="逐笔成交（最近 30 笔）" source={trades[0]?.source}>
              {trades.length > 0 ? (
                <table className="w-full text-sm">
                  <tbody>
                    {trades.map((t, i) => (
                      <tr key={i} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                        <td className="px-3 py-1 font-mono text-xs text-zinc-400">{timeText(t.ts)}</td>
                        <td className={`px-2 py-1 text-right font-mono ${t.side === "buy" ? "text-up" : t.side === "sell" ? "text-down" : "text-zinc-300"}`}>
                          {fmt(t.price)}
                        </td>
                        <td className="px-2 py-1 text-right font-mono text-zinc-400">{fmtVolume(t.volume)}</td>
                        <td className="px-3 py-1 text-right text-xs text-zinc-400">
                          {t.side === "buy" ? "B" : t.side === "sell" ? "S" : "·"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="px-4 py-8 text-center text-sm text-zinc-400">暂无逐笔数据</p>
              )}
            </Panel>
          </div>
        </div>
      </div>
    </main>
  );
}
