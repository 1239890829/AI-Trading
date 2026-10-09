"use client";

import { useResource } from "@/hooks/use-polling-fetch";
import { getPaperAccount, getPaperOrders } from "@/lib/api";
import { getJson } from "@/lib/api/internal";
import { Panel } from "@/components/panel";
import { fmt } from "@/lib/format";

type Activation = {
  configured: boolean;
  runner_loaded: boolean;
  timezone: string;
  start_time: string | null;
  end_time: string | null;
  mode: "daily_morning" | "qualified_buy_point";
  quote_recheck_seconds: number | null;
  poll_seconds: number;
};
type Runtime = {state: string; as_of: string | null; reason?: string | null};
type DailyRead = {
  enabled?: boolean;
  note?: string;
  activation?: Activation;
  runtime?: Runtime;
  scope?: string;
  cash?: number;
  executed_today?: boolean;
  positions?: {symbol: string; quantity: number; available: number; cost_price: number; buy_date: string}[];
  recent_orders?: {id: number; symbol: string; side: string; quantity: number; status: string; reason: string | null}[];
};
type HuntingRead = {trade_date: string | null; enabled: boolean; note: string; status: string; opportunities: number;
  independent_decisions: number; counts: Record<string, number>; closed_fills: number;
  net_return_pct: number | null; net_median_pct: number | null; win_rate: number | null;
  activation?: Activation; runtime: Runtime; issues: string[];
  records: {id: string; symbol: string; state: string; reason: string; filled_price: number | null;
    net_return_pct: number | null; decision_version: string; entry_order_id: number | null}[];
};
const STATES: Record<string, string> = {filled: "已成交，待退出", exited: "已合法退出", pending: "委托待成交", rejected: "已拒绝", expired: "动作已过期", no_fill: "限价未成交"};
const RUNTIME_STATES: Record<string, string> = {ready: "最近一轮已完成", running: "调度已运行，成交仍以回执为准", degraded: "运行失败，需核回执", paused: "调度已暂停", not_loaded: "运行器未加载", unknown: "运行状态未知", not_started: "尚未运行"};

function ShadowUse({kind, enabled, activation, runtime}: {kind: "daily" | "hunting"; enabled?: boolean; activation?: Activation; runtime?: Runtime}) {
  const window = activation?.start_time && activation.end_time ? `${activation.start_time}–${activation.end_time}` : null;
  return <div className="space-y-2 leading-5">
    <p>{kind === "daily" ? "用途：验证每日精选通过执行闸门后的模拟表现。" : "用途：验证已通过买点规则的机会能否模拟成交并合法退出。"}</p>
    <p className="font-medium">启用状态：{enabled === false ? "未启用，已有记录保留" : enabled !== true ? "尚未返回" : activation?.runner_loaded === false ? "已配置，但运行器未加载" : "已配置启用"}</p>
    {kind === "daily" ? <p>执行时机：{window ? `交易日晨窗 ${window}（北京时间）` : "当前晨窗配置尚未返回"}。读取最新已保存精选，组合日以执行回执为准。只处理通过执行闸门的计划，错过晨窗不追价。</p> : <p>执行时机：交易时段内，已有买点通过资格门后再核行情与价格范围{activation?.quote_recheck_seconds != null ? `；决定须在 ${activation.quote_recheck_seconds} 秒内有效` : ""}。</p>}
    <details>
      <summary className="cursor-pointer py-1">什么时候需要启用</summary>
      <p className="mt-1">{kind === "daily" ? "当每日精选、交易日历和行情能够稳定核验，需要持续积累执行对照时，由后台配置启用。旧持仓按可卖规则轮动，撮合仍受 T+1、涨跌停、整手、费用与停牌限制。" : "当需要积累已获准买点的真实模拟成交、拒绝与退出证据时，由后台配置启用。仅入选或出现在题材参考区不会自动触发；决定过期、报价不可信、价格越界或容量不足会拒绝。"}</p>
      {activation?.poll_seconds != null && <p className="mt-1">后台检查间隔 {activation.poll_seconds} 秒；打开此面板只读取结果。</p>}
    </details>
    <p className="text-zinc-600 dark:text-zinc-400">后台回执：{runtime ? RUNTIME_STATES[runtime.state] ?? "状态未识别，待核对" : "运行状态尚未返回"}{runtime?.as_of ? ` · ${runtime.as_of}` : ""}</p>
    {runtime?.reason && <p className="text-zinc-600 dark:text-zinc-400">回执原因：{runtime.reason}</p>}
  </div>;
}

