"use client";

import {useState} from "react";
import type {FundPublicReference} from "@/lib/api/assets";
import {pctColor} from "@/lib/format";

function time(value: string | null) {
  return value && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value))
    ? new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).format(new Date(value))
    : "时点未提供";
}

export function FundReferencePanel({reference, sourceSymbol}: {reference?: FundPublicReference | null; sourceSymbol: string}) {
  const [selected, setSelected] = useState<"nav_pct" | "nav2_pct">("nav_pct");
  const valid = reference?.source_symbol === sourceSymbol && reference.source === "sina_fund"
    && reference.scope === "reported_top_holdings_reference";
  const data = valid ? reference : undefined;
  const curve = data?.curves.find(item => item.key === selected) ?? data?.curves[0];
  const usable = data && data.state !== "unavailable" && curve && curve.latest_pct !== null && Number.isFinite(curve.latest_pct);
  return <section className="asset-fund-reference" aria-label="基金重仓参考">
    <div className="asset-section-heading"><div><h3>{data?.is_current_day ? "今日重仓参考" : "重仓参考"}</h3><span>新浪基金 · {time(data?.source_as_of ?? null)}（北京时间）{data?.state === "stale" ? " · 陈旧参考" : ""}</span></div>
      {data && data.curves.length > 1 && <div className="asset-segments" aria-label="重仓参考口径">{data.curves.map(item => <button key={item.key} aria-pressed={curve?.key === item.key} onClick={() => setSelected(item.key)}>{item.label}</button>)}</div>}
    </div>
    <div className="asset-reference-reading"><strong className={usable ? pctColor(curve.latest_pct!) : ""}>{usable ? `${curve.latest_pct! > 0 ? "+" : ""}${curve.latest_pct!.toFixed(2)}%` : "暂无有效参考"}</strong>
      <span>上一季度前十大重仓参考；不能代表整只基金当日涨跌或净值。</span></div>
    <p className="asset-note">整只基金当日估值尚未获得；不以参考值冒充。{data?.heavy_stock_us ? "参考持仓包含美股，按源方口径展示。" : ""}</p>
    <details className="asset-evidence"><summary>参考范围与来源</summary><p>{data?.scope_note ?? "来源尚未提供可核参考，公布净值仍可独立查看。"}</p>
      {data?.warning && <p>{data.warning}</p>}
      {data?.disclosed_nav != null && <p>来源公布净值 {data.disclosed_nav}，净值日 {data.disclosed_nav_date ?? "未知"}；不升级为今日净值。</p>}
      {data?.missing.length ? <ul>{data.missing.map(item => <li key={item}>{item}</li>)}</ul> : null}
      {data?.evidence.map(item => <p key={item.receipt_id} className="asset-receipt">接收 {time(item.received_at)} · 回执 {item.receipt_id} · SHA-256 {item.payload_sha256}</p>)}
    </details>
  </section>;
}
