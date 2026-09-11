"use client";

/**
 * 盘中跟踪台账面板（猎场批次 A，2026-09-08 用户需求 7/8/9/10/11）：
 * - 当日台账：入选时间 / 题材归属 / 入选价 / 收盘价 / 盈亏 / 判定 / 入选说明；
 *   tracking 行持续保留（不被中途移除），settled 行收盘清算后展示盈亏与判定。
 * - 历史记录：近 N 个交易日逐日统计（胜率/平均盈亏），收盘后仍可查。
 * - 三态：盈亏/收盘价缺失显式 --（未清算或快照缺失），不臆造。
 */
import { useCallback, useEffect, useState } from "react";

import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { getWatchLedger, placePaperOrder, type WatchLedgerPayload } from "@/lib/api";
import { fmt, pctColor, pctText } from "@/lib/format";

export function WatchLedgerPanel() {
  const [data, setData] = useState<WatchLedgerPayload | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [orderNote, setOrderNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await getWatchLedger(undefined, 5));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  // P1-27 收编 usePollingFetch（挂载即拉 + 定时轮询），语义不变
  usePollingFetch(load, 30_000); // 台账 30s 刷新（低频不干扰判断）

  /** 模拟建仓（系统审查 #7：闭环「选出→验证」）：入选价 × 100 股，撮合规则硬拦截 */
  const quickBuy = async (symbol: string, price: number) => {
    try {
      const res = await placePaperOrder(symbol, "buy", price, 100);
      setOrderNote(`${symbol} 模拟买单已提交（100 股 @ ${price}）——状态见工作台交易页签`);
      void load();
      return res;
    } catch (e) {
      setOrderNote(`${symbol} 建仓失败：${e instanceof Error ? e.message : String(e)}`);
      return null;
    }
  };

  const st = data?.stats;

  return (
    <section className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
          跟踪台账
          <span className="ml-2 text-[11px] font-normal text-zinc-600 dark:text-zinc-400">
            {data ? `${data.trade_date} · ${st?.total ?? 0} 只` : "加载中…"}
          </span>
        </h3>
        <div className="flex items-center gap-2 text-[11px]">
          {st && (
            <span className="text-zinc-600 dark:text-zinc-400">
              已清算 {st.settled} · 胜率{" "}
              <span className={st.win_rate != null && st.win_rate >= 0.5 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down"}>
                {st.win_rate != null ? `${(st.win_rate * 100).toFixed(0)}%` : "--"}
              </span>
              {st.avg_pnl_pct != null && (
                <span className={`ml-1 font-mono ${pctColor(st.avg_pnl_pct)}`}>均盈亏 {pctText(st.avg_pnl_pct)}</span>
              )}
            </span>
          )}
          <button
            onClick={() => setShowHistory((v) => !v)}
            className="rounded border border-zinc-200 px-1.5 py-0.5 text-zinc-600 dark:text-zinc-400 hover:text-zinc-600 dark:border-zinc-700 dark:hover:text-zinc-200"
          >
            历史 {showHistory ? "▲" : "▼"}
          </button>
        </div>
      </div>

      {error && <p className="mb-2 rounded bg-amber-500/10 px-2 py-1 text-[11px] text-amber-800 dark:text-amber-600">台账加载失败：{error}</p>}
      {orderNote && <p className="mb-2 rounded bg-sky-500/10 px-2 py-1 text-[11px] text-sky-700 dark:text-sky-300">{orderNote}</p>}

      {data && (data.rows?.length ?? 0) === 0 && (
        <p className="py-3 text-center text-[11px] text-zinc-600 dark:text-zinc-400">
          今日暂无跟踪记录——盘中 watcher 确认 / 买点触发 / 机会候选首见时自动登记
        </p>
      )}

      {data && (data.rows?.length ?? 0) > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-zinc-200 text-left text-[10px] text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
                <th className="py-1 pr-2 font-normal">入选时间</th>
                <th className="py-1 pr-2 font-normal">个股</th>
                <th className="py-1 pr-2 font-normal">题材</th>
                <th className="py-1 pr-2 text-right font-normal">入选价</th>
                <th className="py-1 pr-2 text-right font-normal">收盘</th>
                <th className="py-1 pr-2 text-right font-normal">盈亏</th>
                <th className="py-1 pr-2 font-normal">判定</th>
                <th className="py-1 font-normal">入选说明</th>
              </tr>
            </thead>
            <tbody>
              {(data?.rows ?? []).map((r) => (
                <tr key={r.id} className="border-b border-zinc-100 align-top last:border-0 dark:border-zinc-800/60">
                  <td className="py-1.5 pr-2 font-mono text-[11px] text-zinc-600 dark:text-zinc-400">{r.entry_time || "--"}</td>
                  <td className="py-1.5 pr-2">
                    <span className="font-medium text-zinc-800 dark:text-zinc-100">{r.name || r.symbol}</span>
                    <span className="ml-1 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{r.symbol}</span>
                    {r.is_leader && (
                      <span className="ml-1 rounded bg-rose-500/10 px-1 text-[10px] text-rose-700 dark:text-rose-300" title="题材内最高连板（龙头识别）">
                        龙头
                      </span>
                    )}
                    {r.boards > 0 && <span className="ml-1 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{r.boards}板</span>}
                  </td>
                  <td className="max-w-[8em] truncate py-1.5 pr-2 text-zinc-600 dark:text-zinc-400" title={r.source_theme}>
                    {r.source_theme || "--"}
                  </td>
                  <td className="py-1.5 pr-2 text-right font-mono tabular-nums text-zinc-600 dark:text-zinc-300">
                    {r.entry_price != null ? fmt(r.entry_price) : "--"}
                  </td>
                  <td className="py-1.5 pr-2 text-right font-mono tabular-nums text-zinc-600 dark:text-zinc-300">
                    {r.close_price != null ? fmt(r.close_price) : "--"}
                  </td>
                  <td className={`py-1.5 pr-2 text-right font-mono tabular-nums ${pctColor(r.pnl_pct ?? null)}`}>
                    {r.pnl_pct != null ? pctText(r.pnl_pct) : "--"}
                  </td>
                  <td className="py-1.5 pr-2">
                    {r.verdict === "success" && <span className="text-up-ink dark:text-up">✓ 成功</span>}
                    {r.verdict === "fail" && <span className="text-down-ink dark:text-down">✗ 失败</span>}
                    {r.verdict === "flat" && <span className="text-zinc-600 dark:text-zinc-400">— 持平</span>}
                    {r.verdict === null && (
                      <span className="text-zinc-600 dark:text-zinc-400" title={r.verdict_reason ?? "收盘后清算"}>
                        跟踪中
                      </span>
                    )}
                    {r.status === "tracking" && r.entry_price != null && (
                      <button
                        onClick={() => void quickBuy(r.symbol, r.entry_price as number)}
                        className="ml-1 rounded border border-up/40 px-1 text-[10px] text-up-ink dark:text-up transition-colors hover:bg-up/10"
                        title={`模拟建仓：100 股 @ ${r.entry_price}（撮合引擎硬拦截：T+1/整手/停牌拒）`}
                      >
                        模拟建仓
                      </button>
                    )}
                  </td>
                  <td className="max-w-[16em] py-1.5 text-[11px] text-zinc-600 dark:text-zinc-400">
                    <span className="line-clamp-2" title={reasonText(r)}>
                      {reasonText(r)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showHistory && (
        <div className="mt-2 border-t border-zinc-100 pt-2 dark:border-zinc-800">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-left text-zinc-600 dark:text-zinc-400">
                <th className="py-1 pr-2 font-normal">日期</th>
                <th className="py-1 pr-2 text-right font-normal">跟踪</th>
                <th className="py-1 pr-2 text-right font-normal">成功</th>
                <th className="py-1 pr-2 text-right font-normal">失败</th>
                <th className="py-1 pr-2 text-right font-normal">胜率</th>
                <th className="py-1 text-right font-normal">平均盈亏</th>
              </tr>
            </thead>
            <tbody>
              {(data?.history ?? []).map((h) => (
                <tr key={h.trade_date} className="border-t border-zinc-100 dark:border-zinc-800/60">
                  <td className="py-1 pr-2 font-mono text-zinc-600 dark:text-zinc-400">{h.trade_date}</td>
                  <td className="py-1 pr-2 text-right font-mono">{h.stats.total}</td>
                  <td className="py-1 pr-2 text-right font-mono text-up-ink dark:text-up">{h.stats.success}</td>
                  <td className="py-1 pr-2 text-right font-mono text-down-ink dark:text-down">{h.stats.fail}</td>
                  <td className="py-1 pr-2 text-right font-mono">
                    {h.stats.win_rate != null ? `${(h.stats.win_rate * 100).toFixed(0)}%` : "--"}
                  </td>
                  <td className={`py-1 text-right font-mono ${pctColor(h.stats.avg_pnl_pct ?? null)}`}>
                    {h.stats.avg_pnl_pct != null ? pctText(h.stats.avg_pnl_pct) : "--"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-2 text-[10px] leading-relaxed text-zinc-600 dark:text-zinc-400">
        台账说明：入选即登记（当日唯一，盘中不移除）→ 收盘清算（入选价 vs 收盘价）→ 逐股判定与统计；判定口径 收盘 ≥ 入选 = 成功、亏 ≤2% = 持平、否则失败。历史记录收盘后可查。
      </p>
    </section>
  );
}

function reasonText(r: { reason: Record<string, unknown>; source_theme: string }): string {
  const parts: string[] = [];
  if (r.source_theme) parts.push(`题材 ${r.source_theme}`);
  const reason = r.reason ?? {};
  if (reason.role) parts.push(`角色 ${String(reason.role)}`);
  if (reason.text) parts.push(String(reason.text).slice(0, 80));
  if (reason.stage) parts.push(`阶段 ${String(reason.stage)}`);
  return parts.join("；") || "（首见登记）";
}
