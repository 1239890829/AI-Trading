"use client";

import { useEffect, useState } from "react";
import { KlineChart } from "@/components/kline-chart";
import { MinuteChart } from "@/components/minute-chart";
import { Panel } from "@/components/panel";
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { addToWatchlist, API_BASE, getKline, getMinuteLine, getOrderBook, getQuotes, getTrades, type MinutePoint } from "@/lib/api";
import { fmt, fmtAmount, fmtVolume, pctColor, pctText, timeText } from "@/lib/format";
import type { Kline, OrderBook, Trade } from "@/types/market";

type Tab = "kline" | "minute" | "book" | "trades" | "longhu";

interface LonghuSeat { seat?: string | null; seat_type: string; buy?: number | null; sell?: number | null; net?: number | null; rise_probability_3day?: number | null }
interface LonghuDetail { trade_date: string; buy_seats: LonghuSeat[]; sell_seats: LonghuSeat[]; empty?: boolean }
interface LonghuHistory { trade_date: string; close?: number | null; change_pct?: number | null; net_buy?: number | null; reason?: string | null; after_1d?: number | null; after_3d?: number | null; after_5d?: number | null; after_10d?: number | null }
interface LonghuStats { count: number; avg_after_5d?: number | null; win_rate_5d?: number | null }

/**
 * 个股详情面板（工作台右栏 / 个股页共用）：行情头 + 全页签（K线/分时/盘口/逐笔/龙虎榜）。
 * 单 symbol 行情走 WS（useQuoteStream），明细数据随 symbol 切换拉取。
 */
