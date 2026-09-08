"use client";

import { useEffect } from "react";
import { createPortal } from "react-dom";

import { PickCard, SUB_LABELS } from "@/components/picks/pick-card";
import { WatchCard } from "@/components/picks/watch-card";
import type { DailyPickItem, IntradayTopStock } from "@/lib/api";

/**
 * 选股详情弹窗（2026-09-07 用户需求）：工作台「每日精选」「盘中跟踪」动态分组
 * 的每行增加「详情」按钮，弹出完整选股依据。
 *
 * - 每日精选：直接复用 PickCard（瀑布流卡片原样呈现，六维评分/联合研判/出场纪律）；
 * - 盘中跟踪：WatchCard（原 TopDetailCard，猎场批次①独立成组件）与 PickCard 同构
 *   （CardShell 壳 + CardHead 头 + chips + 分节），展示 T 档/题材阶段/辨识度/确定性与入选理由。
 * 弹窗外壳与 NewsModal 同款（portal + 背景点击 + Esc）。红线 3：内容全部为
 * 可解释依据与条件陈述，不构成买卖建议。
 */

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
          {target.kind === "pick" ? (
            <PickCard item={target.item} />
          ) : (
            <WatchCard item={target.item}>
              <p className="mt-2 text-[10px] leading-relaxed text-zinc-400">
                盘中跟踪为多维筛选（确定性优先、辨识度次之）的动态名单，随盘面每分钟重算；
                全部为条件陈述与判定依据，不构成买卖建议。
              </p>
            </WatchCard>
          )}
        </div>
        <div className="border-t border-zinc-100 px-5 py-2 text-[10px] text-zinc-400 dark:border-zinc-800/80">
          六维评分：{SUB_LABELS.map(([, l]) => l).join(" / ")} · 一票否决后加权 · 数据仅供投研参考，不构成买卖建议
        </div>
      </div>
    </div>,
    document.body,
  );
}
