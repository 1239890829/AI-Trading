"use client";

import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/panel";
import { QualityBadge } from "@/components/quality-badge";
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

const PHASE_STYLE: Record<string, string> = {
  冰点: "bg-sky-500/15 text-sky-300 border-sky-500/40",
  修复: "bg-teal-500/15 text-teal-300 border-teal-500/40",
  发酵: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  高潮: "bg-up/20 text-up border-up/50",
  分歧: "bg-orange-500/15 text-orange-300 border-orange-500/40",
  退潮: "bg-down/20 text-down border-down/50",
};

export default function MarketPage() {
  const [indices, setIndices] = useState<Quote[]>([]);
  const [totalAmount, setTotalAmount] = useState<number | null>(null);
  const [pool, setPool] = useState<LimitUpRecord[]>([]);
  const [breadth, setBreadth] = useState<Breadth | null>(null);
  const [sent, setSent] = useState<Sentiment | null>(null);
  const [sentHist, setSentHist] = useState<SentimentHistoryPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState("");

  const load = useCallback(async () => {
    try {
      const [overview, zt, breadthRes, sentRes, histRes] = await Promise.all([
        getMarketOverview(),
        getLimitUpPool().catch(() => [] as LimitUpRecord[]),
        getBreadth().catch(() => null),
        getSentiment().catch(() => null),
        getSentimentHistory(10).catch(() => null),
      ]);
      setIndices(overview.indices);
      setTotalAmount(overview.total_amount);
      setPool(zt.slice(0, 10));
      setBreadth(breadthRes);
      setSent(sentRes);
      setSentHist(histRes);
      setError(null);
      setUpdatedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    } catch {
      setError("无法连接后端行情服务（启动方式见工作台页提示）。");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  // 历史序列变化慢（日频），独立 60s 轮询，不跟随 10s 行情刷新
  useEffect(() => {
    const t = setInterval(() => void getSentimentHistory(10).then(setSentHist).catch(() => {}), 60000);
    return () => clearInterval(t);
  }, []);

  const sh = indices.find((q) => q.market === "SH" && q.symbol === "000001");
  const sz = indices.find((q) => q.symbol === "399001");
  const advancing = "市场宽度已上线（上方涨跌/涨停卡片，全市场快照实时计算）；情绪周期判定见情绪面板，炸板率精细化在后续版本提供";

  return (
    <main className="h-full flex flex-col gap-3 px-4 py-3 max-w-[1600px] mx-auto w-full">
      {error && (
        <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-600 dark:text-amber-300">
          {error}
        </div>
      )}
      <div className="flex shrink-0 flex-wrap items-end justify-between gap-2">
        <h1 className="text-lg font-semibold">市场总览</h1>
        <span className="text-xs text-zinc-400">更新 {updatedAt || "--"}</span>
      </div>

      <div className="grid shrink-0 grid-cols-3 gap-3 md:grid-cols-6">
        {indices.map((q) => (
          <div key={q.symbol} className="rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800">
            <div className="flex items-baseline justify-between">
              <span className="text-xs text-zinc-400">{q.name ?? q.symbol}</span>
              <QualityBadge quality={q.quality} reasons={q.quality_reasons} />
            </div>
            <div className="mt-1 font-mono text-lg font-semibold">{fmt(q.price)}</div>
            <div className={`font-mono text-xs ${pctColor(q.change_pct)}`}>{pctText(q.change_pct)}</div>
            <div className="mt-1 font-mono text-xs text-zinc-500">成交额 {fmtAmount(q.amount)}</div>
          </div>
        ))}
      </div>

      <div className="grid shrink-0 grid-cols-3 gap-3 lg:grid-cols-6">
        {[
          ["上涨", breadth?.up, "text-up"],
          ["下跌", breadth?.down, "text-down"],
          ["涨停", breadth?.limit_up, "text-up"],
          ["跌停", breadth?.limit_down, "text-down"],
          ["平盘/停牌", breadth ? `${breadth.flat}/${breadth.suspended}` : null, ""],
          ["沪深京总数", breadth?.total, ""],
        ].map(([label, value, cls]) => (
          <div key={String(label)} className="rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800">
            <div className="text-xs text-zinc-400">{label}</div>
            <div className={`font-mono text-lg font-semibold ${cls}`}>{value ?? "--"}</div>
          </div>
        ))}
      </div>
      {breadth && (
        <p className="mb-4 text-xs text-zinc-500">
          宽度口径：全市场快照价格法（{breadth.total} 只）；涨停 {breadth.limit_up} 只含收盘贴板未封住者，
          权威封板口径见涨停池页（东财）。两市总额（含北交所）{fmtAmount(breadth.total_amount)}。
        </p>
      )}

      {sent && (
        <div className="shrink-0 rounded-xl border border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <span className={`rounded-md border px-2.5 py-1 text-sm font-semibold ${PHASE_STYLE[sent.phase] ?? ""}`}>
              {sent.phase}
            </span>
            <span className="text-xs text-zinc-400">
              情绪温度 <span className="font-mono text-base font-semibold text-zinc-100">{sent.temperature}</span>/100
            </span>
            <span className="text-xs text-zinc-400">置信度 {sent.confidence}</span>
            <span className="text-xs text-zinc-400">
              {sent.indicators.slice(0, 6).map((i) => `${i.name} ${i.value ?? "--"}`).join(" · ")}
            </span>
            <span className="ml-auto text-xs text-zinc-500" title={`${sent.reasons.join("；")}｜误判：${sent.misjudge_caveats.join("；")}｜切换：${sent.switch_conditions}`}>
              判定依据：{sent.reasons[0]}…
            </span>
          </div>
        </div>
      )}

      {sentHist && sentHist.items.length > 0 && (
        <div className="shrink-0 rounded-xl border border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className="text-xs font-medium text-zinc-400">近 {sentHist.items.length} 日情绪序列</span>
            {(() => {
              const cy = sentHist.cycle;
              if (!cy.start_date) return null;
              const groupLabel = cy.current_group === "strong" ? "强区间" : cy.current_group === "weak" ? "弱区间" : "中性段";
              return (
                <span className="text-xs text-zinc-500">
                  本轮自 <span className="font-mono text-zinc-300">{cy.start_date}</span> 起
                  （{cy.start_phase ?? ""}→{sentHist.items[sentHist.items.length - 1]?.phase}），已持续 {cy.days} 日（{groupLabel}）
                </span>
              );
            })()}
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
        </div>
      )}

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-2">
        <Panel title="两市成交额" className="min-h-0 overflow-hidden" source={sh?.source} dataTimestamp={sh?.data_timestamp}>
          <div className="px-4 py-6">
            <p className="font-mono text-3xl font-semibold">{fmtAmount(totalAmount)}</p>
            <p className="mt-2 text-xs text-zinc-400">
              含北交所成交额；历史趋势图随 Parquet 数据积累（已开始落库）逐步提供。
            </p>
          </div>
        </Panel>

        <Panel title="涨停速览（今日连板前列）" source={pool[0]?.source} className="min-h-0 overflow-hidden">
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

      <p className="shrink-0 truncate rounded-lg border border-zinc-200 px-4 py-2 text-xs text-zinc-400 dark:border-zinc-800" title="{advancing}">
        说明：{advancing}。本页所有数据均标注来源与数据时间；免费数据源失败时接口返回 502，前端展示错误态，不伪造实时数据。
      </p>
    </main>
  );
}
