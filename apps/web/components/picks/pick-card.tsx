"use client";

import { CardHead, CardShell } from "@/components/picks/card-shell";
import { useStockRowNav } from "@/components/stock-link";
import { fmt, pctColor, pctText } from "@/lib/format";
import type { DailyPickItem, StandAsideGate } from "@/lib/api";

/**
 * 每日精选卡片（瀑布流单元）。
 *
 * 三块信息，缺一不可：
 * 1. **六维评分**：情绪/消息/技术/基本面/资金 + 梯队（第六维）
 * 2. **联合研判**：梯队地位 × 题材阶段 —— 同一个人角色在不同题材阶段价值完全不同
 * 3. **风险与出场**：风险档位 / 止损参考位 / 跟踪止盈 / 失效条件
 *    （借鉴 freqtrade 的出场纪律；是"能盈利"在规则层面的唯一落点）
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

/** 梯队地位配色：越靠前（空间板/龙头）越"热"，补涨跟风降温 */
export const ROLE_STYLE: Record<string, string> = {
  空间板: "border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-300",
  龙头: "border-orange-500/50 bg-orange-500/10 text-orange-600 dark:text-orange-300",
  反包: "border-amber-500/50 bg-amber-500/10 text-amber-600 dark:text-amber-300",
  中军: "border-sky-500/50 bg-sky-500/10 text-sky-600 dark:text-sky-300",
  领涨: "border-teal-500/50 bg-teal-500/10 text-teal-600 dark:text-teal-300",
  补涨: "border-violet-500/50 bg-violet-500/10 text-violet-600 dark:text-violet-300",
  首板: "border-zinc-400/50 bg-zinc-500/10 text-zinc-600 dark:text-zinc-300",
  同步: "border-zinc-300/50 bg-zinc-500/5 text-zinc-500",
  跟风: "border-zinc-300/50 bg-zinc-500/5 text-zinc-400",
  滞涨: "border-zinc-300/50 bg-zinc-500/5 text-zinc-400",
  断板: "border-zinc-300/50 bg-zinc-500/5 text-zinc-400",
};

export const TIER_STYLE: Record<string, string> = {
  龙头博弈: "border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-300",
  趋势跟随: "border-sky-500/50 bg-sky-500/10 text-sky-600 dark:text-sky-300",
  情绪低位: "border-amber-500/50 bg-amber-500/10 text-amber-600 dark:text-amber-300",
};

function Chip({ text, title, className }: { text: string; title?: string; className?: string }) {
  return (
    <span
      title={title}
      className={`rounded border px-1.5 py-0.5 text-[10px] ${className ?? "border-zinc-300 text-zinc-500 dark:border-zinc-700"}`}
    >
      {text}
    </span>
  );
}

