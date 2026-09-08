"use client";

import type { ReactNode } from "react";

import { CardHead, CardShell } from "@/components/picks/card-shell";
import { ROLE_STYLE } from "@/components/picks/pick-card";
import { pctColor, pctText, triText } from "@/lib/format";
import type { IntradayTopStock, OpportunityStock } from "@/lib/api";

/**
 * 盘中跟踪卡片（猎场批次①）：原 pick-detail-modal 内 TopDetailCard 独立成组件，
 * 与 PickCard 同构（CardShell 外框 + CardHead 头部 + chips + 分节）。
 *
 * 数据源两类结构兼容（猎场瀑布流 / 手风琴展开都要用它）：
 * - IntradayTopStock：工作台「盘中跟踪」分组 / intraday-top 最推荐标的（带 tier/pick_basis）
 * - OpportunityStock：题材手风琴展开后的题材内候选（无 tier，role/boards/判定同构）
 * 两者字段并入 WatchCardItem（全可选除 symbol/name），结构类型天然互认。
 *
 * 判定徽标（辨识度/确定性）三态纪律：unknown = 判不出（数据缺失），≠「低」；
 * tone 与原 intraday 页 JudgeBadge、弹窗 JudgeChip 完全一致——此处为单一真值。
 * 红线 3：全部为可解释依据与条件陈述，不构成买卖建议。
 */

export interface WatchCardItem {
  symbol: string;
  name: string | null;
  role?: string | null;
  boards?: number | null;
  change_pct?: number | null;
  theme?: string | null;
  stage?: string | null;
  strength_tier?: string | null;
  distinctiveness?: { level: string; basis: string } | null;
  certainty?: { level: string; basis: string } | null;
  reason?: string | null;
  pick_basis?: string | null;
  tier?: number | null;
}

/** 三态判定徽标：title 挂完整判定依据（可追溯）。 */
export function JudgeChip({ label, level, basis }: { label: string; level: string; basis: string }) {
  const tone =
    level === "高"
      ? "bg-violet-500/10 text-violet-600 dark:text-violet-300"
      : level === "中"
        ? "bg-sky-500/10 text-sky-600 dark:text-sky-300"
        : level === "低"
          ? "bg-zinc-500/10 text-zinc-500"
          : "bg-amber-500/10 text-amber-600 dark:text-amber-300"; // unknown：判不出 ≠ 低
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] ${tone}`} title={`${label}判定依据：${basis || "—"}`}>
      {/* triText：level="unknown"（判不出）→「未判定」，不把内部字面量打上界面 */}
      {label}·{triText(level)}
    </span>
  );
}

export function WatchCard({
  item,
  flow = false,
  children,
}: {
  item: WatchCardItem;
  /** 瀑布流单元模式（猎场页 CSS columns）；弹窗内不带 */
  flow?: boolean;
  /** 弹窗场景的尾部说明（如「多维筛选动态名单」口径注记） */
  children?: ReactNode;
}) {
  return (
    <CardShell flow={flow}>
      {/* 头：名称代码 + T 档徽标（有 tier 才显示，OpportunityStock 无）+ 涨跌幅（无现价字段，不臆造） */}
      <CardHead
        name={item.name}
        symbol={item.symbol}
        right={
          <>
            {item.tier != null && (
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                  item.tier <= 2 ? "bg-up/10 text-up" : "bg-amber-500/10 text-amber-600 dark:text-amber-400"
                }`}
              >
                T{item.tier} 跟踪档
              </span>
            )}
            <div className={`mt-0.5 font-mono text-xs tabular-nums ${pctColor(item.change_pct ?? null)}`}>
              {item.change_pct != null ? pctText(item.change_pct) : "--"}
            </div>
          </>
        }
      />

      {/* 梯队角色 + 题材阶段 chips */}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {item.role && (
          <span
            className={`rounded border px-1.5 py-0.5 text-[10px] ${ROLE_STYLE[item.role] ?? "border-zinc-300 text-zinc-500 dark:border-zinc-700"}`}
          >
            {item.role}
            {item.boards ? ` · ${item.boards}板` : ""}
          </span>
        )}
        {item.theme && (
          <span className="rounded border border-zinc-300 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:border-zinc-600 dark:text-zinc-300">
            {item.theme}
            {item.stage ? ` · ${item.stage}` : ""}
          </span>
        )}
        {item.strength_tier && (
          <span
            className="rounded border border-zinc-300 px-1.5 py-0.5 text-[10px] text-zinc-500 dark:border-zinc-700"
            title="题材强度档"
          >
            {item.strength_tier}
          </span>
        )}
      </div>

      {/* 判定（辨识度/确定性）：三态，依据悬停可见 */}
      <div className="mt-2 flex flex-wrap gap-1">
        {item.distinctiveness && (
          <JudgeChip label="辨识度" level={item.distinctiveness.level} basis={item.distinctiveness.basis} />
        )}
        {item.certainty && <JudgeChip label="确定性" level={item.certainty.level} basis={item.certainty.basis} />}
      </div>

      {/* 入选理由（与 PickCard 的买入原因分节同构） */}
      <div className="mt-2 space-y-0.5 rounded-lg border border-zinc-100 p-2 text-[11px] leading-relaxed dark:border-zinc-800">
        {item.pick_basis && (
          <div className="flex gap-1.5">
            <span className="shrink-0 text-zinc-400">入选</span>
            <span className="text-zinc-600 dark:text-zinc-300">{item.pick_basis}</span>
          </div>
        )}
        {item.reason && (
          <div className="flex gap-1.5">
            <span className="shrink-0 text-zinc-400">依据</span>
            <span className="text-zinc-600 dark:text-zinc-300">{item.reason}</span>
          </div>
        )}
        {!item.pick_basis && !item.reason && (
          <div className="flex gap-1.5">
            <span className="shrink-0 text-zinc-400">入选</span>
            <span className="text-zinc-600 dark:text-zinc-300">—</span>
          </div>
        )}
      </div>

      {children}
    </CardShell>
  );
}
