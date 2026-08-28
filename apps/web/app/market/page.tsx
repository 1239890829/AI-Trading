"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import { Panel } from "@/components/panel";
import { QualityBadge } from "@/components/quality-badge";
import { getLimitUpPool, getMarketOverview } from "@/lib/api";

interface Breadth {
  up: number; down: number; flat: number; limit_up: number; limit_down: number;
  total: number; total_amount: number; suspended: number;
}
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
import type { LimitUpRecord, Quote } from "@/types/market";

export default function MarketPage() {
  const [indices, setIndices] = useState<Quote[]>([]);
  const [totalAmount, setTotalAmount] = useState<number | null>(null);
  const [pool, setPool] = useState<LimitUpRecord[]>([]);
  const [breadth, setBreadth] = useState<Breadth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState("");

  const load = useCallback(async () => {
    try {
      const [overview, zt, breadthRes] = await Promise.all([
        getMarketOverview(),
        getLimitUpPool().catch(() => [] as LimitUpRecord[]),
        fetch(`${API_BASE}/api/market/breadth`).then((r) => r.json()).catch(() => null),
      ]);
      setIndices(overview.indices);
      setTotalAmount(overview.total_amount);
      setPool(zt.slice(0, 10));
      setBreadth(breadthRes?.data?.breadth ?? null);
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

  const sh = indices.find((q) => q.market === "SH" && q.symbol === "000001");
  const sz = indices.find((q) => q.symbol === "399001");
  const advancing = "市场宽度（涨跌家数/炸板率/情绪周期）按开发顺序在 Phase 3 接入";

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

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-2">
        <Panel title="两市成交额" className="min-h-0 overflow-hidden" source={sh?.source} dataTimestamp={sh?.data_timestamp}>
          <div className="px-4 py-6">
            <p className="font-mono text-3xl font-semibold">{fmtAmount(totalAmount)}</p>
            <p className="mt-2 text-xs text-zinc-400">
              上证 {fmtAmount(sh?.amount)} + 深证成指口径 {fmtAmount(sz?.amount)}；成交额历史趋势与宽度指标随行情落库（Parquet）后在后续阶段提供。
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
