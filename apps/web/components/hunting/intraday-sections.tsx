"use client";

import { useState } from "react";

import { ConceptDetailModal } from "@/components/concept-detail-modal";
import { JUMP_PILL_CLASS, JumpLink } from "@/components/ui/jump-link";
import { MasonryColumns } from "@/components/masonry-columns";
import { WatchLedgerPanel } from "@/components/hunting/watch-ledger-panel";
import { PickCard, fromIntradayStock } from "@/components/picks/pick-card";
import { StockLink } from "@/components/stock-link";
import { pctColor, pctText, timeText, winRateColor } from "@/lib/format";
import { themesUrl } from "@/lib/routing";
import type {
  BriefAlert,
  BriefDirection,
  IntradayOpportunities,
  IntradayReviewStats,
  MorningBrief,
  Climate,
  OvernightBias,
  OvernightBiasEvidence,
  OpportunityTheme,
  WatcherState,
} from "@/lib/api";

/**
 * 猎场「盘中跟踪」侧的全部展示区块（2026-09-08 猎场批次③从 /intraday/page.tsx 迁出）。
 *
 * 内容与原页完全一致（盘前简报方向卡 / watcher 状态 / 提醒 / 对照表 / 近 30 日统计 /
 * 题材机会手风琴），仅两处按审查 §3.2 增强：
 * 1. IntradayThemeRow 整行 hover 变色（原无任何 hover 反馈）；
 * 2. 展开后题材内个股由行式表格升级为 PickCard 瀑布流（与精选同一张卡，2026-09-10 两卡合并；
 *    判定徽标/入选理由直接落在卡上）。
 * 三态纪律与口径注记原样保留；全部输出为模拟跟踪，不构成买卖建议。
 */

const OUTCOME_TONE: Record<string, string> = {
  发酵: "text-up-ink dark:text-up",
  半发酵: "text-amber-800 dark:text-amber-400",
  证伪: "text-down-ink dark:text-down",
  无波动: "text-zinc-600 dark:text-zinc-400",
};

const FAILURE_LABELS: Record<string, string> = {
  逻辑失效: "盘前逻辑未兑现",
  阈值过敏: "确认后即回撤",
  数据缺失误导: "缺数据不可信",
  环境突变: "环境转退潮/冰点",
};

export function outcomeTone(outcome: string): string {
  return OUTCOME_TONE[outcome] ?? "text-zinc-600 dark:text-zinc-400";
}

const STAGE_TONE: Record<string, string> = {
  启动: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  发酵: "bg-up/10 text-up-ink dark:text-up",
  高潮: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
  分歧: "bg-amber-500/10 text-amber-800 dark:text-amber-300",
  退潮: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400",
};

/* ---------------------------------------------------------------- 当前机会 */

/**
 * 题材机会卡（手风琴）：头部是结论（阶段+强度+梯队概况），展开是个股瀑布流。
 *
 * 命名消歧（2026-09-11 冗余清理）：原名为 `ThemeCardView`，与
 * `components/theme-card.tsx` 导出的同名组件**完全是两个组件**（那边是盘面页
 * 题材榜的卡片，props 为 card/rank/strength）。同名不同物会让 search 结果
 * 无法区分调用点，故改为 IntradayThemeRow。
 */
