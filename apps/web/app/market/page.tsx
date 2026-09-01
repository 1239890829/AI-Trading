"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Panel } from "@/components/panel";
import { QualityBadge } from "@/components/quality-badge";
import { EventPanel } from "@/components/event-panel";
import { HeatmapTab } from "@/components/market/heatmap-tab";
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
import type { LimitUpRecord, Quote } from "@/types/market";

/**
 * 市场页（2026-09-01 系统重构）：总览（情绪/宽度/事件）+ 云图 两个视图。
 * 云图无独立数据源（复用市场快照，纯视图），故并入本页为 tab 而非一级导航
 * （docs/architecture-redesign.md §一.1.2 减负原则 2）。
 *
 * 布局 v2（2026-09-01 用户反馈）：成交额/涨停速览/事件驱动曾被顶部 shrink-0
 * 区块挤成只剩标题栏。重排：顶部指标带紧凑化（指数卡 4 行→3 行、宽度卡压扁、
 * 情绪条与情绪序列合并一块），中部三板块给保底 min-h + 固定 flex 比例，
 * 整页 overflow-y-auto 兜底（小视口可滚动，板块永不被压没）。
 */

const PHASE_STYLE: Record<string, string> = {
  冰点: "bg-sky-500/15 text-sky-700 border-sky-500/40 dark:text-sky-300",
  修复: "bg-teal-500/15 text-teal-700 border-teal-500/40 dark:text-teal-300",
  发酵: "bg-amber-500/15 text-amber-700 border-amber-500/40 dark:text-amber-300",
  高潮: "bg-up/20 text-up border-up/50",
  分歧: "bg-orange-500/15 text-orange-700 border-orange-500/40 dark:text-orange-300",
  退潮: "bg-down/20 text-down border-down/50",
};

const VIEWS = [
  { key: "overview", label: "总览" },
  { key: "heatmap", label: "云图" },
] as const;

type ViewKey = (typeof VIEWS)[number]["key"];

function MarketInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const view: ViewKey = sp.get("tab") === "heatmap" ? "heatmap" : "overview";

  const [indices, setIndices] = useState<Quote[]>([]);
  const [totalAmount, setTotalAmount] = useState<number | null>(null);
  const [pool, setPool] = useState<LimitUpRecord[]>([]);
  const [breadth, setBreadth] = useState<Breadth | null>(null);
  const [sent, setSent] = useState<Sentiment | null>(null);
  const [sentHist, setSentHist] = useState<SentimentHistoryPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState("");

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

  useEffect(() => {
    void loadFast();
    const t = setInterval(loadFast, 10000);
    return () => clearInterval(t);
  }, [loadFast]);

  useEffect(() => {
    void loadSlow();
    const t = setInterval(loadSlow, 30000);
    return () => clearInterval(t);
  }, [loadSlow]);

  // 历史序列变化慢（日频），独立 60s 轮询，不跟随 10s 行情刷新
  useEffect(() => {
    void getSentimentHistory(10).then(setSentHist).catch(() => {});
    const t = setInterval(() => void getSentimentHistory(10).then(setSentHist).catch(() => {}), 60000);
    return () => clearInterval(t);
  }, []);

  const sh = indices.find((q) => q.market === "SH" && q.symbol === "000001");
  const staticNote =
    "市场宽度已上线（涨跌/涨停卡片，全市场快照实时计算）；情绪周期判定见情绪面板，炸板率精细化在后续版本提供。本页所有数据均标注来源与数据时间；免费数据源失败时接口返回 502，前端展示错误态，不伪造实时数据。";
  const breadthNote = breadth
    ? `宽度口径：全市场快照价格法（${breadth.total} 只）；涨停 ${breadth.limit_up} 只含收盘贴板未封住者，权威封板口径见盘面页涨停生态 tab（东财）。两市总额（含北交所）${fmtAmount(breadth.total_amount)}。`
    : "";

  function switchView(k: ViewKey) {
    const p = new URLSearchParams(sp.toString());
    p.set("tab", k);
    router.replace(`/market?${p.toString()}`, { scroll: false });
  }

  return (
    <main className="mx-auto flex h-full w-full max-w-[1600px] flex-col gap-3 overflow-y-auto px-4 py-3">
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
                    : "text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                }`}
              >
                {v.label}
              </button>
            ))}
          </nav>
        </div>
        {view === "overview" && <span className="text-xs text-zinc-400">更新 {updatedAt || "--"}</span>}
      </div>

      {view === "heatmap" ? (
        <div className="min-h-0 flex-1">
          <HeatmapTab />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          {error && (
            <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-600 dark:text-amber-300">
              {error}
            </div>
          )}

          {/* 指数带：紧凑 3 行（名称+质量 / 价格+涨跌幅 / 成交额） */}
          <div className="grid shrink-0 grid-cols-3 gap-3 md:grid-cols-6">
            {indices.map((q) => (
              <div key={q.symbol} className="rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800">
                <div className="flex items-center justify-between gap-1">
                  <span className="truncate text-xs text-zinc-400">{q.name ?? q.symbol}</span>
                  <QualityBadge quality={q.quality} reasons={q.quality_reasons} />
                </div>
                <div className="mt-0.5 flex items-baseline justify-between gap-2">
                  {q.price == null ? (
                    <span className="text-xs text-zinc-400">未开盘</span>
                  ) : (
                    <>
                      <span className="font-mono text-lg font-semibold">{fmt(q.price)}</span>
                      <span className={`shrink-0 font-mono text-xs ${pctColor(q.change_pct)}`}>{pctText(q.change_pct)}</span>
                    </>
                  )}
                </div>
                <div className="font-mono text-[11px] text-zinc-500" title="成交额">
                  额 {fmtAmount(q.amount)}
                </div>
              </div>
            ))}
          </div>

          {/* 宽度带：压扁为两行小卡 */}
          <div className="grid shrink-0 grid-cols-3 gap-3 lg:grid-cols-6">
            {[
              ["上涨", breadth?.up, "text-up"],
              ["下跌", breadth?.down, "text-down"],
              ["涨停", breadth?.limit_up, "text-up"],
              ["跌停", breadth?.limit_down, "text-down"],
              ["平盘/停牌", breadth ? `${breadth.flat}/${breadth.suspended}` : null, ""],
              ["沪深京总数", breadth?.total, ""],
            ].map(([label, value, cls]) => (
              <div key={String(label)} className="rounded-xl border border-zinc-200 px-3 py-1.5 dark:border-zinc-800">
                <div className="text-[11px] text-zinc-400">{label}</div>
                <div className={`font-mono text-base font-semibold ${cls}`}>{value ?? "--"}</div>
              </div>
            ))}
          </div>

          {/* 情绪合并卡：左相位/温度/指标，右近 10 日序列柱状（原两块 shrink-0 合一，省一行高度） */}
          {sent && (
            <div className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-zinc-200 px-4 py-2.5 dark:border-zinc-800">
              <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1.5">
                <span className={`rounded-md border px-2.5 py-1 text-sm font-semibold ${PHASE_STYLE[sent.phase] ?? ""}`}>
                  {sent.phase}
                </span>
                <span className="text-xs text-zinc-400">
                  情绪温度 <span className="font-mono text-base font-semibold text-zinc-900 dark:text-zinc-100">{sent.temperature}</span>/100
                </span>
                <span className="text-xs text-zinc-400">置信度 {sent.confidence}</span>
                <span className="text-xs text-zinc-400">
                  {sent.indicators.slice(0, 6).map((i) => `${i.name} ${i.value ?? "--"}`).join(" · ")}
                </span>
                <span
                  className="max-w-[280px] truncate text-xs text-zinc-500"
                  title={`${sent.reasons.join("；")}｜误判：${sent.misjudge_caveats.join("；")}｜切换：${sent.switch_conditions}`}
                >
                  判定依据：{sent.reasons[0]}…
                </span>
              </div>
              {sentHist && sentHist.items.length > 0 && (
                <div className="ml-auto flex items-end gap-2" title={(() => {
                  const cy = sentHist.cycle;
                  return cy.start_date
                    ? `本轮自 ${cy.start_date} 起（${cy.start_phase ?? ""}→${sentHist.items[sentHist.items.length - 1]?.phase}），已持续 ${cy.days} 日`
                    : "近 10 日情绪序列";
                })()}>
                  <span className="mb-1 whitespace-nowrap text-[10px] leading-tight text-zinc-500">
                    近{sentHist.items.length}日
                    <br />
                    情绪
                  </span>
                  <div className="flex items-end gap-1.5">
                    {sentHist.items.map((h) => {
                      const t = h.temperature ?? 0;
                      const height = 6 + Math.round((t / 100) * 30);
                      const color =
                        t >= 75 ? "bg-red-500/70" : t >= 60 ? "bg-amber-500/70" : t >= 45 ? "bg-zinc-500/60" : "bg-sky-500/70";
                      return (
                        <div
                          key={h.trade_date}
                          className="flex w-7 flex-col items-center gap-0.5"
                          title={`${h.trade_date}｜${h.phase}｜温度 ${t ?? "--"}｜置信 ${h.confidence ?? "--"}｜${h.source === "review" ? "复盘" : "实时"}`}
                        >
                          <span className="text-[10px] tabular-nums text-zinc-400">{t ? Math.round(t) : "--"}</span>
                          <div className={`w-full rounded-t ${color}`} style={{ height }} />
                          <span className="text-[9px] text-zinc-500">{h.trade_date.slice(4, 6)}/{h.trade_date.slice(6, 8)}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* 中部：成交额 1/3 + 涨停速览 2/3（min-h 保底，flex-[3] 优先撑高让 10 行表格尽量完整） */}
          <div className="grid min-h-[240px] flex-[3] gap-3 lg:grid-cols-[minmax(260px,1fr)_2fr]">
            <Panel title="两市成交额" className="min-h-0 overflow-hidden" source={sh?.source} dataTimestamp={sh?.data_timestamp}>
              <div className="flex h-full flex-col justify-center px-4 py-4">
                <p className="font-mono text-4xl font-semibold tracking-tight">{totalAmount ? fmtAmount(totalAmount) : "--"}</p>
                <p className="mt-3 text-xs leading-relaxed text-zinc-400">
                  沪深京两市合计（含北交所）。历史趋势图随 Parquet 快照数据积累逐步提供。
                </p>
              </div>
            </Panel>

            <Panel
              title="涨停速览（今日连板前列）"
              source={pool[0]?.source}
              className="min-h-0 overflow-hidden"
              extra={
                <Link href="/tape?tab=limitup" className="text-zinc-400 transition-colors hover:text-zinc-900 dark:hover:text-zinc-100">
                  全部 ↗
                </Link>
              }
            >
              {pool.length > 0 ? (
                <table className="w-full text-sm">
                  <tbody>
                    {pool.map((r) => (
                      <tr key={r.symbol} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                        <td className="px-3 py-2 font-mono text-xs text-zinc-400">{r.symbol}</td>
                        <td className="px-2 py-2">{r.name}</td>
                        <td className="px-2 py-2 text-right font-mono">{fmt(r.price)}</td>
                        <td className={`px-2 py-2 text-right font-mono ${pctColor(r.change_pct)}`}>{pctText(r.change_pct)}</td>
                        <td className="px-3 py-2 text-right text-xs text-zinc-400">{r.boards_stat ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="px-4 py-8 text-center text-sm text-zinc-400">今日暂无涨停数据（或非交易日）</p>
              )}
            </Panel>
          </div>

          {/* 事件驱动（E1⑥/E2）：事件 → 方向 → 标的池 → 题材/详情联动；全宽 + min-h 保底 */}
          <div className="min-h-[240px] flex-[2] overflow-hidden">
            <EventPanel />
          </div>

          <p
            className="shrink-0 truncate rounded-lg border border-zinc-200 px-4 py-2 text-xs text-zinc-400 dark:border-zinc-800"
            title={`${breadthNote}${staticNote}`}
          >
            说明：{breadthNote}
            {staticNote}
          </p>
        </div>
      )}
    </main>
  );
}

export default function MarketPage() {
  return (
    <Suspense fallback={<main className="p-6 text-sm text-zinc-400">加载中…</main>}>
      <MarketInner />
    </Suspense>
  );
}
