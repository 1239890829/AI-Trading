"use client";

import { inspectionClick, useInspection } from "@/components/inspection/inspection-context";


import "./workbench.css";
import { FilterMenu } from "@/components/ui/filter-menu";
import { IconButton } from "@/components/ui/icon-button";
import { HugeiconsIcon } from "@hugeicons/react";
import Cancel01Icon from "@hugeicons/core-free-icons/Cancel01Icon";

import Link from "next/link";
import { patchWorkspaceUrl } from "@/lib/task-navigation";
import { Suspense, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Panel } from "@/components/panel";
import { parseChartTab, parseRightTab } from "@/lib/detail-tabs";
import { IndexCards } from "@/components/index-cards";
import { StockDetailPanel, type ChartTab, type RightTab } from "@/components/stock-detail";
import { SymbolDetailStateCtx } from "@/components/detail/symbol-detail-context";
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { PickDetailModal, type PickDetailTarget } from "@/components/picks/pick-detail-modal";
import { useQuoteStream, STREAM_STATUS_LABEL } from "@/hooks/use-quote-stream";
import { usePollingFetch, useResource } from "@/hooks/use-polling-fetch";
import { PageSkeletonFallback, Skeleton } from "@/components/ui/loading";
import { PanelBoundary } from "@/components/ui/panel-boundary";
import { useRealPositions } from "@/hooks/use-real-positions";
import {
  addToWatchlist,
  createWatchlistGroup,
  deleteWatchlistGroup,
  getIntradayTop,
  isIndexSymbol,
  getMarketOverview,
  getBoardFundBySymbols,
  getQuotes,
  getRiskState,
  getTodayPicks,
  getTurnoverToday,
  getWatchlist,
  getWatchlistGroups,
  removeFromWatchlist,
  renameWatchlistGroup,
  updateWatchlistGroup,
  type DailyPickItem,
  type Freshness,
  type IntradayTopStock,
  type RiskState,
  type SymbolBoardFund,
} from "@/lib/api";
import { fmt, pctColor, pctText, signedYi, sourceLabel, timeText, triAmount, triText } from "@/lib/format";
import { subscribeWatchlist, notifyWatchlistChanged } from "@/lib/watchlist-sync";
import { LAST_SYMBOL_KEY, originLabel } from "@/lib/routing";
import { indexDetailSymbol } from "@/lib/api/client";
import type { Quote } from "@/types/market";

const STATUS_LABEL = STREAM_STATUS_LABEL;
const MOBILE_QUERY = "(max-width: 1023px)";
function mobileSnapshot() { return typeof window.matchMedia === "function" && window.matchMedia(MOBILE_QUERY).matches; }
function subscribeMobile(listener: () => void) {
  if (typeof window.matchMedia !== "function") return () => {};
  const query = window.matchMedia(MOBILE_QUERY);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}

// ?ct= / ?rt= 深链参数解析（2026-09-11 抽到 lib/detail-tabs.ts：这是与
// lib/nav-targets.ts 构造器之间的跨模块契约，放在那里才能被交叉断言——
// 两边漂移时链接不会报错，只会**静默回落默认 tab**）。

