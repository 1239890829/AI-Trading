"use client";

import { useResource } from "@/hooks/use-polling-fetch";
import { getPaperAccount, getPaperOrders } from "@/lib/api";
import { getJson } from "@/lib/api/internal";
import { Panel } from "@/components/panel";
import { fmt } from "@/lib/format";

type HuntingRead = {trade_date: string | null; enabled: boolean; note: string; status: string; opportunities: number;
  independent_decisions: number; counts: Record<string, number>; closed_fills: number;
  net_return_pct: number | null; net_median_pct: number | null; win_rate: number | null;
  runtime: {state: string; as_of: string | null; reason?: string}; issues: string[];
  records: {id: string; symbol: string; state: string; reason: string; filled_price: number | null;
    net_return_pct: number | null; decision_version: string; entry_order_id: number | null}[];
};
const STATES: Record<string, string> = {filled: "已成交，待退出", exited: "已合法退出", pending: "委托待成交", rejected: "已拒绝", expired: "动作已过期", no_fill: "限价未成交"};

/** One selected account at a time; no summed capital, fabricated fills, or writes. */
export function AccountScopePanel({ account, date }: { account: string; date?: string }) {
  const result = useResource(async () => {
    if (account === "hunting") return {kind: "hunting" as const, payload: (await getJson<HuntingRead>(`/api/paper/hunting-shadow${date ? `?trade_date=${encodeURIComponent(date)}` : ""}`)).data};
    if (account === "daily") return {kind: "daily" as const, payload: (await getJson<Record<string, unknown>>("/api/picks/shadow")).data};
    const [summary, orders] = await Promise.all([getPaperAccount(), getPaperOrders()]);
    return { kind: "paper" as const, summary, orders };
  }, { key: `${account}:${date ?? "latest"}`, enabled: account === "paper" || account === "daily" || account === "hunting", intervalMs: 30_000 });
  if (account === "manual") return <p className="text-xs text-zinc-600 dark:text-zinc-400">手工记录来自用户录入，非券商验证。右侧“记账”只维护记录，不提交真实订单。</p>;
  const data = result.data;
  return <Panel title={account === "hunting" ? "机会影子（独立账户）" : account === "daily" ? "每日精选影子（独立账户）" : "手工模拟（main账户）"} className="max-h-56 shrink-0">
    <div className="space-y-2 p-3 text-xs">
      {!!result.error && <p role="alert">读取失败，不能判断当前账户。{data ? "以下为上次读取结果。" : ""}<button data-action="secondary" onClick={result.refresh}>重试</button></p>}
      {result.pending && !data && <p role="status">读取账户结果…</p>}
      {data && (data.kind === "paper" ? <>
        <p>只计当前 main 账户；精选影子、手工记录不合并。</p>
        <p>账户总额 {fmt(data.summary.total)} 元</p>
        {data.orders.length === 0 ? <p>暂无委托记录。</p> : <details><summary>本次读取的委托（{data.orders.length} 笔）</summary><div className="max-h-72 overflow-auto">{data.orders.map(order => <p key={order.id}>{order.symbol} · {order.side === "buy" ? "买入" : "卖出"} · {order.quantity} 股 · {order.status} · {order.reason ?? "无补充回执"}</p>)}</div></details>}
      </> : data.kind === "hunting" ? <>
        <p role="status" className="text-amber-800 dark:text-amber-300">{data.payload.note}</p>
        <p>结果日 {data.payload.trade_date ?? "尚无记录"} · 来源：持久模拟订单</p>
        <p>只计独立猎场模拟；参考价不是成交，不构成买卖建议。</p>
        <p>后台：{data.payload.runtime.state === "ready" ? "最近一轮已完成" : data.payload.runtime.state === "degraded" ? "运行失败，需核回执" : "未加载或尚未运行"}{data.payload.runtime.as_of ? ` · ${data.payload.runtime.as_of}` : ""}</p>
        <p className="tabular-nums">动作尝试 {data.payload.opportunities} 次 · 独立决定 {data.payload.independent_decisions} 个 · 合法退出 {data.payload.closed_fills} 笔</p>
        {data.payload.status === "complete" && !data.payload.issues.length ? <p className="tabular-nums">扣费净均值 {fmt(data.payload.net_return_pct)}% · 中位 {fmt(data.payload.net_median_pct)}% · 胜率 {data.payload.win_rate == null ? "未知" : `${(data.payload.win_rate * 100).toFixed(1)}%`}</p> : <p>结果未成熟或无已退出成交，暂不展示胜率与汇总收益。</p>}
        {data.payload.issues.length > 0 && <p role="alert">成交证据不完整，不能作效果依据。</p>}
        {data.payload.records.length === 0 ? <p>暂无影子动作记录；不代表当天没有机会。</p> : <details><summary>动作与未成交原因（{data.payload.records.length} 条）</summary><div className="max-h-72 overflow-auto">{data.payload.records.map(row => <p key={row.id} className="break-all">{row.symbol} · {STATES[row.state] ?? "未知状态"} · {row.filled_price == null ? "无实际成交价" : `成交 ${fmt(row.filled_price)}`} · {row.reason}<br /><span className="text-zinc-600 dark:text-zinc-400">决定版本 {row.decision_version} · 委托 {row.entry_order_id ?? "未提交"}</span></p>)}</div></details>}
      </> : data.payload.enabled === false ? <p role="status">{String(data.payload.note ?? "每日精选影子未启用")}</p> : <details><summary>当前影子账户原始回执</summary><pre className="whitespace-pre-wrap break-all">{JSON.stringify(data.payload, null, 2)}</pre></details>)}
    </div>
  </Panel>;
}
