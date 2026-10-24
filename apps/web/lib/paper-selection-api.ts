import {ApiError} from "@/lib/api/client";
import {getJson} from "@/lib/api/internal";
import {placePaperOrder} from "@/lib/api/paper";

export interface PaperSelectionBinding {symbol: string; selectionRef: string; sourceVersion: string}
export type PaperSelectionExecutionState = "not_started" | "pending" | "filled" | "rejected" | "expired" | "cancelled" | "unknown";
export interface PaperSelectionActionData {
  scope: "main"; read_only: true;
  selection: {ref: string; symbol: string; source_version: string};
  eligibility: {state: "ready" | "blocked" | "unknown"; reason_code: string; reasons: string[]};
  quote: {price: number | null; source: string | null; as_of: string | null; quality: string | null} | null;
  preflight: {allowed: boolean; side: "buy"; price: number | null; quantity: number; fee: number | null;
    checked_at: string; expires_at: string; decision_id: string | null; decision_version: string | null} | null;
  execution: {state: PaperSelectionExecutionState; reason: string | null; order: {id: number; status: string; filled_price: number | null; fee: number | null} | null};
  permission: {maintenance_required: true; maintenance_granted: boolean};
}
export interface PendingPaperSelection extends PaperSelectionBinding {
  requestId: string; expiresAt: string; price: number; quantity: number; decisionId: string; decisionVersion: string;
}
const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const number = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const nullableNumber = (value: unknown) => value === null || number(value);
const nullableText = (value: unknown) => value === null || text(value);
const time = (value: unknown): value is string => text(value) && /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) && Number.isFinite(Date.parse(value));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const executionStates = new Set(["not_started", "pending", "filled", "rejected", "expired", "cancelled", "unknown"]);
const contractError = () => new ApiError(0,"模拟动作资料未能核实；未据此允许提交。","invalid_paper_selection_contract");

