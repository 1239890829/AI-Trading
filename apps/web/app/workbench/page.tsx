"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Panel } from "@/components/panel";
import { IndexCards } from "@/components/index-cards";
import { StockDetailPanel } from "@/components/stock-detail";
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { Sparkline } from "@/components/sparkline";
import { useQuoteStream, StreamStatus } from "@/hooks/use-quote-stream";
import {
  getMarketOverview,
  getPaperPositions,
  getQuotes,
  getRiskState,
  getSparklines,
  getWatchlist,
  removeFromWatchlist,
  type PaperPositionInfo,
  type RiskState,
  type SparklinePayload,
} from "@/lib/api";
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
import { LAST_SYMBOL_KEY, workbenchUrl } from "@/lib/routing";
import type { Quote } from "@/types/market";

const STATUS_LABEL: Record<StreamStatus, { text: string; cls: string }> = {
  connecting: { text: "连接中", cls: "text-zinc-400" },
  live: { text: "WS 实时推送", cls: "text-sky-400" },
  polling: { text: "WS 断线 · REST 轮询", cls: "text-amber-400" },
  error: { text: "连接失败", cls: "text-red-400" },
};

function WorkbenchInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const paramSymbol = sp.get("symbol");
  const [symbols, setSymbols] = useState<string[]>([]);
  const [indices, setIndices] = useState<Quote[]>([]);
  const [totalAmount, setTotalAmount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 以前用 ref 装着再在渲染期读（React 并发渲染下不可靠，且 Next16 的 lint 直接判错）——改为状态
  const [groupMap, setGroupMap] = useState<Record<string, string>>({});
  const [activeGroup, setActiveGroup] = useState<string>("全部");
  const [updatedAt, setUpdatedAt] = useState<string>("");
  // 选中标的：URL 参数是唯一真相源；无参数时回退「上次查看的标的」，
  // 都没有再落默认 600519（此前硬编码回退是跨页面联动 bug 的一半根因）
  const [selected, setSelected] = useState<string>(paramSymbol ?? "");

  const { quotes, status } = useQuoteStream(symbols);
  const [extra, setExtra] = useState<Record<string, Quote>>({});
  const merged: Record<string, Quote> = { ...extra, ...quotes };
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
      const [wl, overview, positions, riskState] = await Promise.all([
        getWatchlist(),
        getMarketOverview(),
        getPaperPositions().catch(() => []),
        getRiskState().catch(() => null),
      ]);
      setGroupMap(Object.fromEntries(wl.map((i) => [i.symbol, i.group_name ?? "默认"])));
      setSymbols(wl.map((i) => i.symbol));
      setIndices(overview.indices);
      setTotalAmount(overview.total_amount);
      setPositions(positions);
      setRisk(riskState);
      setError(null);
      setUpdatedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    } catch {
      setError("无法连接后端行情服务。请先启动：cd backend && uvicorn app.main:app --reload --port 8000");
    }
  }, []);

  useEffect(() => {
    void loadBase();
    const t = setInterval(loadBase, 10000);
    return () => clearInterval(t);
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
  const groups = useMemo(() => Array.from(new Set(Object.values(groupMap))).sort(), [groupMap]);
  const watchQuotes: Quote[] = symbols
    .filter((s) => activeGroup === "全部" || groupMap[s] === activeGroup)
    .map((s) => merged[s])
    .filter(Boolean);

  async function remove(symbol: string) {
    try {
      await removeFromWatchlist(symbol);
      setSymbols((prev) => prev.filter((s) => s !== symbol));
    } catch {}
  }

  return (
    <main className="mx-auto flex h-full w-full max-w-[1600px] flex-col gap-3 px-4 py-3">
      {error && (
        <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-600 dark:text-amber-300">{error}</div>
      )}

      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 text-xs text-zinc-400">
        <span>
          两市成交额合计：<span className="font-mono tabular-nums text-zinc-200">{fmtAmount(totalAmount)}</span>
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
        <div className="flex shrink-0 flex-wrap gap-1">
          {["全部", ...groups.filter((g) => g !== "默认")].map((g) => (
            <button
              key={g}
              onClick={() => setActiveGroup(g)}
              className={`rounded-full border px-2.5 py-0.5 text-xs ${
                activeGroup === g ? "border-up/60 bg-up/10 text-up" : "border-zinc-200 text-zinc-400 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100"
              }`}
            >
              {g}
            </button>
          ))}
        </div>
        <Panel
          title="自选股"
          extra={
            <Link href="/watchlist" className="text-sky-400 hover:underline">
              管理
            </Link>
          }
          className="min-h-0 flex-1 overflow-hidden"
        >
          {watchQuotes.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-zinc-400">
              自选为空或行情未就绪。
              <br />
              在顶部搜索框选择结果即可查看并加自选。
            </p>
          ) : (
            <table className="w-full text-sm">
              <tbody>
                {watchQuotes.map((q) => (
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
                      <Sparkline closes={sparks?.items.find((i) => i.symbol === q.symbol)?.closes ?? []} />
                    </td>
                    <td className="px-2 py-2 text-right font-mono tabular-nums"><PriceFlash value={q.price}>{fmt(q.price)}</PriceFlash></td>
                    <td className={`px-2 py-2 text-right font-mono text-xs tabular-nums ${pctColor(q.change_pct)}`}>{pctText(q.change_pct)}</td>
                    <td className="hidden px-2 py-2 text-right font-mono text-xs tabular-nums text-zinc-400 md:table-cell">{fmtAmount(q.amount)}</td>
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