export function IntradayThemeRow({
  t,
  expanded,
  onToggle,
}: {
  t: OpportunityTheme;
  expanded: boolean;
  onToggle: () => void;
}) {
  const stage = t.stage ?? "未知";
  const [detailOpen, setDetailOpen] = useState(false);
  return (
    <div className="rounded-xl border border-zinc-200 p-3 text-xs transition-colors hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-900/50">
      {/* L10（切片 E）：题材页 ↗ 与展开按钮同级（button 内不能嵌 a），点题材名仍是展开/收起 */}
      <div className="flex w-full items-center gap-2">
        <button onClick={onToggle} className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-left">
          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">{t.theme}</span>
          {/* 机会三层（需求 4）：今日最强/悄悄启动/孕育待发酵——择时先分层再定档 */}
          {t.opportunity_layer && (
            <span
              className={`rounded px-1.5 py-0.5 text-[10px] ${
                t.opportunity_layer === "today_strongest"
                  ? "bg-rose-500/10 text-rose-700 dark:text-rose-300"
                  : t.opportunity_layer === "quiet_starting"
                    ? "bg-amber-500/10 text-amber-800 dark:text-amber-300"
                    : "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400"
              }`}
              title={`机会分层（规则可解释）：${t.layer_basis ?? ""}`}
            >
              {t.opportunity_layer === "today_strongest"
                ? "今日最强"
                : t.opportunity_layer === "quiet_starting"
                  ? "悄悄启动"
                  : "孕育待发酵"}
            </span>
          )}
          {/* 官方概念徽标：只显示归属的大概念（命中数最高），细分在弹窗里 */}
          {(t.official_matches ?? []).length > 0 && (
            <span
              className="rounded bg-sky-500/10 px-1.5 py-0.5 text-[10px] text-sky-700 dark:text-sky-300"
              title={`官方概念挂靠：${(t.official_matches ?? []).map((m) => m.name).join("、")}`}
            >
              官方·{(t.official_matches ?? [])[0]?.name}
            </span>
          )}
          <span className={`rounded px-1.5 py-0.5 text-[10px] ${STAGE_TONE[stage] ?? "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400"}`} title={(t.stage_basis || []).join("；")}>
            {stage}
          </span>
          <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400" title={t.tier_basis ?? ""}>
            {t.strength_tier ?? "—"}
          </span>
          {t.strength_score != null && (
            <span className="font-mono tabular-nums text-zinc-600 dark:text-zinc-400" title={`题材强度 ${t.strength_score}`}>
              {t.strength_score} 分
            </span>
          )}
          <span className="text-[11px] text-zinc-600 dark:text-zinc-400" title={`梯队：最高 ${t.max_boards ?? "?"} 板，涨停 ${t.limit_up_count ?? "?"} 家${t.has_succession ? "，梯队有接续" : ""}`}>
            {t.max_boards ?? "?"} 板 · {t.limit_up_count ?? "?"} 家涨停
            {t.has_succession === false && " · 梯队断层"}
          </span>
          <span className="ml-auto text-zinc-600 dark:text-zinc-400">
            {expanded ? "收起 ▲" : `${t.participants?.length ?? 0} 只可参与候选 ▼`}
          </span>
        </button>
        <JumpLink
          href={themesUrl(t.theme)}
          title="打开题材梯队看板（盘面页 · 题材梯队 tab），聚焦该题材"
        >
          题材页 ↗
        </JumpLink>
        {t.catalog_code && (
          <button
            onClick={() => setDetailOpen(true)}
            className={JUMP_PILL_CLASS}
            title="查看官方成分全量（与同花顺逐符号一致）与涨停细分"
          >
            成分 ↗
          </button>
        )}
      </div>
      {detailOpen && t.catalog_code && (
        <ConceptDetailModal code={t.catalog_code} name={t.theme} onClose={() => setDetailOpen(false)} />
      )}
      {t.risks.length > 0 && !expanded && (
        <p className="mt-1 text-[11px] text-amber-800 dark:text-amber-300" title={t.risks.join("；")}>
          ⚠ {t.risks[0]}
        </p>
      )}
      {expanded && (
        <>
          {t.stage_basis.length > 0 && (
            <p className="mt-1.5 text-[11px] text-zinc-600 dark:text-zinc-400">阶段依据：{t.stage_basis.join("；")}</p>
          )}
          {t.health_note && <p className="mt-1 text-[11px] text-zinc-600 dark:text-zinc-400">{t.health_note}</p>}
          {t.risks.length > 0 && (
            <p className="mt-1 text-[11px] text-amber-800 dark:text-amber-300">风险：{t.risks.join("；")}</p>
          )}
          {t.stocks.length > 0 || (t.participants?.length ?? 0) > 0 ? (
            <div className="mt-2 space-y-3">
              {/* ① 可参与候选（2026-09-15 猎场口径）：题材内**尚未涨停**、报价可成交的
                  联动个股——这才是"可以买"的那一组。空列表要给出**原因**（没挖 / 挖空了
                  是两回事），否则用户无法判断"今天没机会"还是"系统没干活"。 */}
              <div>
                <p className="mb-1.5 flex flex-wrap items-baseline gap-2 text-[11px] text-zinc-600 dark:text-zinc-400">
                  <span className="font-medium text-zinc-700 dark:text-zinc-300">可参与候选</span>
                  <span>
                    {t.participants?.length ?? 0} 只 · 尚未涨停、报价可成交
                  </span>
                </p>
                {(t.participants?.length ?? 0) > 0 ? (
                  <MasonryColumns gap="gap-2">
                    {(t.participants ?? []).map((s) => (
                      <PickCard key={s.symbol} item={fromIntradayStock(s)} />
                    ))}
                  </MasonryColumns>
                ) : (
                  <p className="text-[11px] text-zinc-600 dark:text-zinc-400">
                    {t.participants_note ?? "无合格可参与候选。"}
                  </p>
                )}
              </div>

              {/* ② 涨停梯队（参考信息）：已封板/开盘即涨停 —— **当日买不进**，
                  只用来回答"资金集中在哪个方向"。刻意沉在下半区并加边注，
                  避免与上面的候选混为一谈（这正是本轮口径变更要修的病）。 */}
              {t.stocks.length > 0 && (
                <div className="rounded-lg border border-dashed border-amber-500/40 p-2">
                  <p className="mb-1.5 flex flex-wrap items-baseline gap-2 text-[11px] text-amber-800 dark:text-amber-300">
                    <span className="font-medium">涨停梯队 · 仅参考</span>
                    <span>
                      {t.stocks.length} 只 · 当日封过涨停板，逐只标注可参与性（开板回落者其实可成交）
                    </span>
                  </p>
                  <MasonryColumns gap="gap-2">
                    {t.stocks.map((s) => (
                      <PickCard key={s.symbol} item={fromIntradayStock(s)} />
                    ))}
                  </MasonryColumns>
                </div>
              )}
            </div>
          ) : (
            <p className="mt-2 text-[11px] text-zinc-600 dark:text-zinc-400">该题材暂无梯队成员。</p>
          )}
        </>
      )}
    </div>
  );
}

