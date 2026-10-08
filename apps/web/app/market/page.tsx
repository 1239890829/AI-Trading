"use client";

import Link from "next/link";
import { Suspense, useCallback, useState } from "react";
import { MarketLensPicker } from "@/components/ui/workspace-deck";
import { useSearchParams } from "next/navigation";
import { Panel } from "@/components/panel";
import { QualityBadge } from "@/components/quality-badge";
import { EventPanel } from "@/components/event-panel";
import { HeatmapTab } from "@/components/market/heatmap-tab";
import { EventsTab } from "@/components/market/events-tab";
import { FundTab } from "@/components/market/fund-tab";
import { indexDetailSymbol } from "@/lib/api";
import { tapeUrl } from "@/lib/routing";
import { marketLensUrl } from "@/lib/workspace-tools";
import { useSymbolDetail } from "@/components/detail/symbol-detail-context";
import {
  getBreadth,
  getLimitUpPool,
  getMarketOverview,
  getSentiment,
  getSentimentHistory,
  type Breadth,
  type Freshness,
  type Sentiment,
  type SentimentHistoryPayload,
} from "@/lib/api";
import { fmt, fmtAmount, pctColor, pctText, sourceLabel, timeText, triAmount } from "@/lib/format";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { FadeSwap, PageSkeletonFallback, Skeleton } from "@/components/ui/loading";
import type { LimitUpRecord, Quote } from "@/types/market";
import "./market-bc.css";

/**
 * Market owns its data polling; the lens only changes presentation and route.
 * The B+C layout groups environment readings above the pool and event columns.
 * At narrow/short viewports the route container scrolls; body never does.
 */

const PHASE_STYLE: Record<string, string> = {
  冰点: "bg-sky-500/15 text-sky-700 border-sky-500/40 dark:text-sky-300",
  修复: "bg-teal-500/15 text-teal-700 border-teal-500/40 dark:text-teal-300",
  发酵: "bg-amber-500/15 text-amber-800 border-amber-500/40 dark:text-amber-300",
  高潮: "bg-up/20 text-up-ink dark:text-up border-up/50",
  分歧: "bg-orange-500/15 text-orange-800 border-orange-500/40 dark:text-orange-300",
  退潮: "bg-down/20 text-down-ink dark:text-down border-down/50",
};

type ViewKey = "overview" | "fund" | "heatmap" | "events";

