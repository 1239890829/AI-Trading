"use client";

import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Panel } from "@/components/panel";
import {
  getHeatmap,
  getWatchlist,
  type HeatmapGroup,
  type HeatmapPayload,
  type HeatmapStock,
} from "@/lib/api";
import { fmtAmount, pctText } from "@/lib/format";
import { workbenchUrl } from "@/lib/routing";
import { usePollingFetch } from "@/hooks/use-polling-fetch";

/**
 * 市场页 · 云图 tab（原 /heatmap 页迁移，2026-09-01 系统重构）。
 *
 * A 股云图：行业分组 treemap。面积=流通市值，颜色=当日涨跌幅（红涨绿跌）。
 * squarified 算法自研（Bruls et al.），零依赖；组点击下钻、范围切换、hover 详情。
 * 无独立数据源（复用市场快照），故降级为市场页 tab 而非一级导航。
 * 个股格子点击 → 详情（L7 联动，2026-09-03）；「其他」聚合格无 symbol 不跳。
 */

interface SqItem {
  key: string;
  value: number;
  area: number;
  stock?: HeatmapStock;
  group?: HeatmapGroup;
}

interface SqRect {
  x: number;
  y: number;
  w: number;
  h: number;
  item: SqItem;
}

function worstAspect(row: { area: number }[], rowArea: number, side: number, candArea: number): number {
  // 行长 = side（短边）；各条目厚度 = (area_i/rowArea) × side
  let worst = 0;
  const total = rowArea + candArea;
  for (const r of row) {
    const thick = (r.area / total) * side;
    worst = Math.max(worst, Math.max(side / thick, thick / side));
  }
  const candThick = (candArea / total) * side;
  worst = Math.max(worst, Math.max(side / candThick, candThick / side));
  return worst;
}

/** 标准 squarified treemap（Bruls et al.）：行沿剩余区域**短边**铺设，行长=短边。 */
function squarify(items: Omit<SqItem, "area">[], x: number, y: number, w: number, h: number): SqRect[] {
  const total = items.reduce((s, i) => s + i.value, 0);
  if (total <= 0 || w <= 0 || h <= 0 || items.length === 0) return [];
  const scale = (w * h) / total;
  const rest: SqItem[] = items
    .map((i) => ({ ...i, area: i.value * scale }))
    .sort((a, b) => b.area - a.area);
  const rects: SqRect[] = [];
  let cx = x, cy = y, cw = w, ch = h;
  let guard = 0;
  while (rest.length > 0 && guard++ < items.length + 4) {
    const horizontal = cw < ch; // 宽<高 → 行沿顶部横铺（行长=cw 短边）
    const side = horizontal ? cw : ch;
    const row: SqItem[] = [];
    let rowArea = 0;
    let bestWorst = Infinity;
    while (rest.length > 0) {
      const cand = rest[0];
      const worst = worstAspect(row, rowArea, side, cand.area);
      if (row.length === 0 || worst <= bestWorst) {
        row.push(cand);
        rowArea += cand.area;
        bestWorst = worst;
        rest.shift();
      } else {
        break;
      }
    }
    if (rowArea <= 0 || side <= 0) break;
    const thick = rowArea / side;
    let off = 0;
    for (const it of row) {
      const seg = (it.area / rowArea) * side;
      if (horizontal) rects.push({ x: cx + off, y: cy, w: seg, h: thick, item: it });
      else rects.push({ x: cx, y: cy + off, w: thick, h: seg, item: it });
      off += seg;
    }
    if (horizontal) { cy += thick; ch -= thick; } else { cx += thick; cw -= thick; }
  }
  return rects;
}

function pctColor(pct: number): string {
  // 红涨绿跌；饱和度随 |pct| 增强（±6% 封顶），近零回灰
  const mag = Math.min(Math.abs(pct) / 6, 1);
  const alpha = 0.18 + mag * 0.72;
  if (pct > 0) return `rgba(239,68,68,${alpha})`;
  if (pct < 0) return `rgba(16,185,129,${alpha})`;
  return "rgba(120,120,130,0.35)";
}

// 收敛说明（2026-09-11 冗余清理）：此处原有本地 `pctText`，与 `lib/format.ts`
// 的导出版在涨跌幅实际区间（|pct| < 1000）内输出完全一致。对外文案按项目纪律
// 单点收口在 format.ts——各页各写一份，早晚出现「这页 +1.20%、那页 +1.2%」。

/**
 * 单个个股格（memo 化）。
 *
 * 为什么必须拆出来：云图单屏一次性渲染约 1540 个 `<g>`
 * （实测 `/api/market/heatmap` = 128 组 / 1540 只），而 hover 详情状态提升在
 * 父组件上——若不 memo，鼠标每跨一个格子就重渲染整张 SVG，盘中高频移动时
 * 主线程被 1500+ 节点重建占满，观感就是"卡"。
 *
 * memo 生效的前提是 `rect` 引用稳定：`visible` → `groupRects` → `stockRects`
 * 三级 useMemo 保证 hover 变化时不会重算，故 `rect` 身份不变。回调亦用
 * useCallback 固定，否则每次渲染新函数会让 memo 完全失效。
 */
