"use client";

import { useEffect, useState } from "react";
import { MinuteChart } from "@/components/minute-chart";
import { KlineChartPro } from "@/components/kline-chart-pro";
import { TradeForm } from "@/components/trade-form";
import { Panel } from "@/components/panel";
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { useQuoteStream } from "@/hooks/use-quote-stream";
import { analyze } from "@/lib/technical-analysis";
import {
  addToWatchlist,
  API_BASE,
  cancelPaperOrder,
  getKline,
  getMinuteLine,
  getOrderBook,
  getPaperAccount,
  getPaperFills,
  getPaperOrders,
  getPaperPositions,
  getQuotes,
  getTrades,
  getWatchlist,
  placePaperOrder,
  resetPaperAccount,
  type MinutePoint,
  type PaperAccountInfo,
  type PaperFill,
  type PaperOrderInfo,
  type PaperPositionInfo,
} from "@/lib/api";
import { fmt, fmtAmount, fmtVolume, pctColor, pctText, sourceLabel, timeText } from "@/lib/format";
import type { Kline, OrderBook, Quote, Trade } from "@/types/market";

type ChartTab = "kline" | "minute" | "flow";
type RightTab = "book" | "trades" | "trade" | "profile" | "info";

interface LonghuSeat { seat?: string | null; seat_type: string; buy?: number | null; sell?: number | null; net?: number | null; rise_probability_3day?: number | null }
interface LonghuDetail { trade_date: string; buy_seats: LonghuSeat[]; sell_seats: LonghuSeat[]; empty?: boolean }
interface LonghuHistory { trade_date: string; close?: number | null; change_pct?: number | null; net_buy?: number | null; reason?: string | null; after_1d?: number | null; after_3d?: number | null; after_5d?: number | null; after_10d?: number | null }
interface LonghuStats { count: number; avg_after_5d?: number | null; win_rate_5d?: number | null }
interface FlowRow { date: string; close?: number | null; change_pct?: number | null; net_main?: number | null; net_super?: number | null; net_big?: number | null; net_mid?: number | null; net_small?: number | null; source: string }
interface CapitalFlow { days: number; flow: FlowRow[]; streak_in: number; definition: string }
interface CompanyProfile {
  name?: string | null;
  industry?: string | null;
  profile?: string | null;
  main_business?: string | null;
  csrc_industry?: string | null;
  region?: string | null;
  boards?: string[];
  board_groups?: { industry: string[]; region: string[]; concept: string[]; style_index: string[] };
  core_themes?: string[];
  source: string;
}

interface InfoItem { title: string; date: string; url: string; type?: string | null; summary?: string | null; source: string }

interface FinRow { report_date: string; revenue?: number | null; revenue_yoy?: number | null; net_profit?: number | null; profit_yoy?: number | null; gross_margin?: number | null; roe?: number | null; eps?: number | null; source: string }

/** 个股详情终端 v3（工作台右栏 / 个股页共用）：
 * 顶部紧凑行情条 → 中部 [左：图表区(K线/分时/资金图) | 右：盘口↔逐笔] → 右列：盘口↔逐笔 + 财务摘要。龙虎榜见独立页面。
 * K线带龙虎榜日标记与金叉死叉技术信号；滚动只存在于表格/列表容器内部。 */
