"use client";

import { useCallback, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Panel } from "@/components/panel";
import { StockLink, useStockRowNav } from "@/components/stock-link";
import { getLimitDownPool } from "@/lib/api";
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import type { LimitDownRecord } from "@/types/market";

/**
 * 盘面页 · 跌停 tab（2026-09-04 新增）。
 *
 * 定位：与涨停生态 tab 对称的"反面证据"——市场页宽度带「跌停」入口跳到这里。
 * 数据源为东财 push2ex getTopicDTPool（免费接口，date 必带）；空池是常态
 * （普涨日可能零跌停），空态与加载态严格区分（同 limit-up-tab 2026-09-04 教训）。
 */

export function LimitDownTab() {
  const stockNav = useStockRowNav();
  const searchParams = useSearchParams();
  const [records, setRecords] = useState<LimitDownRecord[]>([]);
  const [tradeDate, setTradeDate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (date?: string) => {
    try {
      const pool = await getLimitDownPool(date);
      setRecords(pool);
      setTradeDate(pool[0]?.trade_date ?? "");
      setError(null);
    } catch (e) {
      setError((e as Error).message);
      setRecords([]);
    } finally {
      setLoading(false);
    }
  }, []);

  usePollingFetch(() => load(searchParams.get("date") || undefined), null);

  function syncUrl(date: string) {
    const p = new URLSearchParams(window.location.search);
    if (date) p.set("date", date);
    else p.delete("date");
    const qs = p.toString();
    window.history.replaceState({}, "", qs ? `?${qs}` : window.location.pathname);
  }

  const onDate = (v: string) => {
    void load(v || undefined);
    syncUrl(v);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="mb-3 flex shrink-0 flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">跌停池 · {tradeDate || "…"}</h2>
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <label htmlFor="dt-date">按日期查询：</label>
          <input
            id="dt-date"
            type="date"
            value={tradeDate}
            onChange={(e) => onDate(e.target.value)}
            className="rounded-md border border-zinc-200 bg-transparent px-2 py-1 text-sm dark:border-zinc-700"
          />
        </div>
      </div>

      {error && (
        <div className="mb-4 shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-600 dark:text-amber-300">
          跌停池加载失败：{error}（数据源为东方财富 push2ex 免费接口）
        </div>
      )}

      <Panel
        className="min-h-0 flex-1 overflow-hidden"
        title={`共 ${records.length} 只（按连续跌停天数排序）`}
        source={records[0]?.source}
      >
        {records.length === 0 && !error ? (
          loading ? (
            <div className="space-y-2.5 px-3 py-4" aria-hidden>
              {Array.from({ length: 6 }, (_, i) => (
                <div key={i} className="flex items-center gap-3">
                  <div className="h-3.5 w-14 animate-pulse rounded bg-zinc-200/80 dark:bg-zinc-800/70" />
                  <div className="h-3.5 w-20 animate-pulse rounded bg-zinc-200/80 dark:bg-zinc-800/70" />
                  <div className="ml-auto h-3.5 w-24 animate-pulse rounded bg-zinc-200/60 dark:bg-zinc-800/50" />
                </div>
              ))}
            </div>
          ) : (
            <p className="px-4 py-10 text-center text-sm text-zinc-400">当日暂无跌停（或非交易日）</p>
          )
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-zinc-400">
              <tr className="border-b border-zinc-200 dark:border-zinc-800">
                {["代码", "名称", "价格", "跌幅", "连续跌停", "开板", "封单额", "换手", "成交额", "行业"].map((h) => (
                  <th key={h} className={`px-2 py-2 font-medium ${["名称", "行业"].includes(h) ? "" : "text-right"}`}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr
                  key={r.symbol}
                  onClick={stockNav(r.symbol)}
                  className="cursor-pointer border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900"
                >
                  <td className="px-2 py-2 font-mono text-xs text-zinc-400">
                    <StockLink symbol={r.symbol}>
                      {r.symbol}
                    </StockLink>
                  </td>
                  <td className="px-2 py-2">{r.name}</td>
                  <td className="px-2 py-2 text-right font-mono">{fmt(r.price)}</td>
                  <td className={`px-2 py-2 text-right font-mono ${pctColor(r.change_pct)}`}>{pctText(r.change_pct)}</td>
                  <td className="px-2 py-2 text-right font-mono">
                    {(r.consecutive_days ?? 0) > 1 ? (
                      <span className="font-semibold text-down">{r.consecutive_days} 天</span>
                    ) : (
                      (r.consecutive_days ?? 0) || "--"
                    )}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-xs">
                    {(r.open_count ?? 0) > 0 ? <span className="text-amber-400">{r.open_count}</span> : "0"}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-xs">{fmtAmount(r.seal_amount)}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs">{r.turnover_rate != null ? `${fmt(r.turnover_rate)}%` : "--"}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs">{fmtAmount(r.amount)}</td>
                  <td className="max-w-[140px] truncate px-2 py-2 text-xs text-zinc-500 dark:text-zinc-300">{r.industry_board ?? "--"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      <p className="mt-4 shrink-0 text-xs text-zinc-400">
        跌停池是市场极端亏钱效应的反面证据（与涨停生态对照阅读）；连续跌停 ≥2 天的标的风险极高，点击代码进详情仅供参考。
      </p>
    </div>
  );
}
