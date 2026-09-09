"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import {
  generateMorningBrief,
  generatePickReview,
  generatePicks,
  getIntradayOpportunities,
  getIntradayReview,
  getIntradayTop,
  getMorningBriefToday,
  getPicksHistory,
  getPicksMeta,
  getPickReviews,
  getSignalHealth,
  getTodayPicks,
  getWatcherState,
  runIntradayReview,
  runWatcherBeat,
  type DailyPicksPayload,
  type IntradayOpportunities,
  type IntradayReviewStats,
  type IntradayTopPayload,
  type MorningBrief,
  type PickReviewRow,
  type SignalHealthPayload,
  type StyleRouting,
  type WatcherState,
} from "@/lib/api";
import { PickCard, StandAsideBanner } from "@/components/picks/pick-card";
import { WatchCard } from "@/components/picks/watch-card";
import {
  AlertItem,
  DirectionCard,
  EnvStrip,
  MarketOverviewStrip,
  OpportunitySection,
  ReviewOutcomeTable,
  StatsPanel,
  WatcherPanel,
} from "@/components/hunting/intraday-sections";
import {
  DailyReviews,
  HistoryList,
  ReasonDistribution,
  RolePerformanceTable,
  type RolePerformance,
} from "@/components/hunting/pick-sections";
import { HuntingStatsBar } from "@/components/hunting/stats-bar";
import {
  CardListSkeleton,
  FadeIn,
  PageSkeletonFallback,
  StatGridSkeleton,
  StatsSkeleton,
  TableSkeleton,
} from "@/components/ui/loading";
import { timeText } from "@/lib/format";
import { MasonryColumns } from "@/components/masonry-columns";

/**
 * 猎场（/hunting，2026-09-08 板块融合 docs/system-audit-20260908.md §三）：
 * 合并原 /picks（每日精选）+ /intraday（盘中跟踪），回答「今天/现在值得盯哪些票」。
 *
 * 结构：统计条（双口径独立不混算）→ tag 瀑布流（精选 PickCard / 跟踪 WatchCard）→
 * 异动手风琴（题材→个股瀑布流）→ 盘中节拍（简报/watcher/提醒，折叠可展开）→
 * 对照与复盘（对照表 / 近 30 日统计 / 精选逐日复盘，折叠可展开）。
 *
 * 语义差异保持（审查 §3.3-6）：精选是 date+symbol 持久组合，跟踪是当日实时动态名单
 * ——统计条分别标注口径。深链：?tag= / ?theme= / ?sec= / ?review=1 全部保留
 * （旧 /picks /intraday 路径经 next.config 302 兜底）。全页不构成买卖建议。
 */

type TagKey = "all" | "pick" | "watch";

const TAG_TABS: { key: TagKey; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "pick", label: "每日精选" },
  { key: "watch", label: "盘中跟踪" },
];

