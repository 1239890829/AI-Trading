"use client";

import { useEffect, useState } from "react";
import { getBoards, type BoardRow } from "@/lib/api";
import { fmtAmount, pctColor, pctText } from "@/lib/format";

/** 板块涨幅榜（指数详情右列"板块"标签）：行业/概念切换，按涨跌幅降序。
 *  复用 /api/boards（新浪闪电口径，后端 60s 缓存），零新增数据源。 */
export function BoardRankPanel({ className }: { className?: string }) {
  const [type, setType] = useState<"hangye" | "concept">("hangye");
  const [rows, setRows] = useState<BoardRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const b = await getBoards(type);
        if (!alive) return;
        setRows(b);
        setError(null);
      } catch (e) {
        if (alive) setError((e as Error).message);
      }
    };
    void load();
    const t = setInterval(load, 60_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [type]);

  return (
    <div className={`flex min-h-0 flex-col ${className ?? ""}`}>
      <div className="flex shrink-0 items-center gap-1 border-b border-zinc-100 px-2 py-1.5 dark:border-zinc-800/60">
        {(
          [
            ["hangye", "行业"],
            ["concept", "概念"],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            onClick={() => setType(k)}
            className={`rounded px-2 py-0.5 text-xs ${type === k ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"}`}
          >
            {label}
          </button>
        ))}
        <span className="ml-auto text-[10px] text-zinc-500">按涨跌幅降序 · 60s 刷新</span>
      </div>
      {error && <div className="shrink-0 px-3 py-2 text-xs text-amber-600 dark:text-amber-300">{error}</div>}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-zinc-50 text-zinc-400 dark:bg-zinc-900">
            <tr className="text-left">
              <th className="px-3 py-1.5 font-normal">板块</th>
              <th className="px-2 py-1.5 text-right font-normal">涨跌幅</th>
              <th className="px-2 py-1.5 text-right font-normal">成交额</th>
              <th className="px-3 py-1.5 text-right font-normal">领涨股</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.name} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                <td className="px-3 py-1.5">
                  {b.name}
                  {b.count != null && <span className="ml-1 text-[10px] text-zinc-400">{b.count}</span>}
                </td>
                <td className={`px-2 py-1.5 text-right font-mono font-medium tabular-nums ${pctColor(b.change_pct)}`}>
                  {pctText(b.change_pct)}
                </td>
                <td className="px-2 py-1.5 text-right font-mono tabular-nums text-zinc-400">{fmtAmount(b.amount)}</td>
                <td className="px-3 py-1.5 text-right">
                  <span className="text-zinc-300">{b.leader_name ?? "--"}</span>
                  <span className={`ml-1 font-mono tabular-nums ${pctColor(b.leader_change_pct)}`}>
                    {pctText(b.leader_change_pct)}
                  </span>
                </td>
              </tr>
            ))}
            {rows.length === 0 && !error && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-zinc-400">
                  暂无数据
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
