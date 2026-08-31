"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ThemeCardView } from "@/components/theme-card";
import { getThemes } from "@/lib/api";
import type { ThemeBoardPayload } from "@/types/market";

/**
 * 强势题材梯队看板。
 *
 * 与 /limit-up 的区别：涨停池是平铺列表，这里以题材为容器重组，
 * 回答三个问题——题材是否成建制、梯队是否健康、资金是否持续。
 *
 * 滚动约定（2026-08-29 修复）：全局 body 锁屏（h-screen overflow-hidden），
 * 每页自管滚动。本页此前根容器缺 h-full 且无滚动容器，内容超出视口后不可达。
 * 现在结构为：固定头部 + 单一大滚动容器（flex-1 min-h-0 overflow-y-auto）承载全部卡片。
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

/** 强弱分级图例：与后端 strength_tier 的判定规则一一对应 */
const TIER_LEGEND = "领涨 = 成建制·发酵/高潮·封板牢　|　强势 = 发酵/高潮或成建制高位分歧　|　活跃 = 有连板梯队　|　观察 = 暂无梯队结构";

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
  // 聚焦题材（L4 联动：详情页题材 chip → /themes?focus=名称）
  const [focus, setFocus] = useState(searchParams.get("focus") ?? "");

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
    // 首屏必须带上 URL 里的 date——此前裸 load() 只用默认日期，
    // /themes?date=2026-08-28 打开时实际取的是"今天"（盘前为降级数据）。
    void load(date || undefined);
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

  /** 聚焦过滤：题材名精确/包含 + 原始归因标签匹配（官方成分名与归因串口径可能不同） */
  const visibleThemes = useMemo(() => {
    if (!data) return [];
    if (!focus) return data.themes;
    return data.themes.filter(
      (c) => c.theme === focus || c.theme.includes(focus) || (c.raw_tags ?? []).includes(focus)
    );
  }, [data, focus]);

  function clearFocus() {
    setFocus("");
    // 从 URL 移除 focus（沿用本页 replaceState 口径）
    const params = new URLSearchParams(window.location.search);
    params.delete("focus");
    const qs = params.toString();
    window.history.replaceState({}, "", qs ? `?${qs}` : window.location.pathname);
  }

  return (
    <main className="mx-auto flex h-full w-full max-w-[1600px] flex-col px-4 py-3">
      {/* ── 固定头部 ──────────────────────────────────────────── */}
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            强势题材梯队看板
          </h1>
          <p className="mt-0.5 text-xs text-zinc-400">
            {data ? `${data.trade_date}` : "…"}
            {data?.prev_trade_date && ` · 对照 ${data.prev_trade_date}`}
            {data &&
              ` · 涨停 ${data.summary.limit_up_total} 只 / 识别题材 ${data.summary.theme_count} 个（当前展示 ≥${minCount} 家的 ${data.themes.length} 张卡片）/ 最高 ${data.summary.market_max_boards} 板`}
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

      <p className="mb-3 text-[11px] text-zinc-400" title="分级规则由后端 strength_tier 规则化判定，鼠标悬停卡片分级徽标可看判定依据">
        分级：{TIER_LEGEND}
      </p>

      {error && (
        <div className="mb-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-300">
          题材看板加载失败：{error}
        </div>
      )}

      {/* 聚焦条：详情页题材 chip 跳转进来时的上下文提示 */}
      {focus && (
        <div className="mb-3 flex shrink-0 items-center gap-2 rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-1.5 text-xs text-sky-700 dark:text-sky-300">
          <span>
            聚焦题材：<span className="font-medium">{focus}</span>
          </span>
          <button onClick={clearFocus} className="rounded border border-sky-500/40 px-1.5 py-0.5 hover:bg-sky-500/10">
            显示全部
          </button>
        </div>
      )}

      {loading && !data && <p className="py-16 text-center text-sm text-zinc-400">加载中…</p>}

      {data && data.themes.length === 0 && (
        <p className="py-16 text-center text-sm text-zinc-400">
          当前筛选条件下没有题材（连板 ≥{minBoards} 板 / 涨停 ≥{minCount} 家）
        </p>
      )}

      {data && data.themes.length > 0 && visibleThemes.length === 0 && (
        <p className="py-16 text-center text-sm text-zinc-400">
          题材「{focus}」今日没有梯队卡片——可能今日无涨停、未成建制，或归属名称与看板口径不一致（可在涨停池核对该股涨停原因原文）
        </p>
      )}

      {/* ── 唯一滚动容器：全部卡片 + 断板股 + 口径说明 ─────────── */}
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        <div className={`space-y-3 ${loading && data ? "opacity-60 transition-opacity" : ""}`}>
          {visibleThemes.map((c, i) => (
            <ThemeCardView key={c.theme} card={c} rank={i + 1} tradeDate={data?.trade_date ?? ""} />
          ))}
        </div>

        {/* ── 断板股 ─────────────────────────────────────────── */}
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

        {/* ── 口径说明 ───────────────────────────────────────── */}
        {data?.caveats && data.caveats.length > 0 && (
          <div className="mb-4 mt-4 rounded-lg border border-zinc-200 px-4 py-2.5 text-xs text-zinc-400 dark:border-zinc-800">
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

        <p className="pb-2 text-[11px] leading-4 text-zinc-400">
          本页为技术面结构分析，不构成投资建议。题材阶段与健康度为规则化推断，需结合盘中实际走势与个股基本面独立判断。
          梯队归属按当日涨停联动唯一判定（连板密度优先），一只票只出现在一张卡片。
        </p>
      </div>
    </main>
  );
}
