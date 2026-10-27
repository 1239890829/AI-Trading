"use client";

import {ConditionList, NarrativeText, FactCards, FactTimeline} from "@/components/ui/explanation-map";
import { useId, useState, type KeyboardEvent, type ReactNode } from "react";

import { CardHead, CardShell } from "@/components/picks/card-shell";
import { CardEntryRow } from "@/components/picks/card-entries";
import { LimitReason } from "@/components/detail/limit-reason";
import {CurrentThemeReference, SourceThemeIdentity} from "@/components/source-theme-identity";
import type {CurrentThemeCatalogReference} from "@/types/market";
import {StockAttributionPanel} from "@/components/detail/attribution-evidence";
import {PaperSelectionAction} from "@/components/picks/paper-selection-action";
import {TradingAgentsResearchPanel} from "@/components/detail/tradingagents-research-panel";
import {SELECTION_SCORE_LABELS, SelectionMeasurements, SelectionScores, readSelectionMetrics, type SelectionScore} from "@/components/detail/selection-decision-explorer";
import { StockLink, useStockRowNav } from "@/components/stock-link";
import { bjDate, dateTimeTextBJ, fmt, formatLegacyLimitDistance, pctColor, pctText, sourceLabel, timeTextBJ, triText } from "@/lib/format";
import { clockOf, compareGates, gateActive, type GateComparison } from "@/lib/picks-gate";
import { roleClass } from "@/lib/role-style";
import {ReaderBrief, ResearchDisclosure} from "@/components/ui/reader-brief";
import { ModalShell } from "@/components/ui/modal-shell";
import { useExitPresence } from "@/hooks/use-exit-presence";
import {readerExcerpt, readerCautions} from "@/lib/reader-copy";
import compactStyles from "./pick-card-compact.module.css";
import type {
  DailyPickItem,
  ExecutionDecisionContract,
  ExitDiscipline,
  IntradayTopStock,
  IntradayExecutionQualification,
  SelectionMetrics,
  OpportunityStock,
  StandAsideGate,
  StopLossRef,
} from "@/lib/api";

/**
 * 选股类卡片的**唯一**组件（2026-09-10 两卡合并，用户拍板「合并为一张卡、字段互补」）。
 *
 * 合并前是两张平行卡：PickCard（每日精选 · 盘前收盘名单）与 WatchCard（盘中跟踪 ·
 * 当日实时名单）。两者的字段是**互补**关系而非重复关系 —— 盘前有六维评分/估值/
 * 买入区间/失效条件，盘中有辨识度·确定性判定/涨停原因/T 档/题材强度档。分两张卡
 * 导致同一件事被讲两遍、且谁都不完整。
 *
 * 现在：**一个组件 + 两个适配器**。
 * - `fromDailyPick(DailyPickItem)`      → 盘前名单
 * - `fromIntradayStock(IntradayTopStock | OpportunityStock)` → 盘中名单
 * 适配器把所有差异（含下列 4 组异名同义字段）**单点归一**成 `TradingCard`，
 * 卡片本体只认一种形状，分节一律「有则渲染，无则整节不出现」。
 *
 * ⚠️ 4 组异名同义字段（同一件事，两卡各写一个名字）——归一点在适配器里，**只有一份**：
 *   role↔echelon_role · stage↔theme_stage · stop_ref↔stop_loss · exit_plan↔exit_discipline
 * 下游若再写 `item.role ?? item.echelon_role`，就是把归一逻辑又散回渲染层。
 *
 * ⚠️ 首次盘中排名不补每日六维字段；后续完整评估保留独立版本和时点，另列复核。
 * 不适用维度不伪造占位；已适用但缺项的后续评估明确缺项（不适用 ≠ 缺失）。
 *
 * 红线 3：全部为可解释依据与条件陈述，不构成买卖建议。
 */

export const SUB_LABELS: [string, string][] = SELECTION_SCORE_LABELS.map(([key, label]) => [key, label]);

/** 梯队地位配色：唯一权威表在 `@/lib/role-style`（S2-10 合并，跨端由后端测试守卫）。 */

export const TIER_STYLE: Record<string, string> = {
  龙头博弈: "border-zinc-200 dark:border-zinc-800 text-red-700 dark:text-red-300",
  趋势跟随: "border-sky-500/50 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  情绪低位: "border-amber-500/50 bg-amber-500/10 text-amber-800 dark:text-amber-300",
};

/* ---------------------------------------------------------------- 统一模型 */

/** 三态判定（辨识度/确定性）：unknown = 判不出（数据缺失），不是「低」。 */
export interface CardJudgement {
  level: string;
  basis: string;
}

/**
 * 出场纪律（归一）。
 *
 * 盘中路径后端给的是未定型 dict（`exit_plan`），其中 `trailing_pct` 可能缺失。
 * 用 `null` 表达「没有这一项」，**不用 0** —— 渲染出「回落 0%」是臆造出来的数字。
 */
export interface CardExit {
  trailing_pct: number | null;
  roi_ladder: { gain_pct: number; action: string }[];
  note: string;
  disclaimer: string;
}

/** 卡片唯一输入契约：两路数据源在适配器里归一到此。 */
export interface TradingCard {
  symbol: string;
  name: string | null;
  /** 来源（决定头部徽标与卡底口径注记） */
  origin: "picks" | "intraday";
  price: number | null;
  change_pct: number | null;
  /** 盘前生成/首见参考价；仅用于复盘基准，绝不冒充成交。 */
  referencePrice: number | null;
  selectionEntry?: DailyPickItem["selection_entry"];
  researchSummary?: DailyPickItem["research_summary"];
  paperContext?: {ref: string; version: string} | null;
  /** Later persisted assessment; never the original selection score or rationale. */
  executionQualification?: IntradayExecutionQualification | null;
  qualificationIdentityValid?: boolean;
  /** 当前展示身份，保留既有首次入选记录；仅参考不等于从未入选。 */
  referenceOnly?: boolean;
  /** 最新动作时执行复核；null = 尚未产生盘中买点判定。 */
  execution: ExecutionDecisionContract | null;
  /* --- 估值与评分：目前仅盘前名单提供 --- */
  pe_ttm: number | null;
  pb: number | null;
  score: number | null;
  sub_scores: Record<string, number> | null;
  selectionMetrics?: SelectionMetrics | null;
  dimensionEvidence?: DailyPickItem["dimension_evidence"];
  themeSources?: IntradayTopStock["theme_sources"];
  confidence: DailyPickItem["confidence"];
  /* --- 标的画像（两源归一） --- */
  role: string | null;
  roleBasis: string | null;
  boards: number | null;
  theme: string | null;
  currentThemeCatalog?: CurrentThemeCatalogReference;
  stage: string | null;
  riskTier: string | null;
  strengthTier: string | null;
  /** 盘中跟踪档 T1~T3（仅 intraday-top 分层名单有） */
  tier: number | null;
  /* --- 判定：目前仅盘中名单提供 --- */
  distinctiveness: CardJudgement | null;
  certainty: CardJudgement | null;
  /**
   * 联动确定性（2026-09-15 新口径）：候选是**当前未封板**、通过联动门槛的题材个股，
   * 没有封单可看，延续预期只能来自「题材基座 × 距封板跑道」。
   */
  linkage: CardJudgement | null;
  /**
   * 可参与性（2026-09-15 猎场口径）：加入猎场的个股必须是投资者**实际可以参与的**。
   * 「不可参与」= 已封板 / 开盘即涨停 —— 这类卡片是**参考信息**，不是候选。
   */
  tradability: CardJudgement | null;
  /**
   * 板块（沪市主板/深市主板/创业板/…）。透出理由是**账户权限**（2026-09-15 起
   * 只开沪深主板）：让"这只票属于哪个板、我有没有权限买"在卡片上直接可见，
   * 而不是要用户记住代码段规则。
   */
  board: string | null;
  /* --- 依据行（归一：盘前是六维 basis 摘要，盘中是「入选 + 涨停原因」） --- */
  basisRows: { label: string; value: string; source?: string | null; date?: string | null }[];
  vetoes: string[];
  /* --- 立场与买入区间：仅盘前名单有（收盘评分产出，盘中不适用） --- */
  buyRange: { low: number; high: number; basis: string } | null;
  followState: "blocked" | "observe" | "followable" | null;
  followReasons: string[];
  observationOnly: boolean;
  /* --- 出场纪律 --- */
  stopLoss: StopLossRef | null;
  exit: CardExit | null;
  invalidations: string[];
  /* --- 其他 --- */
  chipSignal: DailyPickItem["chip_signal"];
  relatedEvents: string[];
  relatedEventRefs?: DailyPickItem["related_event_refs"];
  /** 卡底口径注记：解释「本卡为什么缺某几节」。null = 不需要解释。 */
  caliberNote: string | null;
}

/** 盘中路径的 `exit_plan` 是未定型 dict：取值后仍可能一项都没有 → 返回 null（整节不渲染）。 */
function toCardExit(raw: unknown): CardExit | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Partial<ExitDiscipline>;
  const ladder = Array.isArray(o.roi_ladder) ? o.roi_ladder : [];
  const trailing = typeof o.trailing_pct === "number" ? o.trailing_pct : null;
  if (trailing == null && ladder.length === 0 && !o.note) return null;
  return { trailing_pct: trailing, roi_ladder: ladder, note: o.note ?? "", disclaimer: o.disclaimer ?? "" };
}

/** 判定归一：level 为空串/缺失 → null（整行不渲染），不走「未判定」占位。 */
function toJudgement(raw: { level: string; basis?: string } | null | undefined): CardJudgement | null {
  if (!raw || !raw.level) return null;
  return { level: raw.level, basis: raw.basis ?? "" };
}

/** A current daily identity may supersede its immutable first-entry reference. */
function selectedPaperContext(it: {symbol: string; selection_entry?: DailyPickItem["selection_entry"]}, scope: "daily" | "intraday", current?: {date: string; version: string | null}) {
  if (!/^\d{6}$/.test(it.symbol)) return null;
  if (scope === "daily" && current && /^\d{4}-\d{2}-\d{2}$/.test(current.date) && current.version) return {ref: `daily:${current.date}:${it.symbol}`, version: current.version};
  const entry = it.selection_entry;
  if (!entry || entry.source !== (scope === "daily" ? "daily_generation" : "intraday_rank_archive") || !entry.source_version || !entry.selected_at || !/(?:Z|[+-]\d{2}:\d{2})$/.test(entry.selected_at)) return null;
  const day = bjDate(entry.selected_at);
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? {ref: `${scope}:${day}:${it.symbol}`, version: entry.source_version} : null;
}

