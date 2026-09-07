"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  generateMorningBrief,
  getIntradayOpportunities,
  getIntradayReview,
  getMorningBriefToday,
  getWatcherState,
  runIntradayReview,
  runWatcherBeat,
  type BriefAlert,
  type BriefDirection,
  type IntradayOpportunities,
  type IntradayReviewStats,
  type MorningBrief,
  type OpportunityJudgement,
  type OpportunityStock,
  type OpportunityTheme,
  type WatcherState,
} from "@/lib/api";
import { workbenchUrlWithBack, themesUrl } from "@/lib/routing";
import { StockLink } from "@/components/stock-link";
import { pctColor, pctText, timeText } from "@/lib/format";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { CardListSkeleton, FadeIn, PageSkeletonFallback, StatGridSkeleton, StatsSkeleton, TableSkeleton } from "@/components/ui/loading";

/**
 * 盘中跟踪（/intraday，选股 2.0 §2 呈现层，批次 B/C）：
 * 当前机会（题材强→弱 + 题材内候选个股辨识度/确定性，2026-09-03）→ 盘前简报（方向 top3
 * + 标的池 + 触发/证伪条件）→ 盘中 watcher 状态与提醒 → 盘后「盘前 vs 实际」对照表 +
 * 近 30 日胜率统计。三节拍共用当日简报文件为唯一事实源。
 * 全页不构成买卖建议；提醒均为模拟跟踪。
 */

const OUTCOME_TONE: Record<string, string> = {
  发酵: "text-up",
  半发酵: "text-amber-500 dark:text-amber-400",
  证伪: "text-down",
  无波动: "text-zinc-400",
};

const FAILURE_LABELS: Record<string, string> = {
  逻辑失效: "盘前逻辑未兑现",
  阈值过敏: "确认后即回撤",
  数据缺失误导: "缺数据不可信",
  环境突变: "环境转退潮/冰点",
};

function outcomeTone(outcome: string): string {
  return OUTCOME_TONE[outcome] ?? "text-zinc-400";
}

/* ---------------------------------------------------------------- 当前机会 *
 * 先题材（强度/阶段/依据）后题材内候选个股（辨识度/确定性）。
 * 判定全部来自后端 intraday_opportunity 纯函数（等级 + 真实依据，可追溯）；
 * unknown 表示"判不出"（数据缺失），不是"低"——展示必须区分（三态纪律）。 */

const STAGE_TONE: Record<string, string> = {
  启动: "bg-sky-500/10 text-sky-600 dark:text-sky-300",
  发酵: "bg-up/10 text-up",
  高潮: "bg-rose-500/10 text-rose-600 dark:text-rose-300",
  分歧: "bg-amber-500/10 text-amber-600 dark:text-amber-300",
  退潮: "bg-zinc-500/10 text-zinc-500",
};

/** 辨识度/确定性三态徽标：title 挂完整判定依据（可追溯）。 */
function JudgeBadge({ label, j }: { label: string; j: OpportunityJudgement }) {
  const tone =
    j.level === "高"
      ? "bg-violet-500/10 text-violet-600 dark:text-violet-300"
      : j.level === "中"
        ? "bg-sky-500/10 text-sky-600 dark:text-sky-300"
        : j.level === "低"
          ? "bg-zinc-500/10 text-zinc-500"
          : "bg-amber-500/10 text-amber-600 dark:text-amber-300";
  return (
    <span className={`rounded px-1 py-0.5 text-[10px] ${tone}`} title={`${label}判定依据：${j.basis || "—"}`}>
      {label}·{j.level}
    </span>
  );
}

