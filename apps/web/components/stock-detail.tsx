"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { MinuteChart } from "@/components/minute-chart";
import { KlineChartPro } from "@/components/kline-chart-pro";
import { priceLimitPct } from "@/lib/price-limit";
import { Panel } from "@/components/panel";
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { useQuoteStream, STREAM_STATUS_LABEL, STREAM_ABNORMAL } from "@/hooks/use-quote-stream";
import { analyze } from "@/lib/technical-analysis";
import { buildEventMarks, buildMinuteNewsEvents } from "@/lib/event-markers";
import { mergeQuoteIntoBars, mergeQuoteIntoMinutes } from "@/lib/kline-live";
import { isIndexSymbol } from "@/lib/api";
import { notifyWatchlistChanged } from "@/lib/watchlist-sync";
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
  getKlinePayload,
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
import type { Kline, OrderBook, Quote, Trade, TradingStatusInfo } from "@/types/market";
import { QuoteStrip } from "@/components/detail/quote-strip";
import { TradePanel, type PaperBundle } from "@/components/detail/trade-panel";
import { ProfilePanel, type CompanyProfile, type FinRow, type BoardRows } from "@/components/detail/profile-panel";
import { InfoPanel, type InfoItem } from "@/components/detail/info-panel";
import { BookTradesView } from "@/components/detail/book-trades-view";
import { Skeleton } from "@/components/ui/loading";
import { ReplayChart } from "@/components/replay-chart";
import { FlowChart, type CapitalFlow } from "@/components/detail/flow-chart";
import { SpeedPanel } from "@/components/detail/speed-panel";
import { BoardRankPanel } from "@/components/detail/board-rank-panel";
import { RealPositionPanel } from "@/components/detail/real-position-panel";
import { SuspendedBadge, SuspendedNotice, isSuspended } from "@/components/detail/suspended-badge";

/**
 * 次屏数据调度（评审 O1，2026-09-01）：切股首屏只需 K 线 + 盘口（各自默认 tab），
 * 其余数据推到浏览器空闲时拉取——首屏不再被最慢的财务/新闻请求拖住（东财慢时
 * 2-4s 白屏）。requestIdleCallback 不可用时退化为 900ms setTimeout。
 */
function scheduleIdle(fn: () => void): () => void {
  const w = window as Window & {
    requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
    cancelIdleCallback?: (id: number) => void;
  };
  if (typeof w.requestIdleCallback === "function") {
    const id = w.requestIdleCallback(fn, { timeout: 2500 });
    return () => w.cancelIdleCallback?.(id);
  }
  const t = window.setTimeout(fn, 900);
  return () => window.clearTimeout(t);
}

/** 个股详情终端 v3（工作台右栏 / 个股页共用）：
 * 顶部紧凑行情条 → 中部 [左：图表区(K线/分时/资金图) | 右：盘口↔逐笔] → 右列：盘口↔逐笔 + 财务摘要。龙虎榜见独立页面。
 * K线带龙虎榜日标记与金叉死叉技术信号；滚动只存在于表格/列表容器内部。 */
export type ChartTab = "kline" | "minute" | "flow";
export type RightTab = "book" | "trades" | "trade" | "real" | "profile" | "info" | "speed" | "boards";

/**
 * 助手「一键跳转」的落点参数（2026-09-06，docs/assistant-optimization-plan.md §1.3）：
 * 工作台 URL 的 ?ct=（图表区 tab）与 ?rt=（右栏 tab）由调用方（workbench 页）解析后
 * 传进来。这里只做**初值 + 跟随变化**：内部仍是 state，用户手动切 tab 不回写 URL
 * （避免每次点击都产生历史/路由噪音），但外部深链进来必须生效——
 * 面板随 symbol 走 key 重挂载，同一只股票内换 tab 则由 effect 跟随。
 */
export interface StockDetailTabs {
  chartTab?: ChartTab;
  rightTab?: RightTab;
}

