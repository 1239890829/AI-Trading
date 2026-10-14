"use client";

/** 紧凑行情条（详情页顶部）：名称/代码/质量徽标/加自选 + 价格涨跌 + 关键指标带。手机可就地收起详细指标。
 *  hideWatchlist=true 时隐藏加自选区（指数无自选语义，防把 sh000001 之类加进自选）。 */
import { useId, useState, useSyncExternalStore, type ReactNode } from "react";
import { useExitPresence } from "@/hooks/use-exit-presence";
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { SuspendedBadge } from "@/components/detail/suspended-badge";
import { fmt, fmtAmount, fmtVolume, pctColor, pctText, sourceLabel, timeText } from "@/lib/format";
import { isPositivePrice } from "@/lib/minute-axis";
import type { Quote, TradingStatusInfo } from "@/types/market";
import {STREAM_STATUS_LABEL, type StreamStatus} from "@/hooks/use-quote-stream";
import {ModalShell} from "@/components/ui/modal-shell";

const COMPACT_QUERY = "(max-width: 767px), (min-width: 1024px) and (max-height: 740px)";
function compactSnapshot() {return typeof window.matchMedia === "function" && window.matchMedia(COMPACT_QUERY).matches;}
function subscribeCompact(listener: () => void) {
  if (typeof window.matchMedia !== "function") return () => {};
  const query = window.matchMedia(COMPACT_QUERY);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}

