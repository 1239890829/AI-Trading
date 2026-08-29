"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { KlineChart } from "@/components/kline-chart";
import { MinuteChart } from "@/components/minute-chart";
import { Panel } from "@/components/panel";
import { QualityBadge } from "@/components/quality-badge";
import { PriceFlash } from "@/components/price-flash";
import { fmt, fmtAmount, fmtVolume, pctColor, pctText, timeText } from "@/lib/format";
import { addToWatchlist, API_BASE, getKline, getMinuteLine, getOrderBook, getQuotes, getTrades, getWatchlist, type MinutePoint } from "@/lib/api";
import type { Kline, OrderBook, Quote, Trade } from "@/types/market";

interface LonghuSeat { symbol: string; side: string; seat?: string | null; seat_type: string; buy?: number | null; sell?: number | null; net?: number | null; reason?: string | null; rise_probability_3day?: number | null; source: string }
interface LonghuDetail { symbol: string; trade_date: string; buy_seats: LonghuSeat[]; sell_seats: LonghuSeat[]; empty?: boolean }
interface LonghuHistory { trade_date: string; close?: number | null; change_pct?: number | null; net_buy?: number | null; reason?: string | null; after_1d?: number | null; after_3d?: number | null; after_5d?: number | null; after_10d?: number | null }
interface LonghuStats { count: number; avg_after_5d?: number | null; win_rate_5d?: number | null }

type Tab = "kline" | "minute" | "book" | "trades" | "longhu";

