"use client";

import { Panel } from "@/components/panel";
import { StockLink, useStockRowNav } from "@/components/stock-link";
import { Skeleton } from "@/components/ui/loading";
import { IncrementalSentinel } from "@/components/ui/incremental-sentinel";
import { useIncremental } from "@/hooks/use-incremental";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { pctColor, pctText } from "@/lib/format";
import { CardHead, CardShell } from "@/components/picks/card-shell";
import { MasonryColumns } from "@/components/masonry-columns";
import { useEffect, useCallback, useMemo, useRef, useState } from "react";
import {
  getBoardFlowMembers,
  getBoardFlowMinute,
  getBoardFundFlow,
  type BoardFlowKind,
  type BoardFlowMembersPayload,
  type BoardFlowMinutePayload,
  type BoardFlowRange,
  type BoardFlowRow,
  type BoardFundFlowPayload,
} from "@/lib/api";
import { FlowIntradayChart, signedFmt } from "@/components/market/flow-intraday-chart";

/**
 * 板块资金流（L2 主视图 + 下钻抽屉，docs/summary/architecture-design.md §2）。
 *
 * 性能契约：后端一次翻页返回全量板块行，本组件排序/筛选纯内存（切换不触发请求）；
 * 轮询统一 30s；下钻抽屉数据独立 60s 轮询、关闭即停（usePollingFetch 卸载即清）。
 *
 * 口径：净流入=主力净额（东财官方板块口径，亿元）；主力占成交=净额/成交额×100（f184）；
 * 连续流入列 null=未沉淀（区别于 0=今日净流出）；排名Δ 正=上升（vs 昨日落盘榜位）。
 * 颜色纪律：净流入红 / 净流出绿（A 股惯例）。
 */

const KIND_CHIPS: { key: BoardFlowKind; label: string }[] = [
  { key: "concept", label: "概念" },
  { key: "industry", label: "行业" },
];

const RANGE_CHIPS: { key: BoardFlowRange; label: string }[] = [
  { key: "intraday", label: "今日" },
  { key: "5d", label: "5日" },
  { key: "10d", label: "10日" },
  { key: "20d", label: "20日" },
];

type SortKey = "main" | "chg" | "ratio" | "streak";

const SORT_CHIPS: { key: SortKey; label: string; title: string }[] = [
  { key: "main", label: "净流入", title: "按当前区间主力净额降序（默认）" },
  { key: "chg", label: "涨跌幅", title: "按板块涨跌幅降序" },
  { key: "ratio", label: "主力占成交", title: "按主力净额/成交额占比降序（仅今日口径）" },
  { key: "streak", label: "连续流入", title: "按连续净流入天数降序（仅沉淀板块可判）" },
];

const CHIP = "rounded px-1.5 py-0.5 text-[10px] transition-colors";
const CHIP_ON = "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900";
const CHIP_OFF = "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-400";

/** 瀑布流分页尺寸（滚动增量逻辑统一在 hooks/use-incremental，此处只留规格）。 */
const PAGE_SIZE = 30;
const PAGE_STEP = 30;
function rangeVal(r: BoardFlowRow, range: BoardFlowRange): number | null {
  if (range === "5d") return r.main_net_5d_yi;
  if (range === "10d") return r.main_net_10d_yi;
  if (range === "20d") return r.main_net_20d_yi ?? null;
  return r.main_net_yi;
}

/** 板块日度主力净额 diverging 柱（近 20 bar，HTML 条非 SVG——定高纪律）。 */
function BoardDailyBars({ bars }: { bars: { date: string; main_yi: number | null }[] }) {
  const last = bars.slice(-20);
  if (last.length === 0) return null;
  const maxAbs = Math.max(1, ...last.map((b) => Math.abs(b.main_yi ?? 0)));
  return (
    <div className="flex h-24 items-stretch gap-1" data-testid="board-daily-bars">
      {last.map((b) => {
        const v = b.main_yi;
        const h = v == null ? 0 : Math.max(2, (Math.abs(v) / maxAbs) * 40);
        return (
          <div key={b.date} className="flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5"
            title={`${b.date}｜主力 ${signedFmt(v)}`}>
            {v != null && v >= 0 && <div className="w-full rounded-t bg-up/80" style={{ height: h }} />}
            <div className="h-px w-full bg-zinc-300 dark:bg-zinc-700" />
            {v != null && v < 0 && <div className="w-full rounded-b bg-down/80" style={{ height: h }} />}
            <span className="w-full truncate text-center text-[8px] text-zinc-600 dark:text-zinc-400">{b.date.slice(5)}</span>
          </div>
        );
      })}
    </div>
  );
}