/** 候选个股行：点击跳工作台定位该股（带 from，工作台可一键返回本页）。 */
function OpportunityStockRow({ s }: { s: OpportunityStock }) {
  return (
    <tr className="border-t border-zinc-100 dark:border-zinc-800/60">
      <td className="py-1.5">
        <StockLink symbol={s.symbol} className="font-medium text-zinc-900 dark:text-zinc-50" title="在工作台打开（可返回盘中跟踪）">
          {s.name || s.symbol} ↗
        </StockLink>
        <span className="ml-1 font-mono text-[10px] text-zinc-400">{s.symbol}</span>
      </td>
      <td className="text-zinc-500 dark:text-zinc-400">
        {s.role}
        {s.boards ? ` · ${s.boards}板` : ""}
      </td>
      <td className={`text-right font-mono tabular-nums ${pctColor(s.change_pct)}`}>
        {s.change_pct != null ? pctText(s.change_pct) : "--"}
      </td>
      <td>
        <span className="flex flex-wrap justify-end gap-1">
          <JudgeBadge label="辨识度" j={s.distinctiveness} />
          <JudgeBadge label="确定性" j={s.certainty} />
        </span>
      </td>
      <td className="max-w-[220px] truncate text-[11px] text-zinc-400" title={s.reason ?? ""}>
        {s.reason || "—"}
      </td>
    </tr>
  );
}

/** 题材机会卡：头部是结论（阶段+强度+梯队概况），展开是个股明细。 */
function ThemeCardView({
  t,
  expanded,
  onToggle,
}: {
  t: OpportunityTheme;
  expanded: boolean;
  onToggle: () => void;
}) {
  const stage = t.stage ?? "未知";
  return (
    <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
      {/* L10（切片 E）：题材页 ↗ 与展开按钮同级（button 内不能嵌 a），点题材名仍是展开/收起 */}
      <div className="flex w-full items-center gap-2">
        <button onClick={onToggle} className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-left">
          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">{t.theme}</span>
          <span className={`rounded px-1.5 py-0.5 text-[10px] ${STAGE_TONE[stage] ?? "bg-zinc-500/10 text-zinc-500"}`} title={(t.stage_basis || []).join("；")}>
            {stage}
          </span>
          <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400" title={t.tier_basis ?? ""}>
            {t.strength_tier ?? "—"}
          </span>
          {t.strength_score != null && (
            <span className="font-mono tabular-nums text-zinc-400" title={`题材强度 ${t.strength_score}`}>
              {t.strength_score} 分
            </span>
          )}
          <span className="text-[11px] text-zinc-400" title={`梯队：最高 ${t.max_boards ?? "?"} 板，涨停 ${t.limit_up_count ?? "?"} 家${t.has_succession ? "，梯队有接续" : ""}`}>
            {t.max_boards ?? "?"} 板 · {t.limit_up_count ?? "?"} 家涨停
            {t.has_succession === false && " · 梯队断层"}
          </span>
          <span className="ml-auto text-zinc-400">{expanded ? "收起 ▲" : `${t.stocks.length} 只候选 ▼`}</span>
        </button>
        <Link
          href={themesUrl(t.theme)}
          className="shrink-0 rounded border border-zinc-200 px-1.5 py-0.5 text-[10px] text-zinc-400 transition-colors hover:border-zinc-300 hover:text-zinc-700 dark:border-zinc-700 dark:hover:border-zinc-600 dark:hover:text-zinc-200"
          title="打开题材梯队看板（盘面页 · 题材梯队 tab），聚焦该题材"
        >
          题材页 ↗
        </Link>
      </div>
      {t.risks.length > 0 && !expanded && (
        <p className="mt-1 text-[11px] text-amber-600 dark:text-amber-300" title={t.risks.join("；")}>
          ⚠ {t.risks[0]}
        </p>
      )}
      {expanded && (
        <>
          {t.stage_basis.length > 0 && (
            <p className="mt-1.5 text-[11px] text-zinc-500 dark:text-zinc-400">阶段依据：{t.stage_basis.join("；")}</p>
          )}
          {t.health_note && <p className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400">{t.health_note}</p>}
          {t.risks.length > 0 && (
            <p className="mt-1 text-[11px] text-amber-600 dark:text-amber-300">风险：{t.risks.join("；")}</p>
          )}
          {t.stocks.length > 0 ? (
            <table className="mt-2 w-full">
              <thead>
                <tr className="text-zinc-400">
                  <th className="text-left font-normal">个股（点击进工作台 ↗）</th>
                  <th className="text-left font-normal">梯队</th>
                  <th className="text-right font-normal">涨跌幅</th>
                  <th className="text-right font-normal">判定（悬停看依据）</th>
                  <th className="text-left font-normal">入选理由</th>
                </tr>
              </thead>
              <tbody>
                {t.stocks.map((s) => (
                  <OpportunityStockRow key={s.symbol} s={s} />
                ))}
              </tbody>
            </table>
          ) : (
            <p className="mt-2 text-[11px] text-zinc-400">该题材暂无梯队成员。</p>
          )}
        </>
      )}
    </div>
  );
}

/** 盘中机会区块：结论可解释、可追溯；不构成买卖建议。 */
function OpportunitySection({
  opps,
  expanded,
  onToggle,
}: {
  opps: IntradayOpportunities;
  expanded: string | null;
  onToggle: (theme: string) => void;
}) {
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
          当前机会（题材 → 个股，证据池 {opps.trade_date ?? "—"}）
        </h2>
        {/* 涨停家数/最高板/人气榜警示已上移至「今日行情」概览带（2026-09-04） */}
      </div>
      {opps.themes.length === 0 ? (
        <div className="rounded-xl border border-zinc-200 p-4 text-xs text-zinc-400 dark:border-zinc-800">
          暂无题材机会（当日无涨停数据或题材未成形）。
        </div>
      ) : (
        <div className="space-y-2">
          {opps.themes.map((t) => (
            <ThemeCardView key={t.theme} t={t} expanded={expanded === t.theme} onToggle={() => onToggle(t.theme)} />
          ))}
        </div>
      )}
      {opps.caveats.length > 0 && (
        <p className="text-[10px] text-zinc-400">口径：{opps.caveats.join("；")}</p>
      )}
      <p className="text-[10px] text-zinc-400">
        辨识度=人气×高度×角色（市场记住它的成本）；确定性=题材阶段基座×封板质量修正（延续预期的支撑）。
        两者独立判定不合并打分；判定依据悬停可见、等级可回放。仅模拟跟踪，不构成买卖建议。
      </p>
    </section>
  );
}

