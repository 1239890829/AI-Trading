import type {ValidationReport} from "./api/validation";

/** Select only the server-projected original cohort; never recalculate returns. */
export function versionObservation(report: ValidationReport, key: string): ValidationReport | null {
  const group = report.version_groups?.find(row => row.key === key);
  if (!group || !report.version_entries || (report.scope !== "all" && report.scope !== group.scope)) return null;
  const byId = new Map(report.version_entries.map(row => [row.id, row]));
  const entries = group.entry_ids.map(id => byId.get(id));
  if (new Set(group.entry_ids).size !== group.entry_ids.length || entries.length !== group.selection.count
    || entries.some(row => !row || row.scope !== group.scope || (row.method_version || null) !== group.method_version
      || (row.feature_version || null) !== group.feature_version)) return null;
  return {...report, selection: group.selection, daily: group.daily, coverage: group.coverage,
    entries: entries as ValidationReport["entries"]};
}
