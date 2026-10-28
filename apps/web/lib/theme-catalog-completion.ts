import {getThemeCatalogCompletion, type ThemeCatalogCompletion} from "@/lib/api/themes";
import {API_BASE,ApiError} from "@/lib/api/client";
import {deviceSessionEpoch} from "@/lib/api/internal";

export type CatalogCompletionSnapshot = {status:"pending"} | {status:"done"; result:ThemeCatalogCompletion}
  | {status:"error"; reason:string};
export const NO_CATALOG_COMPLETION = Object.freeze({status:"idle" as const});
type Snapshot = CatalogCompletionSnapshot | typeof NO_CATALOG_COMPLETION;
type Entry = {epoch:number; key:string; controller:AbortController; snapshot:CatalogCompletionSnapshot; listeners:Set<()=>void>};
const entries = new Map<string,Entry>();
const MAX_COMPLETIONS = 8;
const CAPACITY: CatalogCompletionSnapshot = Object.freeze({status:"error",reason:"completion_capacity"});

export function catalogCompletionOwnerKey(key: string): string {
  return JSON.stringify([deviceSessionEpoch(),typeof window === "undefined" ? "server" : window.location.origin,API_BASE,key]);
}
export function catalogCompletionSnapshot(ownerKey: string): Snapshot {
  const entry=entries.get(ownerKey);
  return entry && entry.epoch === deviceSessionEpoch() ? entry.snapshot : NO_CATALOG_COMPLETION;
}
function failureReason(error:unknown) {
  if (!(error instanceof ApiError)) return "completion_read_failed";
  if (error.status === 401 || error.status === 403) return "completion_access_denied";
  if (error.status === 404 || error.status === 410) return "completion_expired";
  return error.code === "timeout" ? "completion_transport_timeout" : error.code === "bad_catalog_completion" ? "completion_invalid_response" : "completion_read_failed";
}

/** One bounded wait per retained owner, including StrictMode and temporarily absent readers.
 * Unsubscribe removes only the listener. It must not cancel/restart the shared read.
 * Settled, unobserved entries are evicted first; active or observed owners stay pinned. */
function subscribeCatalogCompletion(ownerKey:string,key:string,listener:()=>void,onCapacity:()=>void):()=>void {
  const epoch=deviceSessionEpoch();
  for (const [id,entry] of entries) if (entry.epoch !== epoch) {entry.controller.abort();entries.delete(id);}
  let entry=entries.get(ownerKey);
  if (!entry) {
    while (entries.size >= MAX_COMPLETIONS) {
      const candidate=[...entries].find(([,value])=>value.snapshot.status !== "pending" && value.listeners.size === 0);
      if (!candidate) break;
      entries.delete(candidate[0]);
    }
    if (entries.size >= MAX_COMPLETIONS) {
      // Keep this terminal snapshot bounded to the caller; no request or retry is started.
      onCapacity();
      let alive=true;
      queueMicrotask(()=>{if(alive)listener();});
      return ()=>{alive=false;};
    }
    entry={epoch,key,controller:new AbortController(),snapshot:{status:"pending"},listeners:new Set()};
    entries.set(ownerKey,entry);
    const current=entry;
    void getThemeCatalogCompletion(key,{signal:entry.controller.signal}).then(result=>{
      current.snapshot=current.epoch === deviceSessionEpoch() ? {status:"done",result} : {status:"error",reason:"completion_session_changed"};
    }).catch(error=>{current.snapshot={status:"error",reason:failureReason(error)};}).finally(()=>{
      current.listeners.forEach(notify=>notify());
    });
  }
  // Touch an existing terminal owner without evicting a mounted subscriber.
  entries.delete(ownerKey);entries.set(ownerKey,entry);
  entry.listeners.add(listener);
  return ()=>{entry.listeners.delete(listener);};
}
/** Rejected capacity is a caller-local terminal value, not another global token cache. */
export function createCatalogCompletionObserver(ownerKey:string,key:string) {
  let rejected=false;
  return {
    getSnapshot:():Snapshot=>rejected ? CAPACITY : catalogCompletionSnapshot(ownerKey),
    subscribe:(listener:()=>void)=>rejected ? ()=>{} : subscribeCatalogCompletion(ownerKey,key,listener,()=>{rejected=true;}),
  };
}