/** 盘中机会区块：结论可解释、可追溯；不构成买卖建议。 */
export function OpportunitySection({
  opps,
  expanded,
  onToggle,
}: {
  opps: IntradayOpportunities;
  expanded: string | null;
  onToggle: (theme: string) => void;
}) {
  // 板块权限挡下的容器成分只数：>0 时口径行要说明——「候选为什么这么少」
  // 与「没有候选」是两件事，页面必须能区分（三态纪律）
  const boardBlocked = opps.linkage_stats?.excluded_board ?? 0;
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
          当前机会（题材 → 个股，证据池 {opps.trade_date ?? "—"}）
        </h2>
        {/* 涨停家数/最高板/人气榜警示已上移至「今日行情」概览带（2026-09-04） */}
      </div>
      {opps.themes.length === 0 ? (
        <div className="rounded-xl border border-zinc-200 p-4 text-xs text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
          暂无题材机会（当日无涨停数据或题材未成形）。
        </div>
      ) : (
        <div className="space-y-2">
          {opps.themes.map((t) => (
            <IntradayThemeRow key={t.theme} t={t} expanded={expanded === t.theme} onToggle={() => onToggle(t.theme)} />
          ))}
        </div>
      )}
      {opps.caveats.length > 0 && (
        <p className="text-[10px] text-zinc-600 dark:text-zinc-400">口径：{opps.caveats.join("；")}</p>
      )}
      <p className="text-[10px] text-zinc-600 dark:text-zinc-400">
        板块权限：账户只开沪深主板（创业板/科创板/北交所/B 股不进候选与参考区）
        {boardBlocked > 0 ? `，本日已挡下 ${boardBlocked} 只容器成分` : ""}；
        可参与候选＝题材内尚未涨停、报价可成交的联动个股（联动确定性＝题材基座×距封板跑道）；
        涨停梯队仅作题材集中度的参考信息——已封板/开盘即涨停者当日买不进。
        辨识度＝人气×高度×角色，确定性＝题材阶段基座×封板质量修正，两者独立判定不合并打分；
        判定依据悬停可见、等级可回放。仅模拟跟踪，不构成买卖建议。
      </p>
      {/* 跟踪台账（猎场批次 A）：入选即登记、收盘清算、逐股判定与历史统计 */}
      <WatchLedgerPanel />
    </section>
  );
}

/* ---------------------------------------------------------------- 今日行情 */

