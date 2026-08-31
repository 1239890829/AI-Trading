"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { MinuteChart } from "@/components/minute-chart";
import { KlineChartPro } from "@/components/kline-chart-pro";
import { TradeForm } from "@/components/trade-form";
import { Panel } from "@/components/panel";
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { useQuoteStream } from "@/hooks/use-quote-stream";
import { analyze } from "@/lib/technical-analysis";
import { buildEventMarks } from "@/lib/event-markers";
import { ThemeChipsRow } from "@/components/detail/theme-chips";
import { StockEventsRow } from "@/components/detail/stock-events";
import {
  addToWatchlist,
  cancelPaperOrder,
  getAuction,
  getAnnouncements,
  getCapitalFlow,
  getCompanyProfile,
  getFinancials,
  getKline,
  getMarketOverview,
  getMinuteLine,
  getMinuteLineWithBaseline,
  getNews,
  getOrderBook,
  getPaperAccount,
  getPaperFills,
  getPaperOrders,
  getPaperPositions,
  getQuote,
  getQuotes,
  getNewsDigest,
  getStockThemes,
  getTrades,
  getWatchlist,
  placePaperOrder,
  resetPaperAccount,
  type AuctionData,
  type MinutePoint,
  type PaperFill,
  type StockThemes,
} from "@/lib/api";
import { fmt, pctColor, pctText } from "@/lib/format";
import type { Kline, OrderBook, Quote, Trade } from "@/types/market";
import { QuoteStrip } from "@/components/detail/quote-strip";
import { TradePanel, type PaperBundle } from "@/components/detail/trade-panel";
import { ProfilePanel, type CompanyProfile, type FinRow, type BoardRows } from "@/components/detail/profile-panel";
import { InfoPanel, type InfoItem } from "@/components/detail/info-panel";
import { BookTradesView } from "@/components/detail/book-trades-view";
import { ReplayChart } from "@/components/replay-chart";
import { FlowChart, type CapitalFlow } from "@/components/detail/flow-chart";

type ChartTab = "kline" | "minute" | "flow";
type RightTab = "book" | "trades" | "trade" | "profile" | "info";

/** 个股详情终端 v3（工作台右栏 / 个股页共用）：
 * 顶部紧凑行情条 → 中部 [左：图表区(K线/分时/资金图) | 右：盘口↔逐笔] → 右列：盘口↔逐笔 + 财务摘要。龙虎榜见独立页面。
 * K线带龙虎榜日标记与金叉死叉技术信号；滚动只存在于表格/列表容器内部。 */
