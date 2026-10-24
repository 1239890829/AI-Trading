"use client";

import {useEffect, useRef, useState, type KeyboardEvent} from "react";
import {useDeviceAccess} from "@/components/access/device-access";
import {dateTimeTextBJ, fmt} from "@/lib/format";
import {
  createPaperSelectionRequest, currentPaperPreflight, definitivePaperRejection, getPaperSelectionAction,
  readPendingPaperSelection, resolvePaperSelectionRequest, submitPaperSelectionRequest, validPaperSelectionBinding,
  type PaperSelectionActionData, type PaperSelectionBinding, type PendingPaperSelection,
} from "@/lib/paper-selection-api";
import styles from "./paper-selection-action.module.css";

type Execution = PaperSelectionActionData["execution"];
const stateLabels:Record<Execution["state"],string>={not_started:"未提交",pending:"挂单待核",filled:"已模拟成交",rejected:"已拒绝",expired:"已过期",cancelled:"已取消",unknown:"结果待核"};
function restored(binding:PaperSelectionBinding) {
  try {return {pending:readPendingPaperSelection(binding),error:false};} catch {return {pending:null,error:true};}
}
function message(error:unknown) {
  return error instanceof Error && error.message!=="[object Object]" ? error.message : "拒绝原因未完整读取，请重新预检核对。";
}

