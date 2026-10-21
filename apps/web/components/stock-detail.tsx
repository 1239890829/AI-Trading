"use client";

import { SelectionRail } from "@/components/ui/selection-rail";
import { useExitPresence, useReducedMotion } from "@/hooks/use-exit-presence";

import { useCallback, useEffect, memo, useId, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { MinuteChart } from "@/components/minute-chart";
import { KlineChartPro } from "@/components/kline-chart-pro";
import { priceLimitPct } from "@/lib/price-limit";
import { contextualRightTab, rightTabsFor, type DetailRightTab } from "@/lib/detail-tabs";
import { Panel } from "@/components/panel";
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { useQuoteStream, STREAM_STATUS_LABEL, STREAM_ABNORMAL, type QuoteFeed, type StreamStatus } from "@/hooks/use-quote-stream";
import { usePollingFetch, useResource } from "@/hooks/use-polling-fetch";
import type { RealPositionsResource } from "@/hooks/use-real-positions";
import { analyze } from "@/lib/technical-analysis";
import { buildEventMarks, buildMinuteNewsEvents } from "@/lib/event-markers";
import { hasTrustedQuoteObservation, mergeQuoteIntoBars, mergeQuoteIntoMinutes } from "@/lib/kline-live";
import {buildIndexOverlay, indexQuoteReference} from "@/lib/index-overlay";
import {limitQualification} from "@/lib/limit-qualification";
import { isIndexSymbol } from "@/lib/api";
import { notifyWatchlistChanged, subscribeWatchlist } from "@/lib/watchlist-sync";
import { LimitUpReasonPanel } from "@/components/detail/limit-up-reason";
import {useSymbolDetail} from "@/components/detail/symbol-detail-context";
import {useDetailModal} from "@/components/detail/detail-modal";
import {quoteQualityView} from "@/lib/quote-quality-view";
import { StockEventsRow } from "@/components/detail/stock-events";
import { TradingAgentsResearchPanel } from "@/components/detail/tradingagents-research-panel";
import {
  addToWatchlist,
  getAuction,
  getCapitalFlow,
  getCompanyProfile,
  getFinancials,
  getKlinePayload,
  getMarketOverview,
  getLimitUpPoolSnapshot,
  getMinuteLine,
  getMinuteLineWithBaseline,
  getOrderBook,
  getQuote,
  getQuotes,
  getNewsDigest,
  getStockThemes,
  getTradesPayload,
  getWatchlist,
  type MinutePoint,
} from "@/lib/api";
import { bjDate, fmt, pctColor, pctText, sourceLabel, dateTimeTextBJ } from "@/lib/format";
import type { Kline, OrderBook, Quote, Trade, TradingStatusInfo } from "@/types/market";
import { QuoteStrip } from "@/components/detail/quote-strip";
import { ProfilePanel, type CompanyProfile, type FinRow, type BoardRows } from "@/components/detail/profile-panel";
import { InfoPanel, type InfoItem } from "@/components/detail/info-panel";
import { BookTradesView } from "@/components/detail/book-trades-view";
import {DetailReadStatus, type DetailReadState} from "@/components/detail/read-status";
import { Skeleton } from "@/components/ui/loading";
import { FlowChart, type CapitalFlow } from "@/components/detail/flow-chart";
import { IndexMembersPanel } from "@/components/detail/index-members-panel";
import { BoardRankPanel } from "@/components/detail/board-rank-panel";
import { RealPositionPanel } from "@/components/detail/real-position-panel";
import { SuspendedBadge, SuspendedNotice, isSuspended } from "@/components/detail/suspended-badge";

/** 个股详情终端 v3（工作台右栏 / 个股页共用）：
 * 顶部紧凑行情条 → 中部 [左：图表区(K线/分时/资金图) | 右：盘口↔逐笔] → 右列：盘口↔逐笔 + 财务摘要。龙虎榜见独立页面。
 * K线带龙虎榜日标记与金叉死叉技术信号；滚动只存在于表格/列表容器内部。 */
export type ChartTab = "kline" | "minute" | "flow";
/** 右列 tab 键与 tab 列表见 lib/detail-tabs.ts（抽成纯模块以便单测与口径断言）。 */
export type RightTab = DetailRightTab;
/**
 * 助手「一键跳转」的落点参数（2026-09-06，docs/archive/assistant-optimization-plan.md §1.3）：
 * 工作台 URL 的 ?ct=（图表区 tab）与 ?rt=（右栏 tab）由调用方（workbench 页）解析后
 * 传进来。这里只做**初值 + 跟随变化**：内部仍是 state，用户手动切 tab 不回写 URL
 * （避免每次点击都产生历史/路由噪音），但外部深链进来必须生效——
 * 面板随 symbol 走 key 重挂载，同一只股票内换 tab 则由 effect 跟随。
 */
export interface StockDetailTabs {
  chartTab?: ChartTab;
  rightTab?: RightTab;
  onRightTabChange?: (tab: RightTab) => void;
}

/** P1-5（2026-09-11）：上游已订阅当前标的时，把行情与连接态传下来。
 *
 *  背景：workbench 的 `useQuoteStream` 订阅集**已含 activeSymbol**，而详情面板此前
 *  又为同一只股票自建一条 WS ⇒ 同一标的被两条连接订阅；且面板随 `key={activeSymbol}`
 *  重挂载，每次切股都要关旧建新，中间有一段行情空窗。
 *
 *  两者都传时面板不再建连接；都不传时保持原行为（自带连接）——便于独立嵌入。
 */
export interface StockDetailStreamProps {
  liveQuote?: Quote;
  streamStatus?: StreamStatus;
  quoteFeed?: QuoteFeed;
  /** Existing overview owner's observation; null is an explicit missing index. */
  indexQuote?: Quote | null;
}

/** 空订阅集（引用恒定；`useQuoteStream([])` 的 hasSymbols=false ⇒ 不建连接）。 */
const NO_SYMBOLS: string[] = [];
const NO_QUOTE = () => undefined;
type DetailReadKind = "kline" | "minute" | "book" | "trades" | "financials" | "flow";
const emptyReadState = (): DetailReadState => ({pending: false, loaded: false, error: null, readAt: null});
const emptyDetailReads = (): Record<DetailReadKind, DetailReadState> => ({
  kline: emptyReadState(), minute: emptyReadState(), book: emptyReadState(),
  trades: emptyReadState(), financials: emptyReadState(), flow: emptyReadState(),
});

/**
 * 个股详情面板（含 K 线/分时/盘口/逐笔/资金/财务多页签）。
 *
 * P1-1（2026-09-11）：包 `memo`。这是工作台里**最重的子树**（图表 + 多张表），
 * 行情通过同一 owner 的选中证券订阅更新，不必等待工作台的 3s 列表刷新。
 * 隐藏详情退订即时消费；同股状态与交易草稿仍保留。
 */
export const StockDetailPanel = memo(function StockDetailPanel({
  symbol,
  chartTab: chartTabInit,
  rightTab: rightTabInit,
  onRightTabChange,
  onSymbolChange,
  liveQuote: liveQuoteProp,
  streamStatus: streamStatusProp,
  quoteFeed: quoteFeedProp,
  indexQuote,
  realPositions,
  watchlistMembership,
  onWatchlistAdded,
  active = true,
}: { symbol: string; onSymbolChange?: (symbol: string) => void; active?: boolean; realPositions?: RealPositionsResource; watchlistMembership?: boolean | null; onWatchlistAdded?: (symbol: string) => void } & StockDetailTabs & StockDetailStreamProps) {
  const {open: openDetail} = useDetailModal();
  const {open: openSymbolDetail} = useSymbolDetail();
  const [chartTab, setChartTab] = useState<ChartTab>(chartTabInit ?? "kline");
  const [rightTab, setRightTab] = useState<RightTab>(rightTabInit ?? "book");
  // 深链跟随：同一只股票内（面板未重挂载）外部改了 ct/rt 也要生效。
  // 用「渲染期比对上一轮 props 后调整 state」而非 effect——effect 里 setState 会被
  // lint 判为级联渲染，且首帧仍会闪一下默认 tab。
  const [prevInit, setPrevInit] = useState<{ c?: ChartTab; r?: RightTab }>({
    c: chartTabInit,
    r: rightTabInit,
  });
  if (prevInit.c !== chartTabInit || prevInit.r !== rightTabInit) {
    setPrevInit({ c: chartTabInit, r: rightTabInit });
    if (chartTabInit) setChartTab(chartTabInit);
  }
  // 布局 #1：右列宽度可拖拽（localStorage 持久化；260-480px 防极限）
  const [rightW, setRightW] = useState(300);
  const rightColRef = useRef<HTMLDivElement>(null);
  const chartColumnRef = useRef<HTMLDivElement>(null);
  const previousChartTab = useRef(chartTab);
  const reducedMotion = useReducedMotion();
  useEffect(() => {
    if (previousChartTab.current === chartTab) return;
    previousChartTab.current = chartTab;
    const column = chartColumnRef.current;
    if (reducedMotion || typeof column?.animate !== "function") return;
    // Animate the stable reading surface only on a deliberate tab change.
    // Quote ticks and right-column resizing keep the existing chart instance.
    const animation = column.animate([{opacity: 0.65}, {opacity: 1}], {duration: 160, easing: "ease-out"});
    return () => animation.cancel();
  }, [chartTab, reducedMotion]);

  // 布局方案 B（2026-09-04 用户拍板）：右列可整体收起，收起后 K 线/分时获得全宽
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [exitingRightW, setExitingRightW] = useState(300);
  const rightPresence = useExitPresence(rightCollapsed ? null : true);
  const rightActive = active && !rightCollapsed;
  useEffect(() => {
    // Read after hydration; defer state restoration instead of synchronously
    // setting state in an effect or changing the server's initial layout.
    let alive = true;
    queueMicrotask(() => {
      if (!alive) return;
      try {
        const saved = Number(localStorage.getItem("ashare-right-w"));
        if (saved >= 260 && saved <= 480) setRightW(saved);
        if (localStorage.getItem("ashare-right-collapsed") === "1") setRightCollapsed(true);
      } catch {}
    });
    return () => { alive = false; };
  }, []);
  const toggleRightCollapsed = () => {
    if (!rightCollapsed) setExitingRightW(rightColRef.current?.getBoundingClientRect().width || rightW);
    setRightCollapsed(!rightCollapsed);
    try { localStorage.setItem("ashare-right-collapsed", rightCollapsed ? "0" : "1"); } catch {}
  };
  // 核对标签与内容面板共用稳定的可访问标识。
  const perspectiveId = useId();
  // 技术评估条默认**折叠**（2026-09-02 评审 #5：K 线图被周边元素挤占）。
  // 折叠后只留一行多空结论，把垂直空间还给 K 线；要看信号明细再点开。
  // 采用保守方案：不改整体布局与右列宽度，随时可一键还原为常显。
  const [techOpen, setTechOpen] = useState(false);
  const [bars, setBars] = useState<Kline[]>([]);
  // 停牌判定（UI 缺陷 #1）：随日K 一同返回，非日线为 null（未判定，非"正常"）
  const [tradingStatus, setTradingStatus] = useState<TradingStatusInfo | null>(null);
  // 三态数据纪律（2026-09-08 审查 F1，修"加载中闪现错误文案"）：undefined=尚未
  // 拉到（渲染 Skeleton），null=拉过且确认无（渲染空态文案）——此前 null 身兼
  // 两职，切股重挂载的窗口期把"加载中"渲染成"盘口不可用/暂无逐笔"等误导文案。
  const [book, setBook] = useState<OrderBook | null | undefined>(undefined);
  const [trades, setTrades] = useState<Trade[] | undefined>(undefined);
  const [minutes, setMinutes] = useState<MinutePoint[]>([]);
  const [flow, setFlow] = useState<CapitalFlow | null>(null);
  const [readStates, setReadStates] = useState(emptyDetailReads);
  const [tradeEmptyReason, setTradeEmptyReason] = useState<string | null>(null);
  const [tradeSource, setTradeSource] = useState<string | null>(null);
  // fins 三态：undefined=加载中（ProfilePanel 骨架），null=确认无/拉取失败
  const [fins, setFins] = useState<FinRow[] | null | undefined>(undefined);
  const [anns, setAnns] = useState<InfoItem[] | null>(null);
  const [news, setNews] = useState<InfoItem[] | null>(null);
  // digest 数据源三态：非 null = 该侧降级（InfoPanel 显式提示，非静默空列表）
  const [annError, setAnnError] = useState<string | null>(null);
  const [newsError, setNewsError] = useState<string | null>(null);
  const [digestLoading, setDigestLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inWatchlist, setInWatchlist] = useState(false);
  // quote 三态：undefined=WS 快照/REST 均未到达（行情条渲染骨架）；null=两侧都
  // 确认拉不到（渲染"行情不可用"）；有值=正常。WS 合并与 REST 轮询共用该状态。
  const [quote, setQuote] = useState<Quote | null | undefined>(undefined);

  const [vrBaseline, setVrBaseline] = useState<number[] | null>(null);
  // 指数 symbol（sh000001 等）：禁用个股专属面板（加自选/交易/资料/资金图）
  const isIndex = isIndexSymbol(symbol);
  // Normalize both directions before a consumer mounts; old index deep links
  // must never keep a board request alive under a stock heading.
  const requestedRightTab = prevInit.r !== rightTabInit && rightTabInit ? rightTabInit : rightTab;
  const resolvedRightTab = contextualRightTab(isIndex, requestedRightTab);
  if (resolvedRightTab !== rightTab) setRightTab(resolvedRightTab);
  if (isIndex && chartTab === "flow") setChartTab("kline");

  // 上游 feed/连接态也是连接归属证据，未收到首帧时不另建 WS。
  const ownsStream = quoteFeedProp !== undefined || liveQuoteProp !== undefined || streamStatusProp !== undefined;
  const own = useQuoteStream(!active || ownsStream ? NO_SYMBOLS : [symbol], { throttleMs: 3000 });
  const streamStatus = streamStatusProp ?? own.status;
  const quoteFeed = quoteFeedProp ?? own.quoteFeed;
  const subscribeQuote = useCallback((listener: () => void) => active && quoteFeed
    ? quoteFeed.subscribe(symbol, listener) : () => {}, [active, quoteFeed, symbol]);
  const snapshotQuote = useCallback(() => active ? quoteFeed?.getSnapshot(symbol) : undefined, [active, quoteFeed, symbol]);
  const immediateQuote = useSyncExternalStore(subscribeQuote, snapshotQuote, NO_QUOTE);
  // 未观察到首帧时可用旧接口的首屏报价；收到缺项帧（null）后不能回退慢 prop。
  // 完整即时 Quote 保留源时间/质量/量额，慢列表 prop 不覆盖较新的详情报价。
  const live = immediateQuote === undefined ? liveQuoteProp ?? own.quotes[symbol] : immediateQuote;
  // WS 推送 → 渲染期合并进 quote（adjust-state 模式）：live 引用每拍必变，
  // 哨兵 appliedLive 保证同一帧只合并一次，语义与原 effect 版完全等价。
  // （live 的来源见上方 P1-5：上游 prop 优先，否则自带连接）
  const [appliedLive, setAppliedLive] = useState<Quote | null>(null);
  if (active && live && live !== appliedLive) {
    setAppliedLive(live);
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
  }
  // 报价补源与模拟账户都自行写 state，不能只依赖轮询 hook 丢弃旧 key 的返回值。
  // 每次切股产生独立身份，A→B→A 及卸载后也不接受旧实例的回包。
  const detailReadScopeRef = useRef<{ symbol: string; alive: boolean } | null>(null);
  useEffect(() => {
    if (!active) return;
    const scope = { symbol, alive: true };
    detailReadScopeRef.current = scope;
    return () => { scope.alive = false; };
  }, [symbol, active]);

  // 估值补充：ths 快照无 PE/PB/市值，每 30s 从腾讯源低频补齐（价格仍以 WS 为准）
  // 2026-09-11（S2-5）：裸 setInterval → 统一入口（获得可见性暂停 + 盘外降频）。
  usePollingFetch(
    async () => {
      const scope = detailReadScopeRef.current;
      if (!scope?.alive || scope.symbol !== symbol) return;
      try {
        const q = await getQuote(symbol, "tencent");
        if (!scope.alive || scope !== detailReadScopeRef.current) return;
        setQuote((prev) => {
          // 同股补估值；慢 REST 不覆盖已接纳报价的源钟、质量、量额与来源。
          if (!prev || prev.symbol !== q.symbol) return q;
          return {
            ...q,
            ...prev,
            pe_ttm: q.pe_ttm ?? prev.pe_ttm,
            pb: q.pb ?? prev.pb,
            total_mktcap_yi: q.total_mktcap_yi ?? prev.total_mktcap_yi,
            float_mktcap_yi: q.float_mktcap_yi ?? prev.float_mktcap_yi,
            limit_up_price: prev.limit_up_price ?? q.limit_up_price,
            limit_down_price: prev.limit_down_price ?? q.limit_down_price,
          };
        });
      } catch {
        if (!scope.alive || scope !== detailReadScopeRef.current) return;
        // REST 失败且 WS 也还没推来快照 → 确认行情拉不到（渲染"不可用"）；
        // 已有数据（WS 在推）保持不变——30s 轮询偶发失败不该抹掉实时价
        setQuote((prev) => (prev === undefined ? null : prev));
      }
    },
    30_000,
    symbol,
    { enabled: active && !!symbol }
  );

  const digestRequestRef = useRef(0);
  const loadDigest = useCallback(async () => {
    const scope = detailReadScopeRef.current;
    if (!scope?.alive || scope.symbol !== symbol) return;
    const request = ++digestRequestRef.current;
    setDigestLoading(true);
    try {
      const d = await getNewsDigest(symbol, 8);
      if (!scope.alive || scope !== detailReadScopeRef.current || request !== digestRequestRef.current) return;
      // 单侧失败返回的空数组不证明源确无；保留该侧原始日期、来源与正文。
      setAnns(prev => d.announcements_error && d.announcements.length === 0 ? prev : d.announcements as unknown as InfoItem[]);
      setNews(prev => d.news_error && d.news.length === 0 ? prev : d.news as unknown as InfoItem[]);
      setAnnError(d.announcements_error);
      setNewsError(d.news_error);
    } catch (e) {
      if (!scope.alive || scope !== detailReadScopeRef.current || request !== digestRequestRef.current) return;
      const message = e instanceof Error ? e.message : "资讯读取失败";
      setAnnError(message);
      setNewsError(message);
    } finally {
      if (scope.alive && scope === detailReadScopeRef.current && request === digestRequestRef.current) setDigestLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    if (!active || watchlistMembership !== undefined) return;
    let alive = true;
    let generation = 0;
    let controller: AbortController | undefined;
    const reload = () => {
      const current = ++generation;
      controller?.abort();
      controller = new AbortController();
      getWatchlistSymbols(controller.signal).then((list) => {
        if (alive && current === generation) setInWatchlist(list.includes(symbol));
      }, () => { /* 失败或取消不证明集合为空，保留已确认的 membership。 */ });
    };
    reload();
    const unsubscribe = subscribeWatchlist(reload);
    return () => {
      alive = false;
      controller?.abort();
      unsubscribe();
    };
  }, [symbol, active, watchlistMembership]);

  // 题材归属（L4 联动）：官方成分 + 当日涨停归因，chip 点击跳题材看板聚焦。
  // 复用原一次读取，按symbol绑定数据；右栏可显式重试，失败不拖垮图表。

  // 切股清空三件套（渲染期 adjust-state：symbol 变化时同步重置，防上一只残留）——
  // 切股时清空本股读取结果，自选与题材仍由各自owner管理。
  const [prevResetSymbol, setPrevResetSymbol] = useState<string | null>(null);
  if (symbol !== prevResetSymbol) {
    setPrevResetSymbol(symbol);
    setAnns(null);
    setNews(null);
    setAnnError(null);
    setNewsError(null);
    setDigestLoading(false);
    setInWatchlist(false);
    setFins(undefined); setFlow(null);
    setQuote(undefined); setAppliedLive(null);
    setReadStates(emptyDetailReads()); setTradeEmptyReason(null); setTradeSource(null);
  }
  const quoteDate = quote?.symbol === symbol && quote.data_timestamp && /(?:Z|[+-]\d{2}:\d{2})$/.test(quote.data_timestamp) ? bjDate(quote.data_timestamp) : null;
  const reasonResource = useResource(() => getLimitUpPoolSnapshot(quoteDate || undefined, {includeBreaks: true}), {
    key: `${symbol}:${quoteDate || "unknown"}`, enabled: rightActive && !isIndex && !!quoteDate, intervalMs: 60_000,
  });
  const qualification = limitQualification(reasonResource.data, symbol, quoteDate);
  const viewRightTab = rightTab === "reason" && !qualification.eligible ? "profile" : rightTab;
  const parentRightTab = viewRightTab === "themes" ? "profile" : viewRightTab === "events" ? "info" : viewRightTab;

  const profileVisible = rightActive && !isIndex && (viewRightTab === "profile" || viewRightTab === "themes");
  const themesResource = useResource(async () => {
    const result = await getStockThemes(symbol);
    if (result && result.symbol !== symbol) throw new Error("题材资料标的不匹配");
    return result;
  }, {key: symbol, enabled: profileVisible && !!symbol, intervalMs: null, marketHours: false});
  const stockThemes = themesResource.data ?? null;
  const companyResource = useResource(() => getCompanyProfile<CompanyProfile>(symbol), {
    key: symbol, enabled: profileVisible, intervalMs: null, marketHours: false,
  });
  const company = companyResource.data ?? null;
  const auctionResource = useResource(() => getAuction(symbol), {
    key: symbol, enabled: active && !isIndex && chartTab === "minute", intervalMs: null, marketHours: false,
  });
  const auction = auctionResource.data;
  const auctionProp = useMemo(() => auction?.auction_price ? {price: auction.auction_price, pct: auction.auction_pct} : null, [auction]);

  // Visible consumers own their reads. The hidden daily bar is retained only
  // for trading/suspension status; no idle burst for the other perspectives.
  const [prevDataSymbol, setPrevDataSymbol] = useState<string | null>(null);
  if (symbol !== prevDataSymbol) {
    setPrevDataSymbol(symbol);
    setBars([]);
    setTradingStatus(null);
    setMinutes([]);
    setVrBaseline(null);
    setBook(isIndexSymbol(symbol) ? null : undefined);
    setTrades(isIndexSymbol(symbol) ? [] : undefined);
  }

  const dataTabsRef = useRef({ chartTab, rightTab });
  useEffect(() => {
    dataTabsRef.current = { chartTab, rightTab };
  }, [chartTab, rightTab]);
  const dataReadsRef = useRef<{
    alive: boolean;
    kline: (fresh?: boolean) => ReturnType<typeof getKlinePayload>;
    book: (fresh?: boolean) => ReturnType<typeof getOrderBook>;
    minute: (fresh?: boolean) => ReturnType<typeof getMinuteLineWithBaseline>;
    trades: (fresh?: boolean) => ReturnType<typeof getTradesPayload>;
    financials: (fresh?: boolean) => Promise<FinRow[]>;
    flow: (fresh?: boolean) => Promise<CapitalFlow>;
  } | null>(null);

  useEffect(() => {
    if (!active || !symbol) return;
    // Prefetch and a visible perspective share one application of each result.
    // A manual retry owns a new generation, so a slow old read cannot overwrite it.
    const managedRead = <T,>(kind: DetailReadKind, load: () => Promise<T>, apply: (value: T) => void) => {
      let flight: Promise<T> | null = null;
      let generation = 0;
      let financialSnapshot: {date: string; value: T} | null = null;
      return (fresh = false): Promise<T> => {
        if (flight && !fresh) return flight;
        const date = bjDate(new Date().toISOString());
        if (!fresh && kind === "financials" && financialSnapshot?.date === date) return Promise.resolve(financialSnapshot.value);
        const request = ++generation;
        const current = () => reads.alive && reads === dataReadsRef.current && request === generation;
        const next = Promise.resolve().then(async () => {
          if (current()) setReadStates(previous => ({...previous, [kind]: {...previous[kind], pending: true}}));
          try {
            const value = await load();
            if (current()) {
              apply(value);
              if (kind === "financials") financialSnapshot = {date, value};
              setReadStates(previous => ({...previous, [kind]: {pending: false, loaded: true, error: null, readAt: new Date().toISOString()}}));
            }
            return value;
          } catch (error) {
            if (current()) setReadStates(previous => ({...previous, [kind]: {...previous[kind], pending: false, error: error instanceof Error ? error.message : "读取失败"}}));
            throw error;
          }
        });
        flight = next;
        const clear = () => {if (flight === next) flight = null;};
        void next.then(clear, clear);
        return next;
      };
    };
    const reads: NonNullable<typeof dataReadsRef.current> = {
      alive: true,
      kline: managedRead("kline", () => getKlinePayload(symbol, "1d", 120), payload => {
        if (payload.symbol !== symbol || payload.timeframe !== "1d" || payload.bars.some(row => row.symbol !== symbol)) throw new Error("K线证券身份不匹配");
        setBars(payload.bars); setTradingStatus(payload.trading_status);
      }),
      book: managedRead("book", () => getOrderBook(symbol), value => {
        if (value.symbol !== symbol) throw new Error("盘口证券身份不匹配");
        setBook(value);
      }),
      minute: managedRead("minute", () => getMinuteLineWithBaseline(symbol), value => {
        if ("symbol" in value && value.symbol !== symbol) throw new Error("分钟证券身份不匹配");
        setMinutes(value.points);
        setVrBaseline(previous => {
          const next = value.vr_baseline_5m;
          return previous && next && previous.length === next.length && previous.every((item, index) => item === next[index]) ? previous : next;
        });
      }),
      trades: managedRead("trades", () => getTradesPayload(symbol, 30), value => {
        if (value.items.some(row => row.symbol !== symbol)) throw new Error("成交证券身份不匹配");
        setTrades(value.items); setTradeEmptyReason(value.empty_reason); setTradeSource(value.source);
      }),
      financials: managedRead("financials", () => getFinancials<FinRow>(symbol, 8), value => {
        if (value !== null && !Array.isArray(value)) throw new Error("财报响应缺少报告列表");
        setFins(value ?? []);
      }),
      flow: managedRead("flow", () => getCapitalFlow<CapitalFlow>(symbol, 30), value => setFlow(value)),
    };
    dataReadsRef.current = reads;
    const ignoreFailure = () => {};
    if (dataTabsRef.current.chartTab !== "kline") void reads.kline().catch(ignoreFailure);
    return () => {reads.alive = false;};
  }, [symbol, active]);

  // ---- 盘中实时刷新（2026-08-31 修复"K线/分时/盘口/逐笔拉一次就死"）----
  // 此前所有图表数据只在切股时拉取一次，盘中永不更新——K 线最后一根
  // 停在进场时刻，跟不上行情。频率依据：K 线/分时数据源粒度是分钟级，
  // 60s 校准已超过够用；盘口变化最快对齐 WS 降级轮询的 5s；逐笔走东财
  // （WAF 限流）10s 保守。实时性主力是下面的 WS 合成，不靠轮询。
  // ① K 线当日 bar 的秒级合成：渲染期派生（外部状态订阅的官方推荐模式），
  //    WS quote 更新 → 最后一根 bar 实时跟进；价格未动时 mergeQuoteIntoBars
  //    返回 null → 引用不变 → 下游图表不重渲染。
  const retainedLive = appliedLive?.symbol === symbol ? appliedLive : undefined;
  const candidateLive = active ? live ?? retainedLive : retainedLive;
  const liveQuote = candidateLive?.symbol === symbol ? candidateLive : undefined;
  const projectedBars = useMemo(() => mergeQuoteIntoBars(bars, liveQuote), [bars, liveQuote]);
  // ①' 分时右端点秒级合成：同 K 线思路，WS quote 跟进最后一根分钟点
  //    （价格+累计量），与列表/K线保持同一 1s 节奏，不再干等 60s REST 校准
  const projectedMinutes = useMemo(() => mergeQuoteIntoMinutes(minutes, liveQuote, isIndex), [minutes, liveQuote, isIndex]);
  // Rejected frames do not write a new point and must not undo an already
  // displayed trusted point. Each formal REST replacement resets its own view;
  // symbol/day/period changes cannot borrow a prior projection or its volume.
  const chartContext = `${symbol}:1d`;
  const quoteClock = liveQuote?.data_timestamp;
  const sourceDate = quoteClock && /(?:Z|[+-]\d{2}:\d{2})$/.test(quoteClock) && Number.isFinite(Date.parse(quoteClock)) ? bjDate(quoteClock) : null;
  const [acceptedChart, setAcceptedChart] = useState(() => ({context: chartContext, sourceDate, bars, minutes, quote: liveQuote,
    displayBars: projectedBars ?? bars, displayMinutes: projectedMinutes ?? minutes}));
  let chartView = acceptedChart;
  if (acceptedChart.context !== chartContext || acceptedChart.bars !== bars || acceptedChart.minutes !== minutes || acceptedChart.quote !== liveQuote) {
    const rejected = !hasTrustedQuoteObservation(liveQuote);
    const sameContext = acceptedChart.context === chartContext && (!sourceDate || !acceptedChart.sourceDate || sourceDate === acceptedChart.sourceDate);
    chartView = {context: chartContext, sourceDate: sourceDate || (acceptedChart.context === chartContext ? acceptedChart.sourceDate : null), bars, minutes, quote: liveQuote,
      displayBars: projectedBars ?? (rejected && sameContext && acceptedChart.bars === bars ? acceptedChart.displayBars : bars),
      displayMinutes: projectedMinutes ?? (rejected && sameContext && acceptedChart.minutes === minutes ? acceptedChart.displayMinutes : minutes)};
    setAcceptedChart(chartView);
  }
  const {displayBars, displayMinutes} = chartView;
  // prev_close 只有在能证明与分钟点同一北京交易日时才可作为百分比轴基准。
  // data_timestamp 是源行情时刻；received_at 只是接收时刻，不能拿它替 stale 数据“补日期”。
  const minuteReferenceDate = quote?.data_timestamp ? bjDate(quote.data_timestamp) || null : null;
  // One current-symbol owner applies a result once, including its error state.
  usePollingFetch(async () => {await dataReadsRef.current?.kline().catch(() => {});}, 60_000, symbol,
    {enabled: active && !!symbol && chartTab === "kline"});
  usePollingFetch(async () => {
    const reads = dataReadsRef.current; if (!reads?.alive) return;
    await reads.minute().catch(() => {});
  }, 60_000, symbol, {enabled: active && !!symbol && chartTab === "minute"});
  usePollingFetch(async () => {await dataReadsRef.current?.book().catch(() => {});}, 5_000, symbol,
    {enabled: rightActive && !!symbol && !isIndex && rightTab === "book"});
  usePollingFetch(async () => {
    const reads = dataReadsRef.current; if (!reads?.alive) return;
    await reads.trades().catch(() => {});
  }, 10_000, symbol, {enabled: rightActive && !!symbol && !isIndex && rightTab === "trades"});
  usePollingFetch(async () => {
    const reads = dataReadsRef.current; if (!reads?.alive) return;
    await reads.flow().catch(() => {});
  }, 60_000, symbol, {enabled: active && !isIndex && chartTab === "flow"});
  usePollingFetch(async () => {
    const reads = dataReadsRef.current; if (!reads?.alive) return;
    await reads.financials().catch(() => {});
  }, null, symbol, {enabled: rightActive && !isIndex && (viewRightTab === "profile" || viewRightTab === "themes"), marketHours: false});
  // Model summaries are requested by their visible consumer, never by chart prefetch.
  usePollingFetch(loadDigest, null, symbol, {enabled: rightActive && !isIndex && rightTab === "info", marketHours: false});

  const indexDate = minutes[0]?.ts ? bjDate(minutes[0].ts) : "unknown";
  const indexKey = `${symbol}:${indexDate}`;
  const indexEnabled = active && !isIndex && chartTab === "minute" && minutes.length > 0;
  const parentIndexRef = useRef(indexQuote);
  useEffect(() => {parentIndexRef.current = indexQuote;}, [indexQuote]);
  const [indexCompletion, setIndexCompletion] = useState(0);
  const indexRecoveryRef = useRef<{
    alive: boolean; request: number; inFlight: number; failed: boolean; used: boolean;
    parentAtStart: Quote | null | undefined; candidate: Quote | null; failureAsOf: number;
  } | null>(null);
  useEffect(() => {
    if (!indexEnabled) return;
    const owner = {alive: true, request: 0, inFlight: 0, failed: false, used: false,
      parentAtStart: parentIndexRef.current, candidate: null, failureAsOf: NaN};
    indexRecoveryRef.current = owner;
    return () => {owner.alive = false; owner.candidate = null;};
  }, [indexKey, indexEnabled]);
  const indexResource = useResource(async () => {
    const owner = indexRecoveryRef.current;
    const request = owner ? ++owner.request : 0;
    const current = () => owner?.alive && owner === indexRecoveryRef.current && owner.request === request;
    if (owner) {owner.parentAtStart = parentIndexRef.current; owner.candidate = null;}
    let sourceQuote: Quote | undefined;
    let settled = false;
    const noteFailureClock = () => {
      const asOf = Date.parse(sourceQuote?.display_session?.as_of ?? "");
      if (current() && Number.isFinite(asOf)) owner!.failureAsOf = Math.max(Number.isFinite(owner!.failureAsOf) ? owner!.failureAsOf : -Infinity, asOf);
    };
    const track = <T,>(load: () => Promise<T>): Promise<T> => {
      if (owner) owner.inFlight += 1;
      return Promise.resolve().then(load).finally(() => {
        // A fail-fast pair or manual refresh can leave a sibling/old input
        // unresolved. Count each real input until its own promise settles.
        if (owner) {
          owner.inFlight -= 1;
          if (current() && owner.failed) noteFailureClock();
          if (settled && owner.alive && owner === indexRecoveryRef.current) setIndexCompletion(value => value + 1);
        }
      });
    };
    try {
      const [points] = await Promise.all([track(() => getMinuteLine("sh000001")), track(async () => {
        const overview = await getMarketOverview();
        sourceQuote = overview.indices.find(value => value.symbol === "000001" || value.symbol === "sh000001");
        return overview;
      })]);
      const value = buildIndexOverlay(points, sourceQuote, indexDate);
      if (current()) {owner!.failed = false; owner!.used = false; owner!.candidate = null; owner!.failureAsOf = NaN;}
      return value;
    } catch (error) {
      if (current()) {
        owner!.failed = true;
        noteFailureClock();
      }
      throw error;
    } finally {
      settled = true;
      if (owner?.alive && owner === indexRecoveryRef.current) setIndexCompletion(value => value + 1);
    }
  }, {key: indexKey, intervalMs: 60_000, enabled: indexEnabled});
  const {refresh: refreshIndexOverlay, error: indexReadError, data: indexReadData} = indexResource;
  useEffect(() => {
    const owner = indexRecoveryRef.current;
    if (!indexEnabled || !owner?.alive || owner.used) return;
    owner.candidate = null;
    if (!indexQuote || document.visibilityState === "hidden") return;
    if (indexQuote !== owner.parentAtStart) {
      try {
        indexQuoteReference(indexQuote, indexDate);
        const asOf = Date.parse(indexQuote.display_session?.as_of ?? "");
        if (!Number.isFinite(owner.failureAsOf) || (Number.isFinite(asOf) && asOf > owner.failureAsOf)) owner.candidate = indexQuote;
      } catch {owner.candidate = null;}
    }
    if (owner.failed && !owner.inFlight && owner.candidate) {
      owner.used = true; owner.candidate = null;
      refreshIndexOverlay();
    }
  }, [indexEnabled, indexDate, indexQuote, indexCompletion, indexReadError, indexReadData, refreshIndexOverlay]);
  const indexOverlay = indexResource.data ?? null;
  const retryRead = (kind: DetailReadKind) => {
    const reads = dataReadsRef.current; if (reads?.alive) void reads[kind](true).catch(() => {});
  };

  async function add() {
    if (isIndex) return; // 指数不入自选（sh000001 不是合法自选股代码）
    try {
      await addToWatchlist(symbol);
      setInWatchlist(true);
      onWatchlistAdded?.(symbol);
      notifyWatchlistChanged(); // 工作台左栏立即出现新自选（此前详情面板加自选不通知）
    } catch (error) { setError(`加入自选失败：${error instanceof Error ? error.message : "请重试"}`); }
  }

  // 技术评估：displayBars 随 WS 秒级 tick 变化，必须 memo——否则每 tick
  // 全量重算 MA/MACD/KDJ（评审 F1，replay-chart 同口径示范）
  const techInput = useMemo(
    () => displayBars.map((b) => ({ ts: b.ts, open: b.open ?? 0, high: b.high ?? 0, low: b.low ?? 0, close: b.close ?? 0, volume: b.volume, change_pct: b.change_pct })),
    [displayBars]
  );
  const tech = useMemo(() => analyze(techInput), [techInput]);
  const techVisible = chartTab === "kline" && techOpen && !!tech;
  const techPresence = useExitPresence(techVisible ? true : null);

  // 当前个股的模拟持仓（用于 K 线成本线）
  const costPrice = realPositions?.data?.items.find(p => p.symbol === symbol)?.avg_cost ?? null;

  // 新闻/公告 → K 线事件点（P1-8）：复用 digest 已取回的数据，零新增请求。
  // 依赖必须用内容键（日期串）而非 displayBars 引用——WS 合成每秒给最后一根 bar
  // 换新引用，若直接依赖它，eventMarks 每秒新引用 → KlineChartPro 的标记 effect
  // 每秒重跑 setMarkers + 成本线重建（2026-09-02 闪烁修复的引用稳定性收口）。
  const barDatesKey = useMemo(() => displayBars.map((b) => b.ts.slice(0, 10)).join(","), [displayBars]);
  const eventMarks = useMemo(
    () => buildEventMarks(barDatesKey ? barDatesKey.split(",") : [], anns ?? [], news ?? []),
    [barDatesKey, anns, news],
  );

  // 当日新闻 → 分时图分钟事件点：零新增请求；公告只有日期不进分时。
  // buildMinuteNewsEvents 实际只读首点 ts（取北京日期），依赖缩窄为首点 ts 值
  // （字符串，不随每秒合成变引用）——否则事件点每秒新引用 → MinuteChart
  // 创建 effect 依赖 newsEvents → 整图每秒销毁重建，闪烁回归。
  const minuteFirstTs = displayMinutes[0]?.ts ?? "";
  const minuteNewsEvents = useMemo(
    () => (minuteFirstTs ? buildMinuteNewsEvents(news ?? [], [{ ts: minuteFirstTs }]) : []),
    [news, minuteFirstTs],
  );

  // 板块标签分组：把风格/指数成分与概念题材分开，避免"大盘股/MSCI中国"混进题材
  const boardGroups = company?.board_groups;
  const boardRows: BoardRows = boardGroups
    ? [
        ["行业", boardGroups.industry ?? [], "text-zinc-600 dark:text-zinc-200"],
        ["地域", boardGroups.region ?? [], "text-zinc-600 dark:text-zinc-300"],
        ["概念题材", boardGroups.concept ?? [], "text-amber-800 dark:text-amber-300"],
        ["风格 / 指数成分", boardGroups.style_index ?? [], "text-zinc-600 dark:text-zinc-400"],
      ]
    : company?.boards?.length
      ? [["板块", company.boards, "text-zinc-600 dark:text-zinc-300"]] // 无分组数据（旧缓存）时回退扁平全量
      : [];

  const qualityView = liveQuote ? quoteQualityView({quality: liveQuote.quality, reasons: liveQuote.quality_reasons,
    symbol, market: liveQuote.market, dataTimestamp: liveQuote.data_timestamp, session: liveQuote.display_session}) : null;
  const pausedLabel = !!qualityView?.sessionLabel;
  // 首个Hub缓存快照可能尚无新订阅证券；从未见过该证券报价时不能称“恢复/保留上次”。
  // 首报仍由原REST加载/失败及Quote自身质量呈现，不把REST补价当作live恢复。
  const missingObservedQuote = active && immediateQuote === null && retainedLive !== undefined;
  const quoteIssue = missingObservedQuote ? {label: "报价待恢复", title: "本轮未返回该证券，保留上次报价；当前状态待核。"}
    : liveQuote && !hasTrustedQuoteObservation(liveQuote) && !pausedLabel ? {label: qualityView?.label === "正常" ? "时间待核" : qualityView?.label || "报价待核", title: qualityView?.title} : null;
  const quoteDiagnostics = <>
    {missingObservedQuote && <p>最近行情帧缺少该证券，保留上次报价。当前时点待核实，恢复行情后再操作。</p>}
    {liveQuote && <p>报价来源：{sourceLabel(liveQuote.source)} · 源数据时间：{dateTimeTextBJ(liveQuote.data_timestamp, {seconds: true})} · 质量 {liveQuote.quality}{qualityView?.title ? `。${qualityView.title}` : ""}</p>}
    {liveQuote && !hasTrustedQuoteObservation(liveQuote) && <p>报价质量或源时刻待核，未推进图表尾点；保留原图。</p>}
    {STREAM_ABNORMAL.includes(streamStatus) && <p>行情连接：{STREAM_STATUS_LABEL[streamStatus].text}；连接状态不代表源报价时效。</p>}
  </>;
  const klineDiagnostics = <>{quoteDiagnostics}{bars.at(-1) && <p>REST基座：{sourceLabel(bars.at(-1)?.source)} · 尾记录 {dateTimeTextBJ(bars.at(-1)?.ts, {seconds: true})} · 质量 {bars.at(-1)?.quality}</p>}</>;
  const minuteDiagnostics = <>{quoteDiagnostics}{minutes.at(-1) && <p>REST基座：{sourceLabel(minutes.at(-1)?.source)} · 源分钟 {dateTimeTextBJ(minutes.at(-1)?.ts, {seconds: true})}</p>}{indexOverlay?.points.at(-1) && <p>指数叠加截至 {dateTimeTextBJ(indexOverlay.points.at(-1)?.ts, {seconds: true})}</p>}{indexResource.error instanceof Error && <p>指数叠加：{indexResource.error.message}</p>}</>;


  return (
    <div className="stock-workspace flex min-h-0 min-w-0 flex-col gap-4">
      <div className="stock-context-brief">
      {error && (
        <div className="shrink-0 rounded-lg border border-zinc-200 dark:border-zinc-800 px-3 py-1.5 text-xs text-amber-800 dark:text-amber-300">{error}</div>
      )}

      {/* ① 紧凑行情条（指数隐藏加自选：sh000001 不是合法自选股代码）
          三态：undefined=WS/REST 均未到（同构骨架条）；null=确认拉不到；有值=QuoteStrip */}
      {quote === undefined ? (
        <div aria-hidden className="ui-card shrink-0 rounded-xl border border-zinc-200 px-4 py-1.5 dark:border-zinc-800">
          <div className="flex items-center gap-3">
            <Skeleton className="h-5 w-24" />
            <Skeleton className="h-4 w-20" />
            <Skeleton className="ml-auto hidden h-4 w-[52rem] lg:block" />
          </div>
        </div>
      ) : quote ? (
        <QuoteStrip quote={quote} inWatchlist={watchlistMembership === undefined ? inWatchlist : watchlistMembership} onAdd={() => void add()} hideWatchlist={isIndex} tradingStatus={tradingStatus} active={active} connectionStatus={streamStatus} issue={quoteIssue} diagnostics={chartTab === "kline" ? klineDiagnostics : chartTab === "minute" ? minuteDiagnostics : quoteDiagnostics}/>
      ) : (
        <div className="shrink-0 rounded-xl border border-zinc-200 dark:border-zinc-800 px-4 py-1.5 text-xs text-amber-800 dark:text-amber-300">
          行情数据暂不可用（数据源失败，稍后自动重试）
        </div>
      )}

      </div>

      {/* 共用工具行位于内容网格上方，图表与右侧核对面板保持同一顶线。 */}
      <div className="chart-toolbar flex shrink-0 flex-wrap items-center gap-2">
        <SelectionRail className="chart-modes" activeKey={chartTab} label="图表视角" indicator={false}>
        {(
          (
            [
              ["kline", "K线"],
              ["minute", "分时"],
              // 指数无个股资金流数据：隐藏资金图 tab，避免常空误导
              ["flow", "资金图"],
            ] as const
          ).filter(([key]) => !(isIndex && key === "flow")) as [ChartTab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setChartTab(key)}
            aria-pressed={chartTab === key}
            className="text-xs"
          >
            {label}
          </button>
        ))}

        </SelectionRail>
        {/* 右侧工具组：技术评估与依据同行（2026-09-04 用户反馈：技术评估
            浮层遮挡 K 线，改为工具栏内联，不再覆盖图表） */}
        <div className="chart-tools ml-auto flex flex-wrap items-center gap-2">
          {!isIndex && <button type="button" data-action="secondary" aria-haspopup="dialog" onClick={() => openDetail({kind: "attribution", title: "驱动线索", symbol})}>驱动线索</button>}
          {chartTab === "kline" && tech && (
            <div className="chart-assessment flex flex-wrap items-center gap-1.5 rounded-lg border border-zinc-200 bg-white/85 px-2 py-1 text-[11px] shadow-sm dark:border-zinc-700 dark:bg-zinc-900/85">
              <span
                className={`rounded px-1.5 py-0.5 font-medium ${
                  tech.bias === "bull"
                    ? "bg-up/15 text-up-ink dark:text-up"
                    : tech.bias === "bear"
                      ? "bg-down/15 text-down-ink dark:text-down"
                      : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
                }`}
              >
                技术评估：{tech.bias === "bull" ? "偏多" : tech.bias === "bear" ? "偏空" : "中性"}（{tech.bullCount}多/{tech.bearCount}空）
              </span>
              <button
                onClick={() => setTechOpen(!techOpen)}
                className="shrink-0 text-zinc-600 dark:text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
                title={tech.signals.map((sg) => sg.name + "：" + sg.detail).join("\n")}
                aria-expanded={techVisible}
                aria-controls={`${perspectiveId}-technical-basis`}
              >
                {techOpen ? "收起 ▴" : "依据 ▸"}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* 依据明细：正常文档流展开（打开时图表下移让位，不遮挡任何元素） */}
      {techPresence.value && tech && <div className="tech-basis-presence motion-disclosure shrink-0" data-open={techVisible} aria-hidden={!techVisible || undefined} inert={!techVisible}>
      <div className="motion-disclosure-body">
      {(
        <div id={`${perspectiveId}-technical-basis`} className="tech-signal-details shrink-0 rounded-lg border border-zinc-200 bg-white/90 p-2 text-[11px] leading-relaxed shadow-sm dark:border-zinc-700 dark:bg-zinc-900/90" role="region" tabIndex={techVisible ? 0 : -1} aria-label="技术评估依据">
          <ul className="space-y-0.5">
            {tech.signals.map((sg) => (
              <li key={sg.name} className="flex items-start gap-1.5">
                <span
                  className={`mt-1 inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
                    sg.bias === "bull" ? "bg-up" : sg.bias === "bear" ? "bg-down" : "bg-zinc-400"
                  }`}
                />
                <span>
                  <span className="font-medium text-zinc-700 dark:text-zinc-200">{sg.name}</span>
                  <span className="ml-1 text-zinc-600 dark:text-zinc-400">{sg.detail}</span>
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-[10px] text-zinc-600 dark:text-zinc-400">多因子技术信号汇总，不构成买卖建议</p>
        </div>
      )}
      </div>
      </div>}


      {/* ② 中部：左图表区 + 右盘口/逐笔（右列宽度可拖拽，--right-w 由 state 注入；
          方案 B：右列可整体收起为细条，收起后图表获得全宽） */}
      <div
        className={`detail-content-grid grid min-h-0 min-w-0 flex-1 grid-cols-1 gap-4 ${
          rightCollapsed ? "lg:grid-cols-[minmax(0,1fr),28px]" : "lg:grid-cols-[minmax(0,1fr),var(--right-w)]"
        }`}
        style={{ "--right-w": `${rightW}px` } as React.CSSProperties}
        data-right-collapsed={rightCollapsed}
      >
        <div ref={chartColumnRef} className="detail-chart-column flex min-h-0 min-w-0 flex-col gap-1.5">
          {chartTab === "kline" && (
            /* 头部标题行（股票名 · 日 K 线（前复权）+ 来源）已移除：股票名在页面
               其它位置已展示，这行纯属重复占位，去掉后纵向多出约 41px 给 K 线
               （2026-09-02 用户要求） */
            <Panel bodyClassName="overflow-hidden flex flex-col" className="detail-chart-panel min-h-0 flex-1">
              <DetailReadStatus label="K线" state={readStates.kline} onRetry={() => retryRead("kline")} diagnostics={klineDiagnostics} identity={`${symbol}:kline`} active={active}/>
              {displayBars.length > 0 ? (
                <div className="flex min-h-0 flex-1 flex-col">
                  {/* 停牌提示条：日K 缺 bar 推导，判据随附（UI 缺陷 #1） */}
                  <SuspendedNotice status={tradingStatus} />
                  {/* 技术评估条已移至图表工具栏（与依据同行，2026-09-04 用户
                      反馈浮层遮挡 K 线）：图区只留图表本身 */}
                  <div className="relative min-h-0 flex-1">
                    <KlineChartPro bars={displayBars} costPrice={costPrice} eventMarks={eventMarks} className="h-full" />
                  </div>
                </div>
              ) : (
                !readStates.kline.error && <p className="px-4 py-10 text-center text-sm text-zinc-600 dark:text-zinc-400">{readStates.kline.loaded ? "本次未返回 K 线记录。" : "等待 K 线数据…"}</p>
              )}
            </Panel>
          )}

          {chartTab === "minute" && (
            /* 头部标题行（股票名 · 当日分时 + 来源）已移除，与 K 线面板保持一致：
               股票名在页面其它位置已展示，这行纯属重复占位，去掉后纵向多出约
               41px 给分时图；两个图表 tab 也不再有「一个有头一个无头」的高度跳动
               （2026-09-02 用户要求） */
            <Panel bodyClassName="overflow-hidden flex flex-col" className="detail-chart-panel min-h-0 flex-1">
              <DetailReadStatus label="分钟" state={readStates.minute} onRetry={() => retryRead("minute")} diagnostics={minuteDiagnostics} diagnosticIssue={!!indexResource.error} identity={`${symbol}:minute`} active={active}/>
              {indexOverlay?.mode === "historical" && <p className="px-3 pb-1 text-xs text-zinc-600 dark:text-zinc-400">上证叠加 · {indexOverlay.referenceDate} · 非实时</p>}
              {!!indexResource.error && <div className="flex flex-wrap items-center gap-2 px-3 pb-1 text-xs"><p role="alert" className="text-amber-800 dark:text-amber-300">指数叠加读取失败{indexOverlay ? " · 保留结果" : ""}</p><button type="button" data-action="secondary" disabled={indexResource.pending} onClick={indexResource.refresh}>重新读取指数叠加</button></div>}
              {displayMinutes.length > 0 ? (
                <div className="relative min-h-0 flex-1">
                  {/* 停牌遮罩：分时是当日数据，停牌股当日无成交，
                      空图会被误读成"数据源挂了"，必须显式说明原因 */}
                  {isSuspended(tradingStatus) && (
                    <div className="absolute inset-0 z-10 flex items-center justify-center bg-zinc-50/70 backdrop-blur-[1px] dark:bg-zinc-950/70">
                      <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 px-4 py-3 text-center text-xs text-amber-800 dark:text-amber-300">
                        <div className="text-sm font-medium">该股当前停牌，当日无分时数据</div>
                        <div className="mt-1 opacity-80">{tradingStatus?.reason}</div>
                      </div>
                    </div>
                  )}
                <MinuteChart
                  key={symbol}
                  points={displayMinutes}
                  prevClose={quote?.prev_close ?? null}
                  referenceDate={minuteReferenceDate}
                  yesterdayVol={displayBars.length >= 2 ? (displayBars[displayBars.length - 2]?.volume ?? null) : null}
                  index={indexOverlay}
                  auction={auctionProp}
                  exactBaseline={vrBaseline}
                  newsEvents={minuteNewsEvents}
                  limitPct={priceLimitPct(symbol, quote?.name ?? null)}
                  upperPrice={quote?.limit_up_price ?? null}
                  lowerPrice={quote?.limit_down_price ?? null}
                  className="h-full"
                />
                </div>
              ) : (
                !readStates.minute.error && <p className="px-4 py-10 text-center text-sm text-zinc-600 dark:text-zinc-400">
                  {isSuspended(tradingStatus)
                    ? `该股当前停牌，当日无分时数据（${tradingStatus?.reason ?? ""}）`
                    : readStates.minute.loaded ? "本次未返回分钟记录。" : "正在读取分钟数据…"}
                </p>
              )}
            </Panel>
          )}

          {chartTab === "flow" && (
            /* 头部标题行已移除（图名 + 近 N 日口径改为图内灰标签，见 flow-chart.tsx），
               与 K 线/分时两个 tab 统一，纵向多出约 41px 给资金图（2026-09-02） */
            <Panel bodyClassName="overflow-hidden flex flex-col" className="detail-chart-panel min-h-0 flex-1">
              <DetailReadStatus label="资金" state={readStates.flow} onRetry={() => retryRead("flow")} diagnostics={quoteDiagnostics} identity={`${symbol}:flow`} active={active}/>
              {!flow || flow.flow.length === 0 ? (
                !readStates.flow.error && <p className="px-4 py-10 text-center text-sm text-zinc-600 dark:text-zinc-400">{readStates.flow.loaded ? "本次未返回资金流记录。" : "正在读取资金数据…"}</p>
              ) : (
                <div className="min-h-0 flex-1"><FlowChart flow={flow} /></div>
              )}
            </Panel>
          )}
        </div>

        {/* 右列：盘口↔逐笔 + 资讯 tabs（左缘拖拽条调宽，宽度持久化 localStorage；
            方案 B：可整体收起） */}
        <div className="detail-side-slot" data-collapsed={rightCollapsed}>
        {rightCollapsed && (
          <button
            onClick={toggleRightCollapsed}
            className="detail-side-expand flex min-h-0 w-7 items-center justify-center rounded-lg border border-zinc-200 text-zinc-600 dark:text-zinc-400 transition-colors hover:text-zinc-900 dark:border-zinc-800 dark:hover:text-zinc-100"
            title="展开右列（盘口/逐笔/资料/资讯）"
            aria-label="展开右列"
          >
            <span className="text-xs [writing-mode:vertical-lr] tracking-widest">◀ 展开右列</span>
          </button>
        )}
        {rightPresence.value !== null && (
        <div ref={rightColRef} className="detail-side-column motion-float relative flex min-h-0 min-w-0 flex-col gap-2"
          data-motion-state={rightPresence.active ? "open" : "closed"} aria-hidden={!rightPresence.active} inert={!rightPresence.active}
          style={{"--side-reading-w": `${exitingRightW}px`} as React.CSSProperties}>
          <div
            role="separator"
            tabIndex={0}
            aria-orientation="vertical"
            aria-valuemin={260}
            aria-valuemax={480}
            aria-valuenow={rightW}
            onKeyDown={(event) => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              const width = event.key === "Home" ? 260 : event.key === "End" ? 480 : Math.min(480, Math.max(260, rightW + (event.key === "ArrowLeft" ? 20 : -20)));
              setRightW(width);
              try { localStorage.setItem("ashare-right-w", String(width)); } catch {}
            }}
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
                  try { localStorage.setItem("ashare-right-w", String(w)); } catch {}
                  return w;
                });
              };
              window.addEventListener("mousemove", move);
              window.addEventListener("mouseup", up);
            }}
            className="detail-column-resizer absolute -left-2 top-0 z-10 h-full w-2 cursor-col-resize hover:bg-sky-500/25"
            title="拖拽调整右列宽度"
            aria-label="拖拽调整右列宽度"
          />

        <Panel
          title={
            viewRightTab === "book"
              ? "五档盘口"
              : viewRightTab === "trades"
                ? "逐笔成交"
                : viewRightTab === "real"
                    ? "手工记录（用户录入，非券商核验）"
                    : viewRightTab === "profile"
                      ? "公司资料"
                      : viewRightTab === "members"
                        ? "指数成分股"
                        : viewRightTab === "boards"
                          ? "板块涨幅"
                          : viewRightTab === "themes" ? "公司资料"
                            : viewRightTab === "events" ? "资讯与事件"
                              : viewRightTab === "reason" ? "涨停原因" : "资讯"
          }
          bodyClassName="flex flex-col overflow-hidden"
          source={viewRightTab === "book" ? book?.source : undefined}
          dataTimestamp={viewRightTab === "book" ? book?.data_timestamp : null}
          className="min-h-0 flex-1 overflow-hidden"
          // D-3：本 Panel 是**同一实例换内容**（viewRightTab 决定 title/children）。
          // 2026-09-13 起 title 变化已由默认行为覆盖（label 变 ⇒ 清错误态）；
          // resetKey 保留为显式声明——行为等价，且不依赖 title 恰为字符串形态。
          // 判据与反例见 panel-boundary.test.tsx「Panel.resetKey（D-3）」组。
          resetKey={viewRightTab}
          extra={
            /* 收起按钮走 Panel 头部 extra 槽位（正常文档流）——原 absolute 悬浮
               定位正好压在头部右侧「数据来源/数据时间」徽标上（2026-09-04 用户反馈） */
            <button
              onClick={toggleRightCollapsed}
              className="rounded border border-zinc-200 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:text-zinc-400 transition-colors hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100"
              title="收起右列，图表获得全宽"
              aria-label="收起右列"
            >
              ▶ 收起
            </button>
          }
        >
          <div className="detail-perspective-tabs" style={{display: "grid", gridTemplateColumns: `repeat(${isIndex ? 2 : qualification.eligible ? 4 : 3}, minmax(0, 1fr))`}} data-index={isIndex} role="tablist" aria-label="核对视角"
            onKeyDown={(event) => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
              const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("button"));
              const current = buttons.indexOf(event.target as HTMLButtonElement);
              if (current < 0) return;
              event.preventDefault();
              const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
                : (current + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
              buttons[next].focus();
              buttons[next].click();
            }}>
            {rightTabsFor(isIndex, qualification.eligible).map(([key, label]) => (
              <button key={key} type="button" role="tab" aria-label={key === "themes" ? "题材归属" : key === "events" ? "相关事件" : key === "reason" ? "涨停原因" : label} style={parentRightTab === key ? {color: "var(--ui-ink)", borderBottomColor: "var(--ui-accent)", fontWeight: 600} : undefined} id={`${perspectiveId}-${key}`} aria-controls={`${perspectiveId}-panel`} aria-selected={parentRightTab === key} tabIndex={parentRightTab === key ? 0 : -1}
                onClick={() => { const next = parentRightTab === key ? viewRightTab : key; if (onRightTabChange) onRightTabChange(next); else setRightTab(next); }}>
                {key === "trade" ? "模拟" : key === "real" ? "记账" : label}
              </button>
            ))}
          </div>
          <div id={`${perspectiveId}-panel`} role="tabpanel" aria-labelledby={`${perspectiveId}-${parentRightTab}`} className="min-h-0 flex-1 overflow-auto">
          {viewRightTab === "members" && <IndexMembersPanel key={symbol} symbol={symbol} active={rightActive} onSelect={selected => {if (onSymbolChange) onSymbolChange(selected); else openSymbolDetail({symbol: selected, rightTab: "book"});}} />}
          {viewRightTab === "boards" && <BoardRankPanel className="h-full" active={rightActive} />}
          {(viewRightTab === "info" || viewRightTab === "events") && <div role="tablist" aria-label="资讯内容" className="flex gap-1 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800" onKeyDown={event => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("button"));
            const current = buttons.indexOf(event.target as HTMLButtonElement); if (current < 0) return;
            event.preventDefault();
            const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
            buttons[next].focus(); buttons[next].click();
          }}>
            {([["info", "新闻公告"], ["events", "相关事件"]] as const).map(([key, label]) => <button key={key} type="button" role="tab" tabIndex={viewRightTab === key ? 0 : -1} aria-selected={viewRightTab === key} aria-controls={`${perspectiveId}-information`} data-action={viewRightTab === key ? "secondary" : "quiet"} onClick={() => {if (onRightTabChange) onRightTabChange(key); else setRightTab(key);}}>{label}</button>)}
          </div>}
          {viewRightTab === "events" && <div id={`${perspectiveId}-information`} className="px-3 py-2"><StockEventsRow symbol={symbol} active={rightActive} /><TradingAgentsResearchPanel key={symbol} symbol={symbol} active={rightActive}/></div>}
          {viewRightTab === "reason" && <div className="px-3 py-2"><LimitUpReasonPanel symbol={symbol} quote={quote} active={rightActive} sharedResource={reasonResource} standalone /></div>}
          {rightTab === "reason" && !qualification.eligible && (!!reasonResource.error || reasonResource.data?.coverage && Object.values(reasonResource.data.coverage).some(side => side.state !== "success")) && <div className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs"><p role="alert" className="text-amber-800 dark:text-amber-300">涨停与炸板资格待恢复，暂未展示原因。</p><button type="button" data-action="secondary" onClick={reasonResource.refresh}>重试资格</button></div>}
          {viewRightTab === "real" && (
            <RealPositionPanel symbol={symbol} currentPrice={quote?.price ?? null} currentName={quote?.name ?? null} realPositions={realPositions} active={rightActive} className="h-full" />
          )}
          {(viewRightTab === "profile" || viewRightTab === "themes") && <ProfilePanel themes={stockThemes} themesState={{pending: themesResource.pending, loaded: themesResource.data !== undefined, error: themesResource.error ? "题材读取失败" : null, readAt: null}} onThemesRetry={themesResource.refresh} boardRows={boardRows} company={company} fins={fins} readState={readStates.financials} onRetry={() => retryRead("financials")} readIdentity={`${symbol}:financials`} active={rightActive}/>}

          {viewRightTab === "info" && <div id={`${perspectiveId}-information`}><InfoPanel key={symbol} anns={anns} news={news} annError={annError} newsError={newsError} loading={digestLoading} onRetry={() => { void loadDigest(); }} /></div>}

          {/* Only the two market-print perspectives own this consumer. */}
          {(viewRightTab === "book" || viewRightTab === "trades") && <BookTradesView book={book} trades={trades} showBook={viewRightTab === "book"} readState={readStates[viewRightTab]} onRetry={() => retryRead(viewRightTab)} emptyReason={tradeEmptyReason} tradesSource={tradeSource} readIdentity={`${symbol}:${viewRightTab}`} active={rightActive}/>}
          </div>
        </Panel>
        </div>
        )}
        </div>
      </div>
    </div>
  );
}, (prev, next) => {
  // 手机列表态保留详情与表单；报价 tick 不更新隐藏 Canvas。
  const hidden = prev.active === false && next.active === false;
  const keys = new Set([...Object.keys(prev), ...Object.keys(next)] as (keyof typeof prev)[]);
  return [...keys].every(key => hidden && (key === "liveQuote" || key === "streamStatus")
    || Object.is(prev[key], next[key]));
});

async function getWatchlistSymbols(signal: AbortSignal): Promise<string[]> {
  return (await getWatchlist({signal})).map((i) => i.symbol);
}