export function QuoteStrip({
  quote,
  inWatchlist,
  onAdd,
  hideWatchlist = false,
  tradingStatus = null,
  active = true,
  connectionStatus,
  issue,
  diagnostics,
}: {
  quote: Quote;
  inWatchlist: boolean | null;
  onAdd: () => void;
  hideWatchlist?: boolean;
  /** 停牌判定（日K 缺失交易日数推导）。null = 未判定，不渲染徽标。 */
  tradingStatus?: TradingStatusInfo | null;
  active?: boolean;
  connectionStatus?: StreamStatus;
  issue?: {label: string; title?: string} | null;
  diagnostics?: ReactNode;
}) {
  const [metricsOpen, setMetricsOpen] = useState(false);
  const compact = useSyncExternalStore(subscribeCompact, compactSnapshot, () => false);
  const metricsVisible = !compact || metricsOpen;
  const metricsPresence = useExitPresence(metricsVisible ? true : null);
  const metricsId = useId();
  const [openedFor, setOpenedFor] = useState<string | null>(null);
  const diagnosticPresence = useExitPresence(active && openedFor === quote.symbol ? quote.symbol : null);
  if (openedFor !== null && (!active || openedFor !== quote.symbol)) setOpenedFor(null);
  // PE 缺失原因文案：原版把"停牌"写进猜测清单，但那是**猜**的——PE 缺失
  // 绝大部分是数据源未提供（新股/亏损股），真正的停牌应由 tradingStatus 判定。
  // 现在停牌状态真实可得，两者各归其位：这里只说"数据源未提供"，停牌由徽标表达。
  const peMissing = "数据源未提供市盈率（常见于新股、亏损股）";
  const strip: [string, string, string?][] = [
    ["今开", fmt(quote.open)],
    ["最高", fmt(quote.high)],
    ["最低", fmt(quote.low)],
    ["昨收", fmt(quote.prev_close)],
    ["成交量", fmtVolume(quote.volume) + "手"],
    ["成交额", fmtAmount(quote.amount)],
    ["换手", quote.turnover_rate != null ? `${fmt(quote.turnover_rate)}%` : "--"],
    // PE/PB 兜底（2026-09-01）：后端已补全（fill_valuation），仍缺失说明数据源
    // 确实未提供——显示「暂无」并注明原因，不留白
    ["PE", quote.pe_ttm != null ? fmt(quote.pe_ttm) : "暂无", quote.pe_ttm != null ? undefined : peMissing],
    ["PB", quote.pb != null ? fmt(quote.pb) : "暂无", quote.pb != null ? undefined : "数据源未提供市净率"],
    ["市值", quote.total_mktcap_yi != null ? `${fmt(quote.total_mktcap_yi)}亿` : "--"],
    [
      "涨停",
      isPositivePrice(quote.limit_up_price) ? fmt(quote.limit_up_price) : "--",
      isPositivePrice(quote.limit_up_price) ? undefined : "数据源未提供有效涨停价",
    ],
  ];

  return (
    <div className="ui-card quote-sheet shrink-0 rounded-xl border border-zinc-200 px-4 py-1.5 dark:border-zinc-800" data-metrics-expanded={metricsOpen}>
      <div className="quote-sheet-head flex flex-wrap items-baseline justify-between gap-x-4">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-base font-semibold">{quote.name ?? "--"}</span>
          <span className="font-mono text-xs text-zinc-600 dark:text-zinc-400">{quote.market}.{quote.symbol}</span>
          {/* 可见性策略单点在 QualityBadge 内部（shouldShowQualityBadge）：正常出静音灰「正常」，
              low/medium 瞬态不出（2026-09-02 闪烁修复），stale/invalid 常显。
              2026-09-14 口径统一：此前调用点各自判断，导致同一数据在盘面/工作台表现相反。 */}
          <span data-quote-status-slot className="inline-flex h-7 w-[6.4em] shrink-0 items-center text-xs">
            {issue ? <button type="button" aria-label="查看行情问题" aria-haspopup="dialog" title={issue.title} data-quality={quote.quality} onClick={() => setOpenedFor(quote.symbol)} className={`inline-flex h-6 w-full items-center justify-center whitespace-nowrap rounded px-1 text-xs ${quote.quality === "invalid" ? "bg-red-500/10 text-red-700 dark:text-red-400" : "bg-amber-500/10 text-orange-800 dark:text-orange-400"}`}>{issue.label}</button>
              : <QualityBadge quality={quote.quality} reasons={quote.quality_reasons} symbol={quote.symbol} market={quote.market} dataTimestamp={quote.data_timestamp} session={quote.display_session} />}
          </span>
          <SuspendedBadge status={tradingStatus} />
          {!hideWatchlist &&
            (inWatchlist === true ? (
              <span className="text-xs text-zinc-600 dark:text-zinc-400">已在自选</span>
            ) : (
              <>
                {inWatchlist === null && <span className="text-xs text-zinc-600 dark:text-zinc-400" title="自选列表尚未成功确认">自选待确认</span>}
                <button data-action="secondary" onClick={onAdd} title={inWatchlist === null ? "自选列表尚未确认；仍可尝试加入自选" : undefined}>＋ 自选</button>
              </>
            ))}
        </div>
        <div className="flex items-baseline gap-2">
          {quote.price == null ? (
            <span className="text-sm text-zinc-600 dark:text-zinc-400">未开盘</span>
          ) : (
            <PriceFlash value={quote.price} className={`font-mono text-2xl font-semibold tabular-nums ${pctColor(quote.change_pct)}`}>{fmt(quote.price)}</PriceFlash>
          )}
          <span className={`font-mono text-xs tabular-nums ${pctColor(quote.change)}`}>
            {quote.change != null ? `${quote.change > 0 ? "+" : ""}${fmt(quote.change)}` : "--"}（{pctText(quote.change_pct)}）
          </span>
        </div>
      </div>
      <div className="quote-sheet-meta flex min-h-7 min-w-0 items-center gap-2">
        <span className="quote-source text-zinc-600 dark:text-zinc-400">
          {timeText(quote.data_timestamp)} · {sourceLabel(quote.source)}
        </span>
        {connectionStatus && <span data-transport={connectionStatus} title={STREAM_STATUS_LABEL[connectionStatus].title} className={`inline-flex w-[8.4em] shrink-0 items-center whitespace-nowrap text-[10px] ${STREAM_STATUS_LABEL[connectionStatus].cls}`}>{STREAM_STATUS_LABEL[connectionStatus].text}</span>}
        <button type="button" className="quote-metrics-toggle hidden" aria-expanded={metricsOpen} aria-controls={metricsId} onClick={() => setMetricsOpen(open => !open)}>
          行情指标<svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m7 10 5 5 5-5" /></svg>
        </button>
      </div>
      {metricsPresence.value && <div className="quote-metrics-presence motion-disclosure" data-open={metricsVisible} aria-hidden={!metricsVisible || undefined} inert={!metricsVisible}>
      <div className="motion-disclosure-body">
      <div id={metricsId} className="quote-sheet-metrics text-[11px] text-zinc-600 dark:text-zinc-400" role="region" aria-label="行情详细指标" tabIndex={metricsVisible ? 0 : -1}>
        {strip.map(([k, v, title]) => (
          <span className="quote-metric" key={k} title={title}>
            <span>{k}</span> <span className="font-mono tabular-nums text-zinc-800 dark:text-zinc-200">{v}</span>
          </span>
        ))}
      </div>
      </div>
      </div>}
      {diagnosticPresence.value && <ModalShell presentation="drawer" open={diagnosticPresence.active} onClose={() => setOpenedFor(null)} label="行情诊断" size="md" zIndex={60} header={<h2 className="text-sm font-medium">行情 · 数据诊断</h2>} bodyTabIndex={0} bodyClassName="data-scroll overflow-y-auto px-4 py-3 text-xs leading-relaxed [overflow-wrap:anywhere]">{diagnosticPresence.active && <div className="space-y-3">{diagnostics}</div>}</ModalShell>}
    </div>
  );
}
