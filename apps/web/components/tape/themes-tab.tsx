"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { RankDelta, ThemeCardView } from "@/components/theme-card";
import { getThemes, getThemesHot, getThemeStrength, getAuctionBenchmark, getSkyrocket } from "@/lib/api";
import { fmtHeat, pctColor, pctText, timeText } from "@/lib/format";
import { workbenchUrlWithBack } from "@/lib/routing";
import { sortAuctionBenchmark } from "@/lib/auction";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import type { AuctionBenchmarkItem, SkyrocketRow, ThemeStrengthRow, ThemesHotPayload } from "@/lib/api";
import type { ThemeBoardPayload } from "@/types/market";

/**
 * 盘面页 · 题材梯队 tab（原 /themes 页迁移，2026-09-01 系统重构）。
 *
 * 与涨停生态 tab 的区别：涨停池是平铺列表，这里以题材为容器重组，
 * 回答三个问题——题材是否成建制、梯队是否健康、资金是否持续。
 *
 * 滚动约定（2026-08-29 修复）：全局 body 锁屏（h-screen overflow-hidden），
 * 盘面页自管滚动。本 tab 结构为：固定头部 + 单一大滚动容器承载全部卡片。
 *
 * URL 同步注意：盘面页的 tab 参数在同一个 URL 上，updateUrl 必须在
 * window.location.search 基础上增删（不能新建空 URLSearchParams），
 * 否则切 tab/筛选会把 ?tab=themes 冲掉。
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

export function ThemesTab() {
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
  // 题材人气（B1 热股榜）：best-effort 增强，拉取失败静默降级（看板主体不依赖它）
  const [hot, setHot] = useState<ThemesHotPayload | null>(null);
  // 飙升榜（B1）：「正在变热」信号，与人气榜口径不同；best-effort 同上
  const [sky, setSky] = useState<SkyrocketRow[] | null>(null);
  const [strength, setStrength] = useState<Map<string, ThemeStrengthRow> | null>(null);
  // 聚焦题材（L4 联动：详情页题材 chip → /tape?tab=themes&focus=名称）
  const [focus, setFocus] = useState(searchParams.get("focus") ?? "");
  // 竞价标杆（ths 短线风向标）：best-effort，非交易日/无数据后端返回 502 → 静默不显示。
  // 把 date 一并存进 state：切日期时新数据到达前，靠 date 比对拒绝渲染上一日的榜单
  // （不同步 setBenchmark(null) 清空，避免 effect 内同步 setState 触发级联渲染）。
  const [benchmark, setBenchmark] = useState<{
    date: string;
    rows: AuctionBenchmarkItem[];
  } | null>(null);

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

  // 首屏必须带上 URL 里的 date——此前裸 load() 只用默认日期，
  // ?date=2026-08-28 打开时实际取的是"今天"（盘前为降级数据）。
  // 仅挂载时拉一次（latest-ref 拿到当前 date）；后续筛选由各自的 onChange 触发
  usePollingFetch(() => load(date || undefined), null);

  useEffect(() => {
    // 人气榜独立拉取（实时口径，不看 date 参数——历史日期没有人气数据）
    getThemesHot()
      .then(setHot)
      .catch(() => setHot(null));
    // 飙升榜（B1）：同实时口径独立拉取；空榜/失败静默不显示
    getSkyrocket("day")
      .then((rows) => setSky(rows.length ? rows : null))
      .catch(() => setSky(null));
    // 资金合力（P1-5）：官方成分批量快照聚合，按题材名匹配卡片；60s 后端缓存
    getThemeStrength()
      .then((s) => {
        const byName = new Map<string, ThemeStrengthRow>();
        for (const row of Object.values(s)) byName.set(row.name, row);
        setStrength(byName);
      })
      .catch(() => setStrength(null));
  }, []);

  // 竞价标杆随 date 联动——与人气榜不同，竞价基准**有**历史数据：
  // 2026-09-01 实测 date 参数真实有效（08-31 / 08-28 / 07-15 内容各不相同，非静默回退），
  // 故这里跟随日期筛选；非交易日后端返回 502，catch 后静默不显示。
  useEffect(() => {
    let alive = true;
    getAuctionBenchmark(date || undefined)
      .then((rows) => {
        if (alive) setBenchmark(rows.length > 0 ? { date, rows } : null);
      })
      .catch(() => {
        if (alive) setBenchmark(null);
      });
    return () => {
      alive = false;
    };
  }, [date]);

  function updateUrl(
    d?: string,
    s: SortKey = sort,
    mb = minBoards,
    mc = minCount,
  ) {
    // 在现有 URL 上增删参数（保留 tab= 等盘面页参数）
    const params = new URLSearchParams(window.location.search);
    const setOrDel = (k: string, v?: string) => {
      if (v) params.set(k, v);
      else params.delete(k);
    };
    setOrDel("date", d);
    setOrDel("sort", s !== "strength" ? s : undefined);
    setOrDel("min_boards", mb ? String(mb) : undefined);
    setOrDel("min_count", mc !== 2 ? String(mc) : undefined);
    const qs = params.toString();
    window.history.replaceState({}, "", qs ? `?${qs}` : window.location.pathname);
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

  /** 题材名 → 人气聚合（官方成分口径，与卡片题材名精确匹配；对不上就不显示徽标） */
  const hotByTheme = useMemo(() => new Map((hot?.themes ?? []).map((t) => [t.theme, t])), [hot]);

  /**
   * 竞价标杆按竞价涨幅降序（缺失沉底、不原地变异，规则见 lib/auction.ts）。
   * date 不匹配时返回空——切日期后新数据到达前，不展示上一日的榜单。
   */
  const benchmarkSorted = useMemo(
    () => sortAuctionBenchmark(benchmark?.date === date ? benchmark.rows : null),
    [benchmark, date]
  );

  /** 聚焦过滤：题材名精确/包含 + 原始归因标签 + 官方概念挂靠名（09-08：搜「代糖」
   *  命中「功能糖」簇——簇名与同花顺概念板块口径不同，靠 official_matches 桥接） */
  const visibleThemes = useMemo(() => {
    if (!data) return [];
    if (!focus) return data.themes;
    return data.themes.filter(
      (c) =>
        c.theme === focus ||
        c.theme.includes(focus) ||
        (c.raw_tags ?? []).includes(focus) ||
        (c.official_matches ?? []).some((m) => m.name === focus || m.name.includes(focus))
    );
  }, [data, focus]);

  function clearFocus() {
    setFocus("");
    // 从 URL 移除 focus（保留 tab 等其余参数）
    const params = new URLSearchParams(window.location.search);
    params.delete("focus");
    const qs = params.toString();
    window.history.replaceState({}, "", qs ? `?${qs}` : window.location.pathname);
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* ── 固定头部：摘要 + 筛选 ────────────────────────────── */}
      <div className="mb-3 flex shrink-0 flex-wrap items-end justify-between gap-3">
        <p className="text-xs text-zinc-600 dark:text-zinc-400">
          {data ? `${data.trade_date}` : "…"}
          {data?.prev_trade_date && ` · 对照 ${data.prev_trade_date}`}
          {data &&
            ` · 涨停 ${data.summary.limit_up_total} 只 / 识别题材 ${data.summary.theme_count} 个（当前展示 ≥${minCount} 家的 ${data.themes.length} 张卡片）/ 最高 ${data.summary.market_max_boards} 板`}
          {data?.summary.market_break_rate != null &&
            ` / 全市场炸板率 ${(data.summary.market_break_rate * 100).toFixed(1)}%`}
        </p>

        <div className="flex flex-wrap items-center gap-3 text-xs">
          <label className="flex items-center gap-1.5">
            <span className="text-zinc-600 dark:text-zinc-400">日期</span>
            <input
              type="date"
              value={date}
              onChange={(e) => onDate(e.target.value)}
              className="rounded-md border border-zinc-200 bg-transparent px-2 py-1 text-zinc-700 dark:border-zinc-700 dark:text-zinc-200"
            />
          </label>

          <div className="flex items-center gap-1">
            <span className="text-zinc-600 dark:text-zinc-400">排序</span>
            {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
              <button
                key={k}
                onClick={() => onSort(k)}
                className={`rounded-md px-2 py-1 transition-colors ${
                  sort === k
                    ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "border border-zinc-200 text-zinc-600 hover:text-zinc-900 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-100"
                }`}
              >
                {SORT_LABELS[k]}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1">
            <span className="text-zinc-600 dark:text-zinc-400">连板</span>
            {BOARD_FILTERS.map((v) => (
              <button
                key={v}
                onClick={() => onBoards(v)}
                className={`rounded-md px-2 py-1 transition-colors ${
                  minBoards === v
                    ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "border border-zinc-200 text-zinc-600 hover:text-zinc-900 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-100"
                }`}
              >
                {v === 0 ? "不限" : `${v}板+`}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1">
            <span className="text-zinc-600 dark:text-zinc-400">家数</span>
            {COUNT_FILTERS.map((f) => (
              <button
                key={f.v}
                onClick={() => onCount(f.v)}
                className={`rounded-md px-2 py-1 transition-colors ${
                  minCount === f.v
                    ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "border border-zinc-200 text-zinc-600 hover:text-zinc-900 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-100"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <p className="mb-3 shrink-0 text-[11px] text-zinc-600 dark:text-zinc-400" title="分级规则由后端 strength_tier 规则化判定，鼠标悬停卡片分级徽标可看判定依据">
        分级：{TIER_LEGEND}
      </p>

      {/* ── 竞价标杆条：ths 短线风向标竞价基准（B2 数据面 → UI 消费）──
          按竞价涨幅降序；非交易日/无数据后端 502，此时不渲染本条 ── */}
      {benchmarkSorted.length > 0 && (
        <div
          className="mb-3 flex shrink-0 items-center gap-x-3 gap-y-1 overflow-x-auto rounded-lg border border-zinc-200 px-3 py-1.5 dark:border-zinc-800"
          title="同花顺短线风向标竞价基准：该交易日 09:25 集合竞价终态的标杆个股；题材为官方 tags"
        >
          <span className="shrink-0 text-[11px] text-zinc-600 dark:text-zinc-400">竞价标杆</span>
          {benchmarkSorted.map((b) => (
            <Link
              key={b.symbol}
              href={workbenchUrlWithBack(b.symbol)}
              className="flex shrink-0 items-center gap-1 text-xs hover:opacity-70"
              title={b.tags.length ? `官方题材：${b.tags.join("、")}` : "无官方题材归属"}
            >
              <span className="text-zinc-700 dark:text-zinc-200">{b.name ?? b.symbol}</span>
              <span className={`font-mono tabular-nums ${pctColor(b.auction_pct)}`}>
                {pctText(b.auction_pct)}
              </span>
            </Link>
          ))}
          <div className="flex-1" />
          <span className="shrink-0 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">
            同花顺 · 竞价 {date || "当日"}
          </span>
        </div>
      )}

      {/* ── 人气榜条（B1）：ths 热股 24 小时榜 Top10，点击跳详情；失败静默不显示 ── */}
      {hot && hot.stocks.length > 0 && (
        <div
          className="mb-3 flex shrink-0 items-center gap-x-3 gap-y-1 overflow-x-auto rounded-lg border border-zinc-200 px-3 py-1.5 dark:border-zinc-800"
          title="同花顺热股榜（24 小时口径，人气为估算数据）；题材归属为官方成分反查"
        >
          <span className="shrink-0 text-[11px] text-zinc-600 dark:text-zinc-400">人气榜</span>
          {hot.stocks.slice(0, 10).map((s) => (
            <Link
              key={s.symbol}
              href={workbenchUrlWithBack(s.symbol)}
              className="flex shrink-0 items-center gap-1 text-xs hover:text-rose-600 dark:hover:text-rose-400"
              title={s.themes.length ? `官方题材：${s.themes.join("、")}` : "无官方题材归属"}
            >
              <span className="font-mono text-zinc-600 dark:text-zinc-400">#{s.rank}</span>
              <span className="text-zinc-700 dark:text-zinc-200">{s.name ?? s.symbol}</span>
              <span className="font-mono tabular-nums text-zinc-600 dark:text-zinc-400">{fmtHeat(s.heat)}</span>
              <RankDelta v={s.rank_change} />
            </Link>
          ))}
          <div className="flex-1" />
          <span className="shrink-0 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">
            同花顺 {timeText(hot.ts)}
          </span>
        </div>
      )}

      {/* ── 飙升榜条（B1）：排名变化驱动的「正在变热」，先于人气榜的更早信号 ── */}
      {sky && sky.length > 0 && (
        <div
          className="mb-3 flex shrink-0 items-center gap-x-3 gap-y-1 overflow-x-auto rounded-lg border border-zinc-200 px-3 py-1.5 dark:border-zinc-800"
          title="同花顺飙升榜（排名变化驱动，与人气榜排名逻辑不同；人气为估算数据、榜单有延迟）"
        >
          <span className="shrink-0 text-[11px] text-zinc-600 dark:text-zinc-400">飙升榜</span>
          {sky.slice(0, 10).map((s) => (
            <Link
              key={s.symbol}
              href={workbenchUrlWithBack(s.symbol)}
              className="flex shrink-0 items-center gap-1 text-xs hover:text-rose-600 dark:hover:text-rose-400"
            >
              <span className="font-mono text-zinc-600 dark:text-zinc-400">#{s.rank}</span>
              <span className="text-zinc-700 dark:text-zinc-200">{s.name ?? s.symbol}</span>
              <RankDelta v={s.rank_change} />
            </Link>
          ))}
          <div className="flex-1" />
          <span className="shrink-0 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">
            同花顺
          </span>
        </div>
      )}

      {error && (
        <div className="mb-3 shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-800 dark:text-amber-300">
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

      {loading && !data && (
        /* 首次加载：题材卡同构骨架占位（2026-09-04 统一加载体验） */
        <div className="space-y-3 py-2" aria-hidden>
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="animate-pulse rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
              <div className="flex items-center gap-2">
                <div className="h-4 w-24 rounded bg-zinc-200/80 dark:bg-zinc-800/70" />
                <div className="h-3.5 w-12 rounded bg-zinc-200/80 dark:bg-zinc-800/70" />
                <div className="h-3.5 w-16 rounded bg-zinc-200/80 dark:bg-zinc-800/70" />
              </div>
              <div className="mt-2 h-3 w-2/3 rounded bg-zinc-200/60 dark:bg-zinc-800/50" />
            </div>
          ))}
        </div>
      )}

      {data && data.themes.length === 0 && (
        <p className="py-16 text-center text-sm text-zinc-600 dark:text-zinc-400">
          当前筛选条件下没有题材（连板 ≥{minBoards} 板 / 涨停 ≥{minCount} 家）
        </p>
      )}

      {data && data.themes.length > 0 && visibleThemes.length === 0 && (
        <p className="py-16 text-center text-sm text-zinc-600 dark:text-zinc-400">
          题材「{focus}」今日没有梯队卡片——可能今日无涨停、未成建制，或归属名称与看板口径不一致（可在涨停生态 tab 核对该股涨停原因原文）
        </p>
      )}

      {/* ── 唯一滚动容器：全部卡片 + 断板股 + 口径说明 ─────────── */}
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        <div className={`space-y-3 ${loading && data ? "opacity-60 transition-opacity" : ""}`}>
          {visibleThemes.map((c, i) => (
            <ThemeCardView
              key={c.theme}
              card={c}
              rank={i + 1}
              tradeDate={data?.trade_date ?? ""}
              hot={hotByTheme.get(c.theme)}
              strength={strength?.get(c.theme) ?? null}
            />
          ))}
        </div>

        {/* ── 断板股 ─────────────────────────────────────────── */}
        {broken.length > 0 && (
          <section className="mt-4 overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800">
            <header className="border-b border-zinc-200 bg-zinc-50 px-4 py-2.5 dark:border-zinc-800 dark:bg-zinc-900/40">
              <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-200">
                断板股
                <span className="ml-2 text-xs font-normal text-zinc-600 dark:text-zinc-400">
                  昨日连板、今日未封板 —— 梯队断层与情绪退潮的先行信号（{broken.length} 只）
                </span>
              </h2>
            </header>
            <div className="overflow-x-auto px-4 py-3">
              <table className="w-full min-w-[560px] text-sm">
                <thead>
                  <tr className="border-b border-zinc-200 text-left text-[11px] text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
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
                        <span className="ml-1.5 font-mono text-[11px] text-zinc-600 dark:text-zinc-400">{b.symbol}</span>
                      </td>
                      <td className="py-1.5 font-mono text-rose-700 dark:text-rose-400">{b.prev_boards} 板</td>
                      <td className="py-1.5 text-xs text-zinc-600 dark:text-zinc-400">{b.themes.join("、")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* ── 口径说明 ───────────────────────────────────────── */}
        {data?.caveats && data.caveats.length > 0 && (
          <div className="mb-4 mt-4 rounded-lg border border-zinc-200 px-4 py-2.5 text-xs text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
            <button onClick={() => setShowCaveats((v) => !v)} className="flex items-center gap-1.5 hover:text-zinc-600 dark:hover:text-zinc-200">
              <span>{showCaveats ? "▾" : "▸"}</span>
              <span>口径与已知边界（{data.caveats.length} 条）</span>
            </button>
            {showCaveats && (
              <ul className="mt-2 space-y-1">
                {data.caveats.map((c) => (
                  <li key={c} className="flex gap-1.5">
                    <span className="text-zinc-600 dark:text-zinc-400">·</span>
                    <span>{c}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <p className="pb-2 text-[11px] leading-4 text-zinc-600 dark:text-zinc-400">
          本页为技术面结构分析，不构成投资建议。题材阶段与健康度为规则化推断，需结合盘中实际走势与个股基本面独立判断。
          梯队归属按当日涨停联动唯一判定（连板密度优先），一只票只出现在一张卡片。
        </p>
      </div>
    </div>
  );
}
