"use client";

import { useEffect, useState } from "react";
import {
  getSpeedRank,
  getThemesCatalog,
  type SpeedRankRow,
  type ThemeCatalogItem,
} from "@/lib/api";
import { fmt, pctColor, pctText } from "@/lib/format";

/**
 * 涨速榜（指数/题材详情右列"涨速"标签）。
 *
 * 口径：涨速 = 最近 5 分钟涨跌幅（同花顺/东财/通达信行情"涨速"列同口径），
 * 价格全部来自腾讯批量快照；后端惰性采样，历史不足 5 分钟的标的如实显示
 * "采样中"，绝不拿当日涨跌幅冒充涨速。
 *
 * 交互：官方题材下拉（默认选中目录第一项）→ 榜内成分按涨速降序；
 * 30s 轮询让采样自然滚动积累。冷启动约 5 分钟后数据逐步可用。
 */
export function SpeedPanel({ className }: { className?: string }) {
  const [themes, setThemes] = useState<ThemeCatalogItem[]>([]);
  const [theme, setTheme] = useState<string>("");
  const [rows, setRows] = useState<SpeedRankRow[]>([]);
  const [themeName, setThemeName] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    getThemesCatalog()
      .then((items) => {
        if (!alive) return;
        setThemes(items);
        if (items.length > 0) setTheme((cur) => cur || items[0].code);
      })
      .catch(() => alive && setError("题材目录加载失败"));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!theme) return;
    let alive = true;
    const load = async () => {
      setLoading(true);
      try {
        const d = await getSpeedRank(theme);
        if (!alive) return;
        setRows(d.items);
        setThemeName(d.theme_name);
        setNote(d.note ?? null);
        setError(null);
      } catch (e) {
        if (alive) setError((e as Error).message);
      } finally {
        if (alive) setLoading(false);
      }
    };
    void load();
    const t = setInterval(load, 30_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [theme]);

  const sampled = rows.filter((r) => r.sampled).length;

  return (
    <div className={`flex min-h-0 flex-col ${className ?? ""}`}>
      <div className="flex shrink-0 items-center gap-2 border-b border-zinc-100 px-2 py-1.5 text-xs dark:border-zinc-800/60">
        <select
          value={theme}
          onChange={(e) => setTheme(e.target.value)}
          className="max-w-[180px] rounded border border-zinc-200 bg-transparent px-1.5 py-0.5 text-xs dark:border-zinc-700"
          aria-label="选择题材"
        >
          {themes.length === 0 && <option value="">题材加载中…</option>}
          {themes.map((t) => (
            <option key={t.code} value={t.code}>
              {t.name}
            </option>
          ))}
        </select>
        <span className="text-[10px] text-zinc-400">
          涨速 = 最近 5 分钟涨跌幅 · {sampled}/{rows.length} 已采样
        </span>
        <span className="ml-auto text-[10px] text-zinc-500">{loading ? "刷新中…" : "30s 自动刷新"}</span>
      </div>
      {error && (
        <div className="shrink-0 px-3 py-2 text-xs text-amber-600 dark:text-amber-300">{error}</div>
      )}
      {note && !error && (
        <div className="shrink-0 px-3 py-2 text-xs text-zinc-400">{note}</div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-zinc-50 text-zinc-400 dark:bg-zinc-900">
            <tr className="text-left">
              <th className="px-3 py-1.5 font-normal">标的</th>
              <th className="px-2 py-1.5 text-right font-normal">现价</th>
              <th className="px-2 py-1.5 text-right font-normal">涨速</th>
              <th className="px-2 py-1.5 text-right font-normal">当日</th>
              <th className="px-3 py-1.5 text-right font-normal">状态</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                <td className="px-3 py-1.5">
                  <span className="font-mono text-[10px] text-zinc-400">{r.symbol}</span>
                  <span className="ml-1.5">{r.name ?? "--"}</span>
                </td>
                <td className="px-2 py-1.5 text-right font-mono tabular-nums">{fmt(r.price)}</td>
                <td className={`px-2 py-1.5 text-right font-mono font-medium tabular-nums ${r.sampled ? pctColor(r.speed) : "text-zinc-400"}`}>
                  {r.sampled ? pctText(r.speed) : "采样中…"}
                </td>
                <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${pctColor(r.change_pct)}`}>
                  {pctText(r.change_pct)}
                </td>
                <td className="px-3 py-1.5 text-right text-[10px] text-zinc-400">
                  {r.sampled ? "OK" : r.sample_span_sec != null && r.sample_span_sec > 0 ? `${Math.round(r.sample_span_sec)}s` : "待采样"}
                </td>
              </tr>
            ))}
            {rows.length === 0 && !error && !note && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-zinc-400">
                  {loading ? "加载中…" : "暂无数据"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="shrink-0 border-t border-zinc-100 px-3 py-1 text-[10px] text-zinc-500 dark:border-zinc-800/60">
        {themeName ? `${themeName} · ` : ""}不构成买卖建议 · 数据有延迟
      </div>
    </div>
  );
}