const HeatmapCell = memo(function HeatmapCell({
  rect,
  stock,
  clickable,
  onEnter,
  onLeave,
  onOpen,
}: {
  rect: SqRect;
  stock: HeatmapStock;
  clickable: boolean;
  onEnter: (s: HeatmapStock) => void;
  onLeave: (s: HeatmapStock) => void;
  onOpen: (symbol: string) => void;
}) {
  const w = Math.max(rect.w - 1, 0);
  const h = Math.max(rect.h - 1, 0);
  // 小于 3px 的格子不画（肉眼不可辨，纯属 DOM 负担）
  if (w < 3 || h < 3) return null;
  return (
    <g
      onMouseEnter={() => onEnter(stock)}
      onMouseLeave={() => onLeave(stock)}
      onClick={clickable ? () => onOpen(stock.symbol) : undefined}
      className={clickable ? "cursor-pointer" : undefined}
    >
      <rect x={rect.x} y={rect.y} width={w} height={h} fill={pctColor(stock.change_pct)} />
      {w > 52 && h > 24 && (
        <>
          <text x={rect.x + w / 2} y={rect.y + h / 2 - 2} textAnchor="middle" fontSize={11} fill="#fafafa" className="select-none">
            {stock.name.slice(0, 6)}
          </text>
          <text x={rect.x + w / 2} y={rect.y + h / 2 + 12} textAnchor="middle" fontSize={11} fontWeight="bold" fill="#fafafa" className="select-none">
            {pctText(stock.change_pct)}
          </text>
        </>
      )}
    </g>
  );
});

