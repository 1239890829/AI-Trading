import type {SavedMethodKey, SavedMethodRunDetail, SavedMethodRunIdentity} from "@/lib/api/picks";

export type MethodRowFilter = "selected" | "rejected" | "candidate" | "unknown" | "all";
export interface MethodRunLocation {
  date: string;
  sourceVersion: string;
  missingSource: boolean;
  strategyVersion: string;
  runId: string;
  contentVersion: string;
  filter: MethodRowFilter;
  row: string;
}
export function methodRunDay(value: string | null | undefined): string {
  const day = (value ?? "").replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return "";
  const date = new Date(`${day}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0,10) === day ? day : "";
}
export function readMethodRunLocation(search: string, method: string): MethodRunLocation {
  const params = new URLSearchParams(search);
  const own = !params.get("method") || params.get("method") === method;
  const filter = own ? params.get("run_state") : "";
  return {date: params.get("date") ?? "", sourceVersion: own ? params.get("source_version") ?? "" : "",
    missingSource: own && params.get("source_version_missing") === "true", strategyVersion: own ? params.get("strategy_version") ?? "" : "",
    runId: own ? params.get("run_id") ?? "" : "", contentVersion: own ? params.get("content_version") ?? "" : "",
    filter: ["selected","rejected","candidate","unknown","all"].includes(filter ?? "") ? filter as MethodRowFilter : "selected",
    row: own ? params.get("run_row") ?? "" : ""};
}
export function methodRunIdentity(method: SavedMethodKey, location: MethodRunLocation): SavedMethodRunIdentity | null {
  const day = methodRunDay(location.date);
  if (!day || !location.runId || !/^[a-f0-9]{64}$/i.test(location.contentVersion)
    || (!location.sourceVersion && !location.missingSource) || (location.sourceVersion && location.missingSource)) return null;
  return {method,run_id:location.runId,trade_date:day,content_version:location.contentVersion,
    source_version:location.missingSource ? null : location.sourceVersion,strategy_version:location.strategyVersion || null,state:"saved"};
}
export function identityLocation(identity: SavedMethodRunIdentity, current: MethodRunLocation): MethodRunLocation {
  return {...current,date:identity.trade_date,runId:identity.run_id,contentVersion:identity.content_version,
    sourceVersion:identity.source_version ?? "",missingSource:identity.source_version === null,
    strategyVersion:identity.strategy_version ?? ""};
}
export function methodRunKey(identity: SavedMethodRunIdentity) {
  return JSON.stringify([identity.method,methodRunDay(identity.trade_date),identity.run_id,identity.source_version,identity.content_version]);
}
export function methodRunMatches(value: SavedMethodRunDetail, identity: SavedMethodRunIdentity, offset = 0) {
  return value.method === identity.method && value.run_id === identity.run_id && methodRunDay(value.trade_date) === methodRunDay(identity.trade_date)
    && value.content_version === identity.content_version && value.source_version === identity.source_version
    && (!identity.strategy_version || value.strategy_version === identity.strategy_version)
    && value.coverage?.offset === offset;
}
export function methodRunUrl(pathname: string, search: string, method: string, location?: MethodRunLocation): string {
  const params = new URLSearchParams(search);
  params.set("method",method);
  for (const key of ["source_version","source_version_missing","strategy_version","run_id","content_version","run_state","run_row"]) params.delete(key);
  if (location) {
    if (location.date) params.set("date",location.date); else params.delete("date");
    if (location.sourceVersion) params.set("source_version",location.sourceVersion);
    if (location.missingSource) params.set("source_version_missing","true");
    if (location.strategyVersion) params.set("strategy_version",location.strategyVersion);
    if (location.runId) params.set("run_id",location.runId);
    if (location.contentVersion) params.set("content_version",location.contentVersion);
    if (location.filter !== "selected") params.set("run_state",location.filter);
    if (location.row) params.set("run_row",location.row);
  }
  return `${pathname}?${params}`;
}
