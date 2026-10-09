"use client";

import "./hunting.css";

import { patchWorkspaceUrl } from "@/lib/task-navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import {
  ApiError,
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
  DailyPlanBlock,
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
import { OpportunityEvidencePanel } from "@/components/hunting/opportunity-evidence-panel";
import { HuntingTaskRail } from "@/components/hunting/hunting-task-rail";
import { LeaderResearchPanel } from "@/components/hunting/leader-research-panel";
import { useExitPresence } from "@/hooks/use-exit-presence";
import { ModalShell } from "@/components/ui/modal-shell";
import { WatchLedgerPanel } from "@/components/hunting/watch-ledger-panel";
import { CandidateCollection } from "@/components/ui/candidate-collection";

/**
 * Real selection consumers, organised by discover / observe / verify.
 * Daily persisted combinations and dynamic intraday candidates stay separate.
 * Existing view/panel/sec/theme/review links retain their consumer and return context.
 */
function HuntingInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const requestedView = sp.get("view");
  const view = requestedView === "evidence" || requestedView === "tracking" || requestedView === "research" || requestedView === "review"
    ? requestedView : sp.get("sec") === "review" || sp.get("review") === "1" ? "review" : "discover";
  // Evidence, reference and research own their reads. Do not mount the old feed behind them.
  const isDiscovery = view === "discover" || view === "review";

  const panel = sp.get("panel");
  const accessory = useExitPresence((panel === "evidence" || panel === "tracking") && panel !== view ? panel : null);

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
  const [reviewReadFailures, setReviewReadFailures] = useState<string[]>([]);

  // —— 跟踪组数据 ——
  const [brief, setBrief] = useState<MorningBrief | null>(null);
  const [briefReadFailed, setBriefReadFailed] = useState(false);
  const [watcher, setWatcher] = useState<WatcherState | null>(null);
  const [stats, setStats] = useState<IntradayReviewStats | null>(null);
  const [opps, setOpps] = useState<IntradayOpportunities | null>(null);
  const [top, setTop] = useState<IntradayTopPayload | null>(null);
  const [intradayLoaded, setIntradayLoaded] = useState(false);
  const [intradayFailed, setIntradayFailed] = useState(false);
  const [topReadFailed, setTopReadFailed] = useState(false);


  // —— URL 状态（深链为真相源）——
  // ?tag= 深链仍可解析（nav-targets 兼容）但不再分流视图——单一瀑布流（2026-09-09）
  const expandedTheme = sp.get("theme");
  const sec = sp.get("sec");
  const secValid = /^(overview|candidates|daily|opportunity|postmarket|brief|watcher|reminders|review)$/.test(sec ?? "");
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

  // 闸门展示口径（2026-09-16）：优先**读取时刻复核**结果，缺省退回生成时刻落库值
  // （旧后端 / 复核不可用时不至于整个横幅消失）。对照面始终传 stored。
  const gateView = data?.meta?.gate_live ?? data?.meta?.gate;

  const toggleTheme = useCallback(
    (t: string) => {
      const next = expandedTheme === t ? null : t;
      const params = new URLSearchParams(sp.toString());
      if (next) params.set("theme", next);
      else params.delete("theme");
      const qs = params.toString();
      router.replace(`/hunting${qs ? `?${qs}` : ""}`, { scroll: false });
    },
    [expandedTheme, sp, router],
  );

  const load = useCallback(async () => {
    // 精选主接口失败单独披露，不能因统计接口成功而伪装为有效空组合。
    {
      const [d, h, r, m, sh] = await Promise.all([
        getTodayPicks().catch(() => null),
        getPicksHistory(10).catch(() => null),
        getPickReviews().catch(() => null),
        getPicksMeta().catch(() => null),
        getSignalHealth().catch(() => null),
      ]);
      setData(d);
      setHistory(h ?? []);
      setReviews(r ?? []);
      setReviewReadFailures([...(h === null ? ["历史组合"] : []), ...(r === null ? ["精选归因"] : []), ...(m === null ? ["角色统计"] : [])]);
      setMeta(m);
      setHealth(sh);
      setPicksFailed(d === null);
      setPicksLoaded(true); // 成败都算"拉过"：失败有警示，不能永远停在骨架
    }
    // 跟踪组（原 /intraday 四端点 + intraday-top）
    {
      const [b, w, s, o, tp] = await Promise.all([
        getMorningBriefToday()
          .then((data) => ({ data, failed: false }))
          .catch((e: unknown) => ({ data: null, failed: !(e instanceof ApiError && e.status === 404) })),
        getWatcherState().catch(() => null),
        getIntradayReview().catch(() => null),
        getIntradayOpportunities().catch(() => null),
        getIntradayTop().catch(() => null),
      ]);
      setBrief(b.data);
      setBriefReadFailed(b.failed);
      setWatcher(w);
      setStats(s);
      setOpps(o);
      setTop(tp);
      setTopReadFailed(tp === null);
      setIntradayFailed(b.failed || tp === null || o === null || (b.data === null && w === null && s === null));
      setIntradayLoaded(true);
    }
  }, []);

  // 挂载即拉 + 60s 轮询（对齐后端 watcher 节拍）。
  // 2026-09-11（S2-5）：可见性暂停 + 回可见补拉已内建在 usePollingFetch/useResource 里，
  // 原先手写的那套 visibilitychange 守卫是重复实现，已删除（两个 effect 合一）。
  usePollingFetch(load, 60_000, undefined, {enabled: isDiscovery});

  const alerts = brief?.alerts ?? [];
  const reviewed = (brief?.directions ?? []).filter((d) => d.review);
  const topItems = top?.items ?? [];
  const topRefItems = top?.reference_items ?? [];
  const briefMissing = picksLoaded && intradayLoaded && brief === null && !briefReadFailed;
  const pending = !picksLoaded || !intradayLoaded;

  // ?sec= 滚动定位（展开已在上方渲染期完成，这里只负责滚动）
  useEffect(() => {
    if (!secValid) return;
    const scroll = () => {
      document.getElementById(`sec-${sec}`)?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" });
    };
    scroll();
    const t = window.setTimeout(scroll, 400);
    return () => window.clearTimeout(t);
  }, [sec, secValid, pending, view]);



  // 猎场两条瀑布流（2026-09-10 用户要求分区，取代 09-09 的单容器混排）：
  // 盘中跟踪在前（实时优先）、盘前选择在后；同股两者都在时只出现在盘中组（防重 key）。
  // 闭环「标签」：已模拟持仓/已真实持仓（持仓状态派生，60s 轮询）
  // 2026-09-11（S2-5）：原为裸 setInterval，已收编到统一入口（与 workbench 同款重复实现）。
  const [posLabels, setPosLabels] = useState<Record<string, string>>({});
  usePollingFetch(async () => {
    const m = await getPositionLabels().catch(() => null);
    if (m) setPosLabels(m);
  }, 60_000, undefined, {enabled: view === "discover"});

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

  const sourceStatus = (<>
      {isDiscovery && data?.stale && (
        <div className="shrink-0 rounded-lg border border-zinc-300 px-3 py-1.5 text-xs text-zinc-600 dark:text-zinc-400 dark:border-zinc-700">
          当前展示 {data.date} 生成的组合——今日组合交易日 09:26 自动生成（这里只读取已保存组合，生产状态见系统维护）
        </div>
      )}
      {isDiscovery && data?.note && <div className="shrink-0 text-xs text-zinc-600 dark:text-zinc-400">{data.note}</div>}
  </>);

  const beatsPanel = (<details
          open={beatsOpen}
          onToggle={(e) => setBeatsOpen((e.target as HTMLDetailsElement).open)}
          className="opportunity-fold"
        >
          <summary>
            <span><strong>盘中节拍</strong><span className="fold-caption">盘前简报 · 监测状态 · 提醒</span></span>
            {alerts.length > 0 && <span className="fold-meta">今日 {alerts.length} 条</span>}
            <span className="fold-chevron" aria-hidden="true"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m7 10 5 5 5-5" /></svg></span>
          </summary>
          <div className="mt-3 space-y-4">
            {briefReadFailed ? (
              <div className="rounded-xl border border-amber-500/40 p-6 text-center text-sm text-amber-800 dark:text-amber-300">
                盘前简报读取失败，当前无法确认是否已生成。请刷新重试；不要把读取失败视为今日无简报。
              </div>
            ) : briefMissing ? (
              <div className="ui-card rounded-xl border border-zinc-200 p-6 text-center text-sm text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
                今日尚无盘前简报：可在系统维护核调度与受控生成；自动生产取决于服务和开关。
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
                      <div className="mt-2">
                        <DailyPlanBlock plan={brief.daily_plan} />
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
                    <div className="ui-card rounded-xl border border-zinc-200 p-4 text-xs text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
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
        </details>);

  const journalPanel = (<details
          open={view === "review" || reviewOpen}
          onToggle={(e) => setReviewOpen((e.target as HTMLDetailsElement).open)}
          className="opportunity-fold hunting-journal"
        >
          <summary>
            <span><strong>当日对照与精选复盘</strong><span className="fold-caption">盘前与实际 · 胜率统计 · 逐日归因</span></span>
            {brief?.review && <span className="fold-meta">{timeText(brief.review.reviewed_at)} 复盘</span>}
            <span className="fold-chevron" aria-hidden="true"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m7 10 5 5 5-5" /></svg></span>
          </summary>
          <div className="hunting-journal-body mt-3 space-y-4" tabIndex={0} role="region" aria-label="对照与复盘记录">
            {view === "review" && sourceStatus}
            <section id="sec-review" className="space-y-2 scroll-mt-2">
              <h3 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
                今日盘前 vs 实际
                {briefReadFailed ? "（读取失败）" : brief?.review ? "" : "（未复盘，计划 15:35 运行）"}
              </h3>
              {pending && reviewed.length === 0 ? (
                <TableSkeleton rows={3} />
              ) : briefReadFailed ? <p role="alert" className="hunting-empty">盘前简报读取失败，当前无法核对原判断与复盘结果。请刷新重试。</p> : reviewed.length === 0 ? (
                <div className="ui-card rounded-xl border border-zinc-200 p-4 text-xs text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
                  今日尚未对照。请在系统维护核对盘后调度（收盘后才有意义）。
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
              <section className="space-y-2">
                <h3 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">近 30 日胜率统计（跟踪）</h3>
                {pending ? <StatsSkeleton /> : <p className="hunting-empty">统计暂不可用。缺少统计不表示胜率为零，等待重试读取。</p>}
              </section>
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
              {reviewReadFailures.length > 0 && <p role="alert" className="hunting-empty">{reviewReadFailures.join("、")}读取失败。请刷新重试；以下只展示成功读取的结果，不能据此认定没有记录。</p>}
              {reviewReadFailures.length === 0 && reviews.length === 0 && history.length === 0 && (
                <div className="ui-card rounded-xl border border-zinc-200 p-4 text-xs text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
                  暂无复盘记录。可在系统维护对已有组合生成归因。
                </div>
              )}
            </section>
          </div>
        </details>);

  return (
    <main data-workspace="hunting" className="task-page hunting-workspace mx-auto flex h-full w-full max-w-[1440px] flex-col overflow-hidden">
      {/* 头部：标题 + 口径说明 + 刷新状态 + 操作 */}
      <header className="workspace-masthead hunting-masthead">
        <div><h1>选股</h1><p className="hunting-page-intro">先发现，再核对条件，持续跟踪变化。</p></div>
        <div className="workspace-context">
        {isDiscovery && (picksFailed || intradayFailed) ? (
          <span
            className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-800 dark:text-amber-300"
            title="部分数据端点失败（后端不可达或网络中断）。每 60s 自动重试，也可点「刷新数据」。"
          >
            数据加载失败 · 自动重试中
          </span>
        ) : (
          <span className="text-[10px]" title={view === "tracking" ? "跟踪记录每 30s 读取；切回会立即刷新。" : "当前视图每 60s 读取；切回会立即刷新。"}>
            {view === "tracking" ? "记录每 30s 刷新" : "每 60s 自动刷新"}
          </span>
        )}
        <div className="ml-auto flex flex-wrap items-center justify-end gap-1.5">
          {isDiscovery && <button data-action="secondary"
            onClick={() => void load()}
            className="quiet-action"
            title="立即重新拉取全部数据（通常无需手动点——页面已自动刷新）"
          >
            刷新数据
          </button>}
          <Link href="/agent?area=maintenance&tab=operations" className="quiet-action">生产状态与维护</Link>
        </div></div>
      </header>

      <HuntingTaskRail view={view} section={sec} search={sp.toString()} />
      <div className="hunting-current-context">
        <details className="hunting-context-help"><summary>使用说明<svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m7 10 5 5 5-5" /></svg></summary><p>{view === "evidence" ? "核对同版依据、等待条件与反证；研究观察不取得交易资格。"
          : view === "tracking" ? "参考跟踪记录与收盘对照；关注、参考价均不是持仓或成交。"
          : view === "research" ? "研究强势形成过程；保留来源、缺项与后续观察，尚未验证的规律不进入交易排序。"
          : view === "review" ? "比较原判断与实际结果；精选、参考轨和模拟执行分别统计。"
          : "盘中候选实时变化；每日精选保留组合日期，两种名单分开核对。"}</p></details>
        <Link className="quiet-action" href={patchWorkspaceUrl("/workbench", sp.toString(), {mode: "positions", account: "paper", view: null, panel: null, from: `/hunting?${sp.toString()}`})}>持仓与模拟</Link>
      </div>
      {accessory.value && <ModalShell open={accessory.active} label={accessory.value === "evidence" ? "机会证据台" : "参考跟踪"} size="lg" presentation="drawer" expandable onClose={() => router.replace(patchWorkspaceUrl("/hunting", sp.toString(), {panel: null}), {scroll: false})} header={<div><h2 className="text-lg font-semibold">{accessory.value === "evidence" ? "机会证据台" : "参考跟踪"}</h2></div>} footer="保留当前机会位置；参考价与观察记录不是成交，不构成买卖建议。">
        {accessory.active && (accessory.value === "evidence" ? <OpportunityEvidencePanel date={sp.get("date") ?? undefined} onDateChange={date => router.replace(patchWorkspaceUrl("/hunting", sp.toString(), {date}), {scroll: false})} /> : <WatchLedgerPanel />)}
      </ModalShell>}
      <div className="hunting-content min-h-0 flex-1" data-view={view} aria-label="选股内容">
        {view === "evidence" ? <OpportunityEvidencePanel date={sp.get("date") ?? undefined} onDateChange={date => { const p = new URLSearchParams(sp.toString()); p.set("date", date); router.replace(`/hunting?${p.toString()}`, {scroll:false}); }} /> : view === "tracking" ? <WatchLedgerPanel /> : view === "research" ? <LeaderResearchPanel date={sp.get("date") ?? undefined} onDateChange={date => router.replace(patchWorkspaceUrl("/hunting", sp.toString(), {date: date ?? null}), {scroll: false})} /> : <>
        {view === "discover" && <>
<div className="hunting-discovery-grid"><div className="hunting-candidate-flow" tabIndex={0} role="region" aria-label="候选结果与题材">
        <div className="hunting-candidate-notices">
        {/* ── 空仓闸门横幅（风险提示置顶）──
            2026-09-16 动态化：以**读取时刻复核**（meta.gate_live）为准展示，
            生成时刻落库值（meta.gate）作对照。此前只显示落库值 ⇒ 开盘 3 分钟的
            瞬时快照被当全天结论挂着，实测白天出现「横幅喊空仓观望 + 紧邻 chip 显示
            题材进攻」两个相反结论。实时复核不可用时 gate_live 退化为落库值 + 原因说明，
            故这里仍以它为主、gate 作对照即可（不必再判分支）。 */}
        {gateView && (
          <StandAsideBanner
            gate={gateView}
            stored={data?.meta?.gate}
            generatedAt={data?.meta?.generated_at}
          />
        )}
        {sourceStatus}
        </div>
        <section id="sec-candidates" className="hunting-candidates space-y-3">
          <div className="hunting-section-heading">
            <div><h2 className="hunting-section-title">盘中候选</h2><p>当日动态名单 · 当前未封板，进入参与评估不保证成交</p></div>
            {!pending && !topReadFailed && <span className="hunting-section-count">{topItems.length} 只</span>}
          </div>
          <p className="hunting-source-note" title="名单与参考区均按账户交易权限过滤">权限 {top?.tradable_boards ?? "沪市主板 / 深市主板"}</p>
          {pending ? <CardListSkeleton count={2} /> : topReadFailed ? <div role="alert" className="hunting-empty">盘中候选读取失败。请刷新重试；当前无法判断是否有符合条件的标的。</div>
            : topItems.length > 0 ? <CandidateCollection>{topItems.map(it => <PickCard key={it.symbol} item={fromIntradayStock(it)} positionLabel={(posLabels[it.symbol] as "sim" | "real" | undefined) ?? null} />)}</CandidateCollection>
            : <div className="hunting-empty">当前没有可参与候选。题材尚未集中或条件未满足时，名单可以为空；系统不会凑满名额。</div>}
          {top?.criteria && <p className="hunting-source-note">{top.criteria}</p>}
          {topRefItems.length > 0 && <details className="hunting-reference-fold">
            <summary>涨停梯队 {topRefItems.length} 只<span>参考集合，当前状态逐股核对</span></summary>
            <p className="hunting-source-note">{top?.reference_criteria ?? "保留今日曾封板身份；当前仍封、已开板或未判，以卡片对应时点为准。仅用于观察资金集中方向。"}</p>
            <CandidateCollection>{topRefItems.map(it => <PickCard key={it.symbol} item={fromIntradayStock(it)} positionLabel={(posLabels[it.symbol] as "sim" | "real" | undefined) ?? null} />)}</CandidateCollection>
          </details>}
        </section>
        <section id="sec-daily" className="hunting-daily space-y-3">
          <div className="hunting-section-heading"><div><h2 className="hunting-section-title">每日精选</h2><p>{data?.date ?? "组合日期待读取"} · 收盘定次日的持久组合</p></div>{!pending && !picksFailed && <span className="hunting-section-count">{pickTotal} 只</span>}</div>
          {pending ? <CardListSkeleton count={2} /> : picksFailed ? <div role="alert" className="hunting-empty">每日精选读取失败。请刷新重试；读取失败不表示没有组合。</div>
            : pickTotal === 0 ? <div className="hunting-empty">{data?.date == null
              ? "尚未生成组合。此处仅读取已保存结果；请在系统维护核对调度与受控生成。"
              : `${data.date}${data.stale ? "（最近一次生成，非今日）" : ""} 没有标的达到入选门槛${minPickScore != null ? `（综合分≥${minPickScore}）` : ""}。这是筛选结论，不是数据缺失。`}</div>
            : pickItems.length > 0 ? <CandidateCollection>{pickItems.map(it => <PickCard key={it.symbol} item={fromDailyPick(it)} positionLabel={(posLabels[it.symbol] as "sim" | "real" | undefined) ?? null} />)}</CandidateCollection>
            : <div className="hunting-empty">{pickTotal} 只均已在盘中候选区展示。同一标的保留一张主卡，组合身份与来源仍可核对。</div>}
          <p className="hunting-source-note">{minPickScore != null ? `综合分≥${minPickScore} · ` : ""}{replaceThreshold != null ? `换股门槛 ${replaceThreshold} 分` : "换股门槛未返回"} · {maxSwaps != null ? `每日换股上限 ${maxSwaps} 只` : "换股上限未返回"}；够格几只就保留几只。</p>
        </section>

        {/* ── 异动手风琴（题材 → 个股瀑布流）── */}
        <div id="sec-opportunity" className="scroll-mt-2">
          {opps ? (
            <FadeIn>
              <OpportunitySection opps={opps} expanded={expandedTheme} onToggle={toggleTheme} showLedger={false} />
            </FadeIn>
          ) : (
            pending ? <CardListSkeleton count={3} /> : <p role="alert" className="hunting-empty">题材参与数据读取失败，请刷新重试。无法据此判断没有题材机会。</p>
          )}
        </div>

        {/* ── 盘后增强（P1-5/6）：接力质量排序 + 潜伏观察池，默认收起 ── */}
        <div id="sec-postmarket"><PostMarketEnhance initiallyOpen={sec === "postmarket"} /></div>
        </div><aside className="hunting-context-column" tabIndex={0} aria-label="市场环境、节拍与复盘">
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
                人气榜不可用：辨识度判定不完整
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


        <div className="hunting-reading-note"><h2>如何使用这份名单</h2><p>先看形成依据，再核对来源时间、参与条件和失效条件。强势、入选与可成交是不同事实。</p><p>需要持续留痕时进入“跟踪记录”；需要核验历史判断时进入“验证复盘”。</p></div>
        {beatsPanel}
        {journalPanel}
        </aside></div></>}
        {view === "review" && journalPanel}

        </>}
      </div>

      <details className="hunting-method-note"><summary>判断口径与风险说明</summary><p>
        {data?.meta?.weights
          ? `精选权重（${data.meta.regime?.regime ?? "平衡"}${data.meta.style_routing?.routed ? ` · 风格「${data.meta.style_routing.label}」` : ""}）：${Object.entries(data.meta.weights)
              .map(([k, v]) => `${{ sentiment: "情绪", news: "消息", tech: "技术", fundamental: "基本", capital: "资金", echelon: "梯队" }[k] ?? k} ${Math.round(v * 100)}%`)
              .join(" / ")}`
          : "服务端未返回生效权重，不能据此判断当前规则。"}
        {" · "}全部输出为可解释依据与模拟跟踪，不构成买卖建议。数据有延迟；参与条件及手工模拟动作须由服务端复核。
      </p></details>
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
    <Suspense fallback={<PageSkeletonFallback label="选股加载中" />}>
      <HuntingInner />
    </Suspense>
  );
}
