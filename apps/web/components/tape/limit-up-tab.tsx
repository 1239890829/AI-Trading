"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Panel } from "@/components/panel";
import { getLimitUpPool } from "@/lib/api";
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
import { workbenchUrl } from "@/lib/routing";
import type { LimitUpRecord } from "@/types/market";

/**
 * 盘面页 · 涨停生态 tab（原 /limit-up 页迁移，2026-09-01 系统重构）。
 *
 * 定位：题材梯队 tab 负责"结构"，本 tab 负责"证据"——
 * 每只票的涨停原因原文（ths 官方口径）是题材归属的唯一依据，
 * 归属争议回到这里核对。
 *
 * URL 联动（来自题材卡片「涨停池↗」）：
 * - `?date=YYYY-MM-DD`    初始日期
 * - `?theme=名称&symbols=a,b,c` 高亮该题材梯队成员（不隐藏非成员，
 *   上下文对比是审计页的本分；可切换「只看成员」）
 */

export function LimitUpTab() {
  const searchParams = useSearchParams();
  const [records, setRecords] = useState<LimitUpRecord[]>([]);
  const [tradeDate, setTradeDate] = useState("");
  const [error, setError] = useState<string | null>(null);
  // 题材联动状态：本地持有，URL 仅做初始注入与可分享快照
  const [theme, setTheme] = useState(() => searchParams.get("theme") ?? "");
  const [memberSymbols, setMemberSymbols] = useState<Set<string>>(
    () => new Set((searchParams.get("symbols") ?? "").split(",").filter(Boolean))
  );
  const [onlyMembers, setOnlyMembers] = useState(false);

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
    void load(searchParams.get("date") || undefined);
    // 仅挂载时按 URL 初始日期拉一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load]);

  function syncUrl(next: { date?: string; theme?: string; symbols?: string }) {
    // 在现有 URL 上增删参数（保留 tab= 等盘面页参数）
    const p = new URLSearchParams(window.location.search);
    if (next.date) p.set("date", next.date);
    else p.delete("date");
    if (next.theme) p.set("theme", next.theme);
    else p.delete("theme");
    if (next.symbols) p.set("symbols", next.symbols);
    else p.delete("symbols");
    const qs = p.toString();
    window.history.replaceState({}, "", qs ? `?${qs}` : window.location.pathname);
  }

  const onDate = (v: string) => {
    void load(v || undefined);
    syncUrl({ date: v, theme: theme, symbols: [...memberSymbols].join(",") });
  };

  function clearTheme() {
    setTheme("");
    setMemberSymbols(new Set());
    setOnlyMembers(false);
    syncUrl({ date: tradeDate });
  }

  const membersInPool = useMemo(
    () => records.filter((r) => memberSymbols.has(r.symbol)),
    [records, memberSymbols]
  );

  const shown = onlyMembers && memberSymbols.size > 0 ? membersInPool : records;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="mb-3 flex shrink-0 flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">涨停池 · {tradeDate || "…"}</h2>
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <label htmlFor="zt-date">按日期查询：</label>
          <input
            id="zt-date"
            type="date"
            value={tradeDate}
            onChange={(e) => onDate(e.target.value)}
            className="rounded-md border border-zinc-200 bg-transparent px-2 py-1 text-sm dark:border-zinc-700"
          />
        </div>
      </div>

      {/* ── 题材联动横幅：来自题材卡片「涨停池↗」 ───────────────── */}
      {theme && (
        <div className="mb-3 flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm">
          <span className="text-zinc-700 dark:text-zinc-200">
            题材 <span className="font-semibold">{theme}</span> 梯队成员：
            <span className="font-mono">
              {membersInPool.length}/{memberSymbols.size}
            </span>{" "}
            只在池中（高亮行）
          </span>
          <label className="flex cursor-pointer items-center gap-1 text-xs text-zinc-500 dark:text-zinc-300">
            <input
              type="checkbox"
              checked={onlyMembers}
              onChange={(e) => setOnlyMembers(e.target.checked)}
              className="accent-rose-500"
            />
            只看成员
          </label>
          <div className="flex-1" />
          <Link href="/tape?tab=themes" className="text-xs text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200">
            返回题材梯队 ↩
          </Link>
          <button
            onClick={clearTheme}
            className="text-xs text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
          >
            清除高亮 ✕
          </button>
        </div>
      )}

      {error && (
        <div className="mb-4 shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-600 dark:text-amber-300">
          涨停池加载失败：{error}（数据源为东方财富 push2ex 免费接口）
        </div>
      )}

      <Panel
        className="min-h-0 flex-1 overflow-hidden"
        title={`共 ${shown.length} 只（按连板数排序${theme && !onlyMembers ? "，成员高亮" : ""}）`}
        source={records[0]?.source}
      >
        {records.length === 0 && !error ? (
          <p className="px-4 py-10 text-center text-sm text-zinc-400">今日暂无涨停（或非交易日）</p>
        ) : shown.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-zinc-400">
            「{theme}」的梯队成员均不在 {tradeDate || "当日"} 的涨停池中
          </p>
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
              {shown.map((r) => {
                const isMember = memberSymbols.has(r.symbol);
                return (
                  <tr
                    key={r.symbol}
                    className={`border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900 ${
                      isMember ? "bg-rose-500/[0.07]" : ""
                    }`}
                  >
                    <td className="px-2 py-2 font-mono text-xs text-zinc-400">
                      <Link href={workbenchUrl(r.symbol)} className="hover:text-sky-400 hover:underline">
                        {r.symbol}
                      </Link>
                    </td>
                    <td className="px-2 py-2">
                      {isMember && (
                        <span
                          className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-rose-500 align-middle"
                          title={`「${theme}」梯队成员`}
                        />
                      )}
                      {r.name}
                    </td>
                    <td className="px-2 py-2 text-right font-mono">{fmt(r.price)}</td>
                    <td className={`px-2 py-2 text-right font-mono ${pctColor(r.change_pct)}`}>{pctText(r.change_pct)}</td>
                    <td className="px-2 py-2 text-right font-mono">{r.consecutive_boards ?? "--"}</td>
                    <td className="px-2 py-2 text-right text-xs text-zinc-400">{r.boards_stat ?? "--"}</td>
                    <td className="max-w-[260px] truncate px-2 py-2 text-xs text-zinc-300" title={r.reason ?? ""}>{r.reason ?? "--"}</td>
                    <td className="px-2 py-2 text-right font-mono text-xs">{(r.break_count ?? 0) > 0 ? <span className="text-amber-400">{r.break_count}</span> : "0"}</td>
                    <td className="px-2 py-2 text-right font-mono text-xs">{fmtAmount(r.seal_amount)}</td>
                    <td className="px-2 py-2 text-right font-mono text-xs">{r.turnover_rate != null ? `${fmt(r.turnover_rate)}%` : "--"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
      <p className="mt-4 shrink-0 text-xs text-zinc-400">
        涨停原因已接入（同花顺官方口径），是题材梯队归属的证据来源；次日表现统计/题材标签随历史数据积累在后续版本提供。
      </p>
    </div>
  );
}
