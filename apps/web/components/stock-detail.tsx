"use client";

import { useEffect, useState } from "react";
import { MinuteChart } from "@/components/minute-chart";
import { KlineChartPro } from "@/components/kline-chart-pro";
import { Panel } from "@/components/panel";
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { useQuoteStream } from "@/hooks/use-quote-stream";
import { analyze } from "@/lib/technical-analysis";
import { addToWatchlist, API_BASE, getKline, getMinuteLine, getOrderBook, getQuotes, getTrades, type MinutePoint } from "@/lib/api";
import { fmt, fmtAmount, fmtVolume, pctColor, pctText, timeText } from "@/lib/format";
import type { Kline, OrderBook, Quote, Trade } from "@/types/market";

type ChartTab = "kline" | "minute" | "flow";
type RightTab = "book" | "trades";
type BottomTab = "fin" | "longhu";

interface LonghuSeat { seat?: string | null; seat_type: string; buy?: number | null; sell?: number | null; net?: number | null; rise_probability_3day?: number | null }
interface LonghuDetail { trade_date: string; buy_seats: LonghuSeat[]; sell_seats: LonghuSeat[]; empty?: boolean }
interface LonghuHistory { trade_date: string; close?: number | null; change_pct?: number | null; net_buy?: number | null; reason?: string | null; after_1d?: number | null; after_3d?: number | null; after_5d?: number | null; after_10d?: number | null }
interface LonghuStats { count: number; avg_after_5d?: number | null; win_rate_5d?: number | null }
interface FlowRow { date: string; close?: number | null; change_pct?: number | null; net_main?: number | null; net_super?: number | null; net_big?: number | null; net_mid?: number | null; net_small?: number | null; source: string }
interface CapitalFlow { days: number; flow: FlowRow[]; streak_in: number; definition: string }
interface FinRow { report_date: string; revenue?: number | null; revenue_yoy?: number | null; net_profit?: number | null; profit_yoy?: number | null; gross_margin?: number | null; roe?: number | null; eps?: number | null; source: string }

/** 个股详情终端 v3（工作台右栏 / 个股页共用）：
 * 顶部紧凑行情条 → 中部 [左：图表区(K线/分时/资金图) | 右：盘口↔逐笔] → 底部资讯 tabs(财务/龙虎榜/资金明细)。
 * K线带龙虎榜日标记与金叉死叉技术信号；滚动只存在于表格/列表容器内部。 */
