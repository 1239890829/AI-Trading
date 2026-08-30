"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/panel";
import { getBoards, type BoardRow } from "@/lib/api";
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
import { workbenchUrl } from "@/lib/routing";

const TYPE_LABEL: Record<string, string> = { hangye: "行业板块", concept: "概念板块" };

export default function BoardsPage() {
  const [type, setType] = useState<"hangye" | "concept">("hangye");
  const [rows, setRows] = useState<BoardRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState("");

  const load = useCallback(async (t: string) => {
    try {
      setRows(await getBoards(t as "hangye" | "concept"));
      setError(null);
      setUpdatedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load(type);
    const t = setInterval(() => void load(type), 15000);
    return () => clearInterval(t);
  }, [type, load]);

  return (
    <main className="h-full flex flex-col px-4 py-3 max-w-[1600px] mx-auto w-full gap-3">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h1 className="text-lg font-semibold">板块排行</h1>
          {(["hangye", "concept"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setType(t)}
              className={`rounded-md px-3 py-1.5 text-sm ${
                type === t ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"
              }`}
            >
              {TYPE_LABEL[t]}
            </button>
          ))}
        </div>
        <span className="text-xs text-zinc-400">
          共 {rows.length} 个 · 更新 {updatedAt || "--"} · 按涨跌幅排序
        </span>
      </div>

      {error && (
        <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-600 dark:text-amber-300">
          板块数据加载失败：{error}
        </div>
      )}

      <Panel title={`${TYPE_LABEL[type]} · 涨跌幅排行`} source={rows[0]?.source} className="min-h-0 flex-1 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-white text-left text-xs text-zinc-400 dark:bg-zinc-950">
            <tr className="border-b border-zinc-200 dark:border-zinc-800">
              <th className="px-3 py-2 font-medium">#</th>
              <th className="px-2 py-2 font-medium">板块</th>
              <th className="px-2 py-2 text-right font-medium">涨跌幅</th>
              <th className="px-2 py-2 text-right font-medium">成分股</th>
              <th className="px-2 py-2 text-right font-medium">成交额</th>
              <th className="px-2 py-2 text-right font-medium">领涨股</th>
              <th className="px-3 py-2 text-right font-medium">领涨停幅</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b, i) => (
              <tr key={`${b.name}-${i}`} className="border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900">
                <td className="px-3 py-2 font-mono text-xs tabular-nums text-zinc-400">{i + 1}</td>
                <td className="px-2 py-2 font-medium">{b.name}</td>
                <td className={`px-2 py-2 text-right font-mono tabular-nums ${pctColor(b.change_pct)}`}>{pctText(b.change_pct)}</td>
                <td className="px-2 py-2 text-right font-mono text-xs tabular-nums text-zinc-400">{b.count ?? "--"}</td>
                <td className="px-2 py-2 text-right font-mono text-xs tabular-nums text-zinc-400">{fmtAmount(b.amount)}</td>
                <td className="px-2 py-2 text-right">
                  {b.leader_symbol ? (
                    <Link href={workbenchUrl(b.leader_symbol)} className="text-sky-400 hover:underline">
                      {b.leader_name}
                    </Link>
                  ) : (
                    "--"
                  )}
                </td>
                <td className={`px-3 py-2 text-right font-mono text-xs tabular-nums ${pctColor(b.leader_change_pct)}`}>
                  {pctText(b.leader_change_pct)}
                </td>
              </tr>
            ))}
            {rows.length === 0 && !error && (
              <tr>
                <td colSpan={7} className="px-3 py-10 text-center text-sm text-zinc-400">
                  {error ? "" : "加载中…"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Panel>

      <p className="shrink-0 text-xs text-zinc-500">
        题材事件树/生命周期随新闻模块联动（Phase 4+）；数据源：新浪闪电排行（{TYPE_LABEL[type]}，一次请求全量），经 QuoteHub 链路。
      </p>
    </main>
  );
}
