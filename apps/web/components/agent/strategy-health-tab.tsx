"use client";

/**
 * 策略健康度 + 登记册面板（#11）。
 *
 * 后端能力早就就绪（P1-37 登记册 / P1-38 策略级监控），但前端此前**没有任何消费方**
 * —— 端点返回 200 且数据正确，界面上却完全看不到。本 tab 就是把这条断掉的链补上。
 *
 * 渲染纪律（三条，违反即误导）：
 * 1. `ok/warning/drift` = **有判定**；`insufficient/thin/no_pipeline/unknown/error` = **判不出**。
 *    后者既不是健康也不是失效，必须视觉上区分，绝不能当成 ok 显示成绿色。
 * 2. `basis` 必须显式标注：`market_neutral`（测 alpha 衰减）与 `absolute`（测策略自身变差）
 *    **不可互相解释**。
 * 3. 核验结论（`verification`）缺失时如实说"尚无核验产物"，不得留白让人以为已验证。
 */
import Link from "next/link";
import { useState } from "react";
import { StockLink } from "@/components/stock-link";
import { IncrementalSentinel } from "@/components/ui/incremental-sentinel";
import { useIncremental } from "@/hooks/use-incremental";
import { dateTimeTextBJ } from "@/lib/format";
import type { SavedStrategyRun, StrategyRegistryItem } from "@/lib/api/picks";
import { useResource } from "@/hooks/use-resource";
import {ConditionList, ExplanationMap, NarrativeText} from "@/components/ui/explanation-map";

import {
  getStrategyHealth,
  getStrategyRegistry,
} from "@/lib/api";

/** 有判定的状态（其余一律视为"判不出"） */
const JUDGED = new Set(["ok", "warning", "drift"]);

