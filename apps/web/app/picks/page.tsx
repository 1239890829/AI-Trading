"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { workbenchUrlWithBack } from "@/lib/routing";
import {
  generatePickReview,
  generatePicks,
  getPicksHistory,
  getPicksMeta,
  getPickReviews,
  getTodayPicks,
  type DailyPicksPayload,
  type PickReviewRow,
  type StandAsideGate,
} from "@/lib/api";
import { PickCard, StandAsideBanner } from "@/components/picks/pick-card";
import { fmt, pctColor, pctText } from "@/lib/format";

/**
 * 每日精选（/picks）：≤5 只精挑个股的瀑布流卡片。
 *
 * 组合纪律（grill-with-docs 澄清）：收盘定次日 + 换股门槛 15 分 + 盘中硬性失效例外；
 * 五维评分规则版多角色（情绪/消息/技术/基本面/资金），每张卡片全维度 basis 可解释；
 * 每日自动复盘（走势 vs 入选理由，走坏原因归类），周末元结论建议调权需人工确认。
 * 全页不构成买卖建议。
 */

const REASON_LABELS: Record<string, string> = {
  event_expired: "事件失效",
  board_receding: "板块退潮",
  market_drag: "大盘拖累",
  data_issue: "数据源问题",
  news_gap: "消息卡顿",
  logic_failed: "入选逻辑失效",
  gone_well: "走势健康",
  entry_bad: "买点不对",
  sentiment_misread: "情绪误判",
  missed: "踏空未介入",
};