export function StockDetailPanel({ symbol }: { symbol: string }) {
  const [tab, setTab] = useState<Tab>("kline");
  const [bars, setBars] = useState<Kline[]>([]);
  const [book, setBook] = useState<OrderBook | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [minutes, setMinutes] = useState<MinutePoint[]>([]);
  const [longhu, setLonghu] = useState<{ detail: LonghuDetail; history: LonghuHistory[]; stats: LonghuStats } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [inWatchlist, setInWatchlist] = useState(false);

  const [d, setD] = useState<Quote | null>(null);

  // 单股行情：REST 轮询（/api/quotes/{symbol} 对非自选股走实时链）
  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    const tick = async () => {
      try {
        const qs = await getQuotes([symbol]);
        if (alive && qs[0]) {
          setD(qs[0]);
          setError(null);
        }
      } catch {
        if (alive) setError(`无法获取 ${symbol} 行情，请确认后端已启动`);
      }
    };
    void tick();
    const t = setInterval(tick, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [symbol]);

  useEffect(() => {
    let alive = true;
    getWatchlistSafe().then((list) => alive && setInWatchlist(list.includes(symbol)));
    return () => {
      alive = false;
    };
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    setTab("kline");
    setError(null);
    setBars([]);
    setBook(null);
    setTrades([]);
    setMinutes([]);
    setLonghu(null);
    Promise.all([
      getKline(symbol, "1d", 120),
      getOrderBook(symbol).catch(() => null),
      getTrades(symbol, 30).catch(() => [] as Trade[]),
      getMinuteLine(symbol).catch(() => [] as MinutePoint[]),
      fetch(`${API_BASE}/api/longhu/${symbol}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ])
      .then(([bars2, ob2, tr2, minutes2, lh]) => {
        if (!alive) return;
        setBars(bars2);
        setBook(ob2);
        setTrades(tr2);
        setMinutes(minutes2);
        if (lh?.data) setLonghu(lh.data);
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [symbol]);

  async function add() {
    try {
      await addToWatchlist(symbol);
      setInWatchlist(true);
    } catch {}
  }

  const stats = d
    ? ([
        ["今开", fmt(d.open)],
        ["最高", fmt(d.high)],
        ["最低", fmt(d.low)],
        ["昨收", fmt(d.prev_close)],
        ["成交量", fmtVolume(d.volume) + " 手"],
        ["成交额", fmtAmount(d.amount)],
        ["换手率", d.turnover_rate != null ? `${fmt(d.turnover_rate)}%` : "--"],
        ["来源", d.source],
      ] as const)
    : [];

  return (
    <div className="flex min-h-0 min-w-0 flex-col gap-3">
      {error && (
        <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-600 dark:text-amber-300">
          {error}
        </div>
      )}

      {/* 行情头 */}
      {d && (
        <div className="shrink-0 rounded-xl border border-zinc-200 dark:border-zinc-800">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-4 pt-3">
            <div className="flex items-baseline gap-2">
              <h1 className="text-lg font-semibold">{d.name ?? "--"}</h1>
              <span className="font-mono text-sm text-zinc-400">{d.market}.{d.symbol}</span>
              <QualityBadge quality={d.quality} reasons={d.quality_reasons} />
              {inWatchlist ? (
                <span className="text-xs text-zinc-400">已在自选</span>
              ) : (
                <button onClick={() => void add()} className="rounded-md border border-up/60 px-2 py-0.5 text-xs text-up hover:bg-up/10">
                  ＋ 自选
                </button>
              )}
            </div>
            <div className="flex items-baseline gap-3">
              <PriceFlash value={d.price} className={`font-mono text-3xl font-semibold tabular-nums ${pctColor(d.change_pct)}`}>
                {fmt(d.price)}
              </PriceFlash>
              <span className={`font-mono text-sm tabular-nums ${pctColor(d.change)}`}>
                {d.change != null ? `${d.change > 0 ? "+" : ""}${fmt(d.change)}` : "--"}（{pctText(d.change_pct)}）
              </span>
            </div>
          </div>
          <div className="mt-2 grid grid-cols-4 divide-x divide-zinc-100 border-t border-zinc-100 dark:divide-zinc-800/60 dark:border-zinc-800/60">
            {stats.map(([k, v], i) => (
              <div key={k} className={`px-3 py-2 ${i < 4 ? "border-b border-zinc-100 dark:border-zinc-800/60" : ""}`}>
                <div className="text-xs text-zinc-400">{k}</div>
                <div className="font-mono text-sm tabular-nums">{v}</div>
              </div>
            ))}
          </div>
          <div className="border-t border-zinc-100 px-4 py-1.5 text-right text-xs text-zinc-400 dark:border-zinc-800/60">
            数据时间 {timeText(d.data_timestamp)} · 来源 {d.source} · 接收 {timeText(d.received_at)}
          </div>
        </div>
      )}

      {/* 页签 */}
      <div className="flex shrink-0 gap-1">
        {(
          [
            ["kline", "K 线"],
            ["minute", "分时"],
            ["book", "盘口"],
            ["trades", "逐笔"],
            ["longhu", "龙虎榜"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`rounded-md px-3 py-1.5 text-sm ${
              tab === key ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "kline" && (
        <Panel title="日 K 线（近 120 日 · 前复权）" className="min-h-0 flex-1 overflow-hidden">
          {bars.length > 0 ? <KlineChart bars={bars} className="h-full" /> : <p className="px-4 py-10 text-center text-sm text-zinc-400">等待 K 线数据…</p>}
        </Panel>
      )}

      {tab === "minute" && (
        <Panel title="当日分时（1 分钟）" source={minutes[0]?.source} className="min-h-0 flex-1 overflow-hidden">
          {minutes.length > 0 ? <MinuteChart points={minutes} className="h-full" /> : <p className="px-4 py-10 text-center text-sm text-zinc-400">暂无分时数据</p>}
        </Panel>
      )}

      {tab === "book" && (
        <Panel title="五档盘口" source={book?.source} dataTimestamp={book?.data_timestamp} quality={book?.quality} qualityReasons={book?.quality_reasons} className="min-h-0 flex-1 overflow-hidden">
          {book ? (
            <div className="max-w-md">
              <table className="w-full text-sm">
                <tbody>
                  {[...book.asks].reverse().map((lv, i) => (
                    <tr key={`a${i}`} className="border-b border-zinc-100 dark:border-zinc-800/60">
                      <td className="px-3 py-1.5 text-xs text-zinc-400">卖{book.asks.length - i}</td>
                      <td className="px-2 py-1.5 text-right font-mono tabular-nums text-down">{fmt(lv.price)}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-zinc-400">{fmt(lv.volume, 0)}</td>
                    </tr>
                  ))}
                  {[...book.bids].map((lv, i) => (
                    <tr key={`b${i}`}>
                      <td className="px-3 py-1.5 text-xs text-zinc-400">买{i + 1}</td>
                      <td className="px-2 py-1.5 text-right font-mono tabular-nums text-up">{fmt(lv.price)}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-zinc-400">{fmt(lv.volume, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="px-4 py-10 text-center text-sm text-zinc-400">盘口数据不可用（免费源仅盘中提供）</p>
          )}
        </Panel>
      )}

      {tab === "trades" && (
        <Panel title="逐笔成交（最近 30 笔）" source={trades[0]?.source} className="min-h-0 flex-1 overflow-hidden">
          {trades.length > 0 ? (
            <table className="w-full text-sm">
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                    <td className="px-3 py-1 font-mono text-xs tabular-nums text-zinc-400">{timeText(t.ts)}</td>
                    <td className={`px-2 py-1 text-right font-mono tabular-nums ${t.side === "buy" ? "text-up" : t.side === "sell" ? "text-down" : "text-zinc-300"}`}>{fmt(t.price)}</td>
                    <td className="px-2 py-1 text-right font-mono text-xs tabular-nums text-zinc-400">{fmtVolume(t.volume)}</td>
                    <td className="px-3 py-1 text-right text-xs text-zinc-400">{t.side === "buy" ? "B" : t.side === "sell" ? "S" : "·"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="px-4 py-6 text-center text-sm text-zinc-400">暂无逐笔数据（东财源限流，盘中细粒度请看分时）</p>
          )}
        </Panel>
      )}

      {tab === "longhu" && (
        <Panel title={`龙虎榜席位（${longhu?.detail.trade_date ?? "--"}）`} source="eastmoney" className="min-h-0 flex-1 overflow-hidden">
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
                      <h3 className={`px-3 py-1.5 text-xs font-medium ${side === "buy" ? "text-up" : "text-down"}`}>{side === "buy" ? "买入席位" : "卖出席位"}</h3>
                      <table className="w-full text-sm">
                        <tbody>
                          {longhu.detail[side === "buy" ? "buy_seats" : "sell_seats"].map((st, i) => (
                            <tr key={i} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                              <td className="px-3 py-1.5 text-xs text-zinc-400">{i + 1}</td>
                              <td className="px-2 py-1.5">
                                <div className="truncate" title={st.seat ?? ""}>{st.seat}</div>
                                <div className="text-xs text-zinc-400">{st.seat_type}{st.rise_probability_3day != null ? ` · 席位3日胜率 ${st.rise_probability_3day.toFixed(1)}%` : ""}</div>
                              </td>
                              <td className={`px-2 py-1.5 text-right font-mono text-xs tabular-nums ${side === "buy" ? "text-up" : "text-down"}`}>{fmtAmount(side === "buy" ? st.buy : st.sell)}</td>
                              <td className={`px-3 py-1.5 text-right font-mono text-xs tabular-nums ${(st.net ?? 0) > 0 ? "text-up" : "text-down"}`}>{fmtAmount(st.net)}</td>
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
                  <tr>{["日期", "收盘", "涨跌幅", "净买额", "上榜原因", "T+1", "T+3", "T+5", "T+10"].map((h) => <th key={h} className={`px-3 py-2 font-medium ${["日期", "上榜原因"].includes(h) ? "" : "text-right"}`}>{h}</th>)}</tr>
                </thead>
                <tbody>
                  {longhu.history.slice(0, 15).map((h, i) => (
                    <tr key={`${h.trade_date}-${i}`} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                      <td className="px-3 py-1.5 font-mono text-xs">{h.trade_date}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{fmt(h.close)}</td>
                      <td className={`px-2 py-1.5 text-right font-mono ${pctColor(h.change_pct)}`}>{pctText(h.change_pct)}</td>
                      <td className={`px-2 py-1.5 text-right font-mono text-xs ${(h.net_buy ?? 0) > 0 ? "text-up" : "text-down"}`}>{fmtAmount(h.net_buy)}</td>
                      <td className="max-w-[220px] truncate px-2 py-1.5 text-xs text-zinc-400" title={h.reason ?? ""}>{h.reason ?? "--"}</td>
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
    </div>
  );
}

async function getWatchlistSafe(): Promise<string[]> {
  try {
    const res = await fetch(`${API_BASE}/api/watchlist`, { cache: "no-store" });
    if (!res.ok) return [];
    return ((await res.json()).data as { symbol: string }[]).map((i) => i.symbol);
  } catch {
    return [];
  }
}