/** The keyed boundary prevents an old stock/version response from activating a different selection. */
export function PaperSelectionAction(props:PaperSelectionBinding) {
  return <PaperSelectionActionInner key={JSON.stringify([props.symbol,props.selectionRef,props.sourceVersion])} {...props}/>;
}
function PaperSelectionActionInner(binding:PaperSelectionBinding) {
  const {device,open:openAccess}=useDeviceAccess();
  const [initial]=useState(()=>restored(binding));
  const [pending,setPending]=useState<PendingPaperSelection|null>(initial.pending);
  const pendingRef=useRef(initial.pending);
  const [price,setPrice]=useState(initial.pending ? String(initial.pending.price) : "");
  const [quantity,setQuantity]=useState(initial.pending ? String(initial.pending.quantity) : "100");
  const [checked,setChecked]=useState<{signature:string;data:PaperSelectionActionData}|null>(null);
  const [execution,setExecution]=useState<Execution|null>(initial.pending ? {state:"unknown",reason:"原模拟请求结果尚未核实；保留同一请求编号。",order:null} : null);
  const [busy,setBusy]=useState<"check"|"submit"|"recover"|null>(null);
  const [note,setNote]=useState("");
  const [clock,setClock]=useState(()=>Date.now());
  const busyRef=useRef(false), alive=useRef(true), epoch=useRef(0), composing=useRef(false);
  const controller=useRef<AbortController|null>(null);
  const numericPrice=price.trim()==="" ? undefined : Number(price);
  const numericQuantity=Number(quantity);
  const inputValid=(numericPrice===undefined || Number.isFinite(numericPrice)&&numericPrice>0)
    && Number.isSafeInteger(numericQuantity) && numericQuantity>0 && numericQuantity%100===0;
  const signature=JSON.stringify([price,quantity]);
  const data=checked?.signature===signature ? checked.data : null;
  const observed=execution ?? data?.execution ?? {state:"not_started" as const,reason:null,order:null};
  const maintenance=device?.state==="active" && device.maintenance===true
    && (device.maintenance_until===undefined || device.maintenance_until*1000>clock);
  const permitted=maintenance && data?.permission.maintenance_granted===true;
  const canSubmit=!busy && !pending && !initial.error && inputValid && permitted
    && observed.state==="not_started" && currentPaperPreflight(data,numericQuantity,numericPrice,clock);

  useEffect(()=>{alive.current=true;return()=>{alive.current=false;controller.current?.abort();};},[]);
  useEffect(()=>{
    const expires=data?.preflight?.expires_at;
    if (!expires) return;
    const timer=window.setTimeout(()=>setClock(Date.now()),Math.max(1,Date.parse(expires)-Date.now()));
    const visible=()=>{if (!document.hidden) setClock(Date.now());};
    document.addEventListener("visibilitychange",visible);
    return()=>{clearTimeout(timer);document.removeEventListener("visibilitychange",visible);};
  },[data]);

  const invalidate=()=>{epoch.current++;controller.current?.abort();busyRef.current=false;setBusy(null);setChecked(null);setNote("");};
  async function inspect(recover=false) {
    if (busyRef.current || composing.current || !validPaperSelectionBinding(binding) || (!recover && !inputValid)) return;
    busyRef.current=true;
    const token=++epoch.current;
    const original=pendingRef.current;
    const control=new AbortController();controller.current=control;
    setBusy(recover?"recover":"check");setNote("");
    try {
      const result=await getPaperSelectionAction(binding,{quantity:original?.quantity ?? (inputValid?numericQuantity:100),
        price:original?.price ?? (inputValid?numericPrice:undefined),requestId:original?.requestId,signal:control.signal});
      if (!alive.current || token!==epoch.current) return;
      setChecked({signature,data:result});setClock(Date.now());
      if (original && ["filled","rejected","expired","cancelled"].includes(result.execution.state)) {
        if (resolvePaperSelectionRequest(original)) {pendingRef.current=null;setPending(null);}
        else setNote("结果已核实；本页请求标识仍保留，不会重复提交。");
        setExecution(result.execution);
      } else if (original && result.execution.state==="not_started") {
        setExecution({state:"unknown",reason:"原请求尚无可确认回执；继续核对，不会另建请求重下单。",order:null});
      } else setExecution(result.execution);
    } catch(error) {
      if (alive.current && token===epoch.current) {setChecked(null);setNote(message(error));}
    } finally {if (alive.current && token===epoch.current) {busyRef.current=false;setBusy(null);}}
  }
  async function submit() {
    if (busyRef.current || composing.current || !canSubmit || !data
      || device?.state!=="active" || !device.maintenance || (device.maintenance_until!==undefined && device.maintenance_until*1000<=Date.now())
      || !currentPaperPreflight(data,numericQuantity,numericPrice,Date.now())) {setClock(Date.now());return;}
    busyRef.current=true;setBusy("submit");setNote("");
    let original:PendingPaperSelection|null=null;
    try {
      original=createPaperSelectionRequest(binding,data,numericQuantity,numericPrice);
      pendingRef.current=original;setPending(original);
      const result=await submitPaperSelectionRequest(original);
      if (["filled","rejected"].includes(result.status)) {
        if (resolvePaperSelectionRequest(original)) pendingRef.current=null;
        else if (alive.current) setNote("结果已核实；本页请求标识仍保留，不会重复提交。");
      }
      if (!alive.current) return;
      setPending(pendingRef.current);setChecked(null);
      setExecution({state:result.status as Execution["state"],reason:result.status==="filled" ? "模拟成交已保存。" : result.status==="pending" ? "模拟委托已保存，尚未确认成交。" : "模拟委托已拒绝。",
        order:{id:result.id,status:result.status,filled_price:result.filled_price??null,fee:result.fee??null}});
    } catch(error) {
      if (original && definitivePaperRejection(error) && resolvePaperSelectionRequest(original)) pendingRef.current=null;
      if (!alive.current) return;
      setPending(pendingRef.current);setChecked(null);
      if (original) setExecution({state:definitivePaperRejection(error)?"rejected":"unknown",reason:definitivePaperRejection(error)?"模拟提交被拒绝；未据此宣称成交。":"提交结果未知；保留原请求，只核对回执，不重复下单。",order:null});
      setNote(message(error));
    } finally {if (alive.current) {busyRef.current=false;setBusy(null);}}
  }
  const guardIme=(event:KeyboardEvent<HTMLButtonElement>)=>{if (event.nativeEvent.isComposing || event.keyCode===229) {event.preventDefault();event.stopPropagation();}};
  const expired=!!data?.preflight && Date.parse(data.preflight.expires_at)<=clock;
  const bindingValid=validPaperSelectionBinding(binding);
  const eligibility=data?.eligibility.state;
  return <section className={styles.action} aria-label={`${binding.symbol}模拟买入`} onClick={event=>event.stopPropagation()}
    onCompositionStart={()=>{composing.current=true;}} onCompositionEnd={()=>{composing.current=false;}}>
    <header className={styles.heading}><strong>模拟买入</strong><span>仅模拟 · 不连接券商</span></header>
    <ol className={styles.steps} aria-label="模拟动作核对步骤">
      <li data-state={data?"ready":"unknown"}><span>1</span><div>原入选<small>{data?"已核对版本":"待读取"}</small></div></li>
      <li data-state={expired?"blocked":eligibility??"unknown"}><span>2</span><div>当前资格<small>{expired?"预检已过期":eligibility==="ready"?"通过预检":eligibility==="blocked"?"当前受限":eligibility==="unknown"?"资料待核":"待预检"}</small></div></li>
      <li data-state={observed.state}><span>3</span><div>模拟委托<small>{stateLabels[observed.state]}</small></div></li>
    </ol>
    <div className={styles.inputs}>
      <label>模拟价格<input aria-label="模拟价格" inputMode="decimal" type="number" min="0.01" step="0.01" value={price} placeholder="本次报价" disabled={!!pending||busy==="submit"} onChange={event=>{invalidate();setPrice(event.target.value);}}/></label>
      <label>股数<input aria-label="模拟股数" inputMode="numeric" type="number" min="100" step="100" value={quantity} disabled={!!pending||busy==="submit"} onChange={event=>{invalidate();setQuantity(event.target.value);}}/></label>
    </div>
    {!inputValid && <p className={styles.warning} role="status">价格须为正数；股数须为100股的整数倍。</p>}
    {!bindingValid && <p className={styles.warning} role="status">原入选身份不完整，暂不预检或提交。</p>}
    {data?.preflight && <p className={styles.facts}>本次预检 {fmt(data.preflight.price)} 元 × {data.preflight.quantity} 股 · 费用 {fmt(data.preflight.fee)} 元<small>{dateTimeTextBJ(data.preflight.checked_at,{seconds:true})} · {expired?"已失效":"30秒内有效，改参数需重检"}</small></p>}
    {data?.quote && <p className={styles.source}>{data.quote.source??"来源未知"} · {dateTimeTextBJ(data.quote.as_of,{seconds:true})} · 质量 {data.quote.quality??"未知"}</p>}
    {data?.eligibility.reasons.length ? <ul className={styles.reasons} aria-label="当前阻断原因">{data.eligibility.reasons.map((reason,i)=><li key={`${i}:${reason}`}>{reason}</li>)}</ul> : null}
    {observed.reason && <p role="status" className={observed.state==="unknown"?styles.warning:styles.status}>{observed.reason}</p>}
    {observed.order && <p className={styles.facts}>模拟委托 #{observed.order.id} · {stateLabels[observed.state]}{observed.state==="filled"?` · ${fmt(observed.order.filled_price)} 元 · 费用 ${fmt(observed.order.fee)} 元`:""}</p>}
    {initial.error && <p role="status" className={styles.warning}>无法读取本页请求标识，暂不提交模拟单。</p>}
    {note && <p role="status" className={styles.warning}>{note}</p>}
    {!maintenance && <div className={styles.access}><span>普通配对可预检；提交需临时维护授权。</span><button type="button" onClick={openAccess}>设备与权限</button></div>}
    {maintenance && data && !data.permission.maintenance_granted && <p className={styles.warning}>服务端尚未确认维护权限，请核对设备授权后重新预检。</p>}
    <div className={styles.controls}>
      <button type="button" disabled={!!busy||!!pending||!inputValid||!bindingValid} onKeyDown={guardIme} onClick={()=>void inspect()}>{busy==="check"?"预检中…":"模拟预检"}</button>
      <button type="button" className={styles.confirm} disabled={!canSubmit} onKeyDown={guardIme} onClick={()=>void submit()}>{busy==="submit"?"正在提交…":"确认模拟买入"}</button>
      {(pending||observed.state!=="not_started") && <button type="button" disabled={!!busy} onKeyDown={guardIme} onClick={()=>void inspect(true)}>{busy==="recover"?"正在核对…":pending?"核对原请求":"核对模拟状态"}</button>}
    </div>
  </section>;
}