function PicksInner() {
  const sp = useSearchParams();
  const [data, setData] = useState<DailyPicksPayload | null>(null);
  const [history, setHistory] = useState<{ date: string; symbols: (string | null)[]; score_avg: number }[]>([]);
  const [reviews, setReviews] = useState<PickReviewRow[]>([]);
  const [meta, setMeta] = useState<{
    reason_distribution: Record<string, number>;
    role_performance?: { role: string; count: number; win_rate: number; avg_excess: number }[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // 「生成复盘」独立忙碌态：与「生成/刷新组合」共用 busy 会让两个按钮一起转圈，
  // 且复盘按钮没有 busy 文案分支时，点击后界面零变化 = 用户感知"点了没反应"。
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewOn, setReviewOn] = useState(false);
  const [reviewHint, setReviewHint] = useState<string | null>(null);
  // 复盘区原本**只在 URL 带 ?review=1 时渲染**（showReview 直接取自 query）。
  // 后果：点「生成复盘」→ 接口 0.2s 成功返回 5 条结果 → 但页面不展示 → 用户以为按钮坏了。
  // 现改为：URL 深链仍可用（?review=1），点击生成后自动展开复盘区。
  const showReview = sp.get("review") === "1" || reviewOn;

  const load = useCallback(async () => {
    try {
      const [d, h, r, m] = await Promise.all([
        getTodayPicks(),
        getPicksHistory(10),
        getPickReviews().catch(() => [] as PickReviewRow[]),
        getPicksMeta().catch(() => null),
      ]);
      setData(d);
      setHistory(h);
      setReviews(r);
      setMeta(m);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function runGenerate() {
    setBusy(true);
    setError(null);
    try {
      setData(await generatePicks());
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function runReview() {
    setReviewBusy(true);
    setError(null);
    try {
      // 用接口返回值**直接**上屏：即便后续 load() 失败，结果也不会丢
      const res = await generatePickReview();
      setReviewOn(true); // 生成成功后展开复盘区，否则用户看不到任何变化
      await load();
      setReviewHint(`已生成 ${res.reviews.length} 条复盘（${res.date}）`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setReviewBusy(false);
    }
  }

  const items = data?.items ?? [];

  return (
    <main className="mx-auto flex h-full w-full max-w-[1400px] flex-col gap-2 overflow-hidden px-4 py-3">
      {/* 头部：标题 + 操作 + 组合纪律说明 */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs text-zinc-400">
        <h1 className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">每日精选</h1>
        <span title="收盘定次日；换股门槛：新候选综合分需超出组合内最弱者 ≥15 分；盘中仅硬性失效提前移除">
          组合纪律：收盘定次日 · 换股门槛 15 分 · 硬性失效盘中移除
        </span>
        {data?.meta?.regime && (
          <span
            className="rounded border border-violet-500/40 bg-violet-500/10 px-1.5 py-0.5 text-[10px] text-violet-600 dark:text-violet-300"
            title={`炒作阶段（Speculation Regime）决定六维权重：${data.meta.regime.basis}`}
          >
            {data.meta.regime.regime}
          </span>
        )}
        {data?.meta?.market_phase && (
          <span className="rounded border border-zinc-300 px-1.5 py-0.5 text-[10px] text-zinc-500 dark:border-zinc-700">
            情绪 {data.meta.market_phase}
          </span>
        )}
        {data?.meta?.limit_up_count !== undefined && (
          <span className="text-[10px] text-zinc-400">
            涨停 {data.meta.limit_up_count} 家 · 最高 {data.meta.market_max_boards ?? 0} 板
          </span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          <button
            onClick={() => void runGenerate()}
            disabled={busy}
            className="rounded border border-sky-500/50 px-2 py-0.5 text-sky-400 hover:bg-sky-500/10 disabled:opacity-50"
            title="重跑五维评分管线（收盘后执行；覆盖当日组合）"
          >
            {busy ? "计算中…" : "生成/刷新组合"}
          </button>
          <button
            onClick={() => void runReview()}
            disabled={busy || reviewBusy}
            className="rounded border border-zinc-300 px-2 py-0.5 text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100 disabled:opacity-50"
            title="对最近组合逐只回顾：实际走势 vs 入选理由，走坏原因归类（生成后自动展开复盘区）"
          >
            {reviewBusy ? "生成中…" : "生成复盘"}
          </button>
          {reviewHint && !error && (
            <span className="text-[10px] text-emerald-600 dark:text-emerald-400" title="复盘已生成并展开在下方">
              ✓ {reviewHint}
            </span>
          )}
        </div>
      </div>

      {data?.meta?.gate && data.meta.gate.stand_aside && <StandAsideBanner gate={data.meta.gate} />}
      {error && (
        <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-600 dark:text-amber-300">{error}</div>
      )}
      {data?.stale && (
        <div className="shrink-0 rounded-lg border border-zinc-300 px-3 py-1.5 text-xs text-zinc-500 dark:border-zinc-700">
          当前展示 {data.date} 生成的组合（今日尚未生成）
        </div>
      )}
      {data?.note && <div className="shrink-0 text-xs text-zinc-400">{data.note}</div>}

      {/* 瀑布流卡片：CSS columns，每行多卡自适应 */}
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {items.length === 0 ? (
          <p className="py-12 text-center text-sm text-zinc-400">
            尚无精选组合。点右上「生成/刷新组合」跑一次五维评分管线
            <br />
            （候选池 = 活跃事件标的池 ∪ 当日涨停池 ∪ 热股榜，≤5 只输出，全程可解释不构成买卖建议）。
          </p>
        ) : (
          <div className="columns-1 gap-3 md:columns-2 xl:columns-3">
            {items.map((it) => (
              <PickCard key={it.symbol} item={it} />
            ))}
          </div>
        )}

        {/* 复盘区：走坏原因归类 + 历史组合 */}
        {showReview && (
          <div className="mt-4 space-y-3">
            {meta && meta.role_performance && meta.role_performance.length > 0 && (
              <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
                <div className="mb-1 font-medium">
                  梯队角色胜率（回答「能不能按题材抓妖」的直接证据；样本随交易日积累）
                </div>
                <table className="w-full">
                  <thead>
                    <tr className="text-zinc-400">
                      <th className="text-left font-normal">角色</th>
                      <th className="text-right font-normal">样本</th>
                      <th className="text-right font-normal">胜率</th>
                      <th className="text-right font-normal">平均超额</th>
                    </tr>
                  </thead>
                  <tbody>
                    {meta.role_performance.map((r) => (
                      <tr key={r.role} className="border-t border-zinc-100 dark:border-zinc-800/60">
                        <td className="py-1">{r.role}</td>
                        <td className="text-right font-mono tabular-nums">{r.count}</td>
                        <td className={`text-right font-mono tabular-nums ${r.win_rate >= 50 ? "text-up" : "text-down"}`}>
                          {r.win_rate}%
                        </td>
                        <td className={`text-right font-mono tabular-nums ${pctColor(r.avg_excess)}`}>
                          {pctText(r.avg_excess)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {meta && Object.keys(meta.reason_distribution).length > 0 && (
              <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
                <div className="mb-1 font-medium">复盘元结论：走坏原因分布（周末权重微调建议的输入；权重变更需人工确认）</div>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(meta.reason_distribution).map(([k, v]) => (
                    <span key={k} className="rounded bg-zinc-100 px-1.5 py-0.5 dark:bg-zinc-800">
                      {REASON_LABELS[k] ?? k}: {v}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {reviews.length > 0 && (
              <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
                <div className="mb-1 font-medium">
                  当日复盘（逐只归因：对在哪、错在哪）
                  <span className="ml-1.5 font-normal text-zinc-400">
                    走坏 {reviews.filter((r) => r.verdict === "bad").length} / 共 {reviews.length}
                  </span>
                </div>
                {reviews.map((r) => {
                  const label = REASON_LABELS[r.reason_category] ?? r.reason_category;
                  const tone =
                    r.verdict === "good"
                      ? "text-up"
                      : r.verdict === "bad"
                        ? "text-down"
                        : "text-zinc-400";
                  return (
                    <div
                      key={r.symbol + r.date}
                      className="flex flex-wrap gap-2 border-b border-zinc-100 py-1 last:border-0 dark:border-zinc-800/60"
                    >
                      <Link href={workbenchUrlWithBack(r.symbol)} title="查看个股详情" className="font-mono text-zinc-400 hover:text-sky-400 hover:underline">
                        {r.symbol}
                      </Link>
                      <span>{r.name ?? ""}</span>
                      <span className={`font-mono tabular-nums ${pctColor(r.excess_pct)}`}>
                        超额 {pctText(r.excess_pct)}
                      </span>
                      <span className={tone}>{label}</span>
                      <span className="w-full text-zinc-500">{r.note}</span>
                    </div>
                  );
                })}
              </div>
            )}
            {history.length > 0 && (
              <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
                <div className="mb-1 font-medium">历史组合（一致性可回溯）</div>
                {history.map((h) => (
                  <div key={h.date} className="flex gap-2 border-b border-zinc-100 py-1 last:border-0 dark:border-zinc-800/60">
                    <span className="font-mono text-zinc-400">{h.date}</span>
                    <span>均分 {h.score_avg}</span>
                    <span className="text-zinc-500">{h.symbols.join(" · ")}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="shrink-0 text-[10px] text-zinc-500">
        {data?.meta?.weights
          ? `当前权重（${data.meta.regime?.regime ?? "平衡"}）：${Object.entries(data.meta.weights)
              .map(([k, v]) => `${{ sentiment: "情绪", news: "消息", tech: "技术", fundamental: "基本", capital: "资金", echelon: "梯队" }[k] ?? k} ${Math.round(v * 100)}%`)
              .join(" / ")}`
          : "五维权重：情绪 20% / 消息 25% / 技术 25% / 基本面 15% / 资金 15%"}
        {" · 一票否决 ×0.4 · "}
        全部输出为可解释依据，不构成买卖建议 · 数据有延迟
      </div>
    </main>
  );
}

export default function PicksPage() {
  return (
    <Suspense fallback={<main className="p-6 text-sm text-zinc-400">加载中…</main>}>
      <PicksInner />
    </Suspense>
  );
}
