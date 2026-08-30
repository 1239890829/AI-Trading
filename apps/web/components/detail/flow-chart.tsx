/** 资金图页签内容（左图右明细）。纯展示组件——数据加载在 stock-detail 壳内。 */
import { fmtAmount } from "@/lib/format";

export interface FlowRow {
  date: string;
  close?: number | null;
  change_pct?: number | null;
  net_main?: number | null;
  net_super?: number | null;
  net_big?: number | null;
  net_mid?: number | null;
  net_small?: number | null;
  source: string;
}

export interface CapitalFlow {
  days: number;
  flow: FlowRow[];
  streak_in: number;
  definition: string;
}

export function FlowChart({ flow }: { flow: CapitalFlow }) {
  return (
    <div className="grid h-full grid-cols-1 overflow-hidden md:grid-cols-[minmax(0,1fr),minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col border-r border-zinc-200 px-3 py-2 dark:border-zinc-800">
        <div className="flex shrink-0 flex-wrap items-center gap-x-4 text-xs text-zinc-400">
          <span>
            连续净流入 <span className="font-mono text-sm text-zinc-100">{flow.streak_in}</span> 天
          </span>
          <span title={flow.definition}>口径说明 ⓘ</span>
        </div>
        <div className="mt-2 flex min-w-0 flex-1 items-stretch gap-[2px]">
          {flow.flow.map((r) => {
            const max = Math.max(...flow.flow.map((x) => Math.abs(x.net_main ?? 0)), 1);
            const v = r.net_main ?? 0;
            const h = Math.max(3, (Math.abs(v) / max) * 100);
            return (
              <div key={r.date} className="group relative flex min-w-0 flex-1 flex-col justify-center" title={`${r.date} ${fmtAmount(v)}`}>
                <div className="flex h-1/2 items-end">{v > 0 && <div className="w-full rounded-t bg-[rgba(244,63,94,0.75)]" style={{ height: `${h}%` }} />}</div>
                <div className="flex h-1/2 items-start">{v < 0 && <div className="w-full rounded-b bg-[rgba(16,185,129,0.75)]" style={{ height: `${h}%` }} />}</div>
              </div>
            );
          })}
        </div>
        <div className="shrink-0 border-t border-zinc-100 pt-1 text-[10px] text-zinc-500 dark:border-zinc-800/60">{flow.definition}</div>
      </div>
      {/* 右：明细表 */}
      <div className="min-h-0 overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-zinc-50 text-left text-xs text-zinc-400 dark:bg-zinc-900/50">
            <tr>{["日期", "主力净流入", "超大单", "大单", "中单", "小单"].map((h) => (
              <th key={h} className={`px-3 py-2 font-medium ${h === "日期" ? "" : "text-right"}`}>{h}</th>
            ))}</tr>
          </thead>
          <tbody>
            {[...flow.flow].reverse().map((r) => (
              <tr key={r.date} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                <td className="px-3 py-1.5 font-mono text-xs">{r.date}</td>
                <td className={`px-2 py-1.5 text-right font-mono text-xs ${(r.net_main ?? 0) > 0 ? "text-up" : "text-down"}`}>{fmtAmount(r.net_main)}</td>
                {[r.net_super, r.net_big, r.net_mid, r.net_small].map((v, j) => (
                  <td key={j} className={`px-3 py-1.5 text-right font-mono text-xs ${(v ?? 0) > 0 ? "text-up" : "text-down"}`}>{fmtAmount(v)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
