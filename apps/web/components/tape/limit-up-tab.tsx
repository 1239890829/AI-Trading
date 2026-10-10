"use client";

import Link from "next/link";
import { useId, useMemo, useState } from "react";
import { useSurfaceScope } from "@/components/inspection/surface-scope";
import { inspectionClick, useInspection } from "@/components/inspection/inspection-context";
import { Panel } from "@/components/panel";
import { getLimitUpPoolSnapshot } from "@/lib/api";
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
import { LimitReason } from "@/components/detail/limit-reason";
import { tapeUrl } from "@/lib/routing";
import { StockLink, useStockRowNav } from "@/components/stock-link";
import { useResource } from "@/hooks/use-polling-fetch";

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
  const stockNav = useStockRowNav();
  const { searchParams, replaceSearch } = useSurfaceScope();
  const { open: openInspection } = useInspection();
  const dateId = useId();
  const urlDate = searchParams.get("date") || undefined;
  const resource = useResource(() => getLimitUpPoolSnapshot(urlDate), { key: urlDate ?? "latest", intervalMs: null });
  const records = useMemo(() => resource.error ? [] : resource.data?.pool ?? [], [resource.error, resource.data]);
  const tradeDate = resource.data?.trade_date ?? urlDate?.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3") ?? "";
  const error = resource.error instanceof Error ? resource.error.message : resource.error ? "读取失败" : null;
  const loading = resource.pending || (resource.data === undefined && !resource.error);
  const theme = searchParams.get("theme") ?? "";
  const memberSymbols = useMemo(() => new Set((searchParams.get("symbols") ?? "").split(",").filter(Boolean)), [searchParams]);
  const [onlyMembers, setOnlyMembers] = useState(false);

  function syncUrl(next: { date?: string; theme?: string; symbols?: string }) {
    // 在现有 URL 上增删参数（保留 tab= 等盘面页参数）
    const p = new URLSearchParams(searchParams.toString());
    if (next.date) p.set("date", next.date);
    else p.delete("date");
    if (next.theme) p.set("theme", next.theme);
    else p.delete("theme");
    if (next.symbols) p.set("symbols", next.symbols);
    else p.delete("symbols");
    replaceSearch(p);
  }

  const onDate = (v: string) => {
    // 只改 URL——取数由上面 `key` 变化触发（见该处说明，勿在此再调 load）
    syncUrl({ date: v, theme: theme, symbols: [...memberSymbols].join(",") });
  };

  function clearTheme() {
    setOnlyMembers(false);
    // 原样回写 URL 里已有的 date（而非归一成 tradeDate）：本函数只清题材联动，
    // 日期视图不该被改写。写回同值 ⇒ `urlDate` 不变 ⇒ 不触发多余重拉。
    syncUrl({ date: searchParams.get("date") ?? "", theme: "", symbols: "" });
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
        <div className="query-date text-xs text-zinc-600 dark:text-zinc-400">
          <label htmlFor={dateId}>按日期查询：</label>
          <input
            id={dateId}
            type="date"
            value={tradeDate.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3")}
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
          <label className="flex cursor-pointer items-center gap-1 text-xs text-zinc-600 dark:text-zinc-300">
            <input
              type="checkbox"
              checked={onlyMembers}
              onChange={(e) => setOnlyMembers(e.target.checked)}
              className="accent-rose-500"
            />
            只看成员
          </label>
          <div className="flex-1" />
          <Link href={tapeUrl("themes", {date: tradeDate, focus: theme})} onClick={inspectionClick(openInspection, {kind: "themes", date: tradeDate, focus: theme})} className="text-xs text-zinc-600 dark:text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200">
            返回题材梯队 ↩
          </Link>
          <button
            onClick={clearTheme}
            className="text-xs text-zinc-600 dark:text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
          >
            清除高亮 ✕
          </button>
        </div>
      )}

      {error && (
        <div className="mb-4 shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-800 dark:text-amber-300">
          涨停池加载失败：{error}
          <button type="button" className="quiet-action ml-2" onClick={resource.refresh}>重试读取</button>
        </div>
      )}

      <Panel
        className="min-h-0 flex-1 overflow-hidden"
        bodyClassName="data-scroll"
        title={error ? "数量待核对" : `共 ${shown.length} 只（按连板数排序${theme && !onlyMembers ? "，成员高亮" : ""}）`}
        source={records[0]?.source}
      >
        {error ? (
          <p className="px-4 py-10 text-center text-sm text-zinc-600 dark:text-zinc-400">读取失败，无法确认涨停数量或题材成员。请稍后重试。</p>
        ) : records.length === 0 ? (
          loading ? (
            <div className="space-y-2.5 px-3 py-4" aria-hidden>
              {Array.from({ length: 8 }, (_, i) => (
                <div key={i} className="flex items-center gap-3">
                  <div className="h-3.5 w-14 animate-pulse rounded bg-zinc-200/80 dark:bg-zinc-800/70" />
                  <div className="h-3.5 w-20 animate-pulse rounded bg-zinc-200/80 dark:bg-zinc-800/70" />
                  <div className="ml-auto h-3.5 w-24 animate-pulse rounded bg-zinc-200/60 dark:bg-zinc-800/50" />
                </div>
              ))}
            </div>
          ) : (
            <p className="px-4 py-10 text-center text-sm text-zinc-600 dark:text-zinc-400">所查交易日暂无涨停</p>
          )
        ) : shown.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-zinc-600 dark:text-zinc-400">
            「{theme}」的梯队成员均不在 {tradeDate || "当日"} 的涨停池中
          </p>
        ) : (
          <table className="data-table w-full text-sm" tabIndex={0} aria-label="涨停池，左右滚动查看全部列">
            <thead className="text-left text-xs text-zinc-600 dark:text-zinc-400">
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
                    onClick={stockNav(r.symbol)}
                    className={`cursor-pointer border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900 ${
                      isMember ? "bg-rose-500/[0.07]" : ""
                    }`}
                  >
                    <td className="px-2 py-2 font-mono text-xs text-zinc-600 dark:text-zinc-400">
                      <StockLink symbol={r.symbol}>
                        {r.symbol}
                      </StockLink>
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
                    <td className="px-2 py-2 text-right text-xs text-zinc-600 dark:text-zinc-400">{r.boards_stat ?? "--"}</td>
                    <td className="table-description px-2 py-2 text-xs text-zinc-600 dark:text-zinc-300"><LimitReason reason={r.reason} source={r.source} date={r.trade_date} compact /></td>
                    <td className="px-2 py-2 text-right font-mono text-xs">{(r.break_count ?? 0) > 0 ? <span className="text-amber-800 dark:text-amber-400">{r.break_count}</span> : "0"}</td>
                    <td className="px-2 py-2 text-right font-mono text-xs">{fmtAmount(r.seal_amount)}</td>
                    <td className="px-2 py-2 text-right font-mono text-xs">{r.turnover_rate != null ? `${fmt(r.turnover_rate)}%` : "--"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
      <p className="mt-4 shrink-0 text-xs text-zinc-600 dark:text-zinc-400">
        默认最近交易日。涨停原因保留数据源原文；备用源未提供时如实标注缺失，题材标签不替代已核实的事件因果。
      </p>
    </div>
  );
}
