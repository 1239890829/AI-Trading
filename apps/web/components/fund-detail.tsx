"use client";

import {useState} from "react";
import {FundNavChart} from "@/components/fund-nav-chart";
import {FundRecordEditor} from "@/components/fund-record-editor";
import type {AssetReadSection, AssetRecord, FundSnapshot, FundTerm} from "@/lib/api/assets";

const stateLabel = {ready: "已读取", partial: "部分资料", unavailable: "暂无资料"};
function pct(value: number | null) {return value == null ? "未知" : `${value.toFixed(2)}%`;}
function stamp(value: string | null) {
  if (!value) return "未提供";
  if (!/(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) return `${value}（时区待核）`;
  return `${new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false}).format(new Date(value)).replaceAll("/", "-")}（北京时间）`;
}

function SectionEvidence({section}: {section: AssetReadSection}) {
  return <div className="asset-evidence">
    <span>{section.source === "system_calculation" ? "系统计算 · 非官方结论" : "同花顺官方来源"} · {stateLabel[section.state]}</span>
    {section.reason && <p>{section.reason}</p>}
    {section.missing.length > 0 && <details><summary>缺少 {section.missing.length} 项资料</summary><ul>{section.missing.map(item => <li key={item}>{item}</li>)}</ul></details>}
    {section.evidence.length > 0 && <details><summary>来源核对 · {section.evidence.length} 份回执</summary><ul>{section.evidence.map((item, index) => <li key={`${item.receipt_id}:${index}`}>接收：{stamp(item.received_at)}<br/><span className="asset-receipt">回执 {item.receipt_id} · SHA-256 {item.payload_sha256}</span></li>)}</ul></details>}
  </div>;
}

function Terms({rows, empty}: {rows: FundTerm[]; empty: string}) {
  return rows.length ? <dl className="asset-term-list">{rows.map((row, index) => <div key={`${row.label}:${index}`}><dt>{row.label}</dt><dd>{row.value}{row.condition && <small>{row.condition}</small>}</dd></div>)}</dl> : <p className="asset-note">{empty}</p>;
}

export function FundDetail({snapshot, range, setRange, record, recordPending, onRefreshRecords, refreshing, onRefresh}: {
  snapshot: FundSnapshot; range: "month" | "year"; setRange: (value: "month" | "year") => void;
  record?: AssetRecord; recordPending: boolean; onRefreshRecords: () => void; refreshing: boolean; onRefresh: () => void;
}) {
  const [tab, setTab] = useState<"nav" | "holdings" | "terms" | "research">("nav");
  const [view, setView] = useState<"reported" | "lookthrough">("reported");
  const {instrument, nav, holdings, lookthrough, terms, research} = snapshot;
  const tabs = [{key: "nav", label: "净值与记录"}, {key: "holdings", label: "披露持仓"}, {key: "terms", label: "费用与申赎"}, {key: "research", label: "研究核对"}] as const;
  return <div className="asset-detail">
    <header className="asset-detail-heading"><div><h2 title="同花顺目录中的来源名称">{instrument.name}</h2><p>{instrument.source_symbol} · {instrument.currency} 份额 · 同花顺来源名称</p></div><button className="bc-compact-button" disabled={refreshing} onClick={onRefresh}>{refreshing ? "读取中…" : "刷新资料"}</button></header>
    <nav className="asset-detail-tabs" aria-label="基金详情">{tabs.map(item => <button key={item.key} type="button" aria-pressed={tab === item.key} onClick={() => setTab(item.key)}>{item.label}</button>)}</nav>
    <div className="asset-reading-pane" tabIndex={0} aria-label={tabs.find(item => item.key === tab)!.label}>
      {tab === "nav" && <>
        <div className="asset-section-heading"><div><h3>官方净值</h3><span>净值日 {nav.latest?.nav_date ?? "未提供"} · 发布时刻 {nav.publication_time_known ? stamp(nav.published_at) : "未提供"}</span></div><div className="asset-segments" aria-label="净值区间"><button aria-pressed={range === "month"} onClick={() => setRange("month")}>近一月</button><button aria-pressed={range === "year"} onClick={() => setRange("year")}>近一年</button></div></div>
        <FundNavChart points={nav.points} currency={nav.currency}/>
        <SectionEvidence section={nav}/>
        <p className="asset-note">盘中估值暂不提供；披露持仓不能代表实时仓位。</p>
        <FundRecordEditor key={instrument.instrument_id} instrument={instrument} record={record} latest={nav.state !== "unavailable" && nav.currency === instrument.currency ? nav.latest : null} loading={recordPending} onRefresh={onRefreshRecords}/>
      </>}
      {tab === "holdings" && <>
        <div className="asset-section-heading"><div><h3>披露持仓，不是当前仓位</h3><span>报告期 {holdings.report_date ?? "未提供"}</span></div><div className="asset-segments" aria-label="持仓口径"><button aria-pressed={view === "reported"} onClick={() => setView("reported")}>基金披露</button><button aria-pressed={view === "lookthrough"} onClick={() => setView("lookthrough")}>一层穿透</button></div></div>
        {view === "reported" ? <>
          <p className="asset-note">已披露权重 {pct(holdings.disclosed_weight_pct)}。未披露部分保留未知。</p>
          {holdings.items.length ? <div className="asset-disclosure-list">{holdings.items.map((row, index) => <details key={`${row.source_symbol}:${index}`}><summary><span><strong>{row.name}</strong><small>{row.source_symbol}</small></span><b>{pct(row.weight_pct)}</b></summary><dl className="asset-facts"><div><dt>报告期</dt><dd>{row.report_date ?? "未提供"}</dd></div><div><dt>披露时刻</dt><dd>{stamp(row.published_at)}</dd></div><div><dt>修订时刻</dt><dd>{stamp(row.modified_at)}</dd></div><div><dt>来源资产类型</dt><dd>{row.asset_type}</dd></div></dl></details>)}</div> : <p className="asset-note">未取得可展示的持仓明细，不以空列表推断没有持仓。</p>}
          <SectionEvidence section={holdings}/>
        </> : <>
          <div className="asset-coverage"><span>已穿透 {pct(lookthrough.covered_weight_pct)}</span><meter aria-label="披露股票穿透覆盖" min={0} max={100} value={lookthrough.covered_weight_pct}/><span>未覆盖 {pct(lookthrough.uncovered_weight_pct)}</span></div>
          <p className="asset-note">仅计算已披露股票及一层基金路径；不将已知权重重新补成 100%。</p>
          {lookthrough.items.length ? <div className="asset-disclosure-list">{lookthrough.items.map((row, index) => <details key={`${row.source_symbol}:${index}`}><summary><span><strong>{row.name}</strong><small>{row.source_symbol}</small></span><b>{pct(row.weight_pct)}</b></summary><p className="asset-note">报告期 {row.report_date} · 路径 {row.via.join(" → ") || "直接持有"}</p></details>)}</div> : <p className="asset-note">没有足够披露资料完成穿透，不推测底层成分。</p>}
          <SectionEvidence section={lookthrough}/>
        </>}
      </>}
      {tab === "terms" && <>
        <h3>费用与申赎</h3><dl className="asset-term-list"><div><dt>管理人</dt><dd>{terms.manager_name ?? "未提供"}</dd></div><div><dt>申购状态</dt><dd>{terms.subscription_status ?? "未提供，请以正式公告为准"}</dd></div><div><dt>跟踪基准</dt><dd>{terms.benchmark ?? "未提供"}</dd></div></dl>
        <h3>费用条件</h3><Terms rows={terms.fees} empty="未取得费用条款，不推算费率。"/>
        <h3>申购与赎回条件</h3><Terms rows={terms.dealing_rules} empty="未取得完整申赎条件，不按其他份额套用。"/>
        {terms.investment_scope && <details className="asset-source-text"><summary>投资范围原文</summary><p>{terms.investment_scope}</p></details>}
        <SectionEvidence section={terms}/>
      </>}
      {tab === "research" && <>
        <div className="asset-section-heading"><div><h3>{research.eligible ? "研究资料具备" : "研究候选 · 待补证据"}</h3><span>这是资料检查，不是买入资格或收益预测。</span></div></div>
        <Terms rows={research.checks} empty="尚无可展示的资料检查。"/>
        <SectionEvidence section={research}/><p className="asset-note">{research.disclaimer}</p>
      </>}
    </div>
  </div>;
}
