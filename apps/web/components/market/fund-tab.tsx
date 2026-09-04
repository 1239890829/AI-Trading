"use client";

import { useCallback, useMemo, useState } from "react";
import { Panel } from "@/components/panel";
import { Skeleton } from "@/components/ui/loading";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { pctColor, pctText } from "@/lib/format";
import {
  getFundFlowHistory,
  getFundFlowIntraday,
  getFundFlowRealtime,
  getTurnoverDay,
  getTurnoverHistory,
  getTurnoverToday,
  type FlowHistoryDay,
  type FlowIntradayPoint,
  type FlowTier,
  type FundFlowRealtime,
  type TurnoverDayCompare,
  type TurnoverHistoryDay,
  type TurnoverToday,
} from "@/lib/api";

/**
 * 市场页「资金」Tab（2026-09-04）：
 * 1. 两市成交额：实时 + 昨日同一时刻增减（亿元）+ 按时间进度/昨日分布估算全日
 * 2. 实时五档资金净额（主力/超大/大/中/小单——实时全市场源无机构/游资拆分，不臆造）
 * 3. 分钟级资金流累计曲线（东财延迟 ~15 分钟口径，图例标注）
 * 4. 历史回看：成交额近 10 日 + 点击切换日对比；主力净额近 20 日
 *
 * 颜色纪律（A 股惯例）：净流入=红、净流出=绿；各档资金用固定色区分（图例见各卡）。
 */

const TIER_META: { key: keyof FlowTier; label: string; color: string; desc: string }[] = [
  { key: "main", label: "主力", color: "#dc2626", desc: "主力净额（超大单+大单）" },
  { key: "super_", label: "超大单", color: "#9f1239", desc: "超大单净额" },
  { key: "big", label: "大单", color: "#f87171", desc: "大单净额" },
  { key: "mid", label: "中单", color: "#16a34a", desc: "中单净额" },
  { key: "small", label: "小单", color: "#4ade80", desc: "小单净额" },
];

function fmtYi(v: number | null | undefined, digits = 0): string {
  if (v == null) return "--";
  return v.toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function signedFmt(v: number | null): string {
  if (v == null) return "--";
  return `${v >= 0 ? "+" : ""}${fmtYi(v, 1)}亿`;
}

/** HH:MM → 交易分钟序（0..240），与后端 _sina_bar_seq 同口径。 */
function hmToSeq(t: string): number {
  const [h, m] = t.split(":").map(Number);
  const hm = h * 60 + m;
  if (hm <= 570) return 0;
  if (hm <= 690) return hm - 570;
  return Math.min(240, 120 + (hm - 780));
}

/** 累计成交额对比小图：今日（红实线） vs 昨日（灰虚线）。 */
function CumCompareChart({ today, prev }: { today: { t: string; cum: number }[]; prev: { t: string; cum: number }[] }) {
  const W = 320;
  const H = 96;
  const max = Math.max(1, ...today.map((p) => p.cum), ...prev.map((p) => p.cum));
  const toPath = (pts: { t: string; cum: number }[]) =>
    pts
      .map((p, i) => `${i === 0 ? "M" : "L"}${((hmToSeq(p.t) / 240) * W).toFixed(1)},${(H - (p.cum / max) * (H - 6) - 3).toFixed(1)}`)
      .join(" ");
  if (today.length === 0 && prev.length === 0) return null;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full" role="img" aria-label="今日与昨日分时累计成交额对比">
      <line x1="0" y1={H - 3} x2={W} y2={H - 3} stroke="currentColor" className="text-zinc-200 dark:text-zinc-800" strokeWidth="1" />
      {prev.length > 0 && <path d={toPath(prev)} fill="none" stroke="#a1a1aa" strokeWidth="1.2" strokeDasharray="3 3" />}
      {today.length > 0 && <path d={toPath(today)} fill="none" stroke="#dc2626" strokeWidth="1.6" />}
      <text x="2" y="10" fontSize="9" fill="#a1a1aa">{`max ${fmtYi(max)}亿`}</text>
    </svg>
  );
}