export function StockDetailPanel({ symbol }: { symbol: string }) {
  const [chartTab, setChartTab] = useState<ChartTab>("kline");
  const [rightTab, setRightTab] = useState<RightTab>("book");
  const [bars, setBars] = useState<Kline[]>([]);
  const [book, setBook] = useState<OrderBook | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [minutes, setMinutes] = useState<MinutePoint[]>([]);
  const [longhu, setLonghu] = useState<{ detail: LonghuDetail; history: LonghuHistory[]; stats: LonghuStats } | null>(null);
  const [flow, setFlow] = useState<CapitalFlow | null>(null);
  const [fins, setFins] = useState<FinRow[] | null>(null);
  const [anns, setAnns] = useState<InfoItem[] | null>(null);
  const [news, setNews] = useState<InfoItem[] | null>(null);
  const [company, setCompany] = useState<CompanyProfile | null>(null);
  const [fills, setFills] = useState<PaperFill[]>([]);
  const [resetBusy, setResetBusy] = useState(false);
  const [paper, setPaper] = useState<{ acc: PaperAccountInfo; positions: PaperPositionInfo[]; orders: PaperOrderInfo[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [inWatchlist, setInWatchlist] = useState(false);
  const [quote, setQuote] = useState<Quote | null>(null);

  const { quotes } = useQuoteStream([symbol]);
  useEffect(() => {
    const live = quotes[symbol];
    if (!live) return;
    setQuote((prev) => {
      // 跨股票切换时直接采用新行情：base 必须与当前 symbol 一致，
      // 否则旧股残留字段会混进新股的合并结果
      const base = prev && prev.symbol === live.symbol ? prev : null;
      // ths 快照不含估值字段：合并时保留已补源的估值，避免被 undefined 覆盖
      return {
        ...(base ?? live),
        ...live,
        pe_ttm: live.pe_ttm ?? base?.pe_ttm ?? null,
        pb: live.pb ?? base?.pb ?? null,
        total_mktcap_yi: live.total_mktcap_yi ?? base?.total_mktcap_yi ?? null,
        float_mktcap_yi: live.float_mktcap_yi ?? base?.float_mktcap_yi ?? null,
        limit_up_price: live.limit_up_price ?? base?.limit_up_price ?? null,
        limit_down_price: live.limit_down_price ?? base?.limit_down_price ?? null,
      };
    });
  }, [quotes, symbol]);


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
        setQuote((prev) => {
          // 同股才合并：保留 WS 的最新价（腾讯 REST 可能滞后）。
          // 旧实现无条件沿用 prev.price——切股后 prev 还是上一只股票的，
          // 会把旧价格持续盖在新股票的报价上（串价主因）。
          if (!prev || prev.symbol !== q.symbol) return q;
          return {
            ...q,
            price: prev.price,
            change: prev.change,
            change_pct: prev.change_pct,
            data_timestamp: prev.data_timestamp,
            quality: prev.quality,
            quality_reasons: prev.quality_reasons,
          };
        });
      } catch {}
    };
    void pull();
    const t = setInterval(pull, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    setFills([]); // 成交记录按 symbol 过滤，切股先清空防残留
    const loadPaper = async () => {
      try {
        const [acc, positions, orders, fs] = await Promise.all([
          getPaperAccount(),
          getPaperPositions(),
          getPaperOrders(),
          getPaperFills(symbol),
        ]);
        if (alive) {
          setPaper({ acc, positions, orders });
          setFills(fs);
        }
      } catch {}
    };
    void loadPaper();
    const t = setInterval(loadPaper, 10000);
    window.addEventListener("paper-changed", loadPaper);
    return () => {
      alive = false;
      clearInterval(t);
      window.removeEventListener("paper-changed", loadPaper);
    };
  }, [symbol]);

  useEffect(() => {
    let alive = true;
    setInWatchlist(false); // 切股先归零，防止上一只的加自选状态残留
    getWatchlistSymbols().then((list) => alive && setInWatchlist(list.includes(symbol)));
    return () => {
      alive = false;
    };
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    // 切股即清场：行情与资料类状态一并归零。旧实现只清了图表类，
    // quote/company/anns/news 会残留上一只股票的数据——
    // 在新 WS 快照/新请求返回前，界面渲染的是旧股票的价格与资料。
    setQuote(null);
    setCompany(null);
    setAnns(null);
    setNews(null);
    setError(null);
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
      fetch(`${API_BASE}/api/company/${symbol}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch(`${API_BASE}/api/announcements/${symbol}?limit=8`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch(`${API_BASE}/api/news/${symbol}?limit=8`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ])
      .then(([b, ob, tr, min, lh, cf, fins, comp, anns, news]) => {
        if (!alive) return;
        setBars(b);
        setBook(ob);
        setTrades(tr);
        setMinutes(min);
        if (lh?.data) setLonghu(lh.data);
        if (cf?.data) setFlow(cf.data);
        if (fins?.data) setFins(fins.data.periods);
        if (comp?.data) setCompany(comp.data);
        if (anns?.data) setAnns(anns.data.items);
        if (news?.data) setNews(news.data.items);
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

  // 当前个股的模拟持仓（用于 K 线成本线）
  const myPosition = paper?.positions.find((p) => p.symbol === symbol) ?? null;
  const costPrice = myPosition && myPosition.quantity > 0 ? myPosition.cost_price : null;

  // 板块标签分组：把风格/指数成分与概念题材分开，避免"大盘股/MSCI中国"混进题材
  const boardGroups = company?.board_groups;
  const boardRows: [string, string[], string][] = boardGroups
    ? [
        ["行业", boardGroups.industry ?? [], "text-zinc-200"],
        ["地域", boardGroups.region ?? [], "text-zinc-300"],
        ["概念题材", boardGroups.concept ?? [], "text-amber-600 dark:text-amber-300"],
        ["风格 / 指数成分", boardGroups.style_index ?? [], "text-zinc-500"],
      ]
    : company?.boards?.length
      ? [["板块", company.boards, "text-zinc-300"]] // 无分组数据（旧缓存）时回退扁平全量
      : [];

  async function handleResetAccount() {
    if (!window.confirm("重置模拟账户？当前全部持仓、挂单与成交记录将清空，资金回到初始额度。此操作不可撤销。")) return;
    setResetBusy(true);
    try {
      await resetPaperAccount();
      window.dispatchEvent(new CustomEvent("paper-changed"));
    } catch (e) {
      window.alert(`重置失败：${(e as Error).message}`);
    } finally {
      setResetBusy(false);
    }
  }

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
              {timeText(quote.data_timestamp)} · {sourceLabel(quote.source)}
            </span>
          </div>
        </div>
      )}

      {/* ② 中部：左图表区 + 右盘口/逐笔 */}
      <div className="grid min-h-0 min-w-0 flex-1 grid-cols-1 gap-2 lg:grid-cols-[minmax(0,1fr),300px]">
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
                    <KlineChartPro bars={bars} tradeMarks={fills} costPrice={costPrice} className="h-full" />
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
            <Panel title="主力资金净流入（近 30 日 · 左图右明细）" source={flow?.flow[0]?.source} bodyClassName="overflow-hidden" className="min-h-0 flex-1">
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

        {/* 右列：盘口↔逐笔 + 资讯 tabs */}
        <div className="flex min-h-0 flex-col gap-2">

        <Panel
          title={rightTab === "book" ? "五档盘口" : rightTab === "trades" ? "逐笔成交" : rightTab === "trade" ? "模拟交易" : rightTab === "profile" ? "公司资料" : "资讯"}
          bodyClassName="overflow-y-auto"
          source={rightTab === "book" ? book?.source : undefined}
          dataTimestamp={rightTab === "book" ? book?.data_timestamp : null}
          className="min-h-0 flex-1 overflow-hidden"
        >
          <div className="flex shrink-0 gap-1 border-b border-zinc-100 px-2 py-1 dark:border-zinc-800/60">
            {(
              [
                ["book", "盘口"],
                ["trades", "逐笔"],
                ["trade", "交易"],
                ["profile", "资料"],
                ["info", "资讯"],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setRightTab(k)}
                className={`rounded px-2 py-0.5 text-xs ${rightTab === k ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"}`}
              >
                {label}
              </button>
            ))}
          </div>
          {rightTab === "trade" && paper && (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="grid shrink-0 grid-cols-2 gap-1 border-b border-zinc-100 px-3 py-2 text-xs dark:border-zinc-800/60">
                <span className="text-zinc-400">
                  总资产 <span className="font-mono text-sm text-zinc-100">{fmt(paper.acc.total)}</span>
                </span>
                <span className="text-zinc-400">
                  现金 <span className="font-mono text-sm text-zinc-100">{fmt(paper.acc.cash)}</span>
                </span>
                <span className="text-zinc-400">
                  持仓市值 <span className="font-mono text-sm text-zinc-100">{fmt(paper.acc.market_value)}</span>
                </span>
                <span className={paper.acc.total_pnl >= 0 ? "text-up" : "text-down"}>
                  总盈亏 <span className="font-mono text-sm">{fmt(paper.acc.total_pnl)}（{fmt(paper.acc.total_pnl_pct)}%）</span>
                </span>
              </div>
              <TradeForm symbol={symbol} price={quote?.price ?? null} limitUp={quote?.limit_up_price ?? null} limitDown={quote?.limit_down_price ?? null} />
              <h3 className="shrink-0 px-3 pb-1 pt-2 text-xs font-medium text-zinc-300">持仓</h3>
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
                    <h3 className="px-3 pb-1 pt-2 text-xs font-medium text-zinc-300">挂单</h3>
                    {paper.orders.filter((o) => o.status === "pending").map((o) => (
                      <div key={o.id} className="flex items-center justify-between border-b border-zinc-100 px-3 py-1 text-xs dark:border-zinc-800/60">
                        <span className={o.side === "buy" ? "text-up" : "text-down"}>{o.side === "buy" ? "买" : "卖"} {o.symbol}</span>
                        <span className="font-mono text-zinc-400">{fmt(o.price)} × {o.quantity}</span>
                        <button
                          onClick={async () => { await cancelPaperOrder(o.id).catch(() => {}); window.dispatchEvent(new CustomEvent("paper-changed")); }}
                          className="rounded border border-zinc-300 px-1.5 text-zinc-400 hover:text-red-400 dark:border-zinc-600"
                        >
                          撤
                        </button>
                      </div>
                    ))}
                  </>
                )}

                <h3 className="shrink-0 px-3 pb-1 pt-2 text-xs font-medium text-zinc-300">成交记录</h3>
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
                    onClick={() => void handleResetAccount()}
                    disabled={resetBusy}
                    className="w-full rounded border border-zinc-300 py-1 text-xs text-zinc-400 hover:border-red-400 hover:text-red-400 disabled:opacity-40 dark:border-zinc-600"
                  >
                    {resetBusy ? "重置中…" : "重置模拟账户"}
                  </button>
                  <p className="mt-1 text-[10px] leading-relaxed text-zinc-500">清空全部持仓、挂单与成交记录，资金回到初始额度</p>
                </div>
              </div>
            </div>
          )}

          {rightTab === "profile" && (
            <div className="px-3 py-2 text-xs">
              {boardRows
                .filter(([, items]) => items.length > 0)
                .map(([label, items, tone]) => (
                  <div key={label} className="mb-2.5">
                    <div className="mb-1 text-zinc-400">{label}</div>
                    <div className="flex flex-wrap gap-1">
                      {items.map((b) => (
                        <span key={b} className={`rounded bg-zinc-100 px-1.5 py-0.5 dark:bg-zinc-800 ${tone}`}>
                          {b}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              {company?.main_business && (
                <div className="mb-2">
                  <div className="mb-1 text-zinc-400">主营业务</div>
                  <div className="leading-relaxed text-zinc-200">{company.main_business}</div>
                </div>
              )}
              {company?.profile && (
                <div className="mb-3">
                  <div className="mb-1 text-zinc-400">公司简介</div>
                  <div className="line-clamp-5 leading-relaxed text-zinc-300" title={company.profile}>
                    {company.profile}
                  </div>
                </div>
              )}
              <div className="mb-1 text-zinc-400">最近财报</div>
              {(fins ?? []).slice(0, 2).map((r) => (
                <div key={r.report_date} className="mb-1.5 rounded-lg border border-zinc-100 px-2 py-1.5 dark:border-zinc-800/60">
                  <div className="flex justify-between">
                    <span className="font-mono text-zinc-300">{r.report_date}</span>
                    <span className={`font-mono ${pctColor(r.profit_yoy)}`}>净利同比 {pctText(r.profit_yoy)}</span>
                  </div>
                  <div className="mt-0.5 flex justify-between text-zinc-400">
                    <span>
                      营收 <span className="font-mono text-zinc-200">{r.revenue != null ? fmt(r.revenue / 1e8) : "--"}</span> 亿
                    </span>
                    <span>
                      归母净利 <span className="font-mono text-zinc-200">{r.net_profit != null ? fmt(r.net_profit / 1e8) : "--"}</span> 亿
                    </span>
                  </div>
                </div>
              ))}
              {(fins ?? []).length === 0 && <p className="text-zinc-500">暂无财报数据</p>}
            </div>
          )}

          {rightTab === "info" && (
            <div className="min-h-0 overflow-y-auto">
              <h3 className="px-3 py-1.5 text-xs font-medium text-zinc-300">近期公告</h3>
              {(anns ?? []).map((a, i) => (
                <a
                  key={i}
                  href={a.url}
                  target="_blank"
                  rel="noreferrer"
                  className="block border-b border-zinc-100 px-3 py-1.5 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900"
                >
                  <div className="truncate text-xs text-zinc-200">{a.title}</div>
                  <div className="text-[11px] text-zinc-500">
                    {a.date} {a.type ? `· ${a.type}` : ""}
                  </div>
                </a>
              ))}
              {anns && anns.length === 0 && <p className="px-3 py-3 text-xs text-zinc-500">暂无公告</p>}
              <h3 className="border-t border-zinc-100 px-3 py-1.5 text-xs font-medium text-zinc-300 dark:border-zinc-800/60">相关新闻</h3>
              {(news ?? []).map((n, i) => (
                <a
                  key={i}
                  href={n.url}
                  target="_blank"
                  rel="noreferrer"
                  className="block border-b border-zinc-100 px-3 py-1.5 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900"
                >
                  <div className="truncate text-xs text-zinc-200">{n.title}</div>
                  <div className="text-[11px] text-zinc-500">{n.date}</div>
                </a>
              ))}
              {news && news.length === 0 && <p className="px-3 py-3 text-xs text-zinc-500">暂无新闻</p>}
            </div>
          )}

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
      </div>
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