function WorkbenchInner() {
  const { open: inspect } = useInspection();
  const router = useRouter();
  const sp = useSearchParams();
  const paramSymbol = sp.get("symbol");
  const mode = sp.get("mode") === "positions" ? "positions" : "watch";
  const account = "manual"; // User views contain actual manual holdings; shadows remain internal.
  // 深链 tab（助手一键跳转 / 分享链接）：?ct= 图表区、?rt= 右栏，非法值忽略回落到默认
  const chartTab = parseChartTab(sp.get("ct"));
  const requestedRight = parseRightTab(sp.get("rt"));
  const rightTab = requestedRight === "trade" ? "real" : requestedRight ?? (mode === "positions" ? "real" : undefined);
  const mobileContext = `${mode}:${account}:${rightTab ?? ""}`;
  const preferredMobileView = rightTab ? "check" : "chart";
  const [mobileView, setMobileView] = useState<"list" | "chart" | "check">(preferredMobileView);
  const [appliedMobileContext, setAppliedMobileContext] = useState(mobileContext);
  // Follow a new detail/account deep link; local view switches keep their place.
  if (appliedMobileContext !== mobileContext) {
    setAppliedMobileContext(mobileContext);
    setMobileView(preferredMobileView);
  }
  const [symbols, setSymbols] = useState<string[]>([]);
  const [watchConfirmed, setWatchConfirmed] = useState(false);
  const confirmWatchlistAdded = useCallback((symbol: string) => {
    setSymbols(previous => previous.includes(symbol) ? previous : [...previous, symbol]);
  }, []);
  // 左栏列表 pending 哨兵（审查 F2/R2）：loadBase/dyn 首拉完成前渲染行骨架
  const [baseLoaded, setBaseLoaded] = useState(false);
  const [baseErrors, setBaseErrors] = useState<Record<string, string>>({});
  const baseReadRef = useRef({watch: 0, groups: 0, overview: 0});
  const watchReadsRef = useRef<{watch?: AbortController; groups?: AbortController; fresh: boolean}>({fresh: false});
  useEffect(() => () => {
    const reads = baseReadRef.current;
    reads.watch += 1; reads.groups += 1; reads.overview += 1;
    watchReadsRef.current.watch?.abort(); watchReadsRef.current.groups?.abort();
  }, []);
  // 真实持仓（CONTEXT.md: Holdings Group）：共用 hook 一份轮询（评审 M3），
  // 标的列表派生进 WS 订阅，「持仓」分类共用
  const { data: realData, error: realError, reload: reloadRealPositions } = useRealPositions();
  const realPositions = useMemo(() => ({data: realData, error: realError, reload: reloadRealPositions}), [realData, realError, reloadRealPositions]);
  const isMobile = useSyncExternalStore(subscribeMobile, mobileSnapshot, () => false);
  const symbolDetail = useContext(SymbolDetailStateCtx);
  const detailActive = (!isMobile || mobileView !== "list") && !symbolDetail;
  const realSymbols = useMemo(() => realData?.items.map((i) => i.symbol) ?? [], [realData]);
  const [indices, setIndices] = useState<Quote[] | undefined>();
  const [totalAmount, setTotalAmount] = useState<number | null>(null);
  // 成交额的三态判据（S2-1 契约）。**必须与数值分开存**：`null` 有「上游尚未就绪」
  // 与「真的没有数据」两种成因，只凭数值无法区分（2026-09-14 报障根因）。
  const [amountFreshness, setAmountFreshness] = useState<Freshness | null>(null);
  // 两市成交额 vs 昨日同一时刻增减（亿元）——资金 Tab 顶摘要（2026-09-04）
  const [turnDiff, setTurnDiff] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 以前用 ref 装着再在渲染期读（React 并发渲染下不可靠，且 Next16 的 lint 直接判错）——改为状态
  const [groupMap, setGroupMap] = useState<Record<string, string>>({});
  // 分组清单（持久表 ∪ 成员派生，后端并集）：空分组也可见（评审 A1 分组管理）
  const [allGroupNames, setAllGroupNames] = useState<string[]>(["默认"]);
  const [watchGroup, setActiveGroup] = useState<string>("全部");
  const activeGroup = mode === "positions" ? "持仓" : watchGroup;
  const [updatedAt, setUpdatedAt] = useState<string>("");
  // ── 动态分组（2026-09-04）：每日精选（/picks/today）+ 盘中跟踪（/picks/intraday-top）──
  // 数据源是系统推荐口径，不是用户自选——只读展示，不走 watchlist 表；
  // 60s 轮询随推荐与盘中行情自动更新（intraday-top 后端本身还有 60s 缓存）。
  const [picksItems, setPicksItems] = useState<DailyPickItem[]>([]);
  const [picksDate, setPicksDate] = useState<string | null>(null);
  const [topItems, setTopItems] = useState<IntradayTopStock[]>([]);
  const picksSymbols = useMemo(() => picksItems.map((i) => i.symbol), [picksItems]);
  const topSymbols = useMemo(() => topItems.map((i) => i.symbol), [topItems]);
  // 选中标的：URL 参数是唯一真相源；无参数时回退「上次查看的标的」，
  // 都没有再落默认 600519（此前硬编码回退是跨页面联动 bug 的一半根因）
  const [selected, setSelected] = useState<string>(paramSymbol ?? "");
  // URL 参数变化 → 渲染期同步选中（React 官方 adjust-state-when-props-change 模式，
  // 替代原 effect 同步——消除 setState-in-effect 级联渲染）。页面在 Suspense 边界内
  // CSR 渲染，sessionStorage 仅客户端存在：SSR 渲染期守卫跳过，挂载首帧即恢复 last。
  const [lastAppliedParam, setLastAppliedParam] = useState<string | null>(paramSymbol);
  if (paramSymbol !== lastAppliedParam) {
    setLastAppliedParam(paramSymbol);
    if (paramSymbol) {
      setSelected(paramSymbol);
    } else {
      const last = typeof window !== "undefined" ? (() => { try { return window.sessionStorage.getItem(LAST_SYMBOL_KEY); } catch { return null; } })() : null;
      if (last) setSelected(last);
    }
  }

  // 渲染兜底：selected 尚未就绪（首帧/回退解析中）时保持原默认标的，避免空 symbol 取数。
  // （P1-5 起提到订阅之前——订阅集需要并入 activeSymbol）
  const activeSymbol = selected || "600519";

  // 列表消费侧 3s 节流（2026-09-02 用户反馈：1Hz 刷新整表闪烁跳动）。
  // 详情通过同一 owner 的 quoteFeed 即时消费，不等待列表节流，也不另建连接。
  // 动态分组标的并入订阅：精选/跟踪标的的行情与自选同源同节奏。
  //
  // P1-5（2026-09-11）：订阅集**显式并入 activeSymbol**，详情面板因此可以复用这条
  // 连接（props 下传），不再为同一只股票另开一条 WS、也不再因切股重挂载而断连。
  // activeSymbol 可能是深链/搜索进来的、不在任何分组里的标的——必须显式加入，
  // 否则详情面板会拿不到行情。切股只发 subscribe 消息，不重连（见 useQuoteStream 头注）。
  const streamSymbols = useMemo(
    () => [...new Set([...symbols, ...realSymbols, ...picksSymbols, ...topSymbols, activeSymbol, ...(indices ?? []).map(q => indexDetailSymbol(q.symbol, q.market))])],
    [symbols, realSymbols, picksSymbols, topSymbols, activeSymbol, indices],
  );
  const { quotes, status, quoteFeed } = useQuoteStream(streamSymbols, { throttleMs: 3000 });
  const [extra, setExtra] = useState<Record<string, Quote>>({});
  // WS 每 5s tick 全量替换 quotes：merged/列表查找都必须 memo 化，
  // 否则每次 tick 触发整列表 O(n²) 重算（评审 F2）
  const merged: Record<string, Quote> = useMemo(() => ({ ...extra, ...quotes }), [extra, quotes]);
  const displayedIndices = useMemo(() => indices?.map(q => merged[indexDetailSymbol(q.symbol, q.market)] ?? q), [indices, merged]);

  const [risk, setRisk] = useState<RiskState | null>(null);

  // 记住最近查看的标的（会话内有效；路由规范见 lib/routing.ts）
  useEffect(() => {
    if (selected) { try { window.sessionStorage.setItem(LAST_SYMBOL_KEY, selected); } catch {} }
  }, [selected]);

  // 页内切股：URL 是唯一真相源；from 参数（来源页，见 lib/routing.workbenchUrlWithBack）
  // 原样保留——用户切了几只股后「← 返回来源页」入口不能消失
  const switchSymbol = useCallback(
    (s: string) => {
      setSelected(s);
      setMobileView("chart");
      const prior = parseRightTab(sp.get("rt"));
      const nextRight = isIndexSymbol(s) ? "members"
        : isIndexSymbol(activeSymbol) || prior === "members" || prior === "boards" ? "book" : prior;
      const url = patchWorkspaceUrl("/workbench", sp.toString(), {symbol: s, rt: nextRight ?? null});
      router.replace(url, { scroll: false });
    },
    [router, sp, activeSymbol],
  );

  const switchRightTab = useCallback((tab: RightTab) => {
    setMobileView("check");
    const patch: Record<string, string | null> = { rt: tab };
    if (tab === "trade" || tab === "real") {
      patch.mode = "positions";
      patch.account = "manual";
      patch.rt = "real";
    }
    router.replace(patchWorkspaceUrl("/workbench", sp.toString(), patch), {scroll: false});
  }, [router, sp]);

  // 返回来源页：from 只接受站内绝对路径（防注入），返回即恢复跳转前的 URL（含状态）。
  // 状态保留原理：各功能页的 tab/选中态本来就以 URL query 为真相源，整串带回即可。
  const backFrom = sp.get("from");
  const backLabel = originLabel(backFrom);

  const reportBase = useCallback((key: string, message?: string) => {
    setBaseErrors(previous => {
      if (previous[key] === message || (!message && !(key in previous))) return previous;
      const next = {...previous};
      if (message) next[key] = message;
      else delete next[key];
      return next;
    });
  }, []);

  // 四个读取分别拥有单飞/计时链，首屏自选不等待概览或分组。
  // 自选/分组首次即读、写后即时失效，并以 60s 兜底同步其它客户端；
  // 行情概览保持原10s校准；指数价格复用既有WS帧。
  // 这里沿用各自的状态与错误展示。
  const {refresh: refreshWatch} = useResource(async () => {
    const version = baseReadRef.current.watch;
    const current = () => version === baseReadRef.current.watch;
    const controller = new AbortController();
    watchReadsRef.current.watch?.abort();
    watchReadsRef.current.watch = controller;
    const fresh = watchReadsRef.current.fresh;
    watchReadsRef.current.fresh = false;
    try {
      const value = await getWatchlist({signal: controller.signal, fresh});
      if (!current()) return;
      setGroupMap(Object.fromEntries(value.map(i => [i.symbol, i.group_name ?? "默认"])));
      setSymbols(value.map(i => i.symbol));
      setWatchConfirmed(true);
      reportBase("watch");
    } catch {if (current()) reportBase("watch", "自选读取失败");}
    finally {if (current()) setBaseLoaded(true);}
  }, {intervalMs: 60_000, marketHours: false});
  const {refresh: refreshGroups} = useResource(async () => {
    const version = baseReadRef.current.groups;
    const controller = new AbortController();
    watchReadsRef.current.groups?.abort();
    watchReadsRef.current.groups = controller;
    try {
      const value = await getWatchlistGroups({signal: controller.signal});
      if (version !== baseReadRef.current.groups) return;
      setAllGroupNames(["默认", ...value]);
      reportBase("groups");
    } catch {if (version === baseReadRef.current.groups) reportBase("groups", "分组读取失败");}
  }, {intervalMs: 60_000, marketHours: false});
  const {refresh: refreshOverview} = useResource(async (signal) => {
    const version = baseReadRef.current.overview;
    try {
      const value = await getMarketOverview({signal});
      if (signal.aborted || version !== baseReadRef.current.overview) return;
      setIndices(value.indices); setTotalAmount(value.total_amount);
      setAmountFreshness(value.total_amount_freshness);
      setUpdatedAt(new Date().toLocaleTimeString("zh-CN", {hour12: false}));
      reportBase("overview");
    } catch {if (!signal.aborted && version === baseReadRef.current.overview) reportBase("overview", "行情概览读取失败");}
  }, {intervalMs: 10_000, abortable: true});
  const refreshWatchlist = useCallback(() => {
    baseReadRef.current.watch += 1; baseReadRef.current.groups += 1;
    watchReadsRef.current.watch?.abort(); watchReadsRef.current.groups?.abort();
    watchReadsRef.current.fresh = true;
    refreshWatch(); refreshGroups();
  }, [refreshWatch, refreshGroups]);
  const retryFailedBase = useCallback(() => {
    if (baseErrors.watch) {
      baseReadRef.current.watch += 1;
      watchReadsRef.current.watch?.abort();
      watchReadsRef.current.fresh = true;
      refreshWatch();
    }
    if (baseErrors.groups) {
      baseReadRef.current.groups += 1;
      watchReadsRef.current.groups?.abort();
      refreshGroups();
    }
    if (baseErrors.overview) {baseReadRef.current.overview += 1; refreshOverview();}
  }, [baseErrors, refreshWatch, refreshGroups, refreshOverview]);

  // 风控市场状态（评审 O2）：七档状态变化以日为尺度，独立 30s 轮询——
  // 混在 loadBase 10s 里纯属浪费（state_classifier 本身走 60s 快照聚合）。
  usePollingFetch(async () => {
    const r = await getRiskState().catch(() => null);
    setRisk(r);
  }, 30_000);

  // 成交额增减摘要（60s：后端 30s 缓存 + 新浪分钟K 300s 缓存，此频率零额外压力）
  usePollingFetch(async () => {
    const t = await getTurnoverToday().catch(() => null);
    if (t) setTurnDiff(t.diff_yi);
  }, 60_000);

  // 动态分组数据源（60s：每日精选每日级变化、intraday-top 后端 60s 缓存对齐）。
  // 失败保留旧数据（盘中行情仍在跳，下一次轮询补上），不闪空。
  // dynReady：各自首拉完成哨兵——空态文案（"今日尚无精选组合"）只在确认后渲染。
  const [dynReady, setDynReady] = useState({ picks: false, top: false });
  const [dynErrors, setDynErrors] = useState({ picks: false, top: false });
  usePollingFetch(async () => {
    const p = await getTodayPicks().catch(() => null);
    if (p) {
      setPicksItems(p.items ?? []);
      setPicksDate(p.date ?? null);
    }
    setDynErrors((s) => ({ ...s, picks: p === null }));
    setDynReady((s) => (s.picks ? s : { ...s, picks: true }));
  }, 60_000);
  usePollingFetch(async () => {
    const t = await getIntradayTop().catch(() => null);
    if (t) setTopItems(t.items ?? []);
    setDynErrors((s) => ({ ...s, top: t === null }));
    setDynReady((s) => (s.top ? s : { ...s, top: true }));
  }, 60_000);

  // 自选集合变化（search-box 快捷加自选 / 详情面板 ＋自选）→ 立即刷新
  // （2026-09-01 简化：原 CustomEvent 契约改为 lib/watchlist-sync 模块通知）
  useEffect(() => subscribeWatchlist(refreshWatchlist), [refreshWatchlist]);

  // 首帧行情兜底：WS 订阅切换窗口期里，动态分组标的与自选一起走 REST 补拉
  const feedSymbolKey = [...streamSymbols].sort().join(",");
  useEffect(() => {
    let alive = true;
    const missing = feedSymbolKey.split(",").filter((s) => s && !(s in merged));
    if (missing.length > 0) {
      const requested = new Set(missing);
      getQuotes(missing)
        .then((qs) => {if (alive) setExtra((prev) => Object.fromEntries([...Object.entries(prev), ...qs.filter(q => requested.has(q.symbol)).map((q) => [q.symbol, q])]));})
        .catch(() => {});
    }
    return () => {alive = false;};
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedSymbolKey]);

  // 所属板块资金（P1-4，2026-09-10）：主板块 = 东财行业三级 L2（白酒Ⅱ/银行Ⅱ…），
  // 资金 = 东财 f62 主力净额 + 连续流入天数。**口径与个股资金流（新浪 L4）不同，不可相加**。
  // 60s 与自选行情同节奏；F10 归属在后端有 6h 缓存（所属板块低频变更），轮询实际只刷新资金值。
  const symbolKey = symbols.join(",");
  const boardFund = useResource(async () => {
    const result: Record<string, SymbolBoardFund> = {};
    // 接口单批最多50只；串行批次覆盖完整集合，避免同时向资料源扇出。
    for (let start = 0; start < symbols.length; start += 50) {
      Object.assign(result, await getBoardFundBySymbols(symbols.slice(start, start + 50)));
    }
    return result;
  }, {key: symbolKey, enabled: symbols.length > 0, intervalMs: 60_000});
  // 只保留低频归属文字。资金值只消费本轮结果，缺项绝不沿用旧金额。
  const [boardIdentity, setBoardIdentity] = useState<{
    key: string; data: typeof boardFund.data;
    known: Record<string, Pick<SymbolBoardFund, "board_name" | "level">>;
  }>({key: symbolKey, data: undefined, known: {}});
  if (boardIdentity.key !== symbolKey || boardIdentity.data !== boardFund.data) {
    const known: typeof boardIdentity.known = {};
    for (const symbol of symbols) {
      const row = boardFund.data?.[symbol];
      if (row?.board_name) known[symbol] = {board_name: row.board_name, level: row.level};
      else if (boardIdentity.known[symbol]) known[symbol] = boardIdentity.known[symbol];
    }
    setBoardIdentity({key: symbolKey, data: boardFund.data, known});
  }

  // 分组清单由 groupMap 派生（旧代码是独立的 groups 状态，从未被赋值，chips 永远只有「全部」）
  // 「猎场」是动态分组的保留名（2026-09-09 合并原「每日精选」「盘中跟踪」），用户分组里排除（防 chips 重名撞 key）
  const groups = useMemo(
    () =>
      allGroupNames
        .filter((g, i) => g !== "默认" && allGroupNames.indexOf(g) === i)
        .filter((g) => g !== "每日精选" && g !== "盘中跟踪" && g !== "猎场")
        .sort(),
    [allGroupNames]
  );
  // 管理模式下分组下拉的可选项（含「默认」兜底）
  const allGroups = useMemo(() => Array.from(new Set(["默认", ...allGroupNames])), [allGroupNames]);
  // 对象身份来自真实列表；报价未到时仍保留该证券，不把缺报价解释成空集合。
  const activeRowSymbols = useMemo(() => {
    if (activeGroup === "持仓") return realSymbols;
    if (activeGroup === "猎场") return Array.from(new Set([...topSymbols, ...picksSymbols]));
    return symbols.filter((symbol) => activeGroup === "全部" || (groupMap[symbol] ?? "默认") === activeGroup);
  }, [activeGroup, realSymbols, topSymbols, picksSymbols, symbols, groupMap]);
  // Manual holding labels reuse the existing position owner; no shadow-account read.
  const posLabels = useMemo<Record<string, string>>(() => Object.fromEntries(realSymbols.map(s => [s, "real"])), [realSymbols]);

  const pickInfoBySymbol = useMemo(() => new Map(picksItems.map((i) => [i.symbol, i])), [picksItems]);
  const topInfoBySymbol = useMemo(() => new Map(topItems.map((i) => [i.symbol, i])), [topItems]);
  const isDynamicGroup = activeGroup === "猎场";
  // 选股详情弹窗（2026-09-07 用户需求）：每日精选/盘中跟踪行的「详情」按钮
  const [detailTarget, setDetailTarget] = useState<PickDetailTarget>(null);

  async function remove(symbol: string) {
    try {
      await removeFromWatchlist(symbol);
      setSymbols((prev) => prev.filter((s) => s !== symbol));
      notifyWatchlistChanged();
    } catch (error) { setError(`移出自选失败：${error instanceof Error ? error.message : "请重试"}`); }
  }

  // ── 自选管理模式（2026-09-01 自选页并入工作台）──────────────
  // 原 /watchlist 页仅剩两项独有能力：手动输代码添加、修改分组——
  // 收进这里后独立页面删除（docs/archive/architecture-redesign.md §一.1.2）。
  const [managing, setManaging] = useState(false);
  const [newSymbol, setNewSymbol] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [addBusy, setAddBusy] = useState(false);
  const [addResult, setAddResult] = useState("");

  async function addWatch() {
    if (addBusy) return;
    const s = newSymbol.trim();
    setAddResult("");
    if (!/^\d{6}$/.test(s)) {
      setAddError("请输入 6 位数字代码");
      return;
    }
    setAddBusy(true);
    try {
      await addToWatchlist(s);
      setNewSymbol("");
      setAddError(null);
      setAddResult(`已将 ${s} 加入自选`);
      setSymbols((prev) => (prev.includes(s) ? prev : [...prev, s]));
      notifyWatchlistChanged(); // 其他订阅方同步（本页静态集合由同一订阅即时更新）
    } catch {
      setAddError("添加失败，请确认后端已启动后重试；输入代码已保留。");
    } finally {
      setAddBusy(false);
    }
  }

  async function changeGroup(symbol: string, group: string) {
    try {
      await updateWatchlistGroup(symbol, group);
      setGroupMap((prev) => ({ ...prev, [symbol]: group }));
      notifyWatchlistChanged();
    } catch (error) { setAddError(`修改分组失败：${error instanceof Error ? error.message : "请重试"}`); }
  }

  // 分组命令在关注带原位完成。写入只走原有后端，失败保留用户输入。
  const [groupAction, setGroupAction] = useState<"create" | "rename" | "delete" | null>(null);
  const [groupDraft, setGroupDraft] = useState("");
  const [groupBusy, setGroupBusy] = useState(false);
  const [groupError, setGroupError] = useState<string | null>(null);
  const [groupResult, setGroupResult] = useState("");
  const canEditGroup = activeGroup !== "全部" && activeGroup !== "默认" && activeGroup !== "持仓" && !isDynamicGroup;

  function openGroupAction(action: "create" | "rename" | "delete") {
    setGroupAction(action);
    setGroupDraft(action === "create" ? "" : activeGroup);
    setGroupError(null);
    setGroupResult("");
  }

  async function saveGroup() {
    if (!groupAction || groupBusy) return;
    const name = groupDraft.trim();
    if (groupAction !== "delete" && !name) {
      setGroupError("请输入分组名称");
      return;
    }
    setGroupBusy(true);
    setGroupError(null);
    try {
      if (groupAction === "create") {
        await createWatchlistGroup(name);
        setActiveGroup(name);
        setGroupResult(`已新建分组「${name}」`);
      } else if (groupAction === "rename") {
        await renameWatchlistGroup(activeGroup, name);
        setActiveGroup(name);
        setGroupResult(`已重命名为「${name}」`);
      } else {
        await deleteWatchlistGroup(activeGroup);
        setActiveGroup("全部");
        setGroupResult("已删除分组，成员已回到默认分组");
      }
      notifyWatchlistChanged();
      setGroupAction(null);
    } catch (e) {
      setGroupError(`分组保存失败：${e instanceof Error ? e.message : "请重试"}`);
    } finally {
      setGroupBusy(false);
    }
  }

  const groupOptions = [
    { key: "全部", label: "全部自选", count: symbols.length },
    { key: "默认", label: "默认分组" },
    { key: "猎场", label: "系统候选", count: new Set([...topSymbols, ...picksSymbols]).size, title: "盘中跟踪与每日精选；只读系统结果" },
    ...groups.map((group) => ({ key: group, label: group })),
  ];
  const sourcePending = isDynamicGroup ? !dynReady.picks || !dynReady.top : mode === "positions" ? !realData && !realError : !baseLoaded;
  const sourceError = isDynamicGroup ? dynErrors.picks || dynErrors.top : mode === "positions" ? realError : baseErrors.watch;
  const readWarning = [error, ...Object.values(baseErrors)].filter(Boolean).join("；");

  return (
    <main data-workspace="workbench" className="task-page bc-workbench">
      <h1 className="sr-only">工作台</h1>
      <header className="bc-workbench-header">
        <div className="bc-workbench-title">
          {backLabel && backFrom && <button className="bc-back-button" onClick={() => router.push(backFrom)} title={`返回${backLabel}（跳转前状态已保留）`}>返回{backLabel}</button>}
          <h2>关注与核对</h2>
          <span className="bc-connection inline-flex w-[8.4em] shrink-0" data-transport={status} title={STATUS_LABEL[status].title}><span className={STATUS_LABEL[status].cls}>{STATUS_LABEL[status].text}</span></span>
        </div>
        <div className="bc-workbench-objects">
          <nav aria-label="个人对象" className="bc-object-switch">
            <Link href={patchWorkspaceUrl("/workbench", sp.toString(), {mode: "watch", rt: null})} aria-current={mode === "watch" ? "page" : undefined}>自选跟踪</Link>
            <Link href={patchWorkspaceUrl("/workbench", sp.toString(), {mode: "positions", account: "manual", rt: null})} aria-current={mode === "positions" ? "page" : undefined}>实际持仓</Link>
          </nav>

        </div>
      </header>
      {readWarning && <div role="alert" className="bc-read-warning bc-base-warning"><details><summary>部分数据读取失败 · 查看原因</summary><p className="data-scroll" tabIndex={0}>{readWarning}。保留值仅为上次结果，不能判断当前为空。</p></details>{Object.keys(baseErrors).length > 0 && <button onClick={retryFailedBase}>重试读取</button>}</div>}

      <nav className="bc-mobile-views" aria-label="工作台内容">
        {([["list", "指数与自选"], ["chart", "图表"], ["check", "核对"]] as const).map(([key, label]) => <button key={key} type="button" aria-pressed={mobileView === key} onClick={() => setMobileView(key)}>{label}</button>)}
      </nav>
      <div className="bc-workbench-layout" data-mobile-view={mobileView}>
        <aside className="bc-workbench-sidebar" data-account-view={mode === "positions"} aria-label="指数与证券列表">
        <div className="bc-market-context">
          <div className="bc-index-strip"><IndexCards indices={displayedIndices ?? []} selected={activeSymbol} onSelect={switchSymbol} /></div>
          <div className="bc-market-summary">
            <button className="bc-turnover" onClick={inspectionClick(inspect, {kind: "fund"})} title="查看实时对比、全日估算与资金流">
              <span>两市成交额</span><strong title={amountFreshness?.reason ?? undefined}>{triAmount(totalAmount, amountFreshness?.state)}</strong>
              {turnDiff != null && <span className={turnDiff >= 0 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down"}>{turnDiff >= 0 ? "+" : ""}{turnDiff.toLocaleString("zh-CN", {maximumFractionDigits: 0})}亿 <small>较昨日同时刻</small></span>}
            </button>
            {risk && <span className="bc-market-state" title={risk.reasons.join("；")}>市场状态 <strong>{risk.state}</strong></span>}
            <span className="bc-index-update">指数刷新 {updatedAt || "--"}</span>
          </div>
        </div>

        {mode === "positions" && realError && <p role="alert" className="bc-read-warning">手工记录读取失败，保留结果仅供参考：{realError}</p>}

        <section className="bc-attention" aria-label={mode === "positions" ? "当前账户证券" : "关注与候选"}>
          <div className="bc-attention-heading">
            <div className="bc-attention-scope">
              {mode === "watch" ? <FilterMenu label="列表" value={activeGroup} options={groupOptions} onChange={value => {setActiveGroup(value); setGroupAction(null); setGroupError(null);}} /> : <h3>{"手工记录持仓"}</h3>}
              <span className="bc-list-count">{activeRowSymbols.length} 只</span>
              {isDynamicGroup && <span className="bc-list-note">盘中跟踪 + 每日精选 {picksDate ?? "未生成"}</span>}
            </div>
            <div className="bc-attention-actions">
              <Link href="/hunting" onClick={inspectionClick(inspect, {kind: "selection-preview"})} className="bc-text-link">查看选股</Link>
              {mode === "watch" && <button className="bc-compact-button" aria-expanded={managing} onClick={() => {setManaging(value => !value); setAddError(null); setGroupAction(null); setGroupError(null);}}>{managing ? "完成管理" : "管理自选"}</button>}
            </div>
          </div>
          {isDynamicGroup && (dynErrors.picks || dynErrors.top) && <p role="alert" className="bc-read-warning">{dynErrors.top ? "盘中候选读取失败。" : ""}{dynErrors.picks ? "每日精选读取失败。" : ""}保留值仅为上次结果，不能判断当前为空。</p>}
          {managing && mode === "watch" && <div className="bc-watch-management">
            <form className="bc-add-symbol" onSubmit={event => {event.preventDefault(); void addWatch();}}>
              <label htmlFor="workbench-add-symbol">加入自选</label><input id="workbench-add-symbol" value={newSymbol} onChange={event => setNewSymbol(event.target.value)} placeholder="6 位股票代码" maxLength={6} inputMode="numeric" autoComplete="off" disabled={addBusy} /><button className="bc-compact-button" type="submit" disabled={addBusy}>{addBusy ? "添加中…" : "添加"}</button>
            </form>
            <div className="bc-group-actions"><button className="bc-compact-button" onClick={() => openGroupAction("create")}>新建分组</button>{canEditGroup && <><button className="bc-compact-button" onClick={() => openGroupAction("rename")}>重命名分组</button><button className="bc-compact-button bc-danger-button" onClick={() => openGroupAction("delete")}>删除分组</button></>}</div>
            {addError && <p role="alert" className="bc-management-error">{addError}</p>}
            {addResult && <p role="status" className="bc-management-result">{addResult}</p>}
            {groupResult && <p role="status" className="bc-management-result">{groupResult}</p>}
            {groupAction && <form className="bc-group-form" onSubmit={event => {event.preventDefault(); void saveGroup();}}>
              {groupAction === "delete" ? <p>删除「{activeGroup}」后，成员将回到默认分组。</p> : <label>{groupAction === "create" ? "新分组名称" : "修改分组名称"}<input value={groupDraft} onChange={event => setGroupDraft(event.target.value)} disabled={groupBusy} autoFocus /></label>}
              <button className={`bc-compact-button ${groupAction === "delete" ? "bc-danger-button" : ""}`} type="submit" disabled={groupBusy}>{groupBusy ? "保存中…" : groupAction === "delete" ? "确认删除分组" : "保存分组"}</button>
              <button className="bc-compact-button" type="button" disabled={groupBusy} onClick={() => setGroupAction(null)}>取消</button>
              {groupError && <p role="alert" className="bc-management-error">{groupError}</p>}
            </form>}
          </div>}
          <div className="bc-attention-track" tabIndex={0} aria-label="证券列表，可滚动">
            {sourcePending ? <div className="bc-watch-skeleton" aria-label="正在读取证券列表">{Array.from({length: 4}, (_, index) => <Skeleton key={index} className="h-20 w-44 shrink-0 rounded-lg" />)}</div> : activeRowSymbols.length === 0 ? <div className="bc-watch-empty">
              {sourceError ? "列表读取有缺项，暂不能判断为空。请重试读取。" : activeGroup === "持仓" ? "暂无手工记录。可在记账面板记录已入选股票的实际成交；记录未经券商验证。" : isDynamicGroup ? "暂无系统候选。盘中跟踪随盘面重算；每日精选由后台生成，生成状态见系统维护。" : "自选为空。使用顶部搜索查看证券，再明确加入自选；也可在管理中输入代码。"}
            </div> : activeRowSymbols.map(symbol => {
              const quote = merged[symbol];
              const pick = isDynamicGroup ? pickInfoBySymbol.get(symbol) : undefined;
              const top = isDynamicGroup ? topInfoBySymbol.get(symbol) : undefined;
              const fund = boardFund.error ? undefined : boardFund.data?.[symbol];
              const board = fund ?? boardIdentity.known[symbol];
              const boardLabel = board ? `${board.level === "industry" ? "行业" : board.level === "concept" ? "概念" : "板块"} · ${board.board_name}` : boardFund.status === "pending" ? "所属板块读取中" : "所属板块待确认";
              return <article key={symbol} className="bc-attention-item" data-selected={activeSymbol === symbol}>
                <button className="bc-attention-select" onClick={() => switchSymbol(symbol)} aria-pressed={activeSymbol === symbol} aria-label={`查看 ${quote?.name ?? symbol} ${symbol}`}>
                  <span className="bc-attention-identity"><strong title={quote?.name ?? symbol}>{quote?.name ?? symbol}</strong><span>{symbol}</span></span>
                  <span className="bc-attention-price"><strong>{quote?.price == null ? "待报价" : <PriceFlash value={quote.price}>{fmt(quote.price)}</PriceFlash>}</strong><span className={pctColor(quote?.change_pct)}>{pctText(quote?.change_pct)}</span></span>
                  {(pick || top) && <span className="bc-candidate-basis">
                    {top && <span title={`盘中跟踪 T${top.tier}：${top.pick_basis}；题材 ${top.theme ?? "--"}（${top.stage ?? "?"}）`}>盘中跟踪 T{top.tier}{top.theme ? ` · ${top.theme}` : ""}</span>}
                    {pick && <span title={`六维综合评分 ${pick.score}；题材 ${pick.themes?.join("、") || "--"}`}>每日精选 {pick.score.toFixed(0)} 分</span>}
                    <span title={pick?.echelon_role ?? `确定性 ${triText(top?.certainty?.level)}；辨识度 ${triText(top?.distinctiveness?.level)}`}>{pick?.echelon_role ?? `确定性 ${triText(top?.certainty?.level)} · 辨识度 ${triText(top?.distinctiveness?.level)}`}</span>
                    {posLabels[symbol] && <span title="持仓状态派生标签；删除记录或退出后自动消失">{posLabels[symbol] === "real" ? "手工已记录" : "模拟已持仓"}</span>}
                  </span>}
                  <span className="bc-attention-fund" title={fund ? `${boardLabel}（东财）${signedYi(fund.main_net_yi)}${fund.streak != null && fund.streak >= 1 ? `，连续 ${fund.streak} 日净流入` : ""}；板块与个股资金口径不同，不可相加` : board ? `${boardLabel}为上次确认的归属；本次资金未返回，未沿用旧金额` : boardFund.error ? "所属板块读取失败，可等待下一轮刷新；缺失不代表没有行业归属" : "数据源尚未返回所属板块，不推测行业或概念"}>
                    <span>{boardLabel}{board && !fund ? "（上次）" : ""}</span><span className={fund?.main_net_yi == null ? "" : fund.main_net_yi >= 0 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down"}>{fund ? signedYi(fund.main_net_yi) : board ? "未更新" : "--"}</span>
                  </span>
                </button>
                <div className="bc-attention-meta"><span title={quote ? `来源：${sourceLabel(quote.source)}；数据时间：${timeText(quote.data_timestamp)}；接收时间：${timeText(quote.received_at)}` : "已在列表，报价尚未返回"}>{quote ? sourceLabel(quote.source) : "等待行情"}</span><QualityBadge quality={quote?.quality} reasons={quote?.quality_reasons} symbol={quote?.symbol} market={quote?.market} dataTimestamp={quote?.data_timestamp} session={quote?.display_session} />
                  {(pick || top) ? <span className="bc-candidate-details">{top && <button className="bc-item-detail" onClick={() => setDetailTarget({kind: "top", item: top})} aria-label={`查看 ${symbol} 盘中依据`}>{pick ? "盘中" : "依据"}</button>}{pick && <button className="bc-item-detail" onClick={() => setDetailTarget({kind: "pick", item: pick})} aria-label={`查看 ${symbol} 精选依据`}>{top ? "精选" : "依据"}</button>}</span> : mode === "watch" && <IconButton onClick={() => void remove(symbol)} className="bc-remove-watch" title="移出自选" aria-label={`移出自选 ${symbol}`}><HugeiconsIcon icon={Cancel01Icon} size={14} aria-hidden="true" /></IconButton>}
                </div>
                {managing && mode === "watch" && !isDynamicGroup && <div className="bc-group-assignment"><FilterMenu label={`${symbol} 分组`} value={groupMap[symbol] ?? "默认"} options={allGroups.map(group => ({key: group, label: group}))} onChange={group => void changeGroup(symbol, group)} /></div>}
              </article>;
            })}
          </div>
          {isDynamicGroup && <p className="bc-candidate-disclaimer">系统候选用于投研观察，分数不是胜率，不构成买卖建议。</p>}
        </section>

        </aside>
        <section className="bc-workbench-detail" aria-label="证券详情">
        {/* 边界跟随证券/scope重置，保留深链与共享真实详情的全部消费者。 */}
        <PanelBoundary key={`${activeSymbol}:${mode}:${account}`} label="个股详情">
          <StockDetailPanel symbol={activeSymbol} chartTab={chartTab} rightTab={rightTab} onRightTabChange={switchRightTab} onSymbolChange={switchSymbol} liveQuote={merged[activeSymbol]} streamStatus={status} quoteFeed={quoteFeed} indexQuote={displayedIndices === undefined ? undefined : displayedIndices.find(q => q.symbol === "sh000001" || (q.symbol === "000001" && q.market === "SH")) ?? null} active={detailActive} realPositions={realPositions} watchlistMembership={symbols.includes(activeSymbol) ? true : watchConfirmed ? false : null} onWatchlistAdded={confirmWatchlistAdded} />
        </PanelBoundary>
        </section>
      </div>
      <PickDetailModal target={detailTarget} onClose={() => setDetailTarget(null)} />
    </main>
  );
}

export default function WorkbenchPage() {
  return (
    <Suspense fallback={<PageSkeletonFallback label="工作台加载中" />}>
      <WorkbenchInner />
    </Suspense>
  );
}
