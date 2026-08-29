"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/panel";
import { getLonghu } from "@/lib/api";
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
import type { LongHuRecord } from "@/types/market";

export default function LonghuPage() {
  const [records, setRecords] = useState<LongHuRecord[]>([]);
  const [tradeDate, setTradeDate] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (date?: string) => {
    try {
      const list = await getLonghu(date);
      setRecords(list);
      setTradeDate(list[0]?.trade_date ?? "");
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
        <h1 className="text-xl font-semibold">龙虎榜 · {tradeDate || "…"}</h1>
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <label htmlFor="lh-date">按日期查询（T-1 盘后披露）：</label>
          <input
            id="lh-date"
            type="date"
            onChange={(e) => void load(e.target.value || undefined)}
            className="rounded-md border border-zinc-200 bg-transparent px-2 py-1 text-sm dark:border-zinc-700"
          />
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-600 dark:text-amber-300">
          龙虎榜加载失败：{error}（数据源为东方财富 datacenter 免费接口）
        </div>
      )}

      <Panel className="min-h-0 flex-1 overflow-hidden" title={`共 ${records.length} 条（按榜内净买额排序）`} source={records[0]?.source}>
        {records.length === 0 && !error ? (
          <p className="px-4 py-10 text-center text-sm text-zinc-400">暂无数据（龙虎榜盘后披露，当日数据需收盘后查询）</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-zinc-400">
              <tr className="border-b border-zinc-200 dark:border-zinc-800">
                {["代码", "名称", "收盘", "涨幅", "换手", "榜内成交", "净买额", "买入", "卖出", "上榜原因"].map((h) => (
                  <th key={h} className={`px-2 py-2 font-medium ${["名称", "上榜原因"].includes(h) ? "" : "text-right"}`}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {records.map((r, i) => (
                <tr key={`${r.symbol}-${r.reason ?? ""}-${i}`} className="border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900">
                  <td className="px-2 py-2 font-mono text-xs text-zinc-400">
                    <Link href={`/stock/${r.symbol}`} className="hover:text-sky-400 hover:underline">
                      {r.symbol}
                    </Link>
                  </td>
                  <td className="px-2 py-2">{r.name}</td>
                  <td className="px-2 py-2 text-right font-mono">{fmt(r.close)}</td>
                  <td className={`px-2 py-2 text-right font-mono ${pctColor(r.change_pct)}`}>{pctText(r.change_pct)}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs">{r.turnover_rate != null ? `${fmt(r.turnover_rate)}%` : "--"}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs">{fmtAmount(r.amount)}</td>
                  <td className={`px-2 py-2 text-right font-mono text-xs ${(r.net_buy ?? 0) > 0 ? "text-up" : "text-down"}`}>
                    {fmtAmount(r.net_buy)}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-xs text-zinc-400">{fmtAmount(r.buy_amount)}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs text-zinc-400">{fmtAmount(r.sell_amount)}</td>
                  <td className="px-2 py-2 text-xs text-zinc-400" title={r.reason ?? ""}>
                    {(r.reason ?? "--").slice(0, 22)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      <p className="mt-4 text-xs text-zinc-400">
        个股席位明细已可在工作台详情「龙虎榜」页签查看；营业部追踪 / 关系图谱在 Phase 4 深度版提供；上榜原因阈值将按交易所规则配置化。
      </p>
    </main>
  );
}
