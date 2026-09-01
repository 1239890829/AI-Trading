"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Panel } from "@/components/panel";
import { IndexCards } from "@/components/index-cards";
import { StockDetailPanel } from "@/components/stock-detail";
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { Sparkline } from "@/components/sparkline";
import { useQuoteStream, StreamStatus } from "@/hooks/use-quote-stream";
import { useRealPositions } from "@/hooks/use-real-positions";
import {
  addToWatchlist,
  createWatchlistGroup,
  deleteWatchlistGroup,
  getMarketOverview,
  getPaperPositions,
  getQuotes,
  getRiskState,
  getSparklines,
  getWatchlist,
  getWatchlistGroups,
  removeFromWatchlist,
  renameWatchlistGroup,
  updateWatchlistGroup,
  type PaperPositionInfo,
  type RiskState,
  type SparklinePayload,
} from "@/lib/api";
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
import { APP_EVENTS, emitAppEvent, onAppEvent } from "@/lib/events";
import { LAST_SYMBOL_KEY, workbenchUrl } from "@/lib/routing";
import type { Quote } from "@/types/market";

const STATUS_LABEL: Record<StreamStatus, { text: string; cls: string }> = {
  connecting: { text: "连接中", cls: "text-zinc-400" },
  live: { text: "WS 实时推送", cls: "text-sky-400" },
  polling: { text: "WS 断线 · REST 轮询", cls: "text-amber-400" },
  closed: { text: "休市 · 展示最近交易日数据", cls: "text-zinc-400" },
  stale: { text: "数据过期 · 后端刷新异常", cls: "text-amber-400" },
  error: { text: "连接失败", cls: "text-red-400" },
};

function WorkbenchInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const paramSymbol = sp.get("symbol");
  const [symbols, setSymbols] = useState<string[]>([]);
  // 真实持仓（CONTEXT.md: Holdings Group）：共用 hook 一份轮询（评审 M3），
  // 标的列表派生进 WS 订阅，「持仓」分类共用
  const { data: realData } = useRealPositions();
  const realSymbols = useMemo(() => realData?.items.map((i) => i.symbol) ?? [], [realData]);
  const [indices, setIndices] = useState<Quote[]>([]);
  const [totalAmount, setTotalAmount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 以前用 ref 装着再在渲染期读（React 并发渲染下不可靠，且 Next16 的 lint 直接判错）——改为状态
  const [groupMap, setGroupMap] = useState<Record<string, string>>({});
  // 分组清单（持久表 ∪ 成员派生，后端并集）：空分组也可见（评审 A1 分组管理）
  const [allGroupNames, setAllGroupNames] = useState<string[]>(["默认"]);
  const [activeGroup, setActiveGroup] = useState<string>("全部");
  const [updatedAt, setUpdatedAt] = useState<string>("");
  // 选中标的：URL 参数是唯一真相源；无参数时回退「上次查看的标的」，
  // 都没有再落默认 600519（此前硬编码回退是跨页面联动 bug 的一半根因）
  const [selected, setSelected] = useState<string>(paramSymbol ?? "");

  const { quotes, status } = useQuoteStream([...new Set([...symbols, ...realSymbols])]);
  const [extra, setExtra] = useState<Record<string, Quote>>({});
  // WS 每 5s tick 全量替换 quotes：merged/列表/spark 查找都必须 memo 化，
  // 否则每次 tick 触发整列表 O(n²) 重算（评审 F2）
  const merged: Record<string, Quote> = useMemo(() => ({ ...extra, ...quotes }), [extra, quotes]);
  const [positions, setPositions] = useState<PaperPositionInfo[]>([]);
  const [risk, setRisk] = useState<RiskState | null>(null);

  useEffect(() => {
    if (paramSymbol) {
      setSelected(paramSymbol);
      return;
    }
    // 无参数进入（导航栏点「工作台」）：恢复上次查看的标的，避免永远回到默认股
    const last = window.sessionStorage.getItem(LAST_SYMBOL_KEY);
    if (last) setSelected(last);
  }, [paramSymbol]);

  // 记住最近查看的标的（会话内有效；路由规范见 lib/routing.ts）
  useEffect(() => {
    if (selected) window.sessionStorage.setItem(LAST_SYMBOL_KEY, selected);
  }, [selected]);

  // 渲染兜底：selected 尚未就绪（首帧/回退解析中）时保持原默认标的，避免空 symbol 取数
  const activeSymbol = selected || "600519";

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
  useEffect(() => {
    let alive = true;
    const loadRisk = async () => {
      const r = await getRiskState().catch(() => null);
      if (alive) setRisk(r);
    };
    void loadRisk();
    const t = setInterval(loadRisk, 30_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    void loadBase();
    const t = setInterval(loadBase, 10000);
    // 修复 2026-09-01 实锤断点（架构方案 P1）：search-box 快捷加自选会派发
    // watchlist-changed，但此前全站无监听方——加自选后左栏要等 10s 轮询才出现。
    return onAppEvent(APP_EVENTS.watchlistChanged, () => void loadBase());
  }, [loadBase]);

  useEffect(() => {
    if (symbols.length === 0) return;
    const missing = symbols.filter((s) => !(s in merged));
    if (missing.length > 0) {
      getQuotes(missing)
        .then((qs) => setExtra((prev) => Object.fromEntries([...Object.entries(prev), ...qs.map((q) => [q.symbol, q])])))
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbols]);

  // 迷你走势（retro #9）：日K 级别，随自选变化拉取，独立 5 分钟刷新
  const [sparks, setSparks] = useState<SparklinePayload | null>(null);
  const sparkKey = symbols.join(",");
  useEffect(() => {
    if (symbols.length === 0) {
      setSparks(null);
      return;
    }
    void getSparklines(symbols, 30).then(setSparks).catch(() => {});
    const t = setInterval(() => void getSparklines(symbols, 30).then(setSparks).catch(() => {}), 300_000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sparkKey]);

  // 分组清单由 groupMap 派生（旧代码是独立的 groups 状态，从未被赋值，chips 永远只有「全部」）
  const groups = useMemo(
    () => allGroupNames.filter((g, i) => g !== "默认" && allGroupNames.indexOf(g) === i).sort(),
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
      emitAppEvent(APP_EVENTS.watchlistChanged); // 写操作必广播（lib/events.ts 契约）
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
        <span>
          两市成交额合计：<span className="font-mono tabular-nums text-zinc-200">{totalAmount ? fmtAmount(totalAmount) : "--"}</span>
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
          onSelect={(s) => {
            setSelected(s);
            router.replace(workbenchUrl(s), { scroll: false });
          }}
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
                      onClick={() => {
                        setSelected(p.symbol);
                        router.replace(workbenchUrl(p.symbol), { scroll: false });
                      }}
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
          title={activeGroup === "持仓" ? `真实持仓 (${holdingQuotes.length})` : "自选股"}
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
            {["全部", "持仓", "默认", ...groups].map((g, idx) => (
              <span key={g} className="flex items-center gap-1">
                {(idx === 1 || idx === 2) && <span className="mx-0.5 h-4 w-px bg-zinc-200 dark:bg-zinc-800" aria-hidden />}
                <button
                  onClick={() => setActiveGroup(g)}
                  className={`rounded-full border px-2.5 py-0.5 text-xs ${
                    activeGroup === g ? "border-up/60 bg-up/10 text-up" : "border-zinc-200 text-zinc-400 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100"
                  }`}
                >
                  {g}
                  {g === "持仓" && realSymbols.length > 0 && <span className="ml-1 text-[10px] text-zinc-400">{realSymbols.length}</span>}
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
            {managing && activeGroup !== "全部" && activeGroup !== "持仓" && activeGroup !== "默认" && (
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
          {(activeGroup === "持仓" ? holdingQuotes : watchQuotes).length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-zinc-400">
              {activeGroup === "持仓" ? (
                <>
                  暂无真实持仓。在个股详情页「真实持仓」tab 记一笔买入
                  <br />
                  （按你在券商的实际成交价）。
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
            <table className="w-full text-sm">
              <tbody>
                {(activeGroup === "持仓" ? holdingQuotes : watchQuotes).map((q) => (
                  <tr
                    key={q.symbol}
                    onClick={() => {
                      setSelected(q.symbol);
                      router.replace(workbenchUrl(q.symbol), { scroll: false });
                    }}
                    className={`cursor-pointer border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900 ${
                      activeSymbol === q.symbol ? "bg-zinc-50 dark:bg-zinc-900" : ""
                    }`}
                  >
                    <td className="px-3 py-2">
                      <div className="font-mono text-xs text-zinc-400">{q.symbol}</div>
                      <div>{q.name ?? "--"}</div>
                    </td>
                    <td className="hidden px-1 py-2 sm:table-cell">
                      {managing && activeGroup !== "持仓" ? (
                        <select
                          value={groupMap[q.symbol] ?? "默认"}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => void changeGroup(q.symbol, e.target.value)}
                          className="rounded border border-zinc-200 bg-transparent px-1 py-0.5 text-xs dark:border-zinc-700"
                          aria-label={`修改 ${q.symbol} 分组`}
                        >
                          {allGroups.map((g) => (
                            <option key={g} value={g}>{g}</option>
                          ))}
                        </select>
                      ) : (
                        <Sparkline closes={sparkBySymbol.get(q.symbol) ?? []} />
                      )}
                    </td>
                    <td className="px-2 py-2 text-right font-mono tabular-nums">
                      {q.price == null ? <span className="text-xs font-sans text-zinc-400">未开盘</span> : <PriceFlash value={q.price}>{fmt(q.price)}</PriceFlash>}
                    </td>
                    <td className={`px-2 py-2 text-right font-mono text-xs tabular-nums ${pctColor(q.change_pct)}`}>{pctText(q.change_pct)}</td>
                    <td className="px-1 py-2 text-right">{q.quality !== "high" && <QualityBadge quality={q.quality} reasons={q.quality_reasons} />}</td>
                    <td className="pr-2 text-right">
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
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
        </div>

        {/* key 随代码变化：切股时整面板重挂载，所有内部状态归零——
            否则 useQuoteStream 订阅切换的窗口期里会残留上一只股票的行情 */}
        <StockDetailPanel key={activeSymbol} symbol={activeSymbol} />
      </div>
    </main>
  );
}

export default function WorkbenchPage() {
  return (
    <Suspense fallback={<main className="p-6 text-sm text-zinc-400">加载中…</main>}>
      <WorkbenchInner />
    </Suspense>
  );
}