function qualificationIdentity(it: IntradayTopStock | OpportunityStock) {
  const original = selectedPaperContext(it, "intraday");
  const q = it.execution_qualification;
  if (!q || typeof q !== "object" || Array.isArray(q) || !original) return false;
  const reference = q.reference_entry;
  return q.selection_ref === original.ref && q.source_version === original.version
    && (!reference || (!reference.source_version || reference.source_version === original.version)
      && reference.source === "intraday_rank_archive" && reference.selected_at === it.selection_entry?.selected_at)
    && (!q.item?.symbol || q.item.symbol === it.symbol);
}

function assessmentScore(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
}

function assessmentClock(value: unknown): string | null {
  return typeof value === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)) ? value : null;
}

function qualificationLabel(item: TradingCard, readError: boolean) {
  if (!item.qualificationIdentityValid) return "复核来源待核";
  if (readError) return "上次复核，当前待核";
  const state = item.executionQualification?.state;
  if (state === "ready") return assessmentScore(item.executionQualification?.item?.score) === null ? "后续评分待核" : "评分已齐";
  if (["pending", "queued", "running", "evaluating"].includes(state || "")) return "后续复核处理中";
  if (["blocked", "rejected"].includes(state || "")) return "后续复核受限";
  return "后续复核待核";
}

function QualificationSummary({item, readError}: {item: TradingCard; readError: boolean}) {
  const q = item.executionQualification;
  if (!q || item.referenceOnly) return null;
  const valid = item.qualificationIdentityValid;
  const score = valid ? assessmentScore(q.item?.score) : null;
  const at = valid ? assessmentClock(q.assessment?.assessed_at) : null;
  const decision = valid && !readError ? q.decision : null;
  const decisionLabel = decision?.gate_decision === "rejected" ? "执行判定未通过"
    : decision?.gate_decision === "passed" && decision.archived_decision === "eligible"
      ? "执行判定已记录，仍需当前预检" : "未取得当前执行判定";
  return <div className={compactStyles.reference} role="status" aria-label="后续执行复核概览">
    <strong>{qualificationLabel(item, readError)}</strong>
    {valid && <><span>{score === null ? "评分未提供" : `后续评分 ${fmt(score, 1)}`}</span>
      <span>{at ? `评估 ${timeTextBJ(at)}` : "评估时点未提供"}</span><span>{decisionLabel}</span></>}
  </div>;
}

function QualificationDetails({item, readError}: {item: TradingCard; readError: boolean}) {
  const q = item.executionQualification;
  if (!q || item.referenceOnly) return null;
  const valid = item.qualificationIdentityValid;
  const assessed = valid ? q.item : null;
  const evidence = assessed?.dimension_evidence;
  const scores = assessed?.sub_scores ? SUB_LABELS.map(([key, label]) => ({key,
    label: label === "基本" ? "基本面" : label, maximum: 100 as const,
    value: assessmentScore(assessed.sub_scores?.[key]), state: evidence?.[key]?.state || "unknown",
    basis: evidence?.[key]?.reason || assessed.bases?.[key] || "本次复核未保存此项依据。"})) : [];
  const trace = assessed?.scoring_trace;
  const traceFields = [
    {label: "加权合成", value: trace?.weighted_total}, {label: "否决后", value: trace?.post_veto_score},
    {label: "风险扣分", value: trace?.risk_penalty}, {label: "后续总分", value: trace?.final_score},
  ].filter((row): row is {label: string; value: number} => typeof row.value === "number" && Number.isFinite(row.value));
  const at = valid ? assessmentClock(q.assessment?.assessed_at) : null;
  const gaps = scores.filter(score => !["success", "valid_empty"].includes(score.state));
  const decision = valid && !readError ? q.decision : null;
  const blockers = decision?.gate_checks?.filter(check => !["pass", "passed", "success"].includes(check.state)) ?? [];
  return <section aria-label="后续执行复核" className="my-2 space-y-2">
    <ReaderBrief label="后续复核状态" headline={qualificationLabel(item, readError)} facts={[
      {label: "评估时点", value: at ? dateTimeTextBJ(at, {seconds:true}) : "未提供可信评估时点"},
      {label: "与首次的关系", value: "后续评估不改写首次入选依据、当时测量或原排名。"},
    ]}/>
    {!valid ? <p role="status" className="text-xs text-amber-700 dark:text-amber-300">原证券、日期或来源版本不一致，未展示其他记录的复核评分。</p> : <>
      {q.reason && <p className="text-xs text-zinc-600 dark:text-zinc-400">{readerExcerpt(q.reason, 140)}</p>}
      <p className="text-xs text-zinc-600 dark:text-zinc-400">评分齐全不等于可执行；最新执行判定和当前模拟预检分别核对。</p>
      {decision?.gate_reason && <p className="text-xs text-zinc-600 dark:text-zinc-400">执行判定：{readerExcerpt(decision.gate_reason, 140)}</p>}
      {!!blockers.length && <ConditionList label="已记录执行阻断" tone="warning" items={blockers.map(check => `${check.name}：${check.reason}`)}/>}
      {scores.length ? <SelectionScores key={`${q.selection_ref}:${q.source_version}:${q.assessment?.snapshot_id}:${q.assessment?.version}`}
        scores={scores} compact label="后续六维分解" title="后续六维分解" note="本次评估值，不回填首次分值，不是上涨概率"/>
        : <p className="text-xs text-zinc-600 dark:text-zinc-400">后续六维评分未提供；没有按0分补齐。</p>}
      {!!gaps.length && <ConditionList label="后续依据缺项" tone="warning" items={gaps.map(score => `${score.label}：${score.basis}`)}/>}
      {traceFields.length > 0 && <ResearchDisclosure label="复核计算核对" identity={`${q.assessment?.snapshot_id}:${q.assessment?.version}`}>
        <FactCards label="本次评分合成" items={traceFields.map(row => ({label: row.label, value: `${fmt(row.value, 2)}分`}))}/>
        <FactCards label="本次六维权重与贡献" items={SUB_LABELS.flatMap(([key, label]) => {
          const dimension = trace?.dimensions?.[key];
          return dimension && typeof dimension.weight === "number" && Number.isFinite(dimension.weight)
            && typeof dimension.contribution === "number" && Number.isFinite(dimension.contribution)
            ? [{label, value: `权重 ${fmt(dimension.weight * 100, 2)}% · 合成贡献 ${fmt(dimension.contribution, 2)}分`}] : [];
        })}/>
      </ResearchDisclosure>}
      <p className="break-all text-[10px] text-zinc-600 dark:text-zinc-400">评估版本 {q.assessment?.version || "未提供"} · 原选择版本 {q.source_version || "未提供"}</p>
    </>}
  </section>;
}

function ResearchState({item}: {item: TradingCard}) {
  const state = item.researchSummary;
  if (!state || state.symbol !== item.symbol || item.referenceOnly) return null;
  const labels = {completed: "研究已保存", running: "完整研究进行中", queued: "研究排队中", failed: "本轮研究失败", canceled: "本轮研究已取消", none: "尚无完整研究", unknown: "研究状态待核"};
  const label = labels[state.state] || "研究状态待核";
  return <span title={`${state.generated_at ? `生成于 ${dateTimeTextBJ(state.generated_at)}；` : ""}角色与报告数量仅代表覆盖，不证明结论正确。`}>{label}{state.has_saved && state.roles_count === 12 && state.report_count === 7 ? " · 12角色" : ""}</span>;
}

/** 盘前名单（DailyPickItem）→ 统一模型。 */
export function fromDailyPick(it: DailyPickItem, current?: {date: string; version: string | null; currentThemeCatalog?: CurrentThemeCatalogReference}): TradingCard {
  const basisRows: { label: string; value: string }[] = [];
  const execution = it.execution ?? null;
  const executable = execution?.executable_snapshot;
  const executionReady = executable?.state === "ready";
  for (const [key, label] of SUB_LABELS) {
    const v = it.bases?.[key];
    if (v) basisRows.push({ label, value: v });
  }
  // 筹码是附注维度（不进六维权重），排在六维之后
  if (it.bases?.["chip"]) basisRows.push({ label: "筹码", value: it.bases["chip"] });
  // 「来源」= 入选来源依据（2026-09-15）：题材联动股写明"从哪个题材挖出来、为什么"。
  // 放**首位**——它回答的是"为什么进组合"，比六维分解更靠前。
  if (it.source_basis) basisRows.unshift({ label: "来源", value: it.source_basis });

  return {
    symbol: it.symbol,
    name: it.name,
    origin: "picks",
    // 非 ready 的动作快照只能作为显式降级证据，不能顶掉卡片主价格冒充当前可执行价。
    price: executionReady ? (executable?.price ?? it.price ?? null) : (it.price ?? null),
    change_pct: executionReady ? (executable?.change_pct ?? it.change_pct ?? null) : (it.change_pct ?? null),
    referencePrice: execution?.reference_entry.price ?? it.price ?? null,
    selectionEntry: it.selection_entry,
    researchSummary: it.research_summary,
    paperContext: selectedPaperContext(it, "daily", current),
    referenceOnly: false,
    execution,
    pe_ttm: it.pe_ttm ?? null,
    pb: it.pb ?? null,
    score: it.score ?? null,
    sub_scores: it.sub_scores ?? null,
    dimensionEvidence: it.dimension_evidence,
    confidence: it.confidence ?? null,
    role: it.echelon_role ?? null,
    roleBasis: it.echelon_basis ?? null,
    boards: it.boards ?? null,
    theme: it.theme ?? null,
    currentThemeCatalog: current?.currentThemeCatalog,
    stage: it.theme_stage ?? null,
    riskTier: it.risk_tier ?? null,
    strengthTier: null,
    tier: null,
    distinctiveness: null,
    certainty: null,
    linkage: null,
    tradability: toJudgement(it.tradability),
    // 盘前名单的卡片暂无板块字段（后端 picks 侧未透出）⇒ null，不臆造
    board: null,
    basisRows,
    vetoes: it.vetoes ?? [],
    buyRange: it.buy_range ?? null,
    followState: it.follow_state ?? null,
    followReasons: it.follow_reasons ?? [],
    observationOnly: it.observation_only ?? false,
    stopLoss: it.stop_loss ?? null,
    exit: toCardExit(it.exit_discipline),
    invalidations: it.invalidations ?? [],
    chipSignal: it.chip_signal ?? null,
    relatedEvents: it.related_events ?? [],
    relatedEventRefs: it.related_event_refs,
    // 盘前名单是收盘产出的完整口径（评分/估值/买入区间/失效条件都在），无需额外解释
    caliberNote: null,
  };
}