/** 板块下钻抽屉：分钟五档累计（延迟口径）+ 日度主力净额柱 + 成员个股资金排行 Top20。 */
function BoardFlowDrawer({ row, onClose }: { row: BoardFlowRow; onClose: () => void }) {
  const stockNav = useStockRowNav();
  const [minute, setMinute] = useState<BoardFlowMinutePayload | null>(null);
  const [members, setMembers] = useState<BoardFlowMembersPayload | null>(null);
  const [pending, setPending] = useState(true);

  // 关闭即卸载 → usePollingFetch 清理定时器，轮询随停
  usePollingFetch(async () => {
    try {
      const [m, mem] = await Promise.all([
        getBoardFlowMinute(row.board_code).catch(() => null),
        getBoardFlowMembers(row.board_code).catch(() => null),
      ]);
      if (m) setMinute(m);
      if (mem) setMembers(mem);
    } finally {
      setPending(false);
    }
  }, 60_000);

  const items = minute?.items ?? [];
  const bars = minute?.daily_bars ?? [];
  const memRows = members?.rows ?? [];

  return (
    <div className="fixed inset-0 z-50" data-testid="board-flow-drawer">
      <div className="anim-backdrop-in absolute inset-0 bg-zinc-950/40" onClick={onClose} />
      <div className="anim-slide-in-right absolute inset-y-0 right-0 flex w-[460px] max-w-[94vw] flex-col overflow-y-auto border-l border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-950">
        <div className="sticky top-0 z-10 flex items-start justify-between gap-2 border-b border-zinc-100 bg-white/95 px-4 py-3 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/95">
          <div>
            <p className="text-sm font-semibold">{row.name}</p>
            <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[10px] text-zinc-600 dark:text-zinc-400">
              <span className="font-mono">{row.board_code}</span>
              <span>榜位 #{row.rank}</span>
              {row.streak != null && row.streak > 0 && <span className="text-up-ink dark:text-up">连续流入 {row.streak} 天</span>}
              <span>板块口径：东财 f62（非成分股相加）</span>
            </p>
          </div>
          <button onClick={onClose} className="rounded p-1 text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800"
            aria-label="关闭" data-testid="drawer-close">✕</button>
        </div>

        <div className="space-y-4 px-4 py-3">
          {/* ① 板块分钟五档累计（延迟 ~15min 口径） */}
          <section>
            <p className="mb-1 text-[10px] text-zinc-600 dark:text-zinc-400">
              分钟资金累计（五档）· 东财延迟约 15 分钟口径 · 悬停看明细
              {minute?.degraded.length ? <span className="ml-1 text-amber-800 dark:text-amber-500">⚠ {minute.degraded.join("；")}</span> : null}
            </p>
            {pending && items.length === 0 ? (
              <Skeleton className="h-40 w-full" />
            ) : items.length > 0 ? (
              <FlowIntradayChart items={items} />
            ) : (
              <p className="py-6 text-center text-xs text-zinc-600 dark:text-zinc-400">
                分钟资金流暂不可用{minute?.reason ? `（${minute.reason}）` : ""}，60s 后自动重试。
              </p>
            )}
          </section>

          {/* ② 日度主力净额柱（落盘沉淀板块才有，零外呼） */}
          <section>
            <p className="mb-1 text-[10px] text-zinc-600 dark:text-zinc-400">日度主力净额（近 {Math.min(20, bars.length) || 20} 交易日；上红流入 / 下绿流出）</p>
            {bars.length > 0 ? (
              <BoardDailyBars bars={bars} />
            ) : (
              <p className="py-4 text-center text-xs text-zinc-600 dark:text-zinc-400">该板块日度历史尚未沉淀（每日收盘后自动累积 Top 板块）</p>
            )}
          </section>

          {/* ③ 成员个股资金排行 Top20 */}
          <section>
            <p className="mb-1 text-[10px] text-zinc-600 dark:text-zinc-400">
              成员个股资金排行 Top20（东财成员口径）
              {members?.degraded.length ? <span className="ml-1 text-amber-800 dark:text-amber-500">⚠ {members.degraded.join("；")}</span> : null}
            </p>
            {pending && memRows.length === 0 ? (
              <div className="space-y-1.5">
                {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-6 w-full" />)}
              </div>
            ) : memRows.length > 0 ? (
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-zinc-200 text-left text-[10px] text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
                    <th className="py-1 font-normal">代码</th>
                    <th className="py-1 font-normal">名称</th>
                    <th className="py-1 text-right font-normal">涨跌幅</th>
                    <th className="py-1 text-right font-normal">主力净额</th>
                    <th className="py-1 text-right font-normal">占成交</th>
                  </tr>
                </thead>
                <tbody>
                  {memRows.map((m) => (
                    <tr key={m.symbol} onClick={stockNav(m.symbol)} className="cursor-pointer border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900/50">
                      <td className="py-1 font-mono text-zinc-600 dark:text-zinc-400">
                        <StockLink symbol={m.symbol} className="font-mono text-zinc-600 dark:text-zinc-400">{m.symbol}</StockLink>
                      </td>
                      <td className="max-w-[8em] truncate py-1">
                        <StockLink symbol={m.symbol}>{m.name}</StockLink>
                      </td>
                      <td className={`py-1 text-right font-mono ${pctColor(m.change_pct)}`}>{pctText(m.change_pct)}</td>
                      <td className={`py-1 text-right font-mono ${m.main_net_yi == null ? "text-zinc-600 dark:text-zinc-400" : m.main_net_yi >= 0 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down"}`}>{signedFmt(m.main_net_yi)}</td>
                      <td className="py-1 text-right font-mono text-zinc-600 dark:text-zinc-400">{m.main_net_ratio != null ? `${m.main_net_ratio.toFixed(2)}%` : "--"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="py-4 text-center text-xs text-zinc-600 dark:text-zinc-400">成员排行暂不可用{members?.reason ? `（${members.reason}）` : ""}</p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

/** 板块卡片（瀑布流单元，视觉参照每日精选 PickCard：圆角卡 + 徽标 + mono 数值）。 */
function BoardCard({
  row,
  range,
  onClick,
}: {
  row: BoardFlowRow;
  range: BoardFlowRange;
  onClick: () => void;
}) {
  const v = rangeVal(row, range);
  const rangeLabel = range === "intraday" ? "净流入" : `${range}净流入`;
  return (
    <div onClick={onClick}>
      <CardShell flow className="cursor-pointer transition-colors hover:border-zinc-300 dark:hover:border-zinc-700">
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-sm font-semibold">{row.name}</span>
          <span className="shrink-0 font-mono text-[9px] text-zinc-600 dark:text-zinc-400">{row.board_code.replace(/^BK/, "")}</span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {row.rank_delta != null && row.rank_delta !== 0 && (
            <span
              className={`rounded px-1 py-0.5 text-[9px] font-medium ${
                row.rank_delta > 0 ? "bg-rose-500/10 text-rose-700 dark:text-rose-300" : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
              }`}
              title={`今日榜位 vs 昨日（正=上升）`}
            >
              {row.rank_delta > 0 ? `↑${row.rank_delta}` : `↓${-row.rank_delta}`}
            </span>
          )}
          {row.streak != null && row.streak >= 3 && (
            <span className="rounded bg-amber-500/10 px-1 py-0.5 text-[9px] text-amber-800 dark:text-amber-300" title="连续净流入天数">
              {row.streak}天连
            </span>
          )}
        </div>
      </div>

      <div className="mt-1.5 flex items-end justify-between">
        <div>
          <div className={`font-mono text-lg font-semibold leading-tight tabular-nums ${pctColor(row.change_pct)}`}>{pctText(row.change_pct)}</div>
          <div className="text-[9px] text-zinc-600 dark:text-zinc-400">涨跌幅</div>
        </div>
        <div className="text-right">
          <div className={`font-mono text-lg font-semibold leading-tight tabular-nums ${v == null ? "text-zinc-600 dark:text-zinc-400" : v >= 0 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down"}`}>
            {v == null ? "--" : signedFmt(v)}
          </div>
          <div className="text-[9px] text-zinc-600 dark:text-zinc-400">{rangeLabel}(亿)</div>
        </div>
      </div>

      <div className="mt-2 space-y-0.5 border-t border-zinc-100 pt-1.5 text-[10px] text-zinc-600 dark:text-zinc-400 dark:border-zinc-800/60">
        <div className="flex justify-between">
          <span>主力占成交</span>
          <span className="font-mono tabular-nums">{row.main_net_ratio != null ? `${row.main_net_ratio.toFixed(2)}%` : "--"}</span>
        </div>
        <div className="flex justify-between">
          <span>连续流入</span>
          <span className={`font-mono tabular-nums ${row.streak == null ? "" : row.streak >= 3 ? "font-semibold text-up-ink dark:text-up" : ""}`}>
            {row.streak == null ? "—" : `${row.streak}天`}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="shrink-0">龙头</span>
          <span className="truncate text-right" title={row.leader_symbol ? `${row.leader_name} ${row.leader_symbol}` : undefined}>
            {row.leader_name ?? "—"}
          </span>
        </div>
      </div>
      </CardShell>
    </div>
  );
}

export function BoardFlowPanel() {
  const [kind, setKind] = useState<BoardFlowKind>("concept");
  const [range, setRange] = useState<BoardFlowRange>("intraday");
  const [sortKey, setSortKey] = useState<SortKey>("main");
  const [filters, setFilters] = useState<Set<string>>(new Set());
  const [payload, setPayload] = useState<BoardFundFlowPayload | null>(null);
  const [pending, setPending] = useState(true);
  const [drawer, setDrawer] = useState<BoardFlowRow | null>(null);

  const load = useCallback(async () => {
    setPayload(await getBoardFundFlow(kind, range));
  }, [kind, range]);

  usePollingFetch(async () => {
    try {
      await load();
    } catch {
      /* 失败保持上一次数据 + 降级提示 */
    } finally {
      setPending(false);
    }
  }, 30_000);

  // 维度/区间切换立即补拉（usePollingFetch 只在挂载与周期触发）
  const prevKey = useRef("concept/intraday");
  useEffect(() => {
    const key = `${kind}/${range}`;
    if (prevKey.current === key) return;
    prevKey.current = key;
    void load();
  }, [kind, range, load]);

  const rows = useMemo(() => {
    const src = payload?.rows ?? [];
    const filtered = filters.size === 0
      ? src
      : src.filter((r) => {
          if (filters.has("inflow") && !((rangeVal(r, range) ?? -Infinity) > 0)) return false;
          if (filters.has("ratio5") && !((r.main_net_ratio ?? -Infinity) > 5)) return false;
          if (filters.has("streak3") && !((r.streak ?? -Infinity) >= 3)) return false;
          if (filters.has("rising") && !((r.rank_delta ?? -Infinity) > 0)) return false;
          return true;
        });
    const val = (r: BoardFlowRow): number | null =>
      sortKey === "chg" ? r.change_pct
        : sortKey === "ratio" ? r.main_net_ratio
          : sortKey === "streak" ? r.streak
            : rangeVal(r, range);
    return [...filtered].sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1; // 判不出殿后（不冒充 0）
      if (vb == null) return -1;
      return vb - va;
    });
  }, [payload, filters, sortKey, range]);

  // 滚动增量加载：统一走 hooks/use-incremental（scroll 事件实现，理由见其 docstring）。
  // resetKey = 维度/区间 → 换维度后页码归位首页，避免"新维度直接显示已加载 120/200"。
  const { shown, visible, sentinelRef } = useIncremental(rows, {
    pageSize: PAGE_SIZE,
    step: PAGE_STEP,
    resetKey: `${kind}/${range}`,
  });
  const degraded = payload?.degraded ?? [];

  function toggleFilter(key: string) {
    setFilters((cur) => {
      const next = new Set(cur);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <Panel
      title="板块资金流"
      className="min-h-0 shrink-0 overflow-hidden"
      extra={
        <span className="text-[10px] text-zinc-600 dark:text-zinc-400">
          {payload?.updated_at ? `更新 ${payload.updated_at}` : "--"}
          {payload?.kind === "concept" && payload.total_boards != null ? ` · 共 ${payload.total_boards} 个板块` : ""}
          {payload?.coverage != null ? ` · 沉淀 ${payload.coverage} 板块` : ""}
        </span>
      }
    >
      <div className="flex h-full min-h-0 flex-col px-4 py-2.5">
        {/* 控制行：维度 / 区间 / 排序 / 筛选 */}
        <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5">
          <div className="flex items-center gap-1">
            {KIND_CHIPS.map((c) => (
              <button key={c.key} onClick={() => setKind(c.key)} className={`${CHIP} ${kind === c.key ? CHIP_ON : CHIP_OFF}`}>{c.label}</button>
            ))}
          </div>
          <div className="flex items-center gap-1">
            {RANGE_CHIPS.map((c) => (
              <button key={c.key} onClick={() => setRange(c.key)} className={`${CHIP} ${range === c.key ? CHIP_ON : CHIP_OFF}`}>{c.label}</button>
            ))}
          </div>
          <div className="flex items-center gap-1" title="点击切换排序维度（均为降序，判不出殿后）">
            {SORT_CHIPS.map((c) => (
              <button key={c.key} onClick={() => setSortKey(c.key)} title={c.title} className={`${CHIP} ${sortKey === c.key ? CHIP_ON : CHIP_OFF}`}>{c.label}</button>
            ))}
          </div>
          <div className="flex items-center gap-1" title="多选叠加筛选">
            <button onClick={() => toggleFilter("inflow")} className={`${CHIP} ${filters.has("inflow") ? CHIP_ON : CHIP_OFF}`}>净流入&gt;0</button>
            <button onClick={() => toggleFilter("ratio5")} className={`${CHIP} ${filters.has("ratio5") ? CHIP_ON : CHIP_OFF}`}>主力占比&gt;5%</button>
            <button onClick={() => toggleFilter("streak3")} className={`${CHIP} ${filters.has("streak3") ? CHIP_ON : CHIP_OFF}`}>连续流入≥3</button>
            <button onClick={() => toggleFilter("rising")} className={`${CHIP} ${filters.has("rising") ? CHIP_ON : CHIP_OFF}`}>排名上升</button>
          </div>
        </div>

        {degraded.length > 0 && (
          <p className="mt-1.5 shrink-0 text-[10px] text-amber-800 dark:text-amber-500">⚠ {degraded.join("；")}</p>
        )}

        {pending && !payload ? (
          <div className="mt-2 space-y-1.5">
            {Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-6 w-full" />)}
          </div>
        ) : payload && !payload.available ? (
          <p className="py-8 text-center text-sm text-zinc-600 dark:text-zinc-400">
            板块资金流暂不可用（{payload.reason ?? "数据源异常"}），下一拍自动重试。
          </p>
        ) : !payload ? (
          <p className="py-8 text-center text-sm text-zinc-600 dark:text-zinc-400">
            板块资金流加载失败（后端不可达或版本未更新），下一拍自动重试。
          </p>
        ) : (
          <>
            <div className="mt-2 min-h-0 flex-1 overflow-y-auto" data-testid="board-flow-table">
              {shown.length === 0 ? (
                <p className="py-10 text-center text-xs text-zinc-600 dark:text-zinc-400">当前筛选无匹配板块</p>
              ) : (
                <MasonryColumns>
                  {shown.map((r) => (
                    <div key={r.board_code} data-testid={`board-row-${r.board_code}`} onClick={() => setDrawer(r)}>
                      <BoardCard row={r} range={range} onClick={() => setDrawer(r)} />
                    </div>
                  ))}
                </MasonryColumns>              )}
              <IncrementalSentinel
                sentinelRef={sentinelRef}
                visible={visible}
                total={rows.length}
                unit="个板块"
                testId="board-flow-sentinel"
              />
            </div>
            <p className="mt-1 shrink-0 text-[10px] text-zinc-600 dark:text-zinc-400">
              点卡看板块下钻（分钟累计 + 日度柱 + 成员排行）。净流入为东财官方板块口径（非成分股相加）；
              「主力占成交」仅今日区间；连续流入/20日区间来自收盘落盘沉淀，未沉淀显示 —。
            </p>
          </>
        )}
      </div>

      {drawer && <BoardFlowDrawer row={drawer} onClose={() => setDrawer(null)} />}
    </Panel>
  );
}