export function StockDetailPanel({ symbol }: { symbol: string }) {
  const [chartTab, setChartTab] = useState<ChartTab>("kline");
  const [rightTab, setRightTab] = useState<RightTab>("book");
  // 布局 #1：右列宽度可拖拽（localStorage 持久化；260-480px 防极限）
  const [rightW, setRightW] = useState(300);
  const rightColRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const saved = Number(localStorage.getItem("ashare-right-w"));
    if (saved >= 260 && saved <= 480) setRightW(saved);
  }, []);
  // 历史回放（Phase 6 收官）：K 线页签内切换回放模式
  const [replayMode, setReplayMode] = useState(false);
  const [bars, setBars] = useState<Kline[]>([]);
  const [book, setBook] = useState<OrderBook | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [minutes, setMinutes] = useState<MinutePoint[]>([]);
  const [flow, setFlow] = useState<CapitalFlow | null>(null);
  const [fins, setFins] = useState<FinRow[] | null>(null);
  const [anns, setAnns] = useState<InfoItem[] | null>(null);
  const [news, setNews] = useState<InfoItem[] | null>(null);
  const [company, setCompany] = useState<CompanyProfile | null>(null);
  const [fills, setFills] = useState<PaperFill[]>([]);
  const [resetBusy, setResetBusy] = useState(false);
  const [paper, setPaper] = useState<PaperBundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [inWatchlist, setInWatchlist] = useState(false);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [auction, setAuction] = useState<AuctionData | null>(null);
  const [vrBaseline, setVrBaseline] = useState<number[] | null>(null);

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
        const q = await getQuote(symbol, "tencent");
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

  // 大盘叠加（分时图 P1）：上证分时 + 昨收，归一化成 % 曲线叠加在左轴。
  // 指数不随个股切换变化，只在挂载时拉一次（分时当日不变）。
  const [indexOverlay, setIndexOverlay] = useState<{ points: MinutePoint[]; prevClose: number } | null>(null);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [idxPoints, overview] = await Promise.all([
          getMinuteLine("sh000001").catch(() => [] as MinutePoint[]),
          getMarketOverview().catch(() => null),
        ]);
        const sh = overview?.indices.find((i) => i.symbol === "000001");
        if (alive && idxPoints.length > 0 && sh?.prev_close) {
          setIndexOverlay({ points: idxPoints, prevClose: sh.prev_close });
        }
      } catch {}
    })();
    return () => {
      alive = false;
    };
  }, []);

  // 集合竞价（09:25 终态）：分时图竞价点 + 角标。随 symbol 拉一次（当日不变）。
  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    getAuction(symbol)
      .then((a) => alive && setAuction(a))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [symbol]);

  // 题材归属（L4 联动）：官方成分 + 当日涨停归因，chip 点击跳题材看板聚焦。
  // 独立请求 + 静默失败：归属缺失只影响这一行，不拖垮详情页。
  const [stockThemes, setStockThemes] = useState<StockThemes | null>(null);
  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    setStockThemes(null); // 切股先清空，防残留上一只的题材
    getStockThemes(symbol)
      .then((t) => alive && setStockThemes(t))
      .catch(() => {});
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
    setAuction(null); // 竞价数据按 symbol 归属，切股先清空防残留
    setVrBaseline(null); // 精确量比基线同理
    setFlow(null);
    setFins(null);
    Promise.all([
      getKline(symbol, "1d", 120),
      getOrderBook(symbol).catch(() => null),
      getTrades(symbol, 30).catch(() => [] as Trade[]),
      getMinuteLineWithBaseline(symbol)
        .then((r) => {
          setVrBaseline(r.vr_baseline_5m);
          return r.points;
        })
        .catch(() => [] as MinutePoint[]),
      getCapitalFlow<CapitalFlow>(symbol, 30).catch(() => null),
      getFinancials<FinRow>(symbol, 8).catch(() => null),
      getCompanyProfile<CompanyProfile>(symbol).catch(() => null),
      // 资讯走摘要端点：一次拿回公告+新闻，并附带重要度/情绪/事实摘要。
      // 摘要失败不拖垮整页——降级成空列表，页面其余部分照常。
      getNewsDigest(symbol, 8)
        .then((d) => ({ anns: d.announcements as unknown as InfoItem[], news: d.news as unknown as InfoItem[] }))
        .catch(() => null),
    ])
      .then(([b, ob, tr, min, cf, fins, comp, info]) => {
        if (!alive) return;
        setBars(b);
        setBook(ob);
        setTrades(tr);
        setMinutes(min);
        if (cf) setFlow(cf);
        if (fins) setFins(fins);
        if (comp) setCompany(comp);
        setAnns(info ? info.anns : []);
        setNews(info ? info.news : []);
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

  const tech = analyze(
    bars.map((b) => ({ ts: b.ts, open: b.open ?? 0, high: b.high ?? 0, low: b.low ?? 0, close: b.close ?? 0, volume: b.volume, change_pct: b.change_pct }))
  );

  // 当前个股的模拟持仓（用于 K 线成本线）
  const myPosition = paper?.positions.find((p) => p.symbol === symbol) ?? null;
  const costPrice = myPosition && myPosition.quantity > 0 ? myPosition.cost_price : null;

  // 新闻/公告 → K 线事件点（P1-8）：复用 digest 已取回的数据，零新增请求
  const eventMarks = useMemo(
    () => buildEventMarks(bars.map((b) => b.ts.slice(0, 10)), anns ?? [], news ?? []),
    [bars, anns, news],
  );

  // 板块标签分组：把风格/指数成分与概念题材分开，避免"大盘股/MSCI中国"混进题材
  const boardGroups = company?.board_groups;
  const boardRows: BoardRows = boardGroups
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

  return (
    <div className="flex min-h-0 min-w-0 flex-col gap-2">
      {error && (
        <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-600 dark:text-amber-300">{error}</div>
      )}

      {/* ① 紧凑行情条 */}
      {quote && <QuoteStrip quote={quote} inWatchlist={inWatchlist} onAdd={() => void add()} />}

      {/* ①½ 题材归属 chips（官方成分 / 涨停归因双源）→ 题材看板聚焦 */}
      <ThemeChipsRow themes={stockThemes} />

      {/* ①¾ 相关事件（E2/L9）：方向题材命中归属 或 事件源自该股 */}
      <StockEventsRow symbol={symbol} />

      {/* ② 中部：左图表区 + 右盘口/逐笔（右列宽度可拖拽，--right-w 由 state 注入） */}
      <div
        className="grid min-h-0 min-w-0 flex-1 grid-cols-1 gap-2 lg:grid-cols-[minmax(0,1fr),var(--right-w)]"
        style={{ "--right-w": `${rightW}px` } as React.CSSProperties}
      >
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
            <Panel title="日 K 线（前复权 · 默认聚焦最近 20 日，可缩放看全部）" source={bars[0]?.source} bodyClassName="overflow-hidden" className="min-h-0 flex-1" extra={
              !replayMode && bars.length >= 60 && (
                <button onClick={() => setReplayMode(true)} className="rounded border border-sky-500/50 px-2 py-0.5 text-xs text-sky-400 hover:bg-sky-500/10">
                  ▶ 历史回放
                </button>
              )
            }>
              {bars.length > 0 ? (
                replayMode ? (
                  <ReplayChart bars={bars} fills={fills} onExit={() => setReplayMode(false)} />
                ) : (
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
                    <KlineChartPro bars={bars} tradeMarks={fills} costPrice={costPrice} eventMarks={eventMarks} className="h-full" />
                  </div>
                </div>
                )
              ) : (
                <p className="px-4 py-10 text-center text-sm text-zinc-400">等待 K 线数据…</p>
              )}
            </Panel>
          )}

          {chartTab === "minute" && (
            <Panel title="当日分时（1 分钟）" source={minutes[0]?.source} bodyClassName="overflow-hidden" className="min-h-0 flex-1">
              {minutes.length > 0 ? (
                <MinuteChart
                  points={minutes}
                  prevClose={quote?.prev_close ?? null}
                  yesterdayVol={bars.length >= 2 ? (bars[bars.length - 2]?.volume ?? null) : null}
                  index={indexOverlay}
                  auction={auction?.auction_price ? { price: auction.auction_price, pct: auction.auction_pct } : null}
                  exactBaseline={vrBaseline}
                  className="h-full"
                />
              ) : (
                <p className="px-4 py-10 text-center text-sm text-zinc-400">暂无分时数据</p>
              )}
            </Panel>
          )}

          {chartTab === "flow" && (
            <Panel title="主力资金净流入（近 30 日 · 左图右明细）" source={flow?.flow[0]?.source} bodyClassName="overflow-hidden" className="min-h-0 flex-1">
              {!flow || flow.flow.length === 0 ? (
                <p className="px-4 py-10 text-center text-sm text-zinc-400">暂无资金流数据</p>
              ) : (
                <FlowChart flow={flow} />
              )}
            </Panel>
          )}
        </div>

        {/* 右列：盘口↔逐笔 + 资讯 tabs（左缘拖拽条调宽，宽度持久化 localStorage） */}
        <div ref={rightColRef} className="relative flex min-h-0 flex-col gap-2">
          <div
            onMouseDown={(e) => {
              e.preventDefault();
              const rect = rightColRef.current?.getBoundingClientRect();
              if (!rect) return;
              document.body.style.userSelect = "none";
              const move = (ev: MouseEvent) => {
                setRightW(Math.min(480, Math.max(260, Math.round(rect.right - ev.clientX))));
              };
              // mouseup 必须挂 window：释放时鼠标通常已离开拖拽条，
              // 元素级 onMouseUp 永远不会触发（首测实抓：宽度变了但 localStorage 为 null）
              const up = () => {
                document.body.style.userSelect = "";
                window.removeEventListener("mousemove", move);
                window.removeEventListener("mouseup", up);
                setRightW((w) => {
                  localStorage.setItem("ashare-right-w", String(w));
                  return w;
                });
              };
              window.addEventListener("mousemove", move);
              window.addEventListener("mouseup", up);
            }}
            className="absolute -left-2 top-0 z-10 h-full w-2 cursor-col-resize hover:bg-sky-500/25"
            title="拖拽调整右列宽度"
            aria-label="拖拽调整右列宽度"
          />

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
            <TradePanel
              symbol={symbol}
              paper={paper}
              fills={fills}
              quote={quote}
              resetBusy={resetBusy}
              onResetAccount={() => void handleResetAccount()}
            />
          )}

          {rightTab === "profile" && <ProfilePanel boardRows={boardRows} company={company} fins={fins} />}

          {rightTab === "info" && <InfoPanel anns={anns} news={news} />}

          <BookTradesView book={book} trades={trades} showBook={rightTab === "book"} />
        </Panel>
        </div>
      </div>
    </div>
  );
}

async function getWatchlistSymbols(): Promise<string[]> {
  try {
    return (await getWatchlist()).map((i) => i.symbol);
  } catch {
    return [];
  }
}
