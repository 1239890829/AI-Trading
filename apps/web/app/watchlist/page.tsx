"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/panel";
import { addToWatchlist, getQuotes, getWatchlist, removeFromWatchlist, updateWatchlistGroup } from "@/lib/api";
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
import { workbenchUrl } from "@/lib/routing";
import type { Quote, WatchlistItem } from "@/types/market";

export default function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [error, setError] = useState<string | null>(null);
  const [newSymbol, setNewSymbol] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [allGroups, setAllGroups] = useState<string[]>(["默认"]);

  const load = useCallback(async () => {
    try {
      const list = await getWatchlist();
      setItems(list);
      setAllGroups([...new Set(["默认", ...list.map((i) => i.group_name ?? "默认")])]);
      const qs = await getQuotes(list.map((i) => i.symbol));
      setQuotes(Object.fromEntries(qs.map((q) => [q.symbol, q])));
      setError(null);
    } catch {
      setError("无法连接后端服务。");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  async function remove(symbol: string) {
    await removeFromWatchlist(symbol).catch(() => {});
    void load();
  }

  async function add() {
    const symbol = newSymbol.trim();
    if (!/^\d{6}$/.test(symbol)) {
      setAddError("请输入 6 位数字代码");
      return;
    }
    setAddError(null);
    try {
      await addToWatchlist(symbol);
      setNewSymbol("");
      void load();
    } catch {
      setAddError("添加失败，请确认后端已启动");
    }
  }

  return (
    <main className="h-full flex flex-col px-4 py-3 max-w-[1600px] mx-auto w-full">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">自选股管理</h1>
        <p className="text-xs text-zinc-400">用顶部搜索框添加（回车选中第一个结果即可进入个股页，个股页可加自选）</p>
      </div>
      {error && (
        <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-600 dark:text-amber-300">
          {error}
        </div>
      )}
      <Panel className="min-h-0 flex-1 overflow-hidden" title={`共 ${items.length} 只`}>
        <div className="flex items-center gap-2 border-b border-zinc-100 px-3 py-2 dark:border-zinc-800/60">
          <input
            value={newSymbol}
            onChange={(e) => setNewSymbol(e.target.value)}
            placeholder="输入 6 位代码，如 601899"
            maxLength={6}
            className="w-48 rounded-md border border-zinc-200 bg-transparent px-2 py-1 font-mono text-sm outline-none focus:border-up/60 dark:border-zinc-700"
          />
          <button
            onClick={() => void add()}
            className="rounded-md bg-up/90 px-3 py-1 text-sm text-white hover:bg-up"
          >
            添加自选
          </button>
          {addError && <span className="text-xs text-red-400">{addError}</span>}
        </div>
        {items.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-zinc-400">自选为空</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-zinc-400">
              <tr className="border-b border-zinc-200 dark:border-zinc-800">
                <th className="px-3 py-2 font-medium">代码</th>
                <th className="px-2 py-2 font-medium">名称</th>
                <th className="px-2 py-2 text-right font-medium">现价</th>
                <th className="px-2 py-2 text-right font-medium">涨跌幅</th>
                <th className="px-2 py-2 text-right font-medium">成交额</th>
                <th className="px-2 py-2 text-right font-medium">来源</th>
                <th className="px-2 py-2 text-right font-medium">分组</th>
                <th className="px-3 py-2 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const q = quotes[it.symbol];
                return (
                  <tr key={it.symbol} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                    <td className="px-3 py-2 font-mono text-xs">{it.symbol}</td>
                    <td className="px-2 py-2">{q?.name ?? it.name ?? "--"}</td>
                    <td className="px-2 py-2 text-right font-mono">{fmt(q?.price)}</td>
                    <td className={`px-2 py-2 text-right font-mono ${pctColor(q?.change_pct)}`}>{pctText(q?.change_pct)}</td>
                    <td className="px-2 py-2 text-right font-mono text-zinc-400">{fmtAmount(q?.amount)}</td>
                    <td className="px-2 py-2 text-right text-xs text-zinc-400">
                      <select
                        value={it.group_name ?? "默认"}
                        onChange={async (e) => {
                          await updateWatchlistGroup(it.symbol, e.target.value).catch(() => {});
                          void load();
                        }}
                        className="rounded border border-zinc-200 bg-transparent px-1 py-0.5 text-xs dark:border-zinc-700"
                      >
                        {[...new Set([...allGroups, it.group_name ?? "默认"])].map((g) => (
                          <option key={g} value={g}>{g}</option>
                        ))}
                      </select>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Link href={workbenchUrl(it.symbol)} className="mr-3 text-xs text-sky-400 hover:underline">
                        详情
                      </Link>
                      <button onClick={() => void remove(it.symbol)} className="text-xs text-zinc-400 hover:text-red-400">
                        移除
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
    </main>
  );
}
