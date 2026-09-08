"use client";

import { pctText } from "@/lib/format";
import type { IntradayReviewStats, SignalHealthPayload } from "@/lib/api";
import type { RolePerformance } from "@/components/hunting/pick-sections";

/**
 * 猎场统计条（批次②，docs/system-audit-20260908.md §3.2）：精选与跟踪双口径并列，
 * 各自标注口径、绝不混算（精选=date+symbol 持久组合，跟踪=当日实时动态名单）。
 *
 * 三态纪律（审查 F1/F2）：
 * - signal-health status=insufficient → 显式「样本不足」，绝不把 0 样本渲染成 0%；
 * - win_rate=null / n=0 → 「样本不足」副注说明，不臆造数值；
 * - data=null（端点失败）→ 「暂不可用」，轮询自动恢复。
 *
 * signal-health 为前端首次接入（此前后端已有、前端从未消费，审查判定「零成本转正」）。
 * 注意口径差异：signal-health.win_rate 是 0-1 小数；intraday-review.win_rate 是百分数。
 */

const HEALTH_TONE: Record<string, string> = {
  ok: "border-up/40 bg-up/10 text-up",
  warning: "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-300",
  drift: "border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-300",
  insufficient: "border-zinc-300 text-zinc-500 dark:border-zinc-700 dark:text-zinc-400",
  error: "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-300",
};

const HEALTH_LABEL: Record<string, string> = {
  ok: "健康",
  warning: "预警",
  drift: "下漂",
  insufficient: "样本不足",
  error: "异常",
};

function StatCard({
  label,
  tip,
  badge,
  children,
}: {
  label: string;
  /** 整卡悬停提示（口径注记） */
  tip?: string;
  /** 右上状态徽标（文案 + 类名） */
  badge?: { text: string; tone: string };
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-zinc-200 px-2.5 py-1.5 dark:border-zinc-800" title={tip}>
      <div className="flex items-center gap-1.5">
        <span className="text-[11px] text-zinc-400">{label}</span>
        {badge && (
          <span className={`rounded border px-1 py-0.5 text-[9px] font-medium ${badge.tone}`}>{badge.text}</span>
        )}
      </div>
      {children}
    </div>
  );
}

export function HuntingStatsBar({
  health,
  rolePerformance,
  stats,
}: {
  health: SignalHealthPayload | null;
  rolePerformance: RolePerformance[] | undefined;
  stats: IntradayReviewStats | null;
}) {
  // 精选侧副证据：样本 ≥3 的角色里胜率最高者（样本太小的「100% 胜率」是噪音）
  const bestRole = (rolePerformance ?? [])
    .filter((r) => r.count >= 3)
    .sort((a, b) => b.win_rate - a.win_rate)[0];
  const w = health?.window ?? null;
  const t1 = stats?.alert_t1;
  const t3 = stats?.alert_t3;

  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
      {/* 精选口径 ①：信号健康度（滚动组合日胜率 + CUSUM 下漂） */}
      <StatCard
        label="精选 · 信号健康"
        tip="口径：每日精选命中记录的滚动组合日胜率 + CUSUM 下漂检测（与跟踪口径独立不混算）"
        badge={health ? { text: HEALTH_LABEL[health.status], tone: HEALTH_TONE[health.status] } : undefined}
      >
        {health ? (
          health.status === "insufficient" || health.status === "error" ? (
            <div className="mt-0.5 text-sm font-medium text-zinc-400">{health.reason ?? "样本不足"}</div>
          ) : (
            <>
              <div className="mt-0.5 font-mono text-sm font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
                {w?.win_rate != null ? `${Math.round(w.win_rate * 1000) / 10}%` : "—"}
                <span className="ml-1.5 text-[10px] font-normal text-zinc-400">
                  均超额 {w?.mean_excess != null ? pctText(w.mean_excess) : "—"}
                </span>
              </div>
              <div className="mt-0.5 text-[10px] text-zinc-400">
                近 {w?.groups ?? 0} 组合日 · {w?.total_picks ?? 0} 只 · 好 {w?.good ?? 0} / 坏 {w?.bad ?? 0} / 平 {w?.flat ?? 0}
              </div>
            </>
          )
        ) : (
          <div className="mt-0.5 text-sm text-zinc-400">暂不可用</div>
        )}
      </StatCard>

      {/* 精选口径 ②：最优梯队角色（role_performance 已有端点，此处挑样本达标的最优角色） */}
      <StatCard
        label="精选 · 最优角色"
        tip="口径：按题材角色聚合的历史胜率（/api/picks/meta）；单角色样本 ≥3 只才参与排名"
      >
        <div className="mt-0.5 font-mono text-sm font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
          {bestRole ? (
            <>
              {bestRole.role}
              <span className={`ml-1.5 text-[10px] font-normal ${bestRole.win_rate >= 50 ? "text-up" : "text-down"}`}>
                胜率 {bestRole.win_rate}%
              </span>
            </>
          ) : (
            <span className="font-sans font-normal text-zinc-400">样本不足</span>
          )}
        </div>
        <div className="mt-0.5 text-[10px] text-zinc-400">
          {bestRole ? `平均超额 ${pctText(bestRole.avg_excess)}` : "全量角色胜率表见复盘区"}
        </div>
      </StatCard>

      {/* 跟踪口径 ①：确认提醒 T+1（近 30 日） */}
      <StatCard
        label="跟踪 · T+1 胜率"
        tip="口径：盘中确认提醒次日收益 >0 占比（近 30 日）；与精选口径独立不混算"
      >
        <div className={`mt-0.5 font-mono text-sm font-semibold tabular-nums ${(t1?.win_rate ?? 0) >= 50 ? "text-up" : "text-down"}`}>
          {t1?.win_rate != null ? `${t1.win_rate}%` : <span className="font-sans font-normal text-zinc-400">样本不足</span>}
        </div>
        <div className="mt-0.5 text-[10px] text-zinc-400">
          样本 {t1?.n ?? 0} · 盈亏比 {t1?.profit_loss_ratio != null ? t1.profit_loss_ratio : "—"}
        </div>
      </StatCard>

      {/* 跟踪口径 ②：确认提醒 T+3 */}
      <StatCard label="跟踪 · T+3 胜率" tip="口径：确认提醒三日收益 >0 占比（未到期不计）">
        <div className={`mt-0.5 font-mono text-sm font-semibold tabular-nums ${(t3?.win_rate ?? 0) >= 50 ? "text-up" : "text-down"}`}>
          {t3?.win_rate != null ? `${t3.win_rate}%` : <span className="font-sans font-normal text-zinc-400">样本不足</span>}
        </div>
        <div className="mt-0.5 text-[10px] text-zinc-400">样本 {t3?.n ?? 0}（未到期不计）</div>
      </StatCard>
    </div>
  );
}