/* ---------------------------------------------------------------- 今日行情 *
 * 2026-09-04 用户反馈：数据刷新完成前顶部只有一行小字、布局突兀 →
 * 升级为显式概览指标带（涨停/最高板/题材机会/梯队断层），加载前骨架占位
 * （高度对齐真实块防跳动），数据到达后淡入。三态：加载中≠失败≠空。 */

function MarketOverviewStrip({ opps }: { opps: IntradayOpportunities | null }) {
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
          <span className="text-[11px] text-zinc-400">{label}</span>
          <div className="font-mono text-sm font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
            {value}
          </div>
        </div>
      ))}
    </div>
  );
}

function EnvStrip({ brief }: { brief: MorningBrief }) {
  const env = brief.env;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
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
        <span key={m} className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-600 dark:text-amber-300">
          ⚠ {m}
        </span>
      ))}
    </div>
  );
}

function DirectionCard({ d }: { d: BriefDirection }) {
  const rv = d.review;
  return (
    <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">{d.direction}</span>
        {d.defensive && (
          <span className="rounded border border-sky-500/40 bg-sky-500/10 px-1 py-0.5 text-[10px] text-sky-600 dark:text-sky-300">
            防守
          </span>
        )}
        <span className="font-mono tabular-nums text-zinc-400" title={d.basis}>
          {d.score} 分
        </span>
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] ${
            d.entry_mode === "追涨"
              ? "bg-up/10 text-up"
              : d.entry_mode === "追涨减半"
                ? "bg-amber-500/10 text-amber-600 dark:text-amber-300"
                : d.entry_mode === "潜伏"
                  ? "bg-violet-500/10 text-violet-600 dark:text-violet-300"
                  : "bg-zinc-500/10 text-zinc-500"
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
      <p className="mt-1.5 text-zinc-500 dark:text-zinc-400">{d.logic}</p>
      {rv && (
        <p className="mt-1 text-[11px] text-zinc-400">
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
              <span className="ml-1 text-zinc-400">{p.role}</span>
            </StockLink>
          ))}
        </div>
      )}
      <details className="mt-2 text-[11px] text-zinc-500 dark:text-zinc-400">
        <summary className="cursor-pointer select-none text-zinc-400">触发 / 证伪条件</summary>
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

