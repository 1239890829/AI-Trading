"use client";

import {useState} from "react";
import {FilterMenu} from "@/components/ui/filter-menu";
import {IncrementalSentinel} from "@/components/ui/incremental-sentinel";
import {Skeleton} from "@/components/ui/loading";
import {useIncremental} from "@/hooks/use-incremental";
import type {FundCatalog, FundCatalogCandidate, FundCatalogPurchase} from "@/lib/api/assets";
import {validFundSymbol} from "@/lib/asset-identity";

const sourceGroupLabels: Record<string, string> = {all: "全部", nazhi100: "纳指100", meigukeji: "美股科技", biaopu: "标普"};

export function fundCatalogCandidates(data: FundCatalog | undefined) {
  // The service owns deduplication/conflict detection; retain its first-source order.
  return data?.state !== "unavailable" ? data?.items.filter(item => item.state === "candidate" && validFundSymbol(item.source_symbol)) ?? [] : [];
}

function quotaText(item: FundCatalogCandidate) {
  if (item.quota_status === "unlimited") return "来源限额：不限额";
  if (item.quota_status === "reported" && item.quota_raw != null) return `来源限额：${item.quota_raw}（单位未提供）`;
  return "来源限额：待核";
}

export function FundCatalogList({data, pending, error, purchase, setPurchase, sourceSymbol, onSelect, onRefresh}: {
  data?: FundCatalog; pending: boolean; error: unknown; purchase: FundCatalogPurchase;
  setPurchase: (value: FundCatalogPurchase) => void; sourceSymbol: string | null;
  onSelect: (symbol: string) => void; onRefresh: () => void;
}) {
  const [group, setGroup] = useState("all");
  const seenGroups = new Set<string>();
  const groups = data?.groups.filter(item => {
    if (seenGroups.has(item.source_key)) return false;
    seenGroups.add(item.source_key);
    return true;
  }) ?? [];
  const currentGroup = groups.find(item => item.source_key === group)?.source_key ?? groups[0]?.source_key;
  const candidates = fundCatalogCandidates(data);
  const rows = currentGroup ? candidates.filter(item => item.memberships.some(member => member.group_key === currentGroup)) : [];
  const list = useIncremental(rows, {pageSize: 20, step: 20, resetKey: `${purchase}:${currentGroup}`, active: !!data});
  return <>
    <div className="bc-attention-heading asset-catalog-heading"><div className="bc-attention-scope"><h3>同花顺来源候选</h3><span className="bc-list-count">{data ? `${data.state === "partial" ? "部分名单 · " : ""}${rows.length} 只` : error ? "读取失败" : pending ? "读取中" : "待核来源"}</span></div><button className="bc-compact-button" disabled={pending} onClick={onRefresh}>{pending ? "读取中…" : "刷新名单"}</button></div>
    <div className="asset-catalog-filters">
      {groups.length > 0 && <FilterMenu label="原分类" value={currentGroup ?? ""} options={groups.map(item => ({key: item.source_key, label: sourceGroupLabels[item.source_key] ?? `来源栏目 ${item.source_key}`, title: `原代码：${item.source_key}`, count: candidates.filter(candidate => candidate.memberships.some(member => member.group_key === item.source_key)).length}))} onChange={setGroup}/>}
      <FilterMenu label="名单" value={purchase} options={[{key: "all", label: "全部来源"}, {key: "source_buyable", label: "来源可购", title: "仅源方本次筛选，不等于申购承诺"}]} onChange={setPurchase}/>
    </div>
    <p className="asset-note asset-catalog-note">美国来源栏目含跨区产品 · 投资范围待核 · 非推荐榜</p>
    {error != null && <p role="alert" className="asset-error">候选名单读取失败。{data ? "以下保留上次读取，申购状态待重新核对。" : "净值与已有记录仍可独立读取。"}</p>}
    <div className="bc-attention-track" tabIndex={0} aria-label="基金来源候选，可滚动">
      {!data && pending ? <Skeleton className="h-20 w-full"/> : data?.state === "unavailable" ? <p className="bc-watch-empty">来源名单暂无可用内容；不影响原基金详情与自选。</p> : <>
        {list.shown.map(item => <article key={item.source_symbol} className="bc-attention-item" data-selected={sourceSymbol === item.source_symbol}>
          <button className="bc-attention-select asset-list-select asset-catalog-select" aria-pressed={sourceSymbol === item.source_symbol} onClick={() => onSelect(item.source_symbol)}>
            <strong>{item.source_name ?? item.source_symbol}</strong>
            <span className="asset-note">{item.source_symbol} · 份额资料点选核对</span>
            <span className="asset-note">{quotaText(item)}</span>
          </button>
        </article>)}
        {!!data && !rows.length && <p className="bc-watch-empty">本次来源分类没有可打开的场外基金候选。不据此推断没有基金或已停售。</p>}
        <IncrementalSentinel sentinelRef={list.sentinelRef} visible={list.visible} total={rows.length} unit="只" onLoadMore={list.loadMore}/>
      </>}
      {data && <details className="asset-evidence asset-catalog-evidence"><summary>来源范围与缺项</summary>
        <p>{data.scope_note}</p><p>{data.purchase_note}</p>
        <p>来源 {data.coverage.source_rows} 行 / {data.coverage.unique_symbols} 个唯一代码；场外 {data.coverage.of_symbols} 个，超出范围 {data.coverage.out_of_scope_symbols} 个，冲突 {data.coverage.conflict_symbols} 个。{data.coverage.complete_source_response ? "本次来源响应完整。" : "本次来源响应不完整。"}</p>
        <p>保留各份额独立代码；同一代码的重复归属去重展示。未取得正式中文标签的分类保留原代码。</p>
        <p>限额 0 不代表停售；单位未提供时不补“元”。year 原字段未核收益定义，不据此排名。</p>
        {data.evidence.map((entry, index) => <p key={`${entry.receipt_id}:${index}`}>接收 <time dateTime={entry.received_at}>{entry.received_at}</time>；源方更新时间未提供。<span className="asset-receipt">回执 {entry.receipt_id} · SHA-256 {entry.payload_sha256}</span></p>)}
        {data.missing.length > 0 && <ul>{data.missing.map(value => <li key={value}>{value}</li>)}</ul>}
      </details>}
    </div>
  </>;
}
