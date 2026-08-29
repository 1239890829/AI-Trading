"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Panel } from "@/components/panel";
import { IndexCards } from "@/components/index-cards";
import { StockDetailPanel } from "@/components/stock-detail";
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { useQuoteStream, StreamStatus } from "@/hooks/use-quote-stream";
import { getMarketOverview, getQuotes, removeFromWatchlist } from "@/lib/api";
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
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
  const groupMapRef = useRef<Record<string, string>>({});
  const [groups, setGroups] = useState<string[]>([]);
  const [activeGroup, setActiveGroup] = useState<string>("全部");
  const [updatedAt, setUpdatedAt] = useState<string>("");
  const [selected, setSelected] = useState<string>(paramSymbol ?? "600519");

  const { quotes, status } = useQuoteStream(symbols);
  const [extra, setExtra] = useState<Record<string, Quote>>({});
  const merged: Record<string, Quote> = { ...extra, ...quotes };

  useEffect(() => {
    if (paramSymbol) setSelected(paramSymbol);
  }, [paramSymbol]);

  const loadBase = useCallback(async () => {
    try {
      const [wl, overview, gs] = await Promise.all([
        fetch(`${process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000"}/api/watchlist`, { cache: "no-store" }).then((r) => r.json()),
        getMarketOverview(),
        fetch(`${process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000"}/api/watchlist/groups`, { cache: "no-store" }).then((r) => r.json()).catch(() => ({ data: [] })),
      ]);
      groupMapRef.current = Object.fromEntries(
        (wl.data as { symbol: string; group_name?: string }[]).map((i) => [i.symbol, i.group_name ?? "默认"])
      );
      setSymbols((wl.data as { symbol: string }[]).map((i) => i.symbol));
      setIndices(overview.indices);
      setTotalAmount(overview.total_amount);
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

  const groupMap = groupMapRef.current;
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
          <span>指数刷新 {updatedAt || "--"}</span>
          <span className="hidden text-zinc-500 lg:inline">数据仅供投研与模拟交易参考</span>
        </span>
      </div>

      <div className="grid min-h-0 min-w-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[340px,minmax(0,1fr)]">
        <div className="flex min-h-0 min-w-0 flex-col gap-1.5">
        <IndexCards indices={indices} />
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
                      router.replace(`/workbench?symbol=${q.symbol}`, { scroll: false });
                    }}
                    className={`cursor-pointer border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900 ${
                      selected === q.symbol ? "bg-zinc-50 dark:bg-zinc-900" : ""
                    }`}
                  >
                    <td className="px-3 py-2">
                      <div className="font-mono text-xs text-zinc-400">{q.symbol}</div>
                      <div>{q.name ?? "--"}</div>
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
        <StockDetailPanel key={selected} symbol={selected} />
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
