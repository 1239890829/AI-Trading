"use client";

import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/panel";
import {
  getScreener,
  type ScreenerItem,
  type ScreenerPayload,
} from "@/lib/api";

/** 全市场选股器（Phase 5）：截面条件过滤 → TDX 日K 技术评分卡。
 * 评分=多因子共振强度（可解释依据+失效条件），仅描述技术面状态，不构成买卖建议。
 * 首跑 15-25s（150 只候选逐只拉日K），后端缓存 30 分钟。 */

function pctText(pct: number): string {
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

function pctCls(pct: number): string {
  if (pct > 0) return "text-red-600 dark:text-red-400";
  if (pct < 0) return "text-emerald-600 dark:text-emerald-400";
  return "text-zinc-400";
}

function gradeCls(grade: string): string {
  switch (grade) {
    case "A":
      return "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900";
    case "B":
      return "bg-zinc-200 text-zinc-800 dark:bg-zinc-700 dark:text-zinc-100";
    case "C":
      return "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400";
    default:
      return "bg-zinc-50 text-zinc-400 dark:bg-zinc-900 dark:text-zinc-600";
  }
}

function biasChipCls(bias: string): string {
  if (bias === "bull") return "bg-red-500/10 text-red-600 dark:text-red-400";
  if (bias === "bear") return "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400";
  return "bg-zinc-500/10 text-zinc-500 dark:text-zinc-400";
}

export default function ScreenerPage() {
  const [data, setData] = useState<ScreenerPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ranAt, setRanAt] = useState("");

  // 条件（提交时才生效）
  const [changeLow, setChangeLow] = useState("-2");
  const [changeHigh, setChangeHigh] = useState("9");
  const [minAmountYi, setMinAmountYi] = useState("1");
  const [minTurnover, setMinTurnover] = useState("2");
  const [excludeSt, setExcludeSt] = useState(true);
  const [excludeBj, setExcludeBj] = useState(true);
  const [excludeNew, setExcludeNew] = useState(true);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await getScreener({
        changeLow: Number(changeLow),
        changeHigh: Number(changeHigh),
        minAmountYi: Number(minAmountYi),
        minTurnover: Number(minTurnover),
        excludeSt, excludeBj, excludeNew,
        limit: 50,
      });
      setData(payload);
      setRanAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [changeLow, changeHigh, minAmountYi, minTurnover, excludeSt, excludeBj, excludeNew]);

  useEffect(() => {
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {error && (
        <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-600 dark:text-amber-300">
          {error}
        </div>
      )}

      <Panel
        title="全市场选股器"
        className="min-h-0 flex-1"
        bodyClassName="overflow-hidden flex flex-col"
        extra={
          data && (
            <span className="text-[10px] tabular-nums text-zinc-400">
              评分器 {data.scorer_version}
            </span>
          )
        }
      >
        {/* 条件工具条 */}
        <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-zinc-200 px-3 py-2 text-xs dark:border-zinc-800">
          <label className="flex items-center gap-1">
            涨幅带
            <input value={changeLow} onChange={(e) => setChangeLow(e.target.value)} className="w-14 rounded border border-zinc-300 bg-transparent px-1.5 py-0.5 tabular-nums dark:border-zinc-700" />
            <span className="text-zinc-400">~</span>
            <input value={changeHigh} onChange={(e) => setChangeHigh(e.target.value)} className="w-14 rounded border border-zinc-300 bg-transparent px-1.5 py-0.5 tabular-nums dark:border-zinc-700" />
            <span className="text-zinc-400">%</span>
          </label>
          <label className="flex items-center gap-1">
            成交额≥
            <input value={minAmountYi} onChange={(e) => setMinAmountYi(e.target.value)} className="w-14 rounded border border-zinc-300 bg-transparent px-1.5 py-0.5 tabular-nums dark:border-zinc-700" />
            <span className="text-zinc-400">亿</span>
          </label>
          <label className="flex items-center gap-1">
            换手≥
            <input value={minTurnover} onChange={(e) => setMinTurnover(e.target.value)} className="w-12 rounded border border-zinc-300 bg-transparent px-1.5 py-0.5 tabular-nums dark:border-zinc-700" />
            <span className="text-zinc-400">%</span>
          </label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={excludeSt} onChange={(e) => setExcludeSt(e.target.checked)} /> 排除 ST
          </label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={excludeBj} onChange={(e) => setExcludeBj(e.target.checked)} /> 排除北交所
          </label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={excludeNew} onChange={(e) => setExcludeNew(e.target.checked)} /> 排除次新
          </label>
          <button
            onClick={() => void run()}
            disabled={loading}
            className="rounded bg-zinc-900 px-3 py-1 font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
          >
            {loading ? "计算中…" : data?.cached ? "重新计算" : "运行"}
          </button>
          {data && (
            <span className="ml-auto tabular-nums text-zinc-400">
              扫描 {data.scanned} → 过滤 {data.filtered} → 评分 {data.scored}
              {data.failed > 0 && <span className="text-amber-500">（{data.failed} 失败）</span>}
              {data.cached && " · 缓存"}
              {data.snapshot_time && ` · 数据时点 ${data.snapshot_time}`}
              {ranAt && ` · ${ranAt}`}
            </span>
          )}
        </div>

        {/* 结果表 */}
        <div className="min-h-0 flex-1 overflow-auto">
          {loading && !data ? (
            <p className="px-4 py-10 text-center text-sm text-zinc-400">
              正在扫描全市场并逐只拉取日K计算评分…（首次约 15-25 秒）
            </p>
          ) : !data ? (
            <p className="px-4 py-10 text-center text-sm text-zinc-400">输入条件后点击「运行」</p>
          ) : data.items.length === 0 ? (
            <p className="px-4 py-10 text-center text-sm text-zinc-400">无符合条件的标的——放宽条件后重试</p>
          ) : (
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-zinc-50 text-left text-zinc-400 dark:bg-zinc-900">
                <tr>
                  <th className="px-2 py-1.5 font-medium">#</th>
                  <th className="px-2 py-1.5 font-medium">评分</th>
                  <th className="px-2 py-1.5 font-medium">标的</th>
                  <th className="px-2 py-1.5 text-right font-medium">现价</th>
                  <th className="px-2 py-1.5 text-right font-medium">涨幅</th>
                  <th className="px-2 py-1.5 text-right font-medium">换手</th>
                  <th className="px-2 py-1.5 text-right font-medium">成交额</th>
                  <th className="px-2 py-1.5 text-right font-medium">流通市值</th>
                  <th className="px-2 py-1.5 font-medium">技术面依据</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((it: ScreenerItem, idx: number) => (
                  <tr
                    key={it.symbol}
                    className="cursor-pointer border-t border-zinc-100 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-800/40"
                    onClick={() => window.open(`/workbench?symbol=${it.symbol}`, "_self")}
                  >
                    <td className="px-2 py-1.5 tabular-nums text-zinc-400">{idx + 1}</td>
                    <td className="px-2 py-1.5">
                      <span className="tabular-nums font-semibold">{it.score.toFixed(1)}</span>
                      <span className={`ml-1.5 rounded px-1 py-0.5 text-[10px] font-medium ${gradeCls(it.grade)}`}>
                        {it.grade}
                      </span>
                    </td>
                    <td className="px-2 py-1.5">
                      <span className="font-medium">{it.name}</span>
                      <span className="ml-1 text-zinc-400">{it.symbol}</span>
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{it.price.toFixed(2)}</td>
                    <td className={`px-2 py-1.5 text-right tabular-nums ${pctCls(it.change_pct)}`}>
                      {pctText(it.change_pct)}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-zinc-500">
                      {it.turnover_rate != null ? `${it.turnover_rate.toFixed(1)}%` : "--"}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-zinc-500">{it.amount_yi.toFixed(1)}亿</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-zinc-500">
                      {it.float_cap_yi != null ? `${it.float_cap_yi.toFixed(0)}亿` : "--"}
                    </td>
                    <td className="px-2 py-1.5">
                      <div className="flex max-w-[430px] flex-wrap gap-1">
                        {it.signals.slice(0, 4).map((s) => (
                          <span
                            key={`${it.symbol}-${s.name}`}
                            title={`${s.detail}${it.fail_conditions.length ? `｜失效：${it.fail_conditions.join("；")}` : ""}`}
                            className={`rounded px-1 py-0.5 text-[10px] ${biasChipCls(s.bias)}`}
                          >
                            {s.name} {s.score >= 0.7 ? "↑" : s.score <= 0.3 ? "↓" : "–"}
                          </span>
                        ))}
                        <span className="max-w-[200px] truncate text-[10px] text-zinc-400" title={it.summary}>
                          {it.summary}
                        </span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {data && (
          <div className="shrink-0 border-t border-zinc-200 px-3 py-1.5 text-[10px] text-zinc-400 dark:border-zinc-800">
            {data.disclaimers.join("；")}
          </div>
        )}
      </Panel>
    </div>
  );
}