/**
 * 盘中名单（intraday-top 分层名单 / 题材手风琴候选）→ 统一模型。
 *
 * 两者字段天然互认（除分层名单独有的 tier / pick_basis，与手风琴独有的无 stage 之外）。
 */
export function fromIntradayStock(it: IntradayTopStock | OpportunityStock, currentReference?: CurrentThemeCatalogReference): TradingCard {
  const basisRows: TradingCard["basisRows"] = [];
  // 「入选」= pick_basis——只有分层名单才产生（要解释「为什么在这档」）
  const pickBasis = "pick_basis" in it ? it.pick_basis : null;
  if (pickBasis) basisRows.push({ label: "入选", value: formatLegacyLimitDistance(pickBasis) });
  // 「联动」= participants 的 basis（从哪个题材挖出来、距封板还有多少跑道）。
  // 与 pick_basis 分开两行：前者是"为什么进猎场"，后者是"为什么排在这一档"。
  if ("basis" in it && it.basis) basisRows.push({ label: "联动", value: formatLegacyLimitDistance(it.basis) });
  // 「涨停原因」= reason——同花顺官方原串（ladder 行自带），两条路径都有
  if (it.reason?.trim() || (it.boards ?? 0) > 0) basisRows.push({ label: "涨停原因", value: it.reason ?? "", source: it.reason_source, date: it.reason_date });
  // 「首封」= 可参与性判据的直接证据（开盘即涨停 vs 盘中封板）
  if (it.first_seal_time) basisRows.push({ label: "首封", value: it.first_seal_time });

  return {
    symbol: it.symbol,
    name: it.name,
    origin: "intraday",
    price: it.price ?? null,
    change_pct: it.change_pct ?? null,
    referencePrice: null,
    selectionEntry: it.selection_entry,
    researchSummary: it.research_summary,
    paperContext: it.reference_only ? null : it.execution_qualification
      ? qualificationIdentity(it) ? {ref: it.execution_qualification.selection_ref!, version: it.execution_qualification.source_version!} : null
      : selectedPaperContext(it, "intraday"),
    executionQualification: it.reference_only ? null : it.execution_qualification,
    qualificationIdentityValid: !it.reference_only && qualificationIdentity(it),
    referenceOnly: it.reference_only === true,
    execution: null,
    pe_ttm: null,
    pb: null,
    score: null,
    sub_scores: null,
    selectionMetrics: readSelectionMetrics(it.selection_metrics),
    themeSources: "theme_sources" in it ? it.theme_sources : undefined,
    confidence: null,
    role: it.role ?? null,
    roleBasis: null,
    boards: it.boards ?? null,
    theme: "theme" in it ? (it.theme ?? null) : null,
    currentThemeCatalog: currentReference,
    stage: "stage" in it ? (it.stage ?? null) : null,
    riskTier: null,
    strengthTier: "strength_tier" in it ? (it.strength_tier ?? null) : null,
    tier: "tier" in it ? (it.tier ?? null) : null,
    distinctiveness: toJudgement(it.distinctiveness),
    certainty: toJudgement(it.certainty),
    linkage: "linkage" in it && it.linkage
      ? toJudgement({ ...it.linkage, basis: formatLegacyLimitDistance(it.linkage.basis ?? "") })
      : null,
    tradability: toJudgement(it.tradability),
    board: it.board ?? null,
    basisRows,
    vetoes: [],
    buyRange: null,
    followState: null,
    followReasons: [],
    observationOnly: false,
    stopLoss: it.stop_ref ?? null,
    exit: toCardExit(it.exit_plan),
    invalidations: [],
    chipSignal: null,
    relatedEvents: [],
    relatedEventRefs: it.related_event_refs,
    // 盘中来源不补每日组合字段；原生成时点信息由每日来源提供。
    caliberNote: it.execution_qualification
      ? "盘中首次依据不改写；后续评分与执行判定分开核对。参考信息不代表成交，不构成买卖建议。"
      : "盘中实时口径 · 随盘面重算：含联动确定性判定与出场纪律；" +
        "评分、估值与参考区间由每日组合原生成时点提供，不以盘中数据补填。参考信息不代表成交；判定为条件陈述，不构成买卖建议。",
  };
}

/* ---------------------------------------------------------------- 渲染 */

/** Explain saved selection evidence; a vendor's limit-up note is never the selection reason. */
export function selectionReading(item: TradingCard) {
  const reason = item.basisRows.find(row => ["入选", "来源", "联动"].includes(row.label))?.value;
  const dimensions = SUB_LABELS.map(([key, label]) => ({key, label: label === "基本" ? "基本面" : label,
    score: item.sub_scores?.[key], row: item.basisRows.find(row => row.label === label)}))
    .filter(value => typeof value.score === "number" && Number.isFinite(value.score)
      && value.row && (!item.dimensionEvidence?.[value.key] || item.dimensionEvidence[value.key].state === "success"))
    .sort((a, b) => b.score! - a.score!).slice(0, 2);
  const plainBasis = (value: string) => readerExcerpt(value.replace(/技术面偏多/g, "走势指标多数偏强")
    .replace(/技术面中性/g, "走势尚无明确倾向").replace(/共振向上/g, "多项走势指标同时偏强")
    .replace(/多空分歧/g, "走势信号不一致").replace(/\bPE\b/g, "市盈率").replace(/\bROE\b/g, "净资产收益率"), 90);
  const why = reason ? readerExcerpt(reason, 120)
    : item.origin === "picks" && dimensions.length
    ? `这次综合筛选中，${dimensions[0].label}项的得分相对靠前：${plainBasis(dimensions[0].row!.value)}`
    : item.referenceOnly ? "这是强势参考项，不是可参与候选；原记录未保存完整筛选理由。"
    : "原记录没有保存系统筛选理由，不能把来源的涨停说明当作入选理由。";
  const supports = dimensions.length ? dimensions.map(d => ({label: `${d.label}依据`, value: plainBasis(d.row!.value)}))
    : [item.linkage && {label: "题材支持", value: readerExcerpt(item.linkage.basis, 100)},
      item.distinctiveness && {label: "个股表现", value: readerExcerpt(item.distinctiveness.basis, 100)},
      item.certainty && {label: "判断依据", value: readerExcerpt(item.certainty.basis, 100)}]
      .filter((value): value is {label: string; value: string} => !!value?.value).slice(0, 2);
  const limits = [...item.vetoes, ...item.followReasons, ...item.invalidations];
  return {why, supports, limit: readerExcerpt(limits[0], 100) || (item.referenceOnly ? "先确认是否具备参与条件。" : "按原记录的失效条件继续观察，不代表后续一定上涨。")};
}

