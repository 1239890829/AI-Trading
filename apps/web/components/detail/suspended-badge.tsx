/** 停牌/状态徽标（UI 缺陷 #1）。
 *
 * 三态语义必须严格区分，不能合并：
 * - `suspended`：**已判定停牌**，显示天数与起始日——用户据此判断复牌预期
 * - `unknown`：**判不出来**（无日K / 日历不可用）——如实说"无法判定"，
 *   绝不当成"正常交易"隐掉。这是本项目"缺失要显式标注"纪律的落地
 * - `trading` / null：正常，不渲染（null = 未判定，如非日线 timeframe）
 *
 * 判据在后端 `app/market/trading_status.py`：有历史 K 线但最近交易日缺 bar = 停牌。
 * **不靠"成交量=0"硬猜**——一字涨停也无量，会误标（2026-09-02 实测）。
 */
import type { TradingStatusInfo } from "@/types/market";

export function isSuspended(s: TradingStatusInfo | null | undefined): boolean {
  return s?.status === "suspended";
}

/** 头部/标题栏用的紧凑徽标。正常交易与未判定时返回 null（不占位）。 */
export function SuspendedBadge({ status }: { status?: TradingStatusInfo | null }) {
  if (!status || status.status === "trading") return null;
  if (status.status === "suspended") {
    return (
      <span
        className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[11px] font-medium text-amber-600 dark:text-amber-300"
        title={`停牌判定依据：${status.reason ?? "—"}`}
      >
        停牌{status.suspended_days ? ` ${status.suspended_days} 个交易日` : ""}
      </span>
    );
  }
  return (
    <span
      className="rounded bg-zinc-500/20 px-1.5 py-0.5 text-[11px] text-zinc-500 dark:text-zinc-400"
      title={status.reason || "无足够数据判定交易状态"}
    >
      交易状态未知
    </span>
  );
}

/** 图表区上方的说明条：停牌时**必须**显示判据，否则用户无从判断可信度。 */
export function SuspendedNotice({ status }: { status?: TradingStatusInfo | null }) {
  if (!status || status.status === "trading") return null;
  const suspended = status.status === "suspended";
  return (
    <div
      className={`flex shrink-0 flex-wrap items-baseline gap-x-2 border-b px-3 py-1 text-[11px] ${
        suspended
          ? "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-300"
          : "border-zinc-200 bg-zinc-50 text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-400"
      }`}
    >
      <span className="font-medium">{suspended ? "该股当前停牌" : "交易状态无法判定"}</span>
      <span className="opacity-80">{status.reason || "—"}</span>
      <span className="ml-auto opacity-60">依据：日K 缺失交易日数（非盘口量能推断）</span>
    </div>
  );
}
