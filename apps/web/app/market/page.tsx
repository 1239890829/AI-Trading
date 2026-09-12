"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Panel } from "@/components/panel";
import { QualityBadge } from "@/components/quality-badge";
import { EventPanel } from "@/components/event-panel";
import { HeatmapTab } from "@/components/market/heatmap-tab";
import { EventsTab } from "@/components/market/events-tab";
import { FundTab } from "@/components/market/fund-tab";
import { indexDetailSymbol } from "@/lib/api";
import { tapeUrl, workbenchUrlWithBack } from "@/lib/routing";
import {
  getBreadth,
  getLimitUpPool,
  getMarketOverview,
  getSentiment,
  getSentimentHistory,
  type Breadth,
  type Sentiment,
  type SentimentHistoryPayload,
} from "@/lib/api";
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { FadeSwap, PageSkeletonFallback, Skeleton } from "@/components/ui/loading";
import type { LimitUpRecord, Quote } from "@/types/market";

/**
 * 市场页（2026-09-01 系统重构）：总览（情绪/宽度/事件）+ 云图 两个视图。
 * 云图无独立数据源（复用市场快照，纯视图），故并入本页为 tab 而非一级导航
 * （docs/archive/architecture-redesign.md §一.1.2 减负原则 2）。
 *
 * 布局 v3（2026-09-01 用户要求）：**一屏完整展示，严禁页面级滚动**——
 * 顶部指标带全部紧凑化（指数卡 2 行、宽度卡 py-1、情绪卡降高、低价值说明行
 * 删除），中部成交额+涨停速览与事件驱动按 flex 比例吸收剩余高度（各带
 * min-h 保底），内容超长只在面板内部滚动。633px 小视口实测也无需页面滚动。
 */

const PHASE_STYLE: Record<string, string> = {
  冰点: "bg-sky-500/15 text-sky-700 border-sky-500/40 dark:text-sky-300",
  修复: "bg-teal-500/15 text-teal-700 border-teal-500/40 dark:text-teal-300",
  发酵: "bg-amber-500/15 text-amber-800 border-amber-500/40 dark:text-amber-300",
  高潮: "bg-up/20 text-up-ink dark:text-up border-up/50",
  分歧: "bg-orange-500/15 text-orange-800 border-orange-500/40 dark:text-orange-300",
  退潮: "bg-down/20 text-down-ink dark:text-down border-down/50",
};

const VIEWS = [
  { key: "overview", label: "总览" },
  { key: "fund", label: "资金" },
  { key: "heatmap", label: "云图" },
  { key: "events", label: "事件" },
] as const;

type ViewKey = (typeof VIEWS)[number]["key"];

function MarketInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const raw = sp.get("tab");
  const view: ViewKey =
    raw === "heatmap" || raw === "events" || raw === "fund" ? raw : "overview";

  const [indices, setIndices] = useState<Quote[]>([]);
  const [totalAmount, setTotalAmount] = useState<number | null>(null);
  const [pool, setPool] = useState<LimitUpRecord[]>([]);
  const [breadth, setBreadth] = useState<Breadth | null>(null);
  const [sent, setSent] = useState<Sentiment | null>(null);
  const [sentHist, setSentHist] = useState<SentimentHistoryPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState("");
  // 首轮加载在途：区分「加载中」（骨架占位）与「确认无数据」（空态文案），
  // 避免内容加载完成后整块突然出现（2026-09-04 统一加载体验）。
  const [pending, setPending] = useState(true);

  // 快慢轮询拆分（评审 O2，2026-09-01）：指数/涨停速览是盘中变量保 10s；
  // 宽度/情绪是准日频聚合（后端 60s 缓存 + 全市场快照），10s 拉属于浪费 → 30s；
  // 情绪历史序列本来就是日频 → 60s。
  const loadFast = useCallback(async () => {
    try {
      const [overview, zt] = await Promise.all([
        getMarketOverview(),
        getLimitUpPool().catch(() => [] as LimitUpRecord[]),
      ]);
      setIndices(overview.indices);
      setTotalAmount(overview.total_amount);
      setPool(zt.slice(0, 10));
      setError(null);
      setUpdatedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    } catch {
      setError("无法连接后端行情服务（启动方式见工作台页提示）。");
    } finally {
      setPending(false);
    }
  }, []);

  const loadSlow = useCallback(async () => {
    try {
      const [breadthRes, sentRes] = await Promise.all([
        getBreadth().catch(() => null),
        getSentiment().catch(() => null),
      ]);
      setBreadth(breadthRes);
      setSent(sentRes);
    } catch {}
  }, []);

  usePollingFetch(loadFast, 10_000);
  usePollingFetch(loadSlow, 30_000);

  // 历史序列变化慢（日频），独立 60s 轮询，不跟随 10s 行情刷新
  usePollingFetch(async () => {
    const h = await getSentimentHistory(10).catch(() => null);
    if (h) setSentHist(h); // 拉取失败保持上一次序列（原 .catch(()=>{}) 语义）
  }, 60_000);

  const sh = indices.find((q) => q.market === "SH" && q.symbol === "000001");

  function switchView(k: ViewKey) {
    const p = new URLSearchParams(sp.toString());
    p.set("tab", k);
    router.replace(`/market?${p.toString()}`, { scroll: false });
  }

  return (
    <main className="mx-auto flex h-full w-full max-w-[1600px] flex-col gap-2 overflow-hidden px-4 py-3">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-4">
          <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">市场</h1>
          <nav className="flex items-center gap-1" aria-label="市场视图">
            {VIEWS.map((v) => (
              <button
                key={v.key}
                onClick={() => switchView(v.key)}
                aria-current={view === v.key ? "page" : undefined}
                className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                  view === v.key
                    ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                }`}
              >
                {v.label}
              </button>
            ))}
          </nav>
        </div>
        {view === "overview" && <span className="text-xs text-zinc-600 dark:text-zinc-400">更新 {updatedAt || "--"}</span>}
      </div>

      {/* 视图切换统一 fade 过渡（2026-09-04）：h-full 保持各视图内部布局 */}
      <FadeSwap swapKey={view} className="min-h-0 flex-1">
        {view === "heatmap" ? (
          <div className="h-full">
            <HeatmapTab />
          </div>
        ) : view === "events" ? (
          <div className="h-full">
            <EventsTab />
          </div>
        ) : view === "fund" ? (
          <div className="h-full">
            <FundTab />
          </div>
        ) : (
          <div className="flex h-full min-h-0 flex-col gap-2">
          {error && (
            <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-800 dark:text-amber-300">
              {error}
            </div>
          )}

          {/* 指数带：紧凑 2 行（名称+质量+涨跌幅 / 价格+成交额）。
              联动切片 F（L 指数入口）：点击 → 工作台指数详情（带前缀规范形态），
              带 from 返回——指数与个股同一跳转纪律，不裸拼 URL。 */}
          <div className="grid shrink-0 grid-cols-3 gap-2 md:grid-cols-6">
            {indices.length === 0 && pending
              ? Array.from({ length: 6 }, (_, i) => (
                  <div key={i} className="rounded-lg border border-zinc-200 px-2.5 py-1.5 dark:border-zinc-800">
                    <Skeleton className="h-3 w-16" />
                    <Skeleton className="mt-1.5 h-5 w-20" />
                  </div>
                ))
              : indices.map((q) => (
              <button
                key={q.symbol}
                onClick={() => router.push(workbenchUrlWithBack(indexDetailSymbol(q.symbol, q.market)))}
                title={`查看 ${q.name ?? q.symbol} 指数详情${q.quality_reasons?.length ? "｜" + q.quality_reasons.join("；") : ""}`}
                className="cursor-pointer rounded-lg border border-zinc-200 px-2.5 py-1.5 text-left transition-colors hover:bg-zinc-100/60 dark:border-zinc-800 dark:hover:bg-zinc-800/40"
              >
                <div className="flex items-center justify-between gap-1">
                  <span className="truncate text-xs text-zinc-600 dark:text-zinc-400">{q.name ?? q.symbol}</span>
                  <span className="flex items-center gap-1">
                    <QualityBadge quality={q.quality} reasons={q.quality_reasons} />
                    <span className={`shrink-0 font-mono text-xs ${pctColor(q.change_pct)}`}>{pctText(q.change_pct)}</span>
                  </span>
                </div>
                <div className="mt-0.5 flex items-baseline justify-between gap-2">
                  <span className="font-mono text-base font-semibold">{q.price == null ? "未开盘" : fmt(q.price)}</span>
                  <span className="shrink-0 font-mono text-[10px] text-zinc-600 dark:text-zinc-400" title="成交额">
                    额 {fmtAmount(q.amount)}
                  </span>
                </div>
              </button>
            ))}
          </div>

          {/* 宽度带：未就绪时同构骨架占位（label 已知，只对数值位骨架）。
              涨停/跌停两格可点击 → 盘面页对应 tab（2026-09-04 联动，tapeUrl 统一构造） */}
          <div className="grid shrink-0 grid-cols-3 gap-2 lg:grid-cols-6">
            {([
              ["上涨", breadth?.up, "text-up-ink dark:text-up", null],
              ["下跌", breadth?.down, "text-down-ink dark:text-down", null],
              ["涨停", breadth?.limit_up, "text-up-ink dark:text-up", tapeUrl("limitup")],
              ["跌停", breadth?.limit_down, "text-down-ink dark:text-down", tapeUrl("limitdown")],
              ["平盘/停牌", breadth ? `${breadth.flat}/${breadth.suspended}` : null, "", null],
              ["沪深京总数", breadth?.total, "", null],
            ] as [string, string | number | null | undefined, string, string | null][]).map(([label, value, cls, href]) =>
              href ? (
                <Link
                  key={String(label)}
                  href={href}
                  title={`查看${label}池明细（盘面页 · ${label === "涨停" ? "涨停生态" : "跌停"} tab）`}
                  className="cursor-pointer rounded-lg border border-zinc-200 px-2.5 py-1 transition-colors hover:bg-zinc-100/60 dark:border-zinc-800 dark:hover:bg-zinc-800/40"
                >
                  <span className="text-[11px] text-zinc-600 dark:text-zinc-400">{label}</span>
                  {value == null && pending ? (
                    <Skeleton className="mt-0.5 h-4 w-14" />
                  ) : (
                    <div className={`font-mono text-sm font-semibold ${cls}`}>
                      {value ?? "--"}
                      <span className="ml-1 text-[10px] font-normal text-zinc-600 dark:text-zinc-400">↗</span>
                    </div>
                  )}
                </Link>
              ) : (
                <div key={String(label)} className="rounded-lg border border-zinc-200 px-2.5 py-1 dark:border-zinc-800">
                  <span className="text-[11px] text-zinc-600 dark:text-zinc-400">{label}</span>
                  {value == null && pending ? (
                    <Skeleton className="mt-0.5 h-4 w-14" />
                  ) : (
                    <div className={`font-mono text-sm font-semibold ${cls}`}>{value ?? "--"}</div>
                  )}
                </div>
              )
            )}
          </div>

          {/* 情绪合并卡：左相位/温度/指标，右近 10 日序列柱状（紧凑高度）；未就绪时单行骨架 */}
          {sent ? (
            <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-1.5 rounded-lg border border-zinc-200 px-3.5 py-1.5 dark:border-zinc-800">
              <div className="flex min-w-0 flex-wrap items-center gap-x-3.5 gap-y-1">
                <span className={`rounded-md border px-2 py-0.5 text-xs font-semibold ${PHASE_STYLE[sent.phase] ?? ""}`}>
                  {sent.phase}
                </span>
                <span className="text-xs text-zinc-600 dark:text-zinc-400">
                  情绪温度 <span className="font-mono text-sm font-semibold text-zinc-900 dark:text-zinc-100">{sent.temperature}</span>/100
                </span>
                <span className="text-xs text-zinc-600 dark:text-zinc-400">置信度 {sent.confidence}</span>
                <span className="hidden text-xs text-zinc-600 dark:text-zinc-400 xl:inline">
                  {sent.indicators.slice(0, 6).map((i) => `${i.name} ${i.value ?? "--"}`).join(" · ")}
                </span>
                <span
                  className="max-w-[260px] truncate text-xs text-zinc-600 dark:text-zinc-400"
                  title={`${sent.reasons.join("；")}｜误判：${sent.misjudge_caveats.join("；")}｜切换：${sent.switch_conditions}`}
                >
                  判定依据：{sent.reasons[0]}…
                </span>
              </div>
              {sentHist && sentHist.items.length > 0 && (
                <div
                  className="ml-auto flex items-end gap-1.5"
                  title={(() => {
                    const cy = sentHist.cycle;
                    return cy.start_date
                      ? `近 ${sentHist.items.length} 日情绪序列；本轮自 ${cy.start_date} 起（${cy.start_phase ?? ""}→${sentHist.items[sentHist.items.length - 1]?.phase}），已持续 ${cy.days} 日`
                      : `近 ${sentHist.items.length} 日情绪序列`;
                  })()}
                >
                  {sentHist.items.map((h) => {
                    const t = h.temperature ?? 0;
                    const height = 5 + Math.round((t / 100) * 22);
                    const color =
                      t >= 75 ? "bg-red-500/70" : t >= 60 ? "bg-amber-500/70" : t >= 45 ? "bg-zinc-500/60" : "bg-sky-500/70";
                    return (
                      <div
                        key={h.trade_date}
                        className="flex w-6 flex-col items-center gap-px"
                        title={`${h.trade_date}｜${h.phase}｜温度 ${t ?? "--"}｜置信 ${h.confidence ?? "--"}｜${h.source === "review" ? "复盘" : "实时"}`}
                      >
                        <span className="text-[9px] tabular-nums text-zinc-600 dark:text-zinc-400">{t ? Math.round(t) : "--"}</span>
                        <div className={`w-full rounded-t ${color}`} style={{ height }} />
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ) : pending ? (
            <div className="flex shrink-0 items-center gap-4 rounded-lg border border-zinc-200 px-3.5 py-2 dark:border-zinc-800">
              <Skeleton className="h-5 w-14 rounded-md" />
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-4 w-24" />
              <Skeleton className="hidden h-4 w-72 xl:block" />
            </div>
          ) : null}

          {/* 中部：成交额 1/3 + 涨停速览 2/3（flex-[5] 优先撑高；表格超高时面板内滚动） */}
          <div className="grid min-h-[168px] flex-[5] gap-2 lg:grid-cols-[minmax(250px,1fr)_2fr]">
            <Panel title="两市成交额" className="min-h-0 overflow-hidden" source={sh?.source} dataTimestamp={sh?.data_timestamp}
              extra={<Link href="/market?tab=fund" className="text-zinc-600 dark:text-zinc-400 transition-colors hover:text-zinc-900 dark:hover:text-zinc-100">资金详情 ↗</Link>}>
              <div className="flex h-full flex-col justify-center px-4 py-3">
                <p className="font-mono text-3xl font-semibold tracking-tight">{totalAmount ? fmtAmount(totalAmount) : "--"}</p>
                <p className="mt-2 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">
                  沪深京两市合计（含北交所）。实时对比/全日估算/分钟资金流见「资金」Tab。
                </p>
              </div>
            </Panel>

            <Panel
              title="涨停速览（今日连板前列）"
              source={pool[0]?.source}
              className="min-h-0 overflow-hidden"
              extra={
                <Link href={tapeUrl("limitup")} className="text-zinc-600 dark:text-zinc-400 transition-colors hover:text-zinc-900 dark:hover:text-zinc-100">
                  全部 ↗
                </Link>
              }
            >
              {pool.length > 0 ? (
                <table className="w-full text-sm">
                  <tbody>
                    {pool.map((r) => (
                      <tr
                        key={r.symbol}
                        onClick={() => router.push(workbenchUrlWithBack(r.symbol))}
                        title="查看个股详情"
                        className="cursor-pointer border-b border-zinc-100 last:border-0 transition-colors hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900/60"
                      >
                        <td className="px-3 py-1.5 font-mono text-xs text-zinc-600 dark:text-zinc-400">{r.symbol}</td>
                        <td className="px-2 py-1.5">{r.name}</td>
                        <td className="px-2 py-1.5 text-right font-mono">{fmt(r.price)}</td>
                        <td className={`px-2 py-1.5 text-right font-mono ${pctColor(r.change_pct)}`}>{pctText(r.change_pct)}</td>
                        <td className="px-3 py-1.5 text-right text-xs text-zinc-600 dark:text-zinc-400">{r.boards_stat ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : pending ? (
                <div className="space-y-2.5 px-3 py-3">
                  {Array.from({ length: 5 }, (_, i) => (
                    <div key={i} className="flex items-center gap-3">
                      <Skeleton className="h-3.5 w-14" />
                      <Skeleton className="h-3.5 w-20" />
                      <Skeleton className="ml-auto h-3.5 w-14" />
                    </div>
                  ))}
                </div>
              ) : (
                <p className="px-4 py-6 text-center text-sm text-zinc-600 dark:text-zinc-400">今日暂无涨停数据（或非交易日）</p>
              )}
            </Panel>
          </div>

          {/* 事件驱动（E1⑥/E2）：全宽 + flex-[4]；列表超长时面板内部滚动 */}
          <div className="min-h-[148px] flex-[4] overflow-hidden">
            <EventPanel />
          </div>
        </div>
        )}
      </FadeSwap>
    </main>
  );
}

export default function MarketPage() {
  return (
    <Suspense fallback={<PageSkeletonFallback label="市场页加载中" />}>
      <MarketInner />
    </Suspense>
  );
}
