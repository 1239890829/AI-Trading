"use client";

import Link from "next/link";
import type {ReactNode} from "react";
import {ASSET_LABELS, assetWorkbenchUrl, type WorkbenchAsset} from "@/lib/asset-identity";

/** One route and visual frame; each asset owns its data and supported actions. */
export function WorkbenchFrame({asset, children, actions, connection, back}: {
  asset: WorkbenchAsset; children: ReactNode; actions?: ReactNode; connection?: ReactNode; back?: ReactNode;
}) {
  return <main data-workspace="workbench" data-asset={asset} className="task-page bc-workbench">
    <h1 className="sr-only">工作台</h1>
    <header className="bc-workbench-header">
      <div className="bc-workbench-title">{back}<h2>关注与核对</h2>{connection}</div>
      <div className="bc-workbench-objects">
        <nav aria-label="资产类型" className="bc-object-switch bc-asset-switch">
          {(["cn_equity", "fund", "us_equity"] as const).map(value => <Link key={value} href={assetWorkbenchUrl(value, undefined, value === "fund" ? "research" : "watch")} aria-current={value === asset ? "page" : undefined}>{ASSET_LABELS[value]}</Link>)}
        </nav>
        {actions}
      </div>
    </header>
    {children}
  </main>;
}
