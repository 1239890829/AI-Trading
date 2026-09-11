/** 紧凑行情条（详情页顶部）：名称/代码/质量徽标/加自选 + 价格涨跌 + 关键指标带。纯展示。
 *  hideWatchlist=true 时隐藏加自选区（指数无自选语义，防把 sh000001 之类加进自选）。 */
import { PriceFlash } from "@/components/price-flash";
import { QualityBadge } from "@/components/quality-badge";
import { SuspendedBadge } from "@/components/detail/suspended-badge";
import { fmt, fmtAmount, fmtVolume, pctColor, pctText, isHardQuality, sourceLabel, timeText } from "@/lib/format";
import type { Quote, TradingStatusInfo } from "@/types/market";

export function QuoteStrip({
  quote,
  inWatchlist,
  onAdd,
  hideWatchlist = false,
  tradingStatus = null,
}: {
  quote: Quote;
  inWatchlist: boolean;
  onAdd: () => void;
  hideWatchlist?: boolean;
  /** 停牌判定（日K 缺失交易日数推导）。null = 未判定，不渲染徽标。 */
  tradingStatus?: TradingStatusInfo | null;
}) {
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
    ["涨停", quote.limit_up_price != null ? fmt(quote.limit_up_price) : "--"],
  ];

  return (
    <div className="shrink-0 rounded-xl border border-zinc-200 px-4 py-1.5 dark:border-zinc-800">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <div className="flex items-baseline gap-2">
          <span className="text-base font-semibold">{quote.name ?? "--"}</span>
          <span className="font-mono text-xs text-zinc-600 dark:text-zinc-400">{quote.market}.{quote.symbol}</span>
          {/* 仅硬质量态出徽标（过期/休市/非法）；"正常"常驻是噪音、low"可疑"
              盘中瞬态闪现（2026-09-02 可疑标签修复，口径同列表行/指数卡） */}
          {isHardQuality(quote.quality) && <QualityBadge quality={quote.quality} reasons={quote.quality_reasons} />}
          <SuspendedBadge status={tradingStatus} />
          {!hideWatchlist &&
            (inWatchlist ? (
              <span className="text-xs text-zinc-600 dark:text-zinc-400">已在自选</span>
            ) : (
              <button onClick={onAdd} className="rounded border border-up/50 px-1.5 py-0.5 text-[11px] text-up-ink dark:text-up hover:bg-up/10">＋ 自选</button>
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
      <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[11px] text-zinc-600 dark:text-zinc-400">
        {strip.map(([k, v, title]) => (
          <span key={k} title={title}>
            {k} <span className="font-mono tabular-nums text-zinc-800 dark:text-zinc-200">{v}</span>
          </span>
        ))}
        <span className="ml-auto text-zinc-600 dark:text-zinc-400">
          {timeText(quote.data_timestamp)} · {sourceLabel(quote.source)}
        </span>
      </div>
    </div>
  );
}
