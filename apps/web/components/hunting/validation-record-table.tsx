"use client";

import type {ReactNode} from "react";
import {useIncremental} from "@/hooks/use-incremental";

/** Reuse the project's scroll owner; appending rows never reloads data or resets selection. */
export function ValidationRecordTable<T>({items,columns,renderRow,resetKey,label,empty}: {
  items:readonly T[];columns:string[];renderRow:(row:T,index:number)=>ReactNode;
  resetKey:string;label:string;empty:string;
}) {
  const {shown,visible,hasMore,sentinelRef,loadMore}=useIncremental(items,{pageSize:30,step:30,resetKey});
  return <div className="validation-table-scroll" tabIndex={0} role="region" aria-label={label}>
    <table><thead><tr>{columns.map(t => <th key={t}>{t}</th>)}</tr></thead><tbody>{shown.map(renderRow)}</tbody></table>
    {!items.length ? <p className="validation-empty">{empty}</p> : <div className="validation-load-more" ref={sentinelRef}>
      <span aria-live="polite">已展示 {Math.min(visible,items.length)} / {items.length} 条</span>
      {hasMore ? <button type="button" className="quiet-action" onClick={loadMore}>向下滑动加载更多<svg className="validation-load-arrow" aria-hidden="true" width="14" height="14" viewBox="0 0 16 16"><path d="M8 2v11m-4-4 4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5"/></svg></button> : <span>已展示全部</span>}
    </div>}
  </div>;
}