/** 五档净额 diverging 条形（实时卡用）。 */
function TierBars({ tier, maxAbs }: { tier: FlowTier; maxAbs: number }) {
  return (
    <div className="space-y-1.5">
      {TIER_META.map((m) => {
        const v = tier[m.key];
        const w = v == null || maxAbs === 0 ? 0 : Math.max(2, (Math.abs(v) / maxAbs) * 50);
        const pos = v != null && v >= 0;
        return (
          <div key={m.key} className="flex items-center gap-2 text-xs" title={m.desc}>
            <span className="w-11 shrink-0 text-right text-zinc-400">{m.label}</span>
            <div className="relative h-3.5 flex-1 overflow-hidden rounded-sm bg-zinc-100 dark:bg-zinc-800/60">
              <div className="absolute inset-y-0 left-1/2 w-px bg-zinc-300 dark:bg-zinc-600" />
              {v != null && (
                <div
                  className="absolute inset-y-0"
                  style={{
                    left: pos ? "50%" : `${50 - w}%`,
                    width: `${w}%`,
                    backgroundColor: m.color,
                    opacity: 0.85,
                  }}
                />
              )}
            </div>
            <span className={`w-16 shrink-0 text-right font-mono tabular-nums ${v == null ? "text-zinc-400" : pos ? "text-up" : "text-down"}`}>
              {signedFmt(v)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** 分钟级五档资金流累计曲线（多线 SVG，图例区分）。 */
function FlowIntradayChart({ items }: { items: FlowIntradayPoint[] }) {
  const W = 800;
  const H = 150;
  const maxAbs = Math.max(
    1,
    ...items.flatMap((p) => TIER_META.map((m) => Math.abs(p[m.key] ?? 0))),
  );
  const yOf = (v: number) => H / 2 - (v / maxAbs) * (H / 2 - 8);
  const toPath = (key: keyof FlowTier) =>
    items
      .map((p, i) => {
        const v = p[key];
        if (v == null) return "";
        return `${i === 0 || items[i - 1][key] == null ? "M" : "L"}${((hmToSeq(p.t) / 240) * W).toFixed(1)},${yOf(v).toFixed(1)}`;
      })
      .join(" ");
  if (items.length === 0) return null;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="分钟级五档资金流累计曲线">
      <line x1="0" y1={H / 2} x2={W} y2={H / 2} stroke="currentColor" className="text-zinc-300 dark:text-zinc-700" strokeWidth="1" strokeDasharray="2 3" />
      {TIER_META.map((m) => (
        <path key={m.key} d={toPath(m.key)} fill="none" stroke={m.color} strokeWidth={m.key === "main" ? 2 : 1.1} opacity={m.key === "main" ? 1 : 0.8} />
      ))}
      {[570, 690, 780, 900].map((hm) => (
        <text key={hm} x={((hm - 570) / 240) * W} y={H - 1} fontSize="9" fill="#a1a1aa" textAnchor={hm === 570 ? "start" : hm === 900 ? "end" : "middle"}>
          {`${String(Math.floor(hm / 60)).padStart(2, "0")}:${String(hm % 60).padStart(2, "0")}`}
        </text>
      ))}
    </svg>
  );
}

/** 主力净额近 N 日 diverging 柱（hover 出五档明细）。 */
function MainFlowHistoryChart({ days }: { days: FlowHistoryDay[] }) {
  const maxAbs = Math.max(1, ...days.map((d) => Math.abs(d.main ?? 0)));
  return (
    <div className="flex h-28 items-stretch gap-1.5" data-testid="main-flow-history">
      {days.map((d) => {
        const v = d.main;
        const h = v == null ? 0 : Math.max(2, (Math.abs(v) / maxAbs) * 46);
        const tip = `主力 ${signedFmt(v)}｜超大 ${signedFmt(d.super_)}｜大 ${signedFmt(d.big)}｜中 ${signedFmt(d.mid)}｜小 ${signedFmt(d.small)}`;
        return (
          <div key={d.date} className="flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5" title={tip}>
            {v != null && v >= 0 && <div className="w-full rounded-t bg-up/80" style={{ height: h }} />}
            <div className="h-px w-full bg-zinc-300 dark:bg-zinc-700" />
            {v != null && v < 0 && <div className="w-full rounded-b bg-down/80" style={{ height: h }} />}
            <span className="w-full truncate text-center text-[9px] text-zinc-400">{d.date.slice(5)}</span>
          </div>
        );
      })}
    </div>
  );
}

export function FundTab() {
  const [turnover, setTurnover] = useState<TurnoverToday | null>(null);
  const [turnoverPending, setTurnoverPending] = useState(true);
  const [flowRt, setFlowRt] = useState<FundFlowRealtime | null>(null);
  const [flowRtPending, setFlowRtPending] = useState(true);
  const [intraday, setIntraday] = useState<FlowIntradayPoint[]>([]);
  const [intradayDegraded, setIntradayDegraded] = useState<string[]>([]);
  const [intradayPending, setIntradayPending] = useState(true);
  const [flowHist, setFlowHist] = useState<FlowHistoryDay[]>([]);
  const [flowHistDegraded, setFlowHistDegraded] = useState<string[]>([]);
  const [turnHist, setTurnHist] = useState<TurnoverHistoryDay[]>([]);
  const [turnHistDegraded, setTurnHistDegraded] = useState<string[]>([]);
  const [selDay, setSelDay] = useState<string | null>(null);
  const [dayCompare, setDayCompare] = useState<TurnoverDayCompare | null>(null);

  usePollingFetch(async () => {
    try {
      setTurnover(await getTurnoverToday());
    } catch {}
    finally {
      setTurnoverPending(false);
    }
  }, 30_000);

  usePollingFetch(async () => {
    try {
      setFlowRt(await getFundFlowRealtime());
    } catch {}
    finally {
      setFlowRtPending(false);
    }
  }, 30_000);

  usePollingFetch(async () => {
    try {
      const d = await getFundFlowIntraday();
      setIntraday(d.items);
      setIntradayDegraded(d.degraded);
    } catch {}
    finally {
      setIntradayPending(false);
    }
  }, 60_000);

  usePollingFetch(async () => {
    try {
      const [fh, th] = await Promise.all([
        getFundFlowHistory(20).catch(() => null),
        getTurnoverHistory(10).catch(() => null),
      ]);
      if (fh) {
        setFlowHist(fh.items);
        setFlowHistDegraded(fh.degraded);
      }
      if (th) {
        setTurnHist(th.items);
        setTurnHistDegraded(th.degraded);
      }
    } catch {}
  }, 300_000);

  const loadDayCompare = useCallback(async (day: string) => {
    try {
      setDayCompare(await getTurnoverDay(day));
    } catch {
      setDayCompare(null);
    }
  }, []);

  function pickDay(d: string) {
    setSelDay((cur) => {
      const next = cur === d ? null : d;
      if (next) void loadDayCompare(next); // 选中即拉取对比，无需 effect（规避 set-state-in-effect）
      return next;
    });
  }

  const rtMaxAbs = useMemo(() => {
    const t = flowRt?.items?.total;
    return t ? Math.max(1, ...TIER_META.map((m) => Math.abs(t[m.key] ?? 0))) : 1;
  }, [flowRt]);

  const turnoverDiff = turnover?.diff_yi ?? null;
  const estLabel =
    turnover?.est_method === "closed"
      ? "已收盘"
      : turnover?.est_method === "prev-dist"
        ? "按昨日分时分布估算"
        : turnover?.est_method === "linear"
          ? "按时间线性估算"
          : null;

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {/* 第一行：成交额对比 + 实时五档净额 */}
      <div className="grid shrink-0 gap-2 lg:grid-cols-2">
        <Panel
          title="两市成交额（沪深）"
          className="min-h-0 overflow-hidden"
          extra={<span className="text-[10px] text-zinc-400">更新 {turnover?.updated_at || "--"}</span>}
        >
          {turnoverPending ? (
            <div className="space-y-2 px-4 py-3">
              <Skeleton className="h-8 w-40" />
              <Skeleton className="h-4 w-56" />
              <Skeleton className="h-4 w-48" />
            </div>
          ) : (
            <div className="px-4 py-2.5">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="font-mono text-2xl font-semibold tabular-nums">{fmtYi(turnover?.today_amount_yi, 0)}<span className="ml-0.5 text-xs font-normal text-zinc-400">亿</span></span>
                {turnoverDiff != null && (
                  <span className={`font-mono text-sm font-semibold ${turnoverDiff >= 0 ? "text-up" : "text-down"}`}>
                    {turnoverDiff >= 0 ? "增量" : "缩量"} {fmtYi(Math.abs(turnoverDiff), 0)}亿
                    <span className="ml-1 text-[10px] font-normal text-zinc-400">vs {turnover?.prev_date ?? "上一交易日"}同一时刻</span>
                  </span>
                )}
              </div>
              <p className="mt-1 text-[11px] text-zinc-400">
                预估全日 <span className="font-mono text-zinc-600 dark:text-zinc-300">{fmtYi(turnover?.est_full_day_yi, 0)}亿</span>
                {estLabel && <span className="ml-1">（{estLabel}）</span>}
                {turnover?.prev_total_yi != null && <span className="ml-2">昨日全日 {fmtYi(turnover.prev_total_yi, 0)}亿</span>}
              </p>
              {turnover?.today_series && turnover?.prev_series && (
                <CumCompareChart today={turnover.today_series} prev={turnover.prev_series} />
              )}
              <p className="mt-1 text-[10px] text-zinc-400">
                <span className="mr-2 inline-flex items-center gap-1"><span className="inline-block h-0.5 w-3 bg-[#dc2626]" />今日累计</span>
                <span className="inline-flex items-center gap-1"><span className="inline-block h-0.5 w-3 border-t border-dashed border-zinc-400" />昨日累计</span>
                {turnover?.degraded.length ? <span className="ml-2 text-amber-500">⚠ {turnover.degraded.join("；")}</span> : null}
              </p>
            </div>
          )}
        </Panel>

        <Panel
          title="实时资金净额（五档）"
          className="min-h-0 overflow-hidden"
          extra={<span className="text-[10px] text-zinc-400">截至 {flowRt?.as_of ?? "--"}</span>}
        >
          {flowRtPending ? (
            <div className="space-y-2 px-4 py-3">
              {Array.from({ length: 5 }, (_, i) => (
                <Skeleton key={i} className="h-3.5 w-full" />
              ))}
            </div>
          ) : flowRt?.available && flowRt.items ? (
            <div className="px-4 py-2.5">
              <TierBars tier={flowRt.items.total} maxAbs={rtMaxAbs} />
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-zinc-400">
                {TIER_META.map((m) => (
                  <span key={m.key} className="inline-flex items-center gap-1">
                    <span className="inline-block h-2 w-2 rounded-sm" style={{ backgroundColor: m.color }} />
                    {m.label}
                  </span>
                ))}
                <span>· 红色=净流入，绿色=净流出（亿元）</span>
              </div>
              {flowRt.degraded.length > 0 && (
                <p className="mt-1 text-[10px] text-amber-500">⚠ {flowRt.degraded.join("；")}</p>
              )}
              <p className="mt-0.5 text-[10px] text-zinc-400">
                口径：东财大盘资金流；主力=超大单+大单；实时全市场数据无机构/游资拆分（仅龙虎榜日度有），不提供臆造字段。
                校验：主力+中单+小单≈0。
              </p>
            </div>
          ) : (
            <div className="px-4 py-6 text-center text-sm text-zinc-400">
              资金流数据暂不可用（{flowRt?.reason ?? "数据源异常"}），下一拍自动重试。
            </div>
          )}
        </Panel>
      </div>

      {/* 第二行：分钟级资金流累计曲线 */}
      <Panel
        title="分钟级资金流累计（今日）"
        className="min-h-0 shrink-0 overflow-hidden"
        extra={<span className="text-[10px] text-zinc-400">更新 {intraday.length ? "已加载" : "--"} · 东财延迟约 15 分钟口径</span>}
      >
        {intradayPending ? (
          <div className="px-4 py-3">
            <Skeleton className="h-24 w-full" />
          </div>
        ) : intraday.length > 0 ? (
          <div className="px-4 py-2">
            <FlowIntradayChart items={intraday} />
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-zinc-400">
              {TIER_META.map((m) => (
                <span key={m.key} className="inline-flex items-center gap-1">
                  <span className="inline-block h-0.5 w-3" style={{ backgroundColor: m.color }} />
                  {m.label}
                </span>
              ))}
              <span>· 中轴为 0；向上=净流入累计，向下=净流出累计</span>
              {intradayDegraded.length > 0 && <span className="text-amber-500">⚠ {intradayDegraded.join("；")}</span>}
            </div>
          </div>
        ) : (
          <p className="px-4 py-6 text-center text-sm text-zinc-400">
            分钟资金流暂不可用（{intradayDegraded.join("；") || "数据源异常"}），60s 后自动重试。
          </p>
        )}
      </Panel>

      {/* 第三行：历史回看 */}
      <div className="grid min-h-0 flex-1 gap-2 overflow-hidden lg:grid-cols-2">
        <Panel title="成交额历史（近 10 交易日，点击切换对比）" className="min-h-0 overflow-hidden">
          <div className="flex h-full min-h-0 flex-col px-4 py-2.5">
            {turnHist.length > 0 ? (
              <>
                <div className="flex flex-wrap gap-1">
                  {turnHist.map((d) => (
                    <button
                      key={d.date}
                      onClick={() => pickDay(d.date)}
                      className={`rounded px-1.5 py-0.5 text-[10px] font-mono transition-colors ${
                        selDay === d.date
                          ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
                          : "bg-zinc-100 text-zinc-500 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-400"
                      }`}
                    >
                      {d.date.slice(5)}
                    </button>
                  ))}
                </div>
                {selDay && dayCompare ? (
                  dayCompare.available ? (
                    <div className="mt-2 text-xs">
                      <p className="flex flex-wrap items-baseline gap-x-3">
                        <span className="font-mono text-base font-semibold tabular-nums">{fmtYi(dayCompare.total_yi, 0)}亿</span>
                        {dayCompare.diff_yi != null && (
                          <span className={`font-mono font-semibold ${dayCompare.diff_yi >= 0 ? "text-up" : "text-down"}`}>
                            {dayCompare.diff_yi >= 0 ? "增量" : "缩量"} {fmtYi(Math.abs(dayCompare.diff_yi), 0)}亿
                          </span>
                        )}
                        <span className="text-[10px] text-zinc-400">vs {dayCompare.prev_date}</span>
                      </p>
                      {dayCompare.series && dayCompare.prev_series ? (
                        <CumCompareChart today={dayCompare.series} prev={dayCompare.prev_series} />
                      ) : (
                        <p className="mt-1 text-[10px] text-zinc-400">该日分时曲线超出分钟K覆盖范围</p>
                      )}
                    </div>
                  ) : (
                    <p className="mt-2 text-xs text-zinc-400">{dayCompare.reason}</p>
                  )
                ) : (
                  <MainTurnBars days={turnHist} />
                )}
                {turnHistDegraded.length > 0 && (
                  <p className="mt-auto text-[10px] text-amber-500">⚠ {turnHistDegraded.join("；")}</p>
                )}
              </>
            ) : (
              <p className="py-6 text-center text-sm text-zinc-400">历史成交额加载中或暂无数据</p>
            )}
          </div>
        </Panel>

        <Panel title="主力资金净额历史（近 20 交易日）" className="min-h-0 overflow-hidden">
          <div className="flex h-full min-h-0 flex-col px-4 py-2.5">
            {flowHist.length > 0 ? (
              <>
                <MainFlowHistoryChart days={flowHist} />
                <p className="mt-1.5 text-[10px] text-zinc-400">
                  柱上红=净流入、下绿=净流出；悬浮看五档明细。来源：东财日度资金流（本地落盘，回源失败时自动节流重试）。
                  {flowHist[0]?.close_pct != null && (
                    <span className="ml-2">
                      最新日（{flowHist[0].date.slice(5)}）上证收盘 <span className={`font-mono ${pctColor(flowHist[0].close_pct)}`}>{pctText(flowHist[0].close_pct)}</span>
                    </span>
                  )}
                </p>
                {flowHistDegraded.length > 0 && (
                  <p className="mt-auto text-[10px] text-amber-500">⚠ {flowHistDegraded.join("；")}</p>
                )}
              </>
            ) : (
              <p className="py-6 text-center text-sm text-zinc-400">历史资金流加载中或暂无数据（收盘后自动积累当日）</p>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}

/** 成交额历史柱（全日成交额，hover 出增减）。 */
function MainTurnBars({ days }: { days: TurnoverHistoryDay[] }) {
  const max = Math.max(1, ...days.map((d) => d.total_yi ?? 0));
  return (
    <div className="mt-2 flex h-24 items-end gap-1.5">
      {days.map((d) => (
        <div
          key={d.date}
          className="flex min-w-0 flex-1 flex-col items-center gap-0.5"
          title={d.diff_yi != null ? `${d.date}｜全日 ${fmtYi(d.total_yi, 0)}亿｜${d.diff_yi >= 0 ? "增量" : "缩量"} ${fmtYi(Math.abs(d.diff_yi), 0)}亿` : `${d.date}｜全日 ${fmtYi(d.total_yi, 0)}亿`}
        >
          <span className="text-[9px] tabular-nums text-zinc-400">{fmtYi(d.total_yi, 0)}</span>
          <div className="w-full rounded-t bg-sky-500/60" style={{ height: Math.max(2, ((d.total_yi ?? 0) / max) * 56) }} />
          <span className="w-full truncate text-center text-[9px] text-zinc-400">{d.date.slice(5)}</span>
        </div>
      ))}
    </div>
  );
}