export function PickCard({ item, positionLabel = null }: { item: DailyPickItem; positionLabel?: "sim" | "real" | null }) {
  // 整行点击跳工作台（与 WatchCard 行为一致；名字 StockLink 同 URL 双触发无害）
  const stockNav = useStockRowNav();
  return (
    <CardShell flow onClick={stockNav(item.symbol)}>
      {/* 头：名称代码 + 来源/持仓标注 + 现价 + 综合分（名称/代码可点 → 工作台详情，联动切片 F） */}
      <CardHead
        name={item.name}
        symbol={item.symbol}
        link
        right={
          <>
            <span
              className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-600 dark:text-amber-300"
              title="来源：盘前选择（收盘后生成次日名单，换股门槛 15 分）"
            >
              盘前选择
            </span>
            {positionLabel && (
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                  positionLabel === "real"
                    ? "bg-rose-500/10 text-rose-600 dark:text-rose-300"
                    : "bg-emerald-500/10 text-emerald-600 dark:text-emerald-300"
                }`}
                title="点击打开工作台查看持仓；卖出/删流水后标签自动消失"
              >
                {positionLabel === "real" ? "已真实持仓" : "已模拟持仓"}
              </span>
            )}
            <div className="font-mono text-base font-semibold tabular-nums">{fmt(item.price)}</div>
            <div className={`font-mono text-[10px] tabular-nums ${pctColor(item.change_pct)}`}>{pctText(item.change_pct)}</div>
          </>
        }
      />

      {/* 估值：此前选股页完全没有 PE/PB（个股详情页有，因为走 /api/quotes 的补全）。
          数据源确实不提供时（新股/亏损/长期停牌）显示"暂无"并注明原因——
          不留白、不臆造，与个股详情页口径一致。 */}
      <div className="mt-1 flex items-center gap-2 text-[10px] text-zinc-400">
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

      {/* 综合分 + meta 置信档 + 六维子评分条 */}
      <div className="mt-2 flex items-center gap-2">
        <span className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-xs font-semibold dark:bg-zinc-800" title="六维加权综合分（一票否决后）">
          {item.score}
        </span>
        {item.confidence && (
          <span
            className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${
              item.confidence.tier === "strong"
                ? "border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-300"
                : item.confidence.tier === "executable"
                  ? "border-sky-500/50 bg-sky-500/10 text-sky-600 dark:text-sky-300"
                  : "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-300"
            }`}
            title={`meta 置信层（综合分+相位+筹码+红线 → 三档）：\n${item.confidence.reasons.join("；")}`}
          >
            {item.confidence.label}
          </span>
        )}
        <div className="flex flex-1 gap-1">
          {SUB_LABELS.map(([key, label]) => {
            const v = item.sub_scores[key];
            return (
              <div key={key} className="flex-1" title={`${label}：${item.bases[key] ?? "--"}`}>
                <div className="h-1 w-full overflow-hidden rounded bg-zinc-100 dark:bg-zinc-800">
                  <div className="h-full rounded bg-sky-500/80" style={{ width: `${v ?? 50}%` }} />
                </div>
                <div className="mt-0.5 text-center text-[9px] text-zinc-400">{label}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 梯队地位 + 题材阶段 + 风险档位：联合研判（不只是五维） */}
      {(item.echelon_role || item.theme || item.risk_tier) && (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          {item.echelon_role && (
            <Chip
              text={item.echelon_role}
              title={`梯队地位（Echelon Role）：${item.echelon_basis ?? ""}`}
              className={ROLE_STYLE[item.echelon_role]}
            />
          )}
          {item.theme && (
            <Chip
              text={`${item.theme}${item.theme_stage ? ` · ${item.theme_stage}` : ""}`}
              title="所属题材与题材天梯阶段（启动/发酵/高潮/分歧/退潮）——同一个梯队角色在不同阶段价值不同"
              className="border-zinc-300 text-zinc-600 dark:border-zinc-600 dark:text-zinc-300"
            />
          )}
          {item.risk_tier && (
            <Chip
              text={item.risk_tier}
              title={`风险档位（Risk Tier）：决定止损宽严与仓位保守程度。${item.exit_discipline?.note ?? ""}`}
              className={TIER_STYLE[item.risk_tier]}
            />
          )}
          {item.observation_only && (
            <Chip
              text={
                item.follow_state === "followable"
                  ? "可跟"
                  : item.follow_state === "blocked"
                    ? "禁买"
                    : "仅观察"
              }
              title={
                item.follow_state === "followable"
                  ? `空仓闸门日龙头判据全满足（连板高度+梯队地位+题材催化，无红线）。可跟 ≠ 可买：不给买入范围，参与须经影子持仓先验证。${(item.follow_reasons ?? []).join("；")}`
                  : item.follow_state === "blocked"
                    ? `空仓闸门日红线压制（禁买）：${(item.follow_reasons ?? []).join("；") || "命中否决/异动风险"}`
                    : `空仓闸门已触发：本条不给买入范围，仅供复盘与观察。${(item.follow_reasons ?? []).join("；")}`
              }
              className={
                item.follow_state === "followable"
                  ? "border-sky-500/50 bg-sky-500/10 text-sky-600 dark:text-sky-300"
                  : "border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-300"
              }
            />
          )}
          {item.chip_signal?.signal === "distribution_warning" && (
            <Chip
              text="派发警示"
              title={`筹码形态警示（CYQ 近似口径）：${item.chip_signal.reasons.join("；")}`}
              className="border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-300"
            />
          )}
          {item.chip_signal?.signal === "launch_watch" && (
            <Chip
              text="启动观察"
              title={`筹码形态观察（CYQ 近似口径）：${item.chip_signal.reasons.join("；")}`}
              className="border-teal-500/50 bg-teal-500/10 text-teal-600 dark:text-teal-300"
            />
          )}
        </div>
      )}

      {/* 买入范围（空仓闸门触发时撤除，不给出手依据） */}
      {item.buy_range ? (
        <div className="mt-2 rounded-lg bg-sky-500/5 px-2 py-1.5 text-[11px]" title={item.buy_range.basis}>
          <span className="text-zinc-400">买入参考区间</span>{" "}
          <span className="font-mono font-medium tabular-nums">
            {fmt(item.buy_range.low)} – {fmt(item.buy_range.high)}
          </span>
        </div>
      ) : (
        <div
          className={`mt-2 rounded-lg px-2 py-1.5 text-[11px] ${
            item.follow_state === "followable"
              ? "bg-sky-500/5 text-sky-600 dark:text-sky-300"
              : "bg-red-500/5 text-red-500 dark:text-red-300"
          }`}
        >
          {item.follow_state === "followable"
            ? "空仓闸门日：不给买入区间；属「可跟」名单，参与须经影子持仓先验证"
            : "空仓闸门已触发：本条不给出买入参考区间"}
        </div>
      )}

      {/* 止损参考位与失效条件：出场纪律（freqtrade 的止损/跟踪止盈/ROI 分档 的 A 股映射） */}
      {(item.stop_loss || (item.invalidations && item.invalidations.length > 0)) && (
        <div className="mt-2 space-y-1 rounded-lg border border-zinc-100 p-2 text-[11px] dark:border-zinc-800">
          {item.stop_loss && (
            <div title={item.stop_loss.basis}>
              <span className="text-zinc-400">止损参考</span>{" "}
              <span className="font-mono tabular-nums text-red-500">
                {fmt(item.stop_loss.price)}（-{item.stop_loss.pct}%）
              </span>
              {item.exit_discipline && (
                <span className="ml-1.5 text-zinc-400" title={item.exit_discipline.disclaimer}>
                  · 跟踪回撤 {item.exit_discipline.trailing_pct}%
                  {item.exit_discipline.roi_ladder.length > 0 &&
                    ` · 目标 ${item.exit_discipline.roi_ladder.map((r) => `+${r.gain_pct}%${r.action}`).join("/")}`}
                </span>
              )}
            </div>
          )}
          {item.invalidations && item.invalidations.length > 0 && (
            <div>
              <span className="text-zinc-400">失效条件</span>
              {item.invalidations.slice(0, 3).map((v) => (
                <div key={v} className="text-zinc-500 dark:text-zinc-400">
                  · {v}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 买入原因：各维度 basis 摘要（一票否决显式标红；筹码为附注维度不进权重） */}
      <div className="mt-2 space-y-0.5 text-[11px] leading-relaxed">
        {SUB_LABELS.map(([key, label]) => {
          const b = item.bases[key];
          if (!b) return null;
          return (
            <div key={key} className="flex gap-1.5">
              <span className="shrink-0 text-zinc-400">{label}</span>
              <span className="text-zinc-600 dark:text-zinc-300">{b}</span>
            </div>
          );
        })}
        {item.bases["chip"] && (
          <div className="flex gap-1.5">
            <span className="shrink-0 text-zinc-400">筹码</span>
            <span className="text-zinc-600 dark:text-zinc-300">{item.bases["chip"]}</span>
          </div>
        )}
        {item.vetoes.map((v) => (
          <div key={v} className="text-red-500">
            ⚠ {v}
          </div>
        ))}
      </div>

      {/* 关联消息 */}
      {item.related_events.length > 0 && (
        <div className="mt-2 border-t border-zinc-100 pt-1.5 text-[11px] dark:border-zinc-800/60">
          <span className="text-zinc-400">关联消息：</span>
          {item.related_events.map((e) => (
            <div key={e} className="text-zinc-600 dark:text-zinc-300">
              · {e}
            </div>
          ))}
        </div>
      )}
    </CardShell>
  );
}

/** 空仓闸门横幅：情绪转弱时主动提示规避（红线 3：只提示，不下指令） */
export function StandAsideBanner({ gate }: { gate: StandAsideGate }) {
  if (!gate.stand_aside) return null;
  const strong = gate.level === "strong";
  return (
    <div
      role="alert"
      className={`shrink-0 rounded-lg border px-3 py-2 text-xs ${
        strong
          ? "border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-300"
          : "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-300"
      }`}
    >
      <div className="font-medium">⚠ {gate.advice}</div>
      <ul className="mt-1 space-y-0.5">
        {gate.reasons.map((r) => (
          <li key={r}>· {r}</li>
        ))}
      </ul>
      {gate.disclaimer && <div className="mt-1 text-[10px] opacity-70">{gate.disclaimer}</div>}
    </div>
  );
}