export function MarketOverviewStrip({ opps }: { opps: IntradayOpportunities | null }) {
  const brokenLadder = opps
    ? opps.themes.filter((t) => t.has_succession === false).length
    : 0;
  const stats: [string, string | number, string][] = [
    ["涨停家数", opps?.summary.limit_up_total ?? "--", "ths 封单法口径"],
    ["最高连板", opps ? `${opps.summary.market_max_boards ?? "--"} 板` : "--", "全市场空间板高度"],
    ["题材机会", opps ? `${opps.themes.length} 个` : "--", "当日有候选个股的题材数"],
    ["梯队断层", opps ? `${brokenLadder} 个` : "--", "has_succession=false 的题材（接续风险）"],
  ];
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
      {stats.map(([label, value, tip]) => (
        <div
          key={label}
          className="rounded-lg border border-zinc-200 px-2.5 py-1.5 dark:border-zinc-800"
          title={tip}
        >
          <span className="text-[11px] text-zinc-600 dark:text-zinc-400">{label}</span>
          <div className="font-mono text-sm font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
            {value}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- 盘前简报 */

export function EnvStrip({ brief }: { brief: MorningBrief }) {
  const env = brief.env;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
      {env.phase && (
        <span className="rounded border border-zinc-300 px-1.5 py-0.5 text-[10px] dark:border-zinc-700">
          相位 {env.phase}
        </span>
      )}
      {env.promo_percentile != null && (
        <span className="text-[10px]" title="1进2 晋级率的历史分位（0-100，P0-3b 校准）——是分位不是百分比">
          晋级率分位 P{env.promo_percentile}
        </span>
      )}
      {env.pool_date && <span className="text-[10px]">证据池 {env.pool_date}</span>}
      <span className="text-[10px]">
        生成于 {timeText(brief.generated_at)}（{brief.trigger === "schedule" ? "调度" : "手动"}）
      </span>
      {brief.missing.map((m) => (
        <span key={m} className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-800 dark:text-amber-300">
          ⚠ {m}
        </span>
      ))}
    </div>
  );
}

export function MacroCalendar({ brief }: { brief: MorningBrief }) {
  const note = brief.macro_note;
  const events = brief.macro_events;
  // 三态：null=源不可得（Envelope 的 missing 里已有 ⚠ 标）、[]=当日确无高信号事件
  if (!note && !events) return null;
  return (
    <div className="space-y-1 rounded-lg border border-sky-500/25 bg-sky-500/5 px-2.5 py-2 text-[11px] leading-5">
      {note && <div className="text-sky-700 dark:text-sky-300">{note}</div>}
      {events && events.length > 0 && (
        <>
          <div className="text-[10px] text-zinc-600 dark:text-zinc-400">
            今日宏观日历（{events.length} 项 · 高信号筛选，非全量）
          </div>
          <ul className="space-y-0.5">
            {events.map((e) => (
              <li key={e.line} className="text-zinc-700 dark:text-zinc-200" title={e.event}>
                {e.line}
              </li>
            ))}
          </ul>
        </>
      )}
      {events && events.length === 0 && (
        <div className="text-[10px] text-zinc-600 dark:text-zinc-400">
          今日无 CPI/PPI/GDP/PMI/社融/非农 等高信号宏观发布
        </div>
      )}
    </div>
  );
}

const STANCE_TONE: Record<string, string> = {
  走强: "border-up/40 bg-up/10 text-up-ink dark:text-up",
  中性: "border-zinc-300 bg-zinc-500/10 text-zinc-600 dark:border-zinc-700 dark:text-zinc-300",
  承压: "border-down/40 bg-down/10 text-down-ink dark:text-down",
};

const DIR_CHIP: Record<string, string> = {
  偏多: "bg-up/10 text-up-ink dark:text-up",
  偏空: "bg-down/10 text-down-ink dark:text-down",
  无偏向: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400",
  未参与: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400",
  仅记录: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
};

function fmtChange(e: OvernightBiasEvidence): string {
  if (e.change === null) return "—";
  const sign = e.change > 0 ? "+" : "";
  return e.unit === "bp" ? `${sign}${e.change.toFixed(1)}bp` : `${sign}${e.change.toFixed(2)}%`;
}

/**
 * P1-34 隔夜海外四输入 → 大盘方向偏向。
 *
 * 三态：`bias` 为 null = 整块不可用（简报顶部 missing 已有 ⚠）；`bias.stance` 为 null =
 * 输入不足「未判定」——**与「中性」严格区分**，不得渲染成中性。
 * 红线 3：输出恒为「偏向 + 依据 + 失效条件」，页脚带不构成买卖建议。
 */
export function OvernightBiasBlock({ bias }: { bias?: OvernightBias | null }) {
  if (!bias) return null;
  const stance = bias.stance;
  const tone = stance
    ? STANCE_TONE[stance]
    : "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300";
  const scoreText = bias.score === null ? null : `${bias.score > 0 ? "+" : ""}${bias.score}`;
  return (
    <div className="space-y-1.5 rounded-lg border border-zinc-200 bg-zinc-50/60 px-2.5 py-2 text-[11px] leading-5 dark:border-zinc-800 dark:bg-zinc-900/40">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] text-zinc-600 dark:text-zinc-400">隔夜海外 · 今日偏向</span>
        <span
          className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${tone}`}
          title={bias.unjudged_reason ?? undefined}
        >
          {stance ?? "未判定"}
        </span>
        {scoreText && (
          <span
            className="font-mono text-[10px] tabular-nums text-zinc-600 dark:text-zinc-400"
            title={`规则分 ${scoreText} / ${bias.score_range}`}
          >
            {scoreText} / {bias.score_range}
          </span>
        )}
      </div>
      {stance === null && bias.unjudged_reason && (
        <div className="text-[10px] text-amber-800 dark:text-amber-300">{bias.unjudged_reason}</div>
      )}
      <ul className="space-y-0.5">
        {bias.evidence.map((e) => {
          const dir = e.skip_reason
            ? "未参与"
            : !e.weighted
              ? "仅记录"
              : e.bullish === true
                ? "偏多"
                : e.bullish === false
                  ? "偏空"
                  : "无偏向";
          return (
            <li key={e.key} className="flex flex-wrap items-center gap-1.5" title={e.note}>
              <span className="text-zinc-700 dark:text-zinc-200">{e.label}</span>
              <span className="font-mono text-[10px] tabular-nums text-zinc-600 dark:text-zinc-400">
                {e.date ? e.date.slice(5) : ""} {fmtChange(e)}
              </span>
              <span className={`rounded px-1 py-px text-[10px] ${DIR_CHIP[dir]}`}>{dir}</span>
              {e.skip_reason && (
                <span className="text-[10px] text-amber-800 dark:text-amber-300">{e.skip_reason}</span>
              )}
            </li>
          );
        })}
      </ul>
      {bias.missing.length > 0 && (
        <div className="text-[10px] text-amber-800 dark:text-amber-300">
          输入缺失：{bias.missing.join("；")}
        </div>
      )}
      <div className="text-[10px] text-zinc-600 dark:text-zinc-400">{bias.horizon_note}</div>
      <details className="text-[10px] text-zinc-600 dark:text-zinc-400">
        <summary className="cursor-pointer select-none">失效条件（{bias.invalidation.length}）</summary>
        <ul className="mt-0.5 list-disc space-y-0.5 pl-4">
          {bias.invalidation.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      </details>
      <div className="text-[10px] text-zinc-600 dark:text-zinc-400">{bias.disclaimer}</div>
    </div>
  );
}

const CLIMATE_STATE: Record<string, { label: string; tone: string }> = {
  el_nino: {
    label: "厄尔尼诺",
    tone: "border-orange-500/40 bg-orange-500/10 text-orange-800 dark:text-orange-300",
  },
  la_nina: {
    label: "拉尼娜",
    tone: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  },
  neutral: {
    label: "中性",
    tone: "border-zinc-400/40 bg-zinc-500/10 text-zinc-600 dark:text-zinc-300",
  },
};

const CLIMATE_STRENGTH: Record<string, string> = {
  weak: "弱",
  moderate: "中等",
  strong: "强",
  very_strong: "超强",
};

/**
 * P1-32 气候一阶相位（ENSO/ONI）。
 *
 * 三态：`climate` 为 null = 源不可得 → **整块不渲染**（调用方已判）；
 * `state` 为 null = 输入不足「未判定」——**与「中性」严格区分**。
 * 红线 3：候选题材只作线索，必须同时渲染 `timing_note`（无领先性）与
 * `empirical_verdict`（人工链未获数据支持），否则等于把假设当结论。
 */
export function ClimateBlock({ climate }: { climate?: Climate | null }) {
  if (!climate) return null;
  const st = climate.state;
  const meta = st ? CLIMATE_STATE[st] : null;
  const strengthLabel = climate.strength ? CLIMATE_STRENGTH[climate.strength] : null;
  const alertLabel = climate.alert ? CLIMATE_STATE[climate.alert]?.label : null;
  return (
    <div className="space-y-1.5 rounded-lg border border-zinc-200 bg-zinc-50/60 px-2.5 py-2 text-[11px] leading-5 dark:border-zinc-800 dark:bg-zinc-900/40">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] text-zinc-600 dark:text-zinc-400">气候相位 · ENSO/ONI</span>
        <span
          className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${
            meta
              ? meta.tone
              : "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300"
          }`}
          title={climate.unjudged_reason ?? undefined}
        >
          {meta ? meta.label : "未判定"}
          {strengthLabel ? ` · ${strengthLabel}` : ""}
        </span>
        {climate.latest && (
          <span className="font-mono text-[10px] tabular-nums text-zinc-600 dark:text-zinc-400">
            {climate.latest.season} {climate.latest.year}{" "}
            {climate.latest.anom > 0 ? "+" : ""}
            {climate.latest.anom.toFixed(2)}
          </span>
        )}
      </div>
      {st === null && climate.unjudged_reason && (
        <div className="text-[10px] text-amber-800 dark:text-amber-300">
          {climate.unjudged_reason}
        </div>
      )}
      {alertLabel && (
        <div className="text-[10px] text-amber-800 dark:text-amber-300">
          <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1 py-px">
            预警态
          </span>{" "}
          已连续越线 {climate.consecutive} 个季，未满 {climate.persist_seasons} 季 ⇒
          尚未构成官方「{alertLabel}确立」
        </div>
      )}
      {climate.series.length > 0 && (
        <div className="font-mono text-[10px] tabular-nums text-zinc-600 dark:text-zinc-400">
          近几季：{climate.series.map((s) => `${s.season} ${s.anom > 0 ? "+" : ""}${s.anom.toFixed(2)}`).join("　")}
        </div>
      )}
      {climate.candidate_links.length > 0 && (
        <div className="text-[10px] text-zinc-600 dark:text-zinc-300">
          候选题材（人工映射 · 未获数据支持）：
          {climate.candidate_links.map((l) => l.target).join("、")}
        </div>
      )}
      <div className="text-[10px] text-zinc-600 dark:text-zinc-400">{climate.timing_note}</div>
      <details className="text-[10px] text-zinc-600 dark:text-zinc-400">
        <summary className="cursor-pointer select-none">实证判读（人工链未获支持）</summary>
        <div className="mt-0.5 pl-4">{climate.empirical_verdict}</div>
      </details>
      <div className="text-[10px] text-zinc-600 dark:text-zinc-400">{climate.disclaimer}</div>
    </div>
  );
}

export function DirectionCard({ d }: { d: BriefDirection }) {
  const rv = d.review;
  return (
    <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">{d.direction}</span>
        {d.defensive && (
          <span className="rounded border border-sky-500/40 bg-sky-500/10 px-1 py-0.5 text-[10px] text-sky-700 dark:text-sky-300">
            防守
          </span>
        )}
        <span className="font-mono tabular-nums text-zinc-600 dark:text-zinc-400" title={d.basis}>
          {d.score} 分
        </span>
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] ${
            d.entry_mode === "追涨"
              ? "bg-up/10 text-up-ink dark:text-up"
              : d.entry_mode === "追涨减半"
                ? "bg-amber-500/10 text-amber-800 dark:text-amber-300"
                : d.entry_mode === "潜伏"
                  ? "bg-violet-500/10 text-violet-700 dark:text-violet-300"
                  : "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400"
          }`}
          title={d.entry_basis}
        >
          {d.entry_mode}
        </span>
        {rv && (
          <span className={`ml-auto font-medium ${outcomeTone(rv.outcome)}`}>
            盘后对照：{rv.outcome}
            {rv.failure_class ? ` · ${FAILURE_LABELS[rv.failure_class] ?? rv.failure_class}` : ""}
          </span>
        )}
      </div>
      <p className="mt-1.5 text-zinc-600 dark:text-zinc-400">{d.logic}</p>
      {rv && (
        <p className="mt-1 text-[11px] text-zinc-600 dark:text-zinc-400">
          实际：板块 {rv.actual_pct == null ? "unknown" : `${pctText(rv.actual_pct)}`}
          {" · "}涨停 {rv.actual_limit_up ?? "?"} 家 / 最高 {rv.actual_max_boards ?? "?"} 板
          {rv.actual_leader ? ` · 龙头 ${rv.actual_leader}` : ""}
          {" · "}条件 {rv.closing_met}/{rv.closing_total}
          {rv.closing_unknown > 0 ? `（unknown ${rv.closing_unknown}）` : ""}
          {rv.note ? ` · ${rv.note}` : ""}
        </p>
      )}
      {d.pool.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {d.pool.map((p) => (
            <StockLink
              key={p.symbol}
              symbol={p.symbol}
              className="bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-600 no-underline dark:bg-zinc-800 dark:text-zinc-300"
              title={`${p.name || p.symbol} · ${p.role}${p.boards ? ` · ${p.boards} 板` : ""} — 点击进工作台`}
            >
              {p.name || p.symbol}
              {p.boards >= 2 ? ` ${p.boards}板` : ""}
              <span className="ml-1 text-zinc-600 dark:text-zinc-400">{p.role}</span>
            </StockLink>
          ))}
        </div>
      )}
      <details className="mt-2 text-[11px] text-zinc-600 dark:text-zinc-400">
        <summary className="cursor-pointer select-none text-zinc-600 dark:text-zinc-400">触发 / 证伪条件</summary>
        <ul className="mt-1 space-y-0.5">
          {d.trigger_conditions.map((c) => (
            <li key={c}>确认 · {c}</li>
          ))}
          {d.falsify_conditions.map((c) => (
            <li key={c}>证伪 · {c}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

/* ---------------------------------------------------------------- 盘中提醒 */

export function AlertItem({ a }: { a: BriefAlert }) {
  const ret = a.meta?.returns;
  const isConfirm = a.kind === "confirm";
  return (
    <div className="rounded-lg border border-zinc-200 p-2.5 dark:border-zinc-800">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
            isConfirm ? "bg-up/10 text-up-ink dark:text-up" : "bg-down/10 text-down-ink dark:text-down"
          }`}
        >
          {isConfirm ? "确认" : "证伪"}
        </span>
        <span className="font-medium text-zinc-900 dark:text-zinc-50">{a.direction}</span>
        {a.symbol && (
          <StockLink symbol={a.symbol} className="text-zinc-600 dark:text-zinc-400" title="点击看该股详情">
            {a.name}（{a.symbol}）
          </StockLink>
        )}
        <span className="ml-auto text-[10px] text-zinc-600 dark:text-zinc-400">{timeText(a.at)}</span>
        {isConfirm && ret && (ret.t1_return != null || ret.t3_return != null) && (
          <span className="text-[10px] text-zinc-600 dark:text-zinc-400">
            T+1{" "}
            <span className={`font-mono ${pctColor(ret.t1_return)}`}>
              {ret.t1_return != null ? pctText(ret.t1_return) : "—"}
            </span>
            {" · "}T+3{" "}
            <span className={`font-mono ${pctColor(ret.t3_return)}`}>
              {ret.t3_return != null ? pctText(ret.t3_return) : "未到期"}
            </span>
          </span>
        )}
      </div>
      <pre className="mt-1.5 whitespace-pre-wrap font-sans text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-300">
        {a.text}
      </pre>
    </div>
  );
}