/** One selected account at a time; no summed capital, fabricated fills, or writes. */
export function AccountScopePanel({ account, date, readOnly = false }: { account: string; date?: string; readOnly?: boolean }) {
  const result = useResource(async () => {
    if (account === "hunting") return {kind: "hunting" as const, payload: (await getJson<HuntingRead>(`/api/paper/hunting-shadow${date ? `?trade_date=${encodeURIComponent(date)}` : ""}`)).data};
    if (account === "daily") return {kind: "daily" as const, payload: (await getJson<DailyRead>("/api/picks/shadow")).data};
    const [summary, orders] = await Promise.all([readOnly ? getPaperAccount(true) : getPaperAccount(), getPaperOrders()]);
    return { kind: "paper" as const, summary, orders };
  }, { key: `${account}:${date ?? "latest"}:${readOnly}`, enabled: account === "paper" || account === "daily" || account === "hunting", intervalMs: 30_000 });
  if (account === "manual") return <p className="text-xs text-zinc-600 dark:text-zinc-400">手工记录来自用户录入，非券商验证。{readOnly ? "此处只读核对记录。" : "右侧“记账”只维护记录，不提交真实订单。"}</p>;
  const data = result.data;
  return <Panel title={account === "hunting" ? "机会影子（独立账户）" : account === "daily" ? "每日精选影子（独立账户）" : "手工模拟（main账户）"} className={readOnly ? "shrink-0" : "max-h-56 shrink-0"}>
    <div className="space-y-3 p-3 text-xs [overflow-wrap:anywhere]">
      {!!result.error && <p role="alert">读取失败，不能判断当前账户。{data ? "以下为上次读取结果。" : ""}<button data-action="secondary" onClick={result.refresh}>重试</button></p>}
      {result.pending && !data && <p role="status">读取账户结果…</p>}
      {data && (data.kind === "paper" ? <>
        <p>只计当前 main 账户；精选影子、手工记录不合并。</p>
        {"account_created" in data.summary && !data.summary.account_created ? <p>main 账户尚未建立；此旁览不初始化资金。</p> : <p>账户总额 {fmt(data.summary.total)} 元</p>}
        {data.orders.length === 0 ? <p>暂无委托记录。</p> : <details><summary>本次读取的委托（{data.orders.length} 笔）</summary><div className="space-y-2 pt-2">{data.orders.map(order => <p key={order.id}>{order.symbol} · {order.side === "buy" ? "买入" : "卖出"} · {order.quantity} 股 · {order.status} · {order.reason ?? "无补充回执"}</p>)}</div></details>}
      </> : data.kind === "hunting" ? <>
        <ShadowUse kind="hunting" enabled={data.payload.enabled} activation={data.payload.activation} runtime={data.payload.runtime} />
        <p className="text-zinc-600 dark:text-zinc-400">{data.payload.note}</p>
        <p>结果日 {data.payload.trade_date ?? "尚无记录"} · 来源：持久模拟订单</p>
        <p>只计独立猎场模拟；参考价不是成交，不构成买卖建议。</p>
        <p className="tabular-nums">动作尝试 {data.payload.opportunities} 次 · 独立决定 {data.payload.independent_decisions} 个 · 合法退出 {data.payload.closed_fills} 笔</p>
        {data.payload.status === "complete" && !data.payload.issues.length ? <p className="tabular-nums">扣费净均值 {fmt(data.payload.net_return_pct)}% · 中位 {fmt(data.payload.net_median_pct)}% · 胜率 {data.payload.win_rate == null ? "未知" : `${(data.payload.win_rate * 100).toFixed(1)}%`}</p> : <p>结果未成熟或无已退出成交，暂不展示胜率与汇总收益。</p>}
        {data.payload.issues.length > 0 && <p role="alert">成交证据不完整，不能作效果依据。</p>}
        {data.payload.records.length === 0 ? <p>暂无影子动作记录；不代表当天没有机会。</p> : <details><summary>动作与未成交原因（{data.payload.records.length} 条）</summary><div className="space-y-3 pt-2">{data.payload.records.map(row => <p key={row.id}>{row.symbol} · {STATES[row.state] ?? "未知状态"} · {row.filled_price == null ? "无实际成交价" : `成交 ${fmt(row.filled_price)}`} · {row.reason}<br /><span className="text-zinc-600 dark:text-zinc-400">决定版本 {row.decision_version} · 委托 {row.entry_order_id ?? "未提交"}</span></p>)}</div></details>}
      </> : <>
        <ShadowUse kind="daily" enabled={data.payload.enabled} activation={data.payload.activation} runtime={data.payload.runtime} />
        {data.payload.note && <p className="text-zinc-600 dark:text-zinc-400">{data.payload.note}</p>}
        <p>只计每日精选独立模拟账户；参考价不是成交，不构成买卖建议。</p>
        {data.payload.cash != null && <p className="tabular-nums">可用模拟现金 {fmt(data.payload.cash)} 元</p>}
        {data.payload.executed_today != null && <p>{data.payload.executed_today ? "今日已有买入委托，成交情况请核下方回执。" : "今日尚无买入委托；不代表没有精选或计划失败。"}</p>}
        {!!data.payload.positions?.length && <details><summary>此账户持仓（{data.payload.positions.length} 只）</summary><div className="space-y-2 pt-2">{data.payload.positions.map(position => <p key={position.symbol}>{position.symbol} · {position.quantity} 股 · 可卖 {position.available} 股 · 成本 {fmt(position.cost_price)} 元</p>)}</div></details>}
        {!!data.payload.recent_orders?.length && <details><summary>最近模拟委托（{data.payload.recent_orders.length} 笔）</summary><div className="space-y-2 pt-2">{data.payload.recent_orders.map(order => <p key={order.id}>{order.symbol} · {order.side === "buy" ? "买入" : "卖出"} · {order.quantity} 股 · {STATES[order.status] ?? order.status} · {order.reason ?? "无补充回执"}</p>)}</div></details>}
      </>)}
    </div>
  </Panel>;
}
