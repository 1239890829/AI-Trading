"use client";

import { useEffect, useState } from "react";
import { KlineChart } from "@/components/kline-chart";
import { MinuteChart } from "@/components/minute-chart";
import { Panel } from "@/components/panel";
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { addToWatchlist, API_BASE, getKline, getMinuteLine, getOrderBook, getQuotes, getTrades, type MinutePoint } from "@/lib/api";
import { fmt, fmtAmount, fmtVolume, pctColor, pctText, timeText } from "@/lib/format";
import type { Kline, OrderBook, Quote, Trade } from "@/types/market";

type Tab = "kline" | "minute" | "book" | "trades" | "longhu" | "flow" | "fin";

interface FinRow { report_date: string; revenue?: number | null; revenue_yoy?: number | null; net_profit?: number | null; profit_yoy?: number | null; gross_margin?: number | null; roe?: number | null; eps?: number | null; debt_ratio?: number | null; source: string }

interface FlowRow { date: string; close?: number | null; change_pct?: number | null; net_main?: number | null; net_super?: number | null; net_big?: number | null; net_mid?: number | null; net_small?: number | null; source: string }
interface CapitalFlow { days: number; flow: FlowRow[]; streak_in: number; definition: string }

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
  const [flow, setFlow] = useState<CapitalFlow | null>(null);
  const [fins, setFins] = useState<FinRow[] | null>(null);
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
      fetch(`${API_BASE}/api/capital-flow/${symbol}?days=30`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch(`${API_BASE}/api/financials/${symbol}?periods=8`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ])
      .then(([bars2, ob2, tr2, minutes2, lh, cf, fins]) => {
        if (!alive) return;
        setBars(bars2);
        setBook(ob2);
        setTrades(tr2);
        setMinutes(minutes2);
        if (lh?.data) setLonghu(lh.data);
        if (cf?.data) setFlow(cf.data);
        if (fins?.data) setFins(fins.data.periods);
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
        ["PE(TTM)", d.pe_ttm != null ? fmt(d.pe_ttm) : "--"],
        ["PB", d.pb != null ? fmt(d.pb) : "--"],
        ["总市值", d.total_mktcap_yi != null ? `${fmt(d.total_mktcap_yi)} 亿` : "--"],
        ["涨停价", d.limit_up_price != null ? fmt(d.limit_up_price) : "--"],
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
              <div key={k} className={`px-3 py-2 ${i < 8 ? "border-b border-zinc-100 dark:border-zinc-800/60" : ""}`}>
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
            ["flow", "资金"],
            ["fin", "财务"],
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

      {tab === "flow" && (
        <Panel title="资金流向（近 30 日 · 主力口径）" source={flow?.flow[0]?.source} className="min-h-0 flex-1 overflow-hidden">
          {!flow || flow.flow.length === 0 ? (
            <p className="px-4 py-10 text-center text-sm text-zinc-400">暂无资金流数据</p>
          ) : (
            <div className="grid h-[430px] grid-cols-1 overflow-hidden md:grid-cols-[minmax(0,2fr),minmax(0,3fr)]">
              {/* 左：柱状图 */}
              <div className="flex min-w-0 flex-col border-b border-zinc-200 md:border-b-0 md:border-r dark:border-zinc-800">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 text-xs text-zinc-400">
                  <span>
                    连续净流入 <span className="font-mono text-sm text-zinc-100">{flow.streak_in}</span> 天
                  </span>
                  <span>
                    最新主力净流入{" "}
                    <span className={`font-mono text-sm ${(flow.flow[0].net_main ?? 0) > 0 ? "text-up" : "text-down"}`}>
                      {fmtAmount(flow.flow[0].net_main)}
                    </span>
                  </span>
                </div>
                <div className="flex min-w-0 flex-1 items-end gap-[2px] px-3 py-2">
                  {[...flow.flow].reverse().map((r) => {
                    const max = Math.max(...flow.flow.map((x) => Math.abs(x.net_main ?? 0)), 1);
                    const v = r.net_main ?? 0;
                    const h = Math.max(2, (Math.abs(v) / max) * 48);
                    return (
                      <div key={r.date} className="group relative flex h-full min-w-0 flex-1 flex-col justify-center">
                        <div className="flex h-1/2 items-end">
                          {v > 0 && <div className="w-full rounded-t bg-[rgba(244,63,94,0.75)]" style={{ height: `${h}%` }} />}
                        </div>
                        <div className="flex h-1/2 items-start">
                          {v < 0 && <div className="w-full rounded-b bg-[rgba(16,185,129,0.75)]" style={{ height: `${h}%` }} />}
                        </div>
                        <div className="pointer-events-none absolute -top-1 left-1/2 z-10 hidden -translate-x-1/2 whitespace-nowrap rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-100 group-hover:block">
                          {r.date} {fmtAmount(v)}
                        </div>
                      </div>
                    );
                  })}
                </div>
                <p className="border-t border-zinc-200 px-4 py-2 text-xs text-zinc-500 dark:border-zinc-800">{flow.definition}</p>
              </div>
              {/* 右：明细表 */}
              <div className="min-h-0 overflow-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-zinc-50 text-left text-xs text-zinc-400 dark:bg-zinc-900/50">
                    <tr>{["日期", "收盘", "涨跌幅", "主力净流入", "超大单", "大单", "中单", "小单"].map((h) => (
                      <th key={h} className={`px-3 py-2 font-medium ${h === "日期" ? "" : "text-right"}`}>{h}</th>
                    ))}</tr>
                  </thead>
                  <tbody>
                    {flow.flow.map((r) => (
                      <tr key={r.date} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                        <td className="px-3 py-1.5 font-mono text-xs">{r.date}</td>
                        <td className="px-2 py-1.5 text-right font-mono">{fmt(r.close)}</td>
                        <td className={`px-2 py-1.5 text-right font-mono ${pctColor(r.change_pct)}`}>{pctText(r.change_pct)}</td>
                        <td className={`px-2 py-1.5 text-right font-mono text-xs ${(r.net_main ?? 0) > 0 ? "text-up" : "text-down"}`}>{fmtAmount(r.net_main)}</td>
                        {[r.net_super, r.net_big, r.net_mid, r.net_small].map((v, j) => (
                          <td key={j} className={`px-3 py-1.5 text-right font-mono text-xs ${(v ?? 0) > 0 ? "text-up" : "text-down"}`}>{fmtAmount(v)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </Panel>
      )}
      {tab === "fin" && (
        <Panel title="财务摘要（按报告期倒序 · 东财业绩报表）" source={fins?.[0]?.source} className="min-h-0 flex-1 overflow-hidden">
          {!fins || fins.length === 0 ? (
            <p className="px-4 py-10 text-center text-sm text-zinc-400">暂无财务数据</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 text-left text-xs text-zinc-400 dark:bg-zinc-900/50">
                <tr>{["报告期","营收(亿)","营收同比","归母净利(亿)","净利同比","毛利率","ROE","EPS"].map((h) => (
                  <th key={h} className={`px-3 py-2 font-medium ${h === "报告期" ? "" : "text-right"}`}>{h}</th>
                ))}</tr>
              </thead>
              <tbody>
                {fins.map((r) => (
                  <tr key={r.report_date} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                    <td className="px-3 py-1.5 font-mono text-xs">{r.report_date}</td>
                    <td className="px-2 py-1.5 text-right font-mono">{r.revenue != null ? fmt(r.revenue / 1e8) : "--"}</td>
                    <td className={`px-2 py-1.5 text-right font-mono text-xs ${pctColor(r.revenue_yoy)}`}>{pctText(r.revenue_yoy)}</td>
                    <td className="px-2 py-1.5 text-right font-mono">{r.net_profit != null ? fmt(r.net_profit / 1e8) : "--"}</td>
                    <td className={`px-2 py-1.5 text-right font-mono text-xs ${pctColor(r.profit_yoy)}`}>{pctText(r.profit_yoy)}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">{r.gross_margin != null ? `${fmt(r.gross_margin)}%` : "--"}</td>
                    <td className="px-2 py-1.5 text-right font-mono text-xs">{r.roe != null ? `${fmt(r.roe)}%` : "--"}</td>
                    <td className="px-2 py-1.5 text-right font-mono text-xs">{r.eps != null ? fmt(r.eps) : "--"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
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
