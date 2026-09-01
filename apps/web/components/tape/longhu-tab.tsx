"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Panel } from "@/components/panel";
import { getLonghu } from "@/lib/api";
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
import { workbenchUrl } from "@/lib/routing";
import type { LongHuRecord } from "@/types/market";

/** 盘面页 · 龙虎榜 tab（原 /longhu 页迁移，2026-09-01 系统重构）。 */

/** 统计区间文案：1=当日榜，3=三日榜（不同触发条件，交易所分别披露）。 */
function scopeText(rd?: number | null): string {
  if (rd == null) return "--";
  return rd === 1 ? "日榜" : `${rd}日榜`;
}

/** 本地日期 YYYY-MM-DD（客户端计算，避免 SSR 与浏览器时区不一致导致 hydration mismatch）。 */
function todayISO(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function LonghuTab() {
  const [records, setRecords] = useState<LongHuRecord[]>([]);
  const [tradeDate, setTradeDate] = useState("");
  const [error, setError] = useState<string | null>(null);
  // 数据返回那一刻的客户端时间，用于判断"这份数据是不是盘中未定稿"。
  // 必须在拿到数据后再取（放在渲染期会 SSR 水合不一致，放在挂载 effect 里会被 lint 告警）。
  const [now, setNow] = useState<Date | null>(null);

  const load = useCallback(async (date?: string) => {
    try {
      const list = await getLonghu(date);
      setRecords(list);
      setTradeDate(list[0]?.trade_date ?? "");
      setNow(new Date());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
      setRecords([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 数据源返回的是原序（实测既非升序也非降序），这里显式排序，别让表格标题说谎。
  // 排序口径：日榜(1) 优先于三日榜(3)——两者是不同统计区间的累计值，金额不可直接比大小；
  // 同区间内再按净买额降序（龙虎榜的阅读习惯是先看谁买得最多）。
  const sorted = useMemo(
    () =>
      [...records].sort((a, b) => {
        const ra = a.range_days ?? 99;
        const rb = b.range_days ?? 99;
        if (ra !== rb) return ra - rb;
        return (b.net_buy ?? 0) - (a.net_buy ?? 0);
      }),
    [records],
  );
  // 同一只股票可同时上当日榜与三日榜 → 记录数多于股票数，标题要分开说，
  // 否则用户会以为"76 条"里有 6 条是脏数据。
  const stockCount = useMemo(() => new Set(records.map((r) => r.symbol)).size, [records]);
  // 龙虎榜盘后定稿。盘中（15:30 前）查当天会拿到未定稿快照，必须显式标注，
  // 否则用户会把盘中快照当终稿用。
  const isIntradaySnapshot = useMemo(() => {
    if (!now || !tradeDate || tradeDate !== todayISO()) return false;
    return now.getHours() * 60 + now.getMinutes() < 15 * 60 + 30;
  }, [now, tradeDate]);
  // ths 龙虎榜个股明细不含 close / turnover_rate / amount（成交额仅游资榜提供）。
  // 恒空的列直接隐藏并在页脚说明原因，不留一列 "--" 让用户猜是不是又坏了；
  // 换成东财等含这些字段的数据源时会自动恢复。
  const hasClose = useMemo(() => records.some((r) => r.close != null), [records]);
  const hasTurnover = useMemo(() => records.some((r) => r.turnover_rate != null), [records]);
  const hasAmount = useMemo(() => records.some((r) => r.amount != null), [records]);
  const hiddenCols = [
    !hasClose ? "收盘价" : null,
    !hasTurnover ? "换手率" : null,
    !hasAmount ? "榜内成交" : null,
  ].filter(Boolean) as string[];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="mb-4 flex shrink-0 flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">龙虎榜 · {tradeDate || "…"}</h2>
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
        <div className="mb-4 shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-600 dark:text-amber-300">
          {/* 加载失败时 records 为空，无从判断实际数据源，不猜、不写死源名 */}
          龙虎榜加载失败：{error}
        </div>
      )}

      <Panel
        className="min-h-0 flex-1 overflow-hidden"
        title={
          records.length > 0
            ? `共 ${records.length} 条 / ${stockCount} 只股票（日榜优先·按净买额降序）`
            : "共 0 条"
        }
        source={records[0]?.source}
      >
        {records.length === 0 && !error ? (
          <p className="px-4 py-10 text-center text-sm text-zinc-400">暂无数据（龙虎榜盘后披露，当日数据需收盘后查询）</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-zinc-400">
              <tr className="border-b border-zinc-200 dark:border-zinc-800">
                {["代码", "名称", "区间", "涨幅", ...(hasClose ? ["收盘"] : []), ...(hasTurnover ? ["换手"] : []), ...(hasAmount ? ["榜内成交"] : []), "净买额", "买入", "卖出", "游资净额", "机构净额", "上榜原因"].map((h) => (
                  <th key={h} className={`px-2 py-2 font-medium ${["名称", "区间", "上榜原因"].includes(h) ? "" : "text-right"}`}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, i) => (
                // 同股多榜并存，key 必须带 range_days，否则 React 复用错行
                <tr key={`${r.symbol}-${r.range_days ?? "na"}-${i}`} className="border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900">
                  <td className="px-2 py-2 font-mono text-xs text-zinc-400">
                    <Link href={workbenchUrl(r.symbol)} className="hover:text-sky-400 hover:underline">
                      {r.symbol}
                    </Link>
                  </td>
                  <td className="px-2 py-2">{r.name}</td>
                  <td className="px-2 py-2 text-xs text-zinc-400" title="统计区间：交易所按不同触发条件分别披露当日榜与三日榜，两者金额不可相加">
                    {scopeText(r.range_days)}
                  </td>
                  <td className={`px-2 py-2 text-right font-mono ${pctColor(r.change_pct)}`}>{pctText(r.change_pct)}</td>
                  {hasClose && <td className="px-2 py-2 text-right font-mono">{fmt(r.close)}</td>}
                  {hasTurnover && (
                    <td className="px-2 py-2 text-right font-mono text-xs">{r.turnover_rate != null ? `${fmt(r.turnover_rate)}%` : "--"}</td>
                  )}
                  {hasAmount && <td className="px-2 py-2 text-right font-mono text-xs">{fmtAmount(r.amount)}</td>}
                  <td className={`px-2 py-2 text-right font-mono text-xs ${(r.net_buy ?? 0) > 0 ? "text-up" : "text-down"}`}>
                    {fmtAmount(r.net_buy)}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-xs text-zinc-400">{fmtAmount(r.buy_amount)}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs text-zinc-400">{fmtAmount(r.sell_amount)}</td>
                  {/* 游资/机构净额缺失 = 该榜单无对应席位参与，与"参与但净额为 0"不同，显示 -- 而非 0 */}
                  <td className={`px-2 py-2 text-right font-mono text-xs ${(r.hot_money_net_value ?? 0) > 0 ? "text-up" : (r.hot_money_net_value ?? 0) < 0 ? "text-down" : ""}`}>
                    {fmtAmount(r.hot_money_net_value)}
                  </td>
                  <td className={`px-2 py-2 text-right font-mono text-xs ${(r.org_net_value ?? 0) > 0 ? "text-up" : (r.org_net_value ?? 0) < 0 ? "text-down" : ""}`}>
                    {fmtAmount(r.org_net_value)}
                  </td>
                  <td className="px-2 py-2 text-xs text-zinc-400" title={r.reason ?? ""}>
                    {(r.reason ?? "--").slice(0, 22)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      <div className="mt-4 shrink-0 space-y-1 text-xs text-zinc-400">
        {isIntradaySnapshot && (
          <p className="text-amber-500">
            当前为盘中未定稿快照：龙虎榜以收盘后交易所披露为准，盘中数据可能继续新增或变化。
          </p>
        )}
        {stockCount > 0 && records.length > stockCount && (
          <p>
            记录数多于股票数属正常：同一只股票可同时上「当日榜」与「三日榜」（交易所按不同触发条件分别披露），
            两者买卖净额是不同区间的累计值，<span className="text-amber-500">不可相加</span>。
          </p>
        )}
        {hiddenCols.length > 0 && (
          <p>当前数据源个股明细不含{hiddenCols.join("、")}，相关列已自动隐藏（换数据源后恢复）。</p>
        )}
        <p>个股席位明细已可在工作台详情「龙虎榜」页签查看；上榜原因阈值将按交易所规则配置化。</p>
      </div>
    </div>
  );
}