/** 三态判定徽标：title 挂完整判定依据（可追溯）。 */
export function JudgeChip({ label, level, basis }: { label: string; level: string; basis: string }) {
  const tone =
    level === "高"
      ? "bg-violet-500/10 text-violet-700 dark:text-violet-300"
      : level === "中"
        ? "bg-sky-500/10 text-sky-700 dark:text-sky-300"
        : level === "低"
          ? "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400"
          : "bg-amber-500/10 text-amber-800 dark:text-amber-300"; // unknown：判不出 ≠ 低
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] ${tone}`} title={`${label}判定依据：${basis || "—"}`}>
      {/* triText：level="unknown"（判不出）→「未判定」，不把内部字面量打上界面 */}
      {label}·{triText(level)}
    </span>
  );
}

function Chip({ text, title, className }: { text: string; title?: string; className?: string }) {
  return (
    <span
      title={title}
      className={`rounded border px-1.5 py-0.5 text-[10px] ${className ?? "border-zinc-300 text-zinc-600 dark:text-zinc-400 dark:border-zinc-700"}`}
    >
      {text}
    </span>
  );
}

/** 「标签: 值」行——依据行与出场纪律行共用，避免两处分节各写一套排版。 */
function Row({ k, v, title }: { k: string; v: ReactNode; title?: string }) {
  return (
    <div className="flex gap-1.5" title={title}>
      <span className="shrink-0 text-zinc-600 dark:text-zinc-400">{k}</span>
      <span className="text-zinc-600 dark:text-zinc-300">{v}</span>
    </div>
  );
}

/** Local original-value inspection only. Opening a field does not read company research. */
function CompactPickSummary({item, scores, reading, positionLabel, dailyEvidence, readError, followText, followTitle, executionState, executionText}: {
  item: TradingCard; scores: SelectionScore[] | undefined; reading: ReturnType<typeof selectionReading>;
  positionLabel: "sim" | "real" | null; dailyEvidence?: {item: DailyPickItem}; readError: boolean;
  followText: string; followTitle: string; executionState: string | null; executionText: string | null;
}) {
  const [selected, setSelected] = useState("reason");
  const inspectionId = useId();
  const fields = item.referenceOnly ? [] : item.selectionMetrics?.items.length ? item.selectionMetrics.items.map(metric => ({
    key: metric.key, label: metric.label, unit: metric.unit, value: metric.value, state: metric.state,
    measured: metric.state === "ready" && metric.value !== null, basis: metric.basis,
    metric,
  })) : (scores ?? []).map(score => ({key: score.key, label: score.label, unit: "分", value: score.value, state: score.state,
    measured: score.value !== null && ["success", "valid_empty", "partial"].includes(score.state), basis: score.basis, metric: null}));
  const scoreLayout = item.origin === "picks" && !fields.some(value => value.metric);
  const field = fields.find(value => value.key === selected);
  const keyboardEntry = field?.key ?? fields[0]?.key;
  function inspectField(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.defaultPrevented || event.nativeEvent.isComposing || event.keyCode === 229
      || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey
      || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    // Deliberately linear even when the responsive grid changes its column count.
    const next = event.key === "Home" ? 0 : event.key === "End" ? fields.length - 1
      : (index + (["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 1) + fields.length) % fields.length;
    setSelected(fields[next].key);
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("[data-compact-field]")[next]?.focus({preventScroll:true});
  }
  const originalReason = item.basisRows.find(row => ["入选", "来源", "联动"].includes(row.label))?.value || reading.why;
  const basis = field?.basis || (field ? "原记录未保存本项依据。" : originalReason);
  const fieldValue = (value: typeof fields[number]) => !value.measured ? ["missing", "error"].includes(value.state) ? "缺项" : "待核"
    : value.unit === "元" && Math.abs(value.value!) >= 100000000 ? `${(value.value! / 100000000).toFixed(2)}亿`
      : value.unit === "元" && Math.abs(value.value!) >= 10000 ? `${(value.value! / 10000).toFixed(0)}万`
        : `${new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 2}).format(value.value!)}${value.unit}`;
  const qualification = readError ? "参与条件待重新核实" : item.tradability?.level === "可参与" ? "可参与评估 · 当前未封板"
    : item.tradability?.level === "不可参与" ? "当前不可参与" : "可参与性未判定";
  const hasMissing = item.dimensionEvidence && Object.values(item.dimensionEvidence).some(value => ["missing", "error", "partial"].includes(value.state));
  return <div className={compactStyles.summary}>
    <header className={compactStyles.header}>
      <div className={compactStyles.identity}><StockLink symbol={item.symbol}><strong>{item.name || item.symbol}</strong><span>{item.symbol}</span></StockLink></div>
      <div className={compactStyles.quote}><strong title={readError ? "上次读取的报价，当前行情未核实" : item.origin === "picks" ? executionState === "ready" ? "原执行复核快照价，不是成交价" : "组合生成/首见参考价，不是成交价" : "名单快照价，来源时点见原依据"}>{fmt(item.price)}</strong><span className={pctColor(item.change_pct)}>{pctText(item.change_pct)}</span></div>
    </header>
    <div className={compactStyles.meta}>
      <span>{item.origin === "picks" ? "每日精选" : "盘中跟踪"}{item.tier != null ? ` · T${item.tier}` : ""}</span>
      {item.referenceOnly && <strong>当前仅参考</strong>}
      <span title={item.tradability?.basis}>{qualification}</span>
      <ResearchState item={item}/>
      {positionLabel && <span>{positionLabel === "real" ? "有手工持仓记录" : "有模拟持仓记录"}</span>}
      {item.observationOnly && <strong title={followTitle}>{followText}</strong>}
    </div>
    <SelectionEntryRow compact entry={item.selectionEntry} scope={item.origin === "picks" ? "daily" : "intraday"} referenceOnly={item.referenceOnly}/>
    <QualificationSummary item={item} readError={readError}/>
    {item.origin === "intraday" && dailyEvidence?.item.symbol === item.symbol && <SelectionEntryRow compact entry={dailyEvidence.item.selection_entry} scope="daily"/>}
    {(item.theme || item.role || item.board || item.riskTier || item.linkage) && <p className={compactStyles.theme}>
      {item.theme && <span>{item.theme}{item.stage ? ` · ${item.stage}` : ""} <SourceThemeIdentity reason={item.theme} historical/><CurrentThemeReference reason={item.theme} reference={item.currentThemeCatalog}/></span>}
      {item.role && <span title={item.roleBasis || undefined}>{item.role}{item.boards ? ` · ${item.boards}板` : ""}</span>}
      {item.board && <span>{item.board}</span>}{item.riskTier && <span title={item.exit?.note}>{item.riskTier}</span>}
      {item.linkage && <span title={item.linkage.basis}>题材关联 {triText(item.linkage.level)}</span>}
    </p>}
    {!!fields.length && <div className={compactStyles.fields} data-count={fields.length} data-layout={scoreLayout ? "scores" : "metrics"} role="group" aria-label={item.origin === "picks" ? "本卡原规则分项" : "本卡原测量"} aria-describedby={`${inspectionId}-keys`}>
      <span id={`${inspectionId}-keys`} className="sr-only">方向键按显示顺序切换：左、上为前一项，右、下为后一项；Home、End 到首尾。细线仅表示每日原规则分值，不是上涨概率。</span>
      {fields.map((value, index) => <button type="button" key={value.key} className={compactStyles.field} data-compact-field
      tabIndex={keyboardEntry === value.key ? 0 : -1} aria-controls={`${inspectionId}-basis`} onKeyDown={event => inspectField(event, index)}
      aria-pressed={field?.key === value.key} data-state={value.state} aria-label={`核对${value.label}，${fieldValue(value)}`} onClick={() => setSelected(value.key)}>
      <span className={compactStyles.fieldLabel}>{value.label}</span><strong className={compactStyles.fieldValue}>{fieldValue(value)}</strong>
      {value.state === "partial" && <small>部分</small>}{value.state === "valid_empty" && <small>有效空集</small>}
      {item.origin === "picks" && !value.metric && value.measured && typeof value.value === "number" && Number.isFinite(value.value) && value.value >= 0 && value.value <= 100
        && <span className={compactStyles.ruleScale} role="meter" aria-label={`${value.label}原规则分值`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value.value}><span style={{width:`${value.value}%`}}/></span>}
    </button>)}</div>}
    <section id={`${inspectionId}-basis`} className={compactStyles.basis} aria-label="本卡所选短依据">
      <div className={compactStyles.basisHeading}><span>{field ? `${field.label} · 依据节选` : `为什么入选${basis.length > 68 ? " · 节选" : ""}`}</span>{field && <button type="button" onClick={() => setSelected("reason")}>回看入选理由</button>}</div>
      <p>{readerExcerpt(basis, 68)}</p>
      {field && <details key={field.key} className={compactStyles.fullBasis} onClick={event => event.stopPropagation()}><summary>核对本项完整依据</summary><p>{basis}</p>
        {field?.metric && <><p>原值：{field.value === null ? "未记录" : `${field.value}${field.unit}`} · 状态：{field.measured ? "原测量可核" : "待核，不能当作实测零值"}</p>
          <p>{field.metric.thresholds.map(value => `${value.label} ${value.operator === ">=" ? "≥" : "<"}${value.value}${field.unit}`).join("；") || "原记录未设数值门槛"}</p>
          <p>来源：{sourceLabel(field.metric.source)} · 源时刻：{field.metric.as_of ? dateTimeTextBJ(field.metric.as_of, {seconds: true}) : "未提供"} · 接收：{field.metric.received_at ? dateTimeTextBJ(field.metric.received_at, {seconds: true}) : "未提供"}</p>
          <p>原版本：{item.selectionMetrics?.snapshot_version || "未记录"}</p></>}
        {field && !field.metric && <p>原规则分，非上涨概率；{field.measured ? field.state === "partial" ? "依据仅部分取得" : field.state === "valid_empty" ? "原来源为有效空集" : "原依据读取成功" : "原数值是待核或计算占位，不作为实测分"}。</p>}
      </details>}
    </section>
    {!readError && item.execution?.gate_decision !== "passed" && item.execution?.gate_reason
      ? <p className={compactStyles.limits} title={item.execution.gate_reason}>{item.execution.gate_decision === "rejected" ? "本次阻断" : "本次待核"}：{readerExcerpt(item.execution.gate_reason, 72)}</p>
      : <p className={compactStyles.limits} title={reading.limit}>留意：{readerExcerpt(reading.limit, 40)}</p>}
    {(item.execution || item.stopLoss || item.buyRange || item.score != null) && <div className={compactStyles.reference}>
      {item.execution && <span title={`${item.execution.gate_reason || "原复核"}；${item.execution.decision_id} · ${item.execution.decision_version}；不是成交`}>{readError ? "执行复核待读取" : executionState !== "ready" ? `执行快照${executionText || "未判定"}` : item.execution.gate_decision === "passed" ? "执行复核通过" : "执行复核未通过"}</span>}
      {item.score != null && <span title="原综合筛选分，不是上涨概率">综合 {fmt(item.score, 1)}</span>}
      {item.stopLoss && <span title={item.stopLoss.basis}>止损参考 {fmt(item.stopLoss.price)}（-{item.stopLoss.pct}%）</span>}
      {item.buyRange && <span title={`${item.buyRange.basis}；非成交区间`}>参考 {fmt(item.buyRange.low)}–{fmt(item.buyRange.high)}</span>}
    </div>}
    {(item.vetoes.length > 0 || hasMissing || item.chipSignal?.signal) && <div className={compactStyles.flags} role="status">
      {item.vetoes.length > 0 && <span title={item.vetoes.join("；")}>存在 {item.vetoes.length} 项不满足条件</span>}{hasMissing && <span>部分依据缺失</span>}
      {item.chipSignal?.signal === "distribution_warning" && <strong title={item.chipSignal.reasons.join("；")}>派发警示</strong>}
      {item.chipSignal?.signal === "launch_watch" && <span title={item.chipSignal.reasons.join("；")}>启动观察</span>}
    </div>}
  </div>;
}

export function PickCard({
  item,
  positionLabel = null,
  dailyEvidence,
  onShowDailyEvidence,
  readError = false,
  evidenceExpanded = false,
  compact = false,
  active = true,
}: {
  item: TradingCard;
  /** 闭环「标签」：sim=已模拟持仓 / real=已真实持仓（持仓状态派生，卖出自动消失） */
  positionLabel?: "sim" | "real" | null;
  dailyEvidence?: {item: DailyPickItem; date: string; generatedAt: string | null; version: string | null};
  onShowDailyEvidence?: (item: DailyPickItem) => void;
  /** Retained read-only snapshot after a failed refresh, never current eligibility. */
  readError?: boolean;
  evidenceExpanded?: boolean;
  /** Hunting's local scan view; full evidence retains the existing drawer. */
  compact?: boolean;
  /** Hidden list groups retain local inspection state, never a live evidence drawer. */
  active?: boolean;
}) {
  // 整行点击跳工作台（2026-09-09 用户反馈：盘中跟踪条目标点击无响应——两卡现已统一）
  const stockNav = useStockRowNav();
  const followChipText =
    item.followState === "followable" ? "可跟" : item.followState === "blocked" ? "禁买" : "仅观察";
  const followChipTitle =
    item.followState === "followable"
      ? `空仓闸门日龙头判据全满足（连板高度+梯队地位+题材催化，无红线）。可跟 ≠ 可买：不给买入范围，参与须经影子持仓先验证。${item.followReasons.join("；")}`
      : item.followState === "blocked"
        ? `空仓闸门日红线压制（禁买）：${item.followReasons.join("；") || "命中否决/异动风险"}`
        : `空仓闸门已触发：本条不给买入范围，仅供复盘与观察。${item.followReasons.join("；")}`;

  const reading = selectionReading(item);
  const selectionScores = item.sub_scores ? SUB_LABELS.map(([key, label]) => ({key, label: label === "基本" ? "基本面" : label,
    value: typeof item.sub_scores?.[key] === "number" && Number.isFinite(item.sub_scores[key]) && item.sub_scores[key] >= 0 && item.sub_scores[key] <= 100 ? item.sub_scores[key] : null, maximum: 100 as const,
    state: item.dimensionEvidence?.[key]?.state || "unknown",
    basis: item.dimensionEvidence?.[key]?.reason || item.basisRows.find(row => row.label === label)?.value || "原记录未保存此项依据。"})) : undefined;
  const executionState = readError && item.execution ? "stale" : item.execution?.executable_snapshot.state ?? null;
  const executionStateText =
    executionState === "ready"
      ? "可判"
      : executionState === "stale"
        ? "陈旧"
        : executionState === "degraded"
          ? "降级"
          : executionState === "unavailable"
            ? "不可用"
            : executionState
              ? "未判定"
              : null;

  const readingKey = `${item.symbol}:${item.origin}:${item.selectionEntry?.selected_at || ""}:${item.selectionEntry?.source_version || ""}`;
  const [readingIdentity, setReadingIdentity] = useState<string | null>(null);
  if (readingIdentity !== null && (readingIdentity !== readingKey || !active)) setReadingIdentity(null);
  const readingOpen = active && !evidenceExpanded && readingIdentity === readingKey;
  const presence = useExitPresence(readingOpen ? readingKey : null);
  const evidence = <>
      {compact && <><SelectionEntryRow entry={item.selectionEntry} scope={item.origin === "picks" ? "daily" : "intraday"} referenceOnly={item.referenceOnly}/>
      {item.origin === "intraday" && dailyEvidence?.item.symbol === item.symbol && <SelectionEntryRow entry={dailyEvidence.item.selection_entry} scope="daily"/>}</>}
      <ReaderBrief label="本次筛选考虑" facts={[{label: reading.why.endsWith("…") ? "理由节选" : "选择理由", value: reading.why}, {label: reading.limit.endsWith("…") ? "条件节选" : "失效条件", value: reading.limit}]}/>
      <QualificationDetails item={item} readError={readError}/>
      <p className="text-xs text-zinc-600 dark:text-zinc-400">入选依据保留原记录；下方公司资料与研究是补充，不回填当时理由。</p>
      <StockAttributionPanel symbol={item.symbol} title="相关消息与AI解读" defaultExpanded bounded={false} concise
        selectedAt={item.selectionEntry?.selected_at} recordedRefs={item.relatedEventRefs} selectionContext selectionSummary={{
          reason: item.basisRows.find(row => ["入选", "来源", "联动"].includes(row.label))?.value || reading.why,
          reasonRecorded: item.basisRows.some(row => ["入选", "来源", "联动"].includes(row.label)),
          condition: [...item.vetoes, ...item.followReasons, ...item.invalidations].join("\n") || reading.limit,
          supports: item.basisRows, version: item.selectionEntry?.source_version,
          measurements: item.selectionMetrics,
          scores: selectionScores,
        }}/>
      <TradingAgentsResearchPanel symbol={item.symbol} compact allowStart={!item.referenceOnly}/>
      {!item.referenceOnly && item.paperContext && <PaperSelectionAction key={`${item.paperContext.ref}:${item.paperContext.version}`} symbol={item.symbol} selectionRef={item.paperContext.ref} sourceVersion={item.paperContext.version}/>}
      <ResearchDisclosure label="原筛选完整依据" identity={readingKey} defaultOpen={evidenceExpanded}>
      <ReaderBrief label="筛选支持项" facts={reading.supports}/>
      {compact && item.execution && <FactCards label="原报价与执行复核" items={[
        {label: "生成参考价", value: `${fmt(item.referencePrice)}；不是成交价`},
        {label: "执行快照", value: `${fmt(item.execution.executable_snapshot.price)}；${readError ? "上次读取，当前待核实" : executionStateText || "未判定"}；不是成交价`},
        {label: "执行快照来源", value: sourceLabel(item.execution.executable_snapshot.source)},
        {label: "执行快照源时刻", value: item.execution.executable_snapshot.as_of ? dateTimeTextBJ(item.execution.executable_snapshot.as_of, {seconds:true}) : "原记录未提供"},
        {label: "原复核依据", value: item.execution.gate_reason || "原记录未提供"},
        {label: "原复核身份", value: `${item.execution.decision_id} · ${item.execution.decision_version}`},
      ]}/>}
      {compact && <FactCards label="原画像与判定" items={[
        item.strengthTier && {label: "题材强度", value: item.strengthTier},
        item.distinctiveness && {label: "辨识度", value: `${triText(item.distinctiveness.level)}；${item.distinctiveness.basis || "原依据未记录"}`},
        item.certainty && {label: "确定性", value: `${triText(item.certainty.level)}；${item.certainty.basis || "原依据未记录"}`},
        item.linkage && {label: "联动确定性", value: `${triText(item.linkage.level)}；${item.linkage.basis || "原依据未记录"}`},
        item.confidence && {label: "原置信档", value: `${item.confidence.label}；${item.confidence.reasons.join("；")}`},
      ].filter((value): value is {label: string; value: string} => !!value)}/>}
      {/* 估值（仅盘前名单）：数据源确实不提供时（新股/亏损/长期停牌）显示"暂无"并注明原因，
          不留白、不臆造，与个股详情页口径一致。 */}
      {(item.pe_ttm != null || item.pb != null) && (
        <div className="mt-1 flex items-center gap-2 text-[10px] text-zinc-600 dark:text-zinc-400">
          <span
            className="font-mono"
            // 负 PE = TTM 净利润为负（亏损）。直接显示 "-78.01" 会被误读成"极低估值"，
            // 因此负值与缺失分别表述；悬停给出原始数值，信息不丢失。
            title={
              item.pe_ttm == null
                ? "数据源未提供市盈率（常见于新股、亏损或长期停牌个股）"
                : item.pe_ttm < 0
                  ? `TTM 净利润为负（亏损），市盈率 ${item.pe_ttm} 不适用估值比较`
                  : "市盈率 TTM（后端已用腾讯行情补全；链首 ths 快照不带该字段）"
            }
          >
            PE {item.pe_ttm == null ? "暂无" : item.pe_ttm < 0 ? "亏损" : fmt(item.pe_ttm)}
          </span>
          <span className="font-mono" title="市净率">
            PB {item.pb != null ? fmt(item.pb) : "暂无"}
          </span>
        </div>
      )}

      {/* 六维评分（仅盘前名单）+ meta 置信档 */}
      {item.sub_scores && (
        <div className="mt-2 flex items-center gap-2">
          <span
            className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-xs font-semibold dark:bg-zinc-800"
            title="六维加权综合分（一票否决后）"
          >
            {item.score ?? "--"}
          </span>
          {item.confidence && (
            <span
              className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${
                item.confidence.tier === "strong"
                  ? "border-zinc-200 dark:border-zinc-800 text-red-700 dark:text-red-300"
                  : item.confidence.tier === "executable"
                    ? "border-sky-500/50 bg-sky-500/10 text-sky-700 dark:text-sky-300"
                    : "border-zinc-200 dark:border-zinc-800 text-amber-800 dark:text-amber-300"
              }`}
              title={`meta 置信层（综合分+相位+筹码+红线 → 三档）：\n${item.confidence.reasons.join("；")}`}
            >
              {item.confidence.label}
            </span>
          )}
          <div className="flex flex-1 gap-1">
            {SUB_LABELS.map(([key, label]) => {
              const v = item.sub_scores?.[key];
              const evidence = item.dimensionEvidence?.[key];
              const absent = evidence?.state === "missing" || evidence?.state === "error";
              const measured = typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 100
                && (evidence?.state === "success" || evidence?.state === "valid_empty" || evidence?.state === "partial");
              return (
                <div key={key} className="flex-1" title={`${label}：${evidence?.reason || item.basisRows.find((r) => r.label === label)?.value || "--"}`}>
                  {absent ? <span className="block text-center text-[9px] text-zinc-600 dark:text-zinc-400" aria-label={`${label}依据缺失，计算占位 ${v ?? "未知"}`} title={evidence.reason}>缺项</span> : !measured ? <span className="block text-center text-[9px] text-zinc-600 dark:text-zinc-400" aria-label={v == null ? `${label}评分缺失` : `${label}原值 ${v}，读取状态待核`}>{v == null ? "暂无" : `${v}·待核`}</span> : <div role="meter" aria-label={`${label}评分`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={v} className="score-track h-1 w-full overflow-hidden rounded bg-zinc-100 dark:bg-zinc-800">
                    <div className="h-full rounded bg-zinc-500 dark:bg-zinc-400" style={{ width: `${v}%` }} />
                  </div>}
                  <div className="mt-0.5 text-center text-[9px] text-zinc-600 dark:text-zinc-400">{label}{evidence?.state === "partial" ? "·部分" : evidence?.state === "valid_empty" ? "·有效空集" : ""}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {item.sub_scores && !item.dimensionEvidence && <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">历史记录未保存各维读取状态；评分数字不能证明依据齐全。</p>}
      {item.dimensionEvidence && Object.entries(item.dimensionEvidence).some(([, e]) => ["missing", "error", "partial"].includes(e.state)) && (
        <ConditionList label="依据缺项" tone="warning" items={SUB_LABELS.flatMap(([key, label]) => {
          const evidence = item.dimensionEvidence?.[key];
          return evidence && ["missing", "error", "partial"].includes(evidence.state) ? [`${label}：${evidence.reason}；当前计分 ${item.sub_scores?.[key] ?? "未知"}${evidence.state !== "partial" ? " 为计算占位，不代表实测中性" : " 仅依据已取得部分"}。`] : [];
        })}/>

      )}
      {dailyEvidence && onShowDailyEvidence && (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-zinc-600 dark:text-zinc-400" onClick={event => event.stopPropagation()}>
          <span className="min-w-0" title={dailyEvidence.version ?? "生成版本未记录"}>每日 {dailyEvidence.date} · {dailyEvidence.generatedAt ? clockOf(dailyEvidence.generatedAt) : "生成时点未记录"} · {dailyEvidence.item.score}分</span>
          <button type="button" className="quiet-action shrink-0" onClick={() => onShowDailyEvidence(dailyEvidence.item)}>每日依据</button>
        </div>
      )}
      {item.themeSources && item.themeSources.length > 1 && (
        <section aria-label="全部题材来源" className="space-y-2">{item.themeSources.map((source, index) => <div key={index}><h4>{source.theme || "题材未记录"} · {source.strength_tier || "强度未记录"}</h4><p className="whitespace-pre-wrap">{source.pick_basis || source.linkage?.basis || "来源依据未记录"}</p></div>)}</section>

      )}

      {/* 入选原因：「为什么选它」。
          盘前 = 六维 basis 摘要（一票否决显式标红；筹码为附注维度不进权重）；
          盘中 = 入选理由（pick_basis，仅分层名单）+ 涨停原因（reason，同花顺官方原串）。
          ⚠️ 必须带标题行（2026-09-10 用户反馈「盘前选择怎么没有入选原因」）：盘前的依据
          本就是七行维度依据，但没有标题时看起来像「这卡没有入选原因」——数据没丢，
          丢的是口径。标题两侧统一，用户才能一眼对上「入选」这件事。
          两者皆无时**整节不渲染**——不拿「入选 —」把「本就没有这个维度」伪装成「没取到」。 */}
      {(item.basisRows.length > 0 || item.vetoes.length > 0) && (
        <div className="stock-card-basis text-[11px] leading-relaxed">
          <div className="text-[10px] font-medium text-zinc-600 dark:text-zinc-400">入选原因</div>
          <dl aria-label="入选依据核对" className="space-y-3">{item.basisRows.map((row, index) => <div key={`${index}:${row.label}`}><dt className="font-medium">{row.label}</dt><dd className="mt-1 whitespace-pre-wrap">{row.label === "涨停原因" ? <LimitReason symbol={item.symbol} reason={row.value} source={row.source} date={row.date} full/> : row.value}</dd></div>)}</dl>
          <ConditionList label="否决条件" items={item.vetoes} tone="warning"/>

        </div>
      )}

      {/* 买入区间（仅盘前名单：收盘评分产出，盘中路径不适用 ⇒ 整节不渲染并见卡底口径注记）。
          空仓闸门触发时撤除区间，不给出手依据。 */}
      {item.origin === "picks" &&
        (item.buyRange ? (
          <div className="mt-2 rounded-lg bg-sky-500/5 px-2 py-1.5 text-[11px]" title={item.buyRange.basis}>
            <span className="text-zinc-600 dark:text-zinc-400">买入参考区间</span>{" "}
            <span className="font-mono font-medium tabular-nums">
              {fmt(item.buyRange.low)} – {fmt(item.buyRange.high)}
            </span>{item.buyRange.basis && <NarrativeText text={item.buyRange.basis} label="区间依据"/>}
          </div>
        ) : (
          <div
            className={`mt-2 rounded-lg px-2 py-1.5 text-[11px] ${
              item.followState === "followable"
                ? "bg-sky-500/5 text-sky-700 dark:text-sky-300"
                : "bg-red-500/5 text-red-700 dark:text-red-300"
            }`}
          >
            {item.followState === "followable"
              ? "空仓闸门日：不给买入区间；属「可跟」名单，参与须经影子持仓先验证"
              : "空仓闸门已触发：本条不给出买入参考区间"}
          </div>
        ))}

      {/* 止损参考位 + 出场纪律 + 失效条件（freqtrade 的止损/跟踪止盈/ROI 分档 的 A 股映射） */}
      {(item.stopLoss || item.exit || item.invalidations.length > 0) && (
        <div className="stock-card-risk text-[11px]">
          {item.stopLoss && <Row k="止损计算依据" v={item.stopLoss.basis || "原记录未提供"}/>}
          {item.exit?.trailing_pct != null && (
            <Row k="跟踪止盈" v={`回落 ${item.exit.trailing_pct}%`} title={item.exit.disclaimer} />
          )}
          {item.exit && item.exit.roi_ladder.length > 0 && (
            <Row
              k="ROI 阶梯"
              v={item.exit.roi_ladder.map((r) => `+${r.gain_pct}%→${r.action}`).join("；")}
            />
          )}
          {item.exit?.note && <Row k="纪律" v={item.exit.note} />}
          {item.invalidations.length > 0 && (
            <ConditionList label="失效条件" items={item.invalidations} tone="warning"/>
          )}
        </div>
      )}

      {/* 关联消息（仅盘前名单有） */}
      {item.relatedEvents.length > 0 && (
        <div className="stock-card-events text-[11px]">
          <ConditionList label="入选当时记录的消息" items={item.relatedEvents}/>
        </div>
      )}

      {/* 口径注记：解释「本卡为什么缺某几节」（仅盘中路径需要——缺的维度属收盘口径） */}
      {item.caliberNote && (
        <p className="stock-card-note text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">
          {item.caliberNote}
        </p>
      )}

      </ResearchDisclosure>
  </>;

  return (
    <>
    <CardShell flow className={`stock-card-${item.origin}${compact ? ` ${compactStyles.compactCard}` : ""}`} onClick={stockNav(item.symbol)}>
      {readError && <p role="status" className="text-xs text-amber-800 dark:text-amber-300">上次读取结果 · 刷新失败，当前行情与参与条件待重新核实。原始依据仅供核对。</p>}
      {compact ? <CompactPickSummary key={`${readingKey}:compact`} item={item} scores={selectionScores} reading={reading} positionLabel={positionLabel}
        dailyEvidence={dailyEvidence} readError={readError} followText={followChipText} followTitle={followChipTitle} executionState={executionState} executionText={executionStateText}/> : <>
      {/* 头：名称代码（可点 → 工作台）+ 来源/持仓/T 档徽标 + 现价 + 涨跌幅 */}
      <CardHead
        name={item.name}
        symbol={item.symbol}
        link
        right={
          <>
            <span
              className={`rounded px-1.5 py-0.5 text-[10px] ${
                item.origin === "picks"
                  ? "bg-amber-500/10 text-amber-800 dark:text-amber-300"
                  : "bg-sky-500/10 text-sky-700 dark:text-sky-300"
              }`}
              title={
                item.origin === "picks"
                  ? "来源：每日精选（当日生成的持久候选；原生成依据与当前执行复核分开）"
                  : "来源：盘中跟踪（当日实时随盘面重算）"
              }
            >
              {item.origin === "picks" ? "每日精选" : "盘中跟踪"}
            </span>
            {positionLabel === "real" && (
              <span
                className="rounded px-1.5 py-0.5 text-[10px] font-medium text-rose-700 dark:text-rose-300"
                title="人工成交流水记录，未经券商核验；卖出或修正后更新"
              >
                有手工持仓记录
              </span>
            )}
            {item.tier != null && (
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                  item.tier <= 2 ? "bg-up/10 text-up-ink dark:text-up" : "bg-amber-500/10 text-amber-800 dark:text-amber-400"
                }`}
                title="跟踪档：确定性优先、辨识度次之的多维筛选分层（T1 最高）"
              >
                T{item.tier} 跟踪档
              </span>
            )}
            <div className="mt-0.5 flex items-baseline justify-end gap-2">
              <span
                className="font-mono text-base font-semibold tabular-nums"
                title={
                  readError
                    ? "上次读取的报价，当前行情未核实，不作为执行资格"
                    : executionState === "ready"
                    ? "最新执行复核快照价（不是成交价）"
                    : item.origin === "picks"
                      ? item.execution
                        ? "执行快照非可用状态，主价格回退为组合生成/首见参考价（不是成交价）"
                        : "组合生成/首见参考价（尚无盘中执行复核，不是成交价）"
                      : "现价（全市场快照）"
                }
              >
                {fmt(item.price)}
              </span>
              <span className={`font-mono text-[10px] tabular-nums ${pctColor(item.change_pct)}`}>
                {pctText(item.change_pct)}
              </span>
            </div>
          </>
        }
      />

      <SelectionEntryRow entry={item.selectionEntry} scope={item.origin === "picks" ? "daily" : "intraday"} referenceOnly={item.referenceOnly} />
      <QualificationSummary item={item} readError={readError}/>
      {item.origin === "intraday" && dailyEvidence?.item.symbol === item.symbol && <SelectionEntryRow entry={dailyEvidence.item.selection_entry} scope="daily" />}

      {/* IMP-006：参考价与动作时执行快照分开呈现；两者都不是成交。 */}
      {item.origin === "picks" && (item.referencePrice != null || item.execution) && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[10px] text-zinc-600 dark:text-zinc-400">
          <span className="font-mono" title="组合生成/首见时参考价，仅用于复盘基准，不代表成交">
            参考价 {fmt(item.referencePrice)}
          </span>
          {item.execution && (
            <>
              <span
                className="font-mono"
                title={"动作时复核行情 · " + (item.execution.executable_snapshot.source ?? "来源未知") + " · " + item.execution.executable_snapshot.state}
              >
                {readError ? "上次执行快照" : "执行快照"} {fmt(item.execution.executable_snapshot.price)}
              </span>
              <Chip
                text={
                  readError
                    ? "执行复核·待读取"
                    : executionState !== "ready"
                    ? `执行快照·${executionStateText}`
                    : item.execution.gate_decision === "passed"
                      ? "执行复核·通过"
                      : "执行复核·未通过"
                }
                title={
                  "行情状态 " + (executionStateText ?? "未知") +
                  (item.execution.gate_reason ? "；" + item.execution.gate_reason : "") +
                  "；decision " + item.execution.decision_id +
                  " · version " + item.execution.decision_version +
                  "；该标记只表示门控判定，不表示已成交"
                }
                className={
                  executionState !== "ready"
                    ? "border-zinc-200 dark:border-zinc-800 text-amber-800 dark:text-amber-300"
                    : item.execution.gate_decision === "passed"
                      ? "border-sky-500/50 bg-sky-500/10 text-sky-700 dark:text-sky-300"
                      : "border-zinc-300 text-zinc-600 dark:border-zinc-700 dark:text-zinc-400"
                }
              />
            </>
          )}
        </div>
      )}

      {/* 可参与性（2026-09-15 猎场口径）：加入猎场的个股必须是投资者**实际可以参与的**。
          这张 chip 把"能不能买"放在卡片第一屏，而不是让用户自己从"已封板"推导；
          参考区按 current 状态显示「仅参考 / 未判定」；曾封板但当前已开板者会进入候选并从参考区去重。 */}
      {item.tradability && (
        <div className="mt-1.5">
          <Chip
            text={
              readError
                ? "可参与性待重新核实"
                : item.tradability.level === "可参与"
                ? "可参与评估 · 当前未封板"
                : item.tradability.level === "不可参与"
                  ? "当前不可参与"
                  : "可参与性未判定"
            }
            title={`${readError ? "上次读取的" : ""}可参与性判定（候选资格，不代表成交保证）：${item.tradability.basis}`}
            className={
              readError
                ? "border-zinc-200 dark:border-zinc-800 text-amber-800 dark:text-amber-300"
                : item.tradability.level === "可参与"
                ? "border-sky-500/50 bg-sky-500/10 text-sky-700 dark:text-sky-300"
                : item.tradability.level === "不可参与"
                  ? "border-zinc-200 dark:border-zinc-800 text-amber-800 dark:text-amber-300"
                  : "border-zinc-300 text-zinc-600 dark:border-zinc-700 dark:text-zinc-400"
            }
          />
        </div>
      )}

      {/* 标的画像 chips：梯队地位 / 题材·阶段 / 题材强度档 / 风险档位（两源归一，有则渲染） */}
      {(item.role || item.theme || item.strengthTier || item.riskTier) && (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          {item.role && (
            <Chip
              text={`${item.role}${item.boards ? ` · ${item.boards}板` : ""}`}
              title={item.roleBasis ? `梯队地位（Echelon Role）：${item.roleBasis}` : `梯队地位：${item.role}`}
              className={roleClass(item.role)}
            />
          )}
          {item.theme && (
            <span className="inline-flex min-w-0 flex-wrap items-center gap-1"><Chip
              text={`${item.theme}${item.stage ? ` · ${item.stage}` : ""}`}
              title="原入选标签与原阶段；不证明正式目录归属或当日受益"
              className="border-zinc-300 text-zinc-600 dark:border-zinc-600 dark:text-zinc-300"
            /><SourceThemeIdentity reason={item.theme} historical/><CurrentThemeReference reason={item.theme} reference={item.currentThemeCatalog}/></span>
          )}
          {item.strengthTier && <Chip text={item.strengthTier} title="题材强度档" />}
          {item.board && (
            <Chip
              text={item.board}
              title="板块与账户交易权限（2026-09-15 起账户只开沪深主板）——本名单已按权限过滤，不含创业板/科创板/北交所/B 股"
            />
          )}
          {item.riskTier && (
            <Chip
              text={item.riskTier}
              title={`风险档位（Risk Tier）：决定止损宽严与仓位保守程度。${item.exit?.note ?? ""}`}
              className={TIER_STYLE[item.riskTier]}
            />
          )}
        </div>
      )}

      {/* 立场与筹码信号 chips（仅盘前名单） */}
      {(item.observationOnly || item.chipSignal?.signal) && (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          {item.observationOnly && (
            <Chip
              text={followChipText}
              title={followChipTitle}
              className={
                item.followState === "followable"
                  ? "border-sky-500/50 bg-sky-500/10 text-sky-700 dark:text-sky-300"
                  : "border-zinc-200 dark:border-zinc-800 text-red-700 dark:text-red-300"
              }
            />
          )}
          {item.chipSignal?.signal === "distribution_warning" && (
            <Chip
              text="派发警示"
              title={`筹码形态警示（CYQ 近似口径）：${item.chipSignal.reasons.join("；")}`}
              className="border-zinc-200 dark:border-zinc-800 text-red-700 dark:text-red-300"
            />
          )}
          {item.chipSignal?.signal === "launch_watch" && (
            <Chip
              text="启动观察"
              title={`筹码形态观察（CYQ 近似口径）：${item.chipSignal.reasons.join("；")}`}
              className="border-teal-500/50 bg-teal-500/10 text-teal-700 dark:text-teal-300"
            />
          )}
        </div>
      )}

      {/* 判定（辨识度/确定性/联动，仅盘中名单）：三态，依据悬停可见。
          联动确定性是 2026-09-15 新口径下**候选卡片的主判定**（候选当前未封板，
          封板质量那一套判据不适用 ⇒ 辨识度/确定性为 null 时不渲染，不拿占位糊上）。 */}
      {(item.distinctiveness || item.certainty || item.linkage) && (
        <div className="mt-2 flex flex-wrap gap-1">
          {item.linkage && <JudgeChip label="联动确定性" level={item.linkage.level} basis={item.linkage.basis} />}
          {item.distinctiveness && (
            <JudgeChip label="辨识度" level={item.distinctiveness.level} basis={item.distinctiveness.basis} />
          )}
          {item.certainty && <JudgeChip label="确定性" level={item.certainty.level} basis={item.certainty.basis} />}
        </div>
      )}

      <div aria-label="个股关键参考" className="my-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[11px] tabular-nums">
        {!item.role && item.boards != null && <span>板数 {item.boards}板</span>}
        {item.score != null && <span title="筛选评分，不是上涨概率">筛选评分 {item.score}</span>}
        {item.stopLoss && <span>止损参考 <strong className="font-medium">{fmt(item.stopLoss.price)}（-{item.stopLoss.pct}%）</strong></span>}
        {item.buyRange && <span>参考区间 {fmt(item.buyRange.low)}–{fmt(item.buyRange.high)}</span>}
      </div>
      {!evidenceExpanded && !item.referenceOnly && (item.selectionMetrics?.items.length
        ? <SelectionMeasurements key={`${readingKey}:measurements`} value={item.selectionMetrics} compact/>
        : selectionScores?.length ? <SelectionScores key={`${readingKey}:scores`} scores={selectionScores} compact/> : null)}
      <ReaderBrief label="入选简述"
        headline={item.theme ? `${item.theme}${item.role ? ` · ${item.role}` : ""}` : "已进入观察名单"}
        facts={[{label: "为什么关注", value: readerExcerpt(reading.why, 60)}, {label: "还需留意", value: readerExcerpt(reading.limit, 36)}]}
        cautions={[...(readError ? ["刷新失败，当前条件待核实"] : []), ...(item.vetoes.length ? ["存在不满足条件的项目"] : []), ...readerCautions([...item.basisRows.map(row => row.value), ...item.followReasons]), ...(item.dimensionEvidence && Object.values(item.dimensionEvidence).some(e => ["missing", "error", "partial"].includes(e.state)) ? ["部分依据缺失"] : [])]}
      />
      </>}
      {active && evidenceExpanded ? <ResearchDisclosure label="入选解读" identity={readingKey} defaultOpen>{evidence}</ResearchDisclosure> : null}
      <CardEntryRow symbol={item.symbol} name={item.name} theme={item.theme ?? null}
        onRead={evidenceExpanded ? undefined : () => {if (active) setReadingIdentity(readingKey);}} readingOpen={readingOpen}/>

    </CardShell>
    {/* Sibling of the spatial card: portal pointer/capture events cannot tilt or activate it. */}
    {presence.value === readingKey && <ModalShell presentation="drawer" open={presence.active}
      onClose={() => setReadingIdentity(null)} label={`${item.name || item.symbol} · 入选解读`} size="md" zIndex={60}
      header={<h2 className="text-sm font-medium">{item.name || item.symbol} · 入选解读</h2>}
      bodyClassName="overflow-y-auto space-y-4 px-4 py-4"
      footer="来自保存的筛选记录；消息用于核对关联，不改写当时理由。不构成买卖建议。">
      {presence.active && evidence}
    </ModalShell>}
    </>
  );
}

/** Original entry facts stay legible beside a moving quote, including sealed observations. */
function SelectionEntryRow({ entry, scope, referenceOnly = false, compact = false }: {
  entry?: DailyPickItem["selection_entry"];
  scope: "daily" | "intraday";
  referenceOnly?: boolean;
  compact?: boolean;
}) {
  const scopeName = scope === "daily" ? "每日" : "盘中";
  const quoteClockLabel = scope === "daily" ? "报价源时间" : "行情快照时间";
  const expectedSource = scope === "daily" ? "daily_generation" : "intraday_rank_archive";
  const usable = (entry?.state === "recorded" || entry?.state === "partial") && entry.semantics === "observation_only_not_fill" && entry.source === expectedSource;
  const selectedAt = usable ? entry?.selected_at : null;
  const validClock = (value: string | null | undefined) => !!value && Number.isFinite(Date.parse(value)) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value);
  const knownTime = validClock(selectedAt);
  const price = usable && typeof entry?.reference_price === "number" && Number.isFinite(entry.reference_price) && entry.reference_price > 0 ? entry.reference_price : null;
  const change = usable && typeof entry?.reference_change_pct === "number" && Number.isFinite(entry.reference_change_pct) ? entry.reference_change_pct : null;
  const hasFacts = usable && (knownTime || price != null || change != null);
  const quoteClock = usable && validClock(entry?.quote_as_of) ? entry?.quote_as_of : null;
  const reason = entry?.reason || (entry && !usable && entry.state !== "missing"
    ? "原首次入选记录格式或来源与当前名单不匹配；未用现价或读取时间补填"
    : "没有已记录的首次入选证据，未用现价或读取时间补填");
  return <div className={compact ? compactStyles.entry : "selection-entry-row mt-2 flex flex-wrap gap-x-3 gap-y-1 border-b border-zinc-200 pb-2 text-[11px] text-zinc-600 dark:border-zinc-800 dark:text-zinc-400"} aria-label={`${scopeName}首次加入记录`} title={`${scopeName}首次加入用于复盘，不是成交。${quoteClock ? `${quoteClockLabel}：${quoteClock}。` : ""}${usable && entry?.source_version ? `来源版本：${entry.source_version}` : ""}`}>
    {hasFacts ? <>
      <span className="whitespace-nowrap">{scopeName}首次加入 {knownTime ? <time dateTime={selectedAt!}>{bjDate(selectedAt!)} {timeTextBJ(selectedAt!)}</time> : "时间缺项"}</span>
      <span className="whitespace-nowrap tabular-nums">加入参考价 {price == null ? "缺项" : `¥${fmt(price)}`}</span>
      {change != null && <span className={`whitespace-nowrap tabular-nums ${pctColor(change)}`}>当时 {pctText(change)}</span>}
      <span className="whitespace-nowrap text-[10px]">非成交价</span>
      {(entry?.state === "partial" || !knownTime || price == null) && <span className="whitespace-nowrap text-[10px] text-amber-800 dark:text-amber-400">依据不完整</span>}
    </> : <span className="min-w-0 [overflow-wrap:anywhere]">{scopeName}{referenceOnly ? " · 当前仅参考，未查到已归档入选依据" : "首次入选依据未记录"}</span>}
    {!compact && <div className="w-full"><ResearchDisclosure label="加入记录的完整依据" identity={`${scope}:${JSON.stringify(entry)}`}>
      <FactTimeline label="时点依据" items={[
        {label: quoteClockLabel, value: quoteClock ?? "原记录缺少有效时间", kind: "time"},
      ]}/>
      <FactCards label="记录身份" items={[
        {label: "记录来源", value: !usable ? (entry?.source ? "来源不匹配，不能作为本名单依据" : "未记录") : scope === "daily" ? "每日组合生成" : "盘中入选归档"},
        {label: "原版本", value: usable && entry?.source_version ? entry.source_version : "版本缺项"},
      ]}/>
      {(!hasFacts || entry?.reason) && <p className="whitespace-pre-wrap">{reason}</p>}
    </ResearchDisclosure></div>}
  </div>;
}

/** 空仓闸门横幅：情绪转弱时主动提示规避（红线 3：只提示，不下指令）。
 *
 *  分档（2026-09-10 用户拍板）：撤除档（相位级信号/多信号叠加）撤买入区间 + 卡上标「仅观察」；
 *  提示档（单条量化阈值擦线）只提示、**保留买入区间**。两者横幅都在——差别在标的层，
 *  且必须写在横幅上，否则用户无法从界面看出「这次能不能按区间参与」。
 *  读 `strip_buy_range`（后端算好带出），不在前端重算规则。
 *
 *  ## 动态对照（2026-09-16 用户问题 7：「猎场判断必须动态化」）
 *
 *  `gate` 是**实时复核**结果（后端读时重算），`stored` 是生成时刻落库值。两者不一致时
 *  必须把差异讲清楚——今天实测的是「生成时判退潮·建议空仓观望 → 收盘口径高潮·已解除」，
 *  而当天恰好有多只标的给了介入机会。只显示实时值会让用户以为"系统改主意了"，
 *  只显示落库值则是**把开盘 3 分钟的瞬时快照当全天结论**（这正是要修的缺陷）。
 *
 *  ⚠️ 已解除时**不得暗示当日名单恢复可买**：`buy_range` 在生成时已被撤除并落库，
 *  读时复核是展示层行为，不回溯改写名单。文案必须说清这一层，否则等于给出
 *  「系统说可以买了」的错误印象（红线 3）。
 */
export function StandAsideBanner({
  gate,
  stored,
  generatedAt,
  compact = false,
}: {
  gate: StandAsideGate;
  stored?: StandAsideGate | null;
  generatedAt?: string | null;
  compact?: boolean;
}) {
  const cmp = compareGates(gate, stored);
  const at = clockOf(generatedAt);
  const live = gateActive(gate);
  const newly = cmp.drift === "newly_triggered";
  const cleared = cmp.drift === "cleared";
  const unavailable = gate.gate_source === "unavailable";
  // An unknown current state must still expose its failed recheck, even without a stored active gate.
  if (!live && !newly && !cleared && !unavailable) return null;

  const strong = gate.level === "strong";
  const stripped = gate.strip_buy_range;
  return (
    <div
      role={cleared ? "status" : "alert"}
      data-testid={cleared ? "stand-aside-cleared" : "stand-aside-banner"}
      data-compact={compact || undefined}
      className={`gate-banner shrink-0 rounded-lg border px-3 py-2 text-xs ${
        cleared
          ? "border-zinc-200 dark:border-zinc-800 text-emerald-800 dark:text-emerald-300"
          : strong
            ? "border-zinc-200 dark:border-zinc-800 text-red-700 dark:text-red-300"
            : "border-zinc-200 dark:border-zinc-800 text-amber-800 dark:text-amber-300"
      }`}
    >
      <div className="gate-current-advice flex items-start gap-1.5 font-medium">
        <svg aria-hidden="true" className="mt-0.5 shrink-0" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
          {cleared ? <path d="m5 12 4 4L19 6" /> : <><path d="m12 3 9 17H3L12 3Z" /><path d="M12 9v4m0 3v1" /></>}
        </svg>
        <span>{unavailable ? "实时判断未更新，以下为生成时记录" : (gate.presentation?.headline ?? gate.advice) || "风险复核不可用，当前状态待确认"}</span>
        {!unavailable && gate.presentation?.judged_at && <span className="ml-auto shrink-0 font-normal tabular-nums opacity-80">{timeTextBJ(gate.presentation.judged_at)}</span>}
      </div>
      {!compact && !unavailable && gate.presentation && <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        {gate.presentation.facts.map(fact => <div key={fact.label}><dt className="text-[10px] opacity-80">{fact.label}</dt><dd className="mt-0.5 font-medium tabular-nums">{fact.value}</dd></div>)}
      </dl>}
      {newly && <p className="mt-1 font-medium">生成时{at ? `（${at}）` : ""}未触发闸门；当前复核<strong className="font-medium">已触发</strong>，新增风险须核对。</p>}
      {cleared ? <>
        <p className="mt-1 font-medium">生成时{at ? `（${at}）` : ""}按「{cmp.storedPhase ?? "未知"}」判空仓观望；当前<strong className="font-medium">已解除</strong>。</p>
        <p className="mt-1 font-medium text-amber-800 dark:text-amber-300">生成时撤除决定<strong className="font-medium">不回溯</strong>：当日区间仍已撤除、标的仅观察。不构成买入建议。</p>
      </> : stripped !== undefined && <p className="mt-1 font-medium">
        {stripped ? "已撤除买入区间 · 下方标的仅观察" : "保留买入区间 · 提示档，非空仓信号"}
      </p>}
      {unavailable && <p className="gate-unavailable mt-1 font-medium">{gate.gate_note || "实时复核不可用，当前风险状态未知；请核对生成时刻结论。"}</p>}
      <details className="gate-disclosure mt-1.5">
        <summary className="cursor-pointer py-1 font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-current">核对依据与时点对照</summary>
        <div className="gate-detail-content mt-2 space-y-2 leading-relaxed">
      {compact && !unavailable && gate.presentation && <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        {gate.presentation.facts.map(fact => <div key={fact.label}><dt className="text-[10px] opacity-80">{fact.label}</dt><dd className="mt-0.5 font-medium tabular-nums">{fact.value}</dd></div>)}
      </dl>}
          {unavailable && gate.advice && <p>生成时原提示：{gate.advice}</p>}
          {gate.presentation && <p>{gate.presentation.note}</p>}
          {gate.reasons.length > 0 && <div><p className="font-medium">{unavailable ? "生成时刻触发依据（当前复核不可用）" : "当前触发依据"}</p><ul className="mt-1 space-y-0.5">{gate.reasons.map(r => <li key={r}>· {r}</li>)}</ul></div>}
          {(cleared || newly || cmp.drift === "reasons_changed") && <GateComparisonLines cmp={cmp} />}
          {stored && gate.gate_source === "live" && stored.reasons.length > 0 && <div><p className="font-medium">生成时刻原始依据</p><ul className="mt-1 space-y-0.5">{stored.reasons.map(r => <li key={r}>· {r}</li>)}</ul></div>}
          {cleared ? <p>生成时的撤除档决定已生效：当日名单的买入区间已随之撤除，下方标的仍为仅观察。本条只表示「当前盘面不再支持空仓观望的判断」，不改变当日已生成名单的形态，也不构成买入建议。</p>
            : stripped !== undefined && <p>{stripped ? "记录保留供复盘。" : "按「控制仓位、减少出手频率」酌情执行。"}</p>}
          {newly && <p>按当前盘面复核触发的是盘中新增的风险信号，不回写生成时刻的判断。</p>}
          {gate.disclaimer && <p>{gate.disclaimer}</p>}
        </div>
      </details>
    </div>
  );
}

/** 「生成时 → 当前」逐项对照行（相位 + 闸门输入）。两侧都空则整节不出现。 */
function GateComparisonLines({ cmp }: { cmp: GateComparison }) {
  const hasSide = cmp.storedInputs.length > 0 || cmp.liveInputs.length > 0;
  if (!hasSide && !cmp.livePhase) return null;
  return (
    <div data-testid="stand-aside-comparison" className="mt-1 space-y-0.5 text-[10px] opacity-90">
      <div>
        生成时相位「{cmp.storedPhase ?? "未知"}」
        {cmp.storedInputs.length > 0 && <> · {cmp.storedInputs.join(" · ")}</>}
      </div>
      <div>
        当前相位「{cmp.livePhase ?? "未知"}」
        {cmp.liveInputs.length > 0 && <> · {cmp.liveInputs.join(" · ")}</>}
      </div>
    </div>
  );
}
