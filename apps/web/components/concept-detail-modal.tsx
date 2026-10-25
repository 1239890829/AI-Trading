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
import { useMemo, useState } from "react";

import { useResource } from "@/hooks/use-resource";
import { getConceptDetail } from "@/lib/api";
import { fmtAmount, pctColor, pctText } from "@/lib/format";
import { useStockRowNav } from "@/components/stock-link";
import { ModalShell } from "@/components/ui/modal-shell";
import { LimitReason } from "@/components/detail/limit-reason";

export function ConceptDetailModal({
  code,
  name,
  onClose,
  active = true,
}: {
  code: string;
  name: string;
  onClose: () => void;
  active?: boolean;
}) {
  const stockNav = useStockRowNav();
  const resource = useResource(async () => {
    const value = await getConceptDetail(code);
    if (value.code !== code) throw new Error("返回成分代码不符");
    return value;
  }, { key: code, enabled: active });
  const [enrichmentCode, setEnrichmentCode] = useState("");
  const [previousCode, setPreviousCode] = useState(code);
  if (previousCode !== code) {setPreviousCode(code); setEnrichmentCode("");}
  const enrichment = useResource(async () => {
    const base = resource.data;
    const value = await getConceptDetail(code, {enrichment: "full"});
    if (value.code !== code) throw new Error("返回归因代码不符");
    if (base && (value.name !== base.name || JSON.stringify(value.members.map(row => [row.symbol, row.name]).sort()) !== JSON.stringify(base.members.map(row => [row.symbol, row.name]).sort()))) throw new Error("成分版本已变，请先刷新成分后读取归因");
    return {base, detail: value};
  }, {key: code, enabled: active && enrichmentCode === code, refreshOnVisible: false});
  const currentEnrichment = enrichment.data?.base === resource.data ? enrichment.data?.detail : undefined;
  const detail = currentEnrichment ?? resource.data;
  const error = resource.error instanceof Error ? resource.error.message : String(resource.error);
  const [filters, setFilters] = useState({ code, tab: "__all__", q: "", detail: undefined as typeof detail });
  // A scope change resets its filters before commit. Within the same concept,
  // only a successful new result can retire a tag; failure keeps the selection.
  if (filters.code !== code || (detail !== undefined && filters.detail !== detail)) {
    const current = filters.code === code ? filters : { code, tab: "__all__", q: "", detail: undefined };
    setFilters({
      ...current, detail,
      tab: detail && current.tab !== "__all__" && !detail.tag_groups.some(group => group.tag === current.tab) ? "__all__" : current.tab,
    });
  }
  const tab = filters.code === code ? filters.tab : "__all__";
  const q = filters.code === code ? filters.q : "";
  const setTab = (tab: string) => setFilters(current => ({ ...current, tab }));

  const members = useMemo(() => {
    const list = detail?.members ?? [];
    const filtered = tab === "__all__" ? list : list.filter((m) => m.tags.includes(tab));
    const kw = q.trim();
    if (!kw) return filtered;
    return filtered.filter((m) => m.symbol.includes(kw) || (m.name ?? "").includes(kw));
  }, [detail, tab, q]);

  return (
    <ModalShell
      open={active}
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
              ? `同花顺成分记录 ${detail.total} 只 · ${detail.limit_up_count == null ? "涨停归因未读取" : `源池涨停记录 ${detail.limit_up_count}只`}`
              : resource.error ? "读取失败" : "加载中…"}
          </p>
        </>
      }
      footer={
        <>
          {detail?.meta_note ?? "成分来自同花顺正式目录"} · 原因标签不等于二级概念。
        </>
      }
    >
      {active && <>
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-[11px] text-zinc-600 dark:text-zinc-400">
          <span>名称与成分先读；完整涨停归因按需补充。</span>
          <button type="button" className="quiet-action" disabled={!resource.data || enrichment.pending && enrichmentCode === code} onClick={() => {if (enrichmentCode === code) enrichment.refresh(); else setEnrichmentCode(code);}}>
            {enrichment.pending && enrichmentCode === code ? "读取归因中…" : currentEnrichment ? "刷新涨停归因" : "读取涨停归因"}
          </button>
          {enrichment.error != null && <><span role="alert">归因读取失败或成分版本已变；已存成分仍可查看。</span><button type="button" className="quiet-action" disabled={resource.pending} onClick={resource.refresh}>重读正式成分</button></>}
          {detail?.membership_state === "stale" && <span role="status">成员记录已过保鲜期，当前变动待核。</span>}
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
            onChange={(e) => setFilters(current => ({ ...current, q: e.target.value }))}
            placeholder="搜代码/名称"
            className="w-28 shrink-0 rounded-lg border border-zinc-200 bg-transparent px-2 py-1 text-xs text-zinc-700 placeholder:text-zinc-400 focus:border-zinc-400 focus:outline-none dark:border-zinc-700 dark:text-zinc-200"
          />
        </div>

        {/* 列表 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
          {resource.error != null && <div role="alert" className="py-2 text-xs text-amber-800 dark:text-amber-300">
            {detail ? "刷新失败，保留上次成分：" : "加载失败："}{error}
            <button type="button" onClick={resource.refresh} aria-label="重试概念读取" className="ml-2 ui-filter">重试</button>
          </div>}
          {detail === undefined && !resource.error ? (
            <div className="space-y-1.5 py-2">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="h-9 w-full animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-800" />
              ))}
            </div>
          ) : detail === undefined ? null : (
          members.length === 0 ? (
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
                    {(m.reason || m.limit_up) && <div className="min-w-0 basis-full"><LimitReason symbol={m.symbol} reason={m.reason} source={m.reason_source} date={m.reason_date} compact /></div>}
                  </div>
                </li>
              ))}
            </ul>
          ))}
        </div>

      </>}
    </ModalShell>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      aria-pressed={active}
      onClick={onClick}
      className={`ui-filter shrink-0 whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] transition-colors ${
        active
          ? "ui-filter-selected"
          : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
      }`}
    >
      {children}
    </button>
  );
}
