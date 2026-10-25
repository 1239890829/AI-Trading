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
import { useEffect, useState } from "react";
import type { StrategyRegistryItem } from "@/lib/api/picks";
import {MethodRunReader} from "./method-run-reader";
import {methodRunUrl} from "./method-run-location";
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

export function StrategyHealthTab() {
  const health = useResource(getStrategyHealth, { marketHours: false });
  const registryResource = useResource(getStrategyRegistry, { marketHours: false });
  const [query,setQuery] = useState("");
  const [draft,setDraft] = useState("");
  const [composing,setComposing] = useState(false);
  const [lifecycle,setLifecycle] = useState("all");
  const [chosen,setChosen] = useState(() => typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("method") || "");
  useEffect(() => {
    const sync = () => setChosen(new URLSearchParams(window.location.search).get("method") || "");
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  function chooseMethod(key: string) {
    window.history.pushState(window.history.state, "", methodRunUrl(window.location.pathname, window.location.search, key));
    setChosen(key);
  }
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
      <nav aria-label="方法目录"><ul className="space-y-1">{visible.map(row => <li key={row.key}><button type="button" aria-pressed={row.key===selected.key} onClick={() => chooseMethod(row.key)}
        className={`w-full rounded-lg border px-3 py-2 text-left text-xs ${row.key===selected.key ? "border-current bg-zinc-100 dark:bg-zinc-800" : "border-transparent hover:bg-zinc-100 dark:hover:bg-zinc-800"}`}>
        <span className="block font-medium">{row.name}</span><span className="mt-0.5 block text-[10px] text-zinc-600 dark:text-zinc-400">{LIFECYCLE_LABEL[row.lifecycle] || "状态未记录"} · {STATUS_META[row.health?.status || "unknown"]?.label || "未知"}</span>
      </button></li>)}</ul></nav>
      <div className="min-w-0 space-y-3" aria-label="当前方法详情">
        <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-[11px]">{selected.key}</span><StatusBadge status={h?.status}/><span className="text-[11px] text-zinc-600 dark:text-zinc-400">生命周期：{LIFECYCLE_LABEL[selected.lifecycle] || "未记录"}</span></div>
        {selected.note && <p className="text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">{selected.note}</p>}
        <MethodRunReader key={selected.key} method={selected.key}/>
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
