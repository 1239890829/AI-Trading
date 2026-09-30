"use client";

import { useResource } from "@/hooks/use-polling-fetch";
import { getPaperAccount, getPaperOrders } from "@/lib/api";
import { getJson } from "@/lib/api/internal";
import { Panel } from "@/components/panel";
import { fmt } from "@/lib/format";

/** One selected account at a time; no summed capital, fabricated fills, or writes. */
export function AccountScopePanel({ account }: { account: string }) {
  const result = useResource(async () => {
    if (account === "daily") return {kind: "daily" as const, payload: (await getJson<Record<string, unknown>>("/api/picks/shadow")).data};
    const [summary, orders] = await Promise.all([getPaperAccount(), getPaperOrders()]);
    return { kind: "paper" as const, summary, orders };
  }, { key: account, enabled: account === "paper" || account === "daily", intervalMs: 30_000 });
  if (account === "manual") return <p className="text-xs text-zinc-600 dark:text-zinc-400">手工记录来自用户录入，非券商验证。右侧“记账”只维护记录，不提交真实订单。</p>;
  if (account === "hunting") return <p role="status" className="text-xs text-amber-800 dark:text-amber-300">机会影子成交尚未准入。机会参考价与跟踪结果不是实际成交，不展示虚构账户或收益。</p>;
  const data = result.data;
  return <Panel title={account === "daily" ? "每日精选影子（独立账户）" : "手工模拟（main账户）"} className="max-h-56 shrink-0">
    <div className="space-y-2 p-3 text-xs">
      {!!result.error && <p role="alert">读取失败，不能判断当前账户。{data ? "以下为上次读取结果。" : ""}<button onClick={result.refresh}>重试</button></p>}
      {result.pending && !data && <p role="status">读取账户结果…</p>}
      {data && (data.kind === "paper" ? <>
        <p>只计当前 main 账户；精选影子、手工记录不合并。</p>
        <p>账户总额 {fmt(data.summary.total)} 元</p>
        {data.orders.length === 0 ? <p>暂无委托记录。</p> : <details><summary>本次读取的委托（{data.orders.length} 笔）</summary><div className="max-h-72 overflow-auto">{data.orders.map(order => <p key={order.id}>{order.symbol} · {order.side === "buy" ? "买入" : "卖出"} · {order.quantity} 股 · {order.status} · {order.reason ?? "无补充回执"}</p>)}</div></details>}
      </> : data.payload.enabled === false ? <p role="status">{String(data.payload.note ?? "每日精选影子未启用")}</p> : <details><summary>当前影子账户原始回执</summary><pre className="whitespace-pre-wrap break-all">{JSON.stringify(data.payload, null, 2)}</pre></details>)}
    </div>
  </Panel>;
}
