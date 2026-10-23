"use client";

import { dailyEmptySummary } from "@/lib/picks-gate";

import "./hunting.css";
import { SelectionValidationPanel } from "@/components/hunting/validation-panel";

import { patchWorkspaceUrl } from "@/lib/task-navigation";
import { Suspense, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type UIEvent } from "react";
import Link from "next/link";
import { inspectionClick, useInspection } from "@/components/inspection/inspection-context";

import { useRouter, useSearchParams } from "next/navigation";
import { useResource } from "@/hooks/use-resource";
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
  type DailyPickItem,
  type MorningBrief,
  type StyleRouting,
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
import { PickDetailModal } from "@/components/picks/pick-detail-modal";
import { CandidateCollection } from "@/components/ui/candidate-collection";

/**
 * Real selection consumers, organised by discover / observe / verify.
 * Daily persisted combinations and dynamic intraday candidates stay separate.
 * Existing view/panel/sec/theme/review links retain their consumer and return context.
 */
function HuntingInner() {
  const { open: inspect } = useInspection();
  const router = useRouter();
  const sp = useSearchParams();
  const requestedView = sp.get("view");
  const view = requestedView === "validation" || requestedView === "evidence" || requestedView === "tracking" || requestedView === "research" || requestedView === "review"
    ? requestedView : sp.get("sec") === "review" || sp.get("review") === "1" ? "review" : "discover";
  // Evidence, reference and research own their reads. Do not mount the old feed behind them.
  const isDiscovery = view === "discover" || view === "review";
  const isDiscoverView = view === "discover";
  const reviewDate = view === "review" ? sp.get("date") ?? undefined : undefined;
  const reviewVersion = view === "review" ? sp.get("version") ?? undefined : undefined;

  const panel = sp.get("panel");
  const accessory = useExitPresence((panel === "evidence" || panel === "tracking") && panel !== view ? panel : null);

  const [dailyDetail, setDailyDetail] = useState<{item: DailyPickItem; date: string | null; version: string | null; group: "candidates" | "daily"} | null>(null);
  const [referenceOpen, setReferenceOpen] = useState(false);
  const referencePresence = useExitPresence(referenceOpen ? true : null);
  const referenceId = useId();

  // —— URL 状态（深链为真相源）——
  // ?tag= 深链仍可解析（nav-targets 兼容）但不再分流视图——单一瀑布流（2026-09-09）
  const expandedTheme = sp.get("theme");
  const sec = sp.get("sec");
  const selectedGroup = sec === "daily" ? "daily" : "candidates";
  const flowRef = useRef<HTMLDivElement>(null);
  const discoveryRef = useRef<HTMLDivElement>(null);
  const groupScroll = useRef({candidates: 0, daily: 0});
  const scrollOwner = useCallback(() => {
    const flow = flowRef.current;
    return flow && ["auto", "scroll", "overlay"].includes(window.getComputedStyle(flow).overflowY) ? flow : discoveryRef.current;
  }, []);
  const recordGroupScroll = (event: UIEvent<HTMLDivElement>) => {
    const owner = scrollOwner();
    if (owner && event.currentTarget === owner && event.target === owner) groupScroll.current[selectedGroup] = owner.scrollTop;
  };
  useLayoutEffect(() => {
    if (!isDiscoverView) return;
    const owner = scrollOwner();
    if (owner) owner.scrollTop = groupScroll.current[selectedGroup];
  }, [selectedGroup, isDiscoverView, scrollOwner]);
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

  // Each visible result owns its request. An auxiliary report never holds back candidates.
  const options = {intervalMs: 60_000, enabled: isDiscovery};
  const discoveryOptions = {...options, enabled: isDiscoverView};
  const daily = useResource(getTodayPicks, options);
  // Closed review panels do not poll archives. Current gates keep their own live read.
  const reviewOptions = {...options, enabled: isDiscovery && (view === "review" || reviewOpen)};
  const historyResource = useResource(() => getPicksHistory(10), {...reviewOptions,
    enabled: reviewOptions.enabled && (daily.data !== undefined || !!daily.error), intervalMs: null,
    key: `${daily.data?.date ?? ""}/${daily.data?.meta?.selection_version ?? ""}`});
  const reviewsResource = useResource(() => getPickReviews(reviewDate, reviewVersion), {...reviewOptions,
    // A selected version identifies its basis, not a sealed outcome: D1/D3/D5 can arrive later.
    intervalMs: 60_000, key: `${reviewDate ?? ""}/${reviewVersion ?? ""}`});
  const metaResource = useResource(getPicksMeta, reviewOptions);
  const healthResource = useResource(getSignalHealth, discoveryOptions);
  const briefResource = useResource(async () => {
    try { return await getMorningBriefToday(); }
    catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }
  }, options);
  const watcherResource = useResource(getWatcherState, discoveryOptions);
  const statsResource = useResource(getIntradayReview, options);
  const opportunitiesResource = useResource(getIntradayOpportunities, discoveryOptions);
  const intraday = useResource(getIntradayTop, discoveryOptions);
  const data = daily.data ?? null;
  const history = historyResource.data ?? [];
  const reviews = reviewsResource.data ?? [];
  const meta = metaResource.data ?? null;
  const health = healthResource.data ?? null;
  const picksLoaded = daily.status !== "pending" && daily.status !== "unknown";
  const picksFailed = !!daily.error || (picksLoaded && data === null);
  const reviewReadFailures = [
    ...(historyResource.error ? ["历史组合"] : []),
    ...(reviewsResource.error ? ["精选归因"] : []),
    ...(metaResource.error ? ["角色统计"] : []),
  ];
  const brief = briefResource.data ?? null;
  const briefReadFailed = !!briefResource.error;
  const watcher = watcherResource.data ?? null;
  const stats = statsResource.data ?? null;
  const opps = opportunitiesResource.data ?? null;
  const top = intraday.data ?? null;
  const intradayLoaded = intraday.status !== "pending" && intraday.status !== "unknown";
  const topReadFailed = !!intraday.error || (intradayLoaded && top === null);
  const intradayFailed = briefReadFailed || !!statsResource.error || (isDiscoverView && (topReadFailed || !!opportunitiesResource.error || !!watcherResource.error || !!healthResource.error));
  // A failed re-read retains observation content, never current gate eligibility.
  const gateView = daily.error ? undefined : data?.meta?.gate_live ?? data?.meta?.gate;
  const detailItem = dailyDetail && isDiscoverView && dailyDetail.group === selectedGroup && data?.date === dailyDetail.date && (data?.meta?.selection_version ?? null) === dailyDetail.version
    ? data?.items.find(item => item.symbol === dailyDetail.item.symbol) ?? null : null;
  // Do not revive a captured ready snapshot after its date/version/member identity exits.
  if (dailyDetail && !detailItem) setDailyDetail(null);
  const showDailyDetail = (item: DailyPickItem) => setDailyDetail({item, date: data?.date ?? null, version: data?.meta?.selection_version ?? null, group: selectedGroup});
  const load = () => {
    for (const resource of [daily, historyResource, reviewsResource, metaResource, healthResource, briefResource, watcherResource, statsResource, opportunitiesResource, intraday]) {
      if (!resource.pending) resource.refresh();
    }
  };

  const alerts = brief?.alerts ?? [];
  const reviewed = (brief?.directions ?? []).filter((d) => d.review);
  const topItems = top?.items ?? [];
  const topRefItems = top?.reference_items ?? [];
  const briefMissing = briefResource.data !== undefined && brief === null && !briefReadFailed;
  const pending = !picksLoaded || (isDiscoverView && !intradayLoaded);

  // ?sec= 滚动定位（展开已在上方渲染期完成，这里只负责滚动）
  useEffect(() => {
    if (!secValid || sec === "candidates" || sec === "daily") return;
    const scroll = () => {
      document.getElementById(`sec-${sec}`)?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" });
    };
    scroll();
    const t = window.setTimeout(scroll, 400);
    return () => window.clearTimeout(t);
  }, [sec, secValid, pending, view]);



  // Existing task links switch complete intraday/daily groups; neither producer loses same-symbol rows.
  // 闭环「标签」：已模拟持仓/已真实持仓（持仓状态派生，60s 轮询）
  // 2026-09-11（S2-5）：原为裸 setInterval，已收编到统一入口（与 workbench 同款重复实现）。
  const positions = useResource(getPositionLabels, {intervalMs: 60_000, enabled: view === "discover"});
  const posLabels = positions.data ?? {};

  const pickItems = data?.items ?? [];
  // Keep the original daily count, including symbols also present in the intraday group.
  const pickTotal = data?.items?.length ?? 0;
  const dailyBySymbol = useMemo(() => new Map(
    !data?.stale && data?.date && data.date === top?.trade_date
      ? data.items.map(item => [item.symbol, item] as const) : []
  ), [data, top]);
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
            {briefReadFailed && brief && <p role="alert" className="hunting-empty">盘前简报读取失败。以下保留上次读取结果，当前状态待重试核实。</p>}
            {briefReadFailed && !brief ? (
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
              {briefResource.pending && reviewed.length === 0 ? (
                <TableSkeleton rows={3} />
              ) : <>
              {briefReadFailed && <p role="alert" className="hunting-empty">盘前简报读取失败，{brief ? "以下保留上次读取的对照，当前状态待重试核实。" : "当前无法核对原判断与复盘结果。请刷新重试。"}</p>}
              {reviewed.length === 0 ? (!briefReadFailed &&
                <div className="ui-card rounded-xl border border-zinc-200 p-4 text-xs text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
                  今日尚未对照。请在系统维护核对盘后调度（收盘后才有意义）。
                </div>
              ) : (
                <FadeIn>
                  <ReviewOutcomeTable reviewed={reviewed} />
                </FadeIn>
              )}</>}
            </section>

            {stats ? (
              <FadeIn>
                <section className="space-y-2">
                  <h3 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">近 30 日胜率统计（跟踪）</h3>
                  {!!statsResource.error && <p role="alert" className="hunting-empty">统计读取失败。以下保留上次读取结果，当前状态待重试核实。</p>}
                  <StatsPanel stats={stats} />
                </section>
              </FadeIn>
            ) : (
              <section className="space-y-2">
                <h3 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">近 30 日胜率统计（跟踪）</h3>
                {statsResource.pending ? <StatsSkeleton /> : <p className="hunting-empty">统计暂不可用。缺少统计不表示胜率为零，等待重试读取。</p>}
              </section>
            )}

            {/* 精选复盘区（?review=1 / 生成复盘后展开；历史组合一致性可回溯） */}
            <section className="space-y-3">
              <h3 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">精选复盘</h3>
              {(reviewDate || reviewVersion) && <p className="break-all text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">原记录复盘：{reviewDate ?? "指定版本"}{reviewVersion ? ` · 版本 ${reviewVersion}` : ""}。角色汇总仍采用当前可信窗口，不能代替此版本结果。</p>}
              {meta && <p className="text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">{meta.note}{meta.scope ? ` · 读取 ${meta.scope.review_rows} 条，可信 ${meta.scope.trusted_rows} 条，排除 ${meta.scope.excluded_rows} 条` : ""}</p>}
              {meta && meta.role_performance && meta.role_performance.length > 0 && (
                <RolePerformanceTable rows={meta.role_performance} />
              )}
              {meta && Object.keys(meta.reason_distribution).length > 0 && (
                <ReasonDistribution dist={meta.reason_distribution} />
              )}
              {reviews.length > 0 && <DailyReviews reviews={reviews} />}
              {reviewsResource.status === "ready" && !reviewsResource.error && (reviewDate || reviewVersion) && reviews.length === 0 && <p className="hunting-empty">该日期或版本尚无复盘记录。原入选依据保留在消息详情中，不以当前结果补填。</p>}
              {history.length > 0 && <HistoryList history={history} />}
              {reviewReadFailures.length > 0 && <p role="alert" className="hunting-empty">{reviewReadFailures.join("、")}读取失败。请刷新重试；以下只展示成功读取的结果，不能据此认定没有记录。</p>}
              {reviewsResource.status === "ready" && historyResource.status === "ready" && reviewReadFailures.length === 0 && reviews.length === 0 && history.length === 0 && (
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
          <Link href="/agent?area=maintenance&tab=operations" onClick={inspectionClick(inspect, {kind: "system-status"})} className="quiet-action">生产状态与维护</Link>
        </div></div>
      </header>

      <HuntingTaskRail view={view} section={sec} search={sp.toString()} />
      <div className="hunting-current-context">
        <details className="hunting-context-help"><summary>使用说明<svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m7 10 5 5 5-5" /></svg></summary><p>{view === "evidence" ? "核对同版依据、等待条件与反证；研究观察不取得交易资格。"
          : view === "tracking" ? "参考跟踪记录与收盘对照；关注、参考价均不是持仓或成交。"
          : view === "research" ? "研究强势形成过程；保留来源、缺项与后续观察，尚未验证的规律不进入交易排序。"
          : view === "review" ? "比较原判断与实际结果；精选、参考轨和模拟执行分别统计。"
          : "盘中候选实时变化；每日精选保留组合日期，两种名单分开核对。"}</p></details>
        <Link className="quiet-action" onClick={inspectionClick(inspect, {kind: "account", scope: "manual"})} href={patchWorkspaceUrl("/workbench", sp.toString(), {mode: "positions", account: "manual", view: null, panel: null, from: `/hunting?${sp.toString()}`})}>实际持仓</Link>
      </div>
      {accessory.value && <ModalShell open={accessory.active} label={accessory.value === "evidence" ? "机会证据台" : "参考跟踪"} size="lg" presentation="drawer" expandable onClose={() => router.replace(patchWorkspaceUrl("/hunting", sp.toString(), {panel: null}), {scroll: false})} header={<div><h2 className="text-lg font-semibold">{accessory.value === "evidence" ? "机会证据台" : "参考跟踪"}</h2></div>} footer="保留当前机会位置；参考价与观察记录不是成交，不构成买卖建议。">
        {accessory.active && (accessory.value === "evidence" ? <OpportunityEvidencePanel date={sp.get("date") ?? undefined} onDateChange={date => router.replace(patchWorkspaceUrl("/hunting", sp.toString(), {date}), {scroll: false})} /> : <WatchLedgerPanel date={sp.get("date") ?? undefined} />)}
      </ModalShell>}
      <PickDetailModal target={detailItem ? {kind: "pick", item: detailItem} : null} readError={!!daily.error} onClose={() => setDailyDetail(null)} />
      <div className="hunting-content min-h-0 flex-1" data-view={view} aria-label="选股内容">
        {view === "validation" ? <SelectionValidationPanel /> : view === "evidence" ? <OpportunityEvidencePanel date={sp.get("date") ?? undefined} onDateChange={date => { const p = new URLSearchParams(sp.toString()); p.set("date", date); router.replace(`/hunting?${p.toString()}`, {scroll:false}); }} /> : view === "tracking" ? <WatchLedgerPanel date={sp.get("date") ?? undefined} /> : view === "research" ? <LeaderResearchPanel date={sp.get("date") ?? undefined} onDateChange={date => router.replace(patchWorkspaceUrl("/hunting", sp.toString(), {date: date ?? null}), {scroll: false})} /> : <>
        {view === "discover" && <>
<div ref={discoveryRef} className="hunting-discovery-grid" onScroll={recordGroupScroll}><div ref={flowRef} className="hunting-candidate-flow" onScroll={recordGroupScroll} tabIndex={0} role="region" aria-label="候选结果与题材">
        <div className="hunting-candidate-notices">
        {/* ── 空仓闸门横幅（风险提示置顶）──
            2026-09-16 动态化：以**读取时刻复核**（meta.gate_live）为准展示，
            生成时刻落库值（meta.gate）作对照。此前只显示落库值 ⇒ 开盘 3 分钟的
            瞬时快照被当全天结论挂着，实测白天出现「横幅喊空仓观望 + 紧邻 chip 显示
            题材进攻」两个相反结论。实时复核不可用时 gate_live 退化为落库值 + 原因说明，
            故这里仍以它为主、gate 作对照即可（不必再判分支）。 */}
        {gateView && (
          <StandAsideBanner
            compact
            gate={gateView}
            stored={data?.meta?.gate}
            generatedAt={data?.meta?.generated_at}
          />
        )}
        {sourceStatus}
        </div>
        <section id="sec-candidates" className="hunting-candidates space-y-3" hidden={selectedGroup !== "candidates"} inert={selectedGroup !== "candidates"}>
          <div className="hunting-section-heading hunting-group-heading">
            <h2 className="hunting-section-title">盘中候选</h2>
            {intradayLoaded && top && <span className="hunting-section-count">{topItems.length} 只</span>}
            <p className="hunting-group-scope" title="名单与参考区均按账户交易权限过滤">权限 {top?.tradable_boards ?? "沪市主板 / 深市主板"}</p>
            <p className="hunting-group-summary">当日动态名单 · 当前未封板，进入参与评估不保证成交</p>
          </div>
          {topReadFailed && top && <p role="alert" className="hunting-empty">盘中候选读取失败。以下保留上次读取的盘中候选，当前参与条件待重新核实。</p>}
          {!intradayLoaded ? <CardListSkeleton count={2} /> : !top ? <div role="alert" className="hunting-empty">盘中候选读取失败。请刷新重试；当前无法判断是否有符合条件的标的。</div>
            : topItems.length > 0 ? <CandidateCollection>{topItems.map(it => <PickCard compact active={selectedGroup === "candidates"} key={JSON.stringify([top.trade_date, top.selection_identity_version, it.symbol])} item={fromIntradayStock(it)}
                readError={!!intraday.error}
                dailyEvidence={dailyBySymbol.has(it.symbol) && data?.date ? {item: dailyBySymbol.get(it.symbol)!, date: data.date, generatedAt: data.meta?.generated_at ?? null, version: data.meta?.selection_version ?? null} : undefined}
                onShowDailyEvidence={showDailyDetail} positionLabel={(posLabels[it.symbol] as "sim" | "real" | undefined) ?? null} />)}</CandidateCollection>
            : <div className="hunting-empty">{topReadFailed ? "上次读取的名单为空，当前状态待重试核实。" : (top.empty_reasons?.join("；") || "当前没有同时满足条件的候选，系统不会凑满名额。")}</div>}
          <button type="button" className="hunting-source-note" onClick={() => router.replace(patchWorkspaceUrl("/hunting", sp.toString(), {view: "validation"}), {scroll: false})}>查看已保存的入选记录 →</button>
          {top?.read_state === "partial" && <p role="status" className="hunting-empty">部分题材数据不完整，当前展示已核实的候选。</p>}
          {top?.criteria && <p className="hunting-source-note">{top.criteria}</p>}
          {topRefItems.length > 0 && <div className="hunting-reference-fold">
            <button type="button" className="hunting-reference-trigger" aria-expanded={referenceOpen} aria-controls={referenceId} onClick={() => setReferenceOpen(value => !value)}>
              <svg aria-hidden width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="m9 5 7 7-7 7" /></svg>
              涨停梯队 {topRefItems.length} 只<span>参考集合，当前状态逐股核对</span>
            </button>
            <div id={referenceId} className="motion-disclosure" data-open={referencePresence.active} aria-hidden={!referencePresence.active} inert={!referencePresence.active}>
              <div className="motion-disclosure-body">
                {referencePresence.value !== null && <>
                  <p className="hunting-source-note">{top?.reference_criteria ?? "保留今日曾封板身份；当前仍封、已开板或未判，以卡片对应时点为准。仅用于观察资金集中方向。"}</p>
                  <CandidateCollection>{topRefItems.map(it => <PickCard compact active={selectedGroup === "candidates" && referencePresence.active} key={JSON.stringify([top?.trade_date, top?.selection_identity_version, it.symbol])} item={fromIntradayStock(it)} readError={!!intraday.error} positionLabel={(posLabels[it.symbol] as "sim" | "real" | undefined) ?? null} />)}</CandidateCollection>
                </>}
              </div>
            </div>
          </div>}
        </section>
        <section id="sec-daily" className="hunting-daily space-y-3" hidden={selectedGroup !== "daily"} inert={selectedGroup !== "daily"}>
          <div className="hunting-section-heading hunting-group-heading"><h2 className="hunting-section-title">每日精选</h2>{picksLoaded && !picksFailed && <span className="hunting-section-count">{pickTotal} 只</span>}<p>{data?.date ?? "组合日期待读取"} · 当日持久候选</p></div>
          {picksFailed && data && <p role="alert" className="hunting-empty">每日精选读取失败。以下保留上次读取的组合与原生成依据，当前执行复核待重新读取。</p>}
          {!picksLoaded ? <CardListSkeleton count={2} /> : !data ? <div role="alert" className="hunting-empty">每日精选读取失败。请刷新重试；读取失败不表示没有组合。</div>
            : pickTotal === 0 ? <div className="hunting-empty">{dailyEmptySummary(data)}</div>
            : <CandidateCollection>{pickItems.map(it => <PickCard compact active={selectedGroup === "daily"} key={JSON.stringify([data.date, data.meta?.selection_version, it.symbol])} item={fromDailyPick(it)} readError={!!daily.error} positionLabel={(posLabels[it.symbol] as "sim" | "real" | undefined) ?? null} />)}</CandidateCollection>}
          <p className="hunting-source-note">{minPickScore != null ? `综合分≥${minPickScore} · ` : ""}{replaceThreshold != null ? `换股门槛 ${replaceThreshold} 分` : "换股门槛未返回"} · {maxSwaps != null ? `每日换股上限 ${maxSwaps} 只` : "换股上限未返回"}；够格几只就保留几只。</p>
        </section>

        {/* ── 异动手风琴（题材 → 个股瀑布流）── */}
        <div id="sec-opportunity" className="scroll-mt-2">
          {opps ? (
            <FadeIn>
              {!!opportunitiesResource.error && <p role="alert" className="hunting-empty">题材机会读取失败，保留上次题材结果；当前参与条件待重新核实。</p>}
              <OpportunitySection opps={opps} expanded={expandedTheme} onToggle={toggleTheme} showLedger={false} readError={!!opportunitiesResource.error} />
            </FadeIn>
          ) : (
            opportunitiesResource.pending ? <CardListSkeleton count={3} /> : <p role="alert" className="hunting-empty">题材参与数据读取失败，请刷新重试。无法据此判断没有题材机会。</p>
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
        {" · "}全部输出为研究依据与入选观察，不构成买卖建议。数据可能延迟；入选与实际成交分别核验。
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