export default function StockPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = String(params.symbol ?? "");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [bars, setBars] = useState<Kline[]>([]);
  const [book, setBook] = useState<OrderBook | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [minutes, setMinutes] = useState<MinutePoint[]>([]);
  const [longhu, setLonghu] = useState<{ detail: LonghuDetail; history: LonghuHistory[]; stats: LonghuStats } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("kline");
  const [inWatchlist, setInWatchlist] = useState(false);
  const [added, setAdded] = useState(false);

  const loadQuote = useCallback(async () => {
    if (!/^\d{6}$/.test(symbol)) {
      setError(`非法股票代码：${symbol}`);
      return;
    }
    try {
      const [qs] = await Promise.all([getQuotes([symbol])]);
      if (qs.length === 0) {
        setError(`后端缓存中无 ${symbol} 的行情（可先在自选页添加，或稍后重试）`);
      } else {
        setQuote(qs[0]);
        setError(null);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }, [symbol]);

  const loadDetail = useCallback(async () => {
    if (!/^\d{6}$/.test(symbol)) return;
    const [b, ob, tr, minutes, lh] = await Promise.all([
      getKline(symbol, "1d", 250).catch(() => [] as Kline[]),
      getOrderBook(symbol).catch(() => null),
      getTrades(symbol, 50).catch(() => [] as Trade[]),
      getMinuteLine(symbol).catch(() => [] as MinutePoint[]),
      fetch(`${API_BASE}/api/longhu/${symbol}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]);
    setBars(b);
    setBook(ob);
    setTrades(tr);
    setMinutes(minutes);
    if (lh?.data) setLonghu(lh.data);
  }, [symbol]);

  useEffect(() => {
    void loadQuote();
    const t = setInterval(loadQuote, 6000);
    return () => clearInterval(t);
  }, [loadQuote]);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  useEffect(() => {
    let alive = true;
    getWatchlist()
      .then((list) => alive && setInWatchlist(list.some((i) => i.symbol === symbol)))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [symbol]);

  async function add() {
    try {
      await addToWatchlist(symbol, quote?.name ?? undefined);
      setAdded(true);
      setInWatchlist(true);
    } catch {}
  }

  return (
    <main className="h-full flex flex-col overflow-y-auto px-4 py-3 max-w-[1600px] mx-auto w-full">
      <nav className="mb-4 text-xs text-zinc-400">
        <Link href="/workbench" className="hover:underline">
          工作台
        </Link>
        <span className="mx-1">/</span>
        <span className="font-mono">{symbol}</span>
      </nav>

      {error && (
        <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-600 dark:text-amber-300">
          {error}
        </div>
      )}

      {quote && (
        <div className="mb-4 rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <h1 className="text-xl font-semibold">
              {quote.name ?? "--"} <span className="ml-1 font-mono text-sm text-zinc-400">{quote.market}.{quote.symbol}</span>
            </h1>
            <PriceFlash value={quote.price} className={`font-mono text-3xl font-semibold ${pctColor(quote.change_pct)}`}>{fmt(quote.price)}</PriceFlash>
            <span className={`font-mono ${pctColor(quote.change)}`}>
              {quote.change != null ? `${quote.change > 0 ? "+" : ""}${fmt(quote.change)}` : "--"}（{pctText(quote.change_pct)}）
            </span>
            <QualityBadge quality={quote.quality} reasons={quote.quality_reasons} />
            <div className="ml-auto">
              {inWatchlist || added ? (
                <span className="text-xs text-zinc-400">已在自选</span>
              ) : (
                <button onClick={() => void add()} className="rounded-md border border-up/60 px-3 py-1 text-sm text-up hover:bg-up/10">
                  ＋ 加入自选
                </button>
              )}
            </div>
          </div>
          <dl className="mt-3 grid grid-cols-3 gap-x-6 gap-y-1 text-sm md:grid-cols-6">
            {(
              [
                ["今开", fmt(quote.open)],
                ["最高", fmt(quote.high)],
                ["最低", fmt(quote.low)],
                ["昨收", fmt(quote.prev_close)],
                ["成交量(手)", fmtVolume(quote.volume)],
                ["成交额", fmtAmount(quote.amount)],
                ["换手率", quote.turnover_rate != null ? `${fmt(quote.turnover_rate)}%` : "--"],
              ] as const
            ).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2">
                <dt className="text-zinc-400">{k}</dt>
                <dd className="font-mono">{v}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-xs text-zinc-400">
            数据时间 {timeText(quote.data_timestamp)} · 来源 {quote.source} · 接收 {timeText(quote.received_at)}
            {quote.source === "mock" && <span className="ml-2 text-amber-400">（mock 演示数据，非实时行情）</span>}
          </p>
        </div>
      )}

      <div className="mb-3 flex gap-1">
        {(
          [
            ["kline", "K 线"],
            ["minute", "分时"],
            ["book", "盘口"],
            ["trades", "逐笔成交"],
            ["longhu", "龙虎榜"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`rounded-md px-3 py-1.5 text-sm ${
              tab === key
                ? "bg-zinc-100 font-medium dark:bg-zinc-800"
                : "text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "kline" && (
        <Panel title="日 K 线（近 250 日 · 前复权）" source={bars[0]?.source}>
          {bars.length > 0 ? (
            <KlineChart bars={bars} height={420} />
          ) : (
            <p className="px-4 py-10 text-center text-sm text-zinc-400">等待 K 线数据…</p>
          )}
        </Panel>
      )}

      {tab === "minute" && (
        <Panel title="当日分时（1 分钟）" source={minutes[0]?.source} className="min-h-[420px]">
          {minutes.length > 0 ? (
            <MinuteChart points={minutes} className="h-[420px]" />
          ) : (
            <p className="px-4 py-10 text-center text-sm text-zinc-400">暂无分时数据</p>
          )}
        </Panel>
      )}

      {tab === "book" && (
        <Panel title="五档盘口" source={book?.source} dataTimestamp={book?.data_timestamp} quality={book?.quality} qualityReasons={book?.quality_reasons}>
          {book ? (
            <div className="max-w-md">
              <table className="w-full text-sm">
                <tbody>
                  {[...book.asks].reverse().map((lv, i) => (
                    <tr key={`a${i}`} className="border-b border-zinc-100 dark:border-zinc-800/60">
                      <td className="px-3 py-1.5 text-xs text-zinc-400">卖{book.asks.length - i}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-down">{fmt(lv.price)}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-zinc-400">{fmt(lv.volume, 0)}</td>
                    </tr>
                  ))}
                  {[...book.bids].map((lv, i) => (
                    <tr key={`b${i}`}>
                      <td className="px-3 py-1.5 text-xs text-zinc-400">买{i + 1}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-up">{fmt(lv.price)}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-zinc-400">{fmt(lv.volume, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="px-4 py-10 text-center text-sm text-zinc-400">盘口数据不可用</p>
          )}
        </Panel>
      )}

      {tab === "trades" && (
        <Panel title="逐笔成交" source={trades[0]?.source}>
          {trades.length > 0 ? (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-zinc-400">
                <tr className="border-b border-zinc-200 dark:border-zinc-800">
                  <th className="px-3 py-2 font-medium">时间</th>
                  <th className="px-2 py-2 text-right font-medium">价格</th>
                  <th className="px-2 py-2 text-right font-medium">数量(手)</th>
                  <th className="px-3 py-2 text-right font-medium">方向</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                    <td className="px-3 py-1.5 font-mono text-xs text-zinc-400">{timeText(t.ts)}</td>
                    <td className={`px-2 py-1.5 text-right font-mono ${t.side === "buy" ? "text-up" : t.side === "sell" ? "text-down" : "text-zinc-300"}`}>
                      {fmt(t.price)}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono text-zinc-400">{fmtVolume(t.volume)}</td>
                    <td className="px-3 py-1.5 text-right text-xs text-zinc-400">{t.side === "buy" ? "B" : t.side === "sell" ? "S" : "·"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="px-4 py-10 text-center text-sm text-zinc-400">暂无逐笔数据</p>
          )}
        </Panel>
      )}

      {tab === "longhu" && (
        <Panel title={`龙虎榜席位（${longhu?.detail.trade_date ?? "--"}）`} source={longhu?.detail.buy_seats[0]?.source} className="min-h-0">
          {!longhu ? (
            <p className="px-4 py-10 text-center text-sm text-zinc-400">加载中…</p>
          ) : (
            <>
              {longhu.detail.empty ? (
                <p className="px-4 py-6 text-center text-sm text-zinc-400">该交易日未上榜</p>
              ) : (
                <div className="grid gap-4 md:grid-cols-2">
                  {(["buy", "sell"] as const).map((side) => (
                    <div key={side}>
                      <h3 className={`px-3 py-1.5 text-xs font-medium ${side === "buy" ? "text-up" : "text-down"}`}>
                        {side === "buy" ? "买入席位" : "卖出席位"}
                      </h3>
                      <table className="w-full text-sm">
                        <tbody>
                          {longhu.detail[side === "buy" ? "buy_seats" : "sell_seats"].map((st, i) => (
                            <tr key={i} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                              <td className="px-3 py-1.5 text-xs text-zinc-400">{i + 1}</td>
                              <td className="px-2 py-1.5">
                                <div className="truncate" title={st.seat ?? ""}>{st.seat}</div>
                                <div className="text-xs text-zinc-400">{st.seat_type}{st.rise_probability_3day != null ? ` · 席位3日胜率 ${st.rise_probability_3day.toFixed(1)}%` : ""}</div>
                              </td>
                              <td className={`px-2 py-1.5 text-right font-mono text-xs tabular-nums ${side === "buy" ? "text-up" : "text-down"}`}>
                                {fmtAmount(side === "buy" ? st.buy : st.sell)}
                              </td>
                              <td className={`px-3 py-1.5 text-right font-mono text-xs tabular-nums ${(st.net ?? 0) > 0 ? "text-up" : "text-down"}`}>
                                {fmtAmount(st.net)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ))}
                </div>
              )}
              <div className="border-t border-zinc-200 px-4 py-3 text-xs text-zinc-400 dark:border-zinc-800">
                历史上榜 <span className="font-mono text-zinc-200">{longhu.stats.count}</span> 次 · 上榜后5日平均{" "}
                <span className={`font-mono ${pctColor(longhu.stats.avg_after_5d)}`}>{pctText(longhu.stats.avg_after_5d)}</span> · 5日胜率{" "}
                <span className="font-mono text-zinc-200">{longhu.stats.win_rate_5d != null ? `${(longhu.stats.win_rate_5d * 100).toFixed(1)}%` : "--"}</span>
              </div>
              <table className="w-full text-sm">
                <thead className="bg-zinc-50 text-left text-xs text-zinc-400 dark:bg-zinc-900/50">
                  <tr>{["日期","收盘","涨跌幅","净买额","上榜原因","T+1","T+3","T+5","T+10"].map(h => <th key={h} className={`px-3 py-2 font-medium ${["日期","上榜原因"].includes(h) ? "" : "text-right"}`}>{h}</th>)}</tr>
                </thead>
                <tbody>
                  {longhu.history.slice(0, 15).map((h, i) => (
                    <tr key={`${h.trade_date}-${i}`} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                      <td className="px-3 py-1.5 font-mono text-xs">{h.trade_date}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{fmt(h.close)}</td>
                      <td className={`px-2 py-1.5 text-right font-mono ${pctColor(h.change_pct)}`}>{pctText(h.change_pct)}</td>
                      <td className={`px-2 py-1.5 text-right font-mono text-xs ${(h.net_buy ?? 0) > 0 ? "text-up" : "text-down"}`}>{fmtAmount(h.net_buy)}</td>
                      <td className="px-2 py-1.5 max-w-[220px] truncate text-xs text-zinc-400" title={h.reason ?? ""}>{h.reason ?? "--"}</td>
                      {[h.after_1d, h.after_3d, h.after_5d, h.after_10d].map((v, j) => (
                        <td key={j} className={`px-3 py-1.5 text-right font-mono text-xs ${pctColor(v)}`}>{pctText(v)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </Panel>
      )}

      <p className="mt-6 rounded-lg border border-zinc-200 px-4 py-3 text-xs text-zinc-400 dark:border-zinc-800">
        龙虎榜 / 财务 / 估值 / 筹码 / AI 分析等标签页按开发顺序在后续阶段接入（数据模型与 API 契约见 docs/api.md）。
      </p>
    </main>
  );
}
