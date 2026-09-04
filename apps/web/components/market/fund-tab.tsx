"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { Panel } from "@/components/panel";
import { Skeleton } from "@/components/ui/loading";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { fmtAmount, pctColor, pctText } from "@/lib/format";
import {
  getFundFlowHistory,
  getFundFlowIntraday,
  getFundFlowRealtime,
  getLonghu,
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
import type { LongHuRecord } from "@/types/market";

/**
 * 市场页「资金」Tab（2026-09-04）：
 * 1. 两市成交额：实时 + 昨日同一时刻增减（亿元）+ 按时间进度/昨日分布估算全日
 * 2. 实时五档资金净额（主力/超大/大/中/小单——实时全市场源无机构/游资拆分，不臆造）
 * 3. 分钟级资金流累计曲线（东财延迟 ~15 分钟口径），悬停圆点标记 + tooltip 五档明细
 * 4. 历史回看：成交额近 10 日 + 点击切换日对比；主力净额近 20 日
 * 5. 机构/游资动向：龙虎榜日度（org/hot_money 席位净额）——实时源无拆分，这里是唯一真实口径
 *
 * 布局契约（滚动条事故 ×3 的教训）：本 tab 内容天然超过一屏，
 * 根节点必须 overflow-y-auto（滚动兜底），图表 SVG 必须定高
 * （viewBox + w-full 会随宽度等比撑高，宽屏下分钟图可到 290px+，
 * 把后续行挤出视口后被 main 的 overflow-hidden 静默裁剪且无任何滚动条）。
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

/** 坐标轴刻度自适应：≥1万亿 显示万亿，否则亿（输入单位：亿）。 */
function fmtAxis(v: number): string {
  if (v >= 1e4) return `${(v / 1e4).toFixed(1)}万亿`;
  return `${Math.round(v).toLocaleString("zh-CN")}亿`;
}

/** HH:MM → 交易分钟序（0..240），与后端 _sina_bar_seq 同口径。 */
function hmToSeq(t: string): number {
  const [h, m] = t.split(":").map(Number);
  const hm = h * 60 + m;
  if (hm <= 570) return 0;
  if (hm <= 690) return hm - 570;
  return Math.min(240, 120 + (hm - 780));
}

type CumPoint = { t: string; cum: number };

/**
 * 累计成交额对比小图：今日（红实线） vs 昨日（灰虚线）。
 * 修复（2026-09-04）：坐标轴最大值不再取「当前累计」（曲线恒顶格、随刷新呼吸），
 * 改由调用方传静态参考 refMax（昨日全日 / 预估全日取大 + 余量）；
 * SVG 定高 + preserveAspectRatio=none + non-scaling-stroke，
 * 刻度文字移出 SVG（HTML 绝对定位），不再随宽度缩放变形。
 */
function CumCompareChart({ today, prev, refMax }: { today: CumPoint[]; prev: CumPoint[]; refMax: number }) {
  const W = 320;
  const H = 100;
  const ptMax = Math.max(0, ...today.map((p) => p.cum), ...prev.map((p) => p.cum));
  const max = Math.max(1, refMax, ptMax);
  const yOf = (v: number) => H - (v / max) * (H - 10) - 5;
  const toPath = (pts: CumPoint[]) =>
    pts
      .map((p, i) => `${i === 0 ? "M" : "L"}${((hmToSeq(p.t) / 240) * W).toFixed(1)},${yOf(p.cum).toFixed(1)}`)
      .join(" ");
  if (today.length === 0 && prev.length === 0) return null;
  return (
    <div className="relative mt-2 h-28 w-full" data-testid="cum-compare-chart">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-full w-full" role="img" aria-label="今日与昨日分时累计成交额对比">
        <line x1="0" y1={yOf(max)} x2={W} y2={yOf(max)} stroke="currentColor" className="text-zinc-200 dark:text-zinc-800" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        <line x1="0" y1={yOf(max / 2)} x2={W} y2={yOf(max / 2)} stroke="currentColor" className="text-zinc-200/70 dark:text-zinc-800/70" strokeWidth="1" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
        <line x1="0" y1={yOf(0)} x2={W} y2={yOf(0)} stroke="currentColor" className="text-zinc-300 dark:text-zinc-700" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {prev.length > 0 && <path d={toPath(prev)} fill="none" stroke="#a1a1aa" strokeWidth="1.2" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />}
        {today.length > 0 && <path d={toPath(today)} fill="none" stroke="#dc2626" strokeWidth="1.6" vectorEffect="non-scaling-stroke" />}
      </svg>
      <span className="absolute left-1 top-0 font-mono text-[9px] leading-none text-zinc-400">{fmtAxis(max)}</span>
      <span className="absolute bottom-0 left-1 font-mono text-[9px] leading-none text-zinc-400">0</span>
      <span className="absolute right-1 top-0 font-mono text-[9px] leading-none text-zinc-400">{fmtAxis(max / 2)}</span>
    </div>
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

/**
 * 分钟级五档资金流累计曲线。
 * 交互（2026-09-04）：鼠标悬停 → 最近分钟列对齐导引线 + 各档圆点标记 + tooltip 五档明细。
 * SVG 定高（h-44）+ preserveAspectRatio=none + non-scaling-stroke；圆点/tooltip 为 HTML 元素不变形。
 */
function FlowIntradayChart({ items }: { items: FlowIntradayPoint[] }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const maxAbs = Math.max(
    1,
    ...items.flatMap((p) => TIER_META.map((m) => Math.abs(p[m.key] ?? 0))),
  );
  const yPct = (v: number) => 50 - (v / maxAbs) * 46; // viewBox 纵向百分比（中轴 50%）
  const toPath = (key: keyof FlowTier) =>
    items
      .map((p, i) => {
        const v = p[key];
        if (v == null) return "";
        return `${i === 0 || items[i - 1][key] == null ? "M" : "L"}${hmToSeq(p.t).toFixed(1)},${yPct(v).toFixed(1)}`;
      })
      .join(" ");

  const onMove = useCallback(
    (e: React.MouseEvent) => {
      const rect = wrapRef.current?.getBoundingClientRect();
      if (!rect || rect.width === 0) return;
      const seq = Math.round(((e.clientX - rect.left) / rect.width) * 240);
      let best = 0;
      let bestDist = Infinity;
      items.forEach((p, i) => {
        const d = Math.abs(hmToSeq(p.t) - seq);
        if (d < bestDist) {
          bestDist = d;
          best = i;
        }
      });
      setHoverIdx(best);
    },
    [items],
  );

  if (items.length === 0) return null;
  const hp = hoverIdx != null ? items[hoverIdx] : null;
  const hoverLeftPct = hp ? (hmToSeq(hp.t) / 240) * 100 : 0;

  return (
    <div className="w-full" data-testid="flow-intraday-chart">
      <div
        ref={wrapRef}
        className="relative h-44 w-full cursor-crosshair"
        onMouseMove={onMove}
        onMouseLeave={() => setHoverIdx(null)}
      >
        <svg viewBox="0 0 240 100" preserveAspectRatio="none" className="h-full w-full" role="img" aria-label="分钟级五档资金流累计曲线">
          <line x1="0" y1="50" x2="240" y2="50" stroke="currentColor" className="text-zinc-300 dark:text-zinc-700" strokeWidth="1" strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />
          <line x1="60" y1="0" x2="60" y2="100" stroke="currentColor" className="text-zinc-100 dark:text-zinc-800/80" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <line x1="120" y1="0" x2="120" y2="100" stroke="currentColor" className="text-zinc-100 dark:text-zinc-800/80" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <line x1="180" y1="0" x2="180" y2="100" stroke="currentColor" className="text-zinc-100 dark:text-zinc-800/80" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          {TIER_META.map((m) => (
            <path key={m.key} d={toPath(m.key)} fill="none" stroke={m.color} strokeWidth={m.key === "main" ? 2 : 1.1} opacity={m.key === "main" ? 1 : 0.8} vectorEffect="non-scaling-stroke" />
          ))}
        </svg>

        {/* 悬停：列导引线 + 各档圆点标记（HTML 元素，preserveAspectRatio=none 下不变形） */}
        {hp && (
          <>
            <div className="pointer-events-none absolute inset-y-0 w-px bg-zinc-400/60 dark:bg-zinc-500/60" style={{ left: `${hoverLeftPct}%` }} />
            {TIER_META.map((m) => {
              const v = hp[m.key];
              if (v == null) return null;
              return (
                <div
                  key={m.key}
                  className="pointer-events-none absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-white/80 shadow-sm dark:border-zinc-900/80"
                  style={{ left: `${hoverLeftPct}%`, top: `${yPct(v)}%`, backgroundColor: m.color }}
                />
              );
            })}
            {/* tooltip：靠近右缘时翻转到指针左侧 */}
            <div
              className="pointer-events-none absolute top-1 z-10 w-44 rounded-md border border-zinc-200 bg-white/95 p-2 shadow-lg dark:border-zinc-700 dark:bg-zinc-900/95"
              style={{ left: `${hoverLeftPct}%`, transform: hoverLeftPct > 60 ? "translateX(calc(-100% - 8px))" : "translateX(8px)" }}
              data-testid="flow-tooltip"
            >
              <p className="mb-1 font-mono text-[10px] text-zinc-500">{hp.t}｜分钟累计净额（亿元）</p>
              {TIER_META.map((m) => {
                const v = hp[m.key];
                return (
                  <p key={m.key} className="flex items-center justify-between py-px text-[10px]">
                    <span className="inline-flex items-center gap-1 text-zinc-500">
                      <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ backgroundColor: m.color }} />
                      {m.label}
                    </span>
                    <span className={`font-mono tabular-nums ${v == null ? "text-zinc-400" : v >= 0 ? "text-up" : "text-down"}`}>{signedFmt(v)}</span>
                  </p>
                );
              })}
            </div>
          </>
        )}
      </div>
      {/* 时间轴：HTML 行（替代 SVG 内文字，定高下不变形） */}
      <div className="flex justify-between font-mono text-[9px] text-zinc-400">
        <span>09:30</span>
        <span>10:30</span>
        <span>11:30/13:00</span>
        <span>14:00</span>
        <span>15:00</span>
      </div>
    </div>
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

/** 龙虎榜机构/游资聚合（只计当日榜 range_days=1；区间未知的记录单列不计入，绝不跨区间相加）。 */
function aggregateLonghu(records: LongHuRecord[]) {
  const day = records.filter((r) => r.range_days === 1);
  const unknownRange = records.length - day.length;
  let orgSum = 0;
  let orgCount = 0;
  let hotSum = 0;
  let hotCount = 0;
  for (const r of day) {
    if (r.org_net_value != null) {
      orgSum += r.org_net_value;
      orgCount += 1;
    }
    if (r.hot_money_net_value != null) {
      hotSum += r.hot_money_net_value;
      hotCount += 1;
    }
  }
  const top = [...day]
    .sort((a, b) => Math.abs(b.net_buy ?? 0) - Math.abs(a.net_buy ?? 0))
    .slice(0, 6);
  return { dayCount: day.length, unknownRange, orgSum, orgCount, hotSum, hotCount, top };
}

/**
 * 机构/游资动向卡：龙虎榜日度席位净额（实时全市场源无机构/游资拆分，这里是唯一真实口径）。
 * 回退（2026-09-04）：龙虎榜约 17:00 后披露，收盘后~披露前默认日期（最近交易日=当日）
 * 必然空榜报错——此时回退到最近已披露的历史交易日（fallbackDates，来自成交额历史），
 * 拉到哪日如实标注日期；当日榜一经披露，下次轮询自动切回当日。
 */
function LonghuFlowCard({ fallbackDates }: { fallbackDates: string[] }) {
  const [payload, setPayload] = useState<{ date: string; records: LongHuRecord[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(true);

  // 回退池未就绪时 60s 重试（避免高频失败调用喂熔断器），就绪后 5 分钟；
  // intervalMs 变化会重挂 effect 立即补拉一次
  usePollingFetch(async () => {
    try {
      try {
        const records = await getLonghu();
        setPayload({ date: records[0]?.trade_date ?? "", records });
        setError(null);
        return;
      } catch {
        /* 当日未披露，走回退 */
      }
      for (const d of fallbackDates) {
        try {
          const records = await getLonghu(d);
          if (records.length > 0) {
            setPayload({ date: records[0]?.trade_date ?? d, records });
            setError(null);
            return;
          }
        } catch {
          continue; // 该日也失败，继续回退
        }
      }
      setError(fallbackDates.length ? "当日龙虎榜未披露，历史日回退失败" : "龙虎榜数据源失败");
    } finally {
      setPending(false);
    }
  }, fallbackDates.length > 0 ? 300_000 : 60_000);

  const agg = useMemo(() => (payload ? aggregateLonghu(payload.records) : null), [payload]);

  return (
    <Panel
      title="机构 · 游资动向（龙虎榜·日度）"
      className="min-h-0 shrink-0 overflow-hidden"
      extra={<span className="text-[10px] text-zinc-400">{payload?.date ? `${payload.date} 上榜` : "--"} · 交易所收盘后披露</span>}
    >
      {pending ? (
        <div className="space-y-2 px-4 py-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : agg && agg.dayCount > 0 ? (
        <div className="px-4 py-2.5">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1.5">
            <div>
              <p className="text-[10px] text-zinc-400">机构席位净买入合计（{agg.orgCount} 只上榜有机构参与）</p>
              <p className={`font-mono text-lg font-semibold ${agg.orgSum >= 0 ? "text-up" : "text-down"}`}>{fmtAmount(agg.orgSum)}</p>
            </div>
            <div>
              <p className="text-[10px] text-zinc-400">游资席位净买入合计（{agg.hotCount} 只有游资参与）</p>
              <p className={`font-mono text-lg font-semibold ${agg.hotSum >= 0 ? "text-up" : "text-down"}`}>{fmtAmount(agg.hotSum)}</p>
            </div>
            {agg.unknownRange > 0 && (
              <span className="text-[10px] text-zinc-400" title="三日榜等非当日区间记录不与当日榜相加，单列不计入">
                另有 {agg.unknownRange} 条非当日区间记录未计入
              </span>
            )}
          </div>
          <table className="mt-2 w-full text-xs">
            <thead>
              <tr className="border-b border-zinc-200 text-left text-[10px] text-zinc-400 dark:border-zinc-800">
                <th className="py-1 font-normal">代码</th>
                <th className="py-1 font-normal">名称</th>
                <th className="py-1 text-right font-normal">上榜净额</th>
                <th className="py-1 text-right font-normal">机构净额</th>
                <th className="py-1 text-right font-normal">游资净额</th>
              </tr>
            </thead>
            <tbody>
              {agg.top.map((r) => (
                <tr key={`${r.symbol}-${r.range_days}`} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                  <td className="py-1 font-mono text-zinc-400">{r.symbol}</td>
                  <td className="max-w-[8em] truncate py-1">{r.name ?? "--"}</td>
                  <td className={`py-1 text-right font-mono ${(r.net_buy ?? 0) >= 0 ? "text-up" : "text-down"}`}>{fmtAmount(r.net_buy)}</td>
                  <td className={`py-1 text-right font-mono ${r.org_net_value == null ? "text-zinc-400" : r.org_net_value >= 0 ? "text-up" : "text-down"}`}>
                    {r.org_net_value == null ? "—" : fmtAmount(r.org_net_value)}
                  </td>
                  <td className={`py-1 text-right font-mono ${r.hot_money_net_value == null ? "text-zinc-400" : r.hot_money_net_value >= 0 ? "text-up" : "text-down"}`}>
                    {r.hot_money_net_value == null ? "—" : fmtAmount(r.hot_money_net_value)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-1 text-[10px] text-zinc-400">
            「—」= 该榜单无机构/游资席位参与（区别于参与但净额为 0）。金额均为元，按区间展示；同股日榜/三日榜并存时只取当日榜。
          </p>
        </div>
      ) : (
        <div className="px-4 py-5 text-center text-sm text-zinc-400">
          龙虎榜数据暂不可用（{error ?? "当日尚未披露"}），每 5 分钟自动重试。
          <span className="mt-1 block text-[10px]">机构/游资席位净额为交易所日度披露（约收盘后 17:00+），实时全市场数据无此拆分，本页不提供臆造字段。</span>
        </div>
      )}
    </Panel>
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

  // 成交对比图静态参考最大值：点位实际最大 / 昨日全日 / 预估全日取大，留 8% 余量
  const turnoverRefMax = useMemo(() => {
    const pts = [...(turnover?.today_series ?? []), ...(turnover?.prev_series ?? [])];
    const ptMax = Math.max(0, ...pts.map((p) => p.cum));
    return Math.max(ptMax, turnover?.prev_total_yi ?? 0, turnover?.est_full_day_yi ?? 0) * 1.08;
  }, [turnover]);

  const dayRefMax = useMemo(() => {
    const pts = [...(dayCompare?.series ?? []), ...(dayCompare?.prev_series ?? [])];
    const ptMax = Math.max(0, ...pts.map((p) => p.cum));
    return Math.max(ptMax, dayCompare?.total_yi ?? 0, dayCompare?.prev_total_yi ?? 0) * 1.08;
  }, [dayCompare]);

  // 主力 / 散户（中单+小单，同源同区间，口径内合计）分组摘要
  const rtTotal = flowRt?.items?.total ?? null;
  const retail = rtTotal && rtTotal.mid != null && rtTotal.small != null ? rtTotal.mid + rtTotal.small : null;

  const turnoverDiff = turnover?.diff_yi ?? null;

  // 龙虎榜回退池：成交额历史中早于今日的交易日（当日榜 ~17:00 披露前的兜底数据源）
  const now = new Date();
  const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const lhbFallbackDates = useMemo(
    () => turnHist.map((d) => d.date).filter((d) => d < todayStr),
    [turnHist, todayStr],
  );
  const estLabel =
    turnover?.est_method === "closed"
      ? "已收盘"
      : turnover?.est_method === "prev-dist"
        ? "按昨日分时分布估算"
        : turnover?.est_method === "linear"
          ? "按时间线性估算"
          : null;

  return (
    // 滚动兜底（2026-09-04）：内容天然超一屏，根节点必须可滚——
    // 上层 main 是 overflow-hidden，这里再丢滚动就会静默裁剪。
    <div className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto">
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
                <CumCompareChart today={turnover.today_series} prev={turnover.prev_series} refMax={turnoverRefMax} />
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
              <div className="mb-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <span className="text-xs text-zinc-400">
                  主力 <span className={`font-mono text-base font-semibold ${rtTotal && rtTotal.main != null ? (rtTotal.main >= 0 ? "text-up" : "text-down") : ""}`}>{signedFmt(rtTotal?.main ?? null)}</span>
                </span>
                <span className="text-xs text-zinc-400">
                  散户（中+小单） <span className={`font-mono text-base font-semibold ${retail == null ? "text-zinc-400" : retail >= 0 ? "text-up" : "text-down"}`}>{signedFmt(retail)}</span>
                </span>
              </div>
              <TierBars tier={rtTotal ?? { main: null, super_: null, big: null, mid: null, small: null }} maxAbs={rtMaxAbs} />
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
                口径：东财大盘资金流；主力=超大单+大单（机构/游资合流）。实时全市场源无机构/游资拆分，
                机构/游资动向见下方龙虎榜日度卡（唯一真实口径）。校验：主力+中单+小单≈0。
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
        extra={<span className="text-[10px] text-zinc-400">更新 {intraday.length ? "已加载" : "--"} · 东财延迟约 15 分钟口径 · 悬停看分钟明细</span>}
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
      <div className="grid min-h-[210px] shrink-0 gap-2 lg:grid-cols-2">
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
                        <CumCompareChart today={dayCompare.series} prev={dayCompare.prev_series} refMax={dayRefMax} />
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

      {/* 第四行：机构/游资（龙虎榜日度） */}
      <LonghuFlowCard fallbackDates={lhbFallbackDates} />
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