export function StockDetailPanel({
  symbol,
  chartTab: chartTabInit,
  rightTab: rightTabInit,
}: { symbol: string } & StockDetailTabs) {
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
    if (rightTabInit) setRightTab(rightTabInit);
  }
  // 布局 #1：右列宽度可拖拽（localStorage 持久化；260-480px 防极限）
  const [rightW, setRightW] = useState(300);
  const rightColRef = useRef<HTMLDivElement>(null);
  // 模拟交易数据刷新（2026-09-01 简化：原 paperChanged 全局事件改为 ref 直调——
  // trade-form/trade-panel 都在本组件子树内，props 回调 + ref 即可，无需全局广播）
  const loadPaperRef = useRef<() => void>(() => {});
  // 布局方案 B（2026-09-04 用户拍板）：右列可整体收起，收起后 K 线/分时获得全宽
  const [rightCollapsed, setRightCollapsed] = useState(false);
  useEffect(() => {
    // localStorage 恢复必须在 effect 里：渲染期读会把值带进首次 commit，
    // 与 SSR 输出产生 hydration mismatch（宽度类 inline style 必比对）。
    const saved = Number(localStorage.getItem("ashare-right-w"));
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (saved >= 260 && saved <= 480) setRightW(saved);
    // 右列收起状态同样持久化（字符串比较，避免 hydration 差异）
    if (localStorage.getItem("ashare-right-collapsed") === "1") setRightCollapsed(true);
  }, []);
  const toggleRightCollapsed = () => {
    setRightCollapsed((v) => {
      localStorage.setItem("ashare-right-collapsed", v ? "0" : "1");
      return !v;
    });
  };
  // 历史回放（Phase 6 收官）：K 线页签内切换回放模式
  const [replayMode, setReplayMode] = useState(false);
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
  // fins 三态：undefined=加载中（ProfilePanel 骨架），null=确认无/拉取失败
  const [fins, setFins] = useState<FinRow[] | null | undefined>(undefined);
  const [anns, setAnns] = useState<InfoItem[] | null>(null);
  const [news, setNews] = useState<InfoItem[] | null>(null);
  // digest 数据源三态：非 null = 该侧降级（InfoPanel 显式提示，非静默空列表）
  const [annError, setAnnError] = useState<string | null>(null);
  const [newsError, setNewsError] = useState<string | null>(null);
  const [company, setCompany] = useState<CompanyProfile | null>(null);
  const [fills, setFills] = useState<PaperFill[]>([]);
  const [resetBusy, setResetBusy] = useState(false);
  const [paper, setPaper] = useState<PaperBundle | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [inWatchlist, setInWatchlist] = useState(false);
  // quote 三态：undefined=WS 快照/REST 均未到达（行情条渲染骨架）；null=两侧都
  // 确认拉不到（渲染"行情不可用"）；有值=正常。WS 合并与 REST 轮询共用该状态。
  const [quote, setQuote] = useState<Quote | null | undefined>(undefined);
  const [auction, setAuction] = useState<AuctionData | null>(null);
  // MinuteChart 的 auction prop 引用稳定化：行内对象每渲染必新引用，而分时图
  // 创建 effect 依赖 auction——不 memo 会导致整图每秒销毁重建（闪烁回归）。
  const auctionProp = useMemo(
    () => (auction?.auction_price ? { price: auction.auction_price, pct: auction.auction_pct } : null),
    [auction],
  );
  const [vrBaseline, setVrBaseline] = useState<number[] | null>(null);
  // 指数 symbol（sh000001 等）：禁用个股专属面板（加自选/交易/资料/资金图）
  const isIndex = isIndexSymbol(symbol);
  // 指数下个股专属 tab 不可用：残留的 flow/trade/real/profile 选中态强制归位
  //（workbench 切股走 key 重挂载不会残留，这里是 /stock/[symbol] 等复用方的防御）。
  // 渲染期调整（adjust-state 模式）：prevIsIndex 初始 false，挂载即指数时同样归位。
  const [prevIsIndex, setPrevIsIndex] = useState(false);
  if (isIndex !== prevIsIndex) {
    setPrevIsIndex(isIndex);
    if (isIndex) {
      setChartTab((t) => (t === "flow" ? "kline" : t));
      setRightTab((t) => (t === "trade" || t === "real" || t === "profile" || t === "trades" ? "book" : t));
    }
  }

  // throttleMs=3000（审查 F5/R6）：详情面板价格闪烁与列表侧同口径——WS 仍 1Hz
  // 全量接收（内部不丢数据），对外状态 3s 应用一次。1Hz PriceFlash 是每秒红绿
  // 闪的体感噪音源（列表修过、详情漏了的不对称）。
  const { quotes, status: streamStatus } = useQuoteStream([symbol], { throttleMs: 3000 });
  // connecting 防抖（2026-09-09）：WS 秒连场景下"● 连接中"只闪现毫秒级——延迟 2s
  // 才认为真异常；期间离开 connecting 态则取消。
  const [connectingDebounced, setConnectingDebounced] = useState(false);
  useEffect(() => {
    if (streamStatus !== "connecting") {
      setConnectingDebounced(false);
      return;
    }
    const t = setTimeout(() => setConnectingDebounced(true), 2000);
    return () => clearTimeout(t);
  }, [streamStatus]);
  // WS 推送 → 渲染期合并进 quote（adjust-state 模式）：live 引用每拍必变，
  // 哨兵 appliedLive 保证同一帧只合并一次，语义与原 effect 版完全等价。
  const live = quotes[symbol];
  const [appliedLive, setAppliedLive] = useState<Quote | null>(null);
  if (live && live !== appliedLive) {
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
      } catch {
        // REST 失败且 WS 也还没推来快照 → 确认行情拉不到（渲染"不可用"）；
        // 已有数据（WS 在推）保持不变——30s 轮询偶发失败不该抹掉实时价
        setQuote((prev) => (prev === undefined ? null : prev));
      }
    };
    void pull();
    const t = setInterval(pull, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [symbol]);

  useEffect(() => {
    // 模拟账户数据只服务「模拟交易」页签（O2）：不在该页签时不轮询不拉取——
    // 切入页签时 effect 重跑立即 load 一次；挂单成交等状态变化由
    // paper-changed 事件兜底（下单/撤单/重置都会派发）。
    // 轮询 10s → 30s（费率预估与持仓盈亏对实时性不敏感，原 10s 属过密）。
    if (!symbol || rightTab !== "trade") return;
    let alive = true;
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
      } catch {
        if (alive) setPaper(null); // 拉取失败=确认不可用（渲染提示），与"加载中"分离
      }
    };
    loadPaperRef.current = loadPaper;
    void loadPaper();
    const t = setInterval(loadPaper, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [symbol, rightTab]);

  useEffect(() => {
    let alive = true;
    getWatchlistSymbols().then((list) => alive && setInWatchlist(list.includes(symbol)));
    return () => {
      alive = false;
    };
  }, [symbol]);

  // 大盘叠加（分时图 P1）：上证分时 + 昨收。当日不变，随切股在次屏 idle 拉取
  // （评审 O1：不占首屏关键路径；指数详情页不叠加——自己叠自己纯噪音）。
  const [indexOverlay, setIndexOverlay] = useState<{ points: MinutePoint[]; prevClose: number } | null>(null);

  // 题材归属（L4 联动）：官方成分 + 当日涨停归因，chip 点击跳题材看板聚焦。
  // 独立请求 + 静默失败：归属缺失只影响这一行，不拖垮详情页。
  const [stockThemes, setStockThemes] = useState<StockThemes | null>(null);
  // 切股清空三件套（渲染期 adjust-state：symbol 变化时同步重置，防上一只残留）——
  // fills 的按页签拉取、自选/题材的各自 fetch effect 保持不变，只把「清空」前移到渲染期。
  const [prevResetSymbol, setPrevResetSymbol] = useState<string | null>(null);
  if (symbol !== prevResetSymbol) {
    setPrevResetSymbol(symbol);
    setFills([]);
    setInWatchlist(false);
    setStockThemes(null);
  }
  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    getStockThemes(symbol)
      .then((t) => alive && setStockThemes(t))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [symbol]);

  // ---- 切股数据分级加载（2026-09-01 评审 O1）----
  // 此前切股一次并发 ~14 个请求（Promise.all 8 个 + 竞价/题材/大盘叠加/paper 等），
  // 首屏 K 线被最慢的财务/新闻拖住（东财慢时 2-4s 白屏）。按可见性分级：
  //   首屏（立即）：K 线（左图默认 tab）+ 五档盘口（右列默认 tab）+ 题材 chips（首屏行）
  //   次屏（idle ≤2.5s）：分时/逐笔/资金流/财务/公司/新闻/竞价/大盘叠加——
  //     切到对应 tab 时通常已就绪；不在首屏路径上，晚到不阻塞交互。
  // key={activeSymbol} 重挂载下 symbol 在实例内不变，原"切股清空"是死代码；
  // 各回调的 alive 检查负责防串股（切股后晚到的旧股结果直接丢弃）。
  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    const skipStockOnly = isIndexSymbol(symbol); // 指数无盘口/逐笔/资金流，跳过省失败请求
    // 切股无需手动清空 tradingStatus：page.tsx 用 key={activeSymbol} 挂载，
    // 切股即重挂载、state 复位（同上方的 stockThemes 注释）
    void getKlinePayload(symbol, "1d", 120)
      .then((p) => {
        if (!alive) return;
        setBars(p.bars);
        setTradingStatus(p.trading_status);
      })
      .catch(() => {});
    if (!skipStockOnly) {
      getOrderBook(symbol)
        .then((ob) => alive && setBook(ob))
        // 首拉失败 = 确认拉不到（切股窗口结束），渲染"不可用"文案而非永远骨架；
        // 盘中 5s 轮询的失败仍静默保留上次快照（下方轮询 effect，有意设计）
        .catch(() => alive && setBook(null));
    } else {
      // 指数确认无盘口/逐笔数据源：显式置"确认空"，不留加载中转圈
      if (alive) {
        setBook(null);
        setTrades([]);
      }
    }
    const cancelIdle = scheduleIdle(() => {
      if (!alive) return;
      getMinuteLineWithBaseline(symbol)
        .then((r) => {
          if (!alive) return;
          setVrBaseline(r.vr_baseline_5m);
          setMinutes(r.points);
        })
        .catch(() => {});
      if (!skipStockOnly) {
        getTrades(symbol, 30)
          .then((tr) => alive && setTrades(tr))
          .catch(() => alive && setTrades([])); // 首拉失败=确认无，不再闪"暂无逐笔"当加载态
        getCapitalFlow<CapitalFlow>(symbol, 30)
          .then((cf) => alive && cf && setFlow(cf))
          .catch(() => {});
        // 大盘叠加（分时图）：上证分时 + 昨收（当日不变）
        getMinuteLine("sh000001")
          .catch(() => [] as MinutePoint[])
          .then(async (idxPoints) => {
            const overview = await getMarketOverview().catch(() => null);
            const sh = overview?.indices.find((i) => i.symbol === "000001");
            if (alive && idxPoints.length > 0 && sh?.prev_close) {
              setIndexOverlay({ points: idxPoints, prevClose: sh.prev_close });
            }
          });
        // 集合竞价（09:25 终态）：分时图竞价点 + 角标（当日不变）
        getAuction(symbol)
          .then((a) => alive && setAuction(a))
          .catch(() => {});
      }
      getFinancials<FinRow>(symbol, 8)
        .then((f) => alive && setFins(f)) // f=null = 后端确认无财报 → "确认空"态
        .catch(() => alive && setFins(null));
      getCompanyProfile<CompanyProfile>(symbol)
        .then((cp) => alive && cp && setCompany(cp))
        .catch(() => {});
      // 资讯走摘要端点：一次拿回公告+新闻 + 重要度/情绪/事实摘要；
      // 单侧数据源失败时后端降级（error 字段非 null），前端透出提示而非静默空
      getNewsDigest(symbol, 8)
        .then((d) => {
          if (!alive) return;
          setAnns(d.announcements as unknown as InfoItem[]);
          setNews(d.news as unknown as InfoItem[]);
          setAnnError(d.announcements_error);
          setNewsError(d.news_error);
        })
        .catch(() => {});
    });
    return () => {
      alive = false;
      cancelIdle();
    };
  }, [symbol]);

  // ---- 盘中实时刷新（2026-08-31 修复"K线/分时/盘口/逐笔拉一次就死"）----
  // 此前所有图表数据只在切股时拉取一次，盘中永不更新——K 线最后一根
  // 停在进场时刻，跟不上行情。频率依据：K 线/分时数据源粒度是分钟级，
  // 60s 校准已超过够用；盘口变化最快对齐 WS 降级轮询的 5s；逐笔走东财
  // （WAF 限流）10s 保守。实时性主力是下面的 WS 合成，不靠轮询。
  // ① K 线当日 bar 的秒级合成：渲染期派生（外部状态订阅的官方推荐模式），
  //    WS quote 更新 → 最后一根 bar 实时跟进；价格未动时 mergeQuoteIntoBars
  //    返回 null → 引用不变 → 下游图表不重渲染。
  const liveQuote = quotes[symbol];
  const displayBars = useMemo(() => mergeQuoteIntoBars(bars, liveQuote) ?? bars, [bars, liveQuote]);
  // ①' 分时右端点秒级合成：同 K 线思路，WS quote 跟进最后一根分钟点
  //    （价格+累计量），与列表/K线保持同一 1s 节奏，不再干等 60s REST 校准
  const displayMinutes = useMemo(() => mergeQuoteIntoMinutes(minutes, liveQuote) ?? minutes, [minutes, liveQuote]);
  // ② 图表 60s REST 校准（评审 O2：绑定 chartTab——不在 K线/分时 tab 时不校准，
  //    切入 tab 时 effect 重跑先立即拉一次再启轮询）
  useEffect(() => {
    if (!symbol) return;
    const timers: ReturnType<typeof setInterval>[] = [];
    if (chartTab === "kline") {
      const pull = () => {
        void getKlinePayload(symbol, "1d", 120)
          .then((p) => {
            if (p.bars.length === 0) return;
            setBars(p.bars);
            setTradingStatus(p.trading_status);
          })
          .catch(() => {});
      };
      void pull();
      timers.push(setInterval(pull, 60_000));
    }
    if (chartTab === "minute") {
      const pull = () => {
        void getMinuteLineWithBaseline(symbol)
          .then((r) => {
            setMinutes(r.points);
            // 基线内容守卫：数组内容没变就保留旧引用——vrBaseline 是
            // MinuteChart 创建 effect 的依赖，每 60s 换新引用会把整图
            // 销毁重建一次（悬停中十字线丢失 + 图表闪跳）。
            setVrBaseline((prev) => {
              const next = r.vr_baseline_5m;
              if (prev && next && prev.length === next.length && prev.every((v, i) => v === next[i])) return prev;
              return next;
            });
          })
          .catch(() => {});
      };
      void pull();
      timers.push(setInterval(pull, 60_000));
    }
    return () => timers.forEach((t) => clearInterval(t));
  }, [symbol, chartTab]);

  // ④ 盘口 5s / 逐笔 10s 轮询（评审 O2：绑定 rightTab——各自页签激活才轮询，
  //    切入时立即拉一次；失败静默保留上一次快照；指数无此数据源跳过）
  useEffect(() => {
    if (!symbol || isIndex) return;
    const timers: ReturnType<typeof setInterval>[] = [];
    if (rightTab === "book") {
      const pull = () => {
        void getOrderBook(symbol).then(setBook).catch(() => {});
      };
      void pull();
      timers.push(setInterval(pull, 5_000));
    }
    if (rightTab === "trades") {
      const pull = () => {
        void getTrades(symbol, 30).then(setTrades).catch(() => {});
      };
      void pull();
      timers.push(setInterval(pull, 10_000));
    }
    return () => timers.forEach((t) => clearInterval(t));
  }, [symbol, isIndex, rightTab]);

  async function add() {
    if (isIndex) return; // 指数不入自选（sh000001 不是合法自选股代码）
    try {
      await addToWatchlist(symbol);
      setInWatchlist(true);
      notifyWatchlistChanged(); // 工作台左栏立即出现新自选（此前详情面板加自选不通知）
    } catch {}
  }

  // 技术评估：displayBars 随 WS 秒级 tick 变化，必须 memo——否则每 tick
  // 全量重算 MA/MACD/KDJ（评审 F1，replay-chart 同口径示范）
  const techInput = useMemo(
    () => displayBars.map((b) => ({ ts: b.ts, open: b.open ?? 0, high: b.high ?? 0, low: b.low ?? 0, close: b.close ?? 0, volume: b.volume, change_pct: b.change_pct })),
    [displayBars]
  );
  const tech = useMemo(() => analyze(techInput), [techInput]);

  // 当前个股的模拟持仓（用于 K 线成本线）
  const myPosition = paper?.positions.find((p) => p.symbol === symbol) ?? null;
  const costPrice = myPosition && myPosition.quantity > 0 ? myPosition.cost_price : null;

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
        ["地域", boardGroups.region ?? [], "text-zinc-500 dark:text-zinc-300"],
        ["概念题材", boardGroups.concept ?? [], "text-amber-600 dark:text-amber-300"],
        ["风格 / 指数成分", boardGroups.style_index ?? [], "text-zinc-500"],
      ]
    : company?.boards?.length
      ? [["板块", company.boards, "text-zinc-500 dark:text-zinc-300"]] // 无分组数据（旧缓存）时回退扁平全量
      : [];

  async function handleResetAccount() {
    if (!window.confirm("重置模拟账户？当前全部持仓、挂单与成交记录将清空，资金回到初始额度。此操作不可撤销。")) return;
    setResetBusy(true);
    try {
      await resetPaperAccount();
      loadPaperRef.current();
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

      {/* ① 紧凑行情条（指数隐藏加自选：sh000001 不是合法自选股代码）
          三态：undefined=WS/REST 均未到（同构骨架条）；null=确认拉不到；有值=QuoteStrip */}
      {quote === undefined ? (
        <div aria-hidden className="shrink-0 rounded-xl border border-zinc-200 px-4 py-1.5 dark:border-zinc-800">
          <div className="flex items-center gap-3">
            <Skeleton className="h-5 w-24" />
            <Skeleton className="h-4 w-20" />
            <Skeleton className="ml-auto hidden h-4 w-[52rem] lg:block" />
          </div>
        </div>
      ) : quote ? (
        <QuoteStrip quote={quote} inWatchlist={inWatchlist} onAdd={() => void add()} hideWatchlist={isIndex} tradingStatus={tradingStatus} />
      ) : (
        <div className="shrink-0 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-1.5 text-xs text-amber-600 dark:text-amber-300">
          行情数据暂不可用（数据源失败，稍后自动重试）
        </div>
      )}

      {/* ①¼ 实时连接状态：只在异常态显示。2026-09-07 去掉"● 休市"常驻；2026-09-08
          用户反馈"● WS 实时推送"同样不该常驻（正常态都不留痕）——条件从
          `!== "closed"` 收紧为异常态白名单 STREAM_ABNORMAL。
          2026-09-09 用户反馈：connecting 只持续毫秒级（WS 秒连），每次进页都在
          题材归属上方闪一下"● 连接中"——加 2s 防抖：正常快速连接不显示，
          真卡住（>2s 仍在 connecting）才提示。polling/stale/error 立即显示。 */}
      {STREAM_ABNORMAL.includes(streamStatus) &&
        (streamStatus !== "connecting" || connectingDebounced) && (
        <div className="shrink-0 text-[10px]">
          <span className={STREAM_STATUS_LABEL[streamStatus].cls} title="行情连接状态（WebSocket 主通道，断线自动降级 REST 轮询）">
            ● {STREAM_STATUS_LABEL[streamStatus].text}
          </span>
        </div>
      )}

      {/* ①½ 题材归属 chips（官方成分 / 涨停归因双源）→ 题材看板聚焦 */}
      <ThemeChipsRow themes={stockThemes} />

      {/* ①¾ 相关事件（E2/L9）：方向题材命中归属 或 事件源自该股 */}
      <StockEventsRow symbol={symbol} />

      {/* ② 中部：左图表区 + 右盘口/逐笔（右列宽度可拖拽，--right-w 由 state 注入；
          方案 B：右列可整体收起为细条，收起后图表获得全宽） */}
      <div
        className={`grid min-h-0 min-w-0 flex-1 grid-cols-1 gap-2 ${
          rightCollapsed ? "lg:grid-cols-[minmax(0,1fr),28px]" : "lg:grid-cols-[minmax(0,1fr),var(--right-w)]"
        }`}
        style={{ "--right-w": `${rightW}px` } as React.CSSProperties}
      >
        <div className="flex min-h-0 min-w-0 flex-col gap-1.5">
          <div className="flex shrink-0 gap-1">
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
                className={`rounded-md px-2.5 py-1 text-xs ${chartTab === key ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"}`}
              >
                {label}
              </button>
            ))}

            {/* 右侧工具组：技术评估 + 历史回放同行（2026-09-04 用户反馈：技术评估
                浮层遮挡 K 线，改为工具栏内联，不再覆盖图表） */}
            <div className="ml-auto flex items-center gap-1">
              {chartTab === "kline" && !replayMode && tech && (
                <div className="flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white/85 px-2 py-1 text-[11px] shadow-sm dark:border-zinc-700 dark:bg-zinc-900/85">
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
                  <button
                    onClick={() => setTechOpen(!techOpen)}
                    className="shrink-0 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
                    title={tech.signals.map((sg) => sg.name + "：" + sg.detail).join("\n")}
                  >
                    {techOpen ? "收起 ▴" : "依据 ▸"}
                  </button>
                </div>
              )}
              {chartTab === "kline" && !replayMode && displayBars.length >= 60 && (
                <button
                  onClick={() => setReplayMode(true)}
                  className="rounded border border-sky-500/50 px-2 py-0.5 text-xs text-sky-400 hover:bg-sky-500/10"
                  title="按日逐根推进 K 线，回放历史买卖点与成交（需要 ≥60 根日 K）"
                >
                  ▶ 历史回放
                </button>
              )}
            </div>
          </div>

          {/* 依据明细：正常文档流展开（打开时图表下移让位，不遮挡任何元素） */}
          {chartTab === "kline" && !replayMode && techOpen && tech && (
            <div className="shrink-0 rounded-lg border border-zinc-200 bg-white/90 p-2 text-[11px] leading-relaxed shadow-sm dark:border-zinc-700 dark:bg-zinc-900/90">
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
                      <span className="ml-1 text-zinc-400">{sg.detail}</span>
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-1.5 text-[10px] text-zinc-400">多因子技术信号汇总，不构成买卖建议</p>
            </div>
          )}

          {chartTab === "kline" && (
            /* 头部标题行（股票名 · 日 K 线（前复权）+ 来源）已移除：股票名在页面
               其它位置已展示，这行纯属重复占位，去掉后纵向多出约 41px 给 K 线
               （2026-09-02 用户要求） */
            <Panel bodyClassName="overflow-hidden" className="min-h-0 flex-1">
              {displayBars.length > 0 ? (
                replayMode ? (
                  <ReplayChart bars={bars} fills={fills} onExit={() => setReplayMode(false)} />
                ) : (
                <div className="flex h-full min-h-0 flex-col">
                  {/* 停牌提示条：日K 缺 bar 推导，判据随附（UI 缺陷 #1） */}
                  <SuspendedNotice status={tradingStatus} />
                  {/* 技术评估条已移至图表工具栏（与历史回放同行，2026-09-04 用户
                      反馈浮层遮挡 K 线）：图区只留图表本身 */}
                  <div className="relative min-h-0 flex-1">
                    <KlineChartPro bars={displayBars} tradeMarks={fills} costPrice={costPrice} eventMarks={eventMarks} className="h-full" />
                  </div>
                </div>
                )
              ) : (
                <p className="px-4 py-10 text-center text-sm text-zinc-400">等待 K 线数据…</p>
              )}
            </Panel>
          )}

          {chartTab === "minute" && (
            /* 头部标题行（股票名 · 当日分时 + 来源）已移除，与 K 线面板保持一致：
               股票名在页面其它位置已展示，这行纯属重复占位，去掉后纵向多出约
               41px 给分时图；两个图表 tab 也不再有「一个有头一个无头」的高度跳动
               （2026-09-02 用户要求） */
            <Panel bodyClassName="overflow-hidden" className="min-h-0 flex-1">
              {minutes.length > 0 ? (
                <div className="relative h-full">
                  {/* 停牌遮罩：分时是当日数据，停牌股当日无成交，
                      空图会被误读成"数据源挂了"，必须显式说明原因 */}
                  {isSuspended(tradingStatus) && (
                    <div className="absolute inset-0 z-10 flex items-center justify-center bg-zinc-50/70 backdrop-blur-[1px] dark:bg-zinc-950/70">
                      <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-center text-xs text-amber-600 dark:text-amber-300">
                        <div className="text-sm font-medium">该股当前停牌，当日无分时数据</div>
                        <div className="mt-1 opacity-80">{tradingStatus?.reason}</div>
                      </div>
                    </div>
                  )}
                <MinuteChart
                  points={displayMinutes}
                  prevClose={quote?.prev_close ?? null}
                  yesterdayVol={displayBars.length >= 2 ? (displayBars[displayBars.length - 2]?.volume ?? null) : null}
                  index={indexOverlay}
                  auction={auctionProp}
                  exactBaseline={vrBaseline}
                  newsEvents={minuteNewsEvents}
                  limitPct={priceLimitPct(symbol, quote?.name ?? null)}
                  className="h-full"
                />
                </div>
              ) : (
                <p className="px-4 py-10 text-center text-sm text-zinc-400">
                  {isSuspended(tradingStatus)
                    ? `该股当前停牌，当日无分时数据（${tradingStatus?.reason ?? ""}）`
                    : "暂无分时数据"}
                </p>
              )}
            </Panel>
          )}

          {chartTab === "flow" && (
            /* 头部标题行已移除（图名 + 近 N 日口径改为图内灰标签，见 flow-chart.tsx），
               与 K 线/分时两个 tab 统一，纵向多出约 41px 给资金图（2026-09-02） */
            <Panel bodyClassName="overflow-hidden" className="min-h-0 flex-1">
              {!flow || flow.flow.length === 0 ? (
                <p className="px-4 py-10 text-center text-sm text-zinc-400">暂无资金流数据</p>
              ) : (
                <FlowChart flow={flow} />
              )}
            </Panel>
          )}
        </div>

        {/* 右列：盘口↔逐笔 + 资讯 tabs（左缘拖拽条调宽，宽度持久化 localStorage；
            方案 B：可整体收起） */}
        {rightCollapsed ? (
          <button
            onClick={toggleRightCollapsed}
            className="flex min-h-0 w-7 items-center justify-center rounded-lg border border-zinc-200 text-zinc-400 transition-colors hover:text-zinc-900 dark:border-zinc-800 dark:hover:text-zinc-100"
            title="展开右列（盘口/逐笔/资料/资讯）"
            aria-label="展开右列"
          >
            <span className="text-xs [writing-mode:vertical-lr] tracking-widest">◀ 展开右列</span>
          </button>
        ) : (
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
          title={
            rightTab === "book"
              ? "五档盘口"
              : rightTab === "trades"
                ? "逐笔成交"
                : rightTab === "trade"
                  ? "模拟交易"
                  : rightTab === "real"
                    ? "真实持仓（券商实际成交记账）"
                    : rightTab === "profile"
                      ? "公司资料"
                      : rightTab === "speed"
                        ? "题材涨速榜（5 分钟）"
                        : rightTab === "boards"
                          ? "板块涨幅"
                          : "资讯"
          }
          bodyClassName="overflow-y-auto"
          source={rightTab === "book" ? book?.source : undefined}
          dataTimestamp={rightTab === "book" ? book?.data_timestamp : null}
          className="min-h-0 flex-1 overflow-hidden"
          extra={
            /* 收起按钮走 Panel 头部 extra 槽位（正常文档流）——原 absolute 悬浮
               定位正好压在头部右侧「数据来源/数据时间」徽标上（2026-09-04 用户反馈） */
            <button
              onClick={toggleRightCollapsed}
              className="rounded border border-zinc-200 px-1.5 py-0.5 text-[10px] text-zinc-400 transition-colors hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100"
              title="收起右列，图表获得全宽"
              aria-label="收起右列"
            >
              ▶ 收起
            </button>
          }
        >
          <div className="flex shrink-0 gap-1 border-b border-zinc-100 px-2 py-1 dark:border-zinc-800/60">
            {(
              isIndex
                ? ([
                    // 指数右列（参考同花顺指数页：分时/盘口/涨速/相关板块/资讯）：
                    // 无五档与逐笔数据源，保留 tab 显示空态；涨速/板块为指数专属价值 tab
                    ["book", "盘口"],
                    ["speed", "涨速"],
                    ["boards", "板块"],
                    ["info", "资讯"],
                  ] as const)
                : ([
                    ["book", "盘口"],
                    ["trades", "逐笔"],
                    ["trade", "模拟交易"],
                    // 真实持仓：券商实际成交的手工账本，与模拟交易完全独立
                    ["real", "真实持仓"],
                    ["profile", "资料"],
                    ["info", "资讯"],
                  ] as const)
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
          {rightTab === "speed" && <SpeedPanel className="h-full" />}
          {rightTab === "boards" && <BoardRankPanel className="h-full" />}
          {rightTab === "real" && (
            <RealPositionPanel symbol={symbol} currentPrice={quote?.price ?? null} currentName={quote?.name ?? null} className="h-full" />
          )}
          {rightTab === "trade" &&
            (paper === undefined ? (
              <div className="space-y-2 p-1" aria-hidden>
                <Skeleton className="h-8 w-full rounded-lg" />
                <Skeleton className="h-24 w-full rounded-xl" />
                <Skeleton className="h-16 w-full rounded-xl" />
              </div>
            ) : paper ? (
              <TradePanel
                symbol={symbol}
                paper={paper}
                fills={fills}
                quote={quote ?? null}
                resetBusy={resetBusy}
                onResetAccount={() => void handleResetAccount()}
                onPaperChanged={() => loadPaperRef.current()}
              />
            ) : (
              <p className="px-3 py-10 text-center text-xs text-zinc-400">模拟账户数据加载失败（稍后自动重试）</p>
            ))}

          {rightTab === "profile" && <ProfilePanel boardRows={boardRows} company={company} fins={fins} />}

          {rightTab === "info" && <InfoPanel anns={anns} news={news} annError={annError} newsError={newsError} />}

          <BookTradesView book={book} trades={trades} showBook={rightTab === "book"} />
        </Panel>
        </div>
        )}
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