function AlertItem({ a }: { a: BriefAlert }) {
  const ret = a.meta?.returns;
  const isConfirm = a.kind === "confirm";
  return (
    <div className="rounded-lg border border-zinc-200 p-2.5 dark:border-zinc-800">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
            isConfirm ? "bg-up/10 text-up" : "bg-down/10 text-down"
          }`}
        >
          {isConfirm ? "确认" : "证伪"}
        </span>
        <span className="font-medium text-zinc-900 dark:text-zinc-50">{a.direction}</span>
        {a.symbol && (
          <StockLink symbol={a.symbol} className="text-zinc-500 dark:text-zinc-400" title="点击进工作台看该股详情">
            {a.name}（{a.symbol}）
          </StockLink>
        )}
        <span className="ml-auto text-[10px] text-zinc-400">{timeText(a.at)}</span>
        {isConfirm && ret && (ret.t1_return != null || ret.t3_return != null) && (
          <span className="text-[10px] text-zinc-400">
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

function StatsPanel({ stats }: { stats: IntradayReviewStats }) {
  const d = stats.directions;
  const t1 = stats.alert_t1;
  const t3 = stats.alert_t3;
  const reviewedDays = stats.daily.filter((x) => x.reviewed > 0);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="text-zinc-400">已复盘方向</div>
          <div className="mt-1 font-mono text-lg tabular-nums text-zinc-900 dark:text-zinc-50">{d.total}</div>
          <div className="mt-0.5 text-[10px] text-zinc-400">
            发酵 {d.outcomes["发酵"] ?? 0} · 半发酵 {d.outcomes["半发酵"] ?? 0} · 证伪 {d.outcomes["证伪"] ?? 0} · 无波动 {d.outcomes["无波动"] ?? 0}
          </div>
        </div>
        <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="text-zinc-400">确认提醒 T+1 胜率</div>
          <div className={`mt-1 font-mono text-lg tabular-nums ${(t1.win_rate ?? 0) >= 50 ? "text-up" : "text-down"}`}>
            {t1.win_rate != null ? `${t1.win_rate}%` : "—"}
          </div>
          <div className="mt-0.5 text-[10px] text-zinc-400">
            样本 {t1.n} · 均值 {t1.avg_return != null ? pctText(t1.avg_return) : "—"}
          </div>
        </div>
        <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="text-zinc-400">盈亏比（T+1）</div>
          <div className="mt-1 font-mono text-lg tabular-nums text-zinc-900 dark:text-zinc-50">
            {t1.profit_loss_ratio != null ? t1.profit_loss_ratio : "—"}
          </div>
          <div className="mt-0.5 text-[10px] text-zinc-400">
            均盈 {t1.avg_win != null ? pctText(t1.avg_win) : "—"} / 均亏 {t1.avg_loss != null ? pctText(t1.avg_loss) : "—"}
          </div>
        </div>
        <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="text-zinc-400">T+3 胜率</div>
          <div className={`mt-1 font-mono text-lg tabular-nums ${(t3.win_rate ?? 0) >= 50 ? "text-up" : "text-down"}`}>
            {t3.win_rate != null ? `${t3.win_rate}%` : "—"}
          </div>
          <div className="mt-0.5 text-[10px] text-zinc-400">样本 {t3.n}（未到期不计）</div>
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
              <tr className="text-zinc-400">
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
                  <td className="py-1 font-mono text-zinc-400">{x.date}</td>
                  <td className="text-right font-mono tabular-nums">{x.reviewed}/{x.directions}</td>
                  <td className="text-right font-mono tabular-nums text-up">{x.fermented}</td>
                  <td className="text-right font-mono tabular-nums text-amber-500 dark:text-amber-400">{x.half}</td>
                  <td className="text-right font-mono tabular-nums text-down">{x.falsified}</td>
                  <td className="text-right font-mono tabular-nums text-zinc-400">{x.flat}</td>
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
              <span className="font-mono text-zinc-400">{a.date}</span>
              <span>{a.name || a.symbol}</span>
              <span className="text-zinc-400">{a.direction}</span>
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
      <p className="text-[10px] text-zinc-400">{stats.sample_note}</p>
    </div>
  );
}

/** 极简 SVG 柱带：每日 发酵/半发酵/证伪/无波动 堆叠。样本 ≥2 天才渲染。 */
function DailyCurve({ daily }: { daily: IntradayReviewStats["daily"] }) {
  const W = 660;
  const H = 90;
  const bw = Math.max(6, Math.min(18, (W - 8) / daily.length - 4));
  const gap = (W - 8) / daily.length;
  const max = Math.max(1, ...daily.map((x) => Math.max(1, x.reviewed)));
  const colors: [string, number][] = [
    ["var(--color-up, #16a34a)", 0],
    ["#f59e0b", 0],
    ["var(--color-down, #dc2626)", 0],
    ["#a1a1aa", 0],
  ];
  return (
    <svg viewBox={`0 0 ${W} ${H + 14}`} className="w-full" role="img" aria-label="每日方向对照堆叠柱">
      {daily.map((x, i) => {
        const segs: [string, number][] = [
          [colors[0][0], x.fermented],
          ["#f59e0b", x.half],
          [colors[2][0], x.falsified],
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

function IntradayPageInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const [brief, setBrief] = useState<MorningBrief | null>(null);
  const [watcher, setWatcher] = useState<WatcherState | null>(null);
  const [stats, setStats] = useState<IntradayReviewStats | null>(null);
  const [opps, setOpps] = useState<IntradayOpportunities | null>(null);
  const [briefMissing, setBriefMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // 数据刷新状态（2026-09-03）：上次成功拉取时间 + 全部失败警示。
  // 此前页面只在挂载时拉一次且失败全静默——进入页面后数据定格，
  // watcher 后端每拍推进但界面不跟随，用户以为必须手动刷新。
  const [loadedAt, setLoadedAt] = useState<number | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  // 首轮加载在途（2026-09-04）：区分「加载中」与「确认无数据」——
  // 加载中渲染骨架占位，加载完成后才允许出现空态/失败态，避免整块内容突然弹出。
  const [pending, setPending] = useState(true);

  // 展开的题材以 URL query 为真相源（?theme=）：跳工作台后返回，展开态原样保留
  const [expandedTheme, setExpandedTheme] = useState<string | null>(() => sp.get("theme"));
  // 分区深链（助手一键跳转 / 分享）：?sec=overview|opportunity|brief|watcher|reminders|review。
  // 数据是异步拉的，挂载时目标分区可能还没渲染 → 首帧找不到就在 400ms 后重试一次，
  // 仍找不到则静默放弃（绝不报错、绝不滚到顶部造成"页面乱跳"的错觉）。
  const sec = sp.get("sec");
  const secValid = /^(overview|opportunity|brief|watcher|reminders|review)$/.test(sec ?? "");
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
      router.replace(next ? `/intraday?theme=${encodeURIComponent(next)}` : "/intraday", {
        scroll: false,
      });
    },
    [expandedTheme, router],
  );

  const load = useCallback(async () => {
    const [b, w, s, o] = await Promise.all([
      getMorningBriefToday().catch(() => null),
      getWatcherState().catch(() => null),
      getIntradayReview().catch(() => null),
      getIntradayOpportunities().catch(() => null),
    ]);
    setBrief(b);
    setBriefMissing(b === null);
    setWatcher(w);
    setStats(s);
    setOpps(o);
    // 全部失败 = 后端不可达/网络断：可见警示（轮询会自动重试）；
    // 部分成功不警示——briefMissing 等单端点缺失有各自的空态文案。
    setLoadFailed(b === null && w === null && s === null && o === null);
    setLoadedAt(Date.now());
    setPending(false);
  }, []);

  // 挂载即拉（轮询由下方 visibility 感知 effect 承担：60s 对齐后端 watcher 节拍）
  usePollingFetch(load, null);

  // 自动刷新（60s，对齐后端 watcher 节拍）：页面不可见时暂停轮询、
  // 回到可见立即补拉一次——保证切回页面看到的是当前盘面而非陈旧快照。
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

  async function act(kind: "brief" | "beat" | "review") {
    setBusy(kind);
    setError(null);
    setHint(null);
    try {
      if (kind === "brief") {
        const b = await generateMorningBrief();
        setHint(`简报已生成：${b.directions.length} 个方向（覆盖当日文件，盘中提醒已清空）`);
      } else if (kind === "beat") {
        const r = await runWatcherBeat();
        const n = r.alerts.length;
        setHint(n > 0 ? `单拍完成：${n} 条提醒（去重后实际分发见日志）` : "单拍完成：本拍无新增提醒");
      } else {
        const r = await runIntradayReview();
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

  return (
    <main className="mx-auto flex h-full w-full max-w-[1400px] flex-col gap-3 overflow-hidden px-4 py-3">
      <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs text-zinc-400">
        <h1 className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">盘中跟踪</h1>
        <span title="盘前 08:40 生成简报 → 盘中每 60s 取拍验证 → 盘后 15:35 对照复盘（选股 2.0 §2 三节拍）">
          盘前简报 · 盘中验证 · 盘后对照
        </span>
        {/* 数据刷新状态（对齐工作台连接状态的可见性纪律：状态必须如实呈现） */}
        {loadFailed ? (
          <span
            className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-600 dark:text-amber-300"
            title="四个数据端点全部失败（后端不可达或网络中断）。每 60s 自动重试，也可点右侧「刷新数据」。"
          >
            ⚠ 数据加载失败 · 自动重试中
          </span>
        ) : (
          loadedAt != null && (
            <span className="text-[10px]" title="页面每 60s 自动拉取最新数据（对齐后端 watcher 60s 节拍）；切走再切回会立即刷新。">
              数据 {timeText(new Date(loadedAt).toISOString())} 更新 · 每 60s 自动刷新
            </span>
          )
        )}
        <div className="ml-auto flex items-center gap-1.5">
          <button
            onClick={() => void load()}
            disabled={busy !== null}
            className="rounded border border-zinc-300 px-2 py-0.5 text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100 disabled:opacity-50"
            title="立即重新拉取全部数据（通常无需手动点——页面已自动刷新）"
          >
            刷新数据
          </button>          <button
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

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
        {/* ── 今日行情概览（2026-09-04）：原 header 一行小字升级为指标带；未就绪时骨架占位 ── */}
        <section id="sec-overview" className="space-y-2 scroll-mt-2">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">今日行情</h2>
            {opps?.hot_available === false && (
              <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-600 dark:text-amber-300">
                ⚠ 人气榜不可用：辨识度判定不完整
              </span>
            )}
          </div>
          {opps ? (
            <FadeIn>
              <MarketOverviewStrip opps={opps} />
            </FadeIn>
          ) : pending ? (
            <StatGridSkeleton count={4} />
          ) : (
            <div className="rounded-lg border border-zinc-200 px-3 py-2.5 text-xs text-zinc-400 dark:border-zinc-800">
              当前行情数据不可用（后端不可达或端点失败）——每 60s 自动重试。
            </div>
          )}
        </section>

        {/* 当前机会不依赖简报文件（实时涨停池题材），独立于 briefMissing 展示 */}
        <div id="sec-opportunity" className="scroll-mt-2">
          {opps ? (
            <FadeIn>
              <OpportunitySection opps={opps} expanded={expandedTheme} onToggle={toggleTheme} />
            </FadeIn>
          ) : (
            pending && <CardListSkeleton count={3} />
          )}
        </div>
        {briefMissing ? (
          <div className="rounded-xl border border-zinc-200 p-6 text-center text-sm text-zinc-400 dark:border-zinc-800">
            今日尚无盘前简报：点右上「生成/刷新简报」，或等交易日 08:40 自动生成。
            <br />
            简报是盘中跟踪与盘后对照的唯一事实源，没有它 watcher 会空转。
          </div>
        ) : pending || brief ? (
          <>
            <section id="sec-brief" className="space-y-2 scroll-mt-2">
              <h2 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">盘前简报</h2>
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
                <CardListSkeleton count={3} />
              )}
            </section>

            <section id="sec-watcher" className="space-y-2 scroll-mt-2">
              <h2 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">盘中 watcher 状态</h2>
              <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
                {pending && watcher === null ? (
                  <TableSkeleton rows={4} />
                ) : watcher?.active ? (
                  <FadeIn>
                    <div className="mb-2 flex flex-wrap gap-3 text-[11px] text-zinc-400">
                      <span>已推进 {watcher.beat_count ?? 0} 拍</span>
                      <span>启动于 {timeText(watcher.started_at ?? null)}</span>
                    </div>
                    <table className="w-full">
                      <thead>
                        <tr className="text-zinc-400">
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
                            <td className="text-right font-mono tabular-nums text-zinc-400">{t.missing_beats}</td>
                            <td className={`text-right font-mono tabular-nums ${pctColor(t.peak_pct)}`}>
                              {t.peak_pct != null ? pctText(t.peak_pct) : "—"}
                            </td>
                            <td className="text-right">
                              {t.confirmed ? <span className="text-up">是</span> : <span className="text-zinc-400">否</span>}
                            </td>
                            <td className="text-right">
                              {t.falsified ? (
                                <span className="text-down" title={t.falsify_triggers.map((x) => x.detail).join("；")}>
                                  是
                                </span>
                              ) : (
                                <span className="text-zinc-400">否</span>
                              )}
                            </td>
                            <td className="text-right font-mono text-[11px] text-zinc-400">
                              {t.alerted_symbols.join("、") || "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </FadeIn>
                ) : (
                  <div className="text-zinc-400">
                    {watcher?.note ?? "watcher 未启动（后端未运行或开关关闭）——非交易时段属正常"}
                  </div>
                )}
              </div>
            </section>

            <section id="sec-reminders" className="space-y-2 scroll-mt-2">
              <h2 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
                盘中提醒（{alerts.length} 条，当日去重）
              </h2>
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

            <section id="sec-review" className="space-y-2 scroll-mt-2">
              <h2 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
                今日盘前 vs 实际（对照表
                {brief?.review ? ` · ${timeText(brief.review.reviewed_at)} 复盘）` : " · 未复盘，15:35 自动运行）"}
              </h2>
              {pending && reviewed.length === 0 ? (
                <TableSkeleton rows={3} />
              ) : reviewed.length === 0 ? (
                <div className="rounded-xl border border-zinc-200 p-4 text-xs text-zinc-400 dark:border-zinc-800">
                  今日尚未对照。点右上「运行对照」或等 15:35 调度（收盘后才有意义）。
                </div>
              ) : (
                <FadeIn>
                  <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
                    <table className="w-full">
                      <thead>
                        <tr className="text-zinc-400">
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
                              <td className="text-zinc-500 dark:text-zinc-400">{d.entry_mode}</td>
                              <td className={`font-medium ${outcomeTone(rv.outcome)}`}>{rv.outcome}</td>
                              <td className={`text-right font-mono tabular-nums ${pctColor(rv.actual_pct)}`}>
                                {rv.actual_pct != null ? pctText(rv.actual_pct) : "unknown"}
                              </td>
                              <td className="text-zinc-500 dark:text-zinc-400">
                                {rv.failure_class ? FAILURE_LABELS[rv.failure_class] ?? rv.failure_class : "—"}
                                {rv.note ? ` · ${rv.note}` : ""}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </FadeIn>
              )}
            </section>

            {stats ? (
              <FadeIn>
                <section className="space-y-2">
                  <h2 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">近 30 日胜率统计</h2>
                  <StatsPanel stats={stats} />
                </section>
              </FadeIn>
            ) : (
              pending && (
                <section className="space-y-2">
                  <h2 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">近 30 日胜率统计</h2>
                  <StatsSkeleton />
                </section>
              )
            )}
          </>
        ) : null}
      </div>

      <div className="shrink-0 text-[10px] text-zinc-500">
        三节拍（盘前简报 / 盘中 60s 验证 / 盘后对照）共用当日简报文件 · 阈值集中在
        intraday_rules 常量表（批次 D 回测调参唯一入口）· 全部输出为模拟跟踪，不构成买卖建议
      </div>
    </main>
  );
}

/** useSearchParams 需要 Suspense 边界（Next 16 约束，workbench 同款结构）。 */
export default function IntradayPage() {
  return (
    <Suspense fallback={<PageSkeletonFallback label="盘中跟踪加载中" />}>
      <IntradayPageInner />
    </Suspense>
  );
}