function HuntingInner() {
  const router = useRouter();
  const sp = useSearchParams();

  // —— 精选组数据 ——
  const [data, setData] = useState<DailyPicksPayload | null>(null);
  const [history, setHistory] = useState<{ date: string; symbols: (string | null)[]; score_avg: number }[]>([]);
  const [reviews, setReviews] = useState<PickReviewRow[]>([]);
  const [meta, setMeta] = useState<{
    reason_distribution: Record<string, number>;
    role_performance?: RolePerformance[];
  } | null>(null);
  const [health, setHealth] = useState<SignalHealthPayload | null>(null);
  const [picksLoaded, setPicksLoaded] = useState(false);
  const [picksFailed, setPicksFailed] = useState(false);

  // —— 跟踪组数据 ——
  const [brief, setBrief] = useState<MorningBrief | null>(null);
  const [watcher, setWatcher] = useState<WatcherState | null>(null);
  const [stats, setStats] = useState<IntradayReviewStats | null>(null);
  const [opps, setOpps] = useState<IntradayOpportunities | null>(null);
  const [top, setTop] = useState<IntradayTopPayload | null>(null);
  const [intradayLoaded, setIntradayLoaded] = useState(false);
  const [intradayFailed, setIntradayFailed] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // —— URL 状态（深链为真相源）——
  const [tag, setTag] = useState<TagKey>(() => {
    const t = sp.get("tag");
    return t === "pick" || t === "watch" ? t : "all";
  });
  const [expandedTheme, setExpandedTheme] = useState<string | null>(() => sp.get("theme"));
  const sec = sp.get("sec");
  const secValid = /^(overview|opportunity|brief|watcher|reminders|review)$/.test(sec ?? "");
  // 折叠区开合：?sec= 深链自动展开对应组；?review=1 / 生成复盘成功展开复盘组
  const [beatsOpen, setBeatsOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(() => sp.get("review") === "1");

  // ?sec= 滚动定位（挂载与变化时；目标在折叠区内则先展开）
  useEffect(() => {
    if (!secValid) return;
    if (sec === "brief" || sec === "watcher" || sec === "reminders") setBeatsOpen(true);
    if (sec === "review") setReviewOpen(true);
    const scroll = () => {
      document.getElementById(`sec-${sec}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    scroll();
    const t = window.setTimeout(scroll, 400);
    return () => window.clearTimeout(t);
  }, [sec, secValid]);

  const switchTag = useCallback(
    (t: TagKey) => {
      setTag(t);
      const params = new URLSearchParams(sp.toString());
      if (t === "all") params.delete("tag");
      else params.set("tag", t);
      const qs = params.toString();
      router.replace(`/hunting${qs ? `?${qs}` : ""}`, { scroll: false });
    },
    [sp, router],
  );

  const toggleTheme = useCallback(
    (t: string) => {
      const next = expandedTheme === t ? null : t;
      setExpandedTheme(next);
      const params = new URLSearchParams(sp.toString());
      if (next) params.set("theme", encodeURIComponent(next));
      else params.delete("theme");
      const qs = params.toString();
      router.replace(`/hunting${qs ? `?${qs}` : ""}`, { scroll: false });
    },
    [expandedTheme, sp, router],
  );

  const load = useCallback(async () => {
    // 精选组（任一失败不拖垮其他；全组失败置 picksFailed 可见警示）
    {
      const [d, h, r, m, sh] = await Promise.all([
        getTodayPicks().catch(() => null),
        getPicksHistory(10).catch(() => []),
        getPickReviews().catch(() => [] as PickReviewRow[]),
        getPicksMeta().catch(() => null),
        getSignalHealth().catch(() => null),
      ]);
      setData(d);
      setHistory(h);
      setReviews(r);
      setMeta(m);
      setHealth(sh);
      setPicksFailed(d === null && m === null && sh === null);
      setPicksLoaded(true); // 成败都算"拉过"：失败有警示，不能永远停在骨架
    }
    // 跟踪组（原 /intraday 四端点 + intraday-top）
    {
      const [b, w, s, o, tp] = await Promise.all([
        getMorningBriefToday().catch(() => null),
        getWatcherState().catch(() => null),
        getIntradayReview().catch(() => null),
        getIntradayOpportunities().catch(() => null),
        getIntradayTop().catch(() => null),
      ]);
      setBrief(b);
      setWatcher(w);
      setStats(s);
      setOpps(o);
      setTop(tp);
      setIntradayFailed(b === null && w === null && s === null && o === null && tp === null);
      setIntradayLoaded(true);
    }
  }, []);

  // 挂载即拉 + 60s 轮询（对齐后端 watcher 节拍；页面不可见暂停、回可见补拉）
  usePollingFetch(load, null);
  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === "visible") void load();
    };
    const timer = setInterval(tick, 60_000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  async function act(kind: "picks" | "pickReview" | "brief" | "beat" | "review") {
    setBusy(kind);
    setError(null);
    setHint(null);
    try {
      if (kind === "picks") {
        const d = await generatePicks();
        setData(d);
        setHint("组合已生成/刷新");
      } else if (kind === "pickReview") {
        const res = await generatePickReview();
        setReviewOpen(true); // 生成成功后展开复盘组，否则用户看不到任何变化
        setHint(`已生成 ${res.reviews.length} 条复盘（${res.date}）`);
      } else if (kind === "brief") {
        const b = await generateMorningBrief();
        setBeatsOpen(true);
        setHint(`简报已生成：${b.directions.length} 个方向（覆盖当日文件，盘中提醒已清空）`);
      } else if (kind === "beat") {
        const r = await runWatcherBeat();
        const n = r.alerts.length;
        setBeatsOpen(true);
        setHint(n > 0 ? `单拍完成：${n} 条提醒（去重后实际分发见日志）` : "单拍完成：本拍无新增提醒");
      } else {
        const r = await runIntradayReview();
        setReviewOpen(true);
        setHint(`对照完成：${r.directions.map((x) => `${x.direction} ${x.outcome}`).join(" · ")}`);
      }
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const items = data?.items ?? [];
  const alerts = brief?.alerts ?? [];
  const reviewed = (brief?.directions ?? []).filter((d) => d.review);
  const topItems = top?.items ?? [];
  const briefMissing = picksLoaded && intradayLoaded && brief === null;
  const pending = !picksLoaded || !intradayLoaded;

  return (
    <main className="mx-auto flex h-full w-full max-w-[1400px] flex-col gap-3 overflow-hidden px-4 py-3">
      {/* 头部：标题 + 口径说明 + 刷新状态 + 操作 */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs text-zinc-400">
        <h1 className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">猎场</h1>
        <span title="精选=盘后布下的猎物（date+symbol 持久组合）；跟踪=盘中正在追的猎物（当日实时动态名单）；题材异动=猎群">
          精选 · 跟踪 · 猎群
        </span>
        {picksFailed || intradayFailed ? (
          <span
            className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-600 dark:text-amber-300"
            title="部分数据端点失败（后端不可达或网络中断）。每 60s 自动重试，也可点「刷新数据」。"
          >
            ⚠ 数据加载失败 · 自动重试中
          </span>
        ) : (
          <span className="text-[10px]" title="页面每 60s 自动拉取最新数据；切走再切回会立即刷新。">
            每 60s 自动刷新
          </span>
        )}
        <div className="ml-auto flex flex-wrap items-center justify-end gap-1.5">
          <button
            onClick={() => void load()}
            disabled={busy !== null}
            className="rounded border border-zinc-300 px-2 py-0.5 text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100 disabled:opacity-50"
            title="立即重新拉取全部数据（通常无需手动点——页面已自动刷新）"
          >
            刷新数据
          </button>
          <button
            onClick={() => void act("picks")}
            disabled={busy !== null}
            className="rounded border border-sky-500/50 px-2 py-0.5 text-sky-400 hover:bg-sky-500/10 disabled:opacity-50"
            title="重跑五维评分管线（收盘后执行；覆盖当日组合）"
          >
            {busy === "picks" ? "计算中…" : "生成/刷新组合"}
          </button>
          <button
            onClick={() => void act("pickReview")}
            disabled={busy !== null}
            className="rounded border border-zinc-300 px-2 py-0.5 text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100 disabled:opacity-50"
            title="对最近组合逐只回顾：实际走势 vs 入选理由，走坏原因归类（生成后展开复盘区）"
          >
            {busy === "pickReview" ? "生成中…" : "生成复盘"}
          </button>
          <button
            onClick={() => void act("brief")}
            disabled={busy !== null}
            className="rounded border border-sky-500/50 px-2 py-0.5 text-sky-400 hover:bg-sky-500/10 disabled:opacity-50"
            title="重新采集证据生成/刷新今日简报（覆盖当日文件）"
          >
            {busy === "brief" ? "生成中…" : "生成/刷新简报"}
          </button>
          <button
            onClick={() => void act("beat")}
            disabled={busy !== null || briefMissing}
            className="rounded border border-zinc-300 px-2 py-0.5 text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100 disabled:opacity-50"
            title="手动推进一拍：取数 → 全部方向 confirm/falsify 判定（与盘中 watcher 同代码路径）"
          >
            {busy === "beat" ? "取拍中…" : "手动单拍"}
          </button>
          <button
            onClick={() => void act("review")}
            disabled={busy !== null || briefMissing}
            className="rounded border border-zinc-300 px-2 py-0.5 text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100 disabled:opacity-50"
            title="对照当日盘前方向 vs 实际盘面（四分类+误判分类）并回填提醒收益"
          >
            {busy === "review" ? "对照中…" : "运行对照"}
          </button>
        </div>
      </div>

      {hint && (
        <div className="shrink-0 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-600 dark:text-emerald-300">
          ✓ {hint}
        </div>
      )}
      {error && (
        <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-600 dark:text-amber-300">
          {error}
        </div>
      )}
      {data?.stale && (
        <div className="shrink-0 rounded-lg border border-zinc-300 px-3 py-1.5 text-xs text-zinc-500 dark:border-zinc-700">
          当前展示 {data.date} 生成的组合（今日尚未生成）
        </div>
      )}
      {data?.note && <div className="shrink-0 text-xs text-zinc-400">{data.note}</div>}

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
        {/* ── 空仓闸门横幅（风险提示置顶）── */}
        {data?.meta?.gate && data.meta.gate.stand_aside && <StandAsideBanner gate={data.meta.gate} />}

        {/* ── 相位→风格路由（审查 §4.1）：当日风格 + 权重偏移，路由未生效时显式说明 ── */}
        {data?.meta?.style_routing && (
          <StyleRoutingChip sr={data.meta.style_routing} />
        )}

        {/* ── 统计条：精选 / 跟踪双口径（三态纪律，insufficient 显式不判 ok）── */}
        <section id="sec-overview" className="space-y-2 scroll-mt-2">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">今日盘面</h2>
            {opps?.hot_available === false && (
              <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-600 dark:text-amber-300">
                ⚠ 人气榜不可用：辨识度判定不完整
              </span>
            )}
          </div>
          {!pending ? (
            <FadeIn>
              <HuntingStatsBar health={health} rolePerformance={meta?.role_performance} stats={stats} />
            </FadeIn>
          ) : (
            <StatGridSkeleton count={4} />
          )}
          {opps ? (
            <FadeIn>
              <MarketOverviewStrip opps={opps} />
            </FadeIn>
          ) : (
            pending && <StatGridSkeleton count={4} />
          )}
        </section>

        {/* ── tag 切换 + 瀑布流（精选 PickCard / 跟踪 WatchCard 同构混排）── */}
        <section className="space-y-2">
          <div className="flex items-center gap-1.5">
            {TAG_TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => switchTag(t.key)}
                className={`rounded-full px-3 py-1 text-xs transition-colors ${
                  tag === t.key
                    ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "border border-zinc-300 text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-100"
                }`}
              >
                {t.label}
              </button>
            ))}
            <span className="ml-2 text-[10px] text-zinc-400">
              {tag === "watch" ? "当日实时动态名单，随盘面重算" : tag === "pick" ? "收盘定次日 · 换股门槛 15 分" : "精选与跟踪合并视图"}
            </span>
          </div>

          {/* 精选瀑布流 */}
          {(tag === "all" || tag === "pick") && (
            <div>
              {!picksLoaded ? (
                <CardListSkeleton count={3} />
              ) : picksFailed ? (
                <div className="rounded-lg border border-zinc-200 px-3 py-2.5 text-xs text-zinc-400 dark:border-zinc-800">
                  精选数据不可用（后端不可达或端点失败）——每 60s 自动重试。
                </div>
              ) : items.length === 0 ? (
                <p className="py-8 text-center text-sm text-zinc-400">
                  尚无精选组合。点右上「生成/刷新组合」跑一次五维评分管线
                  <br />
                  （候选池 = 活跃事件标的池 ∪ 当日涨停池 ∪ 热股榜，≤5 只输出，全程可解释不构成买卖建议）。
                </p>
              ) : (
                <FadeIn>
                  <MasonryColumns>
                    {items.map((it) => (
                      <PickCard key={it.symbol} item={it} />
                    ))}
                  </MasonryColumns>
                </FadeIn>
              )}
            </div>
          )}

          {/* 跟踪瀑布流（intraday-top 最推荐标的，与工作台「盘中跟踪」分组同源） */}
          {(tag === "all" || tag === "watch") && (
            <div>
              {!intradayLoaded ? (
                <CardListSkeleton count={3} />
              ) : intradayFailed ? (
                <div className="rounded-lg border border-zinc-200 px-3 py-2.5 text-xs text-zinc-400 dark:border-zinc-800">
                  跟踪数据不可用（后端不可达或端点失败）——每 60s 自动重试。
                </div>
              ) : topItems.length === 0 ? (
                <p className="py-8 text-center text-sm text-zinc-400">
                  暂无跟踪标的（当日无涨停数据或候选未成形）——盘中动态重算，稍后自动更新。
                </p>
              ) : (
                <FadeIn>
                  <MasonryColumns>
                    {topItems.map((it) => (
                      <WatchCard key={it.symbol} item={it} flow />
                    ))}
                  </MasonryColumns>
                  <p className="mt-1 text-[10px] text-zinc-400">{top?.criteria}</p>
                </FadeIn>
              )}
            </div>
          )}
        </section>

        {/* ── 异动手风琴（题材 → 个股瀑布流）── */}
        <div id="sec-opportunity" className="scroll-mt-2">
          {opps ? (
            <FadeIn>
              <OpportunitySection opps={opps} expanded={expandedTheme} onToggle={toggleTheme} />
            </FadeIn>
          ) : (
            pending && <CardListSkeleton count={3} />
          )}
        </div>

        {/* ── 盘中节拍（简报 / watcher / 提醒）：默认收起，深链或操作后展开 ── */}
        <details
          open={beatsOpen}
          onToggle={(e) => setBeatsOpen((e.target as HTMLDetailsElement).open)}
          className="rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800"
        >
          <summary className="cursor-pointer select-none text-xs font-medium text-zinc-500 dark:text-zinc-400">
            盘中节拍（盘前简报 · watcher · 提醒{alerts.length > 0 ? ` · 今日 ${alerts.length} 条` : ""}）
          </summary>
          <div className="mt-3 space-y-4">
            {briefMissing ? (
              <div className="rounded-xl border border-zinc-200 p-6 text-center text-sm text-zinc-400 dark:border-zinc-800">
                今日尚无盘前简报：点右上「生成/刷新简报」，或等交易日 08:40 自动生成。
                <br />
                简报是盘中跟踪与盘后对照的唯一事实源，没有它 watcher 会空转。
              </div>
            ) : (
              <>
                <section id="sec-brief" className="space-y-2 scroll-mt-2">
                  <h3 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">盘前简报</h3>
                  {brief ? (
                    <FadeIn>
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-zinc-400">简报日期 {brief.brief_date}</span>
                        <EnvStrip brief={brief} />
                      </div>
                      <div className="mt-2 grid grid-cols-1 gap-3 lg:grid-cols-3">
                        {brief.directions.map((d) => (
                          <DirectionCard key={d.direction} d={d} />
                        ))}
                      </div>
                    </FadeIn>
                  ) : (
                    pending && <CardListSkeleton count={3} />
                  )}
                </section>

                <section id="sec-watcher" className="space-y-2 scroll-mt-2">
                  <h3 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">盘中 watcher 状态</h3>
                  <WatcherPanel watcher={watcher} pending={pending} />
                </section>

                <section id="sec-reminders" className="space-y-2 scroll-mt-2">
                  <h3 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
                    盘中提醒（{alerts.length} 条，当日去重）
                  </h3>
                  {pending && alerts.length === 0 ? (
                    <CardListSkeleton count={1} />
                  ) : alerts.length === 0 ? (
                    <div className="rounded-xl border border-zinc-200 p-4 text-xs text-zinc-400 dark:border-zinc-800">
                      暂无提醒。确认条件五项全过才触发（量比数据源缺 → 会压档）；
                      证伪任一触发即推送并当日静默。
                    </div>
                  ) : (
                    <FadeIn>
                      <div className="space-y-2">
                        {alerts.map((a) => (
                          <AlertItem key={a.key} a={a} />
                        ))}
                      </div>
                    </FadeIn>
                  )}
                </section>
              </>
            )}
          </div>
        </details>

        {/* ── 对照与复盘（对照表 / 近 30 日统计 / 精选复盘区）：默认收起 ── */}
        <details
          open={reviewOpen}
          onToggle={(e) => setReviewOpen((e.target as HTMLDetailsElement).open)}
          className="rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800"
        >
          <summary className="cursor-pointer select-none text-xs font-medium text-zinc-500 dark:text-zinc-400">
            对照与复盘（盘前 vs 实际 · 胜率统计 · 逐日归因{brief?.review ? ` · ${timeText(brief.review.reviewed_at)} 复盘` : ""}）
          </summary>
          <div className="mt-3 space-y-4">
            <section id="sec-review" className="space-y-2 scroll-mt-2">
              <h3 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
                今日盘前 vs 实际
                {brief?.review ? "" : "（未复盘，15:35 自动运行）"}
              </h3>
              {pending && reviewed.length === 0 ? (
                <TableSkeleton rows={3} />
              ) : reviewed.length === 0 ? (
                <div className="rounded-xl border border-zinc-200 p-4 text-xs text-zinc-400 dark:border-zinc-800">
                  今日尚未对照。点右上「运行对照」或等 15:35 调度（收盘后才有意义）。
                </div>
              ) : (
                <FadeIn>
                  <ReviewOutcomeTable reviewed={reviewed} />
                </FadeIn>
              )}
            </section>

            {stats ? (
              <FadeIn>
                <section className="space-y-2">
                  <h3 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">近 30 日胜率统计（跟踪）</h3>
                  <StatsPanel stats={stats} />
                </section>
              </FadeIn>
            ) : (
              pending && (
                <section className="space-y-2">
                  <h3 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">近 30 日胜率统计（跟踪）</h3>
                  <StatsSkeleton />
                </section>
              )
            )}

            {/* 精选复盘区（?review=1 / 生成复盘后展开；历史组合一致性可回溯） */}
            <section className="space-y-3">
              <h3 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">精选复盘</h3>
              {meta && meta.role_performance && meta.role_performance.length > 0 && (
                <RolePerformanceTable rows={meta.role_performance} />
              )}
              {meta && Object.keys(meta.reason_distribution).length > 0 && (
                <ReasonDistribution dist={meta.reason_distribution} />
              )}
              {reviews.length > 0 && <DailyReviews reviews={reviews} />}
              {history.length > 0 && <HistoryList history={history} />}
              {reviews.length === 0 && history.length === 0 && (
                <div className="rounded-xl border border-zinc-200 p-4 text-xs text-zinc-400 dark:border-zinc-800">
                  暂无复盘记录。点右上「生成复盘」对最近组合逐只归因。
                </div>
              )}
            </section>
          </div>
        </details>
      </div>

      <div className="shrink-0 text-[10px] text-zinc-500">
        {data?.meta?.weights
          ? `精选权重（${data.meta.regime?.regime ?? "平衡"}${data.meta.style_routing?.routed ? ` · 风格「${data.meta.style_routing.label}」` : ""}）：${Object.entries(data.meta.weights)
              .map(([k, v]) => `${{ sentiment: "情绪", news: "消息", tech: "技术", fundamental: "基本", capital: "资金", echelon: "梯队" }[k] ?? k} ${Math.round(v * 100)}%`)
              .join(" / ")}`
          : "精选五维权重：情绪 20% / 消息 25% / 技术 25% / 基本面 15% / 资金 15%"}
        {" · 一票否决 ×0.4 · "}
        盘中节拍阈值集中在 intraday_rules 常量表 · 全部输出为可解释依据与模拟跟踪，不构成买卖建议 · 数据有延迟
      </div>
    </main>
  );
}

/** 相位→风格路由 chip（审查 §4.1）：当日风格与权重偏移，悬停看完整依据；未路由时诚实说明。 */
function StyleRoutingChip({ sr }: { sr: StyleRouting }) {
  const dimLabel: Record<string, string> = {
    sentiment: "情绪", news: "消息", tech: "技术",
    fundamental: "基本", capital: "资金", echelon: "梯队",
  };
  const offsets = Object.entries(sr.offsets);
  return (
    <div
      className="shrink-0 rounded-lg border border-zinc-200 px-3 py-1.5 text-[11px] dark:border-zinc-800"
      title={`${sr.basis}（${sr.phase ?? "相位缺失"}）· 偏移在 regime 基础权重上叠加，配置可覆盖（picks_style_offsets_json）`}
    >
      <span className="text-zinc-400">当日风格</span>{" "}
      <span className="font-medium text-zinc-700 dark:text-zinc-200">{sr.label}</span>
      {offsets.length > 0 ? (
        <span className="ml-1.5 font-mono text-[10px] tabular-nums text-zinc-500 dark:text-zinc-400">
          {offsets
            .sort(([, a], [, b]) => Math.abs(b) - Math.abs(a))
            .map(([d, v]) => `${dimLabel[d] ?? d} ${v > 0 ? "+" : ""}${Math.round(v * 100)}%`)
            .join(" / ")}
        </span>
      ) : (
        <span className="ml-1.5 text-zinc-400">
          {sr.routed ? "（均衡，无偏移）" : "（相位缺失，未路由）"}
        </span>
      )}
    </div>
  );
}

/** useSearchParams 需要 Suspense 边界（Next 16 约束，workbench 同款结构）。 */
export default function HuntingPage() {
  return (
    <Suspense fallback={<PageSkeletonFallback label="猎场加载中" />}>
      <HuntingInner />
    </Suspense>
  );
}