export function StockDetailPanel({ symbol }: { symbol: string }) {
  const [chartTab, setChartTab] = useState<ChartTab>("kline");
  const [rightTab, setRightTab] = useState<RightTab>("book");
  const [bottomTab, setBottomTab] = useState<BottomTab>("fin");
  const [bars, setBars] = useState<Kline[]>([]);
  const [book, setBook] = useState<OrderBook | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [minutes, setMinutes] = useState<MinutePoint[]>([]);
  const [longhu, setLonghu] = useState<{ detail: LonghuDetail; history: LonghuHistory[]; stats: LonghuStats } | null>(null);
  const [flow, setFlow] = useState<CapitalFlow | null>(null);
  const [fins, setFins] = useState<FinRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [inWatchlist, setInWatchlist] = useState(false);
  const [quote, setQuote] = useState<Quote | null>(null);

  const { quotes } = useQuoteStream([symbol]);
  useEffect(() => {
    if (quotes[symbol]) setQuote((prev) => ({ ...(prev ?? quotes[symbol]), ...quotes[symbol] }));

  // 估值补充：ths 快照无 PE/PB/市值，每 30s 从腾讯源低频补齐（价格仍以 WS 为准）
  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    const pull = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/quotes/${symbol}?source=tencent`, { cache: "no-store" });
        if (!res.ok) return;
        const body = await res.json();
        const q = body.data as Quote;
        setQuote((prev) =>
          prev
            ? { ...q, price: prev.price, change: prev.change, change_pct: prev.change_pct, data_timestamp: prev.data_timestamp, quality: prev.quality, quality_reasons: prev.quality_reasons }
            : q
        );
      } catch {}
    };
    void pull();
    const t = setInterval(pull, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [symbol]);
  }, [quotes, symbol]);

  useEffect(() => {
    let alive = true;
    getWatchlistSymbols().then((list) => alive && setInWatchlist(list.includes(symbol)));
    return () => {
      alive = false;
    };
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    setBars([]);
    setBook(null);
    setTrades([]);
    setMinutes([]);
    setLonghu(null);
    setFlow(null);
    setFins(null);
    Promise.all([
      getKline(symbol, "1d", 120),
      getOrderBook(symbol).catch(() => null),
      getTrades(symbol, 30).catch(() => [] as Trade[]),
      getMinuteLine(symbol).catch(() => [] as MinutePoint[]),
      fetch(`${API_BASE}/api/longhu/${symbol}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch(`${API_BASE}/api/capital-flow/${symbol}?days=30`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch(`${API_BASE}/api/financials/${symbol}?periods=8`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ])
      .then(([b, ob, tr, min, lh, cf, fins]) => {
        if (!alive) return;
        setBars(b);
        setBook(ob);
        setTrades(tr);
        setMinutes(min);
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

  const lhbDates = (longhu?.history ?? []).map((h) => ({ date: h.trade_date, note: h.reason ?? "" }));
  const tech = analyze(
    bars.map((b) => ({ ts: b.ts, open: b.open ?? 0, high: b.high ?? 0, low: b.low ?? 0, close: b.close ?? 0, volume: b.volume, change_pct: b.change_pct }))
  );

  const strip = quote
    ? ([
        ["今开", fmt(quote.open)],
        ["最高", fmt(quote.high)],
        ["最低", fmt(quote.low)],
        ["昨收", fmt(quote.prev_close)],
        ["成交量", fmtVolume(quote.volume) + "手"],
        ["成交额", fmtAmount(quote.amount)],
        ["换手", quote.turnover_rate != null ? `${fmt(quote.turnover_rate)}%` : "--"],
        ["PE", quote.pe_ttm != null ? fmt(quote.pe_ttm) : "--"],
        ["PB", quote.pb != null ? fmt(quote.pb) : "--"],
        ["市值", quote.total_mktcap_yi != null ? `${fmt(quote.total_mktcap_yi)}亿` : "--"],
        ["涨停", quote.limit_up_price != null ? fmt(quote.limit_up_price) : "--"],
      ] as const)
    : [];

  return (
    <div className="flex min-h-0 min-w-0 flex-col gap-2">
      {error && (
        <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-600 dark:text-amber-300">{error}</div>
      )}

      {/* ① 紧凑行情条 */}
      {quote && (
        <div className="shrink-0 rounded-xl border border-zinc-200 px-4 py-1.5 dark:border-zinc-800">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4">
            <div className="flex items-baseline gap-2">
              <span className="text-base font-semibold">{quote.name ?? "--"}</span>
              <span className="font-mono text-xs text-zinc-400">{quote.market}.{quote.symbol}</span>
              <QualityBadge quality={quote.quality} reasons={quote.quality_reasons} />
              {inWatchlist ? (
                <span className="text-xs text-zinc-400">已在自选</span>
              ) : (
                <button onClick={() => void add()} className="rounded border border-up/50 px-1.5 py-0.5 text-[11px] text-up hover:bg-up/10">＋ 自选</button>
              )}
            </div>
            <div className="flex items-baseline gap-2">
              <PriceFlash value={quote.price} className={`font-mono text-2xl font-semibold tabular-nums ${pctColor(quote.change_pct)}`}>{fmt(quote.price)}</PriceFlash>
              <span className={`font-mono text-xs tabular-nums ${pctColor(quote.change)}`}>
                {quote.change != null ? `${quote.change > 0 ? "+" : ""}${fmt(quote.change)}` : "--"}（{pctText(quote.change_pct)}）
              </span>
            </div>
          </div>
          <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[11px] text-zinc-400">
            {strip.map(([k, v]) => (
              <span key={k}>
                {k} <span className="font-mono tabular-nums text-zinc-200">{v}</span>
              </span>
            ))}
            <span className="ml-auto text-zinc-500">
              {timeText(quote.data_timestamp)} · {quote.source}
            </span>
          </div>
        </div>
      )}

      {/* ② 中部：左图表区 + 右盘口/逐笔 */}
      <div className="grid min-h-0 min-w-0 flex-1 grid-cols-1 gap-2 lg:grid-cols-[minmax(0,1fr),248px]">
        <div className="flex min-h-0 min-w-0 flex-col gap-1.5">
          <div className="flex shrink-0 gap-1">
            {(
              [
                ["kline", "K线"],
                ["minute", "分时"],
                ["flow", "资金图"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setChartTab(key)}
                className={`rounded-md px-2.5 py-1 text-xs ${chartTab === key ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"}`}
              >
                {label}
              </button>
            ))}
          </div>

          {chartTab === "kline" && (
            <Panel title="日 K 线（前复权 · 默认聚焦最近 20 日，可缩放看全部）" source={bars[0]?.source} bodyClassName="overflow-hidden" className="min-h-0 flex-1">
              {bars.length > 0 ? (
                <div className="flex h-full min-h-0 flex-col">
                  {tech && (
                    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-zinc-100 px-3 py-1 text-[11px] dark:border-zinc-800/60">
                      <span
                        className={`rounded px-1.5 py-0.5 font-medium ${
                          tech.bias === "bull"
                            ? "bg-up/15 text-up"
                            : tech.bias === "bear"
                              ? "bg-down/15 text-down"
                              : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-300"
                        }`}
                      >
                        技术评估：{tech.bias === "bull" ? "偏多" : tech.bias === "bear" ? "偏空" : "中性"}（{tech.bullCount}多/{tech.bearCount}空）
                      </span>
                      <span
                        className="text-zinc-400"
                        title={tech.signals.map((sg) => sg.name + "：" + sg.detail).join("\n")}
                      >
                        {tech.signals.filter((sg) => sg.bias !== "neutral").slice(0, 4).map((sg) => sg.name).join(" · ")}
                        <span className="ml-1 underline decoration-dotted">依据ⓘ</span>
                      </span>
                      <span className="ml-auto text-zinc-500">多因子技术信号汇总，不构成买卖建议</span>
                    </div>
                  )}
                  <div className="min-h-0 flex-1">
                    <KlineChartPro bars={bars} lhbDates={lhbDates} className="h-full" />
                  </div>
                </div>
              ) : (
                <p className="px-4 py-10 text-center text-sm text-zinc-400">等待 K 线数据…</p>
              )}
            </Panel>
          )}

          {chartTab === "minute" && (
            <Panel title="当日分时（1 分钟）" source={minutes[0]?.source} bodyClassName="overflow-hidden" className="min-h-0 flex-1">
              {minutes.length > 0 ? <MinuteChart points={minutes} className="h-full" /> : <p className="px-4 py-10 text-center text-sm text-zinc-400">暂无分时数据</p>}
            </Panel>
          )}

          {chartTab === "flow" && (
            <Panel title="主力资金净流入（近 30 日柱状 · 明细见底部）" source={flow?.flow[0]?.source} bodyClassName="overflow-hidden" className="min-h-0 flex-1">
              {!flow || flow.flow.length === 0 ? (
                <p className="px-4 py-10 text-center text-sm text-zinc-400">暂无资金流数据</p>
              ) : (
                <div className="grid h-full grid-cols-1 overflow-hidden md:grid-cols-[minmax(0,1fr),minmax(0,1fr)]">
                <div className="flex min-w-0 flex-col border-r border-zinc-200 px-3 py-2 dark:border-zinc-800">
                  <div className="flex shrink-0 flex-wrap items-center gap-x-4 text-xs text-zinc-400">
                    <span>
                      连续净流入 <span className="font-mono text-sm text-zinc-100">{flow.streak_in}</span> 天
                    </span>
                    <span title={flow.definition}>口径说明 ⓘ</span>
                  </div>
                  <div className="mt-2 flex min-w-0 flex-1 items-stretch gap-[2px]">
                    {flow.flow.map((r) => {
                      const max = Math.max(...flow.flow.map((x) => Math.abs(x.net_main ?? 0)), 1);
                      const v = r.net_main ?? 0;
                      const h = Math.max(3, (Math.abs(v) / max) * 100);
                      return (
                        <div key={r.date} className="group relative flex min-w-0 flex-1 flex-col justify-center" title={`${r.date} ${fmtAmount(v)}`}>
                          <div className="flex h-1/2 items-end">{v > 0 && <div className="w-full rounded-t bg-[rgba(244,63,94,0.75)]" style={{ height: `${h}%` }} />}</div>
                          <div className="flex h-1/2 items-start">{v < 0 && <div className="w-full rounded-b bg-[rgba(16,185,129,0.75)]" style={{ height: `${h}%` }} />}</div>
                        </div>
                      );
                    })}
                  </div>
                  <div className="shrink-0 border-t border-zinc-100 pt-1 text-[10px] text-zinc-500 dark:border-zinc-800/60">{flow.definition}</div>
                </div>
                {/* 右：明细表 */}
                <div className="min-h-0 overflow-auto">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-zinc-50 text-left text-xs text-zinc-400 dark:bg-zinc-900/50">
                      <tr>{["日期", "主力净流入", "超大单", "大单", "中单", "小单"].map((h) => (
                        <th key={h} className={`px-3 py-2 font-medium ${h === "日期" ? "" : "text-right"}`}>{h}</th>
                      ))}</tr>
                    </thead>
                    <tbody>
                      {[...flow.flow].reverse().map((r) => (
                        <tr key={r.date} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                          <td className="px-3 py-1.5 font-mono text-xs">{r.date}</td>
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
        </div>

        {/* 右列：盘口 ↔ 逐笔（盘口默认） */}
        <Panel
          title={
            <span className="flex gap-2">
              {(
                [
                  ["book", "五档盘口"],
                  ["trades", "逐笔"],
                ] as const
              ).map(([k, label]) => (
                <button
                  key={k}
                  onClick={() => setRightTab(k)}
                  className={`rounded px-1.5 py-0.5 text-xs ${rightTab === k ? "bg-zinc-100 font-semibold text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100" : "text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"}`}
                >
                  {label}
                </button>
              ))}
            </span>
          }
          source={rightTab === "book" ? book?.source : trades[0]?.source}
          dataTimestamp={rightTab === "book" ? book?.data_timestamp : undefined}
          bodyClassName="overflow-y-auto"
          className="min-h-0 overflow-hidden"
        >
          {rightTab === "book" ? (
            book ? (
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
            ) : (
              <p className="px-3 py-10 text-center text-xs text-zinc-400">盘口数据不可用（免费源仅盘中提供）</p>
            )
          ) : trades.length > 0 ? (
            <table className="w-full text-sm">
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                    <td className="px-2 py-1 font-mono text-[11px] tabular-nums text-zinc-400">{timeText(t.ts)}</td>
                    <td className={`px-1 py-1 text-right font-mono tabular-nums ${t.side === "buy" ? "text-up" : t.side === "sell" ? "text-down" : "text-zinc-300"}`}>{fmt(t.price)}</td>
                    <td className="px-2 py-1 text-right font-mono text-[11px] tabular-nums text-zinc-400">{fmtVolume(t.volume)}</td>
                    <td className="pr-2 text-right text-[11px] text-zinc-400">{t.side === "buy" ? "B" : t.side === "sell" ? "S" : "·"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="px-3 py-8 text-center text-xs text-zinc-400">暂无逐笔（盘中看分时）</p>
          )}
        </Panel>
      </div>

      {/* ③ 底部资讯 tabs：财务 | 龙虎榜 | 资金明细 */}
      <Panel
        title={
          <span className="flex gap-2">
            {(
              [
                ["fin", "财务"],
                ["longhu", "龙虎榜"],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setBottomTab(k)}
                className={`rounded px-1.5 py-0.5 text-xs ${bottomTab === k ? "bg-zinc-100 font-semibold text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100" : "text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"}`}
              >
                {label}
              </button>
            ))}
          </span>
        }
        source={bottomTab === "fin" ? fins?.[0]?.source : bottomTab === "longhu" ? "eastmoney" : flow?.flow[0]?.source}
        bodyClassName="overflow-hidden"
        className="h-[150px] shrink-0"
      >
        <div className="h-full overflow-y-auto">
          {bottomTab === "fin" &&
            (!fins || fins.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-zinc-400">暂无财务数据</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-zinc-50 text-left text-xs text-zinc-400 dark:bg-zinc-900/50">
                  <tr>{["报告期", "营收(亿)", "营收同比", "净利(亿)", "净利同比", "毛利率", "ROE", "EPS"].map((h) => (
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
            ))}

          {bottomTab === "longhu" &&
            (!longhu ? (
              <p className="px-4 py-8 text-center text-sm text-zinc-400">加载中…</p>
            ) : (
              <>
                {longhu.detail.empty ? (
                  <p className="px-4 py-4 text-center text-xs text-zinc-400">最近交易日未上榜（历史记录见下方）</p>
                ) : (
                  <div className="grid gap-3 p-2 md:grid-cols-2">
                    {(["buy", "sell"] as const).map((side) => (
                      <div key={side}>
                        <h3 className={`px-2 py-1 text-xs font-medium ${side === "buy" ? "text-up" : "text-down"}`}>{side === "buy" ? "买入席位" : "卖出席位"}</h3>
                        <table className="w-full text-sm">
                          <tbody>
                            {longhu.detail[side === "buy" ? "buy_seats" : "sell_seats"].map((st, i) => (
                              <tr key={i} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                                <td className="px-2 py-1 text-xs text-zinc-400">{i + 1}</td>
                                <td className="px-2 py-1">
                                  <div className="truncate" title={st.seat ?? ""}>{st.seat}</div>
                                  <div className="text-[11px] text-zinc-400">{st.seat_type}{st.rise_probability_3day != null ? ` · 3日胜率 ${st.rise_probability_3day.toFixed(1)}%` : ""}</div>
                                </td>
                                <td className={`px-2 py-1 text-right font-mono text-xs tabular-nums ${side === "buy" ? "text-up" : "text-down"}`}>{fmtAmount(side === "buy" ? st.buy : st.sell)}</td>
                                <td className={`px-3 py-1 text-right font-mono text-xs tabular-nums ${(st.net ?? 0) > 0 ? "text-up" : "text-down"}`}>{fmtAmount(st.net)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ))}
                  </div>
                )}
                <div className="border-t border-zinc-200 px-4 py-2 text-xs text-zinc-400 dark:border-zinc-800">
                  历史上榜 <span className="font-mono text-zinc-200">{longhu.stats.count}</span> 次 · 5日平均{" "}
                  <span className={`font-mono ${pctColor(longhu.stats.avg_after_5d)}`}>{pctText(longhu.stats.avg_after_5d)}</span> · 胜率{" "}
                  <span className="font-mono text-zinc-200">{longhu.stats.win_rate_5d != null ? `${(longhu.stats.win_rate_5d * 100).toFixed(1)}%` : "--"}</span>
                </div>
                <table className="w-full text-sm">
                  <thead className="bg-zinc-50 text-left text-xs text-zinc-400 dark:bg-zinc-900/50">
                    <tr>{["日期", "收盘", "涨跌幅", "净买额", "上榜原因", "T+1", "T+3", "T+5", "T+10"].map((h) => (
                      <th key={h} className={`px-3 py-2 font-medium ${["日期", "上榜原因"].includes(h) ? "" : "text-right"}`}>{h}</th>
                    ))}</tr>
                  </thead>
                  <tbody>
                    {longhu.history.slice(0, 15).map((h, i) => (
                      <tr key={`${h.trade_date}-${i}`} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                        <td className="px-3 py-1 font-mono text-xs">{h.trade_date}</td>
                        <td className="px-2 py-1 text-right font-mono">{fmt(h.close)}</td>
                        <td className={`px-2 py-1 text-right font-mono ${pctColor(h.change_pct)}`}>{pctText(h.change_pct)}</td>
                        <td className={`px-2 py-1 text-right font-mono text-xs ${(h.net_buy ?? 0) > 0 ? "text-up" : "text-down"}`}>{fmtAmount(h.net_buy)}</td>
                        <td className="max-w-[200px] truncate px-2 py-1 text-xs text-zinc-400" title={h.reason ?? ""}>{h.reason ?? "--"}</td>
                        {[h.after_1d, h.after_3d, h.after_5d, h.after_10d].map((v, j) => (
                          <td key={j} className={`px-3 py-1 text-right font-mono text-xs ${pctColor(v)}`}>{pctText(v)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            ))}
        </div>
        </Panel>
    </div>
  );
}

async function getWatchlistSymbols(): Promise<string[]> {
  try {
    const res = await fetch(`${API_BASE}/api/watchlist`, { cache: "no-store" });
    if (!res.ok) return [];
    return ((await res.json()).data as { symbol: string }[]).map((i) => i.symbol);
  } catch {
    return [];
  }
}
