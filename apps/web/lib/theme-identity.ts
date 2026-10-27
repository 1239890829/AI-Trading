import type {ThemeCatalogIdentity, ThemeMappingState} from "@/types/market";

export type OfficialThemeIdentity = ThemeCatalogIdentity & {
  source: "ths_official"; basis: "exact_name"; kind: "concept" | "industry";
  catalog_version: string; observed_at: string;
};

/** Identity evidence never derives a code from a label, prefix, or shared members. */
export function officialThemeIdentity(reason: string, candidate: unknown, state: unknown, notAfter?: string): OfficialThemeIdentity | null {
  if (state !== "exact" || !candidate || typeof candidate !== "object" || Array.isArray(candidate)) return null;
  const value = candidate as Record<string, unknown>;
  if (value.source !== "ths_official" || value.basis !== "exact_name" || value.name !== reason
    || value.kind !== "concept" && value.kind !== "industry" || typeof value.code !== "string" || !/^\d{6}\.TI$/.test(value.code)
    || typeof value.catalog_version !== "string" || !/^[a-f0-9]{64}$/i.test(value.catalog_version)
    || typeof value.observed_at !== "string" || !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value.observed_at)
    || !Number.isFinite(Date.parse(value.observed_at))) return null;
  if (notAfter && (!Number.isFinite(Date.parse(notAfter)) || Date.parse(value.observed_at) > Date.parse(notAfter))) return null;
  return {code:value.code, name:reason, source:"ths_official", basis:"exact_name", kind:value.kind,
    catalog_version:value.catalog_version, observed_at:value.observed_at};
}

/** A read-time reference stays separate from the original selection/report evidence. */
export function currentThemeMapping(reason: string, reference: unknown): {state: ThemeMappingState; identity: OfficialThemeIdentity | null} | null {
  if (!reference || typeof reference !== "object" || Array.isArray(reference)) return null;
  const value = reference as Record<string, unknown>;
  if (value.scope !== "current_directory_reference" || !Array.isArray(value.items)) return null;
  const matches = value.items.filter((item): item is Record<string, unknown> => !!item && typeof item === "object" && !Array.isArray(item) && item.theme === reason);
  if (matches.length !== 1) return null;
  const state=matches[0].catalog_mapping_state;
  if (!["exact","unmapped","ambiguous","unclassified","unavailable"].includes(String(state))) return null;
  if (state === "unavailable" && value.catalog_version == null && value.observed_at == null) return {state,identity:null};
  if (typeof value.catalog_version !== "string" || !/^[a-f0-9]{64}$/i.test(value.catalog_version)
    || typeof value.observed_at !== "string" || !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value.observed_at)
    || !Number.isFinite(Date.parse(value.observed_at))) return null;
  const identity = officialThemeIdentity(reason,matches[0].exact_catalog,matches[0].catalog_mapping_state);
  if (state === "exact" && identity?.catalog_version !== value.catalog_version) return null;
  return {state:state as ThemeMappingState,identity};
}