export function validPaperSelectionBinding(binding: PaperSelectionBinding): boolean {
  return /^\d{6}$/.test(binding.symbol) && /^(daily|intraday):\d{4}-\d{2}-\d{2}:\d{6}$/.test(binding.selectionRef)
    && binding.selectionRef.endsWith(`:${binding.symbol}`) && binding.sourceVersion.length > 0 && binding.sourceVersion.length <= 128;
}
/** Check the received account/source/decision envelope before any UI can enable submission. */
export function parsePaperSelectionAction(raw: unknown, binding: PaperSelectionBinding): PaperSelectionActionData {
  const value=object(raw), selection=object(value.selection), eligibility=object(value.eligibility), execution=object(value.execution), permission=object(value.permission);
  if (!validPaperSelectionBinding(binding) || value.scope!=="main" || value.read_only!==true
    || selection.ref!==binding.selectionRef || selection.symbol!==binding.symbol || selection.source_version!==binding.sourceVersion
    || !["ready","blocked","unknown"].includes(String(eligibility.state)) || !text(eligibility.reason_code)
    || !Array.isArray(eligibility.reasons) || !eligibility.reasons.every(reason=>typeof reason==="string")
    || !executionStates.has(String(execution.state)) || !nullableText(execution.reason)
    || permission.maintenance_required!==true || typeof permission.maintenance_granted!=="boolean") throw contractError();
  if (value.quote!==null) {
    const quote=object(value.quote);
    if (!nullableNumber(quote.price) || !nullableText(quote.source) || !(quote.as_of===null || time(quote.as_of))
      || !(quote.quality===null || ["high","medium","low","stale","invalid"].includes(String(quote.quality)))) throw contractError();
  }
  if (value.preflight!==null) {
    const check=object(value.preflight);
    if (typeof check.allowed!=="boolean" || check.side!=="buy" || !nullableNumber(check.price)
      || !number(check.quantity) || !Number.isSafeInteger(check.quantity) || !nullableNumber(check.fee)
      || !time(check.checked_at) || !time(check.expires_at) || Date.parse(check.expires_at)<=Date.parse(check.checked_at)
      || Date.parse(check.expires_at)-Date.parse(check.checked_at)>30_000
      || !nullableText(check.decision_id) || !nullableText(check.decision_version)) throw contractError();
    if (check.allowed && (eligibility.state!=="ready" || !(number(check.price)&&check.price>0)
      || check.quantity<=0 || check.quantity%100!==0 || !(number(check.fee)&&check.fee>=0)
      || !text(check.decision_id) || !text(check.decision_version)
      || !["high","medium"].includes(String(object(value.quote).quality)) || !text(object(value.quote).source)
      || !time(object(value.quote).as_of) || !(number(object(value.quote).price) && Number(object(value.quote).price)>0))) throw contractError();
  }
  if (execution.order!==null) {
    const order=object(execution.order);
    if (!number(order.id) || !Number.isInteger(order.id) || order.id<=0 || !text(order.status)
      || !nullableNumber(order.filled_price) || !nullableNumber(order.fee)) throw contractError();
  }
  if (execution.state==="filled") {
    const order=object(execution.order);
    if (order.status!=="filled" || !number(order.filled_price) || order.filled_price<=0 || !number(order.fee) || order.fee<0) throw contractError();
  }
  return raw as PaperSelectionActionData;
}
export async function getPaperSelectionAction(binding: PaperSelectionBinding, options: {price?:number; quantity?:number; requestId?:string; signal?:AbortSignal} = {}): Promise<PaperSelectionActionData> {
  if (!validPaperSelectionBinding(binding) || !Number.isSafeInteger(options.quantity??100) || (options.quantity??100)<=0 || (options.quantity??100)%100!==0
    || (options.price!==undefined && (!number(options.price) || options.price<=0))) throw contractError();
  const query=new URLSearchParams({selection_ref:binding.selectionRef,source_version:binding.sourceVersion,quantity:String(options.quantity??100)});
  if (options.price!==undefined) query.set("price",String(options.price));
  if (options.requestId) {if (!UUID.test(options.requestId)) throw contractError(); query.set("request_id",options.requestId);}
  const response=await getJson<unknown>(`/api/paper/selection-action?${query}`,8000,{signal:options.signal,fresh:true});
  const value=parsePaperSelectionAction(response.data,binding);
  if (value.preflight && (value.preflight.quantity!==(options.quantity??100) || (options.price!==undefined && value.preflight.price!==options.price))) throw contractError();
  return value;
}
export function currentPaperPreflight(data: PaperSelectionActionData | null, quantity: number, price: number | undefined, now: number): boolean {
  const check=data?.preflight;
  return !!(data?.eligibility.state==="ready" && check?.allowed && check.quantity===quantity
    && (price===undefined || check.price===price) && Date.parse(check.checked_at)<=now+5_000
    && now-Date.parse(check.checked_at)<=30_000 && Date.parse(check.expires_at)>now);
}
const pendingKey=(binding: PaperSelectionBinding)=>`paper-selection:main:${JSON.stringify([binding.symbol,binding.selectionRef,binding.sourceVersion])}`;
export function readPendingPaperSelection(binding: PaperSelectionBinding): PendingPaperSelection | null {
  if (typeof window==="undefined") return null;
  const raw=sessionStorage.getItem(pendingKey(binding));
  if (!raw) return null;
  const value=JSON.parse(raw) as PendingPaperSelection;
  if (value.symbol!==binding.symbol || value.selectionRef!==binding.selectionRef || value.sourceVersion!==binding.sourceVersion
    || !UUID.test(value.requestId) || !time(value.expiresAt) || !number(value.price) || value.price<=0
    || !number(value.quantity) || !Number.isSafeInteger(value.quantity) || value.quantity<=0 || value.quantity%100!==0
    || !text(value.decisionId) || !text(value.decisionVersion)) throw contractError();
  return value;
}
function requestUuid(): string {
  if (typeof crypto.randomUUID==="function") return crypto.randomUUID();
  // getRandomValues also works in the repository's approved HTTP LAN setup.
  const bytes=crypto.getRandomValues(new Uint8Array(16));
  bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const hex=[...bytes].map(value=>value.toString(16).padStart(2,"0")).join("");
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
/** Persist the UUID before POST. Existing unresolved requests cannot become a new intent. */
export function createPaperSelectionRequest(binding: PaperSelectionBinding, data: PaperSelectionActionData, quantity: number, price?: number): PendingPaperSelection {
  parsePaperSelectionAction(data,binding);
  if (readPendingPaperSelection(binding)) throw new Error("原模拟请求尚未核实，请先核对结果。");
  if (!currentPaperPreflight(data,quantity,price,Date.now()) || !data.permission.maintenance_granted) throw contractError();
  const check=data.preflight!;
  const pending:PendingPaperSelection={...binding,requestId:requestUuid(),expiresAt:check.expires_at,
    price:check.price!,quantity:check.quantity,decisionId:check.decision_id!,decisionVersion:check.decision_version!};
  sessionStorage.setItem(pendingKey(binding),JSON.stringify(pending));
  return pending;
}
export function resolvePaperSelectionRequest(pending: PendingPaperSelection): boolean {
  try {
    const saved=readPendingPaperSelection(pending);
    if (!saved) return true;
    if (saved.requestId!==pending.requestId) return false;
    sessionStorage.removeItem(pendingKey(pending));
    return readPendingPaperSelection(pending)===null;
  } catch {return false;}
}
export async function submitPaperSelectionRequest(pending: PendingPaperSelection) {
  if (readPendingPaperSelection(pending)?.requestId!==pending.requestId) throw contractError();
  const result=await placePaperOrder(pending.symbol,"buy",pending.price,pending.quantity,{
    selection_ref:pending.selectionRef,source_version:pending.sourceVersion,decision_id:pending.decisionId,
    decision_version:pending.decisionVersion,request_id:pending.requestId,expires_at:pending.expiresAt});
  if (result.symbol!==pending.symbol || result.side!=="buy" || !number(result.id) || result.id<=0 || !Number.isInteger(result.id) || !["pending","filled","rejected"].includes(result.status)
    || !(result.filled_price===undefined || nullableNumber(result.filled_price)) || !(result.fee===undefined || nullableNumber(result.fee))) throw contractError();
  if (result.status==="filled" && (!number(result.filled_price) || result.filled_price<=0 || !number(result.fee) || result.fee<0)) throw contractError();
  return result;
}
export function definitivePaperRejection(error: unknown): boolean {return error instanceof ApiError && [401,403,422].includes(error.status);}