export function HeatmapTab() {
  const router = useRouter();
  const [data, setData] = useState<HeatmapPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState("");
  const [scope, setScope] = useState<"all" | "watch">("all");
  const [focusGroup, setFocusGroup] = useState<HeatmapGroup | null>(null);
  const [hover, setHover] = useState<HeatmapStock | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await getHeatmap());
      setError(null);
      setUpdatedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  usePollingFetch(load, 30_000);

  const watchSymbols = useMemo(() => new Set<string>(), []);
  const [watchLoaded, setWatchLoaded] = useState(false);
  useEffect(() => {
    if (scope !== "watch" || watchLoaded) return;
    getWatchlist()
      .then((list) => {
        for (const i of list) watchSymbols.add(i.symbol);
        setWatchLoaded(true);
        // 触发重渲染
        setData((prev) => (prev ? { ...prev } : prev));
      })
      .catch(() => {});
  }, [scope, watchLoaded, watchSymbols]);

  const visible = useMemo(() => {
    if (!data) return [];
    if (scope === "watch") {
      const stocks = data.groups
        .flatMap((g) => g.stocks.filter((s) => !s.is_aggregate && watchSymbols.has(s.symbol)));
      if (stocks.length === 0) return [];
      return [{
        industry: "自选", float_cap_yi: stocks.reduce((s, x) => s + x.float_cap_yi, 0),
        change_pct_w: 0, count: stocks.length, stocks,
      } as HeatmapGroup];
    }
    return focusGroup ? [focusGroup] : data.groups;
  }, [data, scope, focusGroup, watchSymbols]);

  const W = 1600, H = 900;
  const groupRects = useMemo(
    () => squarify(visible.map((g) => ({ key: g.industry, value: g.float_cap_yi, group: g })), 0, 0, W, H),
    [visible]
  );
  const stockRects = useMemo(() => {
    // 组内个股：正常视图下每组内部单独 squarify（组矩形即画布）
    const out: { rect: SqRect; group: HeatmapGroup }[] = [];
    for (const gr of groupRects) {
      const g = gr.item.group;
      if (!g || g.stocks.length === 0) continue;
      const inner = squarify(
        g.stocks.map((s) => ({ key: `${g.industry}:${s.symbol || s.name}`, value: s.float_cap_yi, stock: s })),
        gr.x + 1, gr.y + 18, gr.w - 2, Math.max(gr.h - 19, 0)
      );
      for (const r of inner) out.push({ rect: r, group: g });
    }
    return out;
  }, [groupRects]);

  // 回调引用固定：HeatmapCell 的 memo 依赖它们不变，否则 memo 形同虚设
  const handleEnter = useCallback((s: HeatmapStock) => setHover(s), []);
  const handleLeave = useCallback(
    (s: HeatmapStock) => setHover((p) => (p === s ? null : p)),
    []
  );
  const handleOpen = useCallback((symbol: string) => router.push(workbenchUrl(symbol)), [router]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {error && (
        <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-800 dark:text-amber-300">{error}</div>
      )}

      <Panel
        title={`A 股云图${focusGroup ? ` · ${focusGroup.industry}` : ""}`}
        className="min-h-0 flex-1"
        bodyClassName="overflow-hidden"
        extra={
          <div className="flex items-center gap-2 text-xs">
            <button
              onClick={() => { setScope("all"); setFocusGroup(null); }}
              className={`rounded px-2 py-0.5 ${scope === "all" ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-zinc-600 dark:text-zinc-400"}`}
            >
              全市场
            </button>
            <button
              onClick={() => { setScope("watch"); setFocusGroup(null); }}
              className={`rounded px-2 py-0.5 ${scope === "watch" ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-zinc-600 dark:text-zinc-400"}`}
            >
              自选
            </button>
            {focusGroup && (
              <button onClick={() => setFocusGroup(null)} className="rounded border border-zinc-300 px-2 py-0.5 text-zinc-600 dark:text-zinc-400 dark:border-zinc-600">
                返回全部
              </button>
            )}
          </div>
        }
      >
        {!data ? (
          /* 首次加载（含行业映射构建约需数秒）：同构骨架占位，避免整块突然出现 */
          <div className="h-full w-full p-4" aria-hidden>
            <div className="grid h-full grid-cols-6 grid-rows-4 gap-2">
              {Array.from({ length: 24 }, (_, i) => (
                <div key={i} className="animate-pulse rounded-md bg-zinc-200/60 dark:bg-zinc-800/50" style={{ opacity: 1 - i * 0.02 }} />
              ))}
            </div>
            <p className="mt-3 text-center text-xs text-zinc-600 dark:text-zinc-400">云图构建中…（首次含行业映射构建约需数秒）</p>
          </div>
        ) : (
          <div className="relative h-full w-full">
            <svg viewBox={`0 0 ${W} ${H}`} className="h-full w-full" preserveAspectRatio="xMidYMid meet">
              {groupRects.map((gr) => {
                const g = gr.item.group!;
                const showHeader = gr.h > 26 && gr.w > 90;
                return (
                  <g key={`g-${g.industry}`}>
                    <rect
                      x={gr.x} y={gr.y} width={gr.w} height={gr.h}
                      fill="rgba(120,120,130,0.06)"
                      stroke="rgba(120,120,130,0.45)" strokeWidth={1.5}
                      className={focusGroup ? "" : "cursor-pointer"}
                      onClick={() => !focusGroup && setFocusGroup(g)}
                    />
                    {showHeader && (
                      <text x={gr.x + 6} y={gr.y + 14} fontSize={12} className="select-none" fill="#a1a1aa">
                        {g.industry}
                        <tspan fill={g.change_pct_w >= 0 ? "#ef4444" : "#10b981"} fontWeight="bold">
                          {"  "}{pctText(g.change_pct_w)}
                        </tspan>
                      </text>
                    )}
                  </g>
                );
              })}
              {stockRects.map(({ rect, group }) => {
                const s = rect.item.stock!;
                // L7（切片 E）：个股格点击进详情；聚合格（「其他」）无 symbol 不跳
                return (
                  <HeatmapCell
                    key={`${group.industry}:${s.symbol || s.name}`}
                    rect={rect}
                    stock={s}
                    clickable={!s.is_aggregate && !!s.symbol}
                    onEnter={handleEnter}
                    onLeave={handleLeave}
                    onOpen={handleOpen}
                  />
                );
              })}
            </svg>

            {/* hover 详情 */}
            {hover && (
              <div className="pointer-events-none absolute right-3 top-3 rounded-lg border border-zinc-200 bg-white/95 px-3 py-2 text-xs shadow-lg dark:border-zinc-700 dark:bg-zinc-900/95">
                <div className="font-semibold">{hover.name} <span className="font-mono text-zinc-600 dark:text-zinc-400">{hover.symbol}</span></div>
                <div className="mt-1 space-y-0.5 font-mono text-zinc-600 dark:text-zinc-300">
                  <div>涨跌 <span className={hover.change_pct >= 0 ? "text-red-700 dark:text-red-500" : "text-emerald-700 dark:text-emerald-500"}>{pctText(hover.change_pct)}</span></div>
                  <div>价格 {hover.price != null ? hover.price : "--"}</div>
                  <div>流通市值 {fmtAmount(hover.float_cap_yi * 1e8)}</div>
                  <div>成交额 {fmtAmount(hover.amount_yi * 1e8)}</div>
                </div>
              </div>
            )}

            {/* 图例 */}
            <div className="pointer-events-none absolute bottom-2 left-3 flex items-center gap-1 text-[10px] text-zinc-600 dark:text-zinc-400">
              <span>-6%</span>
              {[-6, -4, -2, 0, 2, 4, 6].map((v) => (
                <span key={v} className="inline-block h-3 w-5 rounded-sm" style={{ background: pctColor(v) }} />
              ))}
              <span>+6%</span>
            </div>
          </div>
        )}
      </Panel>

      {data && (
        <div className="shrink-0 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">
          更新 {updatedAt} · 共 {data.count} 只（{data.breadth_summary.up} 涨 / {data.breadth_summary.down} 跌 / {data.breadth_summary.flat} 平）·
          两市成交 {fmtAmount(data.total_amount_yi * 1e8)} · 行业覆盖 {(data.industry_coverage * 100).toFixed(0)}%（TDX HY 行业，
          未覆盖归「未分类」）· 组内展示流通市值 Top12，其余聚合为「其他」· 面积=流通市值，颜色=当日涨跌幅
        </div>
      )}
    </div>
  );
}
