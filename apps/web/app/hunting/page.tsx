"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
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
  getPositionLabels,
} from "@/lib/api";
import { PickCard, StandAsideBanner, fromDailyPick, fromIntradayStock } from "@/components/picks/pick-card";
import {
  AlertItem,
  DirectionCard,
  EnvStrip,
  MacroCalendar,
  MarketOverviewStrip,
  OpportunitySection,
  ClimateBlock,
  OvernightBiasBlock,
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
import { PostMarketEnhance } from "@/components/hunting/post-market-enhance";
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
 * 结构：统计条（双口径独立不混算）→ **两条瀑布流**（盘中跟踪在上 / 盘前选择在下，
 * 2026-09-10 用户要求分区）→ 异动手风琴（题材→个股瀑布流）→ 盘中节拍（简报/watcher/
 * 提醒，折叠可展开）→ 对照与复盘（对照表 / 近 30 日统计 / 精选逐日复盘，折叠可展开）。
 *
 * 语义差异保持（审查 §3.3-6）：精选是 date+symbol 持久组合，跟踪是当日实时动态名单
 * ——统计条分别标注口径。深链：?tag= / ?theme= / ?sec= / ?review=1 全部保留
 * （旧 /picks /intraday 路径经 next.config 302 兜底）。全页不构成买卖建议。
 */

// 布局沿革：09-09 取消 tab 分类改单容器混排 → 09-10 两卡合并为同一个 PickCard 后
// **重新分区为两条瀑布流**（盘中在上）。原因：卡片字段已统一，但**来源节奏不同**
// （盘中=当日实时动态名单，盘前=收盘定次日持久组合），混排会让两种节奏互相干扰。
// 来源仍由卡片适配器写入 origin 并渲染右上角徽标（分区是对节奏的提示，徽标是对单条的确认）。
//
// 名额口径（09-10 用户要求「不要硬凑五个，也不要过多，按实际情况来选」）：
// 两个分区的只数都是**筛选结果**而非固定值——盘中按「确定性×辨识度」档位筛（unknown/低
// 不入选，上限 8），盘前按六维综合分入选门槛筛（<50 不入选，上限 5）。因此 0 只是合法结论。

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
  // ?tag= 深链仍可解析（nav-targets 兼容）但不再分流视图——单一瀑布流（2026-09-09）
  const [expandedTheme, setExpandedTheme] = useState<string | null>(() => sp.get("theme"));
  const sec = sp.get("sec");
  const secValid = /^(overview|opportunity|brief|watcher|reminders|review)$/.test(sec ?? "");
  // 折叠区开合：?sec= 深链自动展开对应组；?review=1 / 生成复盘成功展开复盘组
  const [beatsOpen, setBeatsOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(() => sp.get("review") === "1");

  // ?sec= 深链落在折叠区内 → 先展开对应分组。
  // 渲染期 adjust-state（P1-27）：当帧展开，DOM 已就绪，随后 effect 的滚动能直接命中。
  const [openedSec, setOpenedSec] = useState<string | null>(null);
  if (secValid && sec !== openedSec) {
    setOpenedSec(sec);
    if (sec === "brief" || sec === "watcher" || sec === "reminders") setBeatsOpen(true);
    if (sec === "review") setReviewOpen(true);
  }

  // ?sec= 滚动定位（展开已在上方渲染期完成，这里只负责滚动）
  useEffect(() => {
    if (!secValid) return;
    const scroll = () => {
      document.getElementById(`sec-${sec}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    scroll();
    const t = window.setTimeout(scroll, 400);
    return () => window.clearTimeout(t);
  }, [sec, secValid]);

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

  const alerts = brief?.alerts ?? [];
  const reviewed = (brief?.directions ?? []).filter((d) => d.review);
  const topItems = top?.items ?? [];
  const briefMissing = picksLoaded && intradayLoaded && brief === null;
  const pending = !picksLoaded || !intradayLoaded;

  // 猎场两条瀑布流（2026-09-10 用户要求分区，取代 09-09 的单容器混排）：
  // 盘中跟踪在前（实时优先）、盘前选择在后；同股两者都在时只出现在盘中组（防重 key）。
  // 闭环「标签」：已模拟持仓/已真实持仓（持仓状态派生，60s 轮询）
  const [posLabels, setPosLabels] = useState<Record<string, string>>({});
  useEffect(() => {
    const load = () => {
      getPositionLabels().then(setPosLabels).catch(() => {});
    };
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);

  // 依赖取**状态对象** data/top（引用稳定），不取派生的 items/topItems：
  // `?? []` 每次渲染都会新建数组引用，放进依赖会让 memo 每轮失效
  // （react-hooks/exhaustive-deps，P1-27）。
  //
  // 2026-09-10 用户要求分区：两卡合并后字段已统一，但**来源语义仍然不同**
  // （盘中=当日实时动态名单，盘前=收盘定次日持久组合），混排会让两种节奏混在一起，
  // 因此改为**两个容器 / 两条瀑布流，盘中在上**。同 symbol 只在盘中出现（盘前组去重）。
  const pickItems = useMemo(() => {
    const topSyms = new Set((top?.items ?? []).map((t) => t.symbol));
    return (data?.items ?? []).filter((p) => !topSyms.has(p.symbol));
  }, [data, top]);
  // 盘前名单的**真实只数**（去重前）：名额不兜底后「名单可能为空」是正常结论，
  // 因此分区是否渲染、抬头写几只、空态文案，一律以它为准，不以去重后的 pickItems 为准
  // ——否则「5 只全被盘中组去重」会误显示成空名单（2026-09-10 入选门槛落地时一并收口）。
  const pickTotal = data?.items?.length ?? 0;
  const minPickScore = data?.meta?.min_pick_score;
  // 换股门槛/换股上限已进控制台参数白名单（P1-15）⇒ **必须读 meta 的生效值**，
  // 不能硬编码：硬编码会在参数被调整后继续显示旧数（口径漂移）。
  const replaceThreshold = data?.meta?.replace_threshold;
  const maxSwaps = data?.meta?.max_swaps_per_day;
  const feedEmpty = topItems.length === 0 && pickTotal === 0;

  return (
    <main className="mx-auto flex h-full w-full max-w-[1400px] flex-col gap-3 overflow-hidden px-4 py-3">
      {/* 头部：标题 + 口径说明 + 刷新状态 + 操作 */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
        <h1 className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">猎场</h1>
        <span title="精选=盘后布下的猎物（date+symbol 持久组合）；跟踪=盘中正在追的猎物（当日实时动态名单）；题材异动=猎群">
          精选 · 跟踪 · 猎群
        </span>
        {picksFailed || intradayFailed ? (
          <span
            className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-800 dark:text-amber-300"
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
            className="rounded border border-zinc-300 px-2 py-0.5 text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100 disabled:opacity-50"
            title="立即重新拉取全部数据（通常无需手动点——页面已自动刷新）"
          >
            刷新数据
          </button>
          <button
            onClick={() => void act("picks")}
            disabled={busy !== null}
            className="rounded border border-sky-500/50 px-2 py-0.5 text-sky-700 dark:text-sky-400 hover:bg-sky-500/10 disabled:opacity-50"
            title="重跑五维评分管线（收盘后执行；覆盖当日组合）"
          >
            {busy === "picks" ? "计算中…" : "生成/刷新组合"}
          </button>
          <button
            onClick={() => void act("pickReview")}
            disabled={busy !== null}
            className="rounded border border-zinc-300 px-2 py-0.5 text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100 disabled:opacity-50"
            title="对最近组合逐只回顾：实际走势 vs 入选理由，走坏原因归类（生成后展开复盘区）"
          >
            {busy === "pickReview" ? "生成中…" : "生成复盘"}
          </button>
          <button
            onClick={() => void act("brief")}
            disabled={busy !== null}
            className="rounded border border-sky-500/50 px-2 py-0.5 text-sky-700 dark:text-sky-400 hover:bg-sky-500/10 disabled:opacity-50"
            title="重新采集证据生成/刷新今日简报（覆盖当日文件）"
          >
            {busy === "brief" ? "生成中…" : "生成/刷新简报"}
          </button>
          <button
            onClick={() => void act("beat")}
            disabled={busy !== null || briefMissing}
            className="rounded border border-zinc-300 px-2 py-0.5 text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100 disabled:opacity-50"
            title="手动推进一拍：取数 → 全部方向 confirm/falsify 判定（与盘中 watcher 同代码路径）"
          >
            {busy === "beat" ? "取拍中…" : "手动单拍"}
          </button>
          <button
            onClick={() => void act("review")}
            disabled={busy !== null || briefMissing}
            className="rounded border border-zinc-300 px-2 py-0.5 text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100 disabled:opacity-50"
            title="对照当日盘前方向 vs 实际盘面（四分类+误判分类）并回填提醒收益"
          >
            {busy === "review" ? "对照中…" : "运行对照"}
          </button>
        </div>
      </div>

      {hint && (
        <div className="shrink-0 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-700 dark:text-emerald-300">
          ✓ {hint}
        </div>
      )}
      {error && (
        <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-800 dark:text-amber-300">
          {error}
        </div>
      )}
      {data?.stale && (
        <div className="shrink-0 rounded-lg border border-zinc-300 px-3 py-1.5 text-xs text-zinc-600 dark:text-zinc-400 dark:border-zinc-700">
          当前展示 {data.date} 生成的组合——今日组合交易日 09:26 自动生成（急用可点右上「生成/刷新组合」立即重算）
        </div>
      )}
      {data?.note && <div className="shrink-0 text-xs text-zinc-600 dark:text-zinc-400">{data.note}</div>}

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
            <h2 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">今日盘面</h2>
            {opps?.hot_available === false && (
              <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-800 dark:text-amber-300">
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

        {/* ── 猎场瀑布流：两个容器 / 两条瀑布流（2026-09-10 用户要求，推翻 09-09 的单容器混排）──
            卡片已合并为同一个 PickCard（字段互补），但**来源节奏不同**：盘中是当日实时
            动态名单、盘前是收盘定次日的持久组合。混排会让两种节奏互相干扰，故分区，
            盘中在上（当下要看的东西优先）。 */}
        <section className="space-y-3">
          {/* 口径说明已内联到各分区标题行（避免同一句话在页头/分区/徽标重复三遍） */}
          {picksFailed && intradayFailed ? (
            <div className="rounded-lg border border-zinc-200 px-3 py-2.5 text-xs text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
              精选与跟踪数据均不可用（后端不可达或端点失败）——每 60s 自动重试。
            </div>
          ) : pending ? (
            <CardListSkeleton count={3} />
          ) : feedEmpty ? (
            <p className="py-8 text-center text-sm text-zinc-600 dark:text-zinc-400">
              暂无跟踪标的与精选组合——盘中候选成形后自动出现；也可点右上「生成/刷新组合」跑一次五维评分管线
              <br />
              （候选池 = 活跃事件标的池 ∪ 当日涨停池 ∪ 热股榜；六维评分达到入选门槛者入选，最多 5
              只、够格几只就是几只，全程可解释不构成买卖建议）。
            </p>
          ) : (
            <>
              {/* ① 盘中跟踪（在上） */}
              {topItems.length > 0 && (
                <FadeIn>
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <h2 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">盘中跟踪</h2>
                      <span className="text-[10px] text-zinc-600 dark:text-zinc-400">
                        {topItems.length} 只 · 当日实时动态名单（随盘面重算，非收盘名单）
                      </span>
                    </div>
                    <MasonryColumns>
                      {topItems.map((it) => (
                        <PickCard
                          key={it.symbol}
                          item={fromIntradayStock(it)}
                          positionLabel={(posLabels[it.symbol] as "sim" | "real" | undefined) ?? null}
                        />
                      ))}
                    </MasonryColumns>
                    {top?.criteria && <p className="text-[10px] text-zinc-600 dark:text-zinc-400">{top.criteria}</p>}
                  </div>
                </FadeIn>
              )}

              {/* ② 盘前选择（在下）——名单长度由质量决定：达到入选门槛几只就是几只，
                  0 只也是结论（弱市里硬凑满 5 只才是风险）。 */}
              {pickTotal > 0 ? (
                <FadeIn>
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <h2 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">盘前选择</h2>
                      <span className="text-[10px] text-zinc-600 dark:text-zinc-400">
                        {pickTotal} 只 · 收盘定次日持久组合（
                        {minPickScore != null ? `入选门槛 综合分≥${minPickScore}，不硬凑名额 · ` : ""}
                        {replaceThreshold != null ? `换股门槛 ${replaceThreshold} 分` : "换股门槛 15 分"}
                        {maxSwaps != null ? ` · 每日换股上限 ${maxSwaps} 只` : " · 每日换股上限 2 只"}）
                      </span>
                    </div>
                    {pickItems.length > 0 ? (
                      <MasonryColumns>
                        {pickItems.map((it) => (
                          <PickCard
                            key={it.symbol}
                            item={fromDailyPick(it)}
                            positionLabel={(posLabels[it.symbol] as "sim" | "real" | undefined) ?? null}
                          />
                        ))}
                      </MasonryColumns>
                    ) : (
                      <p className="text-[10px] text-zinc-600 dark:text-zinc-400">
                        {pickTotal} 只均已在盘中跟踪区展示（同一标的只出现一次，避免同一页重复卡片）。
                      </p>
                    )}
                  </div>
                </FadeIn>
              ) : (
                <FadeIn>
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <h2 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">盘前选择</h2>
                      <span className="text-[10px] text-zinc-600 dark:text-zinc-400">0 只 · 收盘定次日持久组合</span>
                    </div>
                    <p className="rounded-lg border border-zinc-200 px-3 py-2 text-[11px] text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
                      {data?.date == null
                        ? "尚未生成组合——点右上「生成/刷新组合」跑一次评分管线（或等收盘后的自动管线）。"
                        : `${data.date}${data.stale ? "（非今日，最近一次生成）" : ""} 无标的达到入选门槛` +
                          `${minPickScore != null ? `（综合分 ≥${minPickScore}）` : ""}` +
                          "——弱市里名单本就该短，硬凑满名额才是风险；这是筛选结论，不是数据缺失。"}
                    </p>
                  </div>
                </FadeIn>
              )}
            </>
          )}
          {(picksFailed || intradayFailed) && (
            <p className="text-[10px] text-amber-800 dark:text-amber-500">
              {picksFailed ? "盘前选择数据不可用（每 60s 自动重试）" : ""}
              {intradayFailed ? "盘中跟踪数据不可用（每 60s 自动重试）" : ""}
            </p>
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

        {/* ── 盘后增强（P1-5/6）：接力质量排序 + 潜伏观察池，默认收起 ── */}
        <PostMarketEnhance />

        {/* ── 盘中节拍（简报 / watcher / 提醒）：默认收起，深链或操作后展开 ── */}
        <details
          open={beatsOpen}
          onToggle={(e) => setBeatsOpen((e.target as HTMLDetailsElement).open)}
          className="rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800"
        >
          <summary className="cursor-pointer select-none text-xs font-medium text-zinc-600 dark:text-zinc-400">
            盘中节拍（盘前简报 · watcher · 提醒{alerts.length > 0 ? ` · 今日 ${alerts.length} 条` : ""}）
          </summary>
          <div className="mt-3 space-y-4">
            {briefMissing ? (
              <div className="rounded-xl border border-zinc-200 p-6 text-center text-sm text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
                今日尚无盘前简报：点右上「生成/刷新简报」，或等交易日 08:40 自动生成。
                <br />
                简报是盘中跟踪与盘后对照的唯一事实源，没有它 watcher 会空转。
              </div>
            ) : (
              <>
                <section id="sec-brief" className="space-y-2 scroll-mt-2">
                  <h3 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">盘前简报</h3>
                  {brief ? (
                    <FadeIn>
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-zinc-600 dark:text-zinc-400">简报日期 {brief.brief_date}</span>
                        <EnvStrip brief={brief} />
                      </div>
                      <div className="mt-2">
                        <MacroCalendar brief={brief} />
                      </div>
                      <div className="mt-2">
                        <OvernightBiasBlock bias={brief.overnight_bias} />
                      </div>
                      <div className="mt-2">
                        <ClimateBlock climate={brief.climate} />
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
                  <h3 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">盘中 watcher 状态</h3>
                  <WatcherPanel watcher={watcher} pending={pending} />
                </section>

                <section id="sec-reminders" className="space-y-2 scroll-mt-2">
                  <h3 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
                    盘中提醒（{alerts.length} 条，当日去重）
                  </h3>
                  {pending && alerts.length === 0 ? (
                    <CardListSkeleton count={1} />
                  ) : alerts.length === 0 ? (
                    <div className="rounded-xl border border-zinc-200 p-4 text-xs text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
                      暂无提醒。确认条件五项全过才触发（量比数据源缺 → 会压档）；
                      证伪任一触发即推送并当日静默。
                    </div>
                  ) : (
                    <FadeIn>
                      <div className="space-y-2">
                        {alerts.map((a, i) => (
                          <AlertItem key={a.key || `${a.kind}:${a.symbol}:${i}`} a={a} />
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
          <summary className="cursor-pointer select-none text-xs font-medium text-zinc-600 dark:text-zinc-400">
            对照与复盘（盘前 vs 实际 · 胜率统计 · 逐日归因{brief?.review ? ` · ${timeText(brief.review.reviewed_at)} 复盘` : ""}）
          </summary>
          <div className="mt-3 space-y-4">
            <section id="sec-review" className="space-y-2 scroll-mt-2">
              <h3 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
                今日盘前 vs 实际
                {brief?.review ? "" : "（未复盘，15:35 自动运行）"}
              </h3>
              {pending && reviewed.length === 0 ? (
                <TableSkeleton rows={3} />
              ) : reviewed.length === 0 ? (
                <div className="rounded-xl border border-zinc-200 p-4 text-xs text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
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
                  <h3 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">近 30 日胜率统计（跟踪）</h3>
                  <StatsPanel stats={stats} />
                </section>
              </FadeIn>
            ) : (
              pending && (
                <section className="space-y-2">
                  <h3 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">近 30 日胜率统计（跟踪）</h3>
                  <StatsSkeleton />
                </section>
              )
            )}

            {/* 精选复盘区（?review=1 / 生成复盘后展开；历史组合一致性可回溯） */}
            <section className="space-y-3">
              <h3 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">精选复盘</h3>
              {meta && meta.role_performance && meta.role_performance.length > 0 && (
                <RolePerformanceTable rows={meta.role_performance} />
              )}
              {meta && Object.keys(meta.reason_distribution).length > 0 && (
                <ReasonDistribution dist={meta.reason_distribution} />
              )}
              {reviews.length > 0 && <DailyReviews reviews={reviews} />}
              {history.length > 0 && <HistoryList history={history} />}
              {reviews.length === 0 && history.length === 0 && (
                <div className="rounded-xl border border-zinc-200 p-4 text-xs text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
                  暂无复盘记录。点右上「生成复盘」对最近组合逐只归因。
                </div>
              )}
            </section>
          </div>
        </details>
      </div>

      <div className="shrink-0 text-[10px] text-zinc-600 dark:text-zinc-400">
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
  // 相位来源（2026-09-09：style_routing 改为读取时实时重算，来源必须显式——
  // 生成时刻快照 vs 实时相位语义不同，混着看会误判「为什么今天没路由」）
  const srcNote =
    sr.phase_source === "live" ? " · 实时相位"
    : sr.phase_source === "unavailable" ? ` · ${sr.phase_note ?? "实时相位不可用，显示生成时刻快照"}`
    : "";
  return (
    <div
      className="shrink-0 rounded-lg border border-zinc-200 px-3 py-1.5 text-[11px] dark:border-zinc-800"
      title={`${sr.basis}（${sr.phase ?? "相位缺失"}${srcNote}）· 偏移在 regime 基础权重上叠加，配置可覆盖（picks_style_offsets_json）`}
    >
      <span className="text-zinc-600 dark:text-zinc-400">当日风格</span>{" "}
      <span className="font-medium text-zinc-700 dark:text-zinc-200">{sr.label}</span>
      {offsets.length > 0 ? (
        <span className="ml-1.5 font-mono text-[10px] tabular-nums text-zinc-600 dark:text-zinc-400">
          {offsets
            .sort(([, a], [, b]) => Math.abs(b) - Math.abs(a))
            .map(([d, v]) => `${dimLabel[d] ?? d} ${v > 0 ? "+" : ""}${Math.round(v * 100)}%`)
            .join(" / ")}
        </span>
      ) : (
        <span className="ml-1.5 text-zinc-600 dark:text-zinc-400">
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
