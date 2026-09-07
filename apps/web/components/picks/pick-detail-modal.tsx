"use client";

import { useEffect } from "react";
import { createPortal } from "react-dom";

import { PickCard, ROLE_STYLE, SUB_LABELS } from "@/components/picks/pick-card";
import { fmt, pctColor, pctText } from "@/lib/format";
import type { DailyPickItem, IntradayTopStock } from "@/lib/api";

/**
 * 选股详情弹窗（2026-09-07 用户需求）：工作台「每日精选」「盘中跟踪」动态分组
 * 的每行增加「详情」按钮，弹出完整选股依据。
 *
 * - 每日精选：直接复用 PickCard（瀑布流卡片原样呈现，六维评分/联合研判/出场纪律）；
 * - 盘中跟踪：TopDetailCard 与 PickCard 同构（同样的圆角卡 + chips + 分节），
 *   展示 T 档/题材阶段/辨识度/确定性与入选理由。
 * 弹窗外壳与 NewsModal 同款（portal + 背景点击 + Esc）。红线 3：内容全部为
 * 可解释依据与条件陈述，不构成买卖建议。
 */

/** 三态判定徽标：title 挂完整判定依据（与盘中跟踪页 JudgeBadge 同语义）。 */
function JudgeChip({ label, level, basis }: { label: string; level: string; basis: string }) {
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
      {label}·{level}
    </span>
  );
}

/** 盘中跟踪标的卡：与 PickCard 同构（头部/chips/分节），字段来自 intraday-top。 */
function TopDetailCard({ item }: { item: IntradayTopStock }) {
  return (
    <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
      {/* 头：名称代码 + 涨跌幅（无现价字段，不臆造） */}
      <div className="flex items-baseline justify-between gap-2">
        <div>
          <span className="text-sm font-semibold">{item.name ?? "--"}</span>
          <span className="ml-1.5 font-mono text-[10px] text-zinc-400">{item.symbol}</span>
        </div>
        <div className="text-right">
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
              item.tier <= 2 ? "bg-up/10 text-up" : "bg-amber-500/10 text-amber-600 dark:text-amber-400"
            }`}
          >
            T{item.tier} 跟踪档
          </span>
          <div className={`mt-0.5 font-mono text-xs tabular-nums ${pctColor(item.change_pct)}`}>
            {item.change_pct != null ? pctText(item.change_pct) : "--"}
          </div>
        </div>
      </div>

      {/* 梯队角色 + 题材阶段 chips */}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {item.role && (
          <span className={`rounded border px-1.5 py-0.5 text-[10px] ${ROLE_STYLE[item.role] ?? "border-zinc-300 text-zinc-500 dark:border-zinc-700"}`}>
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
          <span className="rounded border border-zinc-300 px-1.5 py-0.5 text-[10px] text-zinc-500 dark:border-zinc-700" title="题材强度档">
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
        <div className="flex gap-1.5">
          <span className="shrink-0 text-zinc-400">入选</span>
          <span className="text-zinc-600 dark:text-zinc-300">{item.pick_basis || "—"}</span>
        </div>
        {item.reason && (
          <div className="flex gap-1.5">
            <span className="shrink-0 text-zinc-400">依据</span>
            <span className="text-zinc-600 dark:text-zinc-300">{item.reason}</span>
          </div>
        )}
      </div>

      <p className="mt-2 text-[10px] leading-relaxed text-zinc-400">
        盘中跟踪为多维筛选（确定性优先、辨识度次之）的动态名单，随盘面每分钟重算；
        全部为条件陈述与判定依据，不构成买卖建议。
      </p>
    </div>
  );
}

export type PickDetailTarget =
  | { kind: "pick"; item: DailyPickItem }
  | { kind: "top"; item: IntradayTopStock }
  | null;

export function PickDetailModal({ target, onClose }: { target: PickDetailTarget; onClose: () => void }) {
  // Esc 关闭（挂载即监听，target 为 null 时是无操作）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!target) return null;
  const title = target.kind === "pick" ? "每日精选 · 选股详情" : "盘中跟踪 · 入选详情";

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label={title}
      data-testid="pick-detail-modal"
    >
      <div className="flex max-h-[85vh] w-full max-w-md flex-col overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-start justify-between gap-3 border-b border-zinc-100 px-5 py-3 dark:border-zinc-800/80">
          <h2 className="text-[15px] font-semibold text-zinc-900 dark:text-zinc-100">{title}</h2>
          <button
            onClick={onClose}
            className="shrink-0 rounded p-1 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
            aria-label="关闭"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3" data-testid="pick-detail-body">
          {target.kind === "pick" ? <PickCard item={target.item} /> : <TopDetailCard item={target.item} />}
        </div>
        <div className="border-t border-zinc-100 px-5 py-2 text-[10px] text-zinc-400 dark:border-zinc-800/80">
          六维评分：{SUB_LABELS.map(([, l]) => l).join(" / ")} · 一票否决后加权 · 数据仅供投研参考，不构成买卖建议
        </div>
      </div>
    </div>,
    document.body,
  );
}
