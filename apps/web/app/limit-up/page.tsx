"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/panel";
import { getLimitUpPool } from "@/lib/api";
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
import type { LimitUpRecord } from "@/types/market";

export default function LimitUpPage() {
  const [records, setRecords] = useState<LimitUpRecord[]>([]);
  const [tradeDate, setTradeDate] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (date?: string) => {
    try {
      const pool = await getLimitUpPool(date);
      setRecords(pool);
      setTradeDate(pool[0]?.trade_date ?? "");
      setError(null);
    } catch (e) {
      setError((e as Error).message);
      setRecords([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="h-full flex flex-col px-4 py-3 max-w-[1600px] mx-auto w-full">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">涨停池 · {tradeDate || "…"}</h1>
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <label htmlFor="zt-date">按日期查询：</label>
          <input
            id="zt-date"
            type="date"
            onChange={(e) => void load(e.target.value || undefined)}
            className="rounded-md border border-zinc-200 bg-transparent px-2 py-1 text-sm dark:border-zinc-700"
          />
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-600 dark:text-amber-300">
          涨停池加载失败：{error}（数据源为东方财富 push2ex 免费接口）
        </div>
      )}

      <Panel className="min-h-0 flex-1 overflow-hidden" title={`共 ${records.length} 只（按连板数排序）`} source={records[0]?.source}>
        {records.length === 0 && !error ? (
          <p className="px-4 py-10 text-center text-sm text-zinc-400">今日暂无涨停（或非交易日）</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-zinc-400">
              <tr className="border-b border-zinc-200 dark:border-zinc-800">
                {["代码", "名称", "价格", "涨幅", "连板", "梯队", "涨停原因", "炸板", "封单额", "换手"].map((h) => (
                  <th key={h} className={`px-2 py-2 font-medium ${["名称"].includes(h) ? "" : "text-right"}`}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr key={r.symbol} className="border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900">
                  <td className="px-2 py-2 font-mono text-xs text-zinc-400">
                    <Link href={`/stock/${r.symbol}`} className="hover:text-sky-400 hover:underline">
                      {r.symbol}
                    </Link>
                  </td>
                  <td className="px-2 py-2">{r.name}</td>
                  <td className="px-2 py-2 text-right font-mono">{fmt(r.price)}</td>
                  <td className={`px-2 py-2 text-right font-mono ${pctColor(r.change_pct)}`}>{pctText(r.change_pct)}</td>
                  <td className="px-2 py-2 text-right font-mono">{r.consecutive_boards ?? "--"}</td>
                  <td className="px-2 py-2 text-right text-xs text-zinc-400">{r.boards_stat ?? "--"}</td>
                  <td className="max-w-[260px] truncate px-2 py-2 text-xs text-zinc-300" title={r.reason ?? ""}>{r.reason ?? "--"}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs">{(r.break_count ?? 0) > 0 ? <span className="text-amber-400">{r.break_count}</span> : "0"}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs">{fmtAmount(r.seal_amount)}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs">{r.turnover_rate != null ? `${fmt(r.turnover_rate)}%` : "--"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      <p className="mt-4 text-xs text-zinc-400">
        连板梯队 / 涨停原因 / 题材标签 / 次日表现统计等深度字段按开发顺序在 Phase 3 接入。
      </p>
    </main>
  );
}
