"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ThemeCardView } from "@/components/theme-card";
import { getThemes } from "@/lib/api";
import { fmt } from "@/lib/format";
import type { ThemeBoardPayload } from "@/types/market";

/**
 * 强势题材梯队看板。
 *
 * 与 /limit-up 的区别：涨停池是平铺列表，这里以题材为容器重组，
 * 回答三个问题——题材是否成建制、梯队是否健康、资金是否持续。
 */

type SortKey = "strength" | "boards" | "count";

const SORT_LABELS: Record<SortKey, string> = {
  strength: "综合强度",
  boards: "连板高度",
  count: "涨停家数",
};

/** 连板高度筛选档位：0 = 不限 */
const BOARD_FILTERS = [0, 2, 3, 4, 5];

/** 成建制筛选：对应后端 formation 分档 */
const COUNT_FILTERS = [
  { v: 2, label: "≥2 家" },
  { v: 3, label: "≥3 家" },
  { v: 5, label: "仅成建制" },
];

export default function ThemesPage() {
  const searchParams = useSearchParams();
  const [data, setData] = useState<ThemeBoardPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [date, setDate] = useState<string>(searchParams.get("date") ?? "");
  const [sort, setSort] = useState<SortKey>((searchParams.get("sort") as SortKey) || "strength");
  const [minBoards, setMinBoards] = useState(() => {
    const v = searchParams.get("min_boards");
    return v ? Number(v) : 0;
  });
  const [minCount, setMinCount] = useState(() => {
    const v = searchParams.get("min_count");
    return v ? Number(v) : 2;
  });
  const [showCaveats, setShowCaveats] = useState(false);

  const load = useCallback(
    async (d?: string, s: SortKey = sort, mb = minBoards, mc = minCount) => {
      setLoading(true);
      try {
        const r = await getThemes({
          date: d || undefined,
          sort: s,
          minBoards: mb || undefined,
          minCount: mc || undefined,
          limit: 60,
        });
        setData(r);
        setError(null);
      } catch (e) {
        setError((e as Error).message);
        setData(null);
      } finally {
        setLoading(false);
      }
    },
    [sort, minBoards, minCount]
  );

  useEffect(() => {
    void load();
    // 仅在挂载时拉一次；后续筛选由各自的 onChange 触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function updateUrl(
    d?: string,
    s: SortKey = sort,
    mb = minBoards,
    mc = minCount,
  ) {
    const params = new URLSearchParams();
    if (d) params.set("date", d);
    if (s !== "strength") params.set("sort", s);
    if (mb) params.set("min_boards", String(mb));
    if (mc !== 2) params.set("min_count", String(mc));
    const qs = params.toString();
    const url = qs ? `?${qs}` : window.location.pathname;
    window.history.replaceState({}, "", url);
  }

  const onSort = (s: SortKey) => {
    setSort(s);
    updateUrl(date || undefined, s, minBoards, minCount);
    void load(date || undefined, s);
  };
  const onBoards = (v: number) => {
    setMinBoards(v);
    updateUrl(date || undefined, sort, v, minCount);
    void load(date || undefined, sort, v);
  };
  const onCount = (v: number) => {
    setMinCount(v);
    updateUrl(date || undefined, sort, minBoards, v);
    void load(date || undefined, sort, minBoards, v);
  };
  const onDate = (v: string) => {
    setDate(v);
    updateUrl(v || undefined, sort, minBoards, minCount);
    void load(v || undefined);
  };

  const broken = useMemo(() => data?.broken_ladder ?? [], [data]);

  return (
    <main className="mx-auto w-full max-w-[1600px] px-4 py-4">
      {/* ── 头部 ─────────────────────────────────────────────── */}
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            强势题材梯队看板
          </h1>
          <p className="mt-0.5 text-xs text-zinc-400">
            {data ? `${data.trade_date}` : "…"}
            {data?.prev_trade_date && ` · 对照 ${data.prev_trade_date}`}
            {data &&
              ` · 涨停 ${data.summary.limit_up_total} 只 / 识别题材 ${data.summary.theme_count} 个 / 最高 ${data.summary.market_max_boards} 板`}
            {data?.summary.market_break_rate != null &&
              ` / 全市场炸板率 ${(data.summary.market_break_rate * 100).toFixed(1)}%`}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3 text-xs">
          <label className="flex items-center gap-1.5">
            <span className="text-zinc-400">日期</span>
            <input
              type="date"
              value={date}
              onChange={(e) => onDate(e.target.value)}
              className="rounded-md border border-zinc-200 bg-transparent px-2 py-1 text-zinc-700 dark:border-zinc-700 dark:text-zinc-200"
            />
          </label>

          <div className="flex items-center gap-1">
            <span className="text-zinc-400">排序</span>
            {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
              <button
                key={k}
                onClick={() => onSort(k)}
                className={`rounded-md px-2 py-1 transition-colors ${
                  sort === k
                    ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "border border-zinc-200 text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-100"
                }`}
              >
                {SORT_LABELS[k]}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1">
            <span className="text-zinc-400">连板</span>
            {BOARD_FILTERS.map((v) => (
              <button
                key={v}
                onClick={() => onBoards(v)}
                className={`rounded-md px-2 py-1 transition-colors ${
                  minBoards === v
                    ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "border border-zinc-200 text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-100"
                }`}
              >
                {v === 0 ? "不限" : `${v}板+`}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1">
            <span className="text-zinc-400">家数</span>
            {COUNT_FILTERS.map((f) => (
              <button
                key={f.v}
                onClick={() => onCount(f.v)}
                className={`rounded-md px-2 py-1 transition-colors ${
                  minCount === f.v
                    ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "border border-zinc-200 text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-100"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-300">
          题材看板加载失败：{error}
        </div>
      )}

      {loading && !data && <p className="py-16 text-center text-sm text-zinc-400">加载中…</p>}

      {data && data.themes.length === 0 && (
        <p className="py-16 text-center text-sm text-zinc-400">
          当前筛选条件下没有题材（连板 ≥{minBoards} 板 / 涨停 ≥{minCount} 家）
        </p>
      )}

      {/* ── 题材卡片 ─────────────────────────────────────────── */}
      <div className={`space-y-3 ${loading ? "opacity-60 transition-opacity" : ""}`}>
        {data?.themes.map((c, i) => (
          <ThemeCardView key={c.theme} card={c} rank={i + 1} />
        ))}
      </div>

      {/* ── 断板股 ───────────────────────────────────────────── */}
      {broken.length > 0 && (
        <section className="mt-4 overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800">
          <header className="border-b border-zinc-200 bg-zinc-50 px-4 py-2.5 dark:border-zinc-800 dark:bg-zinc-900/40">
            <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-200">
              断板股
              <span className="ml-2 text-xs font-normal text-zinc-400">
                昨日连板、今日未封板 —— 梯队断层与情绪退潮的先行信号（{broken.length} 只）
              </span>
            </h2>
          </header>
          <div className="overflow-x-auto px-4 py-3">
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="border-b border-zinc-200 text-left text-[11px] text-zinc-400 dark:border-zinc-800">
                  <th className="py-1.5 font-medium">名称</th>
                  <th className="py-1.5 font-medium">昨日连板</th>
                  <th className="py-1.5 font-medium">所属题材</th>
                </tr>
              </thead>
              <tbody>
                {broken.map((b) => (
                  <tr key={b.symbol} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                    <td className="py-1.5">
                      <span className="text-zinc-700 dark:text-zinc-200">{b.name ?? b.symbol}</span>
                      <span className="ml-1.5 font-mono text-[11px] text-zinc-400">{b.symbol}</span>
                    </td>
                    <td className="py-1.5 font-mono text-rose-600 dark:text-rose-400">{b.prev_boards} 板</td>
                    <td className="py-1.5 text-xs text-zinc-500 dark:text-zinc-400">{b.themes.join("、")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── 口径说明 ─────────────────────────────────────────── */}
      {data?.caveats && data.caveats.length > 0 && (
        <div className="mt-4 rounded-lg border border-zinc-200 px-4 py-2.5 text-xs text-zinc-400 dark:border-zinc-800">
          <button onClick={() => setShowCaveats((v) => !v)} className="flex items-center gap-1.5 hover:text-zinc-600 dark:hover:text-zinc-200">
            <span>{showCaveats ? "▾" : "▸"}</span>
            <span>口径与已知边界（{data.caveats.length} 条）</span>
          </button>
          {showCaveats && (
            <ul className="mt-2 space-y-1">
              {data.caveats.map((c) => (
                <li key={c} className="flex gap-1.5">
                  <span className="text-zinc-300 dark:text-zinc-600">·</span>
                  <span>{c}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <p className="mt-4 text-[11px] leading-4 text-zinc-400">
        本页为技术面结构分析，不构成投资建议。题材阶段与健康度为规则化推断，需结合盘中实际走势与个股基本面独立判断。
      </p>
    </main>
  );
}