const STATUS_META: Record<string, { label: string; cls: string }> = {
  ok: { label: "健康", cls: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" },
  warning: { label: "关注", cls: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  drift: { label: "下漂", cls: "bg-rose-500/15 text-rose-700 dark:text-rose-300" },
  // 判不出（不是 ok 也不是失效）
  insufficient: { label: "样本不足", cls: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400" },
  thin: { label: "笔数不足", cls: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400" },
  no_pipeline: { label: "无逐日落库", cls: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400" },
  unknown: { label: "未知", cls: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400" },
  error: { label: "读取失败", cls: "bg-rose-500/15 text-rose-700 dark:text-rose-300" },
};

const LIFECYCLE_LABEL: Record<string, string> = {
  active: "现役", observing: "观察", rejected: "已否决",
};

function StatusBadge({ status }: { status?: string }) {
  const s = status || "unknown";
  const meta = STATUS_META[s] ?? { label: s, cls: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400" };
  const judged = JUDGED.has(s);
  return (
    // data-testid 用于精确断言：**不能靠全文匹配**——caveat 里也含"判不出"字样，
    // 全文断言在徽标不再标注时仍会命中，等于没测（2026-09-11 注入验证抓到）。
    <span
      data-testid={`status-badge-${s}`}
      className={`rounded px-1.5 py-0.5 text-[10px] ${meta.cls}`}
      title={judged ? "有判定" : "样本/能力不足，判不出——既不是健康也不是失效"}
    >
      {meta.label}
      {!judged && "（判不出）"}
    </span>
  );
}

function countText(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value.toLocaleString("zh-CN") : "未记录";
}

const ROW_LABELS: Record<string, string> = { selected: "原入选", rejected: "拒绝存档", candidate: "候选", unknown: "待核" };
const DECISION_LABELS: Record<string, string> = { passed: "通过", ranked: "已排序", rejected: "未过", blocked: "阻断", unknown: "待核" };

function SavedRun({ run, method }: {run: SavedStrategyRun | null | undefined; method: string}) {
  const [filter, setFilter] = useState("selected");
  const items = run?.items ?? [];
  const rows = items.filter(row => filter === "all" || row.state === filter);
  const incremental = useIncremental(rows, {pageSize: 20, step: 20, resetKey: `${method}:${run?.source_version}:${filter}`});
  if (!run || (run.state !== "saved" && run.state !== "partial")) return <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-700">
    <h3 className="font-medium">最近运行</h3>
    <p className="mt-1 text-zinc-600 dark:text-zinc-400">{run?.reason || (run?.state === "error" ? "读取失败，当前运行未知。" : "尚无本版可读取的保存运行；不按页面读取时间制造运行记录。")}</p>
    <p className="mt-1 text-zinc-600 dark:text-zinc-400">观察或已否决的方法不会因打开目录而启动。</p>
  </div>;
  const counts = method === "daily_picks"
    ? [["候选母集", run.counts?.candidates], ["评分成功", run.counts?.scored], ["原入选", run.counts?.selected], ["拒绝存档", run.counts?.rejected_saved]]
    : [["候选行", run.counts?.candidate_rows], ["排序行", run.counts?.rank_rows], ["硬门行", run.counts?.hard_gate_rows], ["原入选", run.counts?.selected]];
  const hunting = method === "daily_picks" ? "/hunting?view=discover&sec=daily" : "/hunting?view=discover&sec=candidates";
  return <section aria-label="最近保存运行" className="space-y-3">
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-zinc-200 pb-2 dark:border-zinc-700">
      <h3 className="text-sm font-medium">最近保存运行 <span className="ml-1 text-xs font-normal text-zinc-600 dark:text-zinc-400">{run.trade_date || "日期未记录"}</span></h3>
      <span className="text-[11px] text-zinc-600 dark:text-zinc-400">{run.run_at ? dateTimeTextBJ(run.run_at) : "运行时刻未记录"} · {typeof run.elapsed_ms === "number" && Number.isFinite(run.elapsed_ms) ? `${run.elapsed_ms}ms` : "耗时未记录"}</span>
    </div>
    {run.state === "partial" && <p role="status" className="text-xs text-amber-700 dark:text-amber-300">部分存档不可读取：{run.reason || "原始内容不完整"}；未知计数不按 0 处理。</p>}
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 border-b border-zinc-200 pb-3 text-xs sm:grid-cols-4 dark:border-zinc-700">
      {counts.map(([label,value]) => <div key={String(label)}><dt className="text-zinc-600 dark:text-zinc-400">{label}</dt><dd className="mt-0.5 font-medium tabular-nums">{countText(value)}</dd></div>)}
    </dl>
    <p className="text-[11px] text-zinc-600 dark:text-zinc-400">{method === "daily_picks" ? "入选与拒绝均为原保存结果，不代表执行通过。" : "各阶段按行计数，可能包含同股/同题材多行，不是递减漏斗。"} 运行、健康窗口与离线核验分别读取，不能当作共同版本。</p>
    {run.data_state && <p className="text-[11px] text-zinc-600 dark:text-zinc-400">运行输入：{run.data_state}（原保存状态）；存档存在不等于输入完整或执行通过。</p>}
    {run.possibly_truncated && <p role="status" className="text-xs text-amber-700 dark:text-amber-300">此投影可能截断，{run.counts?.selected == null ? "原入选总数未知；不能以当前行数冒充全集。" : "以下存档行不是全集；原保存计数仍按各自范围展示。"}</p>}
    {(run.criteria?.min_pick_score != null || run.criteria?.max_picks != null) && <p className="text-[11px] text-zinc-600 dark:text-zinc-400">原入选条件：{run.criteria?.min_pick_score != null ? `分线 ${run.criteria.min_pick_score}` : "分线未记录"} · {run.criteria?.max_picks != null ? `容量 ${run.criteria.max_picks}` : "容量未记录"}；执行资格另行复核。</p>}
    <div className="flex flex-wrap gap-2 text-xs" aria-label="运行结果筛选">
      {["selected","rejected","candidate","unknown","all"].map(value => <button key={value} type="button" data-action="secondary" aria-pressed={filter===value} onClick={() => setFilter(value)}>{value === "all" ? "全部存档行" : ROW_LABELS[value]}</button>)}
    </div>
    {rows.length === 0 ? <p className="py-2 text-xs text-zinc-600 dark:text-zinc-400">当前筛选没有保存行，可切换核对其他阶段；不是新运行或成交结论。</p> : <ul className="divide-y divide-zinc-200 dark:divide-zinc-700">
      {incremental.shown.map((row,index) => <li key={`${row.snapshot_id || row.symbol}:${row.state}:${index}`} className="py-2 text-xs">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          {/^\d{6}$/.test(row.symbol) ? <StockLink symbol={row.symbol} className="font-medium">{row.name || row.symbol}</StockLink> : <span className="font-medium">{row.name || row.symbol || "证券未记录"}</span>}
          <span className="font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{row.symbol}</span>
          <span className="text-[11px] text-zinc-600 dark:text-zinc-400">{ROW_LABELS[row.state] || row.state}</span>
          {row.stage && <span className="text-[11px] text-zinc-600 dark:text-zinc-400">{row.stage === "hard_gate" ? "硬门" : row.stage === "rank" ? "排序" : row.stage}：{row.decision ? DECISION_LABELS[row.decision] || `未识别（${row.decision}）` : "未记录"}</span>}
          <span className="ml-auto tabular-nums">{typeof row.score === "number" && Number.isFinite(row.score) ? `原分 ${row.score}` : "未保存分数"}</span>
        </div>
        <p className="mt-1 break-words text-zinc-600 dark:text-zinc-400">{row.reason || (Object.keys(row.basis || {}).length > 0 ? "原评分依据已保存，可展开核对；简短理由未记录。" : "原存档没有简短理由，不能据此推定通过。")}</p>
        {(Object.keys(row.basis || {}).length > 0 || row.snapshot_id) && <details className="mt-1 text-[11px] text-zinc-600 dark:text-zinc-400"><summary className="cursor-pointer">核对原依据与身份</summary>
          {Object.entries(row.basis || {}).map(([key,value]) => <p key={key} className="mt-1 break-words">{key}：{value}</p>)}
          {row.snapshot_id && <p className="mt-1 break-all font-mono">快照 {row.snapshot_id}</p>}
        </details>}
      </li>)}
    </ul>}
    <IncrementalSentinel sentinelRef={incremental.sentinelRef} visible={Math.min(incremental.visible,rows.length)} total={rows.length} onLoadMore={incremental.loadMore}/>
    <div className="flex flex-wrap gap-3 border-t border-zinc-200 pt-2 text-xs dark:border-zinc-700">
      <Link href={hunting}>当前名单与完整研究</Link><Link href="/hunting?view=validation">执行与成交核对</Link>
      <Link href={`/agent?area=research&tab=review${run.trade_date ? `&date=${encodeURIComponent(run.trade_date)}` : ""}`}>同日复盘</Link>
    </div>
    <details className="text-[10px] text-zinc-600 dark:text-zinc-400"><summary className="cursor-pointer">运行版本与范围</summary><p className="mt-1 break-all">来源版本：{run.source_version || "未记录"}</p><p>方法版本：{run.strategy_version || "未记录"}</p><p>仅保存选股事实，不是实际委托、成交或方法有效性证明。</p></details>
  </section>;
}

export function StrategyHealthTab() {
  const health = useResource(getStrategyHealth, { marketHours: false });
  const registryResource = useResource(getStrategyRegistry, { marketHours: false });
  const [query,setQuery] = useState("");
  const [draft,setDraft] = useState("");
  const [composing,setComposing] = useState(false);
  const [lifecycle,setLifecycle] = useState("all");
  const [chosen,setChosen] = useState(() => typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("method") || "");
  const items = health.data?.strategies ?? [];
  const registry = registryResource.data ?? [];
  const healthByKey = new Map(items.map(row => [row.strategy_key,row]));
  const registryByKey = new Map(registry.map(row => [row.key,row]));
  const keys = [...new Set([...registry.map(row => row.key), ...items.map(row => row.strategy_key)])];
  const directory = keys.map(key => {
    const record = registryByKey.get(key), h = healthByKey.get(key);
    return {key, record, health:h, name:record?.name || h?.name || key,
      lifecycle: record?.status || h?.lifecycle || "unknown", note:record?.note || h?.note || ""};
  });
  const visible = directory.filter(row => (lifecycle === "all" || row.lifecycle === lifecycle)
    && `${row.name} ${row.key} ${row.note}`.toLowerCase().includes(query.trim().toLowerCase()));
  const selected = visible.find(row => row.key === chosen) || visible[0];
  const h = selected?.health, record: StrategyRegistryItem | undefined = selected?.record;
  const v = record?.verification;
  const w = h?.window;
  const winRate = typeof w?.win_rate === "number" && Number.isFinite(w.win_rate) ? `${(w.win_rate*100).toFixed(0)}%` : "—";
  const caveat = health.data?.caveat;
  function refresh() {health.refresh(); registryResource.refresh();}

  return <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto p-1">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div><h2 className="text-sm font-medium">选股方法目录</h2><p className="mt-0.5 text-[11px] text-zinc-600 dark:text-zinc-400">登记 {directory.length} 个策略键 · 健康可评估 {health.data?.counts?.evaluable ?? "—"} · 需关注 {health.data?.counts?.attention ?? "—"}；不代表全系统因子数量。</p></div>
      <button type="button" data-action="secondary" onClick={refresh}>刷新</button>
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <label className="min-w-36 flex-1 text-xs"><span className="sr-only">搜索方法</span><input aria-label="搜索方法" className="w-full rounded border border-zinc-300 bg-transparent px-2 py-1.5 text-xs dark:border-zinc-600" placeholder="搜索名称、方法或依据" value={draft}
        onCompositionStart={() => setComposing(true)} onCompositionEnd={event => {setComposing(false);setQuery(event.currentTarget.value);}}
        onChange={event => {setDraft(event.target.value); if (!composing) setQuery(event.target.value);}}/></label>
      <div className="flex flex-wrap gap-1 text-[11px]" aria-label="方法生命周期筛选">
        {[["all","全部"],["active","现役"],["observing","观察"],["rejected","已否决"]].map(([key,label]) => <button key={key} type="button" data-action="secondary" aria-pressed={lifecycle===key} onClick={() => setLifecycle(key)}>{label}</button>)}
      </div>
      <span aria-live="polite" className="text-[11px] text-zinc-600 dark:text-zinc-400">{visible.length} / {directory.length}</span>
    </div>
    {[{label:"策略健康",resource:health},{label:"核验登记册",resource:registryResource}].map(({label,resource}) => resource.error != null && <p key={label} role="alert" className="rounded border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">{label}加载失败：{resource.error instanceof Error ? resource.error.message : "接口异常"}。{resource.data !== undefined ? "保留上次读取结果，当前状态未确认。" : "当前状态未确认。"}</p>)}
    {directory.length===0 && <p className="text-xs text-zinc-600 dark:text-zinc-400">{health.data===undefined && registryResource.data===undefined ? "正在读取方法与健康结果…" : "暂无策略键"}</p>}
    {directory.length>0 && visible.length===0 && <p className="text-xs text-zinc-600 dark:text-zinc-400">没有匹配方法，清空搜索或切换生命周期即可查看原登记。</p>}
    {selected && <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(12rem,.7fr)_minmax(0,1.6fr)]">
      <nav aria-label="方法目录"><ul className="space-y-1">{visible.map(row => <li key={row.key}><button type="button" aria-pressed={row.key===selected.key} onClick={() => setChosen(row.key)}
        className={`w-full rounded-lg border px-3 py-2 text-left text-xs ${row.key===selected.key ? "border-current bg-zinc-100 dark:bg-zinc-800" : "border-transparent hover:bg-zinc-100 dark:hover:bg-zinc-800"}`}>
        <span className="block font-medium">{row.name}</span><span className="mt-0.5 block text-[10px] text-zinc-600 dark:text-zinc-400">{LIFECYCLE_LABEL[row.lifecycle] || "状态未记录"} · {STATUS_META[row.health?.status || "unknown"]?.label || "未知"}</span>
      </button></li>)}</ul></nav>
      <div className="min-w-0 space-y-3" aria-label="当前方法详情">
        <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-[11px]">{selected.key}</span><StatusBadge status={h?.status}/><span className="text-[11px] text-zinc-600 dark:text-zinc-400">生命周期：{LIFECYCLE_LABEL[selected.lifecycle] || "未记录"}</span></div>
        {selected.note && <p className="text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">{selected.note}</p>}
        <SavedRun key={selected.key} method={selected.key} run={record?.runtime}/>
        <section className="space-y-2 border-t border-zinc-200 pt-3 dark:border-zinc-700" aria-label="健康与核验">
          <h3 className="text-xs font-medium">滚动健康与独立核验</h3>
          {(record?.basis || h?.basis) && <p className="text-[11px] text-zinc-600 dark:text-zinc-400" title="market_neutral 测 alpha 衰减；absolute 测策略自身变差，两者不可互相解释">口径：<span className="font-mono">{record?.basis || h?.basis}</span></p>}
          {w && <p className="text-[11px] text-zinc-600 dark:text-zinc-400">窗口 {w.groups ?? "—"} 组合日 · {w.total_picks ?? "—"} 笔 · 观察胜率 {winRate}；不是模拟成交胜率。</p>}
          <p className="text-[11px] text-zinc-600 dark:text-zinc-400">核验：{registryResource.data===undefined ? (registryResource.error ? "登记册读取失败，核验结论未知" : "登记册正在读取，核验结论未知") : v?.available ? <><span className="font-mono">{v.verdict ?? "—"}</span>{v.stale && <span className="ml-1 text-amber-700 dark:text-amber-300">（产物超期）</span>}{v.headline && <span className="ml-1">— {v.headline}</span>}</> : <>尚无产物（{v?.reason || "未跑过核验"}）</>}</p>
          {h?.note && <ExplanationMap label="健康依据" identity={selected.key} steps={[
            {id:"note",label:"判读说明",icon:"source",content:<NarrativeText text={h.note} label="原始判读说明"/>},
            {id:"window",label:"样本与口径",icon:"response",content:<ConditionList label="采样事实" items={[`口径：${h.basis || "未记录"}`,`窗口：${w?.groups ?? "未记录"} 组合日；${w?.total_picks ?? "未记录"} 笔；观察胜率 ${winRate}。`,"market_neutral 测 alpha 衰减；absolute 测策略自身变差，两者不可互相解释。"]}/>},
            {id:"limit",label:"核验边界",icon:"risk",tone:"warning",content:<ConditionList label="不能混同的结论" tone="warning" items={["判不出既不代表健康，也不代表失效。","最近运行、滚动健康和离线核验不是共同版本的快照。"]}/>},
          ]}/>}
        </section>
      </div>
    </div>}
    {caveat && <ConditionList label="健康判读边界" items={[caveat]} tone="warning"/>}
    <p className="text-[10px] text-zinc-600 dark:text-zinc-400">状态为「判不出」时既不代表健康也不代表失效；核验结论来自离线重算，随重跑更新。目录不会启用新方法、重新评分或生成研究。</p>
  </div>;
}
