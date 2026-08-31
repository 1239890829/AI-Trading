"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  generatePickReview,
  generatePicks,
  getPicksHistory,
  getPicksMeta,
  getPickReviews,
  getTodayPicks,
  type DailyPicksPayload,
  type PickReviewRow,
} from "@/lib/api";
import { fmt, pctColor, pctText } from "@/lib/format";

/**
 * 每日精选（/picks）：≤5 只精挑个股的瀑布流卡片。
 *
 * 组合纪律（grill-with-docs 澄清）：收盘定次日 + 换股门槛 15 分 + 盘中硬性失效例外；
 * 五维评分规则版多角色（情绪/消息/技术/基本面/资金），每张卡片全维度 basis 可解释；
 * 每日自动复盘（走势 vs 入选理由，走坏原因归类），周末元结论建议调权需人工确认。
 * 全页不构成买卖建议。
 */

const SUB_LABELS: [string, string][] = [
  ["sentiment", "情绪"],
  ["news", "消息"],
  ["tech", "技术"],
  ["fundamental", "基本"],
  ["capital", "资金"],
];

const REASON_LABELS: Record<string, string> = {
  event_expired: "事件失效",
  board_receding: "板块退潮",
  market_drag: "大盘拖累",
  data_issue: "数据源问题",
  news_gap: "消息卡顿",
  logic_failed: "入选逻辑失效",
  gone_well: "走势健康",
};

function PickCard({ item }: { item: DailyPicksPayload["items"][number] }) {
  return (
    <div className="mb-3 break-inside-avoid rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
      {/* 头：名称代码 + 现价 + 综合分 */}
      <div className="flex items-baseline justify-between gap-2">
        <div>
          <span className="text-sm font-semibold">{item.name ?? "--"}</span>
          <span className="ml-1.5 font-mono text-[10px] text-zinc-400">{item.symbol}</span>
        </div>
        <div className="text-right">
          <div className="font-mono text-base font-semibold tabular-nums">{fmt(item.price)}</div>
          <div className={`font-mono text-[10px] tabular-nums ${pctColor(item.change_pct)}`}>{pctText(item.change_pct)}</div>
        </div>
      </div>

      {/* 综合分 + 五维子评分条 */}
      <div className="mt-2 flex items-center gap-2">
        <span className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-xs font-semibold dark:bg-zinc-800" title="五维加权综合分（一票否决后）">
          {item.score}
        </span>
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

      {/* 买入范围 */}
      <div className="mt-2 rounded-lg bg-sky-500/5 px-2 py-1.5 text-[11px]" title={item.buy_range.basis}>
        <span className="text-zinc-400">买入参考区间</span>{" "}
        <span className="font-mono font-medium tabular-nums">
          {fmt(item.buy_range.low)} – {fmt(item.buy_range.high)}
        </span>
      </div>

      {/* 买入原因：各维度 basis 摘要（一票否决显式标红） */}
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
    </div>
  );
}

function PicksInner() {
  const sp = useSearchParams();
  const [data, setData] = useState<DailyPicksPayload | null>(null);
  const [history, setHistory] = useState<{ date: string; symbols: (string | null)[]; score_avg: number }[]>([]);
  const [reviews, setReviews] = useState<PickReviewRow[]>([]);
  const [meta, setMeta] = useState<{ reason_distribution: Record<string, number> } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const showReview = sp.get("review") === "1";

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
    setBusy(true);
    setError(null);
    try {
      await generatePickReview();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const items = data?.items ?? [];
  const badReviews = reviews.filter((r) => r.verdict === "bad");

  return (
    <main className="mx-auto flex h-full w-full max-w-[1400px] flex-col gap-2 overflow-hidden px-4 py-3">
      {/* 头部：标题 + 操作 + 组合纪律说明 */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs text-zinc-400">
        <h1 className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">每日精选</h1>
        <span title="收盘定次日；换股门槛：新候选综合分需超出组合内最弱者 ≥15 分；盘中仅硬性失效提前移除">
          组合纪律：收盘定次日 · 换股门槛 15 分 · 硬性失效盘中移除
        </span>
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
            disabled={busy}
            className="rounded border border-zinc-300 px-2 py-0.5 text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100 disabled:opacity-50"
            title="对最近组合逐只回顾：实际走势 vs 入选理由，走坏原因归类"
          >
            生成复盘
          </button>
        </div>
      </div>

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
            {badReviews.length > 0 && (
              <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
                <div className="mb-1 font-medium">当日复盘（走坏逐只归因）</div>
                {badReviews.map((r) => (
                  <div key={r.symbol + r.date} className="flex gap-2 border-b border-zinc-100 py-1 last:border-0 dark:border-zinc-800/60">
                    <span className="font-mono text-zinc-400">{r.symbol}</span>
                    <span>{r.name ?? ""}</span>
                    <span className={`font-mono tabular-nums ${pctColor(r.excess_pct)}`}>超额 {pctText(r.excess_pct)}</span>
                    <span className="text-zinc-500">{REASON_LABELS[r.reason_category] ?? r.reason_category}：{r.note}</span>
                  </div>
                ))}
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
        五维权重：情绪 20% / 消息 25% / 技术 25% / 基本面 15% / 资金 15% · 一票否决 ×0.4 ·
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
