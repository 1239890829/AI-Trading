"use client";

import {useCallback, useState} from "react";
import Link from "next/link";
import type {SavedMethodRunIdentity, StrategyRegistryItem} from "@/lib/api/picks";
import {discoveryMetadata, discoveryMethodHref, MethodFavoriteButton, methodStatusLabel, methodSupportsManualResearch, methodSupportsRuns, SavedRunSummary, useMethodFavorites, validMethodRow} from "./discovery-methods";
import {ReferenceMethodResearch} from "./reference-method-research";
import {MethodRunReader} from "@/components/agent/method-run-reader";

function RuleList({title, items, empty}: {title: string; items: readonly string[]; empty: string}) {
  return <section className="space-y-1.5" aria-label={title}><h3 className="text-xs font-medium">{title}</h3>
    {items.length ? <ul className="space-y-1 text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">{items.map((item, index) => <li key={`${index}:${item}`}>{item}</li>)}</ul>
      : <p className="text-xs text-zinc-600 dark:text-zinc-400">{empty}</p>}
  </section>;
}

/** A catalog explanation. No registry fetch, run, model, quote or execution request. */
export function DiscoveryMethodDetail({row, date, search = "", onClose}: {
  row: StrategyRegistryItem; date?: string; search?: string; onClose: () => void;
}) {
  const favorites = useMethodFavorites();
  const [notice, setNotice] = useState("");
  const [savedRun, setSavedRun] = useState<SavedMethodRunIdentity | undefined>();
  const valid = validMethodRow(row);
  const methodKey = valid ? row.key : "";
  const manual = valid && methodSupportsManualResearch(row);
  const executionVersion = manual ? row.candidate!.execution!.version : "";
  const disclosureValue = JSON.stringify([methodKey, executionVersion]);
  const params = new URLSearchParams(search);
  const disclosureScope = JSON.stringify([methodKey, executionVersion, params.get("browse"), params.get("disc_method"), params.get("disc_saved")]);
  const requestedOpen = manual && params.get("browse") === "methods" && params.get("disc_method") === methodKey && params.get("disc_saved") === disclosureValue;
  const [disclosure, setDisclosure] = useState({scope:disclosureScope, open:requestedOpen});
  if (disclosure.scope !== disclosureScope) setDisclosure({scope:disclosureScope, open:requestedOpen});
  const showRuns = disclosure.scope === disclosureScope ? disclosure.open : requestedOpen;
  const setSavedOpen = useCallback((open: boolean) => {
    setDisclosure({scope:disclosureScope, open});
    // Display intent only: no task, permission, run identity or saved result is
    // created here. The reader's exact-run pin preserves this same owner marker.
    const next = new URLSearchParams(window.location.search);
    if (!manual || next.get("disc_method") !== methodKey || next.get("browse") !== "methods") return;
    if (open) next.set("disc_saved", disclosureValue);
    else if (next.get("disc_saved") === disclosureValue) next.delete("disc_saved");
    const url = `${window.location.pathname}${next.size ? `?${next}` : ""}${window.location.hash}`;
    // Next copies its router state on the external-history path. Replaying
    // __NA/_N here would bypass its useSearchParams update after manual close.
    if (url !== window.location.pathname + window.location.search + window.location.hash) window.history.replaceState({}, "", url);
  }, [disclosureScope, disclosureValue, manual, methodKey]);
  const completed = useCallback((identity: SavedMethodRunIdentity) => {
    if (!manual || identity.method !== methodKey || identity.strategy_version !== executionVersion) return;
    setSavedRun(identity); setSavedOpen(true);
  }, [manual, methodKey, executionVersion, setSavedOpen]);
  const ownedSavedRun = savedRun?.method === methodKey && savedRun.strategy_version === executionVersion ? savedRun : undefined;
  if (!valid) return <section data-testid="discovery-method-detail" aria-label="方法规则" className="space-y-3">
    <h2 tabIndex={-1} className="text-sm font-semibold">方法身份未核</h2><p role="alert" className="text-xs">来源规则结构不完整，未显示为可用方法。</p>
    <button className="quiet-action" type="button" onClick={onClose}>返回方法目录</button></section>;
  const metadata = discoveryMetadata(row);
  const candidate = row.catalog_kind === "reference" ? row.candidate : null;
  const supportsRuns = methodSupportsRuns(row);
  return <section data-testid="discovery-method-detail" aria-label="方法规则" className="min-w-0 space-y-4">
    <header className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0"><h2 tabIndex={-1} className="break-words text-sm font-semibold">{row.name || row.key}</h2>
        <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{methodStatusLabel(row)} · {candidate?.asset === "etf" ? "ETF参考" : "A股"}</p></div>
      <button className="quiet-action" type="button" onClick={onClose}>返回方法目录</button>
    </header>
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
      <span>{metadata.category_label} · {metadata.session_label}</span>
      <MethodFavoriteButton name={row.name || row.key} followed={favorites.keys.includes(row.key)}
        onToggle={() => setNotice(favorites.toggle(row.key) ? "" : "本机最多关注40个方法，请先取消一项。")}/>
    </div>
    {!favorites.saved && <p role="status" className="text-xs text-amber-800 dark:text-amber-300">本机存储不可用，关注仅保留在当前页面，尚未保存。</p>}
    {notice && favorites.keys.length >= 40 && <p role="status" className="text-xs text-amber-800 dark:text-amber-300">{notice}</p>}
    <p className="text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">{metadata.summary}</p>
    {candidate ? <>
      <p role="status" className="text-xs text-amber-800 dark:text-amber-300">{manual ? "仅手动价格研究；不参与正式入选、通知或模拟。" : "来源候选，不可运行；关注不会启用方法或生成选股。"}</p>
      <p className="break-words text-[11px] text-zinc-600 dark:text-zinc-400">来源 {candidate.source_label} · 原版本 {candidate.source_version} · {candidate.source_code}</p>
      <RuleList title="原规则" items={candidate.criteria} empty="来源未给出可核规则。"/>
      {manual && <RuleList title="本系统v1明确口径" items={[candidate.execution!.definition, candidate.execution!.definition_supplement]} empty="补充口径未登记。"/>}
      <RuleList title="现有覆盖" items={candidate.existing_coverage} empty="现有覆盖尚未登记。"/>
      <RuleList title="仍缺什么" items={candidate.limitations} empty="限制未登记，不代表已验收。"/>
      {manual && <>
        <p className="text-[11px] text-zinc-600 dark:text-zinc-400">本系统明确版本 {candidate.execution!.version}；本系统定义与补充口径保留在保存批次。</p>
        <ReferenceMethodResearch key={`${row.key}:${date ?? "latest"}:${candidate.execution!.version}`} method={row.key} version={candidate.execution!.version} date={date} onComplete={completed}/>
        <details open={showRuns} onToggle={event => {if (event.currentTarget.open !== showRuns) setSavedOpen(event.currentTarget.open);}}><summary className="cursor-pointer text-xs">保存研究结果</summary>
          {showRuns && <div className="mt-3"><MethodRunReader key={`${row.key}:${date ?? "latest"}:${ownedSavedRun?.run_id ?? "browse"}`} method={row.key} initialDate={date} initialIdentity={ownedSavedRun} compact/></div>}
        </details>
      </>}
    </> : <>
      {supportsRuns ? <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-zinc-600 dark:text-zinc-400"><SavedRunSummary row={row} date={date}/></div>
        : <p className="text-xs text-zinc-600 dark:text-zinc-400">{row.verification?.available === true ? "有核验产物，可查看原依据与反例。" : row.verification?.available === false ? "尚无核验产物。" : "核验状态未记录。"}</p>}
      <Link href={discoveryMethodHref(row.key, search, date)} className="quiet-action inline-flex min-h-8 items-center">
        {supportsRuns ? "查看原运行与版本" : "查看核验与反例"}</Link>
      <p className="text-[11px] text-zinc-600 dark:text-zinc-400">只读原依据；方法状态、入选和模拟执行分别核验。</p>
    </>}
  </section>;
}
