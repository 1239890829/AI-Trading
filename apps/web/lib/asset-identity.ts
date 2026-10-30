/** Asset namespaces never infer an A-share identity from a fund's six-digit code. */
export type WorkbenchAsset = "cn_equity" | "fund" | "us_equity";
export function workbenchAsset(value: string | null | undefined): WorkbenchAsset {
  return value === "fund" || value === "us_equity" ? value : "cn_equity";
}
export const ASSET_LABELS: Record<WorkbenchAsset, string> = {cn_equity: "A 股", fund: "基金", us_equity: "美股"};

export type AssetWorkbenchMode = "research" | "watch" | "positions";
export function assetWorkbenchUrl(asset: WorkbenchAsset, sourceSymbol?: string, mode: AssetWorkbenchMode = "watch") {
  const params = new URLSearchParams({mode});
  if (asset !== "cn_equity") params.set("asset", asset);
  if (sourceSymbol) params.set("symbol", sourceSymbol);
  return `/workbench?${params.toString()}`;
}

export function validFundSymbol(value: string | null | undefined): value is string {
  return typeof value === "string" && /^\d{6}\.OF$/.test(value);
}

export function fundIdentityMatches(instrument: {instrument_id: string; source_symbol: string; asset_type: string; market: string; currency: string; symbol: string}) {
  return instrument.asset_type === "fund" && instrument.market === "OF" && /^[A-Z]{3}$/.test(instrument.currency)
    && validFundSymbol(instrument.source_symbol) && instrument.symbol === instrument.source_symbol.slice(0, 6)
    && instrument.instrument_id === `fund:OF:${instrument.symbol}:${instrument.currency}`;
}

export const ASSET_RECORDS_EVENT = "ashare-asset-records-changed";
export function notifyAssetRecordsChanged() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(ASSET_RECORDS_EVENT));
}
