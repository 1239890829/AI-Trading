"use client";

/**
 * 概念详情弹窗（2026-09-08 用户需求，参考同花顺概念页设计）：
 * - 默认 tab「全部成分」：官方成分**全量**（已保证与同花顺逐符号一致），
 *   带当日行情与涨停标注；
 * - 细分 tab：当日涨停成员按 ths 涨停原因官方标签分组（逐字，不改写）；
 *   同花顺官方 API 无二级概念层级（四类 tag 已实测穷尽），子概念成分待
 *   数据源支持后接入——界面不提供关键词自创分组。
 * 外壳走全站统一的 `ModalShell`（2026-09-15：portal / 遮罩 / Esc / 尺寸档收在一处）。
 */
import { useCallback, useMemo, useState } from "react";

import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { getConceptDetail, type ConceptDetail } from "@/lib/api";
import { fmtAmount, pctColor, pctText } from "@/lib/format";
import { useStockRowNav } from "@/components/stock-link";
import { ModalShell } from "@/components/ui/modal-shell";

export function ConceptDetailModal({
  code,
  name,
  onClose,
}: {
  code: string;
  name: string;
  onClose: () => void;
}) {
  const stockNav = useStockRowNav();
  const [detail, setDetail] = useState<ConceptDetail | undefined | null>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<string>("__all__");
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    try {
      setDetail(await getConceptDetail(code));
      setError(null);
    } catch (e) {
      setDetail(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [code]);

  // P1-27 收编 usePollingFetch：setState 落在 promise 回调里。
  // `code` 是会变的入参 → 必须作为第三参 key 传入，否则切概念不会立即重拉。
  usePollingFetch(load, null, code);

  const members = useMemo(() => {
    const list = detail?.members ?? [];
    const filtered = tab === "__all__" ? list : list.filter((m) => m.tags.includes(tab));
    const kw = q.trim();
    if (!kw) return filtered;
    return filtered.filter((m) => m.symbol.includes(kw) || (m.name ?? "").includes(kw));
  }, [detail, tab, q]);

  return (
    <ModalShell
      onClose={onClose}
      label={`${detail?.name ?? name} 概念详情`}
      size="md"
      radius="2xl"
      bodyClassName="overflow-hidden"
      header={
        <>
          <h2 className="truncate text-base font-semibold text-zinc-900 dark:text-zinc-50">
            {detail?.name ?? name}
            <span className="ml-2 font-mono text-[11px] text-zinc-600 dark:text-zinc-400">{code}</span>
          </h2>
          <p className="mt-0.5 text-[11px] text-zinc-600 dark:text-zinc-400">
            {detail
              ? `官方成分 ${detail.total} 只（与同花顺逐符号一致） · 今日涨停 ${detail.limit_up_count}`
              : "加载中…"}
          </p>
        </>
      }
      footer={
        <>
          {detail?.meta_note ?? "成分=同花顺官方目录"} · 细分 tab 为当日涨停成员的官方归因标签（官方 API 未提供二级概念成分，待数据源支持）
        </>
      }
    >
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
            <p className="py-6 text-center text-xs text-red-700 dark:text-red-500">加载失败：{error}</p>
          ) : members.length === 0 ? (
            <p className="py-6 text-center text-xs text-zinc-600 dark:text-zinc-400">
              {tab === "__all__" ? "无匹配成分" : "该细分下暂无当日涨停成员"}
            </p>
          ) : (
            <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {members.map((m) => (
                <li key={m.symbol} onClick={stockNav(m.symbol)} className="cursor-pointer py-1.5 text-xs transition-colors hover:bg-sky-500/5">
                  <div className="flex items-center gap-2">
                    <span className="w-14 shrink-0 font-mono text-zinc-600 dark:text-zinc-400">{m.symbol}</span>
                    <span className="w-24 shrink-0 truncate font-medium text-zinc-800 dark:text-zinc-100">
                      {m.name}
                    </span>
                    <span
                      className={`w-16 shrink-0 text-right font-mono tabular-nums ${pctColor(m.change_pct ?? null)}`}
                    >
                      {m.change_pct != null ? pctText(m.change_pct) : "--"}
                    </span>
                    {m.limit_up && (
                      <span className="shrink-0 rounded bg-rose-500/10 px-1 py-0.5 text-[10px] text-rose-700 dark:text-rose-300">
                        涨停
                      </span>
                    )}
                    {/* 换手 / 流通市值：全市场快照口径，缺失显式 -- */}
                    <span className="ml-auto w-20 shrink-0 text-right font-mono tabular-nums text-zinc-600 dark:text-zinc-400" title="换手率%">
                      换手 {m.turnover_rate != null ? `${m.turnover_rate.toFixed(2)}%` : "--"}
                    </span>
                    <span className="w-24 shrink-0 text-right font-mono tabular-nums text-zinc-600 dark:text-zinc-400" title="流通市值（亿元）">
                      流通 {m.float_market_cap_yi != null ? `${m.float_market_cap_yi.toFixed(1)}亿` : "--"}
                    </span>
                  </div>
                  {/* 第二行：涨停成员的官方归因与封板细节（开板/封单/连板；非涨停不显示） */}
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 pl-16 text-[11px] text-zinc-600 dark:text-zinc-400">
                    {m.limit_up && (
                      <span className="font-mono tabular-nums" title="连板数（ths 官方）">
                        {m.boards != null ? `${m.boards} 板` : "--"}
                        {m.break_count != null && m.break_count > 0 && (
                          <span className="ml-1 text-amber-800 dark:text-amber-400">开板 {m.break_count} 次</span>
                        )}
                      </span>
                    )}
                    {m.seal_amount != null && (
                      <span className="font-mono tabular-nums" title="封单额（ths 官方 seal_money）">
                        封单 {fmtAmount(m.seal_amount)}
                      </span>
                    )}
                    <span className="min-w-0 flex-1 truncate" title={m.reason ?? ""}>
                      {m.reason ?? (m.limit_up ? "（ths 涨停池未提供该股归因）" : "")}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

    </ModalShell>
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