function MarketInner() {
  // 个股/指数详情**就地弹窗**（2026-09-15）：指数卡与标的池行不再跳工作台
  const { open: openSymbolDetail } = useSymbolDetail();
  const sp = useSearchParams();
  const raw = sp.get("tab");
  const view: ViewKey =
    raw === "heatmap" || raw === "events" || raw === "fund" ? raw : "overview";

  const [indices, setIndices] = useState<Quote[]>([]);
  const [totalAmount, setTotalAmount] = useState<number | null>(null);
  // 成交额的三态判据（S2-1 契约）。**必须与数值分开存**：`null` 有「上游尚未就绪」
  // 与「真的没有数据」两种成因，只凭数值无法区分（2026-09-14 报障根因）。
  const [amountFreshness, setAmountFreshness] = useState<Freshness | null>(null);
  const [poolError, setPoolError] = useState(false);
  const [contextError, setContextError] = useState(false);
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
      const [overview, zt] = await Promise.allSettled([getMarketOverview(), getLimitUpPool()]);
      if (overview.status === "fulfilled") {
        setIndices(overview.value.indices);
        setTotalAmount(overview.value.total_amount);
        setAmountFreshness(overview.value.total_amount_freshness);
        setError(null);
        setUpdatedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
      } else setError("市场概览读取失败，保留值仅作上次结果参考。请重试或核对系统维护状态。");
      setPoolError(zt.status === "rejected");
      if (zt.status === "fulfilled") setPool(zt.value.slice(0, 10));
    } catch {
      setError("市场概览读取失败，请重试或核对系统维护状态。");
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
      setContextError(breadthRes === null || sentRes === null);
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
  const search = sp.toString();
  // Summary shortcuts keep the same date/object/return identity as the lens picker.
  const lensHref = (href: string) => marketLensUrl(href, search);

  return (
    <main data-workspace="market" className="task-page bc-market-page mx-auto flex h-full w-full max-w-[1600px] flex-col overflow-hidden">
      <header className="workspace-masthead bc-market-masthead">
        <div><h1>市场</h1><p className="workspace-kicker">先看环境，再核对资金、题材与事件</p></div>
        <div className="workspace-context">
          {view === "overview" && <span className="bc-market-read-time">最近读取 {updatedAt || "--"}</span>}
          <MarketLensPicker selected={view} search={search} />
        </div>
      </header>

      <FadeSwap swapKey={view} className={`task-scroll bc-market-view min-h-0 flex-1 bc-market-view-${view}`}>
        {view === "heatmap" ? <HeatmapTab /> : view === "events" ? <EventsTab /> : view === "fund" ? <FundTab /> : (
          <div className="bc-market-overview">
            {(error || contextError) && <div className="bc-market-notices">
              {error && <p role="alert">{error}</p>}
              {contextError && <p role="status">宽度或情绪来源未就绪，缺项不能解读为零或健康。</p>}
            </div>}

            <section className="bc-market-indices" aria-label="主要指数">
              {indices.length === 0 && pending ? Array.from({length: 6}, (_, index) => (
                <div className="bc-index-skeleton" key={index}><Skeleton className="h-3 w-16" /><Skeleton className="mt-2 h-5 w-20" /></div>
              )) : indices.length === 0 ? <p className="bc-market-empty">暂无可用指数。请核对数据源状态。</p> : indices.map(q => (
                <button type="button" className="bc-index-quote" key={`${q.market}-${q.symbol}`}
                  onClick={() => openSymbolDetail({symbol: indexDetailSymbol(q.symbol, q.market)})}
                  title={`查看 ${q.name ?? q.symbol} 指数详情${q.quality_reasons?.length ? "｜" + q.quality_reasons.join("；") : ""}`}>
                  <span className="bc-index-name"><span>{q.name ?? q.symbol}</span><QualityBadge quality={q.quality} reasons={q.quality_reasons} /></span>
                  <span className="bc-index-value"><strong>{q.price == null ? "未开盘" : fmt(q.price)}</strong><span className={pctColor(q.change_pct)}>{pctText(q.change_pct)}</span></span>
                  <span className="bc-index-amount">成交额 {fmtAmount(q.amount)}</span>
                </button>
              ))}
            </section>

            <section className="bc-market-environment" aria-label="成交与市场宽度">
              <div className="bc-market-turnover">
                <div className="bc-market-turnover-head"><span>沪深京成交额</span><Link href={lensHref("/market?tab=fund")} className="bc-market-text-action">资金详情<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10" /></svg></Link></div>
                <strong title={amountFreshness?.reason ?? undefined}>{triAmount(totalAmount, amountFreshness?.state)}</strong>
                <span className="bc-market-turnover-source">{amountFreshness?.source ? sourceLabel(amountFreshness.source) : sh?.source ? sourceLabel(sh.source) : "来源未提供"}{amountFreshness?.as_of ? ` ${timeText(amountFreshness.as_of)}` : sh?.data_timestamp ? ` ${timeText(sh.data_timestamp)}` : ""}</span>
              </div>
              <dl className="bc-market-breadth">
                {([
                  ["上涨", breadth?.up, "text-up-ink dark:text-up", null],
                  ["下跌", breadth?.down, "text-down-ink dark:text-down", null],
                  ["涨停", breadth?.limit_up, "text-up-ink dark:text-up", tapeUrl("limitup")],
                  ["跌停", breadth?.limit_down, "text-down-ink dark:text-down", tapeUrl("limitdown")],
                  ["平盘 / 停牌", breadth ? `${breadth.flat} / ${breadth.suspended}` : null, "", null],
                  ["沪深京总数", breadth?.total, "", null],
                ] as [string, string | number | null | undefined, string, string | null][]).map(([label, value, cls, href]) => {
                  const valueContent = value == null && pending ? <Skeleton className="h-4 w-12" /> : value ?? "--";
                  return <div key={label}><dt>{label}</dt><dd className={cls}>{href ? <Link href={lensHref(href)} aria-label={`查看${label}池明细：${value ?? "未提供"}`} title={`查看${label}池明细`}>{valueContent}<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10" /></svg></Link> : valueContent}</dd></div>;
                })}
              </dl>
            </section>

            {sent ? (
              <section className="bc-market-sentiment" aria-label="市场情绪与判定依据">
                <div className="bc-market-sentiment-reading">
                  <span className={`bc-market-phase ${PHASE_STYLE[sent.phase] ?? ""}`}>{sent.phase}</span>
                  <span>情绪温度 <strong>{sent.temperature}</strong><small> / 100</small></span>
                  <span className="bc-market-confidence">置信度 {sent.confidence}</span>
                  <details className="sentiment-basis bc-market-basis"><summary>依据与失效条件</summary><div>
                    <p><strong>依据</strong> {sent.reasons.join("；") || "未提供"}</p>
                    <p><strong>误判风险</strong> {sent.misjudge_caveats.join("；") || "未提供"}</p>
                    <p><strong>切换条件</strong> {sent.switch_conditions || "未提供"}</p>
                    {sent.indicators.length > 0 && <p><strong>指标</strong> {sent.indicators.slice(0, 6).map(indicator => `${indicator.name} ${indicator.value ?? "--"}`).join("；")}</p>}
                  </div></details>
                </div>
                {sentHist && sentHist.items.length > 0 && <div className="bc-market-cycle" title={sentHist.cycle.start_date ? `本轮自 ${sentHist.cycle.start_date} 起（${sentHist.cycle.start_phase ?? ""}→${sentHist.items[sentHist.items.length - 1]?.phase}），已持续 ${sentHist.cycle.days} 日` : `近 ${sentHist.items.length} 日情绪序列`}>
                  <span>近 {sentHist.items.length} 日</span><div className="bc-market-history">{sentHist.items.map(history => {
                    const temperature = history.temperature;
                    const height = temperature == null ? 3 : 5 + Math.round((temperature / 100) * 22);
                    const color = temperature == null ? "bg-zinc-500/30" : temperature >= 75 ? "bg-red-500/70" : temperature >= 60 ? "bg-amber-500/70" : temperature >= 45 ? "bg-zinc-500/60" : "bg-sky-500/70";
                    return <div key={history.trade_date} title={`${history.trade_date}｜${history.phase}｜温度 ${temperature ?? "--"}｜置信 ${history.confidence ?? "--"}｜${history.source === "review" ? "复盘" : "实时"}`}><span>{temperature == null ? "--" : Math.round(temperature)}</span><i className={color} style={{height}} /></div>;
                  })}</div>
                </div>}
              </section>
            ) : pending ? <div className="bc-market-sentiment"><Skeleton className="h-5 w-14" /><Skeleton className="h-4 w-40" /><Skeleton className="h-4 w-24" /></div> : null}

            <div className="bc-market-reading">
              <Panel title="涨停前列" source={pool[0]?.source} className="bc-market-pool" bodyClassName="bc-market-pool-scroll" extra={<Link href={lensHref(tapeUrl("limitup"))} className="bc-market-text-action">查看全池<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10" /></svg></Link>}>
                {poolError && <p role="alert" className="bc-market-inline-warning">涨停速览读取失败。{pool.length ? "下面是上次读取结果。" : "不能据此判断没有涨停。"}</p>}
                {pool.length > 0 ? <table className="bc-market-pool-table">
                  <caption className="sr-only">今日涨停池连板前列，点击证券查看详情</caption>
                  <thead><tr><th scope="col">证券</th><th scope="col">最新价</th><th scope="col">涨跌幅</th><th scope="col">连板记录</th></tr></thead>
                  <tbody>{pool.map(record => <tr key={record.symbol} onClick={event => {event.currentTarget.querySelector("button")?.focus(); openSymbolDetail({symbol: record.symbol});}} title="查看个股详情">
                    <td><button type="button" className="market-stock-open bc-market-stock" aria-label={`查看 ${record.name ?? record.symbol} 详情`} onClick={event => {event.stopPropagation(); openSymbolDetail({symbol: record.symbol});}}><span>{record.name ?? record.symbol}</span><small>{record.symbol}</small></button></td>
                    <td>{fmt(record.price)}</td><td className={pctColor(record.change_pct)}>{pctText(record.change_pct)}</td><td>{record.boards_stat ?? "--"}</td>
                  </tr>)}</tbody>
                </table> : pending ? <div className="bc-market-pool-loading">{Array.from({length: 5}, (_, index) => <div key={index}><Skeleton className="h-4 w-20" /><Skeleton className="h-4 w-14" /><Skeleton className="h-4 w-14" /></div>)}</div> : poolError ? null : <p className="bc-market-empty">已读取，当前涨停池为空。日期与覆盖以来源为准。</p>}
              </Panel>
              <div className="bc-market-events"><EventPanel /></div>
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
