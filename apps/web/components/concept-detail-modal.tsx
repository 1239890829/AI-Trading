"use client";

/**
 * 概念详情弹窗（2026-09-08 用户需求，参考同花顺概念页设计）：
 * - 默认 tab「全部成分」：官方成分**全量**（已保证与同花顺逐符号一致），
 *   带当日行情与涨停标注；
 * - 细分 tab：当日涨停成员按 ths 涨停原因官方标签分组（逐字，不改写）；
 *   同花顺官方 API 无二级概念层级（四类 tag 已实测穷尽），子概念成分待
 *   数据源支持后接入——界面不提供关键词自创分组。
 * 外壳与 PickDetailModal 同款（portal + 背景点击 + Esc）。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import { getConceptDetail, type ConceptDetail } from "@/lib/api";
import { pctColor, pctText } from "@/lib/format";

export function ConceptDetailModal({
  code,
  name,
  onClose,
}: {
  code: string;
  name: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<ConceptDetail | undefined | null>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<string>("__all__");
  const [q, setQ] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const load = useCallback(async () => {
    try {
      setDetail(await getConceptDetail(code));
      setError(null);
    } catch (e) {
      setDetail(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [code]);

  useEffect(() => {
    void load();
  }, [load]);

  const members = useMemo(() => {
    const list = detail?.members ?? [];
    const filtered = tab === "__all__" ? list : list.filter((m) => m.tags.includes(tab));
    const kw = q.trim();
    if (!kw) return filtered;
    return filtered.filter((m) => m.symbol.includes(kw) || (m.name ?? "").includes(kw));
  }, [detail, tab, q]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-700 dark:bg-zinc-900"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        {/* 头部 */}
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-zinc-200 px-4 py-3 dark:border-zinc-700">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold text-zinc-900 dark:text-zinc-50">
              {detail?.name ?? name}
              <span className="ml-2 font-mono text-[11px] text-zinc-400">{code}</span>
            </h2>
            <p className="mt-0.5 text-[11px] text-zinc-400">
              {detail
                ? `官方成分 ${detail.total} 只（与同花顺逐符号一致） · 今日涨停 ${detail.limit_up_count}`
                : "加载中…"}
            </p>
          </div>
          <button
            onClick={onClose}
            className="shrink-0 rounded-lg px-2 py-1 text-sm text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        {/* 细分 tab + 搜索：单行布局——tab 容器横向滚动（概念多时不换行不挤压），
            搜索框 shrink-0 固定右侧，整行高度恒定 */}
        <div className="flex shrink-0 items-center gap-2 border-b border-zinc-100 px-4 py-2 dark:border-zinc-800">
          <div className="flex min-w-0 flex-1 flex-nowrap items-center gap-1.5 overflow-x-auto">
            <TabBtn active={tab === "__all__"} onClick={() => setTab("__all__")}>
              全部成分{detail ? ` ${detail.total}` : ""}
            </TabBtn>
            {(detail?.tag_groups ?? []).map((g) => (
              <TabBtn key={g.tag} active={tab === g.tag} onClick={() => setTab(g.tag)}>
                {g.tag} {g.symbols.length}
              </TabBtn>
            ))}
          </div>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜代码/名称"
            className="w-28 shrink-0 rounded-lg border border-zinc-200 bg-transparent px-2 py-1 text-xs text-zinc-700 placeholder:text-zinc-400 focus:border-zinc-400 focus:outline-none dark:border-zinc-700 dark:text-zinc-200"
          />
        </div>

        {/* 列表 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
          {detail === undefined ? (
            <div className="space-y-1.5 py-2">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="h-9 w-full animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-800" />
              ))}
            </div>
          ) : detail === null ? (
            <p className="py-6 text-center text-xs text-red-500">加载失败：{error}</p>
          ) : members.length === 0 ? (
            <p className="py-6 text-center text-xs text-zinc-400">
              {tab === "__all__" ? "无匹配成分" : "该细分下暂无当日涨停成员"}
            </p>
          ) : (
            <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {members.map((m) => (
                <li key={m.symbol} className="flex items-center gap-2 py-1.5 text-xs">
                  <span className="w-14 shrink-0 font-mono text-zinc-400">{m.symbol}</span>
                  <span className="w-24 shrink-0 truncate font-medium text-zinc-800 dark:text-zinc-100">
                    {m.name}
                  </span>
                  <span
                    className={`w-16 shrink-0 text-right font-mono tabular-nums ${pctColor(m.change_pct ?? null)}`}
                  >
                    {m.change_pct != null ? pctText(m.change_pct) : "--"}
                  </span>
                  {m.limit_up && (
                    <span className="shrink-0 rounded bg-rose-500/10 px-1 py-0.5 text-[10px] text-rose-600 dark:text-rose-300">
                      涨停
                    </span>
                  )}
                  <span className="min-w-0 flex-1 truncate text-right text-[11px] text-zinc-400" title={m.reason ?? ""}>
                    {m.reason ?? ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <p className="shrink-0 border-t border-zinc-100 px-4 py-1.5 text-[10px] leading-relaxed text-zinc-400 dark:border-zinc-800">
          {detail?.meta_note ?? "成分=同花顺官方目录"} · 细分 tab 为当日涨停成员的官方归因标签（官方 API 未提供二级概念成分，待数据源支持）
        </p>
      </div>
    </div>,
    document.body,
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`shrink-0 whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] transition-colors ${
        active
          ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
          : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
      }`}
    >
      {children}
    </button>
  );
}