/* ---------------------------------------------------------------- 对照与统计 */

export function ReviewOutcomeTable({ reviewed }: { reviewed: BriefDirection[] }) {
  return (
    <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
      <table className="w-full">
        <thead>
          <tr className="text-zinc-600 dark:text-zinc-400">
            <th className="text-left font-normal">方向</th>
            <th className="text-left font-normal">盘前模式</th>
            <th className="text-left font-normal">盘后分类</th>
            <th className="text-right font-normal">实际板块</th>
            <th className="text-left font-normal">误判归因 / 备注</th>
          </tr>
        </thead>
        <tbody>
          {reviewed.map((d) => {
            const rv = d.review!;
            return (
              <tr key={d.direction} className="border-t border-zinc-100 dark:border-zinc-800/60">
                <td className="py-1">{d.direction}</td>
                <td className="text-zinc-600 dark:text-zinc-400">{d.entry_mode}</td>
                <td className={`font-medium ${outcomeTone(rv.outcome)}`}>{rv.outcome}</td>
                <td className={`text-right font-mono tabular-nums ${pctColor(rv.actual_pct)}`}>
                  {rv.actual_pct != null ? pctText(rv.actual_pct) : "unknown"}
                </td>
                <td className="text-zinc-600 dark:text-zinc-400">
                  {rv.failure_class ? FAILURE_LABELS[rv.failure_class] ?? rv.failure_class : "—"}
                  {rv.note ? ` · ${rv.note}` : ""}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** 极简 SVG 柱带：每日 发酵/半发酵/证伪/无波动 堆叠。样本 ≥2 天才渲染（调用方守卫）。 */
function DailyCurve({ daily }: { daily: IntradayReviewStats["daily"] }) {
  const W = 660;
  const H = 90;
  const bw = Math.max(6, Math.min(18, (W - 8) / daily.length - 4));
  const gap = (W - 8) / daily.length;
  const max = Math.max(1, ...daily.map((x) => Math.max(1, x.reviewed)));
  return (
    <svg viewBox={`0 0 ${W} ${H + 14}`} className="w-full" role="img" aria-label="每日方向对照堆叠柱">
      {daily.map((x, i) => {
        const segs: [string, number][] = [
          // 方向色必须取本仓权威 token：原先写 `var(--color-up, #16a34a)` /
          // `var(--color-down, #dc2626)`，而这两个变量**全仓从未定义**
          // （globals.css 定义的是 --accent / --accent-down）⇒ 浏览器必然采用
          // fallback，于是「发酵」（正向）被画成**绿**、「证伪」（负向）被画成**红**，
          // 与 A 股红涨绿跌及 tailwind.config 的 up/down 定义**完全相反**。
          // 改用字面量而非 `var(--accent)`：SVG fill 不参与 Tailwind 的明暗档切换，
          // 这里需要的是亮暗两档都成立的中深色（红 #f43f5e / 绿 #10b981），
          // 与 sparkline.tsx / kline-chart-pro.tsx 的既有取值保持一致。
          ["#f43f5e", x.fermented],
          ["#f59e0b", x.half],
          ["#10b981", x.falsified],
          ["#a1a1aa", x.flat],
        ];
        let y = H;
        const bars = segs
          .filter(([, v]) => v > 0)
          .map(([c, v], j) => {
            const h = (v / max) * (H - 8);
            y -= h;
            return <rect key={j} x={4 + i * gap} y={y} width={bw} height={Math.max(h - 1, 1)} fill={c} rx={1} />;
          });
        return (
          <g key={x.date}>
            {bars}
            <text x={4 + i * gap + bw / 2} y={H + 11} textAnchor="middle" fontSize="8" fill="#a1a1aa">
              {x.date.slice(4, 6)}/{x.date.slice(6, 8)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function StatsPanel({ stats }: { stats: IntradayReviewStats }) {
  const d = stats.directions;
  const t1 = stats.alert_t1;
  const t3 = stats.alert_t3;
  const reviewedDays = stats.daily.filter((x) => x.reviewed > 0);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="text-zinc-600 dark:text-zinc-400">已复盘方向</div>
          <div className="mt-1 font-mono text-lg tabular-nums text-zinc-900 dark:text-zinc-50">{d.total}</div>
          <div className="mt-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">
            发酵 {d.outcomes["发酵"] ?? 0} · 半发酵 {d.outcomes["半发酵"] ?? 0} · 证伪 {d.outcomes["证伪"] ?? 0} · 无波动 {d.outcomes["无波动"] ?? 0}
          </div>
        </div>
        <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="text-zinc-600 dark:text-zinc-400">确认提醒 T+1 胜率</div>
          <div className={`mt-1 font-mono text-lg tabular-nums ${winRateColor(t1.win_rate, "pct")}`}>
            {t1.win_rate != null ? `${t1.win_rate}%` : "—"}
          </div>
          <div className="mt-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">
            样本 {t1.n} · 均值 {t1.avg_return != null ? pctText(t1.avg_return) : "—"}
          </div>
        </div>
        <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="text-zinc-600 dark:text-zinc-400">盈亏比（T+1）</div>
          <div className="mt-1 font-mono text-lg tabular-nums text-zinc-900 dark:text-zinc-50">
            {t1.profit_loss_ratio != null ? t1.profit_loss_ratio : "—"}
          </div>
          <div className="mt-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">
            均盈 {t1.avg_win != null ? pctText(t1.avg_win) : "—"} / 均亏 {t1.avg_loss != null ? pctText(t1.avg_loss) : "—"}
          </div>
        </div>
        <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="text-zinc-600 dark:text-zinc-400">T+3 胜率</div>
          <div className={`mt-1 font-mono text-lg tabular-nums ${winRateColor(t3.win_rate, "pct")}`}>
            {t3.win_rate != null ? `${t3.win_rate}%` : "—"}
          </div>
          <div className="mt-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">样本 {t3.n}（未到期不计）</div>
        </div>
      </div>

      {reviewedDays.length >= 2 && (
        <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
          <div className="mb-2 font-medium text-zinc-900 dark:text-zinc-50">近 30 日方向对照走势</div>
          <DailyCurve daily={reviewedDays} />
        </div>
      )}

      {reviewedDays.length > 0 && (
        <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
          <div className="mb-1 font-medium text-zinc-900 dark:text-zinc-50">逐日对照</div>
          <table className="w-full">
            <thead>
              <tr className="text-zinc-600 dark:text-zinc-400">
                <th className="text-left font-normal">日期</th>
                <th className="text-right font-normal">方向</th>
                <th className="text-right font-normal">发酵</th>
                <th className="text-right font-normal">半发酵</th>
                <th className="text-right font-normal">证伪</th>
                <th className="text-right font-normal">无波动</th>
                <th className="text-right font-normal">提醒</th>
              </tr>
            </thead>
            <tbody>
              {reviewedDays.map((x) => (
                <tr key={x.date} className="border-t border-zinc-100 dark:border-zinc-800/60">
                  <td className="py-1 font-mono text-zinc-600 dark:text-zinc-400">{x.date}</td>
                  <td className="text-right font-mono tabular-nums">{x.reviewed}/{x.directions}</td>
                  <td className="text-right font-mono tabular-nums text-up-ink dark:text-up">{x.fermented}</td>
                  <td className="text-right font-mono tabular-nums text-amber-800 dark:text-amber-400">{x.half}</td>
                  <td className="text-right font-mono tabular-nums text-down-ink dark:text-down">{x.falsified}</td>
                  <td className="text-right font-mono tabular-nums text-zinc-600 dark:text-zinc-400">{x.flat}</td>
                  <td className="text-right font-mono tabular-nums">{x.alerts}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {stats.alerts.length > 0 && (
        <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
          <div className="mb-1 font-medium text-zinc-900 dark:text-zinc-50">确认提醒收益明细（T+1 / T+3，参考价=提醒日收盘）</div>
          {stats.alerts.map((a) => (
            <div key={a.date + a.symbol} className="flex flex-wrap gap-2 border-b border-zinc-100 py-1 last:border-0 dark:border-zinc-800/60">
              <span className="font-mono text-zinc-600 dark:text-zinc-400">{a.date}</span>
              <span>{a.name || a.symbol}</span>
              <span className="text-zinc-600 dark:text-zinc-400">{a.direction}</span>
              <span className={`ml-auto font-mono tabular-nums ${pctColor(a.t1_return)}`}>
                T+1 {a.t1_return != null ? pctText(a.t1_return) : "未到期"}
              </span>
              <span className={`w-20 text-right font-mono tabular-nums ${pctColor(a.t3_return)}`}>
                T+3 {a.t3_return != null ? pctText(a.t3_return) : "—"}
              </span>
            </div>
          ))}
        </div>
      )}
      <p className="text-[10px] text-zinc-600 dark:text-zinc-400">{stats.sample_note}</p>
    </div>
  );
}

/** watcher 状态表（原 /intraday sec-watcher 内容体）。 */
export function WatcherPanel({ watcher, pending }: { watcher: WatcherState | null; pending: boolean }) {
  return (
    <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
      {pending && watcher === null ? (
        <div className="text-zinc-600 dark:text-zinc-400">加载中…</div>
      ) : watcher?.active ? (
        <>
          <div className="mb-2 flex flex-wrap gap-3 text-[11px] text-zinc-600 dark:text-zinc-400">
            <span>已推进 {watcher.beat_count ?? 0} 拍</span>
            <span>启动于 {timeText(watcher.started_at ?? null)}</span>
          </div>
          <table className="w-full">
            <thead>
              <tr className="text-zinc-600 dark:text-zinc-400">
                <th className="text-left font-normal">方向</th>
                <th className="text-right font-normal">拍数</th>
                <th className="text-right font-normal">缺数据拍</th>
                <th className="text-right font-normal">峰值涨幅</th>
                <th className="text-right font-normal">确认</th>
                <th className="text-right font-normal">证伪</th>
                <th className="text-right font-normal">已提醒</th>
              </tr>
            </thead>
            <tbody>
              {(watcher.trackers ?? []).map((t) => (
                <tr key={t.direction} className="border-t border-zinc-100 dark:border-zinc-800/60">
                  <td className="py-1">{t.direction}</td>
                  <td className="text-right font-mono tabular-nums">{t.beats}</td>
                  <td className="text-right font-mono tabular-nums text-zinc-600 dark:text-zinc-400">{t.missing_beats}</td>
                  <td className={`text-right font-mono tabular-nums ${pctColor(t.peak_pct)}`}>
                    {t.peak_pct != null ? pctText(t.peak_pct) : "—"}
                  </td>
                  <td className="text-right">
                    {t.confirmed ? <span className="text-up-ink dark:text-up">是</span> : <span className="text-zinc-600 dark:text-zinc-400">否</span>}
                  </td>
                  <td className="text-right">
                    {t.falsified ? (
                      <span className="text-down-ink dark:text-down" title={t.falsify_triggers.map((x) => x.detail).join("；")}>
                        是
                      </span>
                    ) : (
                      <span className="text-zinc-600 dark:text-zinc-400">否</span>
                    )}
                  </td>
                  <td className="text-right font-mono text-[11px] text-zinc-600 dark:text-zinc-400">
                    {t.alerted_symbols.join("、") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <div className="text-zinc-600 dark:text-zinc-400">
          {watcher?.note ?? "watcher 未启动（后端未运行或开关关闭）——非交易时段属正常"}
        </div>
      )}
    </div>
  );
}
