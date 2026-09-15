"use client";

import { PickCard, SUB_LABELS, fromDailyPick, fromIntradayStock } from "@/components/picks/pick-card";
import { ModalShell } from "@/components/ui/modal-shell";
import type { DailyPickItem, IntradayTopStock } from "@/lib/api";

/**
 * 选股详情弹窗（2026-09-07 用户需求）：工作台「每日精选」「盘中跟踪」动态分组
 * 的每行增加「详情」按钮，弹出完整选股依据。
 *
 * 两类名单现在共用**同一张卡**（PickCard，2026-09-10 两卡合并）：每日精选走
 * `fromDailyPick`、盘中跟踪走 `fromIntradayStock`，字段互补后合成一张完整卡片；
 * 盘中缺的收盘口径维度（六维评分/估值/买入区间）由卡底口径注记说明，不留白。
 * 弹窗外壳走全站统一的 `ModalShell`（2026-09-15：portal / 遮罩 / Esc / 尺寸档
 * 收在一处，不再各自实现）。红线 3：内容全部为可解释依据与条件陈述，不构成买卖建议。
 */

export type PickDetailTarget =
  | { kind: "pick"; item: DailyPickItem }
  | { kind: "top"; item: IntradayTopStock }
  | null;

export function PickDetailModal({ target, onClose }: { target: PickDetailTarget; onClose: () => void }) {
  if (!target) return null;
  const title = target.kind === "pick" ? "每日精选 · 选股详情" : "盘中跟踪 · 入选详情";

  return (
    <ModalShell
      onClose={onClose}
      label={title}
      testid="pick-detail-modal"
      size="sm"
      header={<h2 className="text-[15px] font-semibold text-zinc-900 dark:text-zinc-100">{title}</h2>}
      bodyClassName="overflow-y-auto px-4 py-3"
      bodyTestId="pick-detail-body"
      footer={
        target.kind === "pick"
          ? `六维评分：${SUB_LABELS.map(([, l]) => l).join(" / ")} · 一票否决后加权 · 数据仅供投研参考，不构成买卖建议`
          : "盘中实时口径 · 随盘面重算 · 数据仅供投研参考，不构成买卖建议"
      }
    >
      {target.kind === "pick" ? (
        <PickCard item={fromDailyPick(target.item)} />
      ) : (
        <PickCard item={fromIntradayStock(target.item)} />
      )}
    </ModalShell>
  );
}
