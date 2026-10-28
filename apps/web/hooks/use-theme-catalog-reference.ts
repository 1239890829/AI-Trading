"use client";

import {useCallback,useMemo,useSyncExternalStore} from "react";
import type {CurrentThemeCatalogReference} from "@/types/market";
import {catalogCompletionOwnerKey,createCatalogCompletionObserver,NO_CATALOG_COMPLETION} from "@/lib/theme-catalog-completion";

const visible=()=>typeof document === "undefined" || document.visibilityState !== "hidden";
const subscribeVisibility=(listener:()=>void)=>{document.addEventListener("visibilitychange",listener);return()=>document.removeEventListener("visibilitychange",listener);};
const serverVisible=()=>false;
const serverSnapshot=()=>NO_CATALOG_COMPLETION;

/** A local read-time overlay. Original report/selection objects and gates never change. */
export function useThemeCatalogReference(original:CurrentThemeCatalogReference|null|undefined,scope:string,active=true):CurrentThemeCatalogReference|undefined {
  const shown=useSyncExternalStore(subscribeVisibility,visible,serverVisible);
  const pending=!!original?.items.some(item=>item.catalog_mapping_state === "unavailable" && item.catalog_mapping_context?.reason === "catalog_read_pending");
  const key=original?.completion_key;
  const validKey=typeof key === "string" && /^[a-f0-9]{32}$/.test(key);
  const ownerKey=pending && validKey ? catalogCompletionOwnerKey(key) : "";
  // Retire the local observation when its date/version scope changes; the owner stays shared.
  const observation=useMemo(()=>({scope,observer:ownerKey && validKey ? createCatalogCompletionObserver(ownerKey,key) : null}),[ownerKey,key,validKey,scope]);
  const subscribe=useCallback((listener:()=>void)=>active && shown && observation.observer ? observation.observer.subscribe(listener) : ()=>{},[active,shown,observation]);
  const snapshot=useCallback(()=>observation.observer ? observation.observer.getSnapshot() : NO_CATALOG_COMPLETION,[observation]);
  const completion=useSyncExternalStore(subscribe,snapshot,serverSnapshot);
  return useMemo(()=>{
    if (!original || !pending) return original ?? undefined;
    const invalid=!validKey;
    if (!invalid && (completion.status === "idle" || completion.status === "pending")) return original;
    const result=completion.status === "done" ? completion.result : null;
    const available=result?.state === "available" && result.completion_key === key;
    const reason=invalid ? key == null ? "completion_key_missing" : "completion_key_invalid" : completion.status === "error" ? completion.reason : result?.reason || "completion_unavailable";
    return {...original,completion_key:undefined,catalog_version:available ? result.catalog_version : null,observed_at:available ? result.observed_at : null,items:original.items.map(item=>{
      const identity=available && item.catalog_mapping_state !== "unclassified" && Object.hasOwn(result.identities,item.theme) ? result.identities[item.theme] : null;
      const state=item.catalog_mapping_state === "unclassified" ? "unclassified" as const : identity ? "exact" as const
        : available ? result.ambiguous.includes(item.theme) ? "ambiguous" as const : "unmapped" as const : "unavailable" as const;
      return {...item,exact_catalog:identity,catalog_mapping_state:state,catalog_mapping_context:{...item.catalog_mapping_context,
        completion_key:undefined,catalog_version:available ? result.catalog_version : null,observed_at:available ? result.observed_at : null,reason:available ? null : reason}};
    })};
  },[original,pending,key,validKey,completion]);
}
