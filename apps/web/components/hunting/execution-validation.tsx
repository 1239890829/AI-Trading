"use client";

import {useMemo, useState} from "react";
import {getExecutionValidation, type ExecutionReport} from "@/lib/api/validation";
import {useResource} from "@/hooks/use-resource";
import {useExitPresence} from "@/hooks/use-exit-presence";
import {dateTimeTextBJ} from "@/lib/format";
import {ValidationRecordTable} from "./validation-record-table";
import {FilterMenu} from "@/components/ui/filter-menu";

const states: Record<string,string> = {filled:"已成交",pending:"等待成交",rejected:"未通过",cancelled:"已撤销",unknown:"缺少凭据",no_fill:"未成交",expired:"已失效",exited:"已退出",executed:"有运行留痕",skipped:"已跳过"};
const reasons: Record<string,string> = {capital_slots_full:"验证仓位已满",budget_below_one_lot:"预算不足一手",symbol_already_occupied:"该股已有验证仓位",not_trading_day:"非交易日",window_not_open:"执行窗口未到",gate_not_judgeable:"入场条件无法判断",runner_not_ready:"执行器未启用",accepted:"委托已受理"};

export function ExecutionContent({report}: {report: ExecutionReport}) {
  const [scope,setScope] = useState("shadow");
  const [status,setStatus] = useState<string|null>(null);
  const [chosen,setChosen] = useState<string|null>(null);
  const presence=useExitPresence(chosen);
  const account=report.accounts.find(a => a.scope===scope);
  const orders=report.orders.filter(o => o.scope===scope);
  const filtered=status ? orders.filter(o => o.state===status) : orders;
  const attempts=scope==="hunting_shadow" ? report.attempts : [];
  const attempt=attempts.find(a => `attempt:${a.id}`===presence.value);
  const order=orders.find(o => `order:${o.id}`===presence.value);
  const distribution=(["filled","pending","rejected","cancelled","unknown"] as const).map(state => ({state,count:account?.[state] ?? 0}));
  return <section aria-label="内部执行验证">
    <div className="validation-execution-heading"><FilterMenu label="验证分组" value={scope} options={report.accounts.map(a => ({key:a.scope,label:a.label}))} onChange={value => {setScope(value);setStatus(null);setChosen(null);}} /><p className="validation-muted">独立资金账 · 只读结果</p></div>
    <div className="validation-overview validation-execution-overview"><div className="validation-overview-lead"><span>{account?.label} · 原始委托</span><strong>{account?.orders ?? 0}<small>笔</small></strong><small>买入与卖出均计入，非胜率</small></div>
      <div className="validation-distribution"><div className="validation-distribution-bar" role="img" aria-label={distribution.map(d => `${states[d.state]}${d.count}笔`).join("，")}>{orders.length ? distribution.filter(d => d.count>0).map(d => <span key={d.state} data-execution-state={d.state} style={{flex:d.count}}/>) : <span className="validation-distribution-blank"/>}</div>
        <div className="validation-distribution-legend">{distribution.map(d => <button key={d.state} aria-label={`${states[d.state]} ${d.count}`} aria-pressed={status===d.state} onClick={() => {setStatus(status===d.state ? null : d.state);setChosen(null);}}><i data-execution-state={d.state}/><span>{states[d.state]}</span><b>{d.count}</b></button>)}</div>
      </div>
    </div>
    <p className="validation-muted">点选结果可筛选原委托。尝试与委托分别计数，入选参考价不当作成交价。</p>
    <div className="validation-execution-grid"><aside className="validation-execution-aside">
      {scope==="shadow" ? <><header><h3>运行时间线</h3><span>按日核对</span></header><div className="validation-run-strip" aria-label="每日运行留痕">{report.runs.map(r => <button key={r.date} data-run-state={r.state} aria-expanded={chosen===`run:${r.date}`} onClick={() => setChosen(chosen===`run:${r.date}` ? null : `run:${r.date}`)}><span className="validation-run-dot"/><time>{r.date.slice(5)}</time><span>{states[r.state]}</span></button>)}</div></> : <><header><h3>机会尝试</h3><span>{attempts.length} 次</span></header><ValidationRecordTable key={scope} items={attempts} columns={["股票 / 日期","执行结果 / 原因"]} label="机会执行尝试" resetKey={scope} empty="没有执行尝试。不会自动启用或重跑。" renderRow={a => <tr key={a.id} data-selected={chosen===`attempt:${a.id}`}><td><button aria-expanded={chosen===`attempt:${a.id}`} onClick={() => setChosen(chosen===`attempt:${a.id}` ? null : `attempt:${a.id}`)}>{a.symbol}<small>{a.date}</small></button></td><td>{a.issue ? "关联待核" : states[a.state] ?? "状态待核"}<small>{reasons[a.reason] ?? (a.reason || "原记录未附原因")}</small></td></tr>} /></>}
      <p className="validation-muted">{scope==="shadow" ? "留痕缺失不等于没有执行。" : "先有执行尝试，是否成交仍看右侧原委托。"}</p>
    </aside>
    <section className="validation-records validation-execution-orders"><header><h3>原模拟委托</h3><span>{status ? states[status] : "全部结果"} · {filtered.length} 笔</span></header>
      <ValidationRecordTable key={scope} items={filtered} columns={["股票 / 委托时间","方向","委托价 → 成交价","股数","结果 / 原因"]} resetKey={`${scope}/${status}`} label="内部委托明细，可横向滚动" empty={status ? "该结果下没有委托，可点选同一结果取消筛选。" : "没有原委托记录，不把入选参考价当成交。"} renderRow={o => <tr key={o.id} data-selected={chosen===`order:${o.id}`}><td><button onClick={() => setChosen(chosen===`order:${o.id}` ? null : `order:${o.id}`)} aria-expanded={chosen===`order:${o.id}`}>{o.symbol}<small>{dateTimeTextBJ(o.submitted_at,{seconds:true})}</small></button></td><td>{o.side==="buy" ? "买入" : "卖出"}</td><td>{o.price?.toFixed(2) ?? "—"} <span className="validation-muted">→</span> {o.filled_price?.toFixed(2) ?? "—"}</td><td>{o.quantity}</td><td><span data-execution-state={o.state}>{states[o.state]}</span>{o.reason && <small className="validation-order-reason">{reasons[o.reason] ?? o.reason}</small>}</td></tr>} />
    </section></div>
    {presence.value && <div className="validation-expanded" data-open={presence.active} inert={!presence.active} aria-hidden={!presence.active}><div><div className="validation-detail">
      {order && <><h3>{order.symbol} · 原委托 #{order.id}</h3><p>{reasons[order.reason] ?? (order.reason || "原委托未附原因")}</p><dl><div><dt>成交价</dt><dd>{order.filled_price?.toFixed(2) ?? "未确认"} 元</dd></div><div><dt>已记费用</dt><dd>{order.fee?.toFixed(2) ?? "未确认"} 元</dd></div><div><dt>委托时刻</dt><dd>{dateTimeTextBJ(order.submitted_at,{seconds:true})}</dd></div></dl><p className="validation-muted">所示时刻为委托提交时间，不冒称成交时间。</p></>}
      {attempt && <><h3>{attempt.symbol} · 原机会尝试</h3><p>{attempt.issue || reasons[attempt.reason] || attempt.reason || "原记录未附原因"}</p><p>关联买入委托 {attempt.order_id ?? "无"} · {states[attempt.order_state]}</p><p className="validation-muted">决策 {attempt.decision_id} · 快照 {attempt.snapshot_id}</p></>}
      {report.runs.filter(r => `run:${r.date}`===presence.value).map(r => <section key={r.date}><h3>{r.date} · 运行留痕</h3><p>{reasons[r.reason] ?? r.reason}</p>{r.pick_date && <p>原精选日期 {r.pick_date}</p>}<p className="validation-muted">日级留痕没有订单ID，不推断逐笔成交关联。</p></section>)}
    </div></div></div>}
    <p className="validation-disclaimer">{report.limitation}</p>
  </section>;
}

export function ExecutionValidation({start,end}: {start:string;end:string}) {
  const load=useMemo(() => () => getExecutionValidation(start,end),[start,end]);
  const resource=useResource(load,{key:`execution/${start}/${end}`,intervalMs:null});
  return <><div className="validation-execution-heading"><h3>执行对照 · 内部模拟结果</h3><button className="quiet-action" disabled={resource.pending} onClick={resource.refresh}>{resource.pending ? "读取中…" : "刷新执行结果"}</button></div>
    {resource.pending && <p role="status">正在读取既有委托与运行留痕…</p>}{resource.error && <p role="alert">执行结果读取失败，请重试。</p>}{resource.data && <ExecutionContent report={resource.data}/>}</>;
}
