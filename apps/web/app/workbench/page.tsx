"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Panel } from "@/components/panel";
import { IndexCards } from "@/components/index-cards";
import { StockDetailPanel, type ChartTab, type RightTab } from "@/components/stock-detail";
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { Sparkline } from "@/components/sparkline";
import { useQuoteStream, STREAM_STATUS_LABEL } from "@/hooks/use-quote-stream";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { PageSkeletonFallback } from "@/components/ui/loading";
import { useRealPositions } from "@/hooks/use-real-positions";
import {
  addToWatchlist,
  createWatchlistGroup,
  deleteWatchlistGroup,
  getIntradayTop,
  getMarketOverview,
  getPaperPositions,
  getQuotes,
  getRiskState,
  getSparklines,
  getTodayPicks,
  getTurnoverToday,
  getWatchlist,
  getWatchlistGroups,
  removeFromWatchlist,
  renameWatchlistGroup,
  updateWatchlistGroup,
  type DailyPickItem,
  type IntradayTopStock,
  type PaperPositionInfo,
  type RiskState,
  type SparklinePayload,
} from "@/lib/api";
import { fmt, fmtAmount, isHardQuality, pctColor, pctText } from "@/lib/format";
import { isTradingSession } from "@/lib/market-hours";
import { subscribeWatchlist, notifyWatchlistChanged } from "@/lib/watchlist-sync";
import { LAST_SYMBOL_KEY, originLabel, workbenchUrl } from "@/lib/routing";
import type { Quote } from "@/types/market";

const STATUS_LABEL = STREAM_STATUS_LABEL;

/** ?ct= 白名单解析：非法值一律 undefined（回落默认 kline），不抛错。 */
function parseChartTab(v: string | null): ChartTab | undefined {
  return v === "kline" || v === "minute" || v === "flow" ? v : undefined;
}
/** ?rt= 白名单解析：同上。 */
function parseRightTab(v: string | null): RightTab | undefined {
  return v === "book" ||
    v === "trades" ||
    v === "trade" ||
    v === "real" ||
    v === "profile" ||
    v === "info" ||
    v === "speed" ||
    v === "boards"
    ? v
    : undefined;
}

function WorkbenchInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const paramSymbol = sp.get("symbol");
  // 深链 tab（助手一键跳转 / 分享链接）：?ct= 图表区、?rt= 右栏，非法值忽略回落到默认
  const chartTab = parseChartTab(sp.get("ct"));
  const rightTab = parseRightTab(sp.get("rt"));
  const [symbols, setSymbols] = useState<string[]>([]);
  // 真实持仓（CONTEXT.md: Holdings Group）：共用 hook 一份轮询（评审 M3），
  // 标的列表派生进 WS 订阅，「持仓」分类共用
  const { data: realData } = useRealPositions();
  const realSymbols = useMemo(() => realData?.items.map((i) => i.symbol) ?? [], [realData]);
  const [indices, setIndices] = useState<Quote[]>([]);
  const [totalAmount, setTotalAmount] = useState<number | null>(null);
  // 两市成交额 vs 昨日同一时刻增减（亿元）——资金 Tab 顶摘要（2026-09-04）
  const [turnDiff, setTurnDiff] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 以前用 ref 装着再在渲染期读（React 并发渲染下不可靠，且 Next16 的 lint 直接判错）——改为状态
  const [groupMap, setGroupMap] = useState<Record<string, string>>({});
  // 分组清单（持久表 ∪ 成员派生，后端并集）：空分组也可见（评审 A1 分组管理）
  const [allGroupNames, setAllGroupNames] = useState<string[]>(["默认"]);
  const [activeGroup, setActiveGroup] = useState<string>("全部");
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
      const last = typeof window !== "undefined" ? window.sessionStorage.getItem(LAST_SYMBOL_KEY) : null;
      if (last) setSelected(last);
    }
  }

  // 列表消费侧 3s 节流（2026-09-02 用户反馈：1Hz 刷新整表闪烁跳动）。
  // 详情面板是独立 hook 实例（不传 throttleMs），K线/分时合成不受影响。
  // 动态分组标的并入订阅：精选/跟踪标的的行情与自选同源同节奏。
  const { quotes, status } = useQuoteStream(
    [...new Set([...symbols, ...realSymbols, ...picksSymbols, ...topSymbols])],
    { throttleMs: 3000 },
  );
  const [extra, setExtra] = useState<Record<string, Quote>>({});
  // WS 每 5s tick 全量替换 quotes：merged/列表/spark 查找都必须 memo 化，
  // 否则每次 tick 触发整列表 O(n²) 重算（评审 F2）
  const merged: Record<string, Quote> = useMemo(() => ({ ...extra, ...quotes }), [extra, quotes]);
  const [positions, setPositions] = useState<PaperPositionInfo[]>([]);
  const [risk, setRisk] = useState<RiskState | null>(null);

  // 记住最近查看的标的（会话内有效；路由规范见 lib/routing.ts）
  useEffect(() => {
    if (selected) window.sessionStorage.setItem(LAST_SYMBOL_KEY, selected);
  }, [selected]);

  // 渲染兜底：selected 尚未就绪（首帧/回退解析中）时保持原默认标的，避免空 symbol 取数
  const activeSymbol = selected || "600519";

  // 页内切股：URL 是唯一真相源；from 参数（来源页，见 lib/routing.workbenchUrlWithBack）
  // 原样保留——用户切了几只股后「← 返回来源页」入口不能消失
  const switchSymbol = useCallback(
    (s: string) => {
      setSelected(s);
      const back = sp.get("from");
      const url = back ? `${workbenchUrl(s)}&from=${encodeURIComponent(back)}` : workbenchUrl(s);
      router.replace(url, { scroll: false });
    },
    [router, sp],
  );

  // 返回来源页：from 只接受站内绝对路径（防注入），返回即恢复跳转前的 URL（含状态）。
  // 状态保留原理：各功能页的 tab/选中态本来就以 URL query 为真相源，整串带回即可。
  const backFrom = sp.get("from");
  const backLabel = originLabel(backFrom);

  const loadBase = useCallback(async () => {
    try {
      // 原 groups 裸 fetch 从未被消费（gs 解构后无人用）——随收口一并删除
      const [wl, overview, positions, groupNames] = await Promise.all([
        getWatchlist(),
        getMarketOverview(),
        getPaperPositions().catch(() => []),
        getWatchlistGroups().catch(() => [] as string[]),
      ]);
      setGroupMap(Object.fromEntries(wl.map((i) => [i.symbol, i.group_name ?? "默认"])));
      setSymbols(wl.map((i) => i.symbol));
      // 分组清单 = 持久分组表 ∪ 成员派生（后端并集；空分组也可存在，评审 A1）
      setAllGroupNames(["默认", ...groupNames]);
      setIndices(overview.indices);
      setTotalAmount(overview.total_amount);
      setPositions(positions);
      setError(null);
      setUpdatedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    } catch {
      setError("无法连接后端行情服务。请先启动：cd backend && uvicorn app.main:app --reload --port 8000");
    }
  }, []);

  // 风控市场状态（评审 O2）：七档状态变化以日为尺度，独立 30s 轮询——
  // 混在 loadBase 10s 里纯属浪费（state_classifier 本身走 60s 快照聚合）。
  usePollingFetch(async () => {
    const r = await getRiskState().catch(() => null);
    setRisk(r);
  }, 30_000);

  usePollingFetch(loadBase, 10_000);

  // 成交额增减摘要（60s：后端 30s 缓存 + 新浪分钟K 300s 缓存，此频率零额外压力）
  usePollingFetch(async () => {
    const t = await getTurnoverToday().catch(() => null);
    if (t) setTurnDiff(t.diff_yi);
  }, 60_000);

  // 动态分组数据源（60s：每日精选每日级变化、intraday-top 后端 60s 缓存对齐）。
  // 失败保留旧数据（盘中行情仍在跳，下一次轮询补上），不闪空。
  usePollingFetch(async () => {
    const p = await getTodayPicks().catch(() => null);
    if (p) {
      setPicksItems(p.items ?? []);
      setPicksDate(p.date ?? null);
    }
  }, 60_000);
  usePollingFetch(async () => {
    const t = await getIntradayTop().catch(() => null);
    if (t) setTopItems(t.items ?? []);
  }, 60_000);

  // 自选集合变化（search-box 快捷加自选 / 详情面板 ＋自选）→ 立即刷新
  // （2026-09-01 简化：原 CustomEvent 契约改为 lib/watchlist-sync 模块通知）
  useEffect(() => subscribeWatchlist(() => void loadBase()), [loadBase]);

  // 首帧行情兜底：WS 订阅切换窗口期里，动态分组标的与自选一起走 REST 补拉
  const feedSymbols = useMemo(
    () => [...new Set([...symbols, ...picksSymbols, ...topSymbols])],
    [symbols, picksSymbols, topSymbols],
  );
  useEffect(() => {
    if (feedSymbols.length === 0) return;
    const missing = feedSymbols.filter((s) => !(s in merged));
    if (missing.length > 0) {
      getQuotes(missing)
        .then((qs) => setExtra((prev) => Object.fromEntries([...Object.entries(prev), ...qs.map((q) => [q.symbol, q])])))
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedSymbols]);

  // 迷你走势（retro #9）：2026-09-07 起为当日分时价格（period=minute，用户要求
  // 列表迷你图展示当日分钟级走势，而非近 30 日日K 收盘）。盘中 60s 轮询（与
  // 图表校准同节奏）；盘外分时是最近交易日终态——已有数据时跳过拉取（零外呼，
  // 时段判定见 lib/market-hours.ts），首帧/自选集合变化仍无条件立即拉。
  const [sparks, setSparks] = useState<SparklinePayload | null>(null);
  const sparkKey = symbols.join(",");
  // 自选清空 → 渲染期同步清 sparkline 缓存（防上一组残留，adjust-state 模式）
  if (symbols.length === 0 && sparks !== null) {
    setSparks(null);
  }
  // sparkKey 变化 → 立即拉取（.then 回调里 setState，不在 effect 同步路径）
  useEffect(() => {
    if (symbols.length === 0) return;
    getSparklines(symbols, 30, "minute").then(setSparks).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sparkKey]);
  // 盘中 60s 刷新（拉取失败保持旧序列；盘外已有数据时跳过——不重拉不变的数据）
  usePollingFetch(async () => {
    if (sparks && !isTradingSession()) return;
    const s = await getSparklines(symbols, 30, "minute").catch(() => null);
    if (s) setSparks(s);
  }, 60_000);

  // 分组清单由 groupMap 派生（旧代码是独立的 groups 状态，从未被赋值，chips 永远只有「全部」）
  // 「每日精选」「盘中跟踪」是动态分组的保留名，用户分组里排除（防 chips 重名撞 key）
  const groups = useMemo(
    () =>
      allGroupNames
        .filter((g, i) => g !== "默认" && allGroupNames.indexOf(g) === i)
        .filter((g) => g !== "每日精选" && g !== "盘中跟踪")
        .sort(),
    [allGroupNames]
  );
  // 管理模式下分组下拉的可选项（含「默认」兜底）
  const allGroups = useMemo(() => Array.from(new Set(["默认", ...allGroupNames])), [allGroupNames]);
  // 列表派生 memo 化（评审 F2）：WS tick → merged 变化 → 未 memo 时每 tick 重 filter+map
  const watchQuotes: Quote[] = useMemo(
    () =>
      symbols
        .filter((s) => activeGroup === "全部" || groupMap[s] === activeGroup)
        .map((s) => merged[s])
        .filter(Boolean),
    [symbols, activeGroup, groupMap, merged]
  );
  // 「持仓」分类（Holdings Group）：独立于自选，直接列真实持仓标的
  const holdingQuotes: Quote[] = useMemo(
    () => realSymbols.map((s) => merged[s]).filter(Boolean),
    [realSymbols, merged]
  );
  // ── 动态分组视图（2026-09-04）：标的来自系统推荐口径，行数据=行情(merged)×推荐信息 ──
  const picksQuotes: Quote[] = useMemo(
    () => picksSymbols.map((s) => merged[s]).filter(Boolean),
    [picksSymbols, merged]
  );
  const topQuotes: Quote[] = useMemo(
    () => topSymbols.map((s) => merged[s]).filter(Boolean),
    [topSymbols, merged]
  );
  const pickInfoBySymbol = useMemo(() => new Map(picksItems.map((i) => [i.symbol, i])), [picksItems]);
  const topInfoBySymbol = useMemo(() => new Map(topItems.map((i) => [i.symbol, i])), [topItems]);
  // 当前激活视图的行数据（三个特殊视图各走各的数据源）
  const activeRows: Quote[] =
    activeGroup === "持仓" ? holdingQuotes
    : activeGroup === "每日精选" ? picksQuotes
    : activeGroup === "盘中跟踪" ? topQuotes
    : watchQuotes;
  const isDynamicGroup = activeGroup === "每日精选" || activeGroup === "盘中跟踪";
  // spark 数据按 symbol 建索引，行内 O(1) 取（评审 F2：行内 find 是 O(n²)）
  const sparkBySymbol = useMemo(() => {
    const m = new Map<string, number[]>();
    for (const item of sparks?.items ?? []) m.set(item.symbol, item.closes);
    return m;
  }, [sparks]);

  async function remove(symbol: string) {
    try {
      await removeFromWatchlist(symbol);
      setSymbols((prev) => prev.filter((s) => s !== symbol));
    } catch {}
  }

  // ── 自选管理模式（2026-09-01 自选页并入工作台）──────────────
  // 原 /watchlist 页仅剩两项独有能力：手动输代码添加、修改分组——
  // 收进这里后独立页面删除（docs/architecture-redesign.md §一.1.2）。
  const [managing, setManaging] = useState(false);
  const [newSymbol, setNewSymbol] = useState("");
  const [addError, setAddError] = useState<string | null>(null);

  async function addWatch() {
    const s = newSymbol.trim();
    if (!/^\d{6}$/.test(s)) {
      setAddError("请输入 6 位数字代码");
      return;
    }
    try {
      await addToWatchlist(s);
      setNewSymbol("");
      setAddError(null);
      setSymbols((prev) => (prev.includes(s) ? prev : [...prev, s]));
      notifyWatchlistChanged(); // 其他订阅方同步（本页 loadBase 已被订阅回调覆盖）
    } catch {
      setAddError("添加失败，请确认后端已启动");
    }
  }

  async function changeGroup(symbol: string, group: string) {
    try {
      await updateWatchlistGroup(symbol, group);
      setGroupMap((prev) => ({ ...prev, [symbol]: group }));
    } catch {}
  }

  // ── 分组管理（评审 A1：新建 / 重命名 / 删除）────────────────
  // 保护规则在后端（「默认」不可动、重名 409），前端透出错误信息即可。
  async function handleCreateGroup() {
    const name = window.prompt("新建分组名称：");
    if (!name || !name.trim()) return;
    try {
      await createWatchlistGroup(name.trim());
      setActiveGroup(name.trim());
      await loadBase();
    } catch (e) {
      window.alert(`新建失败：${(e as Error).message}`);
    }
  }

  async function handleRenameGroup(oldName: string) {
    const newName = window.prompt(`重命名分组「${oldName}」为：`, oldName);
    if (!newName || !newName.trim() || newName.trim() === oldName) return;
    try {
      await renameWatchlistGroup(oldName, newName.trim());
      if (activeGroup === oldName) setActiveGroup(newName.trim());
      await loadBase();
    } catch (e) {
      window.alert(`重命名失败：${(e as Error).message}`);
    }
  }

  async function handleDeleteGroup(name: string) {
    if (!window.confirm(`删除分组「${name}」？组内成员将回到「默认」。`)) return;
    try {
      await deleteWatchlistGroup(name);
      if (activeGroup === name) setActiveGroup("全部");
      await loadBase();
    } catch (e) {
      window.alert(`删除失败：${(e as Error).message}`);
    }
  }

  return (
    <main className="mx-auto flex h-full w-full max-w-[1600px] flex-col gap-3 px-4 py-3">
      {error && (
        <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-600 dark:text-amber-300">{error}</div>
      )}

      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 text-xs text-zinc-400">
        <span className="flex items-center gap-2">
          {backLabel && backFrom && (
            <button
              onClick={() => router.push(backFrom)}
              className="rounded border border-zinc-300 px-2 py-0.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:border-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
              title={`返回${backLabel}（跳转前状态已保留）`}
            >
              ← 返回{backLabel}
            </button>
          )}
          <button
            onClick={() => router.push("/market?tab=fund")}
            title="查看资金流向详情：实时对比 / 全日估算 / 分钟资金流 / 历史回看"
            className="flex cursor-pointer items-center gap-1.5 rounded hover:text-zinc-900 dark:hover:text-zinc-100"
          >
            两市成交额合计：<span className="font-mono tabular-nums text-zinc-700 dark:text-zinc-200">{totalAmount ? fmtAmount(totalAmount) : "--"}</span>
            {turnDiff != null && (
              <span className={`font-mono text-[11px] tabular-nums ${turnDiff >= 0 ? "text-up" : "text-down"}`}>
                {turnDiff >= 0 ? "+" : ""}
                {turnDiff.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}亿
                <span className="ml-0.5 font-sans text-[10px] text-zinc-400">vs 昨日同时刻</span>
              </span>
            )}
            <span aria-hidden className="text-zinc-300 dark:text-zinc-600">↗</span>
          </button>
        </span>
        <span className="flex items-center gap-3">
          <span>
            行情状态：
            {status === "live" && <span className="pulse-dot mx-1 align-middle" />}
            <span className={STATUS_LABEL[status].cls}>{STATUS_LABEL[status].text}</span>
          </span>
          {risk && (
            <span title={risk.reasons.join("；")} className="cursor-help">
              市场状态：
              <span className={`rounded px-1.5 py-0.5 ${
                risk.state === "强势多头" ? "bg-up/10 text-up" :
                risk.state === "下跌趋势" || risk.state === "恐慌/极端波动" ? "bg-down/10 text-down" :
                "bg-zinc-100 text-zinc-500 dark:bg-zinc-800"
              }`}>
                {risk.state}
              </span>
            </span>
          )}
          <span>指数刷新 {updatedAt || "--"}</span>
          <span className="hidden text-zinc-500 lg:inline">数据仅供投研与模拟交易参考</span>
        </span>
      </div>

      <div className="grid min-h-0 min-w-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[340px,minmax(0,1fr)]">
        <div className="flex min-h-0 min-w-0 flex-col gap-1.5">
        <IndexCards
          indices={indices}
          selected={activeSymbol}
          onSelect={switchSymbol}
        />
        {/* 持仓组（retro #3 遗留）：仅有持仓时渲染，空仓零占用；行点击选中该股 */}
        {positions.length > 0 && (
          <Panel title={`模拟持仓 (${positions.length})`} className="max-h-36 shrink-0 overflow-hidden">
            <table className="w-full text-xs">
              <tbody>
                {positions.map((p) => {
                  const pct = p.pnl_pct;
                  return (
                    <tr
                      key={p.symbol}
                      onClick={() => switchSymbol(p.symbol)}
                      className="cursor-pointer border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900"
                    >
                      <td className="px-3 py-1.5">
                        <span className="font-mono text-[10px] text-zinc-400">{p.symbol}</span>
                        <span className="ml-1.5">{merged[p.symbol]?.name ?? ""}</span>
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono tabular-nums text-zinc-400">{p.quantity}股</td>
                      <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${pct == null ? "text-zinc-500" : pctColor(pct)}`}>
                        {pct == null ? "--" : `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Panel>
        )}
        <Panel
          title={
            activeGroup === "持仓" ? `真实持仓 (${holdingQuotes.length})`
            : activeGroup === "每日精选" ? `每日精选 · ${picksDate ?? "未生成"} (${picksQuotes.length})`
            : activeGroup === "盘中跟踪" ? `盘中跟踪 (${topQuotes.length})`
            : "自选股"
          }
          extra={
            <div className="flex items-center gap-1.5">
              {managing && activeGroup !== "持仓" && (
                <>
                  <input
                    value={newSymbol}
                    onChange={(e) => setNewSymbol(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void addWatch();
                    }}
                    placeholder="代码添加"
                    maxLength={6}
                    aria-label="输入 6 位代码添加自选"
                    className="w-20 rounded border border-zinc-200 bg-transparent px-1.5 py-0.5 font-mono outline-none focus:border-up/60 dark:border-zinc-700"
                  />
                  <button onClick={() => void addWatch()} className="text-up hover:underline">
                    添加
                  </button>
                  {addError && <span className="text-red-400">{addError}</span>}
                </>
              )}
              <button
                onClick={() => {
                  setManaging((v) => !v);
                  setAddError(null);
                }}
                className="text-sky-400 hover:underline"
              >
                {managing ? "完成" : "管理"}
              </button>
            </div>
          }
          className="min-h-0 flex-1 overflow-hidden"
        >
          {/* ── 视图切换 + 分组（M1/A1 2026-09-01）：chips 移入面板内部——
              「全部」是自选股的默认视图而非页面级筛选；持仓与自选分组用
              竖线区隔（持仓不是分组，是真实持仓账本视角）；管理模式下
              提供 新建 / 重命名 / 删除 分组（保护规则在后端）。────── */}
          <div className="sticky top-0 z-10 flex flex-wrap items-center gap-1 border-b border-zinc-100 bg-white/95 px-3 py-1.5 dark:border-zinc-800/60 dark:bg-zinc-950/95">
            {["全部", "持仓", "默认", ...groups, "每日精选", "盘中跟踪"].map((g) => (
              <span key={g} className="flex items-center gap-1">
                {(g === "持仓" || g === "默认" || g === "每日精选") && (
                  <span className="mx-0.5 h-4 w-px bg-zinc-200 dark:bg-zinc-800" aria-hidden />
                )}
                <button
                  onClick={() => setActiveGroup(g)}
                  className={`rounded-full border px-2.5 py-0.5 text-xs ${
                    activeGroup === g ? "border-up/60 bg-up/10 text-up" : "border-zinc-200 text-zinc-400 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100"
                  }`}
                >
                  {g}
                  {g === "持仓" && realSymbols.length > 0 && <span className="ml-1 text-[10px] text-zinc-400">{realSymbols.length}</span>}
                  {g === "每日精选" && picksItems.length > 0 && <span className="ml-1 text-[10px] text-zinc-400">{picksItems.length}</span>}
                  {g === "盘中跟踪" && topItems.length > 0 && <span className="ml-1 text-[10px] text-zinc-400">{topItems.length}</span>}
                </button>
              </span>
            ))}
            {managing && (
              <button
                onClick={() => void handleCreateGroup()}
                title="新建分组"
                className="rounded-full border border-dashed border-zinc-300 px-2 py-0.5 text-xs text-zinc-400 hover:border-up/60 hover:text-up dark:border-zinc-700"
              >
                ＋ 组
              </button>
            )}
            {managing && activeGroup !== "全部" && activeGroup !== "持仓" && activeGroup !== "默认" && !isDynamicGroup && (
              <span className="flex items-center gap-1">
                <span className="mx-0.5 h-4 w-px bg-zinc-200 dark:bg-zinc-800" aria-hidden />
                <button
                  onClick={() => void handleRenameGroup(activeGroup)}
                  title={`重命名分组「${activeGroup}」`}
                  className="rounded px-1 text-xs text-zinc-400 hover:text-sky-400"
                >
                  ✎
                </button>
                <button
                  onClick={() => void handleDeleteGroup(activeGroup)}
                  title={`删除分组「${activeGroup}」`}
                  className="rounded px-1 text-xs text-zinc-400 hover:text-red-400"
                >
                  🗑
                </button>
              </span>
            )}
          </div>
          {activeRows.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-zinc-400">
              {activeGroup === "持仓" ? (
                <>
                  暂无真实持仓。在个股详情页「真实持仓」tab 记一笔买入
                  <br />
                  （按你在券商的实际成交价）。
                </>
              ) : activeGroup === "每日精选" ? (
                <>
                  今日尚无精选组合（每日精选在收盘后生成次日名单）。
                  <br />
                  生成后本组自动同步，无需手动添加。
                </>
              ) : activeGroup === "盘中跟踪" ? (
                <>
                  当前没有满足多维筛选的跟踪标的
                  <br />
                  （确定性/辨识度未达「高」阈值，宁缺毋滥）。
                </>
              ) : (
                <>
                  自选为空或行情未就绪。
                  <br />
                  在顶部搜索框选择结果即可查看并加自选。
                </>
              )}
            </p>
          ) : (
            /* 2026-09-07 文字挤压修复：table-fixed + 明确列宽。此前 auto 布局下
               迷你图(72px)/价格/涨跌/徽标列占满 340px 左栏，名称列被压到 ~51px
               内容区，4 字简称也换行（行高实测撑到 72px）。名称列 auto 吸收剩余
               宽度（340px 下 ≈98px，容纳 5 字简称），超长名 truncate + title 兜底。 */
            <table className="w-full table-fixed text-sm">
              <tbody>
                {activeRows.map((q) => {
                  const pick = activeGroup === "每日精选" ? pickInfoBySymbol.get(q.symbol) : undefined;
                  const top = activeGroup === "盘中跟踪" ? topInfoBySymbol.get(q.symbol) : undefined;
                  return (
                  <tr
                    key={q.symbol}
                    onClick={() => switchSymbol(q.symbol)}
                    className={`cursor-pointer border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900 ${
                      activeSymbol === q.symbol ? "bg-zinc-50 dark:bg-zinc-900" : ""
                    }`}
                  >
                    <td className="min-w-0 px-3 py-2">
                      <div className="truncate font-mono text-xs text-zinc-400">
                        {q.symbol}
                        {pick != null && (
                          <span
                            className="ml-1.5 rounded bg-up/10 px-1 text-[10px] text-up"
                            title={`六维综合评分 ${pick.score}；题材：${pick.themes?.join("、") || "--"}`}
                          >
                            {pick.score.toFixed(0)} 分
                          </span>
                        )}
                        {top != null && (
                          <span
                            className={`ml-1.5 rounded px-1 text-[10px] ${
                              top.tier <= 2 ? "bg-up/10 text-up" : "bg-amber-500/10 text-amber-600 dark:text-amber-400"
                            }`}
                            title={`盘中跟踪 T${top.tier}：${top.pick_basis}；题材 ${top.theme ?? "--"}（${top.stage ?? "?"}）`}
                          >
                            T{top.tier} {top.theme ?? ""}
                          </span>
                        )}
                      </div>
                      <div className="truncate" title={q.name ?? undefined}>{q.name ?? "--"}</div>
                    </td>
                    <td className="hidden w-[52px] px-1 py-2 sm:table-cell" title="当日分时（盘外展示最近交易日）">
                      {pick != null || top != null ? (
                        <span
                          className="block truncate text-[10px] text-zinc-400"
                          title={pick != null ? (pick.echelon_role ?? "") : `确定性 ${(top?.certainty?.level) ?? "?"} · 辨识度 ${(top?.distinctiveness?.level) ?? "?"}`}
                        >
                          {pick != null
                            ? pick.echelon_role ?? ""
                            : `确定性 ${(top?.certainty?.level) ?? "?"} · 辨识度 ${(top?.distinctiveness?.level) ?? "?"}`}
                        </span>
                      ) : managing && activeGroup !== "持仓" ? (
                        <select
                          value={groupMap[q.symbol] ?? "默认"}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => void changeGroup(q.symbol, e.target.value)}
                          className="w-full min-w-0 rounded border border-zinc-200 bg-transparent px-1 py-0.5 text-xs dark:border-zinc-700"
                          aria-label={`修改 ${q.symbol} 分组`}
                        >
                          {allGroups.map((g) => (
                            <option key={g} value={g}>{g}</option>
                          ))}
                        </select>
                      ) : (
                        <Sparkline
                          closes={sparkBySymbol.get(q.symbol) ?? []}
                          up={q.change_pct == null ? undefined : q.change_pct >= 0}
                          width={44}
                        />
                      )}
                    </td>
                    <td className="w-[76px] px-1.5 py-2 text-right font-mono text-xs tabular-nums">
                      {q.price == null ? <span className="font-sans text-zinc-400">未开盘</span> : <PriceFlash value={q.price}>{fmt(q.price)}</PriceFlash>}
                    </td>
                    <td className={`w-[58px] px-1 py-2 text-right font-mono text-xs tabular-nums ${pctColor(q.change_pct)}`}>{pctText(q.change_pct)}</td>
                    <td className="w-[44px] px-0.5 py-2 text-right">{isHardQuality(q.quality) && <QualityBadge quality={q.quality} reasons={q.quality_reasons} />}</td>
                    <td className="w-[22px] pr-1.5 text-right">
                      {!pick && !top && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            void remove(q.symbol);
                          }}
                          className="text-zinc-400 hover:text-red-400"
                          title="移出自选"
                          aria-label={`移出自选 ${q.symbol}`}
                        >
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
                            <path d="M18 6 6 18M6 6l12 12" />
                          </svg>
                        </button>
                      )}
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Panel>
        </div>

        {/* key 随代码变化：切股时整面板重挂载，所有内部状态归零——
            否则 useQuoteStream 订阅切换的窗口期里会残留上一只股票的行情 */}
        <StockDetailPanel
          key={activeSymbol}
          symbol={activeSymbol}
          chartTab={chartTab}
          rightTab={rightTab}
        />
      </div>
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
