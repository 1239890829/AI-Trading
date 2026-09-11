"use client";

import type { ReactNode } from "react";

import { CardHead, CardShell } from "@/components/picks/card-shell";
import { CardEntryRow } from "@/components/picks/card-entries";
import { useStockRowNav } from "@/components/stock-link";
import { fmt, pctColor, pctText, triText } from "@/lib/format";
import { roleClass } from "@/lib/role-style";
import type {
  DailyPickItem,
  ExitDiscipline,
  IntradayTopStock,
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
 * ⚠️ 盘中算不出/不该给的维度**不补、也不留白**：整节不渲染，并在卡底用一句口径注记
 * 说明「为什么没有」（`caliberNote`）。渲染占位「—」是把「该数据源本就没有这个维度」
 * 伪装成「有数据但没取到」，既误导也违反三态纪律（不适用 ≠ 缺失）。
 *
 * 红线 3：全部为可解释依据与条件陈述，不构成买卖建议。
 */

export const SUB_LABELS: [string, string][] = [
  ["sentiment", "情绪"],
  ["news", "消息"],
  ["tech", "技术"],
  ["fundamental", "基本"],
  ["capital", "资金"],
  ["echelon", "梯队"],
];

/** 梯队地位配色：唯一权威表在 `@/lib/role-style`（S2-10 合并，跨端由后端测试守卫）。 */

export const TIER_STYLE: Record<string, string> = {
  龙头博弈: "border-red-500/50 bg-red-500/10 text-red-700 dark:text-red-300",
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
  /* --- 估值与评分：目前仅盘前名单提供 --- */
  pe_ttm: number | null;
  pb: number | null;
  score: number | null;
  sub_scores: Record<string, number> | null;
  confidence: DailyPickItem["confidence"];
  /* --- 标的画像（两源归一） --- */
  role: string | null;
  roleBasis: string | null;
  boards: number | null;
  theme: string | null;
  stage: string | null;
  riskTier: string | null;
  strengthTier: string | null;
  /** 盘中跟踪档 T1~T3（仅 intraday-top 分层名单有） */
  tier: number | null;
  /* --- 判定：目前仅盘中名单提供 --- */
  distinctiveness: CardJudgement | null;
  certainty: CardJudgement | null;
  /* --- 依据行（归一：盘前是六维 basis 摘要，盘中是「入选 + 涨停原因」） --- */
  basisRows: { label: string; value: string }[];
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

/** 盘前名单（DailyPickItem）→ 统一模型。 */
export function fromDailyPick(it: DailyPickItem): TradingCard {
  const basisRows: { label: string; value: string }[] = [];
  for (const [key, label] of SUB_LABELS) {
    const v = it.bases?.[key];
    if (v) basisRows.push({ label, value: v });
  }
  // 筹码是附注维度（不进六维权重），排在六维之后
  if (it.bases?.["chip"]) basisRows.push({ label: "筹码", value: it.bases["chip"] });

  return {
    symbol: it.symbol,
    name: it.name,
    origin: "picks",
    price: it.price ?? null,
    change_pct: it.change_pct ?? null,
    pe_ttm: it.pe_ttm ?? null,
    pb: it.pb ?? null,
    score: it.score ?? null,
    sub_scores: it.sub_scores ?? null,
    confidence: it.confidence ?? null,
    role: it.echelon_role ?? null,
    roleBasis: it.echelon_basis ?? null,
    boards: it.boards ?? null,
    theme: it.theme ?? null,
    stage: it.theme_stage ?? null,
    riskTier: it.risk_tier ?? null,
    strengthTier: null,
    tier: null,
    distinctiveness: null,
    certainty: null,
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
    // 盘前名单是收盘产出的完整口径（评分/估值/买入区间/失效条件都在），无需额外解释
    caliberNote: null,
  };
}

/**
 * 盘中名单（intraday-top 分层名单 / 题材手风琴候选）→ 统一模型。
 *
 * 两者字段天然互认（除分层名单独有的 tier / pick_basis，与手风琴独有的无 stage 之外）。
 */
export function fromIntradayStock(it: IntradayTopStock | OpportunityStock): TradingCard {
  const basisRows: { label: string; value: string }[] = [];
  // 「入选」= pick_basis——只有分层名单才产生（要解释「为什么在这档」）
  const pickBasis = "pick_basis" in it ? it.pick_basis : null;
  if (pickBasis) basisRows.push({ label: "入选", value: pickBasis });
  // 「涨停原因」= reason——同花顺官方原串（ladder 行自带），两条路径都有
  if (it.reason) basisRows.push({ label: "涨停原因", value: it.reason });

  return {
    symbol: it.symbol,
    name: it.name,
    origin: "intraday",
    price: it.price ?? null,
    change_pct: it.change_pct ?? null,
    pe_ttm: null,
    pb: null,
    score: null,
    sub_scores: null,
    confidence: null,
    role: it.role ?? null,
    roleBasis: null,
    boards: it.boards ?? null,
    theme: "theme" in it ? (it.theme ?? null) : null,
    stage: "stage" in it ? (it.stage ?? null) : null,
    riskTier: null,
    strengthTier: "strength_tier" in it ? (it.strength_tier ?? null) : null,
    tier: "tier" in it ? (it.tier ?? null) : null,
    distinctiveness: toJudgement(it.distinctiveness),
    certainty: toJudgement(it.certainty),
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
    // 「不补，显式标注口径」：缺的是收盘才有的维度，说明一次即可，不逐个渲染占位
    caliberNote:
      "盘中实时口径 · 随盘面重算：含辨识度/确定性判定与出场纪律；" +
      "不含收盘六维评分、估值与买入区间（收盘后生成次日名单）。判定为条件陈述，不构成买卖建议。",
  };
}

/* ---------------------------------------------------------------- 渲染 */

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

export function PickCard({
  item,
  positionLabel = null,
}: {
  item: TradingCard;
  /** 闭环「标签」：sim=已模拟持仓 / real=已真实持仓（持仓状态派生，卖出自动消失） */
  positionLabel?: "sim" | "real" | null;
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

  return (
    <CardShell flow onClick={stockNav(item.symbol)}>
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
                  ? "来源：盘前选择（收盘后生成次日名单，换股门槛 15 分）"
                  : "来源：盘中跟踪（当日实时随盘面重算）"
              }
            >
              {item.origin === "picks" ? "盘前选择" : "盘中跟踪"}
            </span>
            {positionLabel && (
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                  positionLabel === "real"
                    ? "bg-rose-500/10 text-rose-700 dark:text-rose-300"
                    : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                }`}
                title="点击打开工作台查看持仓；卖出/删流水后标签自动消失"
              >
                {positionLabel === "real" ? "已真实持仓" : "已模拟持仓"}
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
              <span className="font-mono text-base font-semibold tabular-nums" title="现价（全市场快照）">
                {fmt(item.price)}
              </span>
              <span className={`font-mono text-[10px] tabular-nums ${pctColor(item.change_pct)}`}>
                {pctText(item.change_pct)}
              </span>
            </div>
          </>
        }
      />

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
                  ? "border-red-500/50 bg-red-500/10 text-red-700 dark:text-red-300"
                  : item.confidence.tier === "executable"
                    ? "border-sky-500/50 bg-sky-500/10 text-sky-700 dark:text-sky-300"
                    : "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300"
              }`}
              title={`meta 置信层（综合分+相位+筹码+红线 → 三档）：\n${item.confidence.reasons.join("；")}`}
            >
              {item.confidence.label}
            </span>
          )}
          <div className="flex flex-1 gap-1">
            {SUB_LABELS.map(([key, label]) => {
              const v = item.sub_scores?.[key];
              return (
                <div key={key} className="flex-1" title={`${label}：${item.basisRows.find((r) => r.label === label)?.value ?? "--"}`}>
                  <div className="h-1 w-full overflow-hidden rounded bg-zinc-100 dark:bg-zinc-800">
                    <div className="h-full rounded bg-sky-500/80" style={{ width: `${v ?? 50}%` }} />
                  </div>
                  <div className="mt-0.5 text-center text-[9px] text-zinc-600 dark:text-zinc-400">{label}</div>
                </div>
              );
            })}
          </div>
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
            <Chip
              text={`${item.theme}${item.stage ? ` · ${item.stage}` : ""}`}
              title="所属题材与题材天梯阶段（启动/发酵/高潮/分歧/退潮）——同一个梯队角色在不同阶段价值不同"
              className="border-zinc-300 text-zinc-600 dark:border-zinc-600 dark:text-zinc-300"
            />
          )}
          {item.strengthTier && <Chip text={item.strengthTier} title="题材强度档" />}
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
                  : "border-red-500/50 bg-red-500/10 text-red-700 dark:text-red-300"
              }
            />
          )}
          {item.chipSignal?.signal === "distribution_warning" && (
            <Chip
              text="派发警示"
              title={`筹码形态警示（CYQ 近似口径）：${item.chipSignal.reasons.join("；")}`}
              className="border-red-500/50 bg-red-500/10 text-red-700 dark:text-red-300"
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

      {/* 判定（辨识度/确定性，仅盘中名单）：三态，依据悬停可见 */}
      {(item.distinctiveness || item.certainty) && (
        <div className="mt-2 flex flex-wrap gap-1">
          {item.distinctiveness && (
            <JudgeChip label="辨识度" level={item.distinctiveness.level} basis={item.distinctiveness.basis} />
          )}
          {item.certainty && <JudgeChip label="确定性" level={item.certainty.level} basis={item.certainty.basis} />}
        </div>
      )}

      {/* 入选原因：「为什么选它」。
          盘前 = 六维 basis 摘要（一票否决显式标红；筹码为附注维度不进权重）；
          盘中 = 入选理由（pick_basis，仅分层名单）+ 涨停原因（reason，同花顺官方原串）。
          ⚠️ 必须带标题行（2026-09-10 用户反馈「盘前选择怎么没有入选原因」）：盘前的依据
          本就是七行维度依据，但没有标题时看起来像「这卡没有入选原因」——数据没丢，
          丢的是口径。标题两侧统一，用户才能一眼对上「入选」这件事。
          两者皆无时**整节不渲染**——不拿「入选 —」把「本就没有这个维度」伪装成「没取到」。 */}
      {(item.basisRows.length > 0 || item.vetoes.length > 0) && (
        <div className="mt-2 space-y-0.5 rounded-lg border border-zinc-100 p-2 text-[11px] leading-relaxed dark:border-zinc-800">
          <div className="text-[10px] font-medium text-zinc-600 dark:text-zinc-400">入选原因</div>
          {item.basisRows.map((r) => (
            <Row key={r.label} k={r.label} v={r.value} />
          ))}
          {item.vetoes.map((v) => (
            <div key={v} className="text-red-700 dark:text-red-500">
              ⚠ {v}
            </div>
          ))}
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
            </span>
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
        <div className="mt-2 space-y-1 rounded-lg border border-zinc-100 p-2 text-[11px] dark:border-zinc-800">
          {item.stopLoss && (
            <div className="flex flex-wrap gap-1.5" title={item.stopLoss.basis}>
              <span className="shrink-0 text-zinc-600 dark:text-zinc-400">止损参考</span>
              <span className="font-mono tabular-nums text-red-700 dark:text-red-500">
                {fmt(item.stopLoss.price)}（-{item.stopLoss.pct}%）
              </span>
            </div>
          )}
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
            <div>
              <span className="text-zinc-600 dark:text-zinc-400">失效条件</span>
              {item.invalidations.slice(0, 3).map((v) => (
                <div key={v} className="text-zinc-600 dark:text-zinc-400">
                  · {v}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 关联消息（仅盘前名单有） */}
      {item.relatedEvents.length > 0 && (
        <div className="mt-2 border-t border-zinc-100 pt-1.5 text-[11px] dark:border-zinc-800/60">
          <span className="text-zinc-600 dark:text-zinc-400">关联消息：</span>
          {item.relatedEvents.map((e) => (
            <div key={e} className="text-zinc-600 dark:text-zinc-300">
              · {e}
            </div>
          ))}
        </div>
      )}

      {/* 口径注记：解释「本卡为什么缺某几节」（仅盘中路径需要——缺的维度属收盘口径） */}
      {item.caliberNote && (
        <p className="mt-2 border-t border-zinc-100 pt-1.5 text-[10px] leading-relaxed text-zinc-600 dark:text-zinc-400 dark:border-zinc-800/60">
          {item.caliberNote}
        </p>
      )}

      {/* 跨模块入口（2026-09-09 需求 3）：事件弹窗 / 消息·资金·梯队跳转 */}
      <CardEntryRow symbol={item.symbol} name={item.name} theme={item.theme ?? null} />
    </CardShell>
  );
}

/** 空仓闸门横幅：情绪转弱时主动提示规避（红线 3：只提示，不下指令）。
 *
 *  分档（2026-09-10 用户拍板）：撤除档（相位级信号/多信号叠加）撤买入区间 + 卡上标「仅观察」；
 *  提示档（单条量化阈值擦线）只提示、**保留买入区间**。两者横幅都在——差别在标的层，
 *  且必须写在横幅上，否则用户无法从界面看出「这次能不能按区间参与」。
 *  读 `strip_buy_range`（后端算好带出），不在前端重算规则。
 */
export function StandAsideBanner({ gate }: { gate: StandAsideGate }) {
  if (!gate.stand_aside) return null;
  const strong = gate.level === "strong";
  const stripped = gate.strip_buy_range;
  return (
    <div
      role="alert"
      className={`shrink-0 rounded-lg border px-3 py-2 text-xs ${
        strong
          ? "border-red-500/50 bg-red-500/10 text-red-700 dark:text-red-300"
          : "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300"
      }`}
    >
      <div className="font-medium">⚠ {gate.advice}</div>
      <ul className="mt-1 space-y-0.5">
        {gate.reasons.map((r) => (
          <li key={r}>· {r}</li>
        ))}
      </ul>
      {stripped !== undefined && (
        <div className="mt-1 font-medium">
          {stripped
            ? "→ 本次已撤除买入区间（下方标的仅观察，记录保留供复盘）"
            : "→ 本次保留买入区间：按「控制仓位、减少出手频率」酌情执行，非空仓信号"}
        </div>
      )}
      {gate.disclaimer && <div className="mt-1 text-[10px] opacity-70">{gate.disclaimer}</div>}
    </div>
  );
}
